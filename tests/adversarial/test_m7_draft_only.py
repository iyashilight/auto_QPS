import asyncio
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from tests.m3v2_helpers import NOW, budget_manager, candidate, registration

from qps.agents.research import AgentResearchService
from qps.core.constants import ResearchActorType, ResearchScope
from qps.core.models.foundation import ResearchBudget


def test_agent_proposals_stop_at_draft() -> None:
    with TemporaryDirectory() as directory:
        manager = budget_manager(Path(directory))
        manager.add_budget(ResearchBudget(
            budget_id="actor-ai-draft", scope=ResearchScope.ACTOR, scope_ref=ResearchActorType.AI.value,
            period="v2", max_trials=10, max_compute=Decimal("100"), max_blind_reveals=2,
            similarity_policy_version="family-v2", approved_by="fixture",
            valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(days=2),
        ))
        service = AgentResearchService(budget=manager)
        proposal = asyncio.run(service.propose_formal(
            registration=registration("draft-only-trial"), candidate=candidate("draft-only-candidate"),
            requested_compute=Decimal("1"), blind_reveals=0, reserved_at=NOW,
        ))
        assert proposal["status"] == "DRAFT"
