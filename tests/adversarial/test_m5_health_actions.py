from tempfile import TemporaryDirectory

from qps.trading.monitoring import StrategyHealthController


def test_health_automation_only_reduces_risk() -> None:
    with TemporaryDirectory() as directory:
        result = StrategyHealthController(f"{directory}/health.jsonl").apply("UNKNOWN", deployment_id="d", reason="stale")
        assert result["action"] == "RISK_REDUCE_ONLY"
