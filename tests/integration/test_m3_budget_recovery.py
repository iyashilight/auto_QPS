from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from tests.m3_helpers import budget_manager, candidate, registration


@pytest.mark.asyncio
async def test_restart_retry_reuses_reservation_without_refund_or_double_debit(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    reg = registration("recovery")
    manager.store.append_registration(reg)
    first = await manager.reserve_trial(registration=reg, candidate=candidate("recovery"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("2"), blind_reveals=0, reserved_at=reg.registered_at)
    restarted = type(manager)(manager.store.db_path)
    second = await restarted.reserve_trial(registration=reg, candidate=candidate("recovery"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("2"), blind_reveals=0, reserved_at=reg.registered_at)
    assert {item.consumption_id for item in first} == {item.consumption_id for item in second}
    remaining = await restarted.remaining("global")
    assert remaining.max_trials == 9
    assert remaining.max_compute == Decimal("98")
