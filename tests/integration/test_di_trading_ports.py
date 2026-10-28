"""Production DI trading-port graph: Paper-only and durable-before-side-effect."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qps.broker.paper import PaperBrokerAdapter
from qps.core.constants import Action, Direction, OrderType, RiskVerdict
from qps.core.models.intent import TradingIntent
from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import CapitalPolicyService, TradingLifecycle, TradingMode
from qps.trading.port_adapters import (
    ExecutionEngineAdapter,
    MonitoringAdapter,
    PortfolioManagerAdapter,
    RiskEngineAdapter,
    SignalEngineAdapter,
)


def test_production_di_registers_concrete_trading_ports(tmp_path, monkeypatch) -> None:
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.trading.gateway import MarketDataGateway
    from qps.trading.runtime.process_runtime import IsolatedStrategyRuntime

    monkeypatch.setenv("QPS_DATA_DIR", str(tmp_path))
    container = build_container(Config(env_vars={"QPS_DATA_DIR": str(tmp_path)}))
    assert isinstance(container.get_port("broker_adapter"), PaperBrokerAdapter)
    assert isinstance(container.get_port("market_gateway"), MarketDataGateway)
    assert isinstance(container.get_port("signal_engine"), SignalEngineAdapter)
    assert isinstance(container.get_port("portfolio_manager"), PortfolioManagerAdapter)
    assert isinstance(container.get_port("risk_engine"), RiskEngineAdapter)
    assert isinstance(container.get_port("execution_engine"), ExecutionEngineAdapter)
    assert isinstance(container.get_port("strategy_runtime"), IsolatedStrategyRuntime)
    assert isinstance(container.get_port("monitoring"), MonitoringAdapter)


def _intent(*, valid_until: datetime) -> TradingIntent:
    return TradingIntent(
        intent_id="intent-di-1",
        strategy_id="strategy-di",
        version_no=1,
        deployment_id="deployment-di",
        instrument_id="BTC-USDT-PERP",
        direction=Direction.LONG,
        action=Action.OPEN,
        quantity=Decimal("1"),
        order_type=OrderType.MARKET,
        valid_until=valid_until,
    )


def test_intent_risk_journal_outbox_paper_flow(tmp_path) -> None:
    now = datetime.now(UTC)
    intent = _intent(valid_until=now + timedelta(minutes=5))
    signal = SignalEngineAdapter(clock=lambda: now)
    assert asyncio.run(signal.ingest_intent(intent))["status"] == "ACCEPTED"

    portfolio = PortfolioManagerAdapter(tmp_path / "trading.db")
    order_request = asyncio.run(portfolio.on_intent(intent))[0]
    order_request["notional"] = Decimal("100")
    risk = RiskEngineAdapter(
        max_single_notional=Decimal("200"),
        max_total_notional=Decimal("500"),
    )
    risk_result = asyncio.run(risk.pre_trade_check(order_request))
    assert risk_result.verdict == RiskVerdict.ALLOW

    journal = JournalDB(tmp_path / "journal.db")
    lifecycle = TradingLifecycle("paper")
    broker = PaperBrokerAdapter(account_id="paper")
    execution = ExecutionEngineAdapter(
        journal,
        broker,
        lifecycle=lifecycle,
        policy=CapitalPolicyService({
            "capital_envelope_by_strategy": {"single": "500"},
            "instrument_limits": {},
        }),
    )

    assert lifecycle.mode == TradingMode.BOOTING
    try:
        asyncio.run(execution.submit_order(order_request, risk_result))
    except RuntimeError as exc:
        assert "不允许新增风险" in str(exc)
    else:
        raise AssertionError("未对账/人工启用时不能提交 Paper 订单")
    assert broker.place_calls == 0

    lifecycle.start(lambda: True)
    lifecycle.enable(approved_by="test", reason="paper integration")
    result = asyncio.run(execution.submit_order(order_request, risk_result))
    assert result["command"]["status"] == "RISK_RESERVED"
    assert result["broker_result"]["client_order_id"] == result["command"]["client_order_id"]
    assert broker.place_calls == 1

    with journal.connect() as conn:
        command = conn.execute(
            "SELECT status FROM order_commands WHERE client_order_id=?",
            (result["command"]["client_order_id"],),
        ).fetchone()
        outbox = conn.execute("SELECT published_at FROM outbox").fetchone()
    assert command["status"] == "PENDING_ACK"
    assert outbox["published_at"] is not None
    canceled = asyncio.run(execution.cancel(deployment_id="deployment-di"))
    assert canceled[0]["client_order_id"] == result["command"]["client_order_id"]
    assert broker.cancel_calls == 1
    with journal.connect() as conn:
        command = conn.execute(
            "SELECT status FROM order_commands WHERE client_order_id=?",
            (result["command"]["client_order_id"],),
        ).fetchone()
        report = conn.execute(
            "SELECT status FROM execution_reports WHERE client_order_id=?",
            (result["command"]["client_order_id"],),
        ).fetchone()
    assert command["status"] == "CANCELED"
    assert report["status"] == "CANCELED"


def test_expired_signal_risk_block_and_monitoring_are_fail_closed(tmp_path) -> None:
    now = datetime.now(UTC)
    expired = _intent(valid_until=now - timedelta(seconds=1))
    assert asyncio.run(SignalEngineAdapter(clock=lambda: now).ingest_intent(expired))["status"] == "REJECTED"

    risk = RiskEngineAdapter(max_single_notional=Decimal("10"), max_total_notional=Decimal("10"))
    blocked = asyncio.run(risk.pre_trade_check({"intent_id": "too-large", "notional": "11"}))
    assert blocked.verdict == RiskVerdict.BLOCK

    monitoring = MonitoringAdapter(tmp_path / "monitoring.jsonl")
    asyncio.run(monitoring.alert("CRITICAL", "risk blocked", {"intent_id": "too-large"}))
    assert len(monitoring.alerts) == 1
    assert "risk blocked" in (tmp_path / "monitoring.jsonl").read_text(encoding="utf-8")
