"""Durable control-plane service tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from qps.control.lifecycle import LifecycleManager
from qps.control.scheduler import TaskScheduler
from qps.core.events import EventType
from qps.infra.event_bus import EventBus
from qps.infra.storage.meta_db import MetaDB

if TYPE_CHECKING:
    from pathlib import Path


def _seed_strategy(meta: MetaDB, strategy_id: str = "control-strategy") -> None:
    meta.initialize()
    with meta.transaction() as conn:
        conn.execute(
            "INSERT INTO strategies(strategy_id,name,style,created_at,updated_at) VALUES (?,?,?,?,?)",
            (strategy_id, strategy_id, "TREND", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )


def _seed_evidence(meta: MetaDB, report_id: str = "report-control-1") -> None:
    with meta.transaction() as conn:
        conn.execute(
            "INSERT INTO evidence_reports(report_id,evidence_quality,expected_edge,risk_estimate,experiment_refs,created_at) "
            "VALUES (?,?,?,?,?,?)",
            (report_id, "{}", "{}", "{}", "[]", "2026-01-01T00:00:00+00:00"),
        )


def test_lifecycle_transition_is_atomic_audited_and_published(tmp_path: Path) -> None:
    meta = MetaDB(tmp_path / "meta.db")
    _seed_strategy(meta)
    _seed_evidence(meta)
    bus = EventBus()
    events = []
    bus.subscribe(events.append, event_type=EventType.LIFECYCLE_TRANSITION)

    result = asyncio.run(LifecycleManager(meta, bus).transition(
        "control-strategy", "BACKTESTING", ["report-control-1"], "tester", "fixture",
    ))

    assert result["from_status"] == "DRAFT"
    assert result["to_status"] == "BACKTESTING"
    assert len(events) == 1
    assert events[0].payload["audit_id"] == result["audit_id"]
    with meta.connect() as conn:
        assert conn.execute(
            "SELECT lifecycle_status FROM strategies WHERE strategy_id=?", ("control-strategy",),
        ).fetchone()[0] == "BACKTESTING"
        assert conn.execute(
            "SELECT action FROM audit_log WHERE log_id=?", (result["audit_id"],),
        ).fetchone()[0] == "LIFECYCLE_TRANSITION"

    # A fresh manager reads the durable projection rather than in-memory state.
    assert asyncio.run(LifecycleManager(meta).transition(
        "control-strategy", "VALIDATED", ["report-control-1"], "tester", "fixture-2",
    ))["from_status"] == "BACKTESTING"


def test_lifecycle_rejects_missing_evidence_and_invalid_transition(tmp_path: Path) -> None:
    meta = MetaDB(tmp_path / "meta.db")
    _seed_strategy(meta)
    manager = LifecycleManager(meta)
    with pytest.raises(ValueError, match="证据不存在"):
        asyncio.run(manager.transition("control-strategy", "BACKTESTING", ["missing"], "tester", "fixture"))
    with meta.connect() as conn:
        assert conn.execute(
            "SELECT lifecycle_status FROM strategies WHERE strategy_id=?", ("control-strategy",),
        ).fetchone()[0] == "DRAFT"

    _seed_evidence(meta)
    with pytest.raises(ValueError, match="迁移非法"):
        asyncio.run(manager.transition("control-strategy", "PRODUCTION", ["report-control-1"], "tester", "fixture"))


def test_scheduler_persists_lists_cancels_and_survives_restart(tmp_path: Path) -> None:
    meta = MetaDB(tmp_path / "meta.db")
    bus = EventBus()
    scheduler = TaskScheduler(meta, bus)
    job_id = asyncio.run(scheduler.schedule({
        "job_type": "data.collect", "interval": "5m", "params": {"instrument": "BTC"},
    }))
    listed = asyncio.run(TaskScheduler(meta).list_jobs())
    assert listed == [{
        "job_id": job_id, "job_type": "data.collect",
        "job": {"job_type": "data.collect", "interval": "5m", "params": {"instrument": "BTC"}},
        "status": "SCHEDULED", "started_at": None, "finished_at": None, "error": None,
    }]

    asyncio.run(TaskScheduler(meta).cancel(job_id))
    assert asyncio.run(TaskScheduler(meta).list_jobs())[0]["status"] == "CANCELED"
    with meta.connect() as conn:
        actions = [row[0] for row in conn.execute(
            "SELECT action FROM audit_log WHERE target_id=? ORDER BY ts", (job_id,),
        ).fetchall()]
    assert actions == ["TASK_SCHEDULE", "TASK_CANCEL"]


def test_scheduler_rejects_ambiguous_schedule(tmp_path: Path) -> None:
    scheduler = TaskScheduler(MetaDB(tmp_path / "meta.db"))
    with pytest.raises(ValueError, match="只能包含"):
        asyncio.run(scheduler.schedule({"job_type": "x", "cron": "*", "interval": "1m"}))


def test_scheduler_run_once_records_task_result_and_failure(tmp_path: Path) -> None:
    meta = MetaDB(tmp_path / "meta.db")
    scheduler = TaskScheduler(meta)
    job_id = asyncio.run(scheduler.schedule({"job_type": "research.run", "interval": "1m"}))
    result = asyncio.run(scheduler.run_once(job_id, lambda job: {"accepted": job["job_type"]}))
    assert result["status"] == "SUCCEEDED"
    assert asyncio.run(TaskScheduler(meta).list_jobs())[0]["status"] == "SUCCEEDED"

    failed_id = asyncio.run(scheduler.schedule({"job_type": "research.fail", "interval": "1m"}))

    def fail(_job: dict[str, object]) -> None:
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError, match="fixture failure"):
        asyncio.run(scheduler.run_once(failed_id, fail))
    failed_job = next(item for item in asyncio.run(TaskScheduler(meta).list_jobs()) if item["job_id"] == failed_id)
    assert failed_job["status"] == "FAILED"
    assert failed_job["error"] == "fixture failure"


def test_scheduler_persistence_restart_cancel_and_execution(tmp_path: Path) -> None:
    meta = MetaDB(tmp_path / "meta.db")
    scheduler = TaskScheduler(meta)
    job_id = asyncio.run(scheduler.schedule({"job_type": "control.full", "interval": "1m"}))
    assert asyncio.run(TaskScheduler(meta).list_jobs())[0]["status"] == "SCHEDULED"
    asyncio.run(TaskScheduler(meta).cancel(job_id))
    assert asyncio.run(TaskScheduler(meta).list_jobs())[0]["status"] == "CANCELED"

    run_id = asyncio.run(scheduler.schedule({"job_type": "control.run", "interval": "1m"}))
    result = asyncio.run(scheduler.run_once(run_id, lambda job: {"job_type": job["job_type"]}))
    assert result["status"] == "SUCCEEDED"
    run_job = next(item for item in asyncio.run(TaskScheduler(meta).list_jobs()) if item["job_id"] == run_id)
    assert run_job["status"] == "SUCCEEDED"
