from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from qps.trading.paper_evidence import (
    EvidenceLineage,
    PaperEvidenceCollector,
    PaperEvidenceError,
    PaperEvidenceImporter,
    PaperEvidenceStore,
    _public,
)

if TYPE_CHECKING:
    from pathlib import Path


def _parts(tmp_path: Path) -> tuple[PaperEvidenceStore, EvidenceLineage, PaperEvidenceCollector]:
    seed = bytes(range(32))
    store = PaperEvidenceStore(tmp_path / "paper.db")
    lineage = EvidenceLineage("dep", "rel", "policy", "paper", store.journal_id, hashlib.sha256(_public(seed)).hexdigest())
    return store, lineage, PaperEvidenceCollector(store, lineage, seed, boot_id="boot")


def test_lineage_and_decision_refs_are_frozen(tmp_path: Path) -> None:
    store, lineage, collector = _parts(tmp_path)
    record = collector.append_observation(recorded_at=datetime.now(UTC), monotonic_ns=1, input_snapshot_ref="input-1", intent_event_hash="intent", outcome_ref="outcome")
    target = PaperEvidenceStore(tmp_path / "import.db", journal_id=store.journal_id)
    importer = PaperEvidenceImporter(target, lineage, collector.public_key)
    importer.import_records([record])
    duplicate = dict(record)
    duplicate["observation_id"] = "different-id"
    with pytest.raises(PaperEvidenceError):
        importer.import_records([duplicate])
