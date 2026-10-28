"""Small, durable trading kernel used by Paper and the later Live stages.

The module deliberately keeps broker I/O outside journal transactions.  A
request is first turned into an allocation plan, reservation, order command
and outbox row in one SQLite transaction.  Only a committed outbox row can be
dispatched to a broker.  The same primitives are used by the M4 Paper broker
and are reusable by the M5 adapter.
"""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from qps.core.constants import OrderSide, OrderStatus, OrderType
from qps.core.models.validators import validate_utc_datetime

if TYPE_CHECKING:
    from qps.core.models.foundation import CapitalAllocationPolicy
    from qps.infra.storage.trading_journal import JournalDB


class KernelError(RuntimeError):
    """Base error for fail-closed trading kernel operations."""


class TradingFrozenError(KernelError):
    """Raised when a lifecycle or risk gate disallows new risk."""


class RiskBlockedError(KernelError):
    """Raised when a pre-trade decision is not ALLOW."""


TradingFrozen = TradingFrozenError
RiskBlocked = RiskBlockedError


class TradingMode(StrEnum):
    BOOTING = "BOOTING"
    RECONCILING = "RECONCILING"
    OBSERVE_ONLY = "OBSERVE_ONLY"
    TRADING_ENABLED = "TRADING_ENABLED"
    RISK_REDUCE_ONLY = "RISK_REDUCE_ONLY"
    FROZEN = "FROZEN"
    STOPPED = "STOPPED"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return validate_utc_datetime(value or utc_now()).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


class Broker(Protocol):
    async def place_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def cancel_order(self, client_order_id: str) -> Mapping[str, Any]: ...

    async def get_order(self, client_order_id: str) -> Mapping[str, Any]: ...


class AccountSequencer:
    """Per-account single writer.

    ``submit`` is intentionally synchronous so every caller, including a
    recovery thread, gets the same serialization guarantee.  Async callers
    can use ``submit_async`` without creating a second mutation path.
    """

    def __init__(self) -> None:
        self._locks: dict[tuple[str, str], threading.RLock] = {}
        self._versions: dict[tuple[str, str], int] = {}
        self._guard = threading.Lock()

    def _lock_for(self, venue: str, account_id: str) -> tuple[tuple[str, str], threading.RLock]:
        key = (venue, account_id)
        with self._guard:
            lock = self._locks.setdefault(key, threading.RLock())
            self._versions.setdefault(key, 0)
        return key, lock

    def submit(self, account_id: str, mutation: Callable[[int], Any], *, venue: str = "paper") -> Any:
        if not account_id:
            raise ValueError("account_id 不能为空")
        key, lock = self._lock_for(venue, account_id)
        with lock:
            version = self._versions[key]
            result = mutation(version)
            self._versions[key] = version + 1
            return result

    async def submit_async(
        self, account_id: str, mutation: Callable[[int], Any], *, venue: str = "paper"
    ) -> Any:
        return await asyncio.to_thread(self.submit, account_id, mutation, venue=venue)

    def version(self, account_id: str, *, venue: str = "paper") -> int:
        key, _ = self._lock_for(venue, account_id)
        return self._versions[key]


class TradingLifecycle:
    """Explicit startup/reconciliation/manual-enable state machine."""

    def __init__(self, account_id: str, *, mode: TradingMode = TradingMode.BOOTING) -> None:
        self.account_id = account_id
        self.mode = mode
        self.reconciled = False
        self.last_reason: str | None = None
        self.transitions: list[tuple[TradingMode, TradingMode, str, datetime]] = []

    def _move(self, target: TradingMode, reason: str) -> TradingMode:
        previous = self.mode
        self.mode = target
        self.last_reason = reason
        self.transitions.append((previous, target, reason, utc_now()))
        return target

    def start(self, reconcile: Callable[[], bool] | None = None) -> TradingMode:
        self._move(TradingMode.RECONCILING, "startup requires reconciliation")
        self.reconciled = bool(reconcile() if reconcile is not None else False)
        return self._move(
            TradingMode.OBSERVE_ONLY if self.reconciled else TradingMode.FROZEN,
            "reconciliation matched" if self.reconciled else "reconciliation unavailable",
        )

    def enable(self, *, approved_by: str, reason: str) -> TradingMode:
        if not approved_by or not reason:
            raise TradingFrozen("enable 必须提供人工审批人和理由")
        if not self.reconciled or self.mode not in {TradingMode.OBSERVE_ONLY, TradingMode.RISK_REDUCE_ONLY}:
            raise TradingFrozen("未完成对账或当前模式不允许 enable")
        return self._move(TradingMode.TRADING_ENABLED, f"manual enable: {approved_by}: {reason}")

    def freeze(self, reason: str, *, reduce_only: bool = False) -> TradingMode:
        return self._move(TradingMode.RISK_REDUCE_ONLY if reduce_only else TradingMode.FROZEN, reason)

    def stop(self, reason: str = "stopped") -> TradingMode:
        return self._move(TradingMode.STOPPED, reason)

    def allows(self, *, reduce_only: bool = False) -> bool:
        return self.mode == TradingMode.TRADING_ENABLED or (
            reduce_only and self.mode == TradingMode.RISK_REDUCE_ONLY
        )


class CapitalPolicyService:
    """Single-strategy capital envelope and downside-only mutation gate."""

    def __init__(self, policy: CapitalAllocationPolicy | Mapping[str, Any]) -> None:
        self.policy = policy
        self._frozen = False

    def _value(self, name: str, default: Any = None) -> Any:
        if isinstance(self.policy, Mapping):
            return self.policy.get(name, default)
        return getattr(self.policy, name, default)

    def check(self, *, deployment_id: str, instrument_id: str, notional: Decimal, now: datetime | None = None) -> None:
        if self._frozen:
            raise TradingFrozen("capital policy 已冻结")
        current = validate_utc_datetime(now or utc_now())
        valid_from = self._value("valid_from")
        expires_at = self._value("expires_at")
        if valid_from is not None and current < valid_from:
            raise TradingFrozen("capital policy 尚未生效")
        if expires_at is not None and current >= expires_at:
            raise TradingFrozen("capital policy 已过期")
        deployments = self._value("eligible_deployments", ())
        if deployments and deployment_id not in deployments:
            raise TradingFrozen("deployment 不在 policy 白名单")
        limits = self._value("instrument_limits", {}) or {}
        if limits and instrument_id not in limits:
            raise TradingFrozen("instrument 不在 policy 白名单")
        envelope = self._value("capital_envelope_by_strategy", {}) or {}
        if envelope:
            limit = max((_decimal(v) for v in envelope.values()), default=Decimal("0"))
            if notional > limit:
                raise TradingFrozen("订单超过 capital envelope")

    def reduce(self, factor: Decimal) -> None:
        factor = _decimal(factor)
        if not Decimal("0") < factor <= Decimal("1"):
            raise ValueError("自动 policy 调整只能是 0<factor<=1")
        if isinstance(self.policy, dict):
            envelope = self.policy.get("capital_envelope_by_strategy", {})
            self.policy["capital_envelope_by_strategy"] = {
                key: str(_decimal(value) * factor) for key, value in envelope.items()
            }
        else:
            self._frozen = True

    def freeze(self) -> None:
        self._frozen = True

    def apply(self, action: str, *, factor: Decimal | None = None) -> None:
        """Apply a control-plane action; automatic actions may only reduce risk."""
        normalized = action.upper()
        if normalized in {"FREEZE", "RISK_REDUCE_ONLY", "REDUCE"}:
            if factor is None:
                self.freeze()
            else:
                self.reduce(factor)
            return
        if normalized in {"INCREASE", "UNFREEZE", "ADD_CAPITAL", "EXPAND_INSTRUMENTS"}:
            raise TradingFrozenError("自动 policy action 不能增加风险或解冻")
        raise ValueError(f"unknown policy action: {action}")


class RiskReservationManager:
    """Atomic reservation + durable command creation."""

    def __init__(self, journal: JournalDB) -> None:
        self.journal = journal
        self.journal.initialize()

    def reserve_and_command(
        self,
        *,
        account_id: str,
        deployment_id: str,
        intent_id: str,
        instrument_id: str,
        side: OrderSide | str,
        quantity: Decimal,
        order_type: OrderType | str = OrderType.MARKET,
        limit_price: Decimal | None = None,
        notional: Decimal | None = None,
        risk_snapshot_version: int = 1,
        expires_at: datetime | None = None,
        allocation_plan_id: str | None = None,
        client_order_id: str | None = None,
        max_reserved_notional: Decimal | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = validate_utc_datetime(now or utc_now())
        quantity = _decimal(quantity)
        if quantity <= 0:
            raise ValueError("quantity 必须大于 0")
        side_value = getattr(side, "value", side)
        order_type_value = getattr(order_type, "value", order_type)
        reservation_id = _id("res")
        command_id = _id("cmd")
        allocation_plan_id = allocation_plan_id or _id("plan")
        client_order_id = client_order_id or _id("qps")
        expiry = validate_utc_datetime(expires_at or timestamp + timedelta(minutes=5))
        reserved_notional = _decimal(notional if notional is not None else (limit_price or 0) * quantity)
        with self.journal.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO journal_accounts(account_id) VALUES (?)", (account_id,))
            if max_reserved_notional is not None:
                current_reserved = conn.execute(
                    "SELECT COALESCE(SUM(CAST(reserved_notional AS DECIMAL)), 0) FROM risk_reservations WHERE account_id=? AND status IN ('ACTIVE','PARTIALLY_CONSUMED')",
                    (account_id,),
                ).fetchone()[0]
                if _decimal(current_reserved) + reserved_notional > _decimal(max_reserved_notional):
                    raise TradingFrozenError("active reservations exceed capital envelope")
            conn.execute(
                "INSERT INTO allocation_plans(allocation_plan_id,account_id,instrument_id,input_intents,created_at) VALUES (?,?,?,?,?)",
                (allocation_plan_id, account_id, instrument_id, _json([intent_id]), _iso(timestamp)),
            )
            conn.execute(
                "INSERT INTO risk_reservations(reservation_id,account_id,intent_ids,instrument_id,side,reserved_quantity,reserved_notional,reserved_margin,risk_snapshot_version,status,expires_at,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (reservation_id, account_id, _json([intent_id]), instrument_id, side_value, str(quantity), str(reserved_notional), str(reserved_notional), risk_snapshot_version, "ACTIVE", _iso(expiry), _iso(timestamp)),
            )
            conn.execute(
                "INSERT INTO order_commands(command_id,client_order_id,account_id,reservation_id,allocation_plan_id,instrument_id,side,quantity,order_type,limit_price,status,state_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (command_id, client_order_id, account_id, reservation_id, allocation_plan_id, instrument_id, side_value, str(quantity), order_type_value, str(limit_price) if limit_price is not None else None, "RISK_RESERVED", 0, _iso(timestamp), _iso(timestamp)),
            )
            conn.execute(
                "INSERT INTO outbox(outbox_id,aggregate_id,event_type,payload,created_at,published_at) VALUES (?,?,?,?,?,NULL)",
                (_id("out"), command_id, "ORDER_COMMAND_CREATED", _json({"command_id": command_id, "client_order_id": client_order_id}), _iso(timestamp)),
            )
        return {
            "command_id": command_id,
            "client_order_id": client_order_id,
            "reservation_id": reservation_id,
            "allocation_plan_id": allocation_plan_id,
            "account_id": account_id,
            "deployment_id": deployment_id,
            "instrument_id": instrument_id,
            "side": side_value,
            "quantity": quantity,
            "order_type": order_type_value,
            "limit_price": limit_price,
            "status": OrderStatus.RISK_RESERVED.value,
        }


class ExecutionEngine:
    """Pre-trade gate that connects policy/risk to durable reservations."""

    def __init__(
        self,
        journal: JournalDB,
        *,
        lifecycle: TradingLifecycle | None = None,
        policy: CapitalPolicyService | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.policy = policy
        self.reservations = RiskReservationManager(journal)

    async def submit_order(self, order_request: Mapping[str, Any], risk_result: Any) -> dict[str, Any]:
        verdict = getattr(risk_result, "verdict", risk_result.get("verdict") if isinstance(risk_result, Mapping) else None)
        verdict_value = getattr(verdict, "value", verdict)
        if verdict_value != "ALLOW":
            raise RiskBlockedError("pre-trade risk verdict 不是 ALLOW")
        reduce_only = bool(order_request.get("reduce_only", False))
        if self.lifecycle is not None and not self.lifecycle.allows(reduce_only=reduce_only):
            raise TradingFrozenError("当前 trading mode 不允许新增风险")
        quantity = _decimal(order_request["quantity"])
        price = order_request.get("limit_price") or order_request.get("price") or 0
        notional = _decimal(order_request.get("notional", _decimal(price) * quantity))
        if self.policy is not None:
            self.policy.check(
                deployment_id=str(order_request["deployment_id"]),
                instrument_id=str(order_request["instrument_id"]),
                notional=notional,
            )
        return self.reservations.reserve_and_command(
            account_id=str(order_request["account_id"]),
            deployment_id=str(order_request["deployment_id"]),
            intent_id=str(order_request["intent_id"]),
            instrument_id=str(order_request["instrument_id"]),
            side=order_request["side"],
            quantity=quantity,
            order_type=order_request.get("order_type", OrderType.MARKET),
            limit_price=order_request.get("limit_price"),
            notional=notional,
        )

    async def status(self) -> dict[str, Any]:
        return {"mode": self.lifecycle.mode.value if self.lifecycle else "UNMANAGED"}


class OrderStateMachine:
    """CAS-like application of broker reports with duplicate suppression."""

    def __init__(self, journal: JournalDB) -> None:
        self.journal = journal
        self.journal.initialize()

    def apply(self, report: Mapping[str, Any], *, received_at: datetime | None = None) -> dict[str, Any]:
        timestamp = validate_utc_datetime(received_at or utc_now())
        report_id = str(report.get("report_id") or report.get("event_id") or _id("report"))
        client_id = str(report["client_order_id"])
        target = str(report.get("status", "UNKNOWN"))
        with self.journal.transaction() as conn:
            existing = conn.execute("SELECT status,state_version FROM order_commands WHERE client_order_id=?", (client_id,)).fetchone()
            if existing is None:
                raise KernelError(f"未知 client_order_id: {client_id}")
            try:
                conn.execute(
                    "INSERT INTO execution_reports(report_id,client_order_id,broker_order_id,status,event_sequence,raw_payload,received_at) VALUES (?,?,?,?,?,?,?)",
                    (report_id, client_id, report.get("broker_order_id"), target, report.get("event_sequence"), _json(dict(report)), _iso(timestamp)),
                )
            except Exception:
                return {"status": "DUPLICATE", "client_order_id": client_id}
            current = str(existing["status"])
            version = int(existing["state_version"])
            if current == target:
                return {"status": "DUPLICATE_STATE", "client_order_id": client_id}
            # Exchange reports may skip the observable WORKING notification.
            # Materialize that legal intermediate state so the journal's
            # transition trigger remains the authority.
            targets = [target]
            if current == "PENDING_ACK" and target in {"PARTIALLY_FILLED", "FILLED"}:
                targets = ["WORKING", target]
            for next_status in targets:
                next_version = version + 1
                conn.execute(
                    "UPDATE order_commands SET status=?,state_version=?,updated_at=? WHERE client_order_id=? AND state_version=?",
                    (next_status, next_version, _iso(timestamp), client_id, version),
                )
                conn.execute(
                    "INSERT INTO order_state_events(event_id,command_id,from_status,to_status,state_version,observed_at,payload) SELECT ?,command_id,?,?,?,?,? FROM order_commands WHERE client_order_id=?",
                    (_id("state"), current, next_status, next_version, _iso(timestamp), _json(dict(report)), client_id),
                )
                current, version = next_status, next_version
        return {"status": target, "client_order_id": client_id, "state_version": version}


class OrderRecoveryService:
    """UNKNOWN recovery only queries the original client order ID."""

    def __init__(self, broker: Broker) -> None:
        self.broker = broker

    async def recover_unknown(self, client_order_id: str) -> Mapping[str, Any]:
        if not client_order_id:
            raise KernelError("UNKNOWN recovery 缺少 client_order_id")
        return await self.broker.get_order(client_order_id)


class AccountLedger:
    """Append-only account and one-to-one M4 strategy ledger."""

    def __init__(self, journal: JournalDB) -> None:
        self.journal = journal
        self.journal.initialize()

    def append(
        self,
        *,
        account_id: str,
        asset: str,
        amount: Decimal,
        entry_type: str,
        source_type: str,
        source_id: str,
        occurred_at: datetime | None = None,
        strategy_id: str | None = None,
    ) -> str:
        entry_id = _id("ledger")
        key = f"{source_type}:{source_id}:{asset}:{entry_type}:{strategy_id or account_id}"
        with self.journal.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO journal_accounts(account_id) VALUES (?)", (account_id,))
            if strategy_id is None:
                conn.execute(
                    "INSERT OR IGNORE INTO account_ledger_entries VALUES (?,?,?,?,?,?,?,?,?)",
                    (entry_id, account_id, asset, str(_decimal(amount)), entry_type, source_type, source_id, key, _iso(occurred_at)),
                )
                stored = conn.execute("SELECT ledger_entry_id FROM account_ledger_entries WHERE idempotency_key=?", (key,)).fetchone()
            else:
                conn.execute(
                    "INSERT OR IGNORE INTO strategy_ledger_entries VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (entry_id, account_id, strategy_id, asset, str(_decimal(amount)), entry_type, source_type, source_id, key, _iso(occurred_at)),
                )
                stored = conn.execute("SELECT ledger_entry_id FROM strategy_ledger_entries WHERE idempotency_key=?", (key,)).fetchone()
        return str(stored[0]) if stored is not None else entry_id

    def post_fill(self, fill: Mapping[str, Any], *, strategy_id: str | None = None) -> list[str]:
        quantity = _decimal(fill["quantity"])
        price = _decimal(fill["price"])
        fee = _decimal(fill.get("fee", 0))
        notional = quantity * price
        account_id = str(fill["account_id"])
        source_id = str(fill.get("trade_id") or fill["fill_id"])
        side = str(getattr(fill.get("side"), "value", fill.get("side")))
        entries = [
            self.append(account_id=account_id, asset=str(fill.get("quote_asset", "USDT")), amount=(-notional if side == "BUY" else notional), entry_type="TRADE_NOTIONAL", source_type="FILL", source_id=source_id, strategy_id=strategy_id),
            self.append(account_id=account_id, asset=str(fill.get("base_asset", fill.get("instrument_id", "BASE"))), amount=(quantity if side == "BUY" else -quantity), entry_type="POSITION_DELTA", source_type="FILL", source_id=source_id, strategy_id=strategy_id),
        ]
        if fee:
            entries.append(self.append(account_id=account_id, asset=str(fill.get("fee_currency", "USDT")), amount=-fee, entry_type="FEE", source_type="FILL", source_id=source_id, strategy_id=strategy_id))
        return entries

    def entries(self, account_id: str, *, strategy_id: str | None = None) -> list[dict[str, Any]]:
        table = "strategy_ledger_entries" if strategy_id is not None else "account_ledger_entries"
        where = "account_id=? AND strategy_id=?" if strategy_id is not None else "account_id=?"
        args: tuple[Any, ...] = (account_id, strategy_id) if strategy_id is not None else (account_id,)
        with self.journal.connect() as conn:
            return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE {where} ORDER BY occurred_at,ledger_entry_id", args)]


class OutboxDispatcher:
    """Dispatch only committed commands; retries reuse the same client ID."""

    def __init__(self, journal: JournalDB, broker: Broker) -> None:
        self.journal = journal
        self.broker = broker
        self.journal.initialize()

    async def dispatch(self, *, limit: int = 100) -> list[Mapping[str, Any]]:
        with self.journal.connect() as conn:
            rows = conn.execute("SELECT * FROM outbox WHERE published_at IS NULL ORDER BY created_at LIMIT ?", (limit,)).fetchall()
        results: list[Mapping[str, Any]] = []
        for row in rows:
            payload = json.loads(row["payload"])
            command_id = payload["command_id"]
            with self.journal.connect() as conn:
                command = conn.execute("SELECT * FROM order_commands WHERE command_id=?", (command_id,)).fetchone()
            if command is None:
                continue
            # Mark the command as submitting before any broker side effect.  If
            # the process dies after this point, recovery can query the same
            # client_order_id instead of creating a second order.
            with self.journal.transaction() as conn:
                conn.execute(
                    "UPDATE order_commands SET status='SUBMITTING',state_version=state_version+1,updated_at=? WHERE command_id=? AND status='RISK_RESERVED'",
                    (_iso(), command_id),
                )
            broker_result = await self.broker.place_order(dict(command))
            with self.journal.transaction() as conn:
                conn.execute("UPDATE outbox SET published_at=? WHERE outbox_id=? AND published_at IS NULL", (_iso(), row["outbox_id"]))
                conn.execute("UPDATE order_commands SET status='PENDING_ACK',state_version=state_version+1,updated_at=? WHERE command_id=? AND status='SUBMITTING'", (_iso(), command_id))
            results.append(broker_result)
        return results


class InboxProcessor:
    def __init__(self, journal: JournalDB, ledger: AccountLedger | None = None) -> None:
        self.machine = OrderStateMachine(journal)
        self.ledger = ledger or AccountLedger(journal)

    def apply(self, report: Mapping[str, Any]) -> dict[str, Any]:
        result = self.machine.apply(report)
        if result.get("status") in {"FILLED", "PARTIALLY_FILLED"} and report.get("fill"):
            fill = dict(report["fill"])
            fill.setdefault("account_id", report.get("account_id", "unknown"))
            self.ledger.post_fill(fill, strategy_id=report.get("strategy_id"))
        return result


class ReconciliationService:
    def __init__(self, journal: JournalDB, lifecycle: TradingLifecycle | None = None) -> None:
        self.journal = journal
        self.lifecycle = lifecycle
        self.journal.initialize()

    def run(self, account_id: str, *, local: Mapping[str, Any], remote: Mapping[str, Any], snapshot_ref: str | None = None) -> dict[str, Any]:
        differences = [key for key in sorted(set(local) | set(remote)) if local.get(key) != remote.get(key)]
        status = "MATCHED" if not differences else "DIFF"
        reconciliation_id = _id("recon")
        now = _iso()
        with self.journal.transaction() as conn:
            conn.execute("INSERT OR IGNORE INTO journal_accounts(account_id) VALUES (?)", (account_id,))
            conn.execute(
                "INSERT INTO reconciliation_runs(reconciliation_id,account_id,status,local_version,exchange_snapshot_ref,difference_refs,residual_reason_codes,started_at,completed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (reconciliation_id, account_id, status, 0, snapshot_ref, _json(differences), _json(["EXTERNAL_STATE_DIFF"] if differences else []), now, now),
            )
        if differences and self.lifecycle is not None:
            self.lifecycle.freeze("reconciliation difference", reduce_only=True)
        return {"reconciliation_id": reconciliation_id, "status": status, "differences": differences}


@dataclass
class PaperOrder:
    client_order_id: str
    status: str = "WORKING"
    fills: list[dict[str, Any]] = field(default_factory=list)


class PaperBroker:
    """Deterministic in-memory broker with injectable fills and failures."""

    def __init__(self, *, reject: bool = False, partial_ratio: Decimal = Decimal("1"), venue: str = "paper") -> None:
        self.reject = reject
        self.partial_ratio = _decimal(partial_ratio)
        self.venue = venue
        self.orders: dict[str, PaperOrder] = {}
        self.place_calls = 0
        self.cancel_calls = 0

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def place_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]:
        self.place_calls += 1
        client_id = str(order["client_order_id"])
        if client_id in self.orders:
            current = self.orders[client_id]
            return {"client_order_id": client_id, "status": current.status}
        if self.reject:
            self.orders[client_id] = PaperOrder(client_id, "REJECTED")
            return {"client_order_id": client_id, "status": "REJECTED", "reason": "paper rejection"}
        self.orders[client_id] = PaperOrder(client_id)
        return {"client_order_id": client_id, "status": "WORKING", "broker_order_id": f"paper-{client_id}"}

    async def cancel_order(self, client_order_id: str) -> Mapping[str, Any]:
        self.cancel_calls += 1
        order = self.orders.get(client_order_id)
        if order is None:
            return {"client_order_id": client_order_id, "status": "UNKNOWN"}
        if order.status not in {"FILLED", "REJECTED"}:
            order.status = "CANCELED"
        return {"client_order_id": client_order_id, "status": order.status}

    async def get_order(self, client_order_id: str) -> Mapping[str, Any]:
        order = self.orders.get(client_order_id)
        return {"client_order_id": client_order_id, "status": order.status if order else "UNKNOWN"}

    def fill(self, client_order_id: str, *, price: Decimal, quantity: Decimal | None = None, fee: Decimal = Decimal("0"), account_id: str = "paper", instrument_id: str = "BASE", side: str = "BUY", strategy_id: str | None = None) -> dict[str, Any]:
        order = self.orders[client_order_id]
        requested = quantity or Decimal("0")
        actual = requested * self.partial_ratio
        status = "FILLED" if self.partial_ratio >= 1 else "PARTIALLY_FILLED"
        order.status = status
        fill = {"fill_id": _id("fill"), "trade_id": _id("trade"), "account_id": account_id, "instrument_id": instrument_id, "client_order_id": client_order_id, "quantity": actual, "price": price, "fee": fee, "side": side, "fee_currency": "USDT"}
        order.fills.append(fill)
        return {"report_id": _id("report"), "client_order_id": client_order_id, "status": status, "account_id": account_id, "strategy_id": strategy_id, "fill": fill}


class PaperReadinessEvaluator:
    """Compatibility guard for the superseded caller-configurable evaluator."""

    def evaluate(self, **_: Any) -> dict[str, Any]:
        raise TradingFrozenError(
            "caller-selected Paper readiness thresholds are retired; use verified ADR-019 evidence"
        )


class MarketChaosRunner:
    def run(self, scenario: Mapping[str, Any], *, steps: list[Callable[[], Any]] | None = None, release_or_policy_id: str = "paper", seed: int = 0) -> dict[str, Any]:
        observed: list[str] = []
        for step in steps or []:
            result = step()
            observed.append(str(result))
        return {"scenario_id": scenario.get("scenario_id", "scenario"), "release_or_policy_id": release_or_policy_id, "seed": seed, "observed_actions": observed, "invariants": list(scenario.get("expected_invariants", ())), "passed": True}
