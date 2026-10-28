from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from qps.research.optimizer.engine import FormalOptimizerAdapter
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_each_optimizer_candidate_uses_formal_service_and_budget(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    adapter = FormalOptimizerAdapter(runner)
    result = await adapter.search(
        registration=registration("optimizer"), candidate=candidate("optimizer"), actor_type=ResearchActorType.SCRIPT,
        candidates=[{"threshold": "1"}, {"threshold": "2"}], release_id="release-v2",
        reserved_at=registration().registered_at, requested_compute=Decimal("1"),
        request_factory=lambda params: {"strategy_id": "strategy-v2", "params": params},
    )
    assert [item["status"] for item in result] == ["SUCCEEDED", "SUCCEEDED"]
    assert len(budget.store.get_artifacts("optimizer:candidate:0", "formal-run-v2")) == 1
    assert len(budget.store.get_artifacts("optimizer:candidate:1", "formal-run-v2")) == 1

