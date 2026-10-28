from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.agents.knowledge import KnowledgeStore


def test_knowledge_after_asof_is_not_visible() -> None:
    with TemporaryDirectory() as directory:
        store = KnowledgeStore(Path(directory) / "knowledge.db")
        now = datetime.now(UTC)
        store.append(content="future", source_available_at=now + timedelta(days=1))
        restarted = KnowledgeStore(Path(directory) / "knowledge.db")
        assert restarted.query("future", as_of=now) == []
