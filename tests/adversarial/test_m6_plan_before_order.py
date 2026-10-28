from pathlib import Path
from tempfile import TemporaryDirectory

from qps.trading.advanced import AllocationPlanner


def test_order_plan_must_be_persisted_before_use() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "portfolio.db"
        planner = AllocationPlanner(database)
        plan = planner.plan([{"intent_id": "i", "instrument_id": "BTC", "side": "BUY", "quantity": "1"}], account_id="a")
        assert plan["persisted"] is False
        assert planner.persist(plan)["persisted"] is True
        assert AllocationPlanner(database).plans == [planner.plans[0]]
