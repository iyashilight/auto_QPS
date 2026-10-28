from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType, TrialStatus
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_trial_lifecycle_and_terminal_record_are_append_only(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    await runner.register_and_reserve_trial(
        registration=registration("lifecycle"), candidate=candidate("lifecycle"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    assert budget.store.latest_trial_status("lifecycle") is TrialStatus.SUCCEEDED
    assert len(budget.store.list_reservations("lifecycle")) == 4

