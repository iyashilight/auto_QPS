from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_capacity_is_traceable_in_report(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    await runner.register_and_reserve_trial(
        registration=registration("capacity"), candidate=candidate("capacity"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    report = budget.store.get_report("evidence-v2-run-1")
    assert report.capacity["participation_rate"] == "0.1"

