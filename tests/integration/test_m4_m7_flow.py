from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.advanced import (
    AgentContainer,
    AgentKnowledgeGateway,
    AgentResearchService,
    AgentSubsystem,
    AgentTaskRunner,
    AllocationPlanner,
    AllocationReconciler,
    KnowledgeStore,
    PortfolioRiskModel,
)
from qps.trading.kernel import (
    InboxProcessor,
    OutboxDispatcher,
    PaperBroker,
    ReconciliationService,
    RiskReservationManager,
    TradingLifecycle,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_m4_paper_durable_order_fill_and_reconcile(tmp_path: Path) -> None:
    journal = JournalDB(tmp_path / "journal.db")
    lifecycle = TradingLifecycle("paper")
    assert lifecycle.start(lambda: True).value == "OBSERVE_ONLY"
    lifecycle.enable(approved_by="owner", reason="integration test")

    command = RiskReservationManager(journal).reserve_and_command(
        account_id="paper",
        deployment_id="dep-1",
        intent_id="intent-1",
        instrument_id="BTCUSDT",
        side="BUY",
        quantity=Decimal("1"),
        limit_price=Decimal("100"),
    )
    broker = PaperBroker()
    asyncio.run(OutboxDispatcher(journal, broker).dispatch())
    result = InboxProcessor(journal).apply(
        broker.fill(
            command["client_order_id"],
            quantity=Decimal("1"),
            price=Decimal("100"),
            account_id="paper",
            instrument_id="BTCUSDT",
        )
    )
    assert result["status"] == "FILLED"
    assert ReconciliationService(journal, lifecycle).run(
        "paper", local={"orders": 1}, remote={"orders": 1}
    )["status"] == "MATCHED"


def test_m6_netting_and_risk_unknown_are_conservative() -> None:
    plan = AllocationPlanner().construct(
        [
            {"intent_id": "a", "instrument_id": "BTCUSDT", "side": "BUY", "quantity": "2"},
            {"intent_id": "b", "instrument_id": "BTCUSDT", "side": "SELL", "quantity": "1"},
        ],
        account_id="paper",
    )
    assert plan["net_targets"] == {"BTCUSDT": "1"}
    assert AllocationReconciler().reconcile(
        account_quantity=Decimal("1"), allocated_quantities=[Decimal("1")]
    )["matched"]
    assert PortfolioRiskModel().evaluate(
        positions={}, model_version=None
    )["action"] == "RISK_REDUCE_ONLY"


def test_m7_is_draft_only_and_disable_is_independent() -> None:
    now = datetime.now(UTC)
    store = KnowledgeStore()
    store.append(content="visible fact", source_available_at=now)
    gateway = AgentKnowledgeGateway(store)
    assert gateway.retrieve("fact", as_of=now + timedelta(seconds=1))
    research = AgentResearchService()
    assert research.propose(hypothesis="h", candidate={"x": 1})["status"] == "DRAFT"
    assert AgentContainer(knowledge=gateway, research=research).build()["trading_mutation"] is False
    assert AgentTaskRunner().run("research", {"q": "fact"}).status.value == "SUCCEEDED"
    assert AgentSubsystem().stop()["live_impact"] is False
