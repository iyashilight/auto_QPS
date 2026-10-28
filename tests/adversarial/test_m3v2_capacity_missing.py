from decimal import Decimal

import pytest
from tests.m3v2_helpers import V2Engine, candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError


@pytest.mark.asyncio
async def test_missing_capacity_cannot_be_promoted(tmp_path) -> None:
    engine = V2Engine()
    runner, _, _ = service(tmp_path, engine=engine)
    original = engine.run_formal

    async def missing(request, *, trial_id, release):
        run = await original(request, trial_id=trial_id, release=release)
        return run.model_copy(update={"engine_config": {**run.engine_config, "capacity": None}})

    engine.run_formal = missing
    with pytest.raises(BacktestError):
        await runner.register_and_reserve_trial(
            registration=registration("capacity-missing"), candidate=candidate("capacity-missing"), actor_type=ResearchActorType.SCRIPT,
            requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
            frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
        )

