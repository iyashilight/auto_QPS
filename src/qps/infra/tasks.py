"""Durable task queue infrastructure.

The scheduler persists task intent and lifecycle in ``MetaDB``.  The control
layer re-exports this implementation, so infrastructure never depends on
control-plane code.
"""

from __future__ import annotations

import inspect
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from qps.core.events import EVENT_SOURCE_SCHEDULER, EventEnvelope, EventType, make_event_id

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.infra.event_bus import EventBus
    from qps.infra.storage.meta_db import MetaDB


def _now() -> datetime:
    return datetime.now(UTC)


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


class TaskScheduler:
    """Persist schedule intent and lifecycle without running arbitrary code."""

    def __init__(self, meta_db: MetaDB, event_bus: EventBus | None = None) -> None:
        self.meta_db = meta_db
        self.event_bus = event_bus
        self.meta_db.initialize()
        self.meta_db.setup_insert_only_constraints()

    @staticmethod
    def _validate_job(job: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        job_type = job.get("job_type")
        if not isinstance(job_type, str) or not job_type.strip():
            raise ValueError("job 必须包含非空 job_type")
        schedule_keys = [key for key in ("cron", "interval") if job.get(key) is not None]
        if len(schedule_keys) != 1:
            raise ValueError("job 必须且只能包含 cron 或 interval")
        if "params" in job and not isinstance(job["params"], dict):
            raise ValueError("job.params 必须是 object")
        return job_type.strip(), dict(job)

    def _publish(self, event_type: EventType, job_id: str, payload: dict[str, Any], at: datetime) -> None:
        if self.event_bus is not None:
            self.event_bus.publish(EventEnvelope(
                event_id=make_event_id(uuid.uuid4().hex), event_type=event_type,
                source=EVENT_SOURCE_SCHEDULER, occurred_at=at, recorded_at=at,
                correlation_id=job_id, payload=payload,
            ))

    async def schedule(self, job: dict[str, Any]) -> str:
        job_type, normalized = self._validate_job(job)
        job_id = _id("job")
        now = _now()
        params = {"scheduler": True, "job": normalized}
        with self.meta_db.transaction() as conn:
            conn.execute(
                "INSERT INTO tasks(task_id,task_type,params,status,started_at,finished_at,error) "
                "VALUES (?,?,?,?,?,?,?)",
                (job_id, job_type, json.dumps(params, ensure_ascii=False, sort_keys=True), "SCHEDULED", None, None, None),
            )
            conn.execute(
                "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_id("audit"), "scheduler", "TASK_SCHEDULE", "task", job_id,
                 None, json.dumps(normalized, ensure_ascii=False, sort_keys=True), now.isoformat(), "schedule"),
            )
        self._publish(EventType.TASK_RUN, job_id, {"action": "SCHEDULED", "job": normalized}, now)
        return job_id

    async def cancel(self, job_id: str) -> None:
        now = _now()
        with self.meta_db.transaction() as conn:
            row = conn.execute(
                "SELECT status,params FROM tasks WHERE task_id = ? AND json_extract(params, '$.scheduler') = 1",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"调度任务不存在: {job_id}")
            if row[0] in {"CANCELED", "COMPLETED", "FAILED"}:
                return
            conn.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE task_id = ?",
                ("CANCELED", now.isoformat(), "canceled by control plane", job_id),
            )
            conn.execute(
                "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_id("audit"), "scheduler", "TASK_CANCEL", "task", job_id,
                 str(row[0]), "CANCELED", now.isoformat(), "cancel"),
            )
        self._publish(EventType.TASK_RUN, job_id, {"action": "CANCELED"}, now)

    async def run_once(
        self,
        job_id: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> dict[str, Any]:
        """Execute one persisted job with durable TaskRun state transitions."""
        started = _now()
        with self.meta_db.transaction() as conn:
            row = conn.execute(
                "SELECT task_type,params,status FROM tasks WHERE task_id = ? "
                "AND json_extract(params, '$.scheduler') = 1",
                (job_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"调度任务不存在: {job_id}")
            if row[2] != "SCHEDULED":
                raise ValueError(f"任务当前不可执行: {job_id} ({row[2]})")
            conn.execute(
                "UPDATE tasks SET status = ?, started_at = ?, error = NULL WHERE task_id = ?",
                ("RUNNING", started.isoformat(), job_id),
            )
            conn.execute(
                "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_id("audit"), "scheduler", "TASK_START", "task", job_id,
                 "SCHEDULED", "RUNNING", started.isoformat(), "run_once"),
            )
            params = json.loads(row[1])["job"]

        try:
            output = handler(params)
            if inspect.isawaitable(output):
                output = await output
        except Exception as exc:
            finished = _now()
            with self.meta_db.transaction() as conn:
                conn.execute(
                    "UPDATE tasks SET status = ?, finished_at = ?, error = ? WHERE task_id = ?",
                    ("FAILED", finished.isoformat(), str(exc), job_id),
                )
                conn.execute(
                    "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (_id("audit"), "scheduler", "TASK_FAILED", "task", job_id,
                     "RUNNING", "FAILED", finished.isoformat(), str(exc)),
                )
            self._publish(EventType.TASK_RUN, job_id, {"action": "FAILED", "error": str(exc)}, finished)
            raise

        finished = _now()
        with self.meta_db.transaction() as conn:
            conn.execute(
                "UPDATE tasks SET status = ?, finished_at = ?, error = NULL WHERE task_id = ?",
                ("SUCCEEDED", finished.isoformat(), job_id),
            )
            conn.execute(
                "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (_id("audit"), "scheduler", "TASK_COMPLETE", "task", job_id,
                 "RUNNING", "SUCCEEDED", finished.isoformat(), "run_once"),
            )
        self._publish(EventType.TASK_RUN, job_id, {"action": "SUCCEEDED"}, finished)
        return {"job_id": job_id, "status": "SUCCEEDED", "output": output, "finished_at": finished.isoformat()}

    async def list_jobs(self) -> list[dict[str, Any]]:
        with self.meta_db.connect() as conn:
            rows = conn.execute(
                "SELECT task_id,task_type,params,status,started_at,finished_at,error FROM tasks "
                "WHERE json_extract(params, '$.scheduler') = 1 ORDER BY task_id",
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(row[2])
            jobs.append({
                "job_id": row[0], "job_type": row[1], "job": payload["job"],
                "status": row[3], "started_at": row[4], "finished_at": row[5], "error": row[6],
            })
        return jobs


TaskQueue = TaskScheduler

__all__ = ["TaskQueue", "TaskScheduler"]
