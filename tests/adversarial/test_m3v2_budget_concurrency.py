from decimal import Decimal

import pytest
from tests.m3v2_helpers import budget_manager, candidate, registration

from qps.core.constants import ResearchActorType


@pytest.mark.asyncio
async def test_all_scope_budget_is_server_resolved_and_duplicate_is_idempotent(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    reg = registration("budget")
    values = await manager.reserve_trial(registration=reg, candidate=candidate("budget"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=0, reserved_at=reg.registered_at)
    again = await manager.reserve_trial(registration=reg, candidate=candidate("budget"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=0, reserved_at=reg.registered_at)
    assert {item.budget_id for item in again} == {item.budget_id for item in values}
    assert len(values) == 4 and len(manager.store.list_reservations(reg.trial_id)) == 4
