from datetime import UTC, datetime, timedelta

from qps.trading.advanced import PortfolioRiskModel


def test_expired_risk_model_is_unknown_and_reduce_only() -> None:
    result = PortfolioRiskModel().evaluate(positions={}, expires_at=datetime.now(UTC) - timedelta(seconds=1))
    assert result["status"] == "UNKNOWN"
    assert result["action"] == "RISK_REDUCE_ONLY"

