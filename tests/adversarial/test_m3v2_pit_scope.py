from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qps.core.errors import DataError
from qps.research.backtest.data_resolver import BacktestDataResolver


def _row(instrument: str, interval: str, decision: datetime, *, future: bool = False):
    return SimpleNamespace(
        instrument_id=instrument, interval=interval,
        event_time=decision + timedelta(seconds=1 if future else -300),
        available_at=decision + timedelta(seconds=1 if future else -60),
        close_time=decision + timedelta(seconds=1 if future else -1),
        is_closed=True, open_time=decision - timedelta(minutes=5),
    )


def test_v2_pit_rejects_future_and_scope_pollution() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(_row("btc", "5m", decision, future=True), instrument_id="btc", interval="5m", decision_time=decision)
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(_row("eth", "5m", decision), instrument_id="btc", interval="5m", decision_time=decision)
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(_row("btc", "1h", decision), instrument_id="btc", interval="5m", decision_time=decision)

