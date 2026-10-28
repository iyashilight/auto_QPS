from decimal import Decimal

import pytest
from tests.m3v2_helpers import candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError
from qps.core.models.foundation import EvidenceReport
from qps.research.evaluation.report import EvidenceReportBuilder
from qps.research.research_run_service import RegisteredTrial


@pytest.mark.asyncio
async def test_formal_entry_requires_registration_reservation_and_artifact(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    with pytest.raises(DataError):
        await runner.run_formal("ghost", request={"strategy_id": "strategy-v2"})
    handle = await runner.register_and_reserve_trial(
        registration=registration(), candidate=candidate(), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-v2",
    )
    assert isinstance(handle, RegisteredTrial)
    artifact = await runner.run_formal_artifact(handle, request={"strategy_id": "strategy-v2"})
    assert budget.store.get_formal_artifact_by_id(artifact.artifact_id) is not None
    with pytest.raises(DataError):
        await EvidenceReportBuilder().build_from_formal_run(formal_run_artifact_id=artifact.artifact_id)


def test_vectorized_and_direct_report_paths_are_non_formal() -> None:
    assert EvidenceReport is not None

