import sqlite3
from datetime import UTC, datetime

import pytest
from tests.m3_helpers import budget_manager, registration

from qps.core.constants import TrialStatus
from qps.core.models.foundation import TrialEvent


def test_research_records_cannot_be_updated_or_deleted(tmp_path) -> None:
    manager = budget_manager(tmp_path)
    store = manager.store
    reg = registration("append-only")
    store.append_registration(reg)
    with pytest.raises(sqlite3.IntegrityError), store.connect(immediate=True) as conn:
        conn.execute("UPDATE research_trials SET payload=? WHERE trial_id=?", ("{}", reg.trial_id))
    with pytest.raises(sqlite3.IntegrityError), store.connect(immediate=True) as conn:
        conn.execute("DELETE FROM research_trials WHERE trial_id=?", (reg.trial_id,))
    store.append_trial_event(TrialEvent(event_id="append-only:FAILED", trial_id=reg.trial_id, status=TrialStatus.FAILED, reason="test", occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)))
    with pytest.raises(sqlite3.IntegrityError), store.connect(immediate=True) as conn:
        conn.execute("DELETE FROM research_trial_events WHERE trial_id=?", (reg.trial_id,))
