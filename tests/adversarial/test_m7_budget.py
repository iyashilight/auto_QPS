import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from tests.m3v2_helpers import NOW, budget_manager, candidate, registration

from qps.agents.agent_core import AgentPermissionError
from qps.agents.research import AgentResearchService
from qps.core.constants import ResearchActorType, ResearchScope
from qps.core.models.foundation import ResearchBudget


def test_unbudgeted_ai_proposal_is_rejected() -> None:
    service = AgentResearchService()
    with pytest.raises(AgentPermissionError):
        service.propose(hypothesis="h", candidate={"x": 1})


def test_formal_ai_proposal_reserves_shared_budget_before_draft() -> None:
    with TemporaryDirectory() as directory:
        manager = budget_manager(Path(directory))
        manager.add_budget(ResearchBudget(
            budget_id="actor-ai-v2", scope=ResearchScope.ACTOR, scope_ref=ResearchActorType.AI.value,
            period="v2", max_trials=10, max_compute=Decimal("100"), max_blind_reveals=2,
            similarity_policy_version="family-v2", approved_by="fixture",
            valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(days=2),
        ))
        service = AgentResearchService(budget=manager)
        proposal = asyncio.run(service.propose_formal(
            registration=registration("agent-trial-v2"), candidate=candidate("agent-candidate-v2"),
            requested_compute=Decimal("2"), blind_reveals=1, reserved_at=NOW,
        ))
        assert proposal["status"] == "DRAFT"
        assert proposal["budget_consumptions"]
        remaining = asyncio.run(manager.remaining("global-v2"))
        assert remaining.max_trials == 99
        with pytest.raises(AgentPermissionError):
            asyncio.run(service.propose_formal(
                registration=registration("agent-trial-v2-retry"), candidate=candidate("agent-candidate-v2"),
                requested_compute=Decimal("2"), blind_reveals=1, reserved_at=NOW,
            ))
