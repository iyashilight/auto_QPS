from datetime import timedelta
from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType, TrialStatus
from qps.core.models.foundation import TrialEvent
from tests.m3_helpers import budget_manager, candidate, registration


@pytest.mark.asyncio
async def test_all_terminal_outcomes_remain_counted(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    terminal = (TrialStatus.SUCCEEDED, TrialStatus.FAILED, TrialStatus.CANCELLED, TrialStatus.TIMED_OUT)
    for index, status in enumerate(terminal):
        reg = registration(f"accounted-{index}")
        manager.store.append_registration(reg)
        await manager.reserve_trial(registration=reg, candidate=candidate(f"accounted-{index}"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=0, reserved_at=reg.registered_at)
        manager.store.append_trial_event(TrialEvent(event_id=f"{reg.trial_id}:RESERVED", trial_id=reg.trial_id, status=TrialStatus.RESERVED, reason="reserved", occurred_at=reg.registered_at + timedelta(seconds=1)))
        manager.store.append_trial_event(TrialEvent(event_id=f"{reg.trial_id}:RUNNING", trial_id=reg.trial_id, status=TrialStatus.RUNNING, reason="running", occurred_at=reg.registered_at + timedelta(seconds=2)))
        manager.store.append_trial_event(TrialEvent(event_id=f"{reg.trial_id}:{status.value}", trial_id=reg.trial_id, status=status, reason="terminal", occurred_at=reg.registered_at + timedelta(seconds=3)))
    with manager.store.connect() as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM research_reservations").fetchone()["n"] == 16
        assert conn.execute("SELECT COUNT(*) AS n FROM research_trial_events WHERE status IN ('SUCCEEDED','FAILED','CANCELLED','TIMED_OUT')").fetchone()["n"] == 4
