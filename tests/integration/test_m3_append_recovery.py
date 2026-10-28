from datetime import UTC, datetime

from qps.core.constants import TrialStatus
from qps.core.models.foundation import TrialEvent
from qps.infra.storage.research_db import ResearchStore
from tests.m3_helpers import registration


def test_restart_replays_append_only_trial_history(tmp_path) -> None:
    store = ResearchStore(tmp_path / "restart.db")
    reg = registration("restart")
    store.append_registration(reg)
    store.append_trial_event(TrialEvent(event_id="restart:FAILED", trial_id="restart", status=TrialStatus.FAILED, reason="crash recovery", occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)))
    restarted = ResearchStore(tmp_path / "restart.db")
    assert restarted.get_registration("restart").trial_id == "restart"
    assert restarted.latest_trial_status("restart") == TrialStatus.FAILED
    assert restarted.get_artifacts("restart") == []
