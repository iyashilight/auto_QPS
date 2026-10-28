from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_formal_execution_artifact_separates_execution_cost_and_capacity(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    await runner.register_and_reserve_trial(
        registration=registration("execution"), candidate=candidate("execution"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    artifact = budget.store.get_formal_artifact_by_id("execution:formal-run:v2-run-1")
    assert artifact is not None and set(artifact.cost_breakdown) == {"fees", "slippage", "funding", "borrowing", "liquidation"}

