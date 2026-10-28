from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.adversarial.test_m4_paper_evidence_lineage import _parts

from qps.trading.paper_evidence import PaperEvidenceError, PaperReadinessEvaluator


def test_fixture_and_unresolved_incident_cannot_pass(tmp_path: object) -> None:
    store, _, collector = _parts(tmp_path)  # type: ignore[arg-type]
    now = datetime.now(UTC)
    collector.append_observation(recorded_at=now, monotonic_ns=1, input_snapshot_ref="input-1", intent_event_hash="intent", outcome_ref="outcome")
    with pytest.raises(PaperEvidenceError):
        PaperReadinessEvaluator().evaluate_verified_window(store.records(), deployment_id="dep", public_key_fingerprint=collector.store.records()[0]["signer_fingerprint"])
