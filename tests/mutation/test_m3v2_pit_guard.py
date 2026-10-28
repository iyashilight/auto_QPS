from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qps.core.errors import DataError
from qps.research.backtest.data_resolver import BacktestDataResolver


def test_removing_availability_or_scope_guard_is_detectable() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)
    row = SimpleNamespace(
        instrument_id="btc", interval="5m", event_time=decision - timedelta(minutes=1),
        available_at=decision + timedelta(seconds=1), close_time=decision - timedelta(seconds=1),
        is_closed=True,
    )
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(row, instrument_id="btc", interval="5m", decision_time=decision)

