import pytest

from qps.agents.agent_core import AgentPermissionError
from qps.agents.container import AgentContainer
from qps.agents.gateway import AgentGateway, AgentKnowledgeGateway
from qps.agents.knowledge import KnowledgeStore
from qps.agents.research import AgentResearchService


def test_agent_has_no_broker_or_ledger_mutation_capability() -> None:
    container = AgentContainer(knowledge=AgentKnowledgeGateway(KnowledgeStore()), research=AgentResearchService())
    assert container.build()["trading_mutation"] is False
    gateway = AgentGateway(KnowledgeStore())
    with pytest.raises(AgentPermissionError):
        gateway.call("broker.place_order")


def test_network_allowlist_and_secret_redaction_are_enforced() -> None:
    captured: dict[str, object] = {}

    def handler(**kwargs: object) -> str:
        captured.update(kwargs)
        return "ok"

    gateway = AgentGateway(
        KnowledgeStore(), handlers={"network.fetch": handler}, allowed_domains={"approved.example"},
    )
    with pytest.raises(AgentPermissionError):
        gateway.call("network.fetch", domain="unapproved.example", api_secret="secret")
    assert gateway.call("network.fetch", domain="approved.example", api_secret="secret") == "ok"
    assert captured["api_secret"] == "[REDACTED]"
