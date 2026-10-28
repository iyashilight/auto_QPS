from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType, TrialStatus
from tests.m3v2_helpers import budget_manager, candidate, registration


@pytest.mark.asyncio
async def test_budget_retry_cancel_timeout_are_not_refunded(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    reg = registration("budget-recovery")
    await manager.reserve_trial(registration=reg, candidate=candidate("budget-recovery"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("2"), blind_reveals=0, reserved_at=reg.registered_at)
    await manager.finalize_trial(trial_id=reg.trial_id, terminal_status=TrialStatus.CANCELLED, reason="fixture", finished_at=reg.registered_at)
    assert len(manager.store.list_reservations(reg.trial_id)) == 4

