import asyncio
from decimal import Decimal

from qps.core.constants import ResearchActorType
from qps.research.research_run_service import ResearchRunService
from tests.m3_helpers import (
    FormalEngineFake,
    ReleaseStoreFake,
    budget_manager,
    candidate,
    registration,
    release,
)


def test_evidence_dimensions_are_independent_and_alpha_is_display_only(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    service = ResearchRunService(
        research_store=budget.store, budget_port=budget,
        release_store=ReleaseStoreFake(release()),
        event_driven_engine=FormalEngineFake(),
    )
    asyncio.run(service.register_and_reserve_trial(
        registration=registration("evidence"), candidate=candidate("evidence"),
        actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=0,
        reserved_at=registration("evidence").registered_at, frozen_release_id="release-1",
        request={"strategy_id": "strategy-1", "version_no": 1},
    ))
    report = budget.store.get_report("evidence-run-1")
    assert report.formal is True
    assert report.evidence_quality is not report.expected_edge
    assert report.expected_edge is not report.risk_estimate
    assert report.alpha_score is None
    assert "capital" not in report.model_dump(mode="json")
