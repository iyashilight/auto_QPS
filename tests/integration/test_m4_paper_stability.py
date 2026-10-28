from datetime import UTC, datetime

import pytest

from qps.trading.kernel import PaperReadinessEvaluator, TradingFrozenError


def test_paper_readiness_rejects_caller_selected_thresholds() -> None:
    with pytest.raises(TradingFrozenError):
        PaperReadinessEvaluator().evaluate(started_at=datetime.now(UTC), decisions=10)
