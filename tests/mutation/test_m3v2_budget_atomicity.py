from decimal import Decimal

import pytest
from tests.m3v2_helpers import budget_manager, candidate, registration

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError


@pytest.mark.asyncio
async def test_scope_failure_does_not_leave_partial_reservation(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    reg = registration("budget-fail")
    with pytest.raises(DataError):
        await manager.reserve_trial(registration=reg, candidate=candidate("budget-fail"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("99999"), blind_reveals=0, reserved_at=reg.registered_at)
    assert manager.store.list_reservations(reg.trial_id) == ()

