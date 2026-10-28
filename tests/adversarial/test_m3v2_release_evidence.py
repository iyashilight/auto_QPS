from datetime import UTC, datetime

import pytest

from qps.core.errors import DataError
from qps.infra.storage.research_db import ResearchStore
from qps.research.release import ReleaseEvidenceBoundary


def test_unselected_or_nonformal_report_cannot_cross_release_boundary(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")
    boundary = ReleaseEvidenceBoundary(store)
    with pytest.raises(DataError):
        boundary.select_for_release(release_id="candidate", report_id="missing", actor="system", reason="auto", selected_at=datetime.now(UTC))

