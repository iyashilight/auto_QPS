from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_rerun_appends_new_artifact_after_restart(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    await runner.register_and_reserve_trial(
        registration=registration("recovery"), candidate=candidate("recovery"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    original = budget.store.get_artifacts("recovery", "formal-run-v2")
    await runner.rerun("recovery")
    current = budget.store.get_artifacts("recovery", "formal-run-v2")
    assert len(current) == 2 and current[0]["content_hash"] == original[0]["content_hash"]

