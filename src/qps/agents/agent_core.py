"""Offline Agent implementation with no trading/infrastructure imports."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import ResearchActorType, TaskStatus
from qps.core.models.artifact import Artifact
from qps.core.models.task import TaskRun

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from qps.core.models.foundation import BudgetConsumption, CandidateDescriptor, TrialRegistration


class AgentPermissionError(RuntimeError):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class AgentTaskRunner:
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self._db_path = os.fspath(db_path) if db_path is not None else None
        self._tasks: dict[str, TaskRun] = {}
        self._artifacts: dict[str, Artifact] = {}
        if self._db_path is not None:
            with self._connect() as conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS agent_tasks (
                        task_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS agent_artifacts (
                        artifact_id TEXT PRIMARY KEY, payload TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS agent_tasks_no_update
                    BEFORE UPDATE ON agent_tasks BEGIN SELECT RAISE(ABORT, 'agent task append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS agent_tasks_no_delete
                    BEFORE DELETE ON agent_tasks BEGIN SELECT RAISE(ABORT, 'agent task append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS agent_artifacts_no_update
                    BEFORE UPDATE ON agent_artifacts BEGIN SELECT RAISE(ABORT, 'agent artifact append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS agent_artifacts_no_delete
                    BEFORE DELETE ON agent_artifacts BEGIN SELECT RAISE(ABORT, 'agent artifact append-only'); END;
                    """
                )

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise AgentPermissionError("task runner 未配置持久化路径")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def tasks(self) -> dict[str, TaskRun]:
        if self._db_path is None:
            return dict(self._tasks)
        with self._connect() as conn:
            rows = conn.execute("SELECT task_id,payload FROM agent_tasks ORDER BY task_id").fetchall()
        return {row["task_id"]: TaskRun.model_validate(json.loads(row["payload"])) for row in rows}

    @property
    def artifacts(self) -> dict[str, Artifact]:
        if self._db_path is None:
            return dict(self._artifacts)
        with self._connect() as conn:
            rows = conn.execute("SELECT artifact_id,payload FROM agent_artifacts ORDER BY artifact_id").fetchall()
        return {row["artifact_id"]: Artifact.model_validate(json.loads(row["payload"])) for row in rows}

    def run(self, task_type: str, params: Mapping[str, Any], handler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None) -> TaskRun:
        task_id = _id("task")
        started = _now()
        try:
            content = dict(handler(params) if handler else {"status": "draft", "params": dict(params)})
            content.setdefault(
                "lineage",
                {
                    "input_hash": _hash(params),
                    "output_hash": _hash(content),
                    "record_ids": sorted(str(item) for item in content.get("record_ids", [])),
                    "citations": list(content.get("citations", [])),
                    "model_version": params.get("model_version"),
                    "prompt_hash": params.get("prompt_hash"),
                    "tool_versions": dict(params.get("tool_versions", {})),
                },
            )
            artifact = Artifact(artifact_id=_id("artifact"), artifact_type=task_type, content=content, agent_role="local", created_at=_now())
            task = TaskRun(task_id=task_id, task_type=task_type, params=dict(params), status=TaskStatus.SUCCEEDED, started_at=started, finished_at=_now())
        except Exception as exc:
            task = TaskRun(task_id=task_id, task_type=task_type, params=dict(params), status=TaskStatus.FAILED, started_at=started, finished_at=_now(), error=str(exc))
        if self._db_path is None:
            if task.status == TaskStatus.SUCCEEDED:
                self._artifacts[artifact.artifact_id] = artifact
            self._tasks[task_id] = task
        else:
            with self._connect() as conn:
                if task.status == TaskStatus.SUCCEEDED:
                    conn.execute("INSERT INTO agent_artifacts VALUES (?,?)", (artifact.artifact_id, artifact.model_dump_json()))
                conn.execute("INSERT INTO agent_tasks VALUES (?,?)", (task.task_id, task.model_dump_json()))
                conn.commit()
        return task

    def persist_output(self, artifact: Artifact) -> str:
        existing = self.artifacts.get(artifact.artifact_id)
        if existing is not None:
            if existing != artifact:
                raise AgentPermissionError("Artifact 不可覆盖")
            return artifact.artifact_id
        if self._db_path is None:
            self._artifacts[artifact.artifact_id] = artifact
        else:
            with self._connect() as conn:
                conn.execute("INSERT INTO agent_artifacts VALUES (?,?)", (artifact.artifact_id, artifact.model_dump_json()))
                conn.commit()
        return artifact.artifact_id


class KnowledgeStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path) if db_path is not None else None
        self._records: list[dict[str, Any]] = []
        if self._db_path is not None:
            with self._connect() as conn:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS agent_knowledge (
                        record_id TEXT PRIMARY KEY, content TEXT NOT NULL,
                        source_available_at TEXT NOT NULL, blind INTEGER NOT NULL,
                        metadata TEXT NOT NULL, created_at TEXT NOT NULL
                    );
                    CREATE TRIGGER IF NOT EXISTS agent_knowledge_no_update
                    BEFORE UPDATE ON agent_knowledge BEGIN SELECT RAISE(ABORT, 'agent knowledge append-only'); END;
                    CREATE TRIGGER IF NOT EXISTS agent_knowledge_no_delete
                    BEFORE DELETE ON agent_knowledge BEGIN SELECT RAISE(ABORT, 'agent knowledge append-only'); END;
                """)

    def _connect(self) -> sqlite3.Connection:
        if self._db_path is None:
            raise AgentPermissionError("knowledge store 未配置持久化路径")
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @property
    def records(self) -> list[dict[str, Any]]:
        if self._db_path is None:
            return list(self._records)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_knowledge ORDER BY created_at,record_id").fetchall()
        return [
            {"record_id": row["record_id"], "content": row["content"], "source_available_at": datetime.fromisoformat(row["source_available_at"]), "blind": bool(row["blind"]), "metadata": json.loads(row["metadata"]), "created_at": datetime.fromisoformat(row["created_at"])}
            for row in rows
        ]

    def append(self, *, content: str, source_available_at: datetime, record_id: str | None = None, blind: bool = False, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = {"record_id": record_id or _id("knowledge"), "content": content, "source_available_at": source_available_at, "blind": blind, "metadata": dict(metadata or {}), "created_at": _now()}
        if self._db_path is None:
            self._records.append(record)
        else:
            with self._connect() as conn:
                conn.execute("INSERT INTO agent_knowledge VALUES (?,?,?,?,?,?)", (record["record_id"], content, source_available_at.isoformat(), int(blind), json.dumps(record["metadata"], sort_keys=True), _now().isoformat()))
                conn.commit()
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
    _SENSITIVE_KEYS = frozenset({"api_key", "api_secret", "access_token", "authorization", "password", "private_key", "secret", "token"})

    def __init__(
        self,
        store: KnowledgeStore,
        *,
        handlers: Mapping[str, Callable[..., Any]] | None = None,
        allowed_domains: Iterable[str] = (),
    ) -> None:
        super().__init__(store)
        self.handlers = dict(handlers or {})
        self.allowed_domains = frozenset(allowed_domains)

    @classmethod
    def _redact(cls, value: Any, *, key: str = "") -> Any:
        if key.lower() in cls._SENSITIVE_KEYS:
            return "[REDACTED]"
        if isinstance(value, Mapping):
            return {str(item_key): cls._redact(item_value, key=str(item_key)) for item_key, item_value in value.items()}
        if isinstance(value, list):
            return [cls._redact(item) for item in value]
        if isinstance(value, tuple):
            return tuple(cls._redact(item) for item in value)
        return value

    def call(self, tool: str, **kwargs: Any) -> Any:
        if tool not in self.handlers:
            raise AgentPermissionError("tool 不在 Agent allowlist")
        domain = kwargs.get("domain")
        if tool.startswith("network.") and (not isinstance(domain, str) or domain not in self.allowed_domains):
            raise AgentPermissionError("network tool 的域名不在 allowlist")
        safe_kwargs = self._redact(kwargs)
        return self.handlers[tool](**safe_kwargs)

    def execute_tools(self, requests: Iterable[Mapping[str, Any]]) -> list[Any]:
        return [self.call(str(request["tool"]), **dict(request.get("arguments", {}))) for request in requests]


class AgentResearchService:
    def __init__(self, *, budget: Any | None = None) -> None:
        self.budget = budget
        self.proposals: list[dict[str, Any]] = []
        self._fingerprints: set[str] = set()

    async def propose_formal(
        self,
        *,
        registration: TrialRegistration,
        candidate: CandidateDescriptor,
        requested_compute: Decimal,
        blind_reveals: int,
        reserved_at: datetime,
    ) -> dict[str, Any]:
        """Reserve the existing M3 budget before creating an AI proposal."""
        if self.budget is None:
            raise AgentPermissionError("AI proposal 未绑定统一 ResearchBudgetPort")
        if candidate.candidate_fingerprint in self._fingerprints:
            raise AgentPermissionError("AI candidate 近重复，不能重置 trial 计数")
        consumptions = await self.budget.reserve_trial(
            registration=registration,
            candidate=candidate,
            actor_type=ResearchActorType.AI,
            requested_compute=requested_compute,
            blind_reveals=blind_reveals,
            reserved_at=reserved_at,
        )
        return self.propose(
            hypothesis=registration.hypothesis,
            candidate=candidate.model_dump(mode="json"),
            actor="AI",
            budget_consumptions=consumptions,
            candidate_fingerprint=candidate.candidate_fingerprint,
        )

    def propose(
        self,
        *,
        hypothesis: str,
        candidate: Mapping[str, Any],
        actor: str = "AI",
        budget_consumptions: Iterable[BudgetConsumption] | None = None,
        candidate_fingerprint: str | None = None,
    ) -> dict[str, Any]:
        if self.budget is None:
            raise AgentPermissionError("AI proposal 必须绑定统一 ResearchBudgetPort")
        fingerprint = candidate_fingerprint or _hash(candidate)
        receipts = tuple(budget_consumptions or ())
        if not receipts:
            raise AgentPermissionError("AI proposal 必须先取得统一 ResearchBudget reservation")
        if fingerprint in self._fingerprints:
            raise AgentPermissionError("AI candidate 近重复，不能重置 trial 计数")
        if receipts and any(item.actor_type != ResearchActorType.AI for item in receipts):
            raise AgentPermissionError("AI 不能使用其他 actor 的预算凭证")
        proposal = {
            "proposal_id": _id("proposal"),
            "hypothesis": hypothesis,
            "candidate": dict(candidate),
            "actor": actor,
            "status": "DRAFT",
            "candidate_fingerprint": fingerprint,
            "budget_consumptions": [item.model_dump(mode="json") for item in receipts],
            "created_at": _now().isoformat(),
        }
        self._fingerprints.add(fingerprint)
        self.proposals.append(proposal)
        return proposal


class AgentContainer:
    def __init__(self, *, knowledge: AgentKnowledgeGateway, research: AgentResearchService) -> None:
        self.knowledge = knowledge
        self.research = research

    def allowed_capabilities(self) -> tuple[str, ...]:
        return ("knowledge.read", "research.propose", "artifact.write")

    def build(self) -> dict[str, Any]:
        return {"capabilities": self.allowed_capabilities(), "trading_mutation": False}


class DurableAgentGateway:
    """M7 AgentGatewayPort adapter backed by append-only local storage."""

    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.runner = AgentTaskRunner(db_path)
        self.knowledge = KnowledgeStore(os.fspath(db_path))
        self._handlers: dict[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]] = {}

    async def submit_task(self, agent_role: str, task_spec: dict[str, Any]) -> TaskRun:
        handler = self._handlers.get(agent_role)
        if handler is None:
            raise AgentPermissionError(f"Agent role 未注册 handler: {agent_role}")
        return self.runner.run(agent_role, task_spec, handler=handler)

    async def read_artifact(self, artifact_id: str) -> Artifact:
        artifact = self.runner.artifacts.get(artifact_id)
        if artifact is None:
            raise AgentPermissionError(f"Artifact 不存在: {artifact_id}")
        return artifact

    async def write_artifact(self, artifact: Artifact) -> str:
        return self.runner.persist_output(artifact)

    async def register_handler(
        self,
        agent_role: str,
        handler: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    ) -> None:
        if not agent_role.strip():
            raise AgentPermissionError("Agent role 不能为空")
        self._handlers[agent_role] = handler


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
