"""Durable strategy lifecycle management.

The control plane owns lifecycle transitions.  A transition is committed to
MetaDB (strategy projection + audit record) before its event is published so
observers can never see a transition that is not durable.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from qps.core.constants import (
    StrategyLifecycleStatus,
    validate_lifecycle_transition,
)
from qps.core.events import EVENT_SOURCE_LIFECYCLE, EventEnvelope, EventType, make_event_id

if TYPE_CHECKING:
    from qps.infra.event_bus import EventBus
    from qps.infra.storage.meta_db import MetaDB


_EVIDENCE_TABLES: tuple[tuple[str, str], ...] = (
    ("experiments", "run_id"),
    ("evidence_reports", "report_id"),
    ("deployments", "deployment_id"),
    ("release_deployments", "deployment_id"),
    ("strategy_releases", "release_id"),
    ("approval_records", "approval_id"),
    ("strategy_health_assessments", "assessment_id"),
    ("chaos_runs", "chaos_run_id"),
)


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class LifecycleManager:
    """Implementation of :class:`LifecycleManagerPort` over canonical MetaDB."""

    def __init__(self, meta_db: MetaDB, event_bus: EventBus | None = None) -> None:
        self.meta_db = meta_db
        self.event_bus = event_bus
        self.meta_db.initialize()
        self.meta_db.setup_insert_only_constraints()

    def _evidence_exists(self, conn: Any, evidence_ref: str) -> bool:
        for table, column in _EVIDENCE_TABLES:
            if conn.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
                (evidence_ref,),
            ).fetchone() is not None:
                return True
        return False

    async def transition(
        self,
        strategy_id: str,
        target_status: str,
        evidence_refs: list[str],
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Atomically transition a strategy and publish its durable event."""
        try:
            target = StrategyLifecycleStatus(target_status.upper())
        except ValueError as exc:
            raise ValueError(f"无效的策略生命周期状态: {target_status}") from exc
        if not actor.strip():
            raise ValueError("actor 不能为空")
        if not reason.strip():
            raise ValueError("reason 不能为空")
        refs = tuple(dict.fromkeys(item.strip() for item in evidence_refs if item.strip()))
        if not refs:
            raise ValueError("生命周期迁移必须提供至少一个已持久化证据 ID")

        audit_id = _id("audit")
        event_id = make_event_id(uuid.uuid4().hex)
        changed_at = _now()
        with self.meta_db.transaction() as conn:
            row = conn.execute(
                "SELECT lifecycle_status FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"策略不存在: {strategy_id}")
            previous = StrategyLifecycleStatus(row[0])
            validate_lifecycle_transition(previous, target)
            missing = [ref for ref in refs if not self._evidence_exists(conn, ref)]
            if missing:
                raise ValueError(f"证据不存在或未持久化: {', '.join(missing)}")

            conn.execute(
                "UPDATE strategies SET lifecycle_status = ?, updated_at = ? WHERE strategy_id = ?",
                (target.value, changed_at.isoformat(), strategy_id),
            )
            before = json.dumps({"lifecycle_status": previous.value}, ensure_ascii=False, sort_keys=True)
            after = json.dumps({"lifecycle_status": target.value, "evidence_refs": list(refs)}, ensure_ascii=False, sort_keys=True)
            conn.execute(
                "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (audit_id, actor, "LIFECYCLE_TRANSITION", "strategy", strategy_id,
                 before, after, changed_at.isoformat(), reason),
            )

        payload = {
            "strategy_id": strategy_id,
            "from_status": previous.value,
            "to_status": target.value,
            "evidence_refs": list(refs),
            "audit_id": audit_id,
            "actor": actor,
            "reason": reason,
            "transitioned_at": changed_at.isoformat(),
        }
        if self.event_bus is not None:
            self.event_bus.publish(EventEnvelope(
                event_id=event_id,
                event_type=EventType.LIFECYCLE_TRANSITION,
                source=EVENT_SOURCE_LIFECYCLE,
                occurred_at=changed_at,
                recorded_at=changed_at,
                correlation_id=str(strategy_id),
                payload=payload,
            ))
        return {**payload, "event_id": event_id}


__all__ = ["LifecycleManager"]
