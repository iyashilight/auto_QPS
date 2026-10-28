from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from qps.core.errors import DataError
from qps.research.backtest.data_resolver import BacktestDataResolver


class DataFake:
    async def query(self, *args, **kwargs):
        decision = kwargs["decision_time"]
        return [SimpleNamespace(
            instrument_id="btc", interval="5m", open_time=decision - timedelta(minutes=5),
            event_time=decision - timedelta(minutes=5), available_at=decision + timedelta(minutes=1),
            close_time=decision, is_closed=True, close=Decimal("100"),
        )]


@pytest.mark.asyncio
async def test_future_available_at_is_not_visible() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError, match=r"未来数据|可见性"):
        await BacktestDataResolver(DataFake()).resolve(
            instrument_id="btc", interval="5m", start=decision - timedelta(hours=1),
            end=decision + timedelta(hours=1), decision_time=decision, manifest_id="m1",
        )
