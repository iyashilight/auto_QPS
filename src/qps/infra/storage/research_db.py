"""持久化的 M3 研究日志。

M3 的 trial、reservation、run、artifact、report 和 blind 结果不是旧版
``experiments`` 表的可变投影。它们在独立 SQLite 日志中只允许追加；预算
预留使用 ``BEGIN IMMEDIATE`` 把所有 scope 放在同一事务边界内。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator

from qps.core.constants import TrialStatus
from qps.core.errors import DataError
from qps.core.models.foundation import (
    BudgetConsumption,
    CandidateDescriptor,
    EvidenceReport,
    FeatureEvaluation,
    FormalRunArtifact,
    ResearchLineage,
    TrialEvent,
    TrialRegistration,
)


def _default(value: Any) -> Any:
    if hasattr(value, "value"):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_default)


class ResearchStore:
    """M3 append-only store and the transaction primitive used by the budget port."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        try:
            if immediate:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_budgets (
                    budget_id TEXT PRIMARY KEY, scope TEXT NOT NULL, scope_ref TEXT NOT NULL,
                    period TEXT NOT NULL, payload TEXT NOT NULL,
                    UNIQUE(scope, scope_ref, period)
                );
                CREATE TABLE IF NOT EXISTS research_trials (
                    trial_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS candidate_families (
                    family_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_trial_events (
                    event_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL,
                    status TEXT NOT NULL, reason TEXT NOT NULL, occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_trial_events_trial
                    ON research_trial_events(trial_id, occurred_at, event_id);
                CREATE TABLE IF NOT EXISTS research_reservations (
                    consumption_id TEXT PRIMARY KEY, budget_id TEXT NOT NULL,
                    trial_id TEXT NOT NULL, payload TEXT NOT NULL, reserved_at TEXT NOT NULL,
                    UNIQUE(budget_id, trial_id)
                );
                CREATE TABLE IF NOT EXISTS research_reveals (
                    reveal_id TEXT PRIMARY KEY, consumption_id TEXT NOT NULL,
                    budget_id TEXT NOT NULL, trial_id TEXT NOT NULL, payload TEXT NOT NULL,
                    revealed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_artifacts (
                    artifact_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL,
                    artifact_type TEXT NOT NULL, content_hash TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_feature_evaluations (
                    evaluation_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    feature_id TEXT NOT NULL, feature_version INTEGER NOT NULL,
                    strategy_family_id TEXT NOT NULL, experiment_id TEXT NOT NULL,
                    dataset_split TEXT NOT NULL,
                    UNIQUE(feature_id, feature_version, strategy_family_id,
                           experiment_id, dataset_split)
                );
                CREATE TABLE IF NOT EXISTS research_reports (
                    report_id TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS research_report_selections (
                    selection_id TEXT PRIMARY KEY, release_id TEXT NOT NULL,
                    report_id TEXT NOT NULL, actor TEXT NOT NULL,
                    selected_at TEXT NOT NULL, reason TEXT NOT NULL,
                    UNIQUE(release_id, report_id)
                );
                CREATE TABLE IF NOT EXISTS research_blind_results (
                    result_id TEXT PRIMARY KEY, challenge_id TEXT NOT NULL,
                    trial_id TEXT NOT NULL, interval_key TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL,
                    UNIQUE(challenge_id, interval_key)
                );
                CREATE TRIGGER IF NOT EXISTS research_trials_no_update
                    BEFORE UPDATE ON research_trials BEGIN SELECT RAISE(ABORT, 'research trial is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_trials_no_delete
                    BEFORE DELETE ON research_trials BEGIN SELECT RAISE(ABORT, 'research trial is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_events_no_update
                    BEFORE UPDATE ON research_trial_events BEGIN SELECT RAISE(ABORT, 'research event is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_events_no_delete
                    BEFORE DELETE ON research_trial_events BEGIN SELECT RAISE(ABORT, 'research event is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_reservations_no_update
                    BEFORE UPDATE ON research_reservations BEGIN SELECT RAISE(ABORT, 'research reservation is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_reservations_no_delete
                    BEFORE DELETE ON research_reservations BEGIN SELECT RAISE(ABORT, 'research reservation is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_artifacts_no_update
                    BEFORE UPDATE ON research_artifacts BEGIN SELECT RAISE(ABORT, 'research artifact is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_artifacts_no_delete
                    BEFORE DELETE ON research_artifacts BEGIN SELECT RAISE(ABORT, 'research artifact is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_candidate_families_no_update
                    BEFORE UPDATE ON candidate_families BEGIN SELECT RAISE(ABORT, 'candidate family is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_candidate_families_no_delete
                    BEFORE DELETE ON candidate_families BEGIN SELECT RAISE(ABORT, 'candidate family is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_feature_evaluations_no_update
                    BEFORE UPDATE ON research_feature_evaluations BEGIN SELECT RAISE(ABORT, 'feature evaluation is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_feature_evaluations_no_delete
                    BEFORE DELETE ON research_feature_evaluations BEGIN SELECT RAISE(ABORT, 'feature evaluation is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_reports_no_update
                    BEFORE UPDATE ON research_reports BEGIN SELECT RAISE(ABORT, 'research report is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_reports_no_delete
                    BEFORE DELETE ON research_reports BEGIN SELECT RAISE(ABORT, 'research report is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_blind_results_no_update
                    BEFORE UPDATE ON research_blind_results BEGIN SELECT RAISE(ABORT, 'blind result is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_blind_results_no_delete
                    BEFORE DELETE ON research_blind_results BEGIN SELECT RAISE(ABORT, 'blind result is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_selections_no_update
                    BEFORE UPDATE ON research_report_selections BEGIN SELECT RAISE(ABORT, 'research selection is append-only'); END;
                CREATE TRIGGER IF NOT EXISTS research_selections_no_delete
                    BEFORE DELETE ON research_report_selections BEGIN SELECT RAISE(ABORT, 'research selection is append-only'); END;
                """
            )

    @staticmethod
    def _trial_transition_allowed(previous: TrialStatus | None, target: TrialStatus) -> bool:
        if previous is None:
            return target == TrialStatus.REGISTERED
        transitions: dict[TrialStatus, set[TrialStatus]] = {
            TrialStatus.REGISTERED: {TrialStatus.RESERVED, TrialStatus.FAILED, TrialStatus.CANCELLED, TrialStatus.TIMED_OUT},
            TrialStatus.RESERVED: {TrialStatus.RUNNING, TrialStatus.FAILED, TrialStatus.CANCELLED, TrialStatus.TIMED_OUT},
            TrialStatus.RUNNING: {TrialStatus.SUCCEEDED, TrialStatus.FAILED, TrialStatus.CANCELLED, TrialStatus.TIMED_OUT},
            TrialStatus.SUCCEEDED: set(), TrialStatus.FAILED: set(),
            TrialStatus.CANCELLED: set(), TrialStatus.TIMED_OUT: set(),
        }
        return target in transitions[previous]

    def append_registration(self, registration: TrialRegistration) -> None:
        payload = canonical_json(registration)
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM research_trials WHERE trial_id = ?", (registration.trial_id,)).fetchone()
            if row is not None:
                if row["payload"] != payload:
                    raise DataError(detail=f"trial registration 内容冲突: {registration.trial_id}")
                return
            conn.execute(
                "INSERT INTO research_trials(trial_id,payload,created_at) VALUES(?,?,?)",
                (registration.trial_id, payload, registration.registered_at.isoformat()),
            )
            event = TrialEvent(
                event_id=f"{registration.trial_id}:REGISTERED",
                trial_id=registration.trial_id, status=TrialStatus.REGISTERED,
                reason="pre-registered", occurred_at=registration.registered_at,
            )
            conn.execute(
                "INSERT INTO research_trial_events(event_id,trial_id,status,reason,occurred_at) VALUES(?,?,?,?,?)",
                (event.event_id, event.trial_id, event.status.value, event.reason, event.occurred_at.isoformat()),
            )

    def save_candidate_family(self, family_id: str, candidate: CandidateDescriptor, created_at: datetime) -> None:
        payload = canonical_json(candidate)
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM candidate_families WHERE family_id = ?", (family_id,)).fetchone()
            if row is not None:
                return
            conn.execute("INSERT INTO candidate_families(family_id,payload,created_at) VALUES(?,?,?)", (family_id, payload, created_at.isoformat()))

    def list_candidate_families(self) -> tuple[tuple[str, CandidateDescriptor], ...]:
        with self.connect() as conn:
            rows = conn.execute("SELECT family_id,payload FROM candidate_families ORDER BY family_id").fetchall()
        return tuple((str(row["family_id"]), CandidateDescriptor.model_validate(json.loads(row["payload"]))) for row in rows)
    def append_trial_event(self, event: TrialEvent) -> None:
        with self.connect(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM research_trials WHERE trial_id = ?", (event.trial_id,)).fetchone() is None:
                raise DataError(detail=f"trial 未预注册: {event.trial_id}")
            existing = conn.execute(
                "SELECT trial_id,status,reason,occurred_at FROM research_trial_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            expected = (event.trial_id, event.status.value, event.reason, event.occurred_at.isoformat())
            if existing is not None:
                if tuple(existing) != expected:
                    raise DataError(detail=f"trial event 内容冲突: {event.event_id}")
                return
            latest = conn.execute(
                "SELECT status FROM research_trial_events WHERE trial_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
                (event.trial_id,),
            ).fetchone()
            previous = TrialStatus(latest["status"]) if latest else None
            if not self._trial_transition_allowed(previous, event.status):
                raise DataError(detail=f"非法 trial 状态迁移: {previous} -> {event.status}")
            conn.execute(
                "INSERT INTO research_trial_events(event_id,trial_id,status,reason,occurred_at) VALUES(?,?,?,?,?)",
                (event.event_id, event.trial_id, event.status.value, event.reason, event.occurred_at.isoformat()),
            )

    def latest_trial_status(self, trial_id: str) -> TrialStatus:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT status FROM research_trial_events WHERE trial_id = ? ORDER BY occurred_at DESC, rowid DESC LIMIT 1",
                (trial_id,),
            ).fetchone()
        if row is None:
            raise DataError(detail=f"trial 不存在: {trial_id}")
        return TrialStatus(row["status"])

    def get_registration(self, trial_id: str) -> TrialRegistration:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM research_trials WHERE trial_id = ?", (trial_id,)).fetchone()
        if row is None:
            raise DataError(detail=f"trial 不存在: {trial_id}")
        return TrialRegistration.model_validate(json.loads(row["payload"]))

    def verify_formal_trial(self, *, trial_id: str, release_id: str, allow_rerun: bool = False) -> TrialRegistration:
        """Verify the durable service-issued authorization before a formal run."""
        registration = self.get_registration(trial_id)
        if registration.release_id != release_id:
            raise DataError(detail=f"formal trial 与 Release 不一致: {trial_id}")
        status = self.latest_trial_status(trial_id)
        allowed = {TrialStatus.RUNNING}
        if allow_rerun:
            allowed.add(TrialStatus.SUCCEEDED)
        if status not in allowed:
            raise DataError(detail=f"formal engine 只能运行 service-issued RUNNING trial: {trial_id}")
        if allow_rerun and status is TrialStatus.SUCCEEDED and not (
            self.get_artifacts(trial_id, "formal-run-v2") or self.get_artifacts(trial_id, "formal-run")
        ):
            raise DataError(detail=f"formal rerun 缺少既有 formal-run artifact: {trial_id}")
        with self.connect() as conn:
            reservation = conn.execute(
                "SELECT 1 FROM research_reservations WHERE trial_id = ? LIMIT 1", (trial_id,)
            ).fetchone()
        if reservation is None:
            raise DataError(detail=f"formal trial 缺少 durable budget reservation: {trial_id}")
        return registration

    def verify_formal_run_artifact(self, *, trial_id: str, run_id: str) -> None:
        """Require the append-only run artifact before accepting EvidenceReport."""
        self.verify_formal_trial(trial_id=trial_id, release_id=self.get_registration(trial_id).release_id or "")
        artifacts = self.get_artifacts(trial_id, "formal-run-v2") or self.get_artifacts(trial_id, "formal-run")
        if not artifacts:
            raise DataError(detail=f"formal EvidenceReport 缺少 formal-run artifact: {trial_id}")
        payload = artifacts[-1]["payload"]
        if not isinstance(payload, dict) or payload.get("run_id") != run_id:
            raise DataError(detail=f"formal EvidenceReport 的 run artifact 不匹配: {run_id}")
        config = payload.get("engine_config")
        if not isinstance(config, dict) or config.get("formal") is not True or config.get("trial_id") != trial_id:
            raise DataError(detail=f"formal-run artifact 未经 service-issued trial 授权: {trial_id}")

    def append_artifact(self, *, artifact_id: str, trial_id: str, artifact_type: str,
                        content_hash: str, payload: Any, created_at: datetime) -> None:
        encoded = canonical_json(payload)
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT trial_id,artifact_type,content_hash,payload,created_at FROM research_artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
            expected = (trial_id, artifact_type, content_hash, encoded, created_at.isoformat())
            if row is not None:
                if tuple(row) != expected:
                    raise DataError(detail=f"研究 artifact 内容冲突: {artifact_id}")
                return
            conn.execute(
                "INSERT INTO research_artifacts(artifact_id,trial_id,artifact_type,content_hash,payload,created_at) VALUES(?,?,?,?,?,?)",
                (artifact_id, trial_id, artifact_type, content_hash, encoded, created_at.isoformat()),
            )

    def get_artifacts(self, trial_id: str, artifact_type: str | None = None) -> list[dict[str, Any]]:
        query = "SELECT artifact_id,artifact_type,content_hash,payload,created_at FROM research_artifacts WHERE trial_id = ?"
        params: list[Any] = [trial_id]
        if artifact_type is not None:
            query += " AND artifact_type = ?"
            params.append(artifact_type)
        query += " ORDER BY created_at, rowid"
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [{"artifact_id": row["artifact_id"], "artifact_type": row["artifact_type"], "content_hash": row["content_hash"], "payload": json.loads(row["payload"]), "created_at": row["created_at"]} for row in rows]

    @staticmethod
    def lineage_from_artifact(artifact: FormalRunArtifact) -> ResearchLineage:
        return ResearchLineage(
            release_id=artifact.release_id,
            release_archive_hash=artifact.release_archive_hash,
            data_manifest_hash=artifact.data_manifest_hash,
            feature_materialization_hash=artifact.feature_materialization_hash,
            rule_version=artifact.historical_rule_hash,
            cost_version=artifact.cost_hash,
            environment_lock=artifact.environment_lock,
            seed=artifact.seed,
            clock=artifact.clock,
        )

    def append_formal_run_artifact(self, artifact: FormalRunArtifact) -> None:
        """Append the actual engine lineage; registration can only constrain it."""
        registration = self.get_registration(artifact.trial_id)
        if registration.release_id != artifact.release_id:
            raise DataError(detail="formal artifact 与 registration Release 不一致")
        expected = {
            "release_archive_hash": registration.release_archive_hash,
            "data_manifest_hash": registration.data_manifest_hash,
            "feature_materialization_hash": registration.feature_materialization_hash,
            "historical_rule_hash": registration.historical_rule_hash or registration.rule_version,
            "cost_hash": registration.cost_hash or registration.cost_version,
            "environment_lock": registration.environment_lock,
            "seed": registration.seed,
            "clock": registration.clock_id,
        }
        actual = artifact.model_dump(mode="json")
        for key, value in expected.items():
            if value is not None and str(actual[key]) != str(value):
                raise DataError(detail=f"formal artifact {key} 与 registration 预期不一致")
        lineage = self.lineage_from_artifact(artifact)
        expected_hash = hashlib.sha256(canonical_json(lineage).encode()).hexdigest()
        if artifact.canonical_lineage_hash != expected_hash:
            raise DataError(detail="formal artifact canonical lineage hash 不一致")
        payload = canonical_json(artifact)
        content_hash = hashlib.sha256(payload.encode()).hexdigest()
        self.append_artifact(
            artifact_id=artifact.artifact_id, trial_id=artifact.trial_id,
            artifact_type="formal-run-v2", content_hash=content_hash,
            payload=artifact, created_at=artifact.created_at,
        )

    def get_formal_run_artifact(self, *, trial_id: str, run_id: str) -> FormalRunArtifact:
        matches = []
        for item in self.get_artifacts(trial_id, "formal-run-v2"):
            payload = item["payload"]
            if isinstance(payload, dict) and payload.get("run_id") == run_id:
                artifact = FormalRunArtifact.model_validate(payload)
                expected_hash = hashlib.sha256(canonical_json(artifact).encode()).hexdigest()
                if item["content_hash"] != expected_hash:
                    raise DataError(detail=f"formal artifact content hash 不一致: {item['artifact_id']}")
                matches.append(artifact)
        if not matches:
            raise DataError(detail=f"formal v2 artifact 不存在: {trial_id}/{run_id}")
        return matches[-1]

    def get_formal_artifact_by_id(self, artifact_id: str) -> FormalRunArtifact | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload,content_hash FROM research_artifacts WHERE artifact_id = ? AND artifact_type = 'formal-run-v2'",
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        artifact = FormalRunArtifact.model_validate(json.loads(row["payload"]))
        expected_hash = hashlib.sha256(canonical_json(artifact).encode()).hexdigest()
        if row["content_hash"] != expected_hash:
            raise DataError(detail=f"formal artifact content hash 不一致: {artifact_id}")
        return artifact

    def append_report(self, report: EvidenceReport) -> None:
        payload = canonical_json(report)
        if report.formal:
            if len(report.experiment_refs) < 2:
                raise DataError(detail="formal EvidenceReport 必须绑定 trial_id 和 run_id")
            if report.formal_artifact_id is not None:
                artifact = self.get_formal_run_artifact(
                    trial_id=report.experiment_refs[0], run_id=report.experiment_refs[1],
                )
                if artifact.artifact_id != report.formal_artifact_id:
                    raise DataError(detail="EvidenceReport formal artifact 引用不一致")
                expected_lineage = self.lineage_from_artifact(artifact)
                if report.lineage != expected_lineage:
                    raise DataError(detail="EvidenceReport lineage 与 formal artifact 不一致")
            else:
                self.verify_formal_run_artifact(
                    trial_id=report.experiment_refs[0], run_id=report.experiment_refs[1],
                )
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM research_reports WHERE report_id = ?", (report.report_id,)).fetchone()
            if row is not None:
                if row["payload"] != payload:
                    raise DataError(detail=f"EvidenceReport 内容冲突: {report.report_id}")
                return
            conn.execute("INSERT INTO research_reports(report_id,payload,created_at) VALUES(?,?,?)", (report.report_id, payload, report.created_at.isoformat()))

    def get_report(self, report_id: str) -> EvidenceReport:
        with self.connect() as conn:
            row = conn.execute("SELECT payload FROM research_reports WHERE report_id = ?", (report_id,)).fetchone()
        if row is None:
            raise DataError(detail=f"EvidenceReport 不存在: {report_id}")
        return EvidenceReport.model_validate(json.loads(row["payload"]))

    def append_feature_evaluation(self, evaluation: FeatureEvaluation) -> None:
        payload = canonical_json(evaluation)
        scope_key = ":".join((evaluation.feature_id, str(evaluation.feature_version), evaluation.strategy_family_id, evaluation.experiment_id, evaluation.dataset_split.value))
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT payload FROM research_feature_evaluations WHERE evaluation_id = ?", (evaluation.evaluation_id,)).fetchone()
            if row is not None:
                if row["payload"] != payload:
                    raise DataError(detail=f"FeatureEvaluation 内容冲突: {evaluation.evaluation_id}")
                return
            try:
                conn.execute(
                    "INSERT INTO research_feature_evaluations(evaluation_id,scope_key,payload,created_at,feature_id,feature_version,strategy_family_id,experiment_id,dataset_split) VALUES(?,?,?,?,?,?,?,?,?)",
                    (evaluation.evaluation_id, scope_key, payload, evaluation.evaluated_at.isoformat(), evaluation.feature_id, evaluation.feature_version, evaluation.strategy_family_id, evaluation.experiment_id, evaluation.dataset_split.value),
                )
            except sqlite3.IntegrityError as exc:
                raise DataError(detail="FeatureEvaluation scope 已存在不同记录") from exc

    def select_report(self, *, selection_id: str, release_id: str, report_id: str,
                      actor: str, reason: str, selected_at: datetime) -> None:
        report = self.get_report(report_id)
        if not report.formal:
            raise DataError(detail="只有 formal EvidenceReport 可以被 Release 引用")
        if not actor or not reason:
            raise DataError(detail="Release evidence 选择必须记录人工 actor/reason")
        with self.connect(immediate=True) as conn:
            existing = conn.execute("SELECT release_id,report_id,actor,selected_at,reason FROM research_report_selections WHERE selection_id = ?", (selection_id,)).fetchone()
            expected = (release_id, report_id, actor, selected_at.isoformat(), reason)
            if existing is not None:
                if tuple(existing) != expected:
                    raise DataError(detail=f"Evidence 选择内容冲突: {selection_id}")
                return
            conn.execute("INSERT INTO research_report_selections(selection_id,release_id,report_id,actor,selected_at,reason) VALUES(?,?,?,?,?,?)", (selection_id, *expected))

    def is_report_selected(self, *, release_id: str, report_id: str) -> bool:
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM research_report_selections WHERE release_id=? AND report_id=?", (release_id, report_id)).fetchone() is not None

    def list_reservations(self, trial_id: str) -> tuple[BudgetConsumption, ...]:
        with self.connect() as conn:
            rows = conn.execute("SELECT payload FROM research_reservations WHERE trial_id = ? ORDER BY budget_id", (trial_id,)).fetchall()
        return tuple(BudgetConsumption.model_validate(json.loads(row["payload"])) for row in rows)

    def append_reservations(self, conn: sqlite3.Connection, reservations: tuple[BudgetConsumption, ...], reserved_at: datetime) -> None:
        for item in reservations:
            payload = canonical_json(item)
            try:
                conn.execute(
                    "INSERT INTO research_reservations(consumption_id,budget_id,trial_id,payload,reserved_at) VALUES(?,?,?,?,?)",
                    (item.consumption_id, item.budget_id, item.trial_id, payload, reserved_at.isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise DataError(detail=f"(budget_id,trial_id) 已经有 reservation: {item.budget_id}/{item.trial_id}") from exc

    def append_reveals(self, conn: sqlite3.Connection, rows: list[tuple[str, str, str, str, str, str]]) -> None:
        conn.executemany(
            "INSERT INTO research_reveals(reveal_id,consumption_id,budget_id,trial_id,payload,revealed_at) VALUES(?,?,?,?,?,?)",
            rows,
        )

    def append_blind_result(self, *, result_id: str, challenge_id: str, trial_id: str,
                            interval_key: str, payload: Any, created_at: datetime) -> None:
        encoded = canonical_json(payload)
        with self.connect(immediate=True) as conn:
            row = conn.execute("SELECT challenge_id,trial_id,interval_key,payload,created_at FROM research_blind_results WHERE result_id = ?", (result_id,)).fetchone()
            expected = (challenge_id, trial_id, interval_key, encoded, created_at.isoformat())
            if row is not None:
                if tuple(row) != expected:
                    raise DataError(detail=f"blind result 内容冲突: {result_id}")
                return
            try:
                conn.execute("INSERT INTO research_blind_results(result_id,challenge_id,trial_id,interval_key,payload,created_at) VALUES(?,?,?,?,?,?)", (result_id, *expected))
            except sqlite3.IntegrityError as exc:
                raise DataError(detail="blind interval 已被揭示，只能追加一次") from exc


SQLiteResearchStore = ResearchStore


__all__ = ["ResearchStore", "SQLiteResearchStore", "canonical_json"]
