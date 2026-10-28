from decimal import Decimal

import pytest
from tests.m3v2_helpers import candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError


@pytest.mark.asyncio
async def test_report_rejects_placeholder_capacity_or_missing_benchmark(tmp_path) -> None:
    runner, _, engine = service(tmp_path)
    original = engine.run_formal

    async def placeholder(request, *, trial_id, release):
        run = await original(request, trial_id=trial_id, release=release)
        return run.model_copy(update={"engine_config": {**run.engine_config, "capacity": {"participation_rate": "not_estimated"}}})

    engine.run_formal = placeholder
    with pytest.raises(BacktestError):
        await runner.register_and_reserve_trial(
            registration=registration("report-missing"), candidate=candidate("report-missing"), actor_type=ResearchActorType.SCRIPT,
            requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
            frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
        )

