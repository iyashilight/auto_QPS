"""Canonical prepare/commit journal for formal strategy state."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

from qps.core.errors import DataError
from qps.core.sdk.formal import canonical_json, content_hash
from qps.infra.storage.trading_journal import JournalDB


@dataclass(frozen=True)
class CommittedState:
    deployment_id: str
    release_id: str
    input_snapshot_ref: str
    state_version: int
    state_hash: str
    intent_hash: str
    payload: dict[str, Any]
    schema_version: int = 1
    previous_state_hash: str = ""
    event_id: str = ""
    input_hash: str = ""
    intent: dict[str, Any] | None = None
    decision_time: str = ""


class StateStore:
    """SQLite WAL journal with atomic prepare/commit and replay idempotency."""

    def __init__(self, path: Path | str,
                 *, fault_hook: Callable[[str], None] | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fault_hook = fault_hook
        self._journal = JournalDB(self.path)
        self._journal.initialize()
        self._journal.ensure_strategy_state_schema()

    def commit(self, snapshot: CommittedState) -> CommittedState:
        self._validate(snapshot)
        payload_json = canonical_json(snapshot.payload)
        intent_json = None if snapshot.intent is None else canonical_json(snapshot.intent)
        attempt_id = snapshot.event_id or content_hash({
            "deployment_id": snapshot.deployment_id,
            "input_snapshot_ref": snapshot.input_snapshot_ref,
            "state_hash": snapshot.state_hash,
            "intent_hash": snapshot.intent_hash,
        })
        with self._journal.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT c.attempt_id,c.state_version,a.release_id,a.schema_version,a.input_hash,"
                    "a.state_hash,a.intent_hash,a.state_json,a.intent_json,a.event_id,a.previous_state_hash "
                    "FROM state_commits c JOIN state_attempts a ON a.attempt_id=c.attempt_id "
                    "WHERE c.deployment_id=? AND c.input_snapshot_ref=?",
                    (snapshot.deployment_id, snapshot.input_snapshot_ref)).fetchone()
                if existing is not None:
                    expected = (snapshot.release_id, snapshot.schema_version, snapshot.input_hash,
                                snapshot.state_hash, snapshot.intent_hash, payload_json, intent_json,
                                snapshot.event_id, snapshot.previous_state_hash)
                    actual = existing[2:]
                    if tuple(actual) != expected:
                        raise DataError(detail="重复输入 snapshot 内容冲突")
                    conn.commit()
                    return snapshot
                latest = conn.execute(
                    "SELECT a.release_id,a.schema_version,c.state_version,a.state_hash "
                    "FROM state_commits c JOIN state_attempts a ON a.attempt_id=c.attempt_id "
                    "WHERE c.deployment_id=? ORDER BY c.state_version DESC LIMIT 1",
                    (snapshot.deployment_id,)).fetchone()
                if latest is not None:
                    if latest[0] != snapshot.release_id or latest[1] != snapshot.schema_version:
                        raise DataError(detail="Release/state schema 不兼容，必须迁移或冷启动")
                    if snapshot.state_version <= latest[2]:
                        raise DataError(detail="state_version 必须单调递增")
                    if snapshot.previous_state_hash != latest[3]:
                        raise DataError(detail="state previous hash 不匹配")
                conn.execute(
                    "INSERT INTO state_attempts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (attempt_id, snapshot.deployment_id, snapshot.release_id, snapshot.schema_version,
                     snapshot.input_snapshot_ref, snapshot.input_hash, snapshot.previous_state_hash,
                     snapshot.state_hash, snapshot.intent_hash, snapshot.event_id, payload_json,
                     intent_json, snapshot.decision_time))
                if self._fault_hook is not None:
                    self._fault_hook("after_prepare")
                conn.execute(
                    "INSERT INTO state_commits VALUES (?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                    (snapshot.deployment_id, snapshot.input_snapshot_ref, attempt_id, snapshot.state_version))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return snapshot

    def latest(self, deployment_id: str) -> CommittedState | None:
        with self._journal.connect() as conn:
            row = conn.execute(self._select_sql() + " WHERE c.deployment_id=? ORDER BY c.state_version DESC LIMIT 1",
                               (deployment_id,)).fetchone()
        return self._row_to_state(row) if row is not None else None

    def replay(self, deployment_id: str, input_snapshot_ref: str) -> CommittedState | None:
        with self._journal.connect() as conn:
            row = conn.execute(self._select_sql() + " WHERE c.deployment_id=? AND c.input_snapshot_ref=?",
                               (deployment_id, input_snapshot_ref)).fetchone()
        return self._row_to_state(row) if row is not None else None

    @staticmethod
    def _select_sql() -> str:
        return ("SELECT a.deployment_id,a.release_id,a.input_snapshot_ref,c.state_version,"
                "a.state_hash,a.intent_hash,a.state_json,a.schema_version,a.previous_state_hash,"
                "a.event_id,a.input_hash,a.intent_json,a.decision_time FROM state_commits c "
                "JOIN state_attempts a ON a.attempt_id=c.attempt_id")

    @staticmethod
    def _row_to_state(row: tuple[Any, ...]) -> CommittedState:
        return CommittedState(row[0], row[1], row[2], row[3], row[4], row[5], json.loads(row[6]),
            row[7], row[8], row[9], row[10], None if row[11] is None else json.loads(row[11]), row[12])

    @staticmethod
    def _validate(snapshot: CommittedState) -> None:
        if snapshot.state_version < 1 or snapshot.schema_version < 1:
            raise DataError(detail="state/schema version 必须为正数")
        if not snapshot.input_hash:
            raise DataError(detail="state 必须绑定 input hash")
        expected = content_hash(snapshot.payload)
        if snapshot.state_hash != expected:
            raise DataError(detail="state_hash 不匹配 canonical state")
        if snapshot.intent is not None and snapshot.intent_hash != content_hash(snapshot.intent):
            raise DataError(detail="intent_hash 不匹配 canonical Intent")
