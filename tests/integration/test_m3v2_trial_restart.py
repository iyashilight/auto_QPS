from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType, TrialStatus
from qps.infra.storage.research_db import ResearchStore
from tests.m3v2_helpers import candidate, registration, service


@pytest.mark.asyncio
async def test_restart_preserves_one_terminal_trial_and_consumption(tmp_path) -> None:
    runner, _, _ = service(tmp_path)
    await runner.register_and_reserve_trial(
        registration=registration("restart"), candidate=candidate("restart"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2", request={"strategy_id": "strategy-v2"},
    )
    recovered = ResearchStore(tmp_path / "research.db")
    assert recovered.latest_trial_status("restart") is TrialStatus.SUCCEEDED
    assert len(recovered.list_reservations("restart")) == 4
