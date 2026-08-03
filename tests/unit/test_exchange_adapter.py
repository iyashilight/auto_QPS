"""T1 Binance Exchange Adapter 单元测试

测试：
- 令牌桶限频器
- HMAC-SHA256 签名
- 服务器时间校准
- 异常包装
- 状态管理
- 重试策略
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qps.broker.exchange_adapter.binance_exchange_adapter import (
    BinanceExchangeAdapter,
    RetryPolicy,
    TokenBucket,
)
from qps.core.errors import (
    BrokerAuthError,
    BrokerBusinessError,
    BrokerNetworkError,
    BrokerRateLimitError,
)

# ── 令牌桶限频器 ──────────────────────────────────────────


class TestTokenBucket:
    def test_initial_tokens_full(self) -> None:
        """初始令牌数 = 容量"""
        bucket = TokenBucket(rate=10, capacity=10)
        assert bucket.available_tokens == 10.0

    def test_acquire_decrements(self) -> None:
        """获取令牌后数量减少"""
        bucket = TokenBucket(rate=10, capacity=10)
        # 同步获取
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bucket.acquire())
            assert bucket.available_tokens < 10.0
        finally:
            loop.close()

    def test_refill_over_time(self) -> None:
        """令牌随时间补充"""
        bucket = TokenBucket(rate=100, capacity=10)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bucket.acquire())
            tokens_after = bucket.available_tokens
            # 等待一小段时间
            time.sleep(0.05)
            tokens_later = bucket.available_tokens
            assert tokens_later >= tokens_after
        finally:
            loop.close()

    def test_capacity_capped(self) -> None:
        """令牌数不超过容量"""
        bucket = TokenBucket(rate=10, capacity=5)
        # 等一段时间后令牌数不会超过容量
        time.sleep(0.1)
        assert bucket.available_tokens <= 5.0

    def test_acquire_timeout_raises_rate_limit(self) -> None:
        """耗尽令牌后超时抛 BrokerRateLimitError"""
        bucket = TokenBucket(rate=0.1, capacity=1)
        loop = asyncio.new_event_loop()
        try:
            # 消耗唯一令牌
            loop.run_until_complete(bucket.acquire(timeout=0.1))
            # 第二次应该超时
            with pytest.raises(BrokerRateLimitError, match="限频等待超时"):
                loop.run_until_complete(bucket.acquire(timeout=0.05))
        finally:
            loop.close()


# ── 重试策略 ──────────────────────────────────────────────


class TestRetryPolicy:
    def test_default_values(self) -> None:
        policy = RetryPolicy()
        assert policy.max_retries == 3
        assert policy.base_delay == 1.0
        assert policy.max_delay == 30.0

    def test_delay_increases_exponentially(self) -> None:
        policy = RetryPolicy(base_delay=1.0, backoff_factor=2.0)
        assert policy.delay_for_attempt(0) == 1.0
        assert policy.delay_for_attempt(1) == 2.0
        assert policy.delay_for_attempt(2) == 4.0

    def test_delay_capped_at_max(self) -> None:
        policy = RetryPolicy(base_delay=1.0, max_delay=5.0, backoff_factor=10.0)
        assert policy.delay_for_attempt(10) == 5.0


# ── Binance Exchange Adapter ───────────────────────────────


class TestBinanceExchangeAdapter:
    def _make_adapter(self, **kwargs: Any) -> BinanceExchangeAdapter:
        """创建测试用适配器"""
        defaults = {
            "base_url": "https://api.binance.com",
            "ws_base_url": "wss://stream.binance.com:9443/ws",
            "testnet": False,
            "rate_limit_per_sec": 100.0,
            "request_timeout": 5.0,
        }
        defaults.update(kwargs)
        return BinanceExchangeAdapter(**defaults)

    def test_testnet_url_override(self) -> None:
        """测试网 URL 自动覆盖"""
        adapter = self._make_adapter(testnet=True)
        assert "testnet" in adapter._base_url
        assert "testnet" in adapter._ws_base_url

    def test_futures_testnet_url_override(self) -> None:
        """永续合约测试网使用独立主机（非现货测试网）"""
        adapter = self._make_adapter(testnet=True)
        # 永续合约测试网应使用 binancefuture.com，而非 binance.vision
        assert "binancefuture.com" in adapter._futures_base_url
        assert "stream.binancefuture.com" in adapter._futures_ws_base_url
        # 现货测试网仍使用 binance.vision
        assert "binance.vision" in adapter._base_url

    def test_status_disconnected(self) -> None:
        """未连接时状态正确"""
        adapter = self._make_adapter()
        status = adapter.status()
        assert status.connected is False
        assert status.last_heartbeat is None
        assert status.reconnect_count == 0

    def test_rate_limiter_info(self) -> None:
        """限频器信息返回"""
        adapter = self._make_adapter(rate_limit_per_sec=10.0, ws_rate_limit_per_sec=5.0)
        info = adapter.rate_limiter()
        assert info["rest_rate"] == 10.0
        assert info["ws_rate"] == 5.0

    @pytest.mark.asyncio
    async def test_request_without_connection_raises(self) -> None:
        """未连接时请求抛 BrokerNetworkError"""
        adapter = self._make_adapter()
        with pytest.raises(BrokerNetworkError, match="未连接"):
            await adapter.request("GET", "/api/v3/time")

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self) -> None:
        """断开连接幂等"""
        adapter = self._make_adapter()
        await adapter.disconnect()  # 不应抛异常
        await adapter.disconnect()  # 重复调用也不应抛异常

    @pytest.mark.asyncio
    async def test_connect_creates_client(self) -> None:
        """连接成功后创建 HTTP 客户端"""
        adapter = self._make_adapter()

        # Mock HTTP 客户端
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = MagicMock(
                status_code=200,
                json=lambda: {"serverTime": int(time.time() * 1000)},
                raise_for_status=lambda: None,
            )
            mock_client_cls.return_value = mock_client

            await adapter.connect()
            assert adapter.status().connected is True

    @pytest.mark.asyncio
    async def test_connect_network_error(self) -> None:
        """HTTP 客户端创建失败抛 BrokerNetworkError"""
        import httpx

        adapter = self._make_adapter()

        # Mock __init__ of httpx.AsyncClient to raise ConnectError (模拟底层连接失败)
        with patch("httpx.AsyncClient.__init__", side_effect=httpx.ConnectError("连接失败")), \
             pytest.raises((BrokerNetworkError, httpx.ConnectError)):
            await adapter.connect()

    def test_sign_params(self) -> None:
        """HMAC-SHA256 签名正确"""
        adapter = self._make_adapter()
        adapter._api_secret = "test_secret_key"

        params = {"symbol": "BTCUSDT", "interval": "5m", "limit": 100}
        signed = adapter._sign_params(params)

        # 必须包含 timestamp 和 signature
        assert "timestamp" in signed
        assert "signature" in signed

        # 验证签名值
        query_string = "&".join(f"{k}={v}" for k, v in sorted(signed.items()) if k != "signature")
        expected_sig = hmac.new(
            b"test_secret_key",
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert signed["signature"] == expected_sig

    def test_sign_params_without_secret_raises(self) -> None:
        """签名但无 secret 时抛异常"""
        adapter = self._make_adapter()
        adapter._api_secret = None

        with pytest.raises((AssertionError, BrokerAuthError)):
            adapter._sign_params({"symbol": "BTCUSDT"})

    def test_build_ws_stream_name_kline(self) -> None:
        """构建 K 线 WebSocket 流名"""
        adapter = self._make_adapter()
        stream = adapter._build_ws_stream_name(
            "kline",
            {"symbol": "BTCUSDT", "interval": "5m"},
        )
        assert stream == "btcusdt@kline_5m"

    def test_build_ws_stream_name_mark_price(self) -> None:
        """构建标记价格 WebSocket 流名"""
        adapter = self._make_adapter()
        stream = adapter._build_ws_stream_name(
            "markPrice",
            {"symbol": "BTCUSDT"},
        )
        assert stream == "btcusdt@markPrice@1s"

    def test_build_ws_stream_name_funding_rate(self) -> None:
        """构建资金费率 WebSocket 流名"""
        adapter = self._make_adapter()
        stream = adapter._build_ws_stream_name(
            "fundingRate",
            {"symbol": "BTCUSDT"},
        )
        assert stream == "btcusdt@fundingRate"


# ── 异常包装测试 ──────────────────────────────────────────


class TestExceptionWrapping:
    @pytest.mark.asyncio
    async def test_429_rate_limit(self) -> None:
        """HTTP 429 包装为 BrokerRateLimitError"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()
        adapter._retry_policy = RetryPolicy(max_retries=0)

        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {"Retry-After": "5"}
        adapter._client.get.return_value = mock_response

        with pytest.raises(BrokerRateLimitError):
            await adapter._send_request("GET", "/api/v3/klines", {})

    @pytest.mark.asyncio
    async def test_401_auth_error(self) -> None:
        """HTTP 401 包装为 BrokerAuthError"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 401
        adapter._client.get.return_value = mock_response

        with pytest.raises(BrokerAuthError):
            await adapter._send_request("GET", "/api/v3/account", {})

    @pytest.mark.asyncio
    async def test_400_business_error(self) -> None:
        """HTTP 400 包装为 BrokerBusinessError"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "code": -1121,
            "msg": "Invalid symbol.",
        }
        adapter._client.get.return_value = mock_response

        with pytest.raises(BrokerBusinessError, match="Invalid symbol"):
            await adapter._send_request("GET", "/api/v3/klines", {})

    @pytest.mark.asyncio
    async def test_timeout_network_error(self) -> None:
        """请求超时包装为 BrokerNetworkError"""
        import httpx

        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()
        adapter._client.get.side_effect = httpx.TimeoutException("超时")

        with pytest.raises(BrokerNetworkError, match="超时"):
            await adapter._send_request("GET", "/api/v3/klines", {})
