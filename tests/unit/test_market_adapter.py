"""T2 Binance Market Adapter 单元测试

测试：
- 符号映射
- REST K 线解析
- WebSocket K 线解析
- WebSocket 资金费率解析
- 去重缓冲
- 乱序缓冲
- 历史 K 线分页采集（mock）
- 数据版本号生成
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from qps.core.constants import DataQuality
from qps.core.errors import BrokerNetworkError
from qps.core.models.market import Bar
from qps.data.adapters.binance_market_adapter import (
    BinanceMarketAdapter,
    DedupBuffer,
    OutOfOrderBuffer,
    _data_version_for_batch,
    _to_exchange_symbol,
)

# ── 符号映射 ──────────────────────────────────────────────


class TestSymbolMapping:
    def test_btc_perp(self) -> None:
        assert _to_exchange_symbol("binance-btc-usdt-perp") == "BTCUSDT"

    def test_eth_perp(self) -> None:
        assert _to_exchange_symbol("binance-eth-usdt-perp") == "ETHUSDT"

    def test_btc_spot(self) -> None:
        assert _to_exchange_symbol("binance-btc-usdt") == "BTCUSDT"

    def test_unknown_instrument(self) -> None:
        """未知标的兜底处理"""
        result = _to_exchange_symbol("SOLUSDT")
        assert result == "SOLUSDT"


# ── 数据版本号 ────────────────────────────────────────────


class TestDataVersion:
    def test_format(self) -> None:
        version = _data_version_for_batch("binance", "test_batch")
        assert version.startswith("binance-")
        assert len(version.split("-")[1]) == 8

    def test_deterministic(self) -> None:
        """相同输入生成相同版本号"""
        v1 = _data_version_for_batch("binance", "batch1")
        v2 = _data_version_for_batch("binance", "batch1")
        assert v1 == v2

    def test_different_input_different_version(self) -> None:
        """不同输入生成不同版本号"""
        v1 = _data_version_for_batch("binance", "batch1")
        v2 = _data_version_for_batch("binance", "batch2")
        assert v1 != v2


# ── 去重缓冲 ──────────────────────────────────────────────


class TestDedupBuffer:
    def _make_bar(
        self,
        open_time: datetime,
        volume: Decimal = Decimal("100"),
    ) -> Bar:
        return Bar(
            instrument_id="binance-btc-usdt-perp",
            open_time=open_time,
            interval="5m",
            open=Decimal("67000"),
            high=Decimal("67100"),
            low=Decimal("66900"),
            close=Decimal("67050"),
            volume=volume,
            quote_volume=Decimal("6700000"),
            data_version="binance-test",
        )

    def test_push_returns_none_if_window_not_expired(self) -> None:
        """窗口未过期时 push 返回 None"""
        buf = DedupBuffer(window_seconds=60)
        bar = self._make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        result = buf.push(bar)
        assert result is None

    def test_flush_returns_all(self) -> None:
        """flush 返回所有缓冲数据"""
        buf = DedupBuffer(window_seconds=60)
        bar = self._make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        buf.push(bar)
        result = buf.flush()
        assert len(result) == 1

    def test_newer_bar_replaces_older(self) -> None:
        """更新的 K 线替换旧的"""
        buf = DedupBuffer(window_seconds=60)
        t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        old_bar = self._make_bar(t, volume=Decimal("100"))
        new_bar = self._make_bar(t, volume=Decimal("200"))
        buf.push(old_bar)
        buf.push(new_bar)
        result = buf.flush()
        assert len(result) == 1
        assert result[0].volume == Decimal("200")


# ── 乱序缓冲 ──────────────────────────────────────────────


class TestOutOfOrderBuffer:
    def _make_bar(self, open_time: datetime) -> Bar:
        return Bar(
            instrument_id="binance-btc-usdt-perp",
            open_time=open_time,
            interval="5m",
            open=Decimal("67000"),
            high=Decimal("67100"),
            low=Decimal("66900"),
            close=Decimal("67050"),
            volume=Decimal("100"),
            quote_volume=Decimal("6700000"),
            data_version="binance-test",
        )

    def test_buffer_not_full_returns_empty(self) -> None:
        """缓冲未满时返回空列表"""
        buf = OutOfOrderBuffer(max_size=5)
        bar = self._make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        result = buf.push(bar)
        assert result == []

    def test_buffer_full_returns_sorted(self) -> None:
        """缓冲满时返回排序后的 K 线"""
        buf = OutOfOrderBuffer(max_size=3)
        t1 = datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)
        t2 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        t3 = datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)

        buf.push(self._make_bar(t1))
        buf.push(self._make_bar(t2))
        result = buf.push(self._make_bar(t3))

        assert len(result) == 3
        assert result[0].open_time == t2
        assert result[1].open_time == t1
        assert result[2].open_time == t3

    def test_flush_returns_all(self) -> None:
        """flush 返回所有缓冲并排序"""
        buf = OutOfOrderBuffer(max_size=100)
        buf.push(self._make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)))
        buf.push(self._make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)))

        result = buf.flush()
        assert len(result) == 2
        assert result[0].open_time < result[1].open_time


# ── REST K 线解析 ─────────────────────────────────────────


class TestParseRestKlines:
    def _make_adapter(self) -> BinanceMarketAdapter:
        mock_exchange = MagicMock()
        mock_exchange.status.return_value = MagicMock(connected=True, last_heartbeat=None, reconnect_count=0)
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True
        return adapter

    def test_parse_valid_klines(self) -> None:
        """解析标准 Binance K 线响应"""
        adapter = self._make_adapter()

        raw = [
            [
                1704067200000,  # open_time (2024-01-01 00:00:00 UTC)
                "67000.00",     # open
                "67100.00",     # high
                "66900.00",     # low
                "67050.00",     # close
                "100.000",      # volume
                1704067499999,  # close_time
                "6700000.00",   # quote_volume
                500,            # trades_count
                "50.000",       # taker_buy_volume
                "3350000.00",   # taker_buy_quote_volume
                "0",            # ignore
            ],
        ]

        bars = adapter._parse_rest_klines("binance-btc-usdt-perp", "5m", raw)
        assert len(bars) == 1
        bar = bars[0]
        assert bar.instrument_id == "binance-btc-usdt-perp"
        assert bar.interval == "5m"
        assert bar.open == Decimal("67000.00")
        assert bar.high == Decimal("67100.00")
        assert bar.low == Decimal("66900.00")
        assert bar.close == Decimal("67050.00")
        assert bar.volume == Decimal("100.000")
        assert bar.trades_count == 500
        assert bar.quality == DataQuality.OK
        assert bar.available_at is None
        assert bar.decision_time is None

    def test_parse_empty_klines(self) -> None:
        """空响应返回空列表"""
        adapter = self._make_adapter()
        bars = adapter._parse_rest_klines("test", "5m", [])
        assert bars == []

    def test_parse_malformed_kline_skipped(self) -> None:
        """格式错误的数据行被跳过"""
        adapter = self._make_adapter()
        raw = [
            [1704067200000, "67000"],  # 字段不足
            [
                1704067200000, "67000", "67100", "66900", "67050",
                "100", 1704067499999, "6700000", 500, "50", "3350000", "0",
            ],
        ]
        bars = adapter._parse_rest_klines("test", "5m", raw)
        assert len(bars) == 1  # 只有第二行有效


# ── WebSocket K 线解析 ─────────────────────────────────────


class TestParseWsKline:
    def _make_adapter(self) -> BinanceMarketAdapter:
        mock_exchange = MagicMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        return adapter

    def test_parse_valid_ws_kline(self) -> None:
        """解析标准 WebSocket K 线消息"""
        adapter = self._make_adapter()

        data = {
            "e": "kline",
            "k": {
                "t": 1704067200000,
                "o": "67000.00",
                "h": "67100.00",
                "l": "66900.00",
                "c": "67050.00",
                "v": "100.000",
                "q": "6700000.00",
                "n": 500,
                "x": True,
                "V": "50.000",
                "Q": "3350000.00",
            },
        }

        bar = adapter._parse_ws_kline("binance-btc-usdt-perp", "5m", data)
        assert bar is not None
        assert bar.open == Decimal("67000.00")
        assert bar.close == Decimal("67050.00")
        assert bar.trades_count == 500
        assert bar.available_at is None
        assert bar.decision_time is None

    def test_parse_invalid_ws_kline_returns_none(self) -> None:
        """无效数据返回 None"""
        adapter = self._make_adapter()
        bar = adapter._parse_ws_kline("test", "5m", {})
        assert bar is None


# ── WebSocket 资金费率解析 ─────────────────────────────────


class TestParseWsFunding:
    def _make_adapter(self) -> BinanceMarketAdapter:
        mock_exchange = MagicMock()
        return BinanceMarketAdapter(exchange_adapter=mock_exchange)

    def test_parse_valid_funding(self) -> None:
        """解析标准资金费率消息"""
        adapter = self._make_adapter()

        data = {
            "e": "fundingRate",
            "r": "0.00010000",
            "T": 1704067200000,
        }

        fr = adapter._parse_ws_funding("binance-btc-usdt-perp", data)
        assert fr is not None
        assert fr.rate == Decimal("0.00010000")
        assert fr.interval == "8h"

    def test_parse_invalid_funding_returns_none(self) -> None:
        """无效数据返回 None"""
        adapter = self._make_adapter()
        fr = adapter._parse_ws_funding("test", {})
        assert fr is None


# ── Market Adapter 集成测试（mock） ───────────────────────


class TestBinanceMarketAdapter:
    def _make_adapter(self) -> BinanceMarketAdapter:
        mock_exchange = MagicMock()
        mock_exchange.status.return_value = MagicMock(
            connected=True, last_heartbeat=None, reconnect_count=0
        )
        mock_exchange.subscribe = AsyncMock(return_value="ws_sub_1")
        mock_exchange.unsubscribe = AsyncMock()
        mock_exchange.request = AsyncMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = True
        return adapter

    @pytest.mark.asyncio
    async def test_connect_without_exchange_connected(self) -> None:
        """底层未连接时自动连接"""
        mock_exchange = MagicMock()
        mock_exchange.status.return_value = MagicMock(connected=False, last_heartbeat=None, reconnect_count=0)
        mock_exchange.connect = AsyncMock()

        with patch.dict("os.environ", {
            "QPS_BINANCE_API_KEY": "test_key",
            "QPS_BINANCE_API_SECRET": "test_secret",
        }):
            adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
            await adapter.connect()
            assert adapter._connected is True
            mock_exchange.connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_unsubscribes_all(self) -> None:
        """断开连接时取消所有订阅"""
        adapter = self._make_adapter()
        adapter._subscriptions["sub_1"] = {"ws_sub_id": "ws_1"}
        adapter._subscriptions["sub_2"] = {"ws_sub_id": "ws_2"}

        await adapter.disconnect()
        assert adapter._connected is False
        assert len(adapter._subscriptions) == 0

    @pytest.mark.asyncio
    async def test_subscribe_bars_invalid_interval(self) -> None:
        """不支持的 K 线间隔抛 ValueError"""
        adapter = self._make_adapter()
        with pytest.raises(ValueError, match="不支持的 K 线间隔"):
            await adapter.subscribe_bars("test", "2h", lambda b: None)

    @pytest.mark.asyncio
    async def test_subscribe_bars_returns_sub_id(self) -> None:
        """K 线订阅返回订阅 ID"""
        adapter = self._make_adapter()
        sub_id = await adapter.subscribe_bars(
            "binance-btc-usdt-perp", "5m", lambda b: None
        )
        assert sub_id.startswith("market_sub_")

    @pytest.mark.asyncio
    async def test_subscribe_funding_returns_sub_id(self) -> None:
        """资金费率订阅返回订阅 ID"""
        adapter = self._make_adapter()
        sub_id = await adapter.subscribe_funding(
            "binance-btc-usdt-perp", lambda f: None
        )
        assert sub_id.startswith("market_sub_")

    @pytest.mark.asyncio
    async def test_unsubscribe_idempotent(self) -> None:
        """取消不存在的订阅不抛异常"""
        adapter = self._make_adapter()
        await adapter.unsubscribe("non_existent")

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_invalid_interval(self) -> None:
        """不支持的 K 线间隔抛 ValueError"""
        adapter = self._make_adapter()
        with pytest.raises(ValueError, match="不支持的 K 线间隔"):
            await adapter.fetch_historical_bars(
                "test", "2h",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_invalid_range(self) -> None:
        """start >= end 抛 ValueError"""
        adapter = self._make_adapter()
        t = datetime(2026, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="start 必须早于 end"):
            await adapter.fetch_historical_bars("test", "5m", t, t)

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_not_connected(self) -> None:
        """未连接时抛 BrokerNetworkError"""
        mock_exchange = MagicMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = False

        with pytest.raises(BrokerNetworkError, match="未连接"):
            await adapter.fetch_historical_bars(
                "test", "5m",
                datetime(2026, 1, 1, tzinfo=UTC),
                datetime(2026, 1, 2, tzinfo=UTC),
            )

    @pytest.mark.asyncio
    async def test_fetch_historical_bars_with_mock_data(self) -> None:
        """mock 数据采集完整流程"""
        adapter = self._make_adapter()

        # 模拟 Binance 响应
        raw_klines = [
            [
                1704067200000, "67000", "67100", "66900", "67050",
                "100", 1704067499999, "6700000", 500, "50", "3350000", "0",
            ],
            [
                1704067500000, "67050", "67200", "67000", "67100",
                "120", 1704067799999, "8050000", 600, "60", "4030000", "0",
            ],
        ]
        adapter._exchange_adapter.request = AsyncMock(return_value=raw_klines)

        bars = await adapter.fetch_historical_bars(
            "binance-btc-usdt-perp", "5m",
            datetime(2024, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2024, 1, 1, 0, 15, 0, tzinfo=UTC),
        )

        assert len(bars) == 2
        assert bars[0].open_time < bars[1].open_time

    def test_status_reflects_exchange_status(self) -> None:
        """状态反映底层 Exchange Adapter 状态"""
        adapter = self._make_adapter()
        status = adapter.status()
        assert status.connected is True

    @pytest.mark.asyncio
    async def test_operations_without_connection_raise(self) -> None:
        """未连接时订阅/采集操作抛 BrokerNetworkError"""
        mock_exchange = MagicMock()
        adapter = BinanceMarketAdapter(exchange_adapter=mock_exchange)
        adapter._connected = False

        with pytest.raises(BrokerNetworkError):
            await adapter.subscribe_bars("test", "5m", lambda b: None)

        with pytest.raises(BrokerNetworkError):
            await adapter.subscribe_funding("test", lambda f: None)
