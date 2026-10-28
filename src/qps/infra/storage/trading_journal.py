"""SQLite journal primitives for the v0.5 durable trading kernel.

The journal owns immutable facts and a small number of explicitly documented
projections.  It does not implement broker dispatch or reconciliation policy.
"""

from __future__ import annotations

import re
import sqlite3
from contextlib import contextmanager, nullcontext
from functools import lru_cache
from typing import TYPE_CHECKING

from qps.core.constants import OrderStatus, validate_order_transition
from qps.core.models.validators import validate_utc_datetime

if TYPE_CHECKING:
    from collections.abc import Generator
    from datetime import datetime
    from pathlib import Path


class SchemaMigrationError(RuntimeError):
    """Raised when an existing database is neither supported nor migratable."""


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
def _canonical_journal_triggers() -> dict[str, str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(_DDL)
        JournalDB.__new__(JournalDB).setup_append_only_constraints(conn)
        return {
            name: _canonical_trigger_sql(sql)
            for name, sql in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name LIKE '_journal_%'"
            )
        }
    finally:
        conn.close()


class JournalDB:
    """WAL-backed journal database with explicit, repeatable migrations."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise

    def initialize(self) -> None:
        state = "fresh"
        with self.connect() as conn:
            snapshot = sqlite3.connect(":memory:")
            try:
                conn.backup(snapshot)
                state = self._schema_state(conn)
                if state == "legacy":
                    self._upgrade_legacy_schema(conn)
                elif state == "fresh":
                    conn.executescript(_DDL)
                self._ensure_migration_version_column(conn)
                if state in {"fresh", "legacy"}:
                    self._install_strategy_state_schema(conn)
                    self.setup_append_only_constraints(conn)
                    self._validate_schema_contract(conn, require_migration=False)
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_migrations "
                        "(migration_id, applied_at, schema_version) "
                        "VALUES ('m0-v05-journal', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), 2)"
                    )
                    conn.execute(
                        "UPDATE schema_migrations SET schema_version = 2 "
                        "WHERE migration_id = 'm0-v05-journal'"
                    )
                else:
                    self._install_strategy_state_schema(conn)
                    self._validate_schema_contract(conn, require_migration=True)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                snapshot.backup(conn)
                conn.commit()
                raise SchemaMigrationError(
                    f"JournalDB initialization failed; original schema restored: {exc}"
                ) from exc
            finally:
                snapshot.close()

    def table_exists(self, table_name: str) -> bool:
        with self.connect() as conn:
            return self._table_exists(conn, table_name)

    def ensure_strategy_state_schema(self) -> None:
        """Install the M2 state journal as a JournalDB-owned migration."""
        with self.transaction() as conn:
            self._install_strategy_state_schema(conn)

    @staticmethod
    def _install_strategy_state_schema(conn: sqlite3.Connection) -> None:
        conn.executescript("""
                CREATE TABLE IF NOT EXISTS state_attempts (
                    attempt_id TEXT PRIMARY KEY, deployment_id TEXT NOT NULL,
                    release_id TEXT NOT NULL, schema_version INTEGER NOT NULL,
                    input_snapshot_ref TEXT NOT NULL, input_hash TEXT NOT NULL,
                    previous_state_hash TEXT NOT NULL, state_hash TEXT NOT NULL,
                    intent_hash TEXT NOT NULL, event_id TEXT NOT NULL,
                    state_json TEXT NOT NULL, intent_json TEXT,
                    decision_time TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS state_commits (
                    deployment_id TEXT NOT NULL, input_snapshot_ref TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE, state_version INTEGER NOT NULL,
                    committed_at TEXT NOT NULL,
                    PRIMARY KEY (deployment_id, input_snapshot_ref),
                    FOREIGN KEY (attempt_id) REFERENCES state_attempts(attempt_id)
                );
                CREATE INDEX IF NOT EXISTS idx_state_commits_latest
                    ON state_commits(deployment_id, state_version);
                CREATE TRIGGER IF NOT EXISTS _journal_no_update_state_attempts
                    BEFORE UPDATE ON state_attempts FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'state_attempts append-only'); END;
                CREATE TRIGGER IF NOT EXISTS _journal_no_delete_state_attempts
                    BEFORE DELETE ON state_attempts FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'state_attempts append-only'); END;
                CREATE TRIGGER IF NOT EXISTS _journal_no_update_state_commits
                    BEFORE UPDATE ON state_commits FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'state_commits append-only'); END;
                CREATE TRIGGER IF NOT EXISTS _journal_no_delete_state_commits
                    BEFORE DELETE ON state_commits FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'state_commits append-only'); END;
            """)
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (migration_id, applied_at, schema_version) "
            "VALUES ('m2-v05-state', strftime('%Y-%m-%dT%H:%M:%fZ','now'), 1)"
        )

    def compare_and_set_order_status(
        self,
        command_id: str,
        *,
        expected_version: int,
        from_status: OrderStatus,
        target_status: OrderStatus,
        updated_at: datetime,
    ) -> bool:
        """Atomically update a command only when its version and status match."""
        validate_order_transition(from_status, target_status)
        timestamp = validate_utc_datetime(updated_at).isoformat()
        with self.transaction() as conn:
            cursor = conn.execute(
                "UPDATE order_commands SET status = ?, state_version = state_version + 1, "
                "updated_at = ? WHERE command_id = ? AND status = ? AND state_version = ? "
                "AND updated_at <= ?",
                (
                    target_status.value,
                    timestamp,
                    command_id,
                    from_status.value,
                    expected_version,
                    timestamp,
                ),
            )
            return cursor.rowcount == 1

    def setup_append_only_constraints(self, connection: sqlite3.Connection | None = None) -> None:
        with (self.connect() if connection is None else nullcontext(connection)) as conn:
            trigger_names = (
                "_journal_no_update_order_commands",
                "_journal_no_delete_order_commands",
                "_journal_order_command_cas",
                "_journal_order_command_delete",
                "_journal_reservation_projection",
                "_journal_reservation_delete",
                "_journal_outbox_projection",
                "_journal_outbox_delete",
            )
            for trigger in trigger_names:
                conn.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            for table in ("inbox", "order_state_events"):
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_update_{table}")
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_delete_{table}")
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_update_{table}
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW
                    BEGIN
                        SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 UPDATE: {table}');
                    END
                """)
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_delete_{table}
                    BEFORE DELETE ON {table}
                    FOR EACH ROW
                    BEGIN
                        SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 DELETE: {table}');
                    END
                """)
            for table in (
                "execution_reports",
                "fills",
                "account_ledger_entries",
                "strategy_ledger_entries",
                "reconciliation_runs",
            ):
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_update_{table}")
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_delete_{table}")
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_update_{table}
                    BEFORE UPDATE ON {table}
                    FOR EACH ROW
                    BEGIN
                        SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 UPDATE: {table}');
                    END
                """)
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_delete_{table}
                    BEFORE DELETE ON {table}
                    FOR EACH ROW
                    BEGIN
                        SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 DELETE: {table}');
                    END
                """)
            for table in ("state_attempts", "state_commits"):
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_update_{table}")
                conn.execute(f"DROP TRIGGER IF EXISTS _journal_no_delete_{table}")
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_update_{table}
                    BEFORE UPDATE ON {table} FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'INSERT-ONLY 状态表禁止 UPDATE: {table}'); END
                """)
                conn.execute(f"""
                    CREATE TRIGGER _journal_no_delete_{table}
                    BEFORE DELETE ON {table} FOR EACH ROW
                    BEGIN SELECT RAISE(ABORT, 'INSERT-ONLY 状态表禁止 DELETE: {table}'); END
                """)
            conn.execute("""
                CREATE TRIGGER _journal_no_delete_order_commands
                BEFORE DELETE ON order_commands
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, '订单命令历史禁止 DELETE');
                END
            """)
            conn.execute("""
                CREATE TRIGGER _journal_order_command_cas
                BEFORE UPDATE ON order_commands
                FOR EACH ROW
                WHEN NEW.command_id <> OLD.command_id
                  OR NEW.client_order_id <> OLD.client_order_id
                  OR NEW.account_id <> OLD.account_id
                  OR NEW.reservation_id <> OLD.reservation_id
                  OR NEW.allocation_plan_id <> OLD.allocation_plan_id
                  OR NEW.instrument_id <> OLD.instrument_id
                  OR NEW.side <> OLD.side
                  OR NEW.quantity <> OLD.quantity
                  OR NEW.order_type <> OLD.order_type
                  OR COALESCE(NEW.limit_price, '') <> COALESCE(OLD.limit_price, '')
                  OR NEW.state_version <> OLD.state_version + 1
                  OR NEW.updated_at < OLD.updated_at
                  OR NOT (
                    (OLD.status = 'CREATED' AND NEW.status IN ('RISK_RESERVED','REJECTED','EXPIRED'))
                    OR (OLD.status = 'RISK_RESERVED' AND NEW.status IN ('SUBMITTING','REJECTED','EXPIRED'))
                    OR (OLD.status = 'SUBMITTING' AND NEW.status IN ('PENDING_ACK','UNKNOWN','REJECTED'))
                    OR (OLD.status = 'PENDING_ACK' AND NEW.status IN ('WORKING','REJECTED','CANCELED'))
                    OR (OLD.status = 'WORKING' AND NEW.status IN ('PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELED','EXPIRED'))
                    OR (OLD.status = 'PARTIALLY_FILLED' AND NEW.status IN ('WORKING','PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELED','EXPIRED'))
                    OR (OLD.status = 'CANCEL_PENDING' AND NEW.status IN ('CANCELED','UNKNOWN'))
                    OR (OLD.status = 'UNKNOWN' AND NEW.status = 'RECONCILING')
                    OR (OLD.status = 'RECONCILING' AND NEW.status IN ('WORKING','PARTIALLY_FILLED','FILLED','REJECTED','CANCELED'))
                  )
                BEGIN
                    SELECT RAISE(ABORT, '订单状态必须使用合法单调 state_version CAS');
                END
            """)
            conn.execute("""
                CREATE TRIGGER _journal_reservation_projection
                BEFORE UPDATE ON risk_reservations
                FOR EACH ROW
                WHEN NEW.reservation_id <> OLD.reservation_id
                  OR NEW.account_id <> OLD.account_id
                  OR NEW.intent_ids <> OLD.intent_ids
                  OR NEW.instrument_id <> OLD.instrument_id
                  OR NEW.side <> OLD.side
                  OR NEW.reserved_quantity <> OLD.reserved_quantity
                  OR NEW.reserved_notional <> OLD.reserved_notional
                  OR NEW.reserved_margin <> OLD.reserved_margin
                  OR NEW.risk_snapshot_version <> OLD.risk_snapshot_version
                  OR NEW.expires_at <> OLD.expires_at
                  OR NEW.created_at <> OLD.created_at
                BEGIN
                    SELECT RAISE(ABORT, '预留只允许状态投影更新');
                END
            """)
            conn.execute("""
                CREATE TRIGGER _journal_reservation_delete
                BEFORE DELETE ON risk_reservations
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'INSERT-ONLY 预留历史禁止 DELETE');
                END
            """)
            conn.execute("""
                CREATE TRIGGER _journal_outbox_projection
                BEFORE UPDATE ON outbox
                FOR EACH ROW
                WHEN NEW.outbox_id <> OLD.outbox_id
                  OR NEW.aggregate_id <> OLD.aggregate_id
                  OR NEW.event_type <> OLD.event_type
                  OR NEW.payload <> OLD.payload
                  OR NEW.created_at <> OLD.created_at
                BEGIN
                    SELECT RAISE(ABORT, 'outbox 只允许发布投影更新');
                END
            """)
            conn.execute("""
                CREATE TRIGGER _journal_outbox_delete
                BEFORE DELETE ON outbox
                FOR EACH ROW
                BEGIN
                    SELECT RAISE(ABORT, 'outbox 历史禁止 DELETE');
                END
            """)
            if connection is None:
                conn.commit()

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table_name,)
        ).fetchone() is not None

    def _schema_state(self, conn: sqlite3.Connection) -> str:
        existing = {
            table for table in _JOURNAL_TABLES if self._table_exists(conn, table)
        }
        if not existing:
            return "fresh"
        if self._looks_like_current_schema(conn):
            return "current"
        if self._is_supported_legacy_schema(conn):
            return "legacy"
        raise SchemaMigrationError("JournalDB schema drift: 既有 schema 不是完整当前版或受支持旧版")

    @staticmethod
    def _looks_like_current_schema(conn: sqlite3.Connection) -> bool:
        if JournalDB._table_exists(conn, "journal_accounts") or JournalDB._table_exists(
            conn, "allocation_plans"
        ):
            return True
        if JournalDB._table_exists(conn, "order_commands"):
            columns = {row[1] for row in conn.execute("PRAGMA table_info(order_commands)")}
            if "updated_at" in columns:
                return True
        return False

    def _is_current_schema(self, conn: sqlite3.Connection) -> bool:
        try:
            self._validate_schema_contract(conn, require_migration=True)
        except SchemaMigrationError:
            return False
        return True

    @staticmethod
    def _is_supported_legacy_schema(conn: sqlite3.Connection) -> bool:
        if not JournalDB._table_exists(conn, "order_commands"):
            return False
        if JournalDB._table_exists(conn, "journal_accounts") or JournalDB._table_exists(
            conn, "allocation_plans"
        ):
            return False
        columns = {row[1] for row in conn.execute("PRAGMA table_info(order_commands)")}
        required = {
            "command_id", "client_order_id", "account_id", "reservation_id",
            "allocation_plan_id", "instrument_id", "side", "quantity", "order_type",
            "limit_price", "status", "state_version", "created_at",
        }
        return required.issubset(columns) and "updated_at" not in columns and not conn.execute(
            "PRAGMA foreign_key_list(order_commands)"
        ).fetchall()

    def _upgrade_legacy_schema(self, conn: sqlite3.Connection) -> None:
        tables = (
            "inbox", "outbox", "order_state_events", "order_commands", "risk_reservations",
            "execution_reports", "fills", "account_ledger_entries", "strategy_ledger_entries",
            "reconciliation_runs",
        )
        existing = [table for table in tables if self._table_exists(conn, table)]
        for table in existing:
            conn.execute(f"ALTER TABLE {table} RENAME TO _legacy_{table}")
        for index in (
            "idx_outbox_pending", "idx_reports_order", "idx_fills_order",
        ):
            conn.execute(f"DROP INDEX IF EXISTS {index}")
        conn.executescript(_DDL)
        conn.execute("INSERT OR IGNORE INTO journal_accounts (account_id) "
                     "SELECT DISTINCT account_id FROM _legacy_risk_reservations") if "risk_reservations" in existing else None
        for table in ("order_commands", "fills", "account_ledger_entries", "strategy_ledger_entries", "reconciliation_runs"):
            if table in existing:
                conn.execute(f"INSERT OR IGNORE INTO journal_accounts (account_id) "
                             f"SELECT DISTINCT account_id FROM _legacy_{table}")
        if "order_commands" in existing:
            conn.execute("INSERT OR IGNORE INTO allocation_plans "
                         "(allocation_plan_id, account_id, instrument_id, input_intents, created_at) "
                         "SELECT allocation_plan_id, account_id, instrument_id, '[]', created_at "
                         "FROM _legacy_order_commands")
        copy_sql = {
            "inbox": "event_id, event_type, received_at, payload",
            "outbox": "outbox_id, aggregate_id, event_type, payload, created_at, published_at",
            "risk_reservations": "reservation_id, account_id, intent_ids, instrument_id, side, reserved_quantity, reserved_notional, reserved_margin, risk_snapshot_version, status, expires_at, created_at",
            "execution_reports": "report_id, client_order_id, broker_order_id, status, event_sequence, raw_payload, received_at",
            "fills": "fill_id, venue, account_id, client_order_id, trade_id, instrument_id, side, quantity, price, fee, fee_currency, trade_time",
            "account_ledger_entries": "ledger_entry_id, account_id, asset, amount, entry_type, source_type, source_id, idempotency_key, occurred_at",
            "strategy_ledger_entries": "ledger_entry_id, account_id, strategy_id, asset, amount, entry_type, source_type, source_id, idempotency_key, occurred_at",
            "reconciliation_runs": "reconciliation_id, account_id, status, local_version, exchange_snapshot_ref, difference_refs, residual_reason_codes, started_at, completed_at",
        }
        for table, columns in copy_sql.items():
            if table in existing:
                conn.execute(f"INSERT INTO {table} ({columns}) SELECT {columns} FROM _legacy_{table}")
        if "order_commands" in existing:
            conn.execute("INSERT INTO order_commands "
                         "(command_id, client_order_id, account_id, reservation_id, allocation_plan_id, "
                         "instrument_id, side, quantity, order_type, limit_price, status, state_version, created_at, updated_at) "
                         "SELECT command_id, client_order_id, account_id, reservation_id, allocation_plan_id, "
                         "instrument_id, side, quantity, order_type, limit_price, "
                         "CASE status WHEN 'NEW' THEN 'CREATED' WHEN 'PENDING_SUBMIT' THEN 'SUBMITTING' ELSE status END, "
                         "state_version, created_at, created_at "
                         "FROM _legacy_order_commands")
        if "order_state_events" in existing:
            conn.execute("INSERT INTO order_state_events "
                         "(event_id, command_id, from_status, to_status, state_version, observed_at, payload) "
                         "SELECT event_id, command_id, "
                         "CASE from_status WHEN 'NEW' THEN 'CREATED' WHEN 'PENDING_SUBMIT' THEN 'SUBMITTING' ELSE from_status END, "
                         "CASE to_status WHEN 'NEW' THEN 'CREATED' WHEN 'PENDING_SUBMIT' THEN 'SUBMITTING' ELSE to_status END, "
                         "state_version, observed_at, payload FROM _legacy_order_state_events")
        self._ensure_migration_version_column(conn)

    @staticmethod
    def _ensure_migration_version_column(conn: sqlite3.Connection) -> None:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(schema_migrations)")}
        if "schema_version" not in columns:
            conn.execute("ALTER TABLE schema_migrations ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")

    @staticmethod
    def _validate_schema_contract(
        conn: sqlite3.Connection, *, require_migration: bool
    ) -> None:
        for table, required in _JOURNAL_COLUMNS.items():
            if not JournalDB._table_exists(conn, table):
                raise SchemaMigrationError(f"JournalDB schema drift: 缺少表 {table}")
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if columns != required:
                raise SchemaMigrationError(f"JournalDB schema drift: {table} 列结构不完整")
            primary_key = tuple(
                row[1]
                for row in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda row: row[5])
                if row[5]
            )
            if primary_key != _JOURNAL_PRIMARY_KEYS[table]:
                raise SchemaMigrationError(f"JournalDB schema drift: {table} 主键不匹配")
            unique_indexes = JournalDB._unique_indexes(conn, table)
            if not _JOURNAL_UNIQUE_KEYS[table].issubset(unique_indexes):
                raise SchemaMigrationError(f"JournalDB schema drift: {table} UNIQUE 不完整")
            foreign_keys = {
                (row[3], row[4], row[2], row[6])
                for row in conn.execute(f"PRAGMA foreign_key_list({table})")
            }
            if foreign_keys != _JOURNAL_FOREIGN_KEYS[table]:
                raise SchemaMigrationError(f"JournalDB schema drift: {table} FOREIGN KEY 不匹配")
            sql = re.sub(r"\s+", "", conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
            ).fetchone()[0].upper())
            for fragment in _JOURNAL_CHECKS[table]:
                if fragment not in sql:
                    raise SchemaMigrationError(f"JournalDB schema drift: {table} CHECK 不完整")
        for index_name, (table, index_columns) in _JOURNAL_INDEXES.items():
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
            ).fetchone()
            if row is None or JournalDB._index_columns(conn, index_name) != (table, index_columns):
                raise SchemaMigrationError(f"JournalDB schema drift: 索引 {index_name} 不匹配")
        trigger_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE '_journal_%'"
            )
        }
        if not _JOURNAL_TRIGGERS.issubset(trigger_names):
            raise SchemaMigrationError("JournalDB schema drift: append-only/projection trigger 不完整")
        canonical_triggers = _canonical_journal_triggers()
        for trigger_name in _JOURNAL_TRIGGERS:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?",
                (trigger_name,),
            ).fetchone()
            sql = "" if row is None or row[0] is None else _canonical_trigger_sql(row[0])
            if sql != canonical_triggers[trigger_name]:
                raise SchemaMigrationError(
                    f"JournalDB schema drift: trigger {trigger_name} 定义不匹配"
                )
        migration = conn.execute(
            "SELECT schema_version FROM schema_migrations WHERE migration_id = 'm0-v05-journal'"
        ).fetchone()
        if require_migration and (migration is None or migration[0] != 2):
            raise SchemaMigrationError("JournalDB schema drift: migration version 与结构不一致")
        state_migration = conn.execute(
            "SELECT schema_version FROM schema_migrations WHERE migration_id = 'm2-v05-state'"
        ).fetchone()
        if state_migration is None or state_migration[0] != 1:
            raise SchemaMigrationError("JournalDB schema drift: M2 state migration 缺失")

    @staticmethod
    def _unique_indexes(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
        result: set[tuple[str, ...]] = set()
        for row in conn.execute(f"PRAGMA index_list({table})"):
            if row[2]:
                columns = tuple(item[2] for item in conn.execute(f"PRAGMA index_info({row[1]})"))
                result.add(columns)
        return result

    @staticmethod
    def _trigger_contract() -> dict[str, tuple[str, ...]]:
        contract: dict[str, tuple[str, ...]] = {}
        for table in ("inbox", "order_state_events", "execution_reports", "fills", "account_ledger_entries", "strategy_ledger_entries", "reconciliation_runs"):
            contract[f"_journal_no_update_{table}"] = (
                f"BEFOREUPDATEON{table.upper()}", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
            )
            contract[f"_journal_no_delete_{table}"] = (
                f"BEFOREDELETEON{table.upper()}", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
            )
        contract["_journal_no_delete_order_commands"] = (
            "BEFOREDELETEONORDER_COMMANDS", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
        )
        contract["_journal_order_command_cas"] = (
            "BEFOREUPDATEONORDER_COMMANDS", "FOREACHROW", "WHEN",
            "NEW.COMMAND_ID<>OLD.COMMAND_ID", "NEW.CLIENT_ORDER_ID<>OLD.CLIENT_ORDER_ID",
            "NEW.ACCOUNT_ID<>OLD.ACCOUNT_ID", "NEW.RESERVATION_ID<>OLD.RESERVATION_ID",
            "NEW.ALLOCATION_PLAN_ID<>OLD.ALLOCATION_PLAN_ID", "NEW.INSTRUMENT_ID<>OLD.INSTRUMENT_ID",
            "NEW.SIDE<>OLD.SIDE", "NEW.QUANTITY<>OLD.QUANTITY", "NEW.ORDER_TYPE<>OLD.ORDER_TYPE",
            "COALESCE(NEW.LIMIT_PRICE,'')<>COALESCE(OLD.LIMIT_PRICE,'')",
            "NEW.STATE_VERSION<>OLD.STATE_VERSION+1", "NEW.UPDATED_AT<OLD.UPDATED_AT",
            "OLD.STATUS='CREATED'ANDNEW.STATUSIN('RISK_RESERVED','REJECTED','EXPIRED')",
            "OLD.STATUS='RISK_RESERVED'ANDNEW.STATUSIN('SUBMITTING','REJECTED','EXPIRED')",
            "OLD.STATUS='SUBMITTING'ANDNEW.STATUSIN('PENDING_ACK','UNKNOWN','REJECTED')",
            "OLD.STATUS='PENDING_ACK'ANDNEW.STATUSIN('WORKING','REJECTED','CANCELED')",
            "OLD.STATUS='WORKING'ANDNEW.STATUSIN('PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELED','EXPIRED')",
            "OLD.STATUS='PARTIALLY_FILLED'ANDNEW.STATUSIN('WORKING','PARTIALLY_FILLED','FILLED','CANCEL_PENDING','CANCELED','EXPIRED')",
            "OLD.STATUS='CANCEL_PENDING'ANDNEW.STATUSIN('CANCELED','UNKNOWN')",
            "OLD.STATUS='UNKNOWN'ANDNEW.STATUS='RECONCILING'",
            "OLD.STATUS='RECONCILING'ANDNEW.STATUSIN('WORKING','PARTIALLY_FILLED','FILLED','REJECTED','CANCELED')",
            "BEGINSELECTRAISE(ABORT,",
        )
        contract["_journal_reservation_projection"] = (
            "BEFOREUPDATEONRISK_RESERVATIONS", "FOREACHROW", "WHEN",
            "NEW.RESERVATION_ID<>OLD.RESERVATION_ID", "NEW.ACCOUNT_ID<>OLD.ACCOUNT_ID",
            "NEW.INTENT_IDS<>OLD.INTENT_IDS", "NEW.INSTRUMENT_ID<>OLD.INSTRUMENT_ID",
            "NEW.SIDE<>OLD.SIDE", "NEW.RESERVED_QUANTITY<>OLD.RESERVED_QUANTITY",
            "NEW.RESERVED_NOTIONAL<>OLD.RESERVED_NOTIONAL", "NEW.RESERVED_MARGIN<>OLD.RESERVED_MARGIN",
            "NEW.RISK_SNAPSHOT_VERSION<>OLD.RISK_SNAPSHOT_VERSION", "NEW.EXPIRES_AT<>OLD.EXPIRES_AT",
            "NEW.CREATED_AT<>OLD.CREATED_AT", "BEGINSELECTRAISE(ABORT,",
        )
        contract["_journal_reservation_delete"] = (
            "BEFOREDELETEONRISK_RESERVATIONS", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
        )
        contract["_journal_outbox_projection"] = (
            "BEFOREUPDATEONOUTBOX", "FOREACHROW", "WHEN", "NEW.OUTBOX_ID<>OLD.OUTBOX_ID",
            "NEW.AGGREGATE_ID<>OLD.AGGREGATE_ID", "NEW.EVENT_TYPE<>OLD.EVENT_TYPE",
            "NEW.PAYLOAD<>OLD.PAYLOAD", "NEW.CREATED_AT<>OLD.CREATED_AT", "BEGINSELECTRAISE(ABORT,",
        )
        contract["_journal_outbox_delete"] = (
            "BEFOREDELETEONOUTBOX", "FOREACHROW", "BEGINSELECTRAISE(ABORT,"
        )
        return contract

    @staticmethod
    def _index_columns(conn: sqlite3.Connection, index_name: str) -> tuple[str, tuple[str, ...]]:
        table = conn.execute(
            "SELECT tbl_name FROM sqlite_master WHERE type = 'index' AND name = ?", (index_name,)
        ).fetchone()[0]
        columns = tuple(item[2] for item in conn.execute(f"PRAGMA index_info({index_name})"))
        return table, columns


_JOURNAL_TABLES = {
    "schema_migrations", "journal_accounts", "allocation_plans", "inbox", "outbox",
    "order_state_events", "risk_reservations", "order_commands", "execution_reports",
    "fills", "account_ledger_entries", "strategy_ledger_entries", "reconciliation_runs",
    "state_attempts", "state_commits",
}
_JOURNAL_COLUMNS = {
    "schema_migrations": {"migration_id", "applied_at", "schema_version"},
    "journal_accounts": {"account_id"},
    "allocation_plans": {"allocation_plan_id", "account_id", "instrument_id", "input_intents", "created_at"},
    "inbox": {"event_id", "event_type", "received_at", "payload"},
    "outbox": {"outbox_id", "aggregate_id", "event_type", "payload", "created_at", "published_at"},
    "order_state_events": {"event_id", "command_id", "from_status", "to_status", "state_version", "observed_at", "payload"},
    "risk_reservations": {"reservation_id", "account_id", "intent_ids", "instrument_id", "side", "reserved_quantity", "reserved_notional", "reserved_margin", "risk_snapshot_version", "status", "expires_at", "created_at"},
    "order_commands": {"command_id", "client_order_id", "account_id", "reservation_id", "allocation_plan_id", "instrument_id", "side", "quantity", "order_type", "limit_price", "status", "state_version", "created_at", "updated_at"},
    "execution_reports": {"report_id", "client_order_id", "broker_order_id", "status", "event_sequence", "raw_payload", "received_at"},
    "fills": {"fill_id", "venue", "account_id", "client_order_id", "trade_id", "instrument_id", "side", "quantity", "price", "fee", "fee_currency", "trade_time"},
    "account_ledger_entries": {"ledger_entry_id", "account_id", "asset", "amount", "entry_type", "source_type", "source_id", "idempotency_key", "occurred_at"},
    "strategy_ledger_entries": {"ledger_entry_id", "account_id", "strategy_id", "asset", "amount", "entry_type", "source_type", "source_id", "idempotency_key", "occurred_at"},
    "reconciliation_runs": {"reconciliation_id", "account_id", "status", "local_version", "exchange_snapshot_ref", "difference_refs", "residual_reason_codes", "started_at", "completed_at"},
    "state_attempts": {"attempt_id", "deployment_id", "release_id", "schema_version", "input_snapshot_ref", "input_hash", "previous_state_hash", "state_hash", "intent_hash", "event_id", "state_json", "intent_json", "decision_time"},
    "state_commits": {"deployment_id", "input_snapshot_ref", "attempt_id", "state_version", "committed_at"},
}
_JOURNAL_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    table: (column,) for table, column in (
        ("schema_migrations", "migration_id"), ("journal_accounts", "account_id"),
        ("allocation_plans", "allocation_plan_id"), ("inbox", "event_id"),
        ("outbox", "outbox_id"), ("order_state_events", "event_id"),
        ("risk_reservations", "reservation_id"), ("order_commands", "command_id"),
        ("execution_reports", "report_id"), ("fills", "fill_id"),
        ("account_ledger_entries", "ledger_entry_id"),
        ("strategy_ledger_entries", "ledger_entry_id"),
        ("reconciliation_runs", "reconciliation_id"),
        ("state_attempts", "attempt_id"),
    )
}
_JOURNAL_PRIMARY_KEYS["state_commits"] = ("deployment_id", "input_snapshot_ref")
_JOURNAL_UNIQUE_KEYS: dict[str, set[tuple[str, ...]]] = {
    "schema_migrations": set(), "journal_accounts": set(), "allocation_plans": set(),
    "inbox": set(), "outbox": set(), "order_state_events": set(),
    "risk_reservations": set(), "order_commands": {("client_order_id",)},
    "execution_reports": {("client_order_id", "report_id")},
    "fills": {("venue", "account_id", "trade_id")},
    "account_ledger_entries": {("idempotency_key",)},
    "strategy_ledger_entries": {("idempotency_key",)}, "reconciliation_runs": set(),
    "state_attempts": set(), "state_commits": {("attempt_id",)},
}
_JOURNAL_FOREIGN_KEYS = {
    "schema_migrations": set(), "journal_accounts": set(),
    "allocation_plans": {("account_id", "account_id", "journal_accounts", "RESTRICT")},
    "inbox": set(), "outbox": set(),
    "order_state_events": {("command_id", "command_id", "order_commands", "RESTRICT")},
    "risk_reservations": {("account_id", "account_id", "journal_accounts", "RESTRICT")},
    "order_commands": {
        ("account_id", "account_id", "journal_accounts", "RESTRICT"),
        ("reservation_id", "reservation_id", "risk_reservations", "RESTRICT"),
        ("allocation_plan_id", "allocation_plan_id", "allocation_plans", "RESTRICT"),
    },
    "execution_reports": {("client_order_id", "client_order_id", "order_commands", "RESTRICT")},
    "fills": {
        ("account_id", "account_id", "journal_accounts", "RESTRICT"),
        ("client_order_id", "client_order_id", "order_commands", "RESTRICT"),
    },
    "account_ledger_entries": {("account_id", "account_id", "journal_accounts", "RESTRICT")},
    "strategy_ledger_entries": {("account_id", "account_id", "journal_accounts", "RESTRICT")},
    "reconciliation_runs": {("account_id", "account_id", "journal_accounts", "RESTRICT")},
    "state_attempts": set(), "state_commits": {("attempt_id", "attempt_id", "state_attempts", "RESTRICT")},
}
_JOURNAL_CHECKS: dict[str, tuple[str, ...]] = {
    "schema_migrations": (), "journal_accounts": (), "allocation_plans": (), "inbox": (), "outbox": (),
    "order_state_events": ("CHECK(TO_STATUSIN(",),
    "risk_reservations": ("CHECK(SIDEIN(", "CHECK(STATUSIN("),
    "order_commands": ("CHECK(SIDEIN(", "CHECK(STATUSIN(", "CHECK(STATE_VERSION>=0)"),
    "execution_reports": ("CHECK(STATUSIN(",), "fills": ("CHECK(SIDEIN(",),
    "account_ledger_entries": (), "strategy_ledger_entries": (),
    "reconciliation_runs": ("CHECK(STATUSIN(",), "state_attempts": (), "state_commits": (),
}
_JOURNAL_INDEXES: dict[str, tuple[str, tuple[str, ...]]] = {
    "idx_outbox_pending": ("outbox", ("published_at", "created_at")),
    "idx_reports_order": ("execution_reports", ("client_order_id", "received_at")),
    "idx_fills_order": ("fills", ("client_order_id", "trade_time")),
    "idx_state_commits_latest": ("state_commits", ("deployment_id", "state_version")),
}
_JOURNAL_TRIGGERS = {
    "_journal_no_update_inbox", "_journal_no_delete_inbox",
    "_journal_no_update_order_state_events", "_journal_no_delete_order_state_events",
    "_journal_no_update_execution_reports", "_journal_no_delete_execution_reports",
    "_journal_no_update_fills", "_journal_no_delete_fills",
    "_journal_no_update_account_ledger_entries", "_journal_no_delete_account_ledger_entries",
    "_journal_no_update_strategy_ledger_entries", "_journal_no_delete_strategy_ledger_entries",
    "_journal_no_update_reconciliation_runs", "_journal_no_delete_reconciliation_runs",
    "_journal_no_delete_order_commands", "_journal_order_command_cas",
    "_journal_reservation_projection", "_journal_reservation_delete",
    "_journal_outbox_projection", "_journal_outbox_delete",
    "_journal_no_update_state_attempts", "_journal_no_delete_state_attempts",
    "_journal_no_update_state_commits", "_journal_no_delete_state_commits",
}

_ORDER_STATUSES = "'CREATED','RISK_RESERVED','SUBMITTING','PENDING_ACK','WORKING','PARTIALLY_FILLED','CANCEL_PENDING','FILLED','CANCELED','REJECTED','EXPIRED','UNKNOWN','RECONCILING'"
_RESERVATION_STATUSES = "'ACTIVE','PARTIALLY_CONSUMED','CONSUMED','RELEASED','EXPIRED'"

_DDL = f"""
CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS journal_accounts (account_id TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS allocation_plans (
    allocation_plan_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    instrument_id TEXT NOT NULL,
    input_intents TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS inbox (
    event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, received_at TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
    outbox_id TEXT PRIMARY KEY, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL, published_at TEXT
);
CREATE TABLE IF NOT EXISTS order_state_events (
    event_id TEXT PRIMARY KEY, command_id TEXT NOT NULL, from_status TEXT,
    to_status TEXT NOT NULL CHECK (to_status IN ({_ORDER_STATUSES})), state_version INTEGER NOT NULL,
    observed_at TEXT NOT NULL, payload TEXT NOT NULL,
    FOREIGN KEY (command_id) REFERENCES order_commands(command_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS risk_reservations (
    reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, intent_ids TEXT NOT NULL,
    instrument_id TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    reserved_quantity TEXT NOT NULL, reserved_notional TEXT NOT NULL, reserved_margin TEXT NOT NULL,
    risk_snapshot_version INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ({_RESERVATION_STATUSES})),
    expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS order_commands (
    command_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE, account_id TEXT NOT NULL,
    reservation_id TEXT NOT NULL, allocation_plan_id TEXT NOT NULL, instrument_id TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('BUY','SELL')), quantity TEXT NOT NULL, order_type TEXT NOT NULL,
    limit_price TEXT, status TEXT NOT NULL CHECK (status IN ({_ORDER_STATUSES})),
    state_version INTEGER NOT NULL CHECK (state_version >= 0), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT,
    FOREIGN KEY (reservation_id) REFERENCES risk_reservations(reservation_id) ON DELETE RESTRICT,
    FOREIGN KEY (allocation_plan_id) REFERENCES allocation_plans(allocation_plan_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS execution_reports (
    report_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL, broker_order_id TEXT,
    status TEXT NOT NULL CHECK (status IN ({_ORDER_STATUSES})), event_sequence INTEGER,
    raw_payload TEXT NOT NULL, received_at TEXT NOT NULL, UNIQUE (client_order_id, report_id),
    FOREIGN KEY (client_order_id) REFERENCES order_commands(client_order_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY, venue TEXT NOT NULL, account_id TEXT NOT NULL, client_order_id TEXT NOT NULL,
    trade_id TEXT NOT NULL, instrument_id TEXT NOT NULL, side TEXT NOT NULL CHECK (side IN ('BUY','SELL')),
    quantity TEXT NOT NULL, price TEXT NOT NULL, fee TEXT NOT NULL, fee_currency TEXT NOT NULL,
    trade_time TEXT NOT NULL, UNIQUE (venue, account_id, trade_id),
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT,
    FOREIGN KEY (client_order_id) REFERENCES order_commands(client_order_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS account_ledger_entries (
    ledger_entry_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, asset TEXT NOT NULL, amount TEXT NOT NULL,
    entry_type TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS strategy_ledger_entries (
    ledger_entry_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, strategy_id TEXT NOT NULL, asset TEXT NOT NULL,
    amount TEXT NOT NULL, entry_type TEXT NOT NULL, source_type TEXT NOT NULL, source_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS reconciliation_runs (
    reconciliation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('STARTED','MATCHED','DIFF','FAILED')),
    local_version INTEGER NOT NULL, exchange_snapshot_ref TEXT, difference_refs TEXT NOT NULL,
    residual_reason_codes TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS state_attempts (
    attempt_id TEXT PRIMARY KEY, deployment_id TEXT NOT NULL, release_id TEXT NOT NULL,
    schema_version INTEGER NOT NULL, input_snapshot_ref TEXT NOT NULL, input_hash TEXT NOT NULL,
    previous_state_hash TEXT NOT NULL, state_hash TEXT NOT NULL, intent_hash TEXT NOT NULL,
    event_id TEXT NOT NULL, state_json TEXT NOT NULL, intent_json TEXT, decision_time TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state_commits (
    deployment_id TEXT NOT NULL, input_snapshot_ref TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
    state_version INTEGER NOT NULL, committed_at TEXT NOT NULL,
    PRIMARY KEY (deployment_id, input_snapshot_ref),
    FOREIGN KEY (attempt_id) REFERENCES state_attempts(attempt_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox(published_at, created_at);
CREATE INDEX IF NOT EXISTS idx_reports_order ON execution_reports(client_order_id, received_at);
CREATE INDEX IF NOT EXISTS idx_fills_order ON fills(client_order_id, trade_time);
CREATE INDEX IF NOT EXISTS idx_state_commits_latest ON state_commits(deployment_id, state_version);
"""
