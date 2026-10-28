from decimal import Decimal

import pytest
from tests.m3v2_helpers import V2Engine, candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError


@pytest.mark.asyncio
async def test_rerun_mismatch_fails_without_overwriting_old_artifact(tmp_path) -> None:
    engine = V2Engine()
    runner, budget, _ = service(tmp_path, engine=engine)
    await runner.register_and_reserve_trial(
        registration=registration("rerun"), candidate=candidate("rerun"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    before = budget.store.get_artifacts("rerun", "formal-run-v2")
    engine.mode = "mismatch"
    with pytest.raises(BacktestError):
        await runner.rerun("rerun")
    assert budget.store.get_artifacts("rerun", "formal-run-v2") == before

