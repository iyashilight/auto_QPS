from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qps.core.constants import ResearchActorType
from qps.core.errors import DataError
from qps.research.challenge import BlindChallengeRunner, BlindDatasetVault
from tests.m3v2_helpers import budget_manager, candidate, registration


@pytest.mark.asyncio
async def test_blind_reveal_restart_keeps_result_and_quota_consumed(tmp_path) -> None:
    budget = budget_manager(tmp_path)
    reg = registration("blind-restart")
    await budget.reserve_trial(registration=reg, candidate=candidate("blind-restart"), actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"), blind_reveals=1, reserved_at=reg.registered_at)
    runner = BlindChallengeRunner(vault=BlindDatasetVault({"interval_key": "2025-Q4", "answer": "secret", "passed": True}), budget_port=budget, research_store=budget.store)
    result = await runner.run(frozen_release_id="release-v2", trial_id=reg.trial_id, requested_at=datetime.now(UTC))
    recovered = BlindChallengeRunner(vault=runner._vault, budget_port=budget, research_store=budget.store)
    with pytest.raises(DataError):
        await recovered.run(frozen_release_id="release-v2", trial_id=reg.trial_id, requested_at=datetime.now(UTC))
    assert result.result_hash
