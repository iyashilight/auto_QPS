from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.adversarial.test_m4_paper_evidence_lineage import _parts

from qps.trading.paper_evidence import PaperEvidenceError, PaperEvidenceImporter, PaperEvidenceStore


def test_tampered_signature_fails_closed(tmp_path: object) -> None:
    store, lineage, collector = _parts(tmp_path)  # type: ignore[arg-type]
    record = collector.append_observation(recorded_at=datetime.now(UTC), monotonic_ns=1, input_snapshot_ref="input-1", intent_event_hash="intent", outcome_ref="outcome")
    record["signature"] = "00" * 64
    importer = PaperEvidenceImporter(PaperEvidenceStore(tmp_path / "target.db", journal_id=store.journal_id), lineage, collector.public_key)  # type: ignore[operator]
    with pytest.raises(PaperEvidenceError, match="signature"):
        importer.import_records([record])
