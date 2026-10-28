import json
from decimal import Decimal

import pytest
from tests.m3_helpers import budget_manager, candidate, registration

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError


@pytest.mark.asyncio
async def test_any_scope_failure_rolls_back_all_reservations(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    with manager.store.connect(immediate=True) as conn:
        row = conn.execute("SELECT payload FROM research_budgets WHERE budget_id=?", ("global",)).fetchone()
        payload = json.loads(row["payload"])
        payload["max_compute"] = "1"
        conn.execute("UPDATE research_budgets SET payload=? WHERE budget_id=?", (json.dumps(payload, sort_keys=True), "global"))
    reg = registration("atomic")
    manager.store.append_registration(reg)
    with pytest.raises(DataError):
        await manager.reserve_trial(registration=reg, candidate=candidate("atomic"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("2"), blind_reveals=0, reserved_at=reg.registered_at)
    for budget_id in ("global", "family", "feature", "actor"):
        with manager.store.connect() as conn:
            assert conn.execute("SELECT 1 FROM research_reservations WHERE budget_id=?", (budget_id,)).fetchone() is None
