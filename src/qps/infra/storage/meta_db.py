"""SQLite 元数据库（见 docs/02 第 4.1 节）

提供：
- 按 DDL 建表（strategies, strategy_versions, strategy_params, strategy_allocations,
  instruments, symbol_mappings, exchange_meta, experiments, report_cards, metrics,
  risk_rules, risk_events, audit_log, tasks, artifacts, deployments, portfolio_risk_snapshots）
- 事务化访问封装
- insert-only 约束（对 experiments/audit_log 强制）
- 金额字段精度不丢失（TEXT 存储 Decimal）
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager, nullcontext
from functools import lru_cache
from typing import TYPE_CHECKING

from qps.core.errors import DataError
from qps.core.models.foundation import (
    DatasetManifest,
    FeatureDefinition,
    FeatureMaterialization,
    InstrumentRuleVersion,
    SourceReference,
)

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path


def _canonical_trigger_sql(sql: str) -> str:
    """Remove comments while preserving SQL tokens and expression structure."""
    parts: list[str] = []
    index = 0
    quote: str | None = None
    while index < len(sql):
        char = sql[index]
        if quote is not None:
            parts.append(char)
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    parts.append(sql[index + 1])
                    index += 1
                else:
                    quote = None
            index += 1
            continue
        if char in "'\"`":
            quote = char
            parts.append(char)
            index += 1
        elif sql.startswith("--", index):
            newline = sql.find("\n", index)
            index = len(sql) if newline == -1 else newline
            parts.append(" ")
        elif sql.startswith("/*", index):
            end = sql.find("*/", index + 2)
            index = len(sql) if end == -1 else end + 2
            parts.append(" ")
        else:
            parts.append(char)
            index += 1
    effective_sql = "".join(parts).upper()
    if not re.search(r"\bFOR\s+EACH\s+ROW\b", effective_sql):
        effective_sql = re.sub(
            r"(BEFORE\s+(?:UPDATE|DELETE)\s+ON\s+[A-Z0-9_]+)",
            r"\1 FOR EACH ROW",
            effective_sql,
            count=1,
        )
    return re.sub(r"\s+", "", effective_sql)


@lru_cache
def _canonical_meta_triggers() -> dict[str, str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_DDL)
        conn.executescript(_V05_DDL)
        MetaDB.__new__(MetaDB).setup_insert_only_constraints(conn)
        return {
            name: _canonical_trigger_sql(sql)
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE '_no_%'"
            )
        }
    finally:
        conn.close()


class MetaDB:
    """SQLite 元数据库管理器

    用法：
        db = MetaDB(Path("data/meta.db"))
        db.initialize()  # 建表
        with db.transaction() as conn:
            conn.execute("INSERT INTO strategies ...")
    """

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @property
    def db_path(self) -> Path:
        """Stable data-root accessor for adapters that assemble verified ports."""
        return self._db_path

    def record_data_freshness(self, event_id: str, stream_id: str, stale: bool, payload: object, ts: str) -> None:
        """Append a replayable DATA_STALE/DATA_RECOVERED audit record."""
        action = "DATA_STALE" if stale else "DATA_RECOVERED"
        encoded = self._canonical_json(payload)
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT actor, action, target_type, target_id, before, after, ts, reason "
                "FROM audit_log WHERE log_id = ?", (event_id,),
            ).fetchone()
            expected = ("data.health", action, "DATA_FRESHNESS", stream_id, None, encoded, ts, "state-transition")
            if row is not None:
                if tuple(row) != expected:
                    raise DataError(detail=f"freshness audit 内容冲突: {event_id}")
                return
            conn.execute(
                "INSERT INTO audit_log (log_id, actor, action, target_type, target_id, before, after, ts, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (event_id, "data.health", action, "DATA_FRESHNESS", stream_id, None, encoded, ts, "state-transition"),
            )

    def latest_data_freshness(self) -> dict[str, bool]:
        """Replay the last durable state for every freshness stream."""
        with self.connect() as conn:
            rows = list(conn.execute(
                "SELECT target_id, action FROM audit_log WHERE target_type = 'DATA_FRESHNESS' "
                "ORDER BY ts, log_id"
            ))
        state: dict[str, bool] = {}
        for row in rows:
            state[str(row[0])] = row[1] == "DATA_STALE"
        return state

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        """获取数据库连接（自动关闭）"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """获取事务化连接（自动提交或回滚）"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        """建表（幂等，已存在则跳过）"""
        state = "fresh"
        with self.connect() as conn:
            snapshot = sqlite3.connect(":memory:")
            try:
                conn.backup(snapshot)
                if self._table_exists(conn, "schema_migrations"):
                    self._ensure_migration_version_column(conn)
                state = self._schema_state(conn)
                if state in {"fresh", "legacy"}:
                    conn.executescript(_DDL)
                    conn.executescript(_V05_DDL)
                    self._ensure_migration_version_column(conn)
                    self.setup_insert_only_constraints(conn)
                    self._validate_v05_schema(conn, require_migration=False)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations "
                        "(migration_id, applied_at, schema_version) "
                        "VALUES ('m0-v05-contracts', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 2)"
                    )
                    conn.execute(
                        "UPDATE schema_migrations SET schema_version = 2 "
                        "WHERE migration_id = 'm0-v05-contracts'"
                    )
                else:
                    self._validate_v05_schema(conn, require_migration=True)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                snapshot.backup(conn)
                conn.commit()
                raise RuntimeError(
                    f"MetaDB initialization failed; original schema restored: {exc}"
                ) from exc
            finally:
                snapshot.close()

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return cursor.fetchone() is not None

    # ── M1 durable manifest / feature registry ─────────────

    @staticmethod
    def _canonical_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def save_manifest(self, manifest: DatasetManifest) -> None:
        """Insert an immutable manifest, accepting only an identical retry."""
        source_refs = [ref.model_dump(mode="json") for ref in manifest.source_refs]
        values = (
            manifest.manifest_id,
            manifest.dataset_type,
            manifest.schema_version,
            self._canonical_json(list(manifest.partition_refs)),
            manifest.content_hash,
            self._canonical_json(source_refs),
            manifest.quality_report_ref,
            manifest.created_at.isoformat(),
        )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT dataset_type, schema_version, partition_refs, content_hash, "
                "source_refs, quality_report_ref, created_at FROM dataset_manifests "
                "WHERE manifest_id = ?",
                (manifest.manifest_id,),
            ).fetchone()
            if row is not None:
                existing = {
                    "dataset_type": row[0], "schema_version": row[1],
                    "partition_refs": json.loads(row[2]), "content_hash": row[3],
                    "source_refs": json.loads(row[4]), "quality_report_ref": row[5],
                    "created_at": row[6],
                }
                expected = {
                    "dataset_type": manifest.dataset_type, "schema_version": manifest.schema_version,
                    "partition_refs": list(manifest.partition_refs), "content_hash": manifest.content_hash,
                    "source_refs": source_refs, "quality_report_ref": manifest.quality_report_ref,
                    "created_at": manifest.created_at.isoformat(),
                }
                if existing != expected:
                    raise DataError(detail=f"manifest 内容冲突: {manifest.manifest_id}")
                return
            conn.execute(
                "INSERT INTO dataset_manifests "
                "(manifest_id, dataset_type, schema_version, partition_refs, content_hash, "
                "source_refs, quality_report_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def get_manifest(self, manifest_id: str) -> DatasetManifest:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT manifest_id, dataset_type, schema_version, partition_refs, content_hash, "
                "source_refs, quality_report_ref, created_at FROM dataset_manifests WHERE manifest_id = ?",
                (manifest_id,),
            ).fetchone()
        if row is None:
            raise DataError(detail=f"manifest 不存在: {manifest_id}")
        return DatasetManifest(
            manifest_id=row[0], dataset_type=row[1], schema_version=row[2],
            partition_refs=tuple(json.loads(row[3])), content_hash=row[4],
            source_refs=tuple(SourceReference.model_validate(item) for item in json.loads(row[5])),
            quality_report_ref=row[6], created_at=row[7],
        )

    def list_manifests(self, dataset_type: str | None = None) -> list[DatasetManifest]:
        query = "SELECT manifest_id FROM dataset_manifests"
        args: tuple[str, ...] = ()
        if dataset_type is not None:
            query += " WHERE dataset_type = ?"
            args = (dataset_type,)
        query += " ORDER BY created_at, manifest_id"
        with self.connect() as conn:
            ids = [row[0] for row in conn.execute(query, args)]
        return [self.get_manifest(manifest_id) for manifest_id in ids]

    def save_instrument_rule(self, rule: InstrumentRuleVersion) -> None:
        values = (
            rule.rule_version_id, rule.instrument_id, rule.rules_hash,
            rule.valid_from.isoformat(), rule.valid_to.isoformat() if rule.valid_to else None,
            rule.available_at.isoformat(), rule.created_at.isoformat(), str(rule.tick_size),
            str(rule.lot_size), str(rule.min_notional), str(rule.max_leverage) if rule.max_leverage else None,
        )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT instrument_id, rules_hash, valid_from, valid_to, available_at, created_at, "
                "tick_size, lot_size, min_notional, max_leverage FROM instrument_rule_versions "
                "WHERE rule_version_id = ?", (rule.rule_version_id,),
            ).fetchone()
            if row is not None:
                if tuple(row) != values[1:]:
                    raise DataError(detail=f"instrument rule 内容冲突: {rule.rule_version_id}")
                return
            conn.execute(
                "INSERT INTO instrument_rule_versions "
                "(rule_version_id, instrument_id, rules_hash, valid_from, valid_to, available_at, created_at, "
                "tick_size, lot_size, min_notional, max_leverage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def get_instrument_rule(
        self, instrument_id: str, effective_at: datetime, known_at: datetime
    ) -> InstrumentRuleVersion:
        with self.connect() as conn:
            rows = list(conn.execute(
                "SELECT rule_version_id, instrument_id, rules_hash, valid_from, valid_to, available_at, "
                "created_at, tick_size, lot_size, min_notional, max_leverage "
                "FROM instrument_rule_versions WHERE instrument_id = ?", (instrument_id,)
            ))
        candidates = []
        for row in rows:
            rule = InstrumentRuleVersion(
                rule_version_id=row[0], instrument_id=row[1], rules_hash=row[2],
                valid_from=row[3], valid_to=row[4], available_at=row[5], created_at=row[6],
                tick_size=row[7], lot_size=row[8], min_notional=row[9], max_leverage=row[10],
            )
            if rule.available_at <= known_at and rule.valid_from <= effective_at and (
                rule.valid_to is None or effective_at < rule.valid_to
            ):
                candidates.append(rule)
        if not candidates:
            raise DataError(
                detail=f"没有可用的历史 InstrumentRule: instrument={instrument_id} "
                f"effective_at={effective_at.isoformat()} known_at={known_at.isoformat()}"
            )
        return max(candidates, key=lambda item: (item.valid_from, item.available_at, item.rule_version_id))

    def save_feature_definition(self, definition: FeatureDefinition) -> None:
        payload = definition.model_dump(mode="json")
        values = (
            definition.feature_id, definition.version, definition.semantic_name,
            definition.description, definition.formula_or_code_hash,
            self._canonical_json(payload["input_schema"]),
            self._canonical_json(list(definition.source_types)), definition.window,
            definition.warmup, definition.frequency, definition.available_at_rule,
            definition.owner, definition.status.value, definition.created_at.isoformat(),
        )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT semantic_name, description, formula_or_code_hash, input_schema, source_types, "
                "window, warmup, frequency, available_at_rule, owner, status, created_at "
                "FROM feature_definitions WHERE feature_id = ? AND version = ?",
                (definition.feature_id, definition.version),
            ).fetchone()
            if row is not None:
                existing = tuple(row)
                if existing != values[2:]:
                    raise DataError(detail=f"feature definition 内容冲突: {definition.feature_id}@{definition.version}")
                return
            conn.execute(
                "INSERT INTO feature_definitions "
                "(feature_id, version, semantic_name, description, formula_or_code_hash, input_schema, "
                "source_types, window, warmup, frequency, available_at_rule, owner, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values,
            )

    def get_feature_definition(self, feature_id: str, version: int) -> FeatureDefinition:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT feature_id, version, semantic_name, description, formula_or_code_hash, input_schema, "
                "source_types, window, warmup, frequency, available_at_rule, owner, status, created_at "
                "FROM feature_definitions WHERE feature_id = ? AND version = ?", (feature_id, version),
            ).fetchone()
        if row is None:
            raise DataError(detail=f"feature definition 不存在: {feature_id}@{version}")
        return FeatureDefinition(
            feature_id=row[0], version=row[1], semantic_name=row[2], description=row[3],
            formula_or_code_hash=row[4], input_schema=json.loads(row[5]),
            source_types=tuple(json.loads(row[6])), window=row[7], warmup=row[8],
            frequency=row[9], available_at_rule=row[10], owner=row[11], status=row[12],
            created_at=row[13],
        )

    def list_feature_definitions(self, feature_id: str | None = None) -> list[FeatureDefinition]:
        query = "SELECT feature_id, version FROM feature_definitions"
        args: tuple[str, ...] = ()
        if feature_id is not None:
            query += " WHERE feature_id = ?"
            args = (feature_id,)
        query += " ORDER BY feature_id, version"
        with self.connect() as conn:
            rows = list(conn.execute(query, args))
        return [self.get_feature_definition(row[0], row[1]) for row in rows]

    def save_feature_materialization(self, materialization: FeatureMaterialization) -> None:
        values = (
            materialization.materialization_id, materialization.feature_id,
            materialization.feature_version, self._canonical_json(list(materialization.input_manifest_ids)),
            materialization.output_manifest_id, str(materialization.coverage),
            materialization.quality_report_id, materialization.computed_at.isoformat(),
            materialization.content_hash,
        )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT feature_id, feature_version, input_manifest_ids, output_manifest_id, coverage, "
                "quality_report_id, computed_at, content_hash FROM feature_materializations "
                "WHERE materialization_id = ?", (materialization.materialization_id,),
            ).fetchone()
            if row is not None:
                if tuple(row) != values[1:]:
                    raise DataError(detail=f"materialization 内容冲突: {materialization.materialization_id}")
                return
            conn.execute(
                "INSERT INTO feature_materializations "
                "(materialization_id, feature_id, feature_version, input_manifest_ids, output_manifest_id, "
                "coverage, quality_report_id, computed_at, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )

    def save_feature_batch(
        self,
        definitions: list[FeatureDefinition],
        manifest: DatasetManifest,
        materializations: list[FeatureMaterialization],
    ) -> None:
        """Insert a manifest and all feature lineage rows atomically."""
        source_refs = [ref.model_dump(mode="json") for ref in manifest.source_refs]
        manifest_values = (
            manifest.manifest_id, manifest.dataset_type, manifest.schema_version,
            self._canonical_json(list(manifest.partition_refs)), manifest.content_hash,
            self._canonical_json(source_refs), manifest.quality_report_ref,
            manifest.created_at.isoformat(),
        )
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT dataset_type, schema_version, partition_refs, content_hash, source_refs, "
                "quality_report_ref, created_at FROM dataset_manifests WHERE manifest_id = ?",
                (manifest.manifest_id,),
            ).fetchone()
            if row is not None and tuple(row) != manifest_values[1:]:
                raise DataError(detail=f"manifest 内容冲突: {manifest.manifest_id}")

            for definition in definitions:
                payload = definition.model_dump(mode="json")
                definition_values = (
                    definition.feature_id, definition.version, definition.semantic_name,
                    definition.description, definition.formula_or_code_hash,
                    self._canonical_json(payload["input_schema"]),
                    self._canonical_json(list(definition.source_types)), definition.window,
                    definition.warmup, definition.frequency, definition.available_at_rule,
                    definition.owner, definition.status.value, definition.created_at.isoformat(),
                )
                existing = conn.execute(
                    "SELECT semantic_name, description, formula_or_code_hash, input_schema, source_types, "
                    "window, warmup, frequency, available_at_rule, owner, status, created_at "
                    "FROM feature_definitions WHERE feature_id = ? AND version = ?",
                    (definition.feature_id, definition.version),
                ).fetchone()
                if existing is not None and tuple(existing) != definition_values[2:]:
                    raise DataError(detail=f"feature definition 内容冲突: {definition.feature_id}@{definition.version}")

            for materialization in materializations:
                materialization_values = (
                    materialization.materialization_id, materialization.feature_id,
                    materialization.feature_version,
                    self._canonical_json(list(materialization.input_manifest_ids)),
                    materialization.output_manifest_id, str(materialization.coverage),
                    materialization.quality_report_id, materialization.computed_at.isoformat(),
                    materialization.content_hash,
                )
                existing = conn.execute(
                    "SELECT feature_id, feature_version, input_manifest_ids, output_manifest_id, coverage, "
                    "quality_report_id, computed_at, content_hash FROM feature_materializations "
                    "WHERE materialization_id = ?", (materialization.materialization_id,),
                ).fetchone()
                if existing is not None and tuple(existing) != materialization_values[1:]:
                    raise DataError(detail=f"materialization 内容冲突: {materialization.materialization_id}")

            if row is None:
                conn.execute(
                    "INSERT INTO dataset_manifests (manifest_id, dataset_type, schema_version, partition_refs, "
                    "content_hash, source_refs, quality_report_ref, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    manifest_values,
                )
            for definition in definitions:
                payload = definition.model_dump(mode="json")
                conn.execute(
                    "INSERT OR IGNORE INTO feature_definitions "
                    "(feature_id, version, semantic_name, description, formula_or_code_hash, input_schema, "
                    "source_types, window, warmup, frequency, available_at_rule, owner, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        definition.feature_id, definition.version, definition.semantic_name,
                        definition.description, definition.formula_or_code_hash,
                        self._canonical_json(payload["input_schema"]),
                        self._canonical_json(list(definition.source_types)), definition.window,
                        definition.warmup, definition.frequency, definition.available_at_rule,
                        definition.owner, definition.status.value, definition.created_at.isoformat(),
                    ),
                )
            for materialization in materializations:
                conn.execute(
                    "INSERT OR IGNORE INTO feature_materializations "
                    "(materialization_id, feature_id, feature_version, input_manifest_ids, output_manifest_id, "
                    "coverage, quality_report_id, computed_at, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        materialization.materialization_id, materialization.feature_id,
                        materialization.feature_version,
                        self._canonical_json(list(materialization.input_manifest_ids)),
                        materialization.output_manifest_id, str(materialization.coverage),
                        materialization.quality_report_id, materialization.computed_at.isoformat(),
                        materialization.content_hash,
                    ),
                )

    def get_feature_materialization(self, materialization_id: str) -> FeatureMaterialization:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT materialization_id, feature_id, feature_version, input_manifest_ids, output_manifest_id, "
                "coverage, quality_report_id, computed_at, content_hash FROM feature_materializations "
                "WHERE materialization_id = ?", (materialization_id,),
            ).fetchone()
        if row is None:
            raise DataError(detail=f"materialization 不存在: {materialization_id}")
        return FeatureMaterialization(
            materialization_id=row[0], feature_id=row[1], feature_version=row[2],
            input_manifest_ids=tuple(json.loads(row[3])), output_manifest_id=row[4],
            coverage=row[5], quality_report_id=row[6], computed_at=row[7], content_hash=row[8],
        )

    def list_feature_materializations(self, feature_id: str | None = None) -> list[FeatureMaterialization]:
        query = "SELECT materialization_id FROM feature_materializations"
        args: tuple[str, ...] = ()
        if feature_id is not None:
            query += " WHERE feature_id = ?"
            args = (feature_id,)
        query += " ORDER BY computed_at, materialization_id"
        with self.connect() as conn:
            ids = [row[0] for row in conn.execute(query, args)]
        return [self.get_feature_materialization(item) for item in ids]

    def enforce_insert_only(self, table_name: str, conn: sqlite3.Connection) -> None:
        """强制 insert-only 约束

        对 experiments 表：仅拦截内容列变更，放行 status 列更新
        （R4 语义：实验记录只增不改，但 status 是状态机迁移的合法通道）。
        对其他表（如 audit_log）：无条件禁止 UPDATE 和 DELETE。
        """
        if table_name == "experiments":
            # experiments 表：放行仅 status 列变更的 UPDATE，拦截其他列变更
            # 使用 WHEN 子句：只有当非 status 列发生变更时才触发 RAISE
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS _no_update_{table_name}
                BEFORE UPDATE ON {table_name}
                FOR EACH ROW
                WHEN COALESCE(NEW.run_type, '') <> COALESCE(OLD.run_type, '')
                  OR COALESCE(NEW.strategy_id, '') <> COALESCE(OLD.strategy_id, '')
                  OR NEW.version_no <> OLD.version_no
                  OR COALESCE(NEW.params, '') <> COALESCE(OLD.params, '')
                  OR COALESCE(NEW.data_version, '') <> COALESCE(OLD.data_version, '')
                  OR COALESCE(NEW.feature_version, '') <> COALESCE(OLD.feature_version, '')
                  OR COALESCE(NEW.engine_config, '') <> COALESCE(OLD.engine_config, '')
                  OR COALESCE(NEW.environment_lock, '') <> COALESCE(OLD.environment_lock, '')
                  OR COALESCE(NEW.start_ts, '') <> COALESCE(OLD.start_ts, '')
                  OR COALESCE(NEW.end_ts, '') <> COALESCE(OLD.end_ts, '')
                  OR COALESCE(NEW.created_at, '') <> COALESCE(OLD.created_at, '')
                BEGIN
                    SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止内容列 UPDATE: {table_name}');
                END
            """)
        else:
            # 其他表：无条件禁止 UPDATE
            conn.execute(f"""
                CREATE TRIGGER IF NOT EXISTS _no_update_{table_name}
                BEFORE UPDATE ON {table_name}
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 UPDATE: {table_name}');
                END
            """)
        # 禁止 DELETE
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS _no_delete_{table_name}
            BEFORE DELETE ON {table_name}
            FOR EACH ROW
            BEGIN
                SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 DELETE: {table_name}');
            END
        """)

    def setup_insert_only_constraints(self, connection: sqlite3.Connection | None = None) -> None:
        """设置 insert-only 约束（研究、发布和控制面历史表）。"""
        with (self.connect() if connection is None else nullcontext(connection)) as conn:
            for table in (
                "experiments",
                "experiment_events",
                "audit_log",
                "dataset_manifests",
                "instrument_rule_versions",
                "feature_definitions",
                "feature_materializations",
                "feature_evaluations",
                "trial_registrations",
                "budget_consumptions",
                "evidence_reports",
                "blind_challenge_results",
                "strategy_releases",
                "approval_records",
                "release_deployments",
                "strategy_health_assessments",
                "lifecycle_actions",
                "capital_allocation_policies",
                "v05_portfolio_risk_snapshots",
                "market_chaos_scenarios",
                "chaos_runs",
            ):
                conn.execute(f"DROP TRIGGER IF EXISTS _no_update_{table}")
                conn.execute(f"DROP TRIGGER IF EXISTS _no_delete_{table}")
                self.enforce_insert_only(table, conn)
            if connection is None:
                conn.commit()

    @staticmethod
    def _ensure_migration_version_column(conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        if "schema_version" not in columns:
            conn.execute("ALTER TABLE schema_migrations ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")

    def _schema_state(self, conn: sqlite3.Connection) -> str:
        existing = {
            table for table in _META_V05_TABLES if self._table_exists(conn, table)
        }
        if not existing:
            return "fresh"
        if self._looks_like_current_schema(conn):
            self._validate_v05_schema(conn, require_migration=True)
            return "current"
        if self._is_supported_legacy_schema(conn):
            return "legacy"
        raise RuntimeError("MetaDB schema drift: 既有 schema 不是完整当前版或受支持旧版")

    @staticmethod
    def _looks_like_current_schema(conn: sqlite3.Connection) -> bool:
        return MetaDB._migration_version(conn) == 2

    def _is_current_schema(self, conn: sqlite3.Connection) -> bool:
        try:
            self._validate_v05_schema(conn, require_migration=True)
        except RuntimeError:
            return False
        return True

    def _is_supported_legacy_schema(self, conn: sqlite3.Connection) -> bool:
        existing = {
            table for table in _META_V05_TABLES if self._table_exists(conn, table)
        }
        if existing != _META_V05_TABLES:
            return False
        migration = self._migration_version(conn)
        if migration == 2:
            return False
        try:
            self._validate_v05_schema(conn, require_migration=False, require_triggers=False)
        except RuntimeError:
            return False
        return True

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone() is not None

    @staticmethod
    def _migration_version(conn: sqlite3.Connection) -> int | None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        if "migration_id" not in columns or "schema_version" not in columns:
            return None
        row = conn.execute(
            "SELECT schema_version FROM schema_migrations WHERE migration_id = 'm0-v05-contracts'"
        ).fetchone()
        return None if row is None else row[0]

    @staticmethod
    def _validate_v05_schema(
        conn: sqlite3.Connection,
        *,
        require_migration: bool,
        require_triggers: bool = True,
    ) -> None:
        migration_info = list(conn.execute("PRAGMA table_info(schema_migrations)"))
        if {row[1] for row in migration_info} != {"migration_id", "applied_at", "schema_version"}:
            raise RuntimeError("MetaDB schema drift: schema_migrations 列结构不完整")
        migration_pk = tuple(
            row[1] for row in sorted(migration_info, key=lambda row: row[5]) if row[5]
        )
        if migration_pk != ("migration_id",):
            raise RuntimeError("MetaDB schema drift: schema_migrations 主键不匹配")
        for table, required in _META_COLUMNS.items():
            if not MetaDB._table_exists(conn, table):
                raise RuntimeError(f"MetaDB schema drift: 缺少表 {table}")
            info = list(conn.execute(f"PRAGMA table_info({table})"))
            if {row[1] for row in info} != required:
                raise RuntimeError(f"MetaDB schema drift: {table} 列结构不完整")
            primary_key = tuple(
                row[1] for row in sorted(info, key=lambda row: row[5]) if row[5]
            )
            if primary_key != _META_PRIMARY_KEYS[table]:
                raise RuntimeError(f"MetaDB schema drift: {table} 主键不匹配")
            if not _META_UNIQUE_KEYS[table].issubset(MetaDB._unique_indexes(conn, table)):
                raise RuntimeError(f"MetaDB schema drift: {table} UNIQUE 不完整")
            sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()[0].upper()
            if any(fragment not in "".join(sql.split()) for fragment in _META_CHECKS[table]):
                raise RuntimeError(f"MetaDB schema drift: {table} CHECK 不完整")
        if require_triggers:
            trigger_names = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE '_no_%'"
                )
            }
            if not _META_TRIGGERS.issubset(trigger_names):
                raise RuntimeError("MetaDB schema drift: insert-only trigger 不完整")
            canonical_triggers = _canonical_meta_triggers()
            for trigger_name in _META_TRIGGERS:
                row = conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                    (trigger_name,),
                ).fetchone()
                sql = "" if row is None or row[0] is None else _canonical_trigger_sql(row[0])
                if sql != canonical_triggers[trigger_name]:
                    raise RuntimeError(f"MetaDB schema drift: trigger {trigger_name} 定义不匹配")
        if require_migration and MetaDB._migration_version(conn) != 2:
            raise RuntimeError("MetaDB schema drift: migration version 与结构不一致")

    @staticmethod
    def _unique_indexes(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        for row in conn.execute(f"PRAGMA index_list({table})"):
            if row[2]:
                result.add(tuple(item[2] for item in conn.execute(f"PRAGMA index_info({row[1]})")))
        return result

    @staticmethod
    def _trigger_contract() -> dict[str, tuple[str, ...]]:
        tables = (
            "experiments", "experiment_events", "audit_log", "dataset_manifests",
            "instrument_rule_versions", "feature_definitions", "feature_materializations",
            "feature_evaluations", "trial_registrations", "budget_consumptions", "evidence_reports",
            "blind_challenge_results", "strategy_releases", "approval_records", "release_deployments",
            "strategy_health_assessments", "lifecycle_actions", "capital_allocation_policies",
            "v05_portfolio_risk_snapshots", "market_chaos_scenarios", "chaos_runs",
        )
        contract: dict[str, tuple[str, ...]] = {}
        for table in tables:
            update_fragments = [
                f"BEFOREUPDATEON{table.upper()}", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
            ]
            if table == "experiments":
                update_fragments.extend(
                    (
                        "WHEN",
                        "COALESCE(NEW.RUN_TYPE,'')<>COALESCE(OLD.RUN_TYPE,'')",
                        "COALESCE(NEW.STRATEGY_ID,'')<>COALESCE(OLD.STRATEGY_ID,'')",
                        "NEW.VERSION_NO<>OLD.VERSION_NO",
                        "COALESCE(NEW.PARAMS,'')<>COALESCE(OLD.PARAMS,'')",
                        "COALESCE(NEW.DATA_VERSION,'')<>COALESCE(OLD.DATA_VERSION,'')",
                        "COALESCE(NEW.FEATURE_VERSION,'')<>COALESCE(OLD.FEATURE_VERSION,'')",
                        "COALESCE(NEW.ENGINE_CONFIG,'')<>COALESCE(OLD.ENGINE_CONFIG,'')",
                        "COALESCE(NEW.ENVIRONMENT_LOCK,'')<>COALESCE(OLD.ENVIRONMENT_LOCK,'')",
                        "COALESCE(NEW.START_TS,'')<>COALESCE(OLD.START_TS,'')",
                        "COALESCE(NEW.END_TS,'')<>COALESCE(OLD.END_TS,'')",
                        "COALESCE(NEW.CREATED_AT,'')<>COALESCE(OLD.CREATED_AT,'')",
                    )
                )
            contract[f"_no_update_{table}"] = tuple(update_fragments)
            contract[f"_no_delete_{table}"] = (
                f"BEFOREDELETEON{table.upper()}", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
            )
        return contract


# ── v0.5 schema contract ─────────────────────────────────

_META_COLUMNS = {
    "dataset_manifests": {"manifest_id", "dataset_type", "schema_version", "partition_refs", "content_hash", "source_refs", "quality_report_ref", "created_at"},
    "instrument_rule_versions": {"rule_version_id", "instrument_id", "rules_hash", "valid_from", "valid_to", "available_at", "created_at", "tick_size", "lot_size", "min_notional", "max_leverage"},
    "feature_definitions": {"feature_id", "version", "semantic_name", "description", "formula_or_code_hash", "input_schema", "source_types", "window", "warmup", "frequency", "available_at_rule", "owner", "status", "created_at"},
    "feature_materializations": {"materialization_id", "feature_id", "feature_version", "input_manifest_ids", "output_manifest_id", "coverage", "quality_report_id", "computed_at", "content_hash"},
    "feature_evaluations": {"evaluation_id", "feature_id", "feature_version", "strategy_family_id", "experiment_id", "dataset_split", "evidence_metrics", "evaluated_at"},
    "trial_registrations": {"trial_id", "strategy_family_id", "hypothesis", "allowed_datasets", "parameter_space", "primary_metric", "rejection_criteria", "seed", "registered_at"},
    "research_budgets": {"budget_id", "scope", "scope_ref", "period", "max_trials", "max_compute", "max_blind_reveals", "similarity_policy_version", "approved_by", "valid_from", "valid_to"},
    "budget_consumptions": {"consumption_id", "budget_id", "actor_type", "trial_id", "candidate_fingerprint", "compute_cost", "blind_reveals", "consumed_at"},
    "evidence_reports": {"report_id", "evidence_quality", "expected_edge", "risk_estimate", "experiment_refs", "created_at"},
    "blind_challenge_results": {"challenge_id", "trial_id", "result_hash", "passed", "revealed_at", "budget_consumption_id"},
    "strategy_releases": {"release_id", "strategy_id", "strategy_version", "code_content_hash", "parameter_set_hash", "feature_set_ids", "dataset_manifest_ids", "cost_model_version", "environment_lock_hash", "evidence_report_id", "requested_capabilities", "allowed_instruments", "allowed_timeframes", "manifest_hash", "status", "created_at", "created_by", "approved_at", "approved_by"},
    "approval_records": {"approval_id", "release_id", "status", "approved_by", "reason", "created_at"},
    "release_deployments": {"deployment_id", "release_id", "account_id", "status", "created_at"},
    "strategy_health_assessments": {"assessment_id", "deployment_id", "status", "health_version", "metrics", "evidence_refs", "assessed_at", "valid_until", "research_disposition"},
    "lifecycle_actions": {"action_id", "deployment_id", "action", "reason", "requested_by", "created_at"},
    "capital_allocation_policies": {"policy_id", "account_id", "policy_version", "eligible_deployments", "capital_envelope_by_strategy", "risk_budget_by_strategy", "instrument_limits", "turnover_budget", "capacity_constraints", "portfolio_risk_snapshot_id", "valid_from", "expires_at", "approval_record_id"},
    "v05_portfolio_risk_snapshots": {"snapshot_id", "account_id", "position_and_policy_hash", "estimation_window", "model_version", "covariance_and_uncertainty", "factor_exposures", "var", "expected_shortfall", "scenario_losses", "liquidity_adjusted_loss", "observed_at", "expires_at"},
    "market_chaos_scenarios": {"scenario_id", "version", "market_path", "liquidity_path", "funding_and_margin_path", "broker_fault_schedule", "expected_invariants", "created_at"},
    "chaos_runs": {"chaos_run_id", "scenario_id", "release_or_policy_id", "system_version", "seed", "observed_actions", "invariant_results", "worst_loss", "freeze_latency_ms", "run_at"},
}
_META_V05_TABLES = set(_META_COLUMNS)
_META_PRIMARY_KEYS = {
    "dataset_manifests": ("manifest_id",), "instrument_rule_versions": ("rule_version_id",),
    "feature_definitions": ("feature_id", "version"), "feature_materializations": ("materialization_id",),
    "feature_evaluations": ("evaluation_id",), "trial_registrations": ("trial_id",),
    "research_budgets": ("budget_id",), "budget_consumptions": ("consumption_id",),
    "evidence_reports": ("report_id",), "blind_challenge_results": ("challenge_id",),
    "strategy_releases": ("release_id",), "approval_records": ("approval_id",),
    "release_deployments": ("deployment_id",), "strategy_health_assessments": ("assessment_id",),
    "lifecycle_actions": ("action_id",), "capital_allocation_policies": ("policy_id",),
    "v05_portfolio_risk_snapshots": ("snapshot_id",), "market_chaos_scenarios": ("scenario_id", "version"),
    "chaos_runs": ("chaos_run_id",),
}
_META_UNIQUE_KEYS: dict[str, set[tuple[str, ...]]] = {
    table: set() for table in _META_COLUMNS
}
_META_UNIQUE_KEYS["budget_consumptions"] = {("budget_id", "trial_id")}
_META_CHECKS: dict[str, tuple[str, ...]] = {
    table: () for table in _META_COLUMNS
}
_META_CHECKS["research_budgets"] = ("CHECK(MAX_TRIALS>0)", "CHECK(MAX_BLIND_REVEALS>=0)")
_META_CHECKS["budget_consumptions"] = ("CHECK(BLIND_REVEALS>=0)",)
_META_TRIGGERS = {
    f"_no_{operation}_{table}"
    for table in (
        "experiments", "experiment_events", "audit_log", "dataset_manifests",
        "instrument_rule_versions", "feature_definitions", "feature_materializations",
        "feature_evaluations", "trial_registrations", "budget_consumptions", "evidence_reports",
        "blind_challenge_results", "strategy_releases", "approval_records", "release_deployments",
        "strategy_health_assessments", "lifecycle_actions", "capital_allocation_policies",
        "v05_portfolio_risk_snapshots", "market_chaos_scenarios", "chaos_runs",
    )
    for operation in ("update", "delete")
}


# ── DDL ───────────────────────────────────────────────────

_DDL = """
-- 标的
CREATE TABLE IF NOT EXISTS instruments (
    instrument_id   TEXT PRIMARY KEY,
    internal_symbol TEXT NOT NULL UNIQUE,
    asset_class     TEXT NOT NULL CHECK (asset_class IN ('SPOT','PERPETUAL')),
    base_asset      TEXT NOT NULL,
    quote_asset     TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    contract_size   TEXT,
    tick_size       TEXT NOT NULL,
    lot_size        TEXT NOT NULL,
    min_notional    TEXT NOT NULL,
    maker_fee       TEXT NOT NULL,
    taker_fee       TEXT NOT NULL,
    funding_interval TEXT,
    margin_mode     TEXT,
    max_leverage    TEXT,
    status          TEXT NOT NULL DEFAULT 'ACTIVE'
);

CREATE TABLE IF NOT EXISTS symbol_mappings (
    instrument_id   TEXT NOT NULL,
    exchange        TEXT NOT NULL,
    exchange_symbol TEXT NOT NULL,
    is_primary      INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (instrument_id, exchange)
);

CREATE TABLE IF NOT EXISTS exchange_meta (
    exchange_id        TEXT PRIMARY KEY,
    rate_limits        TEXT,
    ws_endpoints       TEXT,
    rest_endpoints     TEXT,
    maintenance_schedule TEXT
);

-- 策略
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id       TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    style             TEXT NOT NULL CHECK (style IN ('TREND','MEAN_REVERSION','ARBITRAGE','GRID','AI','OTHER')),
    lifecycle_status  TEXT NOT NULL DEFAULT 'DRAFT' CHECK (lifecycle_status IN ('DRAFT','BACKTESTING','VALIDATED','PAPER_TRADING','PRODUCTION','PAUSED','DISABLED','DEPRECATED','ARCHIVED')),
    tags              TEXT,
    account_binding   TEXT,
    source            TEXT NOT NULL DEFAULT 'MANUAL',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_versions (
    strategy_id         TEXT NOT NULL,
    version_no          INTEGER NOT NULL,
    code_ref            TEXT NOT NULL,
    code_content        TEXT NOT NULL DEFAULT '',
    params_schema       TEXT NOT NULL,
    data_dependencies   TEXT NOT NULL,
    feature_dependencies TEXT NOT NULL,
    runtime_constraints TEXT NOT NULL,
    asset_class_support TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'MANUAL',
    created_at          TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version_no),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE TABLE IF NOT EXISTS strategy_params (
    strategy_id   TEXT NOT NULL,
    version_no    INTEGER NOT NULL,
    params        TEXT NOT NULL,
    params_hash   TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version_no, params_hash),
    FOREIGN KEY (strategy_id, version_no) REFERENCES strategy_versions(strategy_id, version_no)
);

CREATE TABLE IF NOT EXISTS strategy_allocations (
    allocation_id TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    strategy_id   TEXT NOT NULL,
    weight        TEXT NOT NULL,
    capital_limit TEXT NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'USDT',
    enabled       INTEGER NOT NULL DEFAULT 1
);

-- 实验
CREATE TABLE IF NOT EXISTS experiments (
    run_id            TEXT PRIMARY KEY,
    run_type          TEXT NOT NULL CHECK (run_type IN ('BACKTEST','OPTIMIZATION')),
    strategy_id       TEXT NOT NULL,
    version_no        INTEGER NOT NULL,
    params            TEXT NOT NULL,
    data_version      TEXT NOT NULL,
    feature_version   TEXT NOT NULL,
    engine_config     TEXT NOT NULL,
    environment_lock  TEXT,
    start_ts          TEXT,
    end_ts            TEXT,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_experiments_strategy ON experiments(strategy_id, version_no);

-- 实验事件（增量 evaluations 落盘，insert-only）
CREATE TABLE IF NOT EXISTS experiment_events (
    event_id    TEXT PRIMARY KEY,
    run_id      TEXT NOT NULL,
    event_type  TEXT NOT NULL CHECK (event_type IN ('EVALUATION','STATUS_CHANGE','BEST_UPDATE')),
    event_data  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES experiments(run_id)
);

CREATE INDEX IF NOT EXISTS idx_experiment_events_run ON experiment_events(run_id);

-- 报告卡
CREATE TABLE IF NOT EXISTS report_cards (
    run_id        TEXT PRIMARY KEY,
    strategy_id   TEXT NOT NULL,
    version_no    INTEGER NOT NULL,
    metrics       TEXT NOT NULL,
    gate_results  TEXT NOT NULL,
    alpha_score   TEXT,
    conclusion    TEXT
);

CREATE INDEX IF NOT EXISTS idx_report_cards_strategy ON report_cards(strategy_id, version_no);

-- 风控规则
CREATE TABLE IF NOT EXISTS risk_rules (
    rule_id      TEXT PRIMARY KEY,
    level        TEXT NOT NULL,
    rule_type    TEXT NOT NULL,
    params       TEXT NOT NULL,
    enabled      INTEGER NOT NULL DEFAULT 1,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

-- 风控事件
CREATE TABLE IF NOT EXISTS risk_events (
    event_id      TEXT PRIMARY KEY,
    event_type    TEXT NOT NULL,
    severity      TEXT NOT NULL,
    context       TEXT,
    triggered_at  TEXT NOT NULL,
    action_taken  TEXT
);

-- 审计日志
CREATE TABLE IF NOT EXISTS audit_log (
    log_id      TEXT PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    before      TEXT,
    after       TEXT,
    ts          TEXT NOT NULL,
    reason      TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_log_ts ON audit_log(ts);

-- 任务
CREATE TABLE IF NOT EXISTS tasks (
    task_id     TEXT PRIMARY KEY,
    task_type   TEXT NOT NULL,
    params      TEXT,
    status      TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    error       TEXT
);

-- AI 工件（M6）
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    schema_version TEXT NOT NULL DEFAULT '1',
    content       TEXT NOT NULL,
    strategy_id   TEXT,
    run_id        TEXT,
    agent_role    TEXT,
    created_at    TEXT NOT NULL
);

-- 部署
CREATE TABLE IF NOT EXISTS deployments (
    deployment_id TEXT PRIMARY KEY,
    strategy_id   TEXT NOT NULL,
    version_no    INTEGER NOT NULL,
    params        TEXT NOT NULL,
    account_id    TEXT NOT NULL,
    mode          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'CREATED'
);

-- 组合风险快照
CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
    account_id        TEXT NOT NULL,
    snapshot_ts       TEXT NOT NULL,
    total_notional    TEXT NOT NULL,
    exposure_by_symbol TEXT,
    exposure_by_strategy TEXT,
    net_exposure_by_symbol TEXT,
    concentration     TEXT,
    combined_leverage  TEXT,
    PRIMARY KEY (account_id, snapshot_ts)
);
"""


_V05_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS dataset_manifests (
    manifest_id TEXT PRIMARY KEY,
    dataset_type TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    partition_refs TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_refs TEXT NOT NULL,
    quality_report_ref TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS instrument_rule_versions (
    rule_version_id TEXT PRIMARY KEY,
    instrument_id TEXT NOT NULL,
    rules_hash TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    available_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    tick_size TEXT NOT NULL,
    lot_size TEXT NOT NULL,
    min_notional TEXT NOT NULL,
    max_leverage TEXT
);

CREATE TABLE IF NOT EXISTS feature_definitions (
    feature_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    semantic_name TEXT NOT NULL,
    description TEXT NOT NULL,
    formula_or_code_hash TEXT NOT NULL,
    input_schema TEXT NOT NULL,
    source_types TEXT NOT NULL,
    window INTEGER NOT NULL CHECK (window > 0),
    warmup INTEGER NOT NULL CHECK (warmup >= 0),
    frequency TEXT NOT NULL,
    available_at_rule TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (feature_id, version)
);

CREATE TABLE IF NOT EXISTS feature_materializations (
    materialization_id TEXT PRIMARY KEY,
    feature_id TEXT NOT NULL,
    feature_version INTEGER NOT NULL,
    input_manifest_ids TEXT NOT NULL,
    output_manifest_id TEXT NOT NULL,
    coverage TEXT NOT NULL,
    quality_report_id TEXT,
    computed_at TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feature_evaluations (
    evaluation_id TEXT PRIMARY KEY,
    feature_id TEXT NOT NULL,
    feature_version INTEGER NOT NULL,
    strategy_family_id TEXT NOT NULL,
    experiment_id TEXT NOT NULL,
    dataset_split TEXT NOT NULL,
    evidence_metrics TEXT NOT NULL,
    evaluated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trial_registrations (
    trial_id TEXT PRIMARY KEY,
    strategy_family_id TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    allowed_datasets TEXT NOT NULL,
    parameter_space TEXT NOT NULL,
    primary_metric TEXT NOT NULL,
    rejection_criteria TEXT NOT NULL,
    seed INTEGER NOT NULL,
    registered_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_budgets (
    budget_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_ref TEXT NOT NULL,
    period TEXT NOT NULL,
    max_trials INTEGER NOT NULL CHECK (max_trials > 0),
    max_compute TEXT NOT NULL,
    max_blind_reveals INTEGER NOT NULL CHECK (max_blind_reveals >= 0),
    similarity_policy_version TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS budget_consumptions (
    consumption_id TEXT PRIMARY KEY,
    budget_id TEXT NOT NULL,
    actor_type TEXT NOT NULL,
    trial_id TEXT NOT NULL,
    candidate_fingerprint TEXT NOT NULL,
    compute_cost TEXT NOT NULL,
    blind_reveals INTEGER NOT NULL CHECK (blind_reveals >= 0),
    consumed_at TEXT NOT NULL,
    UNIQUE (budget_id, trial_id)
);

CREATE TABLE IF NOT EXISTS evidence_reports (
    report_id TEXT PRIMARY KEY,
    evidence_quality TEXT NOT NULL,
    expected_edge TEXT NOT NULL,
    risk_estimate TEXT NOT NULL,
    experiment_refs TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blind_challenge_results (
    challenge_id TEXT PRIMARY KEY,
    trial_id TEXT NOT NULL,
    result_hash TEXT NOT NULL,
    passed INTEGER NOT NULL,
    revealed_at TEXT NOT NULL,
    budget_consumption_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_releases (
    release_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    strategy_version INTEGER NOT NULL,
    code_content_hash TEXT NOT NULL,
    parameter_set_hash TEXT NOT NULL,
    feature_set_ids TEXT NOT NULL,
    dataset_manifest_ids TEXT NOT NULL,
    cost_model_version TEXT NOT NULL,
    environment_lock_hash TEXT NOT NULL,
    evidence_report_id TEXT NOT NULL,
    requested_capabilities TEXT NOT NULL,
    allowed_instruments TEXT NOT NULL,
    allowed_timeframes TEXT NOT NULL,
    manifest_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    approved_at TEXT,
    approved_by TEXT
);

CREATE TABLE IF NOT EXISTS approval_records (
    approval_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL,
    status TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS release_deployments (
    deployment_id TEXT PRIMARY KEY,
    release_id TEXT NOT NULL,
    account_id TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS strategy_health_assessments (
    assessment_id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    status TEXT NOT NULL,
    health_version INTEGER NOT NULL,
    metrics TEXT NOT NULL,
    evidence_refs TEXT NOT NULL,
    assessed_at TEXT NOT NULL,
    valid_until TEXT NOT NULL,
    research_disposition TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lifecycle_actions (
    action_id TEXT PRIMARY KEY,
    deployment_id TEXT NOT NULL,
    action TEXT NOT NULL,
    reason TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS capital_allocation_policies (
    policy_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    policy_version INTEGER NOT NULL,
    eligible_deployments TEXT NOT NULL,
    capital_envelope_by_strategy TEXT NOT NULL,
    risk_budget_by_strategy TEXT NOT NULL,
    instrument_limits TEXT NOT NULL,
    turnover_budget TEXT NOT NULL,
    capacity_constraints TEXT NOT NULL,
    portfolio_risk_snapshot_id TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    approval_record_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS v05_portfolio_risk_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    position_and_policy_hash TEXT NOT NULL,
    estimation_window TEXT NOT NULL,
    model_version TEXT NOT NULL,
    covariance_and_uncertainty TEXT NOT NULL,
    factor_exposures TEXT NOT NULL,
    var TEXT NOT NULL,
    expected_shortfall TEXT NOT NULL,
    scenario_losses TEXT NOT NULL,
    liquidity_adjusted_loss TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_chaos_scenarios (
    scenario_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    market_path TEXT NOT NULL,
    liquidity_path TEXT NOT NULL,
    funding_and_margin_path TEXT NOT NULL,
    broker_fault_schedule TEXT NOT NULL,
    expected_invariants TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (scenario_id, version)
);

CREATE TABLE IF NOT EXISTS chaos_runs (
    chaos_run_id TEXT PRIMARY KEY,
    scenario_id TEXT NOT NULL,
    release_or_policy_id TEXT NOT NULL,
    system_version TEXT NOT NULL,
    seed INTEGER NOT NULL,
    observed_actions TEXT NOT NULL,
    invariant_results TEXT NOT NULL,
    worst_loss TEXT NOT NULL,
    freeze_latency_ms INTEGER NOT NULL,
    run_at TEXT NOT NULL
);
"""
