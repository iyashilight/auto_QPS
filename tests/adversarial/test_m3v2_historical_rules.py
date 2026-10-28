from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qps.core.errors import DataError
from qps.research.backtest.data_resolver import BacktestDataResolver


@pytest.mark.asyncio
async def test_formal_rule_resolver_rejects_future_missing_and_cross_scope() -> None:
    decision = datetime(2026, 1, 1, tzinfo=UTC)

    class Rules:
        def __init__(self, mode: str) -> None:
            self.mode = mode

        async def get_instrument_rule(self, instrument_id: str, *, effective_at: datetime, known_at: datetime):
            if self.mode == "missing":
                return None
            return SimpleNamespace(
                instrument_id="eth" if self.mode == "cross" else instrument_id,
                rule_version_id="rule-v2", available_at=known_at + timedelta(seconds=1) if self.mode == "future" else known_at,
                valid_from=effective_at - timedelta(days=1), valid_to=None,
            )

    for mode, text in (("future", "未来"), ("missing", "rule"), ("cross", "跨标的")):
        with pytest.raises((DataError, Exception), match=text):
            await BacktestDataResolver(object(), rule_port=Rules(mode)).resolve_rule(
                instrument_id="btc", effective_at=decision, decision_time=decision,
            )

