from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from qps.core.errors import BacktestError
from qps.research.research_run_service import ResearchRunService
from tests.m3_helpers import (
    FormalEngineFake,
    ReleaseStoreFake,
    budget_manager,
    candidate,
    registration,
    release,
)


@pytest.mark.asyncio
async def test_rerun_fixed_lineage_and_outputs(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    engine = FormalEngineFake()
    service = ResearchRunService(
        research_store=budget.store, budget_port=budget, release_store=ReleaseStoreFake(release()),
        event_driven_engine=engine,
    )
    handle = await service.register_and_reserve_trial(
        registration=registration("rerun"), candidate=candidate(), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-1",
    )
    await service.run_formal(handle, request={"strategy_id": "strategy-1"})
    result = await service.rerun("rerun")
    assert result.engine_config["trades_hash"] == "trades-1"


@pytest.mark.asyncio
async def test_rerun_lineage_mismatch_fails_closed(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    engine = FormalEngineFake()
    service = ResearchRunService(
        research_store=budget.store, budget_port=budget, release_store=ReleaseStoreFake(release()),
        event_driven_engine=engine,
    )
    handle = await service.register_and_reserve_trial(
        registration=registration("rerun-mismatch"), candidate=candidate(), actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=registration().registered_at,
        frozen_release_id="release-1",
    )
    await service.run_formal(handle, request={"strategy_id": "strategy-1"})
    engine.mismatch = True
    with pytest.raises(BacktestError, match="不一致"):
        await service.rerun("rerun-mismatch")
