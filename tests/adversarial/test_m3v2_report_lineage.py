from decimal import Decimal

import pytest
from tests.m3v2_helpers import candidate, registration, service

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError


@pytest.mark.asyncio
async def test_report_cannot_be_forged_or_cross_bound(tmp_path) -> None:
    runner, budget, _ = service(tmp_path)
    artifact = await runner.run_formal_artifact(
        await runner.register_and_reserve_trial(
            registration=registration("report"), candidate=candidate("report"), actor_type=ResearchActorType.SCRIPT,
            requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
            frozen_release_id="release-v2",
        ), request={"strategy_id": "strategy-v2"},
    )
    report = budget.store.get_report(f"evidence-{artifact.run_id}")
    forged = report.model_copy(update={"lineage": report.lineage.model_copy(update={"clock": "forged"})})
    with pytest.raises(DataError):
        budget.store.append_report(forged)
    with pytest.raises(DataError):
        budget.store.append_report(report.model_copy(update={"formal_artifact_id": "other"}))

