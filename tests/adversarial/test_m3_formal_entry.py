from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.m3_helpers import (
    FormalEngineFake,
    ReleaseStoreFake,
    budget_manager,
    candidate,
    registration,
    release,
)

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError, DataError
from qps.core.models.foundation import (
    EvidenceQuality,
    EvidenceReport,
    ExpectedEdge,
    ResearchLineage,
    RiskEstimate,
)
from qps.infra.storage.research_db import ResearchStore
from qps.research.backtest.event_driven import EventDrivenBacktestEngine
from qps.research.evaluation.report import EvidenceReportBuilder
from qps.research.research_run_service import ResearchRunService


@pytest.mark.asyncio
async def test_direct_or_nonformal_output_cannot_make_evidence() -> None:
    with pytest.raises(DataError, match="non-formal"):
        EvidenceReportBuilder().build(SimpleNamespace(engine_config={"formal": False}), lineage=None, trial_id="x")  # type: ignore[arg-type]
    with pytest.raises(DataError, match="authorization"):
        EvidenceReportBuilder().build(SimpleNamespace(engine_config={"formal": True}), lineage=None, trial_id="x")  # type: ignore[arg-type]


def test_direct_formal_engine_and_report_store_cannot_bypass_service(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")

    class GateContainer:
        def get_port(self, name: str):
            if name == "research_store":
                return store
            raise KeyError(name)

    engine = EventDrivenBacktestEngine(GateContainer())

    async def invoke() -> None:
        with pytest.raises(BacktestError, match=r"authorization|trial"):
            await engine.run_formal(
                {"formal": True, "trial_id": "ghost", "release_id": "release-1"},
                trial_id="ghost", release=release(),
            )

    import asyncio

    asyncio.run(invoke())
    report = EvidenceReport(
        report_id="ghost-report",
        evidence_quality=EvidenceQuality(data_quality=Decimal("1"), sample_size=1, stability=Decimal("1"), leakage_risk=Decimal("0"), reproducibility=Decimal("1")),
        expected_edge=ExpectedEdge(net_expected_return=Decimal("0"), confidence_low=Decimal("0"), confidence_high=Decimal("0"), benchmark_ref="cash"),
        risk_estimate=RiskEstimate(max_drawdown=Decimal("0"), tail_loss=Decimal("0"), leverage_sensitivity=Decimal("0"), liquidity_capacity=Decimal("0")),
        experiment_refs=("ghost", "ghost-run"), created_at=registration().registered_at, formal=True,
        lineage=ResearchLineage(release_id="release-1", release_archive_hash="archive-1", data_manifest_hash="data-1", feature_materialization_hash="feature-1", rule_version="rule-1", cost_version="cost-1", environment_lock="env-1", seed=42, clock="clock-1"),
    )
    with pytest.raises(DataError, match="trial 不存在"):
        store.append_report(report)


@pytest.mark.asyncio
async def test_formal_service_requires_registration_and_reservation(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    store = budget.store
    service = ResearchRunService(
        research_store=store, budget_port=budget, release_store=ReleaseStoreFake(release()),
        event_driven_engine=FormalEngineFake(),
    )
    with pytest.raises(DataError, match=r"trial 不存在|reservation|RESERVED"):
        await service.run_formal("missing-trial", request={})
    handle = await service.register_and_reserve_trial(
        registration=registration(), candidate=candidate(), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-1",
    )
    assert handle.status.value == "RESERVED"
