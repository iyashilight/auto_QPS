from decimal import Decimal

import pytest
from tests.m3v2_helpers import V2Engine, candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing-actual", "synthetic", "mismatch"])
async def test_actual_lineage_missing_synthetic_or_mismatch_fails_closed(tmp_path, mode) -> None:
    runner, _, _ = service(tmp_path, engine=V2Engine(mode=mode))
    with pytest.raises(BacktestError):
        await runner.register_and_reserve_trial(
            registration=registration(mode), candidate=candidate(mode), actor_type=ResearchActorType.SCRIPT,
            requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
            frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
        )

