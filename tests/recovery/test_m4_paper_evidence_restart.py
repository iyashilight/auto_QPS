from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.adversarial.test_m4_paper_evidence_lineage import _parts

from qps.trading.paper_evidence import PaperEvidenceError, PaperEvidenceImporter, PaperEvidenceStore


def test_identical_reimport_is_idempotent_and_boot_change_needs_bridge(tmp_path: object) -> None:
    store, lineage, collector = _parts(tmp_path)  # type: ignore[arg-type]
    first = collector.append_observation(recorded_at=datetime.now(UTC), monotonic_ns=1, input_snapshot_ref="input-1", intent_event_hash="intent", outcome_ref="outcome")
    target = PaperEvidenceStore(tmp_path / "target.db", journal_id=store.journal_id)  # type: ignore[operator]
    importer = PaperEvidenceImporter(target, lineage, collector.public_key)
    importer.import_records([first])
    assert importer.import_records([first]) == 1
    changed = dict(first)
    changed["observation_id"] = "new"
    changed["boot_id"] = "other"
    with pytest.raises(PaperEvidenceError):
        importer.import_records([changed])
