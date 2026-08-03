"""契约测试 fixture

提供本地 mock server、模拟交易所响应。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_exchange_adapter() -> AsyncMock:
    """创建 mock Exchange Adapter"""
    adapter = AsyncMock()
    adapter.status.return_value = MagicMock(
        connected=True,
        last_heartbeat=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
        reconnect_count=0,
        latency_ms=50,
    )
    adapter.connect = AsyncMock()
    adapter.disconnect = AsyncMock()
    adapter.subscribe = AsyncMock(return_value="ws_sub_1")
    adapter.unsubscribe = AsyncMock()
    adapter.rate_limiter.return_value = {
        "rest_available": 10.0,
        "ws_available": 5.0,
    }
    return adapter


@pytest.fixture
def sample_klines_response() -> list[list[Any]]:
    """模拟 Binance K 线 REST 响应"""
    return [
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
        [
            1704067500000,  # open_time + 5m
            "67050.00",
            "67200.00",
            "67000.00",
            "67100.00",
            "120.000",
            1704067799999,
            "8050000.00",
            600,
            "60.000",
            "4030000.00",
            "0",
        ],
    ]


@pytest.fixture
def sample_funding_rate_response() -> list[dict[str, Any]]:
    """模拟 Binance 资金费率 REST 响应"""
    return [
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.00010000",
            "fundingTime": 1704067200000,
        },
        {
            "symbol": "BTCUSDT",
            "fundingRate": "0.00005000",
            "fundingTime": 1704096000000,
        },
    ]


@pytest.fixture
def sample_ws_kline_event() -> dict[str, Any]:
    """模拟 Binance WebSocket K 线事件"""
    return {
        "e": "kline",
        "E": 1704067200000,
        "s": "BTCUSDT",
        "k": {
            "t": 1704067200000,
            "T": 1704067499999,
            "s": "BTCUSDT",
            "i": "5m",
            "o": "67000.00",
            "c": "67050.00",
            "h": "67100.00",
            "l": "66900.00",
            "v": "100.000",
            "n": 500,
            "x": True,
            "q": "6700000.00",
            "V": "50.000",
            "Q": "3350000.00",
        },
    }
