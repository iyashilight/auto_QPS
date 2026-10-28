"""Single-strategy policy gate used before reservation."""

from qps.trading.advanced import AllocationPlanner
from qps.trading.kernel import CapitalPolicyService


class PortfolioConstructionService:
    def __init__(self, planner: AllocationPlanner | None = None) -> None:
        self.planner = planner or AllocationPlanner()

    def construct(self, intents: list[dict[str, object]], *, account_id: str) -> dict[str, object]:
        return self.planner.persist(self.planner.plan(intents, account_id=account_id))


__all__ = ["CapitalPolicyService", "PortfolioConstructionService"]
