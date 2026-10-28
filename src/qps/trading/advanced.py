"""M5--M7 control-plane primitives.

These classes are intentionally conservative.  They provide a complete local
flow for recorded/sandbox adapters, portfolio Paper operation and offline AI
research without granting any component an implicit live or broker mutation
capability.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import TaskStatus
from qps.core.models.artifact import Artifact
from qps.core.models.task import TaskRun

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class LiveSafetyError(RuntimeError):
    """Live action is outside the explicitly approved capability envelope."""


class DeploymentCapabilityService:
    def configure(self, requested: Mapping[str, Any], supported: Mapping[str, Any]) -> dict[str, Any]:
        unknown = set(requested) - set(supported)
        if unknown:
            raise LiveSafetyError(f"unsupported broker capabilities: {sorted(unknown)}")
        for key, value in requested.items():
            if value and not supported.get(key):
                raise LiveSafetyError(f"broker capability unavailable: {key}")
        return dict(requested)


class LiveDeploymentPreflight:
    """Credential and secret checks; secrets never enter return values/logs."""

    def check_credentials(self, credentials: Mapping[str, str]) -> dict[str, Any]:
        if not credentials.get("api_key") or not credentials.get("api_secret"):
            raise LiveSafetyError("缺少交易所 API 凭据")
        permissions = {item.strip().lower() for item in credentials.get("permissions", "").split(",") if item.strip()}
        if "withdraw" in permissions or "withdrawal" in permissions:
            raise LiveSafetyError("Live key 禁止 withdrawal 权限")
        return {"configured": True, "permissions": sorted(permissions), "key_fingerprint": _hash(credentials["api_key"])[:12]}


class LiveApprovalGate:
    def __init__(self, *, hard_cap: Decimal, audit_path: str | None = None) -> None:
        self.hard_cap = Decimal(hard_cap)
        self.approvals: list[dict[str, Any]] = []
        self.audit_path = audit_path

    def authorize(self, *, deployment_id: str, notional: Decimal, approved_by: str, reason: str, expires_at: datetime | None = None) -> dict[str, Any]:
        if not approved_by or not reason:
            raise LiveSafetyError("Live 操作必须人工审批并填写理由")
        if Decimal(notional) > self.hard_cap:
            raise LiveSafetyError("订单超过硬资本上限")
        approval = {"approval_id": _id("approval"), "deployment_id": deployment_id, "notional": str(notional), "approved_by": approved_by, "reason": reason, "expires_at": (expires_at or (_now() + timedelta(hours=1))).isoformat()}
        self.approvals.append(approval)
        if self.audit_path is not None:
            with open(self.audit_path, "a", encoding="utf-8") as stream:
                stream.write(json.dumps(approval, sort_keys=True) + "\n")
        return dict(approval)


class LiveDeploymentFactory:
    def __init__(self) -> None:
        self._bindings: dict[str, dict[str, str]] = {}

    def create(self, *, deployment_id: str, release_id: str, account_id: str, data_dir: str, mode: str = "live") -> dict[str, Any]:
        if mode.lower() not in {"live", "paper"}:
            raise LiveSafetyError("未知 deployment mode")
        normalized_mode = mode.upper()
        path = os.path.realpath(os.path.abspath(data_dir))
        for existing_id, binding in self._bindings.items():
            if existing_id == deployment_id:
                if binding != {"account_id": account_id, "data_dir": path, "mode": normalized_mode}:
                    raise LiveSafetyError("deployment binding 不可覆盖")
            elif binding["data_dir"] == path or binding["account_id"] == account_id:
                raise LiveSafetyError("Paper/Live deployment 不能共享账户或 journal 目录")
        os.makedirs(path, exist_ok=True)
        binding = {"account_id": account_id, "data_dir": path, "mode": normalized_mode}
        self._bindings[deployment_id] = dict(binding)
        return {"deployment_id": deployment_id, "release_id": release_id, **binding, "created_at": _now().isoformat(), "immutable": True}


class LiveWatchdog:
    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []
        self.last_heartbeat: datetime | None = None

    def heartbeat(self, *, component: str, at: datetime | None = None) -> None:
        self.last_heartbeat = at or _now()
        self.alerts.append({"type": "HEARTBEAT", "component": component, "at": self.last_heartbeat.isoformat()})

    def monitor(self, *, now: datetime | None = None, timeout: timedelta = timedelta(seconds=30)) -> dict[str, Any]:
        current = now or _now()
        healthy = self.last_heartbeat is not None and current - self.last_heartbeat <= timeout
        if not healthy:
            self.alerts.append({"type": "WATCHDOG_TIMEOUT", "at": current.isoformat(), "action": "FREEZE"})
        return {"healthy": healthy, "action": "NONE" if healthy else "FREEZE", "alerts": list(self.alerts)}


class NativeProtection:
    def __init__(self) -> None:
        self.enabled = False
        self.reason: str | None = None

    def arm(self, *, approved_by: str, reason: str) -> None:
        if not approved_by or not reason:
            raise LiveSafetyError("原生保护必须人工批准")
        self.enabled, self.reason = True, reason


class LiveRecoveryService:
    def restore(self, *, backup_dir: str, live_dir: str, reconcile: Callable[[], Mapping[str, Any]]) -> dict[str, Any]:
        if os.path.abspath(backup_dir) == os.path.abspath(live_dir):
            raise LiveSafetyError("恢复目录不能覆盖当前 Live journal")
        os.makedirs(live_dir, exist_ok=True)
        result = dict(reconcile())
        return {"status": "FROZEN", "reconciled": result, "new_risk_allowed": False}


class LiveOrderRecovery:
    async def recover(self, broker: Any, client_order_id: str) -> Mapping[str, Any]:
        return dict(await broker.get_order(client_order_id))


class StrategyLedger:
    """Append-only virtual strategy ledger."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self._db_path = os.fspath(db_path) if db_path is not None else None
        self._entries: list[dict[str, Any]] = []
        self._keys: set[str] = set()
        self._lock = threading.Lock()
        if self._db_path is not None:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS strategy_virtual_ledger (
                        ledger_entry_id TEXT PRIMARY KEY,
                        strategy_id TEXT,
                        idempotency_key TEXT NOT NULL UNIQUE,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS strategy_virtual_ledger_no_update
                    BEFORE UPDATE ON strategy_virtual_ledger BEGIN SELECT RAISE(ABORT, 'strategy ledger append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS strategy_virtual_ledger_no_delete
                    BEFORE DELETE ON strategy_virtual_ledger BEGIN SELECT RAISE(ABORT, 'strategy ledger append-only'); END;
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise LiveSafetyError("strategy ledger 未配置持久化路径")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def append(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        key = str(entry.get("idempotency_key") or _hash(entry))
        with self._lock:
            if self._db_path is not None:
                item = dict(entry)
                item["ledger_entry_id"] = item.get("ledger_entry_id", _id("sledger"))
                item["idempotency_key"] = key
                item["created_at"] = item.get("created_at", _now().isoformat())
                payload = json.dumps(item, sort_keys=True, default=str)
                with self._connect() as conn:
                    row = conn.execute("SELECT payload FROM strategy_virtual_ledger WHERE idempotency_key=?", (key,)).fetchone()
                    if row is not None:
                        return dict(json.loads(row["payload"]))
                    conn.execute(
                        "INSERT INTO strategy_virtual_ledger VALUES (?,?,?,?,?)",
                        (item["ledger_entry_id"], str(item.get("strategy_id")) if item.get("strategy_id") is not None else None, key, payload, str(item["created_at"])),
                    )
                    conn.commit()
                return dict(item)
            if key in self._keys:
                return next(item for item in self._entries if item["idempotency_key"] == key)
            item = dict(entry)
            item["ledger_entry_id"] = item.get("ledger_entry_id", _id("sledger"))
            item["idempotency_key"] = key
            item["created_at"] = item.get("created_at", _now().isoformat())
            self._keys.add(key)
            self._entries.append(item)
            return dict(item)

    def entries(self, strategy_id: str | None = None) -> list[dict[str, Any]]:
        if self._db_path is not None:
            with self._connect() as conn:
                if strategy_id is None:
                    rows = conn.execute("SELECT payload FROM strategy_virtual_ledger ORDER BY created_at,ledger_entry_id").fetchall()
                else:
                    rows = conn.execute("SELECT payload FROM strategy_virtual_ledger WHERE strategy_id=? ORDER BY created_at,ledger_entry_id", (strategy_id,)).fetchall()
            return [dict(json.loads(row["payload"])) for row in rows]
        return [dict(item) for item in self._entries if strategy_id is None or item.get("strategy_id") == strategy_id]


class AllocationPlanner:
    """Deterministic target netting; plans must be persisted before orders."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self._db_path = os.fspath(db_path) if db_path is not None else None
        self.plans: list[dict[str, Any]] = []
        if self._db_path is not None:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS allocation_plan_records (
                        allocation_plan_id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS allocation_plan_records_no_update
                    BEFORE UPDATE ON allocation_plan_records BEGIN SELECT RAISE(ABORT, 'allocation plan append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS allocation_plan_records_no_delete
                    BEFORE DELETE ON allocation_plan_records BEGIN SELECT RAISE(ABORT, 'allocation plan append-only'); END;
                    """
                )
                rows = conn.execute("SELECT payload FROM allocation_plan_records ORDER BY created_at,allocation_plan_id").fetchall()
                for row in rows:
                    item = dict(json.loads(row["payload"]))
                    item["input_intents"] = tuple(item.get("input_intents", ()))
                    self.plans.append(item)

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise LiveSafetyError("allocation planner 未配置持久化路径")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def plan(self, intents: Iterable[Mapping[str, Any]], *, account_id: str, policy_version: str = "m6-v1") -> dict[str, Any]:
        grouped: dict[str, Decimal] = {}
        ids: list[str] = []
        for intent in intents:
            instrument = str(intent["instrument_id"])
            qty = Decimal(str(intent.get("quantity", 0)))
            side = str(intent.get("side", intent.get("direction", "BUY")))
            grouped[instrument] = grouped.get(instrument, Decimal("0")) + (qty if side in {"BUY", "LONG"} else -qty)
            ids.append(str(intent["intent_id"]))
        plan = {"allocation_plan_id": _id("alloc"), "account_id": account_id, "input_intents": tuple(ids), "net_targets": {key: str(value) for key, value in sorted(grouped.items())}, "allocation_rule_version": policy_version, "created_at": _now().isoformat(), "persisted": False}
        self.plans.append(plan)
        return plan

    def persist(self, plan: Mapping[str, Any]) -> dict[str, Any]:
        known = {item["allocation_plan_id"] for item in self.plans}
        if plan.get("allocation_plan_id") not in known:
            raise LiveSafetyError("只能持久化由 planner 生成的 plan")
        persisted = dict(plan)
        persisted["persisted"] = True
        if self._db_path is not None:
            payload = json.dumps(persisted, sort_keys=True, default=str)
            with self._connect() as conn:
                existing = conn.execute("SELECT payload FROM allocation_plan_records WHERE allocation_plan_id=?", (plan["allocation_plan_id"],)).fetchone()
                if existing is not None:
                    if existing["payload"] != payload:
                        raise LiveSafetyError("allocation plan 不可覆盖")
                else:
                    conn.execute(
                        "INSERT INTO allocation_plan_records VALUES (?,?,?)",
                        (plan["allocation_plan_id"], payload, str(plan["created_at"])),
                    )
                    conn.commit()
        for index, item in enumerate(self.plans):
            if item["allocation_plan_id"] == plan["allocation_plan_id"]:
                self.plans[index] = persisted
        return persisted

    def construct(self, intents: Iterable[Mapping[str, Any]], *, account_id: str) -> dict[str, Any]:
        return self.persist(self.plan(intents, account_id=account_id))


class FillAllocator:
    def allocate(self, *, fill: Mapping[str, Any], plan: Mapping[str, Any]) -> list[dict[str, Any]]:
        if plan.get("persisted") is not True:
            raise LiveSafetyError("Fill 分摊只能使用已持久化 AllocationPlan")
        allocations = plan.get("allocations")
        if not allocations:
            allocations = [{"strategy_id": plan.get("strategy_id", "single"), "ratio": "1"}]
        total = sum((Decimal(str(item["ratio"])) for item in allocations), Decimal("0"))
        if total != 1:
            raise LiveSafetyError("Fill 分摊 ratio 必须预先守恒为 1")
        quantity = Decimal(str(fill["quantity"]))
        return [{**dict(fill), "strategy_id": item["strategy_id"], "quantity": quantity * Decimal(str(item["ratio"]))} for item in allocations]


class AllocationReconciler:
    def reconcile(self, *, account_quantity: Decimal, allocated_quantities: Iterable[Decimal], tolerance: Decimal = Decimal("0.00000001")) -> dict[str, Any]:
        allocated = sum((Decimal(value) for value in allocated_quantities), Decimal("0"))
        residual = Decimal(account_quantity) - allocated
        return {"matched": abs(residual) <= tolerance, "account_quantity": Decimal(account_quantity), "allocated_quantity": allocated, "residual": residual}


class PortfolioRiskModel:
    def evaluate(self, *, positions: Mapping[str, Decimal], limits: Mapping[str, Decimal] | None = None, model_version: str | None = "m6-v1", expires_at: datetime | None = None, now: datetime | None = None) -> dict[str, Any]:
        if not model_version or (expires_at is not None and (now or _now()) >= expires_at):
            return {"status": "UNKNOWN", "action": "RISK_REDUCE_ONLY", "reason": "model missing or expired"}
        limits = limits or {}
        breaches = {key: str(value) for key, value in positions.items() if key in limits and abs(value) > limits[key]}
        return {"status": "BREACH" if breaches else "OK", "action": "RISK_REDUCE_ONLY" if breaches else "NONE", "breaches": breaches, "model_version": model_version}


class PortfolioRiskEngine:
    def __init__(self, model: PortfolioRiskModel | None = None) -> None:
        self.model = model or PortfolioRiskModel()

    def evaluate(self, **kwargs: Any) -> dict[str, Any]:
        return self.model.evaluate(**kwargs)


class PortfolioRuntimeSupervisor:
    def __init__(self) -> None:
        self.frozen_strategies: set[str] = set()

    def poll(self, strategy_id: str, healthy: bool) -> dict[str, Any]:
        if not healthy:
            self.frozen_strategies.add(strategy_id)
        return {"strategy_id": strategy_id, "frozen": strategy_id in self.frozen_strategies, "other_strategies_affected": False}


class PortfolioRuntimeScheduler:
    def schedule(self, strategy_ids: Iterable[str]) -> list[str]:
        return sorted(set(strategy_ids))


class AgentTaskRunner:
    def __init__(self) -> None:
        self.tasks: dict[str, TaskRun] = {}
        self.artifacts: dict[str, Artifact] = {}

    def run(self, task_type: str, params: Mapping[str, Any], handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> TaskRun:
        task_id = _id("task")
        started = _now()
        try:
            output = dict(handler(params) if handler else {"status": "draft", "params": dict(params)})
            artifact = Artifact(artifact_id=_id("artifact"), artifact_type=task_type, content=output, agent_role="local", created_at=_now())
            self.artifacts[artifact.artifact_id] = artifact
            task = TaskRun(task_id=task_id, task_type=task_type, params=dict(params), status=TaskStatus.SUCCEEDED, started_at=started, finished_at=_now())
        except Exception as exc:
            task = TaskRun(task_id=task_id, task_type=task_type, params=dict(params), status=TaskStatus.FAILED, started_at=started, finished_at=_now(), error=str(exc))
        self.tasks[task_id] = task
        return task

    def persist_output(self, artifact: Artifact) -> str:
        if artifact.artifact_id in self.artifacts and self.artifacts[artifact.artifact_id] != artifact:
            raise LiveSafetyError("Artifact 不可覆盖")
        self.artifacts[artifact.artifact_id] = artifact
        return artifact.artifact_id


class KnowledgeStore:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []

    def append(self, *, content: str, source_available_at: datetime, record_id: str | None = None, blind: bool = False, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = {"record_id": record_id or _id("knowledge"), "content": content, "source_available_at": source_available_at, "blind": blind, "metadata": dict(metadata or {}), "created_at": _now()}
        self.records.append(record)
        return dict(record)

    def query(self, text: str, *, as_of: datetime, include_blind: bool = False) -> list[dict[str, Any]]:
        return [dict(item) for item in self.records if (include_blind or not item["blind"]) and item["source_available_at"] <= as_of and text.lower() in item["content"].lower()]

    def supersede(self, record_id: str, *, content: str, available_at: datetime) -> dict[str, Any]:
        return self.append(content=content, source_available_at=available_at, metadata={"supersedes": record_id})

    def retract(self, record_id: str, *, available_at: datetime) -> dict[str, Any]:
        return self.append(content="", source_available_at=available_at, metadata={"retracts": record_id})


class AgentKnowledgeGateway:
    def __init__(self, store: KnowledgeStore) -> None:
        self.store = store

    def retrieve(self, query: str, *, as_of: datetime) -> list[dict[str, Any]]:
        return self.store.query(query, as_of=as_of, include_blind=False)


class AgentGateway(AgentKnowledgeGateway):
    """Allowlisted local tool gateway with no trading mutation surface."""

    def __init__(self, store: KnowledgeStore, *, handlers: Mapping[str, Callable[..., Any]] | None = None, allowed_domains: Iterable[str] = ()) -> None:
        super().__init__(store)
        self.handlers = dict(handlers or {})
        self.allowed_domains = set(allowed_domains)

    def call(self, tool: str, **kwargs: Any) -> Any:
        if tool not in self.handlers:
            raise LiveSafetyError("tool 不在 Agent allowlist")
        return self.handlers[tool](**kwargs)

    def execute_tools(self, requests: Iterable[Mapping[str, Any]]) -> list[Any]:
        return [self.call(str(request["tool"]), **dict(request.get("arguments", {}))) for request in requests]


class AgentResearchService:
    def __init__(self, *, budget: Any | None = None) -> None:
        self.budget = budget
        self.proposals: list[dict[str, Any]] = []

    def propose(self, *, hypothesis: str, candidate: Mapping[str, Any], actor: str = "AI") -> dict[str, Any]:
        proposal = {"proposal_id": _id("proposal"), "hypothesis": hypothesis, "candidate": dict(candidate), "actor": actor, "status": "DRAFT", "candidate_fingerprint": _hash(candidate), "created_at": _now().isoformat()}
        self.proposals.append(proposal)
        return proposal


class AgentContainer:
    """Capability object exposing research-only operations."""

    def __init__(self, *, knowledge: AgentKnowledgeGateway, research: AgentResearchService) -> None:
        self.knowledge = knowledge
        self.research = research

    def allowed_capabilities(self) -> tuple[str, ...]:
        return ("knowledge.read", "research.propose", "artifact.write")

    def build(self) -> dict[str, Any]:
        return {"capabilities": self.allowed_capabilities(), "trading_mutation": False}


class AgentSubsystem:
    def __init__(self) -> None:
        self.enabled = True

    def stop(self, reason: str = "manual disable") -> dict[str, Any]:
        self.enabled = False
        return {"enabled": False, "reason": reason, "live_impact": False}

    def start(self) -> dict[str, Any]:
        self.enabled = True
        return {"enabled": True}


class AgentEvaluationService:
    def evaluate(self, *, outputs: Iterable[Mapping[str, Any]], benchmark: Mapping[str, Any] | None = None) -> dict[str, Any]:
        items = list(outputs)
        return {"evaluation_id": _id("agent-eval"), "count": len(items), "benchmark": dict(benchmark or {}), "status": "DRAFT", "permission_violations": sum(1 for item in items if item.get("permission_violation")), "cost": sum((Decimal(str(item.get("cost", 0))) for item in items), Decimal("0"))}
