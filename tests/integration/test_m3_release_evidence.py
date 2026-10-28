import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType, TrialStatus
from qps.core.errors import DataError
from qps.core.models.foundation import (
    EvidenceQuality,
    EvidenceReport,
    ExpectedEdge,
    ResearchLineage,
    RiskEstimate,
    TrialEvent,
)
from qps.research.release import ReleaseEvidenceBoundary
from tests.m3_helpers import budget_manager, candidate, registration


def report(report_id: str, formal: bool) -> EvidenceReport:
    return EvidenceReport(report_id=report_id, evidence_quality=EvidenceQuality(data_quality=Decimal("1"), sample_size=1, stability=Decimal("1"), leakage_risk=Decimal("0"), reproducibility=Decimal("1")), expected_edge=ExpectedEdge(net_expected_return=Decimal("0"), confidence_low=Decimal("0"), confidence_high=Decimal("0"), benchmark_ref="cash"), risk_estimate=RiskEstimate(max_drawdown=Decimal("0"), tail_loss=Decimal("0"), leverage_sensitivity=Decimal("0"), liquidity_capacity=Decimal("0")), experiment_refs=("trial", "run"), created_at=datetime(2026, 1, 1, tzinfo=UTC), formal=formal, lineage=ResearchLineage(release_id="release", release_archive_hash="a", data_manifest_hash="d", feature_materialization_hash="f", rule_version="r", cost_version="c", environment_lock="e", seed=1, clock="clock"))


def test_only_human_selected_formal_report_crosses_release_boundary(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    store = budget.store
    trial = registration("trial")
    store.append_registration(trial)
    asyncio.run(budget.reserve_trial(
        registration=trial, candidate=candidate("trial"), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=trial.registered_at,
    ))
    store.append_trial_event(TrialEvent(event_id="trial:RESERVED", trial_id="trial", status=TrialStatus.RESERVED, reason="reserved", occurred_at=datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)))
    store.append_trial_event(TrialEvent(event_id="trial:RUNNING", trial_id="trial", status=TrialStatus.RUNNING, reason="running", occurred_at=datetime(2026, 1, 1, 0, 0, 2, tzinfo=UTC)))
    store.append_artifact(artifact_id="trial:run", trial_id="trial", artifact_type="formal-run", content_hash="hash", payload={"run_id": "run", "engine_config": {"formal": True, "trial_id": "trial"}}, created_at=datetime(2026, 1, 1, 0, 0, 3, tzinfo=UTC))
    store.append_report(report("formal-report", True))
    boundary = ReleaseEvidenceBoundary(store)
    boundary.select_for_release(release_id="new-release", report_id="formal-report", actor="human", reason="explicit selection", selected_at=datetime(2026, 1, 2, tzinfo=UTC))
    assert boundary.can_reference(release_id="new-release", report_id="formal-report") is True
    store.append_report(report("nonformal-report", False))
    with pytest.raises(DataError):
        boundary.select_for_release(release_id="bad-release", report_id="nonformal-report", actor="system", reason="auto", selected_at=datetime(2026, 1, 2, tzinfo=UTC))
