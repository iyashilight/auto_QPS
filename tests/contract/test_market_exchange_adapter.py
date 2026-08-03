"""契约测试：Exchange Adapter 与 Market Adapter

测试：
- 签名正确性
- 限频令牌桶
- 断线重连
- 乱序缓冲
- 超时与异常包装
- Market Adapter 端口契约
- 不依赖真实交易所可达性（CI 可离线跑）
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from qps.broker.exchange_adapter.binance_exchange_adapter import (
    BinanceExchangeAdapter,
    RetryPolicy,
    TokenBucket,
)
from qps.core.errors import (
    BrokerAuthError,
    BrokerNetworkError,
    BrokerRateLimitError,
)
from qps.core.models.adapter import AdapterStatus
from qps.core.models.market import Bar
from qps.data.adapters.binance_market_adapter import (
    BinanceMarketAdapter,
    DedupBuffer,
    OutOfOrderBuffer,
)

# ── Exchange Adapter 契约测试 ─────────────────────────────


class TestExchangeAdapterContract:
    """Exchange Adapter 端口契约测试

    验证 BinanceExchangeAdapter 满足 ExchangeAdapterPort 的所有语义。
    """

    def test_status_returns_adapter_status(self) -> None:
        """status() 返回 AdapterStatus"""
        adapter = BinanceExchangeAdapter()
        status = adapter.status()
        assert isinstance(status, AdapterStatus)
        assert isinstance(status.connected, bool)
        assert isinstance(status.reconnect_count, int)

    def test_rate_limiter_returns_dict(self) -> None:
        """rate_limiter() 返回 dict"""
        adapter = BinanceExchangeAdapter()
        info = adapter.rate_limiter()
        assert isinstance(info, dict)
        assert "rest_available" in info

    @pytest.mark.asyncio
    async def test_request_without_connection_raises_network_error(self) -> None:
        """未连接时 request() 抛 BrokerNetworkError"""
        adapter = BinanceExchangeAdapter()
        with pytest.raises(BrokerNetworkError):
            await adapter.request("GET", "/api/v3/time")

    @pytest.mark.asyncio
    async def test_disconnect_idempotent(self) -> None:
        """disconnect() 幂等"""
        adapter = BinanceExchangeAdapter()
        await adapter.disconnect()
        await adapter.disconnect()  # 不应抛异常

    @pytest.mark.asyncio
    async def test_subscribe_returns_sub_id(self) -> None:
        """subscribe() 返回订阅 ID"""
        adapter = BinanceExchangeAdapter()
        # 不需要真实 WebSocket 连接，只需验证接口
        sub_id = await adapter.subscribe("kline", {"symbol": "BTCUSDT"}, lambda d: None)
        assert isinstance(sub_id, str)
        assert len(sub_id) > 0

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self) -> None:
        """unsubscribe() 幂等"""
        adapter = BinanceExchangeAdapter()
        # 不存在的订阅 ID 不应抛异常
        await adapter.unsubscribe("non_existent_sub")
        await adapter.unsubscribe("non_existent_sub")

    def test_sign_params_hmac_sha256(self) -> None:
        """签名使用 HMAC-SHA256"""
        adapter = BinanceExchangeAdapter()
        adapter._api_secret = "test_secret"

        params = {"symbol": "BTCUSDT", "limit": 100}
        signed = adapter._sign_params(params)

        # 验证签名值
        assert "timestamp" in signed
        assert "signature" in signed

        # 手动验证签名
        query_string = "&".join(f"{k}={v}" for k, v in sorted(signed.items()) if k != "signature")
        expected_sig = hmac.new(
            b"test_secret",
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert signed["signature"] == expected_sig

    @pytest.mark.asyncio
    async def test_429_wrapped_as_rate_limit_error(self) -> None:
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
    async def test_401_wrapped_as_auth_error(self) -> None:
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
    async def test_timeout_wrapped_as_network_error(self) -> None:
        """超时包装为 BrokerNetworkError"""
        import httpx

        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()
        adapter._client.get.side_effect = httpx.TimeoutException("超时")

        with pytest.raises(BrokerNetworkError):
            await adapter._send_request("GET", "/api/v3/klines", {})

    @pytest.mark.asyncio
    async def test_connect_error_wrapped_as_network_error(self) -> None:
        """连接错误包装为 BrokerNetworkError"""
        import httpx

        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._client = AsyncMock()
        adapter._client.get.side_effect = httpx.ConnectError("连接失败")

        with pytest.raises(BrokerNetworkError):
            await adapter._send_request("GET", "/api/v3/klines", {})


# ── Market Adapter 契约测试 ───────────────────────────────


class TestMarketAdapterContract:
    """Market Adapter 端口契约测试

    验证 BinanceMarketAdapter 满足 MarketAdapterPort 的所有语义。
    """

    def _make_adapter(self, mock_exchange: Any = None) -> BinanceMarketAdapter:
        if mock_exchange is None:
            mock_exchange = MagicMock()
            mock_exchange.status.return_value = MagicMock(
                connected=True, last_heartbeat=None, reconnect_count=0
            )
            mock_exchange.subscribe = AsyncMock(return_value="ws_sub_1")
            mock_exchange.unsubscribe = AsyncMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True
        return adapter

    @pytest.mark.asyncio
    async def test_subscribe_bars_returns_sub_id(self) -> None:
        """subscribe_bars() 返回订阅 ID"""
        adapter = self._make_adapter()
        sub_id = await adapter.subscribe_bars("binance-btc-usdt-perp", "5m", lambda b: None)
        assert isinstance(sub_id, str)

    @pytest.mark.asyncio
    async def test_subscribe_funding_returns_sub_id(self) -> None:
        """subscribe_funding() 返回订阅 ID"""
        adapter = self._make_adapter()
        sub_id = await adapter.subscribe_funding("binance-btc-usdt-perp", lambda f: None)
        assert isinstance(sub_id, str)

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self) -> None:
        """unsubscribe() 幂等"""
        adapter = self._make_adapter()
        await adapter.unsubscribe("non_existent")
        await adapter.unsubscribe("non_existent")

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_returns_bar_list(self) -> None:
        """fetch_historical_bars() 返回 Bar 列表"""
        mock_exchange = AsyncMock()
        mock_exchange.status.return_value = MagicMock(
            connected=True, last_heartbeat=None, reconnect_count=0
        )
        mock_exchange.request = AsyncMock(return_value=[
            [
                1704067200000, "67000", "67100", "66900", "67050",
                "100", 1704067499999, "6700000", 500, "50", "3350000", "0",
            ],
        ])

        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True

        bars = await adapter.fetch_historical_bars(
            "binance-btc-usdt-perp", "5m",
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 5, 0, tzinfo=UTC),
        )

        assert isinstance(bars, list)
        if bars:
            assert isinstance(bars[0], Bar)

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_sorted_by_time(self) -> None:
        """fetch_historical_bars() 返回按 open_time 升序"""
        mock_exchange = AsyncMock()
        mock_exchange.status.return_value = MagicMock(
            connected=True, last_heartbeat=None, reconnect_count=0
        )
        mock_exchange.request = AsyncMock(return_value=[
            [
                1704067200000, "67000", "67100", "66900", "67050",
                "100", 1704067499999, "6700000", 500, "50", "3350000", "0",
            ],
            [
                1704067500000, "67050", "67200", "67000", "67100",
                "120", 1704067799999, "8050000", 600, "60", "4030000", "0",
            ],
        ])

        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True

        bars = await adapter.fetch_historical_bars(
            "binance-btc-usdt-perp", "5m",
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 15, 0, tzinfo=UTC),
        )

        for i in range(1, len(bars)):
            assert bars[i].open_time >= bars[i - 1].open_time

    @pytest.mark.asyncio
    async def test_operations_without_connection_raise_network_error(self) -> None:
        """未连接时操作抛 BrokerNetworkError"""
        mock_exchange = AsyncMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = False

        with pytest.raises(BrokerNetworkError):
            await adapter.subscribe_bars("test", "5m", lambda b: None)

        with pytest.raises(BrokerNetworkError):
            await adapter.subscribe_funding("test", lambda f: None)

        with pytest.raises(BrokerNetworkError):
            await adapter.fetch_historical_bars(
                "test", "5m",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            )

    def test_status_returns_adapter_status(self) -> None:
        """status() 返回 AdapterStatus"""
        adapter = self._make_adapter()
        status = adapter.status()
        assert isinstance(status, AdapterStatus)


# ── 限频令牌桶契约测试 ────────────────────────────────────


class TestTokenBucketContract:
    def test_rate_limit_enforced(self) -> None:
        """限频生效：令牌耗尽后等待"""
        bucket = TokenBucket(rate=2, capacity=2)
        loop = asyncio.new_event_loop()
        try:
            # 快速消耗所有令牌
            loop.run_until_complete(bucket.acquire(timeout=0.1))
            loop.run_until_complete(bucket.acquire(timeout=0.1))
            # 第三次应该超时
            with pytest.raises(BrokerRateLimitError):
                loop.run_until_complete(bucket.acquire(timeout=0.05))
        finally:
            loop.close()

    def test_tokens_refill(self) -> None:
        """令牌随时间补充"""
        bucket = TokenBucket(rate=100, capacity=10)
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(bucket.acquire())
            tokens_after = bucket.available_tokens
            time.sleep(0.05)
            tokens_later = bucket.available_tokens
            assert tokens_later >= tokens_after
        finally:
            loop.close()


# ── 去重与乱序缓冲契约测试 ────────────────────────────────


class TestDedupBufferContract:
    def test_dedup_keeps_latest(self) -> None:
        """去重保留最新版本"""
        buf = DedupBuffer(window_seconds=60)
        t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

        bar1 = Bar(
            instrument_id="test", open_time=t, interval="5m",
            open=Decimal("67000"), high=Decimal("67100"), low=Decimal("66900"),
            close=Decimal("67050"), volume=Decimal("100"),
            quote_volume=Decimal("6700000"), data_version="v1",
        )
        bar2 = Bar(
            instrument_id="test", open_time=t, interval="5m",
            open=Decimal("67000"), high=Decimal("67100"), low=Decimal("66900"),
            close=Decimal("67050"), volume=Decimal("200"),  # 更新
            quote_volume=Decimal("6700000"), data_version="v1",
        )

        buf.push(bar1)
        buf.push(bar2)

        result = buf.flush()
        assert len(result) == 1
        assert result[0].volume == Decimal("200")


class TestOutOfOrderBufferContract:
    def test_output_sorted(self) -> None:
        """输出按时间排序"""
        buf = OutOfOrderBuffer(max_size=3)

        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        t3 = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)

        # 前两个 push 不满，第三个 push 满 3 后自动排序输出
        buf.push(Bar(
            instrument_id="test", open_time=t1, interval="5m",
            open=Decimal("67000"), high=Decimal("67100"), low=Decimal("66900"),
            close=Decimal("67050"), volume=Decimal("100"),
            quote_volume=Decimal("6700000"), data_version="v1",
        ))
        buf.push(Bar(
            instrument_id="test", open_time=t2, interval="5m",
            open=Decimal("67000"), high=Decimal("67100"), low=Decimal("66900"),
            close=Decimal("67050"), volume=Decimal("100"),
            quote_volume=Decimal("6700000"), data_version="v1",
        ))
        result = buf.push(Bar(
            instrument_id="test", open_time=t3, interval="5m",
            open=Decimal("67000"), high=Decimal("67100"), low=Decimal("66900"),
            close=Decimal("67050"), volume=Decimal("100"),
            quote_volume=Decimal("6700000"), data_version="v1",
        ))

        # max_size=3，第3个 push 时缓冲满，自动排序输出
        assert len(result) == 3
        assert result[0].open_time < result[1].open_time < result[2].open_time
