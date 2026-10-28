from pathlib import Path
from tempfile import TemporaryDirectory

from qps.trading.portfolio import PortfolioConstructionService


def test_policy_plan_has_persistent_lineage() -> None:
    with TemporaryDirectory() as directory:
        service = PortfolioConstructionService()
        service.planner = service.planner.__class__(Path(directory) / "portfolio.db")
        plan = service.construct(
            [{"intent_id": "i", "instrument_id": "BTC", "side": "BUY", "quantity": "1"}], account_id="a")
        assert plan["allocation_rule_version"]
        assert plan["persisted"] is True
