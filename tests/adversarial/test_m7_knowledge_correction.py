from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.agents.knowledge import KnowledgeStore


def test_correction_appends_supersede_record() -> None:
    with TemporaryDirectory() as directory:
        store = KnowledgeStore(Path(directory) / "knowledge.db")
        original = store.append(content="old", source_available_at=datetime.now(UTC))
        corrected = store.supersede(original["record_id"], content="new", available_at=datetime.now(UTC))
        assert corrected["metadata"]["supersedes"] == original["record_id"]
        assert len(KnowledgeStore(Path(directory) / "knowledge.db").records) == 2
