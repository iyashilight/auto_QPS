from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qps.core.errors import DataError
from qps.research.backtest.data_resolver import BacktestDataResolver


def row(instrument: str, interval: str, decision: datetime):
    return SimpleNamespace(
        instrument_id=instrument, interval=interval, open_time=decision - timedelta(minutes=5),
        event_time=decision - timedelta(minutes=5), available_at=decision - timedelta(minutes=1),
        close_time=decision - timedelta(seconds=1), is_closed=True,
    )


def test_cross_instrument_and_interval_are_rejected() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(row("eth", "5m", decision), instrument_id="btc", interval="5m", decision_time=decision)
    with pytest.raises(DataError):
        BacktestDataResolver.validate_row(row("btc", "1h", decision), instrument_id="btc", interval="5m", decision_time=decision)


def test_future_instrument_rule_is_rejected() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)

    class FutureRulePort:
        async def get_instrument_rule(self, instrument_id: str, *, effective_at: datetime, known_at: datetime):
            return SimpleNamespace(
                instrument_id=instrument_id, rule_version_id="future-rule",
                available_at=known_at + timedelta(seconds=1),
                valid_from=effective_at - timedelta(days=1), valid_to=None,
            )

    async def invoke() -> None:
        with pytest.raises(DataError, match="未来"):
            await BacktestDataResolver(object(), rule_port=FutureRulePort()).resolve_rule(
                instrument_id="btc", effective_at=decision, decision_time=decision,
            )

    import asyncio

    asyncio.run(invoke())
