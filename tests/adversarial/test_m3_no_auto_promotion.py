from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qps.core.errors import DataError
from qps.core.models.foundation import EvidenceQuality, EvidenceReport, ExpectedEdge, RiskEstimate
from qps.infra.storage.research_db import ResearchStore
from qps.research.release import ReleaseEvidenceBoundary


def test_alpha_or_unselected_report_cannot_auto_promote(tmp_path) -> None:
    store = ResearchStore(tmp_path / "promotion.db")
    unselected = EvidenceReport(report_id="unselected", evidence_quality=EvidenceQuality(data_quality=Decimal("1"), sample_size=1, stability=Decimal("1"), leakage_risk=Decimal("0"), reproducibility=Decimal("1")), expected_edge=ExpectedEdge(net_expected_return=Decimal("1"), confidence_low=Decimal("0"), confidence_high=Decimal("1"), benchmark_ref="cash"), risk_estimate=RiskEstimate(max_drawdown=Decimal("0"), tail_loss=Decimal("0"), leverage_sensitivity=Decimal("0"), liquidity_capacity=Decimal("0")), experiment_refs=("vectorized-run",), created_at=datetime(2026, 1, 1, tzinfo=UTC), formal=False, alpha_score=Decimal("0.99"))
    store.append_report(unselected)
    with pytest.raises(DataError):
        ReleaseEvidenceBoundary(store).select_for_release(release_id="release", report_id="unselected", actor="system", reason="alpha score", selected_at=datetime(2026, 1, 1, tzinfo=UTC))
