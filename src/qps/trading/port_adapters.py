"""Concrete adapters for the cross-module trading ports.

These adapters compose the already-tested M4--M6 primitives.  They provide a
real, safe local/Paper DI graph while preserving the important boundaries:
orders are journaled before dispatch, the default lifecycle is not enabled,
and risk failures never reach the broker.
"""

from __future__ import annotations

import json
import threading
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import Action, Direction, OrderSide, RiskVerdict
from qps.core.models.account import PortfolioRisk, StrategyAllocation
from qps.core.models.intent import TradingIntent
from qps.core.models.risk import RiskCheckResult, RiskRule, RuleCheckResult
from qps.trading.advanced import AllocationPlanner
from qps.trading.kernel import (
    CapitalPolicyService,
    ExecutionEngine,
    OrderStateMachine,
    OutboxDispatcher,
    ReconciliationService,
    TradingLifecycle,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from qps.infra.storage.trading_journal import JournalDB


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(UTC)


def intent_side(intent: TradingIntent) -> OrderSide:
    """Map a validated position direction/action pair to an order side."""
    opening = intent.action in {Action.OPEN, Action.INCREASE}
    if intent.direction == Direction.LONG:
        return OrderSide.BUY if opening else OrderSide.SELL
    if intent.direction == Direction.SHORT:
        return OrderSide.SELL if opening else OrderSide.BUY
    raise ValueError("FLAT intent 不能直接生成订单")


class SignalEngineAdapter:
    """Validate the already-typed Intent and enforce expiry at ingress."""

    def __init__(self, *, clock: Callable[[], datetime] = _now) -> None:
        self._clock = clock

    async def ingest_intent(self, intent: TradingIntent) -> dict[str, Any]:
        if not isinstance(intent, TradingIntent):
            return {"status": "REJECTED", "reason": "intent 类型不受信任"}
        now = self._clock()
        if intent.valid_until <= now:
            return {"status": "REJECTED", "intent_id": intent.intent_id, "reason": "Intent 已过期"}
        try:
            side = intent_side(intent).value
        except ValueError as exc:
            return {"status": "REJECTED", "intent_id": intent.intent_id, "reason": str(exc)}
        return {"status": "ACCEPTED", "intent_id": intent.intent_id, "side": side}


class PortfolioManagerAdapter:
    """Single-account portfolio adapter backed by durable AllocationPlans."""

    def __init__(self, db_path: Path, *, account_id: str = "paper") -> None:
        self.account_id = account_id
        self._planner = AllocationPlanner(db_path)
        self._allocations: dict[str, StrategyAllocation] = {}

    async def on_intent(self, intent: TradingIntent) -> list[dict[str, Any]]:
        quantity = intent.quantity
        if quantity is None:
            return []
        return [{
            "intent_id": intent.intent_id,
            "strategy_id": intent.strategy_id,
            "deployment_id": intent.deployment_id,
            "account_id": self.account_id,
            "instrument_id": intent.instrument_id,
            "side": intent_side(intent).value,
            "quantity": quantity,
            "order_type": intent.order_type.value,
            "limit_price": intent.limit_price,
        }]

    async def get_allocation(self, account_id: str) -> StrategyAllocation:
        for allocation in self._allocations.values():
            if allocation.account_id == account_id:
                return allocation
        allocation = StrategyAllocation(
            allocation_id=_id("alloc"),
            account_id=account_id,
            strategy_id="single",
            weight=Decimal("1"),
            capital_limit=Decimal("0"),
        )
        self._allocations[allocation.allocation_id] = allocation
        return allocation

    async def get_portfolio_risk(self, account_id: str) -> PortfolioRisk:
        return PortfolioRisk(
            account_id=account_id,
            snapshot_ts=_now(),
            total_notional=Decimal("0"),
        )

    async def merge_positions(self, account_id: str) -> None:
        if not account_id:
            raise ValueError("account_id 不能为空")

    @property
    def planner(self) -> AllocationPlanner:
        return self._planner


class RiskEngineAdapter:
    """Minimal synchronous pre-trade rules with explicit conservative state."""

    def __init__(self, *, max_single_notional: Decimal, max_total_notional: Decimal) -> None:
        self.max_single_notional = Decimal(max_single_notional)
        self.max_total_notional = Decimal(max_total_notional)
        self._rules: dict[str, RiskRule] = {}
        self._frozen = False
        self._last_check: RiskCheckResult | None = None

    async def pre_trade_check(self, order_request: Mapping[str, Any]) -> RiskCheckResult:
        intent_id = str(order_request.get("intent_id", "unknown"))
        notional = Decimal(str(order_request.get("notional", "0")))
        checks = [
            RuleCheckResult(
                rule_id="risk.frozen",
                passed=not self._frozen,
                detail="risk engine frozen" if self._frozen else None,
            ),
            RuleCheckResult(
                rule_id="risk.single_notional",
                passed=Decimal("0") < notional <= self.max_single_notional,
                detail=f"notional={notional} max={self.max_single_notional}",
            ),
            RuleCheckResult(
                rule_id="risk.total_notional",
                passed=notional <= self.max_total_notional,
                detail=f"notional={notional} max={self.max_total_notional}",
            ),
        ]
        verdict = RiskVerdict.ALLOW if all(item.passed for item in checks) else RiskVerdict.BLOCK
        result = RiskCheckResult(
            check_id=_id("risk"),
            intent_id=intent_id,
            rule_results=checks,
            verdict=verdict,
            ts=_now(),
        )
        self._last_check = result
        return result

    async def on_order_update(self, order: Any) -> None:
        if getattr(order, "status", None) in {"UNKNOWN", "REJECTED"}:
            self._frozen = True

    async def on_market_update(self, event: Any) -> None:
        return None

    async def register_rule(self, rule: RiskRule) -> None:
        if rule.rule_id in self._rules and self._rules[rule.rule_id] != rule:
            raise ValueError("RiskRule 不可覆盖")
        self._rules[rule.rule_id] = rule

    async def status(self) -> dict[str, Any]:
        return {"frozen": self._frozen, "rules": sorted(self._rules), "last_check_id": self._last_check.check_id if self._last_check else None}


class ExecutionEngineAdapter:
    """Port facade around durable reservation/outbox execution."""

    def __init__(
        self,
        journal: JournalDB,
        broker: Any,
        *,
        lifecycle: TradingLifecycle,
        policy: CapitalPolicyService,
    ) -> None:
        self.journal = journal
        self.broker = broker
        self.lifecycle = lifecycle
        self._ensure_scope_schema()
        self._engine = ExecutionEngine(journal, lifecycle=lifecycle, policy=policy)
        self._dispatcher = OutboxDispatcher(journal, broker)
        self._state_machine = OrderStateMachine(journal)
        self._reconciliation = ReconciliationService(journal, lifecycle)

    def _ensure_scope_schema(self) -> None:
        """Persist deployment scope before the outbox can cause a side effect."""
        with self.journal.transaction() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS adapter_order_scopes (
                    command_id TEXT PRIMARY KEY,
                    deployment_id TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS adapter_order_scopes_no_update
                BEFORE UPDATE ON adapter_order_scopes FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'adapter order scope append-only'); END;
                CREATE TRIGGER IF NOT EXISTS adapter_order_scopes_no_delete
                BEFORE DELETE ON adapter_order_scopes FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'adapter order scope append-only'); END;
                """
            )

    async def submit_order(self, order_request: Mapping[str, Any], risk_result: RiskCheckResult) -> dict[str, Any]:
        command = await self._engine.submit_order(order_request, risk_result)
        deployment_id = str(order_request.get("deployment_id", ""))
        if not deployment_id:
            raise ValueError("订单必须绑定 deployment_id")
        with self.journal.transaction() as conn:
            conn.execute(
                "INSERT INTO adapter_order_scopes(command_id,deployment_id,created_at) VALUES (?,?,?)",
                (command["command_id"], deployment_id, _now().isoformat()),
            )
        broker_results = await self._dispatcher.dispatch()
        return {"command": command, "broker_result": broker_results[-1] if broker_results else None}

    async def cancel(self, deployment_id: str | None = None, instrument_id: str | None = None) -> list[dict[str, Any]]:
        if not deployment_id and not instrument_id:
            raise ValueError("deployment_id 或 instrument_id 至少需要一个")
        clauses: list[str] = []
        args: list[str] = []
        if deployment_id:
            clauses.append("s.deployment_id=?")
            args.append(deployment_id)
        if instrument_id:
            clauses.append("oc.instrument_id=?")
            args.append(instrument_id)
        with self.journal.connect() as conn:
            rows = conn.execute(
                "SELECT oc.client_order_id,oc.status,oc.instrument_id "
                "FROM order_commands oc "
                "JOIN adapter_order_scopes s ON s.command_id=oc.command_id "
                "WHERE " + " AND ".join(clauses),
                tuple(args),
            ).fetchall()
        results: list[dict[str, Any]] = []
        for row in rows:
            if row["status"] in {"FILLED", "CANCELED", "REJECTED", "EXPIRED"}:
                continue
            broker_result = dict(await self.broker.cancel_order(row["client_order_id"]))
            if broker_result.get("status") in {"CANCELED", "REJECTED", "EXPIRED"}:
                self._state_machine.apply(
                    {
                        "report_id": _id("cancel"),
                        "client_order_id": row["client_order_id"],
                        "broker_order_id": broker_result.get("broker_order_id"),
                        "status": broker_result["status"],
                    }
                )
            results.append(broker_result)
        return results

    async def reconcile(self, account_id: str) -> dict[str, Any]:
        remote = {"account_id": account_id, "orders": await self.broker.get_positions()}
        return self._reconciliation.run(account_id, local={}, remote=remote, snapshot_ref="paper-adapter")

    async def status(self) -> dict[str, Any]:
        return {"mode": self.lifecycle.mode.value, "broker": self.broker.status().model_dump(mode="json")}


class MonitoringAdapter:
    """Non-blocking in-process monitor with optional append-only JSONL output."""

    def __init__(self, audit_path: Path | None = None) -> None:
        self.audit_path = audit_path
        self.events: list[dict[str, Any]] = []
        self.alerts: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    async def record(self, event: Any) -> None:
        item = event.model_dump(mode="json") if hasattr(event, "model_dump") else dict(event)
        with self._lock:
            self.events.append(item)
            self._append(item)

    async def snapshot(self, kind: str) -> None:
        if not kind:
            raise ValueError("snapshot kind 不能为空")
        await self.alert("INFO", "snapshot", {"kind": kind})

    async def alert(self, level: str, message: str, context: dict[str, Any]) -> None:
        item = {"level": level, "message": message, "context": dict(context), "ts": _now().isoformat()}
        with self._lock:
            self.alerts.append(item)
            self._append(item)

    def _append(self, item: dict[str, Any]) -> None:
        if self.audit_path is not None:
            self.audit_path.parent.mkdir(parents=True, exist_ok=True)
            with self.audit_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n")


__all__ = [
    "ExecutionEngineAdapter",
    "MonitoringAdapter",
    "PortfolioManagerAdapter",
    "RiskEngineAdapter",
    "SignalEngineAdapter",
    "intent_side",
]
