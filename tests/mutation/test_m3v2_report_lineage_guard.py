from datetime import UTC, datetime
from decimal import Decimal

import pytest
from tests.m3v2_helpers import budget_manager, registration

from qps.core.errors import DataError
from qps.core.models.foundation import EvidenceQuality, EvidenceReport, ExpectedEdge, RiskEstimate


def test_append_report_rechecks_lineage_boundary(tmp_path) -> None:
    store = budget_manager(tmp_path).store
    report = EvidenceReport(
        report_id="forged", evidence_quality=EvidenceQuality(data_quality=1, sample_size=1, stability=1, leakage_risk=0, reproducibility=1),
        expected_edge=ExpectedEdge(net_expected_return=Decimal("0"), confidence_low=0, confidence_high=0, benchmark_ref="cash"),
        risk_estimate=RiskEstimate(max_drawdown=0, tail_loss=0, leverage_sensitivity=0, liquidity_capacity=0),
        experiment_refs=(registration().trial_id, "run"), created_at=datetime.now(UTC), formal=True,
    )
    with pytest.raises(DataError):
        store.append_report(report)

