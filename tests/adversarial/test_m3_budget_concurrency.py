import asyncio
from decimal import Decimal

import pytest
from tests.m3_helpers import budget_manager, candidate, registration

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError


@pytest.mark.asyncio
async def test_concurrent_reservation_cannot_exceed_trial_limit(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    for budget_id in ("global", "family", "feature", "actor"):
        with manager.store.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM research_budgets WHERE budget_id=?", (budget_id,)).fetchone()
            assert row is not None
            import json
            payload = json.loads(row["payload"])
            payload["max_trials"] = 1
            conn.execute("UPDATE research_budgets SET payload=? WHERE budget_id=?", (json.dumps(payload, sort_keys=True), budget_id))

    async def reserve(trial_id: str):
        reg = registration(trial_id)
        manager.store.append_registration(reg)
        return await manager.reserve_trial(registration=reg, candidate=candidate(trial_id), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=0, reserved_at=reg.registered_at)

    results = await asyncio.gather(reserve("concurrent-a"), reserve("concurrent-b"), return_exceptions=True)
    assert sum(isinstance(item, tuple) for item in results) == 1
    assert len(manager.store.list_reservations("concurrent-a")) + len(manager.store.list_reservations("concurrent-b")) == 4
    assert any(isinstance(item, DataError) for item in results)
