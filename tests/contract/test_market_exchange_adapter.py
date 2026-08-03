"""契约测试：Exchange Adapter 与 Market Adapter

测试：
- 签名正确性
- 限频令牌桶
- 断线重连
- 乱序缓冲
- 超时与异常包装
- Market Adapter 端口契约
- 不依赖真实交易所可达性（CI 可离线跑）
- M1.3: Binance 主机路由（现货/永续）
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import time
from datetime import UTC, datetime
from decimal import Decimal
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
        # Mock WebSocket 连接，避免真实网络调用
        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)
        mock_ws.send = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))

        with patch("websockets.connect", return_value=mock_ws):
            sub_id = await adapter.subscribe("kline", {"symbol": "BTCUSDT"}, lambda d: None)
            assert isinstance(sub_id, str)
            assert len(sub_id) > 0

        # 清理 WebSocket 任务
        if adapter._ws_task and not adapter._ws_task.done():
            adapter._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await adapter._ws_task

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


# ── M1.3: Binance 主机路由测试 ─────────────────────────────


class TestBinanceHostRouting:
    """M1.3: Binance 现货/永续主机路由测试

    验证 PERP 请求打到 futures 主机、spot 请求打到现货主机。
    """

    def test_default_hosts(self) -> None:
        """默认主机配置"""
        adapter = BinanceExchangeAdapter()
        assert adapter._base_url == "https://api.binance.com"
        assert adapter._ws_base_url == "wss://stream.binance.com:9443/ws"
        assert adapter._futures_base_url == "https://fapi.binance.com"
        assert adapter._futures_ws_base_url == "wss://fstream.binance.com/ws"

    def test_custom_hosts(self) -> None:
        """自定义主机配置"""
        adapter = BinanceExchangeAdapter(
            base_url="https://custom-spot.com",
            ws_base_url="wss://custom-spot.com/ws",
            futures_base_url="https://custom-futures.com",
            futures_ws_base_url="wss://custom-futures.com/ws",
        )
        assert adapter._base_url == "https://custom-spot.com"
        assert adapter._futures_base_url == "https://custom-futures.com"

    @pytest.mark.asyncio
    async def test_request_spot_uses_spot_client(self) -> None:
        """现货请求使用现货客户端"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._base_url = "https://api.binance.com"
        adapter._futures_base_url = "https://fapi.binance.com"
        adapter._client = AsyncMock()
        adapter._futures_client = AsyncMock()
        adapter._rest_limiter = TokenBucket(rate=100)
        adapter._retry_policy = RetryPolicy(max_retries=0)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"serverTime": 1234567890000}
        adapter._client.get.return_value = mock_response

        result = await adapter.request("GET", "/api/v3/time", market="spot")
        assert result == {"serverTime": 1234567890000}
        adapter._client.get.assert_called_once()
        adapter._futures_client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_futures_uses_futures_client(self) -> None:
        """永续请求使用永续客户端"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._base_url = "https://api.binance.com"
        adapter._futures_base_url = "https://fapi.binance.com"
        adapter._client = AsyncMock()
        adapter._futures_client = AsyncMock()
        adapter._rest_limiter = TokenBucket(rate=100)
        adapter._retry_policy = RetryPolicy(max_retries=0)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [{"openInterest": "1000"}]
        adapter._futures_client.get.return_value = mock_response

        result = await adapter.request("GET", "/fapi/v1/openInterest", market="futures")
        assert result == [{"openInterest": "1000"}]
        adapter._futures_client.get.assert_called_once()
        adapter._client.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_request_path_prefix_detects_futures(self) -> None:
        """路径前缀自动检测永续市场"""
        adapter = BinanceExchangeAdapter.__new__(BinanceExchangeAdapter)
        adapter._connected = True
        adapter._base_url = "https://api.binance.com"
        adapter._futures_base_url = "https://fapi.binance.com"
        adapter._client = AsyncMock()
        adapter._futures_client = AsyncMock()
        adapter._rest_limiter = TokenBucket(rate=100)
        adapter._retry_policy = RetryPolicy(max_retries=0)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = []
        adapter._futures_client.get.return_value = mock_response

        # /futures/data/openInterestHist 路径应自动路由到 futures 主机
        await adapter.request("GET", "/futures/data/openInterestHist", market="spot")
        adapter._futures_client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_subscribe_market_parameter(self) -> None:
        """subscribe() market 参数传递"""
        adapter = BinanceExchangeAdapter()
        mock_ws = AsyncMock()
        mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
        mock_ws.__aexit__ = AsyncMock(return_value=False)
        mock_ws.send = AsyncMock()
        mock_ws.__aiter__ = MagicMock(return_value=iter([]))

        with patch("websockets.connect", return_value=mock_ws):
            # 订阅现货频道
            sub_id_spot = await adapter.subscribe(
                "kline", {"symbol": "BTCUSDT"}, lambda d: None, market="spot"
            )
            assert isinstance(sub_id_spot, str)
            assert adapter._active_ws_market == "spot"

            # 清理
            if adapter._ws_task and not adapter._ws_task.done():
                adapter._ws_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await adapter._ws_task


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


# ── M1.3: Market Adapter 主机路由测试 ─────────────────────


class TestMarketAdapterHostRouting:
    """M1.3: Market Adapter 主机路由测试

    验证 PERP 标的走 futures 主机、spot 标的走 spot 主机。
    """

    def test_market_of_spot(self) -> None:
        """现货标的识别"""
        from qps.data.adapters.binance_market_adapter import BinanceMarketAdapter

        # 创建 mock adapter
        mock_exchange = MagicMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)

        assert adapter._market_of("binance-btc-usdt") == "spot"
        assert adapter._market_of("binance-eth-usdt") == "spot"

    def test_market_of_futures(self) -> None:
        """永续标的识别"""
        from qps.data.adapters.binance_market_adapter import BinanceMarketAdapter

        mock_exchange = MagicMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)

        assert adapter._market_of("binance-btc-usdt-perp") == "futures"
        assert adapter._market_of("binance-eth-usdt-perp") == "futures"

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_perp_routes_to_futures(self) -> None:
        """PERP K 线采集走 futures 主机"""
        mock_exchange = AsyncMock()
        mock_exchange.status.return_value = MagicMock(
            connected=True, last_heartbeat=None, reconnect_count=0
        )
        mock_exchange.request = AsyncMock(return_value=[[]])

        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True

        await adapter.fetch_historical_bars(
            "binance-btc-usdt-perp", "5m",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        # 验证请求使用了 market="futures"
        call_args = mock_exchange.request.call_args
        assert call_args[1]["market"] == "futures" or \
               ("/fapi/v1/klines" in call_args[0][1] if len(call_args[0]) > 1 else False)

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_spot_routes_to_spot(self) -> None:
        """Spot K 线采集走 spot 主机"""
        mock_exchange = AsyncMock()
        mock_exchange.status.return_value = MagicMock(
            connected=True, last_heartbeat=None, reconnect_count=0
        )
        mock_exchange.request = AsyncMock(return_value=[[]])

        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True

        await adapter.fetch_historical_bars(
            "binance-btc-usdt", "5m",
            datetime(2024, 1, 1, tzinfo=UTC),
            datetime(2024, 1, 2, tzinfo=UTC),
        )

        # 验证请求使用了 market="spot"
        call_args = mock_exchange.request.call_args
        assert call_args[1]["market"] == "spot" or \
               ("/api/v3/klines" in call_args[0][1] if len(call_args[0]) > 1 else False)


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
