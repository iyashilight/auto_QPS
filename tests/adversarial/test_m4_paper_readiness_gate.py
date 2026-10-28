from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.adversarial.test_m4_paper_evidence_lineage import _parts

from qps.trading.paper_evidence import PaperEvidenceIncompleteError, PaperReadinessEvaluator


def test_fixed_real_window_rejects_fixture_and_caller_thresholds(tmp_path: object) -> None:
    store, lineage, collector = _parts(tmp_path)  # type: ignore[arg-type]
    collector.append_observation(recorded_at=datetime.now(UTC), monotonic_ns=1, input_snapshot_ref="input-1", intent_event_hash="intent", outcome_ref="outcome")
    with pytest.raises(PaperEvidenceIncompleteError):
        PaperReadinessEvaluator().evaluate_verified_window(store.records(), deployment_id=lineage.deployment_id, public_key_fingerprint=lineage.signer_fingerprint)
