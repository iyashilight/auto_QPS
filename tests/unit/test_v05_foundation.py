"""M0 v0.5 contract tests: values, event lineage, state CAS and migrations."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from qps.core.constants import OrderStatus
from qps.core.events import EventEnvelope, EventType
from qps.core.models.foundation import (
    DatasetManifest,
    ExecutionReport,
    FeatureDefinition,
    OrderState,
    PointInTimeRecord,
    SourceReference,
)
from qps.infra.storage.meta_db import MetaDB
from qps.infra.storage.trading_journal import JournalDB, SchemaMigrationError

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)


class TestV05TimeAndStateContracts:
    def test_pit_rejects_unavailable_record(self) -> None:
        with pytest.raises(ValidationError, match="available_at"):
            PointInTimeRecord(
                event_time=NOW,
                available_at=NOW - timedelta(seconds=1),
                ingested_at=NOW,
            )

    def test_pit_rejects_non_utc_datetime(self) -> None:
        with pytest.raises(ValidationError, match="UTC"):
            PointInTimeRecord(
                event_time=datetime(2026, 8, 5, 12, 0),
                available_at=NOW,
                ingested_at=NOW,
            )

    def test_manifest_is_immutable_and_hashed(self) -> None:
        manifest = DatasetManifest(
            manifest_id="manifest-1",
            dataset_type="bars",
            schema_version="v1",
            partition_refs=("bars/2026.parquet",),
            content_hash="sha256:abc",
            source_refs=(SourceReference(name="exchange", reference="batch-1"),),
            created_at=NOW,
        )
        with pytest.raises((TypeError, ValidationError)):
            manifest.content_hash = "changed"  # type: ignore[misc]

    def test_order_state_cas_and_late_fill(self) -> None:
        state = OrderState(
            order_id="ord-1",
            status=OrderStatus.CREATED,
            state_version=0,
            filled_quantity=Decimal("0"),
            updated_at=NOW,
        )
        reserved = state.transition(
            OrderStatus.RISK_RESERVED, expected_version=0, updated_at=NOW + timedelta(seconds=1)
        )
        with pytest.raises(ValueError, match="版本已过期"):
            reserved.transition(
                OrderStatus.SUBMITTING, expected_version=0, updated_at=NOW + timedelta(seconds=2)
            )
        terminal = reserved.transition(
            OrderStatus.SUBMITTING, expected_version=1, updated_at=NOW + timedelta(seconds=1)
        ).transition(
            OrderStatus.PENDING_ACK, expected_version=2, updated_at=NOW + timedelta(seconds=2)
        ).transition(
            OrderStatus.WORKING, expected_version=3, updated_at=NOW + timedelta(seconds=3)
        ).transition(
            OrderStatus.FILLED, expected_version=4, updated_at=NOW + timedelta(seconds=4)
        )
        late = terminal.record_late_fill(Decimal("0.25"), updated_at=NOW + timedelta(seconds=5))
        assert late.filled_quantity == Decimal("0.25")
        assert late.late_fill_quantity == Decimal("0.25")

    def test_order_state_rejects_non_monotonic_time_and_non_terminal_late_fill(self) -> None:
        state = OrderState(
            order_id="ord-1",
            status=OrderStatus.WORKING,
            state_version=2,
            filled_quantity=Decimal("0"),
            updated_at=NOW,
        )
        with pytest.raises(ValueError, match="时间"):
            state.transition(
                OrderStatus.FILLED, expected_version=2, updated_at=NOW - timedelta(seconds=1)
            )
        with pytest.raises(ValueError, match="终态"):
            state.record_late_fill(Decimal("0.1"), updated_at=NOW)

        terminal = state.transition(OrderStatus.FILLED, expected_version=2, updated_at=NOW)
        with pytest.raises(ValueError, match="时间"):
            terminal.record_late_fill(Decimal("0.1"), updated_at=NOW - timedelta(seconds=1))


class TestV05Events:
    def test_event_lineage_fields_round_trip(self) -> None:
        event = EventEnvelope(
            event_id="evt-1",
            event_type=EventType.ORDER_COMMAND_CREATED,
            occurred_at=NOW,
            recorded_at=NOW + timedelta(milliseconds=1),
            source="journal",
            correlation_id="intent-1",
            causation_id="evt-parent",
        )
        restored = EventEnvelope.from_json(event.to_json())
        assert restored.occurred_at == NOW
        assert restored.recorded_at is not None
        assert restored.ts is not None

    def test_event_rejects_recorded_before_occurred(self) -> None:
        with pytest.raises(ValidationError, match="recorded_at"):
            EventEnvelope(
                event_id="evt-2",
                event_type=EventType.DATA_AVAILABLE,
                occurred_at=NOW,
                recorded_at=NOW - timedelta(seconds=1),
                source="data",
            )

    def test_event_payload_and_string_maps_are_deeply_immutable(self) -> None:
        event = EventEnvelope(
            event_id="evt-immutable",
            event_type=EventType.DATA_AVAILABLE,
            occurred_at=NOW,
            source="data",
            payload={"nested": {"items": ["a"]}},
        )
        with pytest.raises(TypeError):
            event.payload["nested"]["items"] = ("b",)  # type: ignore[index]
        assert event.payload["nested"]["items"] == ("a",)

        feature = FeatureDefinition(
            feature_id="feature-1",
            semantic_name="close",
            description="close",
            formula_or_code_hash="sha256:x",
            input_schema={"price": "Decimal"},
            source_types=("BAR",),
            window=1,
            warmup=0,
            frequency="1m",
            available_at_rule="close",
            owner="research",
            version=1,
            created_at=NOW,
        )
        assert feature.input_schema == {"price": "Decimal"}
        with pytest.raises(TypeError):
            feature.input_schema["price"] = "float"  # type: ignore[index]

    def test_string_map_and_raw_json_keep_object_semantics(self) -> None:
        feature = FeatureDefinition(
            feature_id="feature-json",
            semantic_name="close",
            description="close",
            formula_or_code_hash="sha256:x",
            input_schema={"volume": "Decimal", "price": "Decimal"},
            source_types=("BAR",),
            window=1,
            warmup=0,
            frequency="1m",
            available_at_rule="close",
            owner="research",
            version=1,
            created_at=NOW,
        )
        dumped = feature.model_dump()
        assert dumped["input_schema"] == {"price": "Decimal", "volume": "Decimal"}
        dumped_json = feature.model_dump_json()
        assert json.loads(dumped_json)["input_schema"] == {
            "price": "Decimal",
            "volume": "Decimal",
        }
        assert FeatureDefinition.model_validate_json(dumped_json) == feature

        report = ExecutionReport(
            report_id="report-json",
            client_order_id="client-json",
            status=OrderStatus.WORKING,
            raw_payload={
                "symbol": "BTC",
                "fills": [{"price": 100.5, "fee": None}],
                "flags": {"maker": True},
            },
            received_at=NOW,
        )
        report_json = report.model_dump_json()
        assert json.loads(report_json)["raw_payload"]["fills"][0]["fee"] is None
        assert ExecutionReport.model_validate_json(report_json) == report
        with pytest.raises(TypeError):
            report.raw_payload["flags"]["maker"] = False  # type: ignore[index]
        with pytest.raises(ValidationError):
            ExecutionReport(
                report_id="report-invalid",
                client_order_id="client-json",
                status=OrderStatus.WORKING,
                raw_payload={"bad": Decimal("1.2")},
                received_at=NOW,
            )


class TestV05Migrations:
    def test_meta_fresh_upgrade_and_rerun(self, tmp_path: Path) -> None:
        path = tmp_path / "meta.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE instruments (instrument_id TEXT PRIMARY KEY);
                CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                """
            )
        db = MetaDB(path)
        db.initialize()
        db.initialize()
        assert db.table_exists("strategy_releases")
        assert db.table_exists("feature_definitions")
        with db.connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 'm0-v05-contracts'"
            ).fetchone()[0] == 1
            assert conn.execute(
                "SELECT schema_version FROM schema_migrations WHERE migration_id = 'm0-v05-contracts'"
            ).fetchone()[0] == 2

        drift_path = tmp_path / "meta-drift.db"
        with sqlite3.connect(drift_path) as conn:
            conn.execute("CREATE TABLE feature_definitions (feature_id TEXT PRIMARY KEY)")
        with pytest.raises(RuntimeError, match="schema drift"):
            MetaDB(drift_path).initialize()

    def test_journal_fresh_and_rerun(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "journal.db")
        journal.initialize()
        journal.initialize()
        assert journal.table_exists("outbox")
        assert journal.table_exists("risk_reservations")
        with journal.connect() as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 'm0-v05-journal'"
            ).fetchone()[0] == 1

    def test_journal_append_only_and_fill_idempotency_key(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "journal.db")
        journal.initialize()
        with journal.transaction() as conn:
            conn.execute("INSERT INTO journal_accounts (account_id) VALUES (?)", ("acc-1",))
            conn.execute(
                "INSERT INTO reconciliation_runs "
                "(reconciliation_id, account_id, status, local_version, difference_refs, "
                "residual_reason_codes, started_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("rec-1", "acc-1", "STARTED", 1, "[]", "[]", NOW.isoformat()),
            )
        with journal.connect() as conn, pytest.raises(sqlite3.DatabaseError, match="INSERT-ONLY"):
            conn.execute("UPDATE reconciliation_runs SET status = 'MATCHED' WHERE reconciliation_id = ?", ("rec-1",))

    def test_journal_upgrades_real_legacy_schema_and_rejects_drift(self, tmp_path: Path) -> None:
        path = tmp_path / "legacy-journal.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE risk_reservations (
                    reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, intent_ids TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, reserved_quantity TEXT NOT NULL,
                    reserved_notional TEXT NOT NULL, reserved_margin TEXT NOT NULL,
                    risk_snapshot_version INTEGER NOT NULL, status TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE order_commands (
                    command_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL, reservation_id TEXT NOT NULL, allocation_plan_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
                    order_type TEXT NOT NULL, limit_price TEXT, status TEXT NOT NULL,
                    state_version INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                INSERT INTO risk_reservations VALUES
                    ('res-legacy', 'acc-legacy', '[]', 'BTC', 'BUY', '1', '1', '1', 1,
                     'ACTIVE', '2026-08-05T13:00:00+00:00', '2026-08-05T12:00:00+00:00');
                INSERT INTO order_commands VALUES
                    ('cmd-legacy', 'client-legacy', 'acc-legacy', 'res-legacy', 'alloc-legacy',
                     'BTC', 'BUY', '1', 'MARKET', NULL, 'CREATED', 0, '2026-08-05T12:00:00+00:00');
                """
            )
        journal = JournalDB(path)
        journal.initialize()
        journal.initialize()
        with journal.connect() as conn:
            assert conn.execute("SELECT updated_at FROM order_commands WHERE command_id = 'cmd-legacy'").fetchone()[0]
            assert conn.execute("PRAGMA foreign_key_list(order_commands)").fetchall()
            assert conn.execute("SELECT COUNT(*) FROM _legacy_order_commands").fetchone()[0] == 1

        drift_path = tmp_path / "drift-journal.db"
        with sqlite3.connect(drift_path) as conn:
            conn.execute("CREATE TABLE order_commands (command_id TEXT PRIMARY KEY)")
        with pytest.raises(SchemaMigrationError, match="schema drift"):
            JournalDB(drift_path).initialize()

    def test_partial_constraint_schemas_fail_before_success_marker(self, tmp_path: Path) -> None:
        journal_path = tmp_path / "partial-journal.db"
        with sqlite3.connect(journal_path) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (
                    migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL, schema_version INTEGER NOT NULL
                );
                CREATE TABLE journal_accounts (account_id TEXT PRIMARY KEY);
                CREATE TABLE allocation_plans (
                    allocation_plan_id TEXT PRIMARY KEY, account_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, input_intents TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE risk_reservations (
                    reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, intent_ids TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, reserved_quantity TEXT NOT NULL,
                    reserved_notional TEXT NOT NULL, reserved_margin TEXT NOT NULL,
                    risk_snapshot_version INTEGER NOT NULL, status TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id)
                );
                CREATE TABLE order_commands (
                    command_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL, reservation_id TEXT NOT NULL, allocation_plan_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
                    order_type TEXT NOT NULL, limit_price TEXT, status TEXT NOT NULL,
                    state_version INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES journal_accounts(account_id)
                );
                CREATE TABLE outbox (
                    outbox_id TEXT PRIMARY KEY, aggregate_id TEXT NOT NULL, event_type TEXT NOT NULL,
                    payload TEXT NOT NULL, created_at TEXT NOT NULL, published_at TEXT
                );
                """
            )
        with pytest.raises(SchemaMigrationError, match="schema drift"):
            JournalDB(journal_path).initialize()
        with sqlite3.connect(journal_path) as conn:
            assert conn.execute(
                "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 'm0-v05-journal'"
            ).fetchone()[0] == 0

        meta_path = tmp_path / "partial-meta.db"
        with sqlite3.connect(meta_path) as conn:
            conn.execute(
                "CREATE TABLE feature_definitions ("
                "feature_id TEXT NOT NULL, version INTEGER NOT NULL, semantic_name TEXT NOT NULL, "
                "description TEXT NOT NULL, formula_or_code_hash TEXT NOT NULL, input_schema TEXT NOT NULL, "
                "source_types TEXT NOT NULL, window INTEGER NOT NULL, warmup INTEGER NOT NULL, "
                "frequency TEXT NOT NULL, available_at_rule TEXT NOT NULL, owner TEXT NOT NULL, "
                "status TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
        with pytest.raises(RuntimeError, match="schema drift"):
            MetaDB(meta_path).initialize()
        with sqlite3.connect(meta_path) as conn:
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone():
                assert conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 'm0-v05-contracts'"
                ).fetchone()[0] == 0

    @pytest.mark.parametrize(
        "tampered_sql",
        (
            "CREATE TRIGGER _journal_no_update_inbox BEFORE UPDATE ON inbox BEGIN SELECT 1; END",
            "CREATE TRIGGER _journal_no_update_inbox BEFORE UPDATE ON inbox "
            "WHEN NEW.payload <> OLD.payload BEGIN SELECT RAISE(ABORT, 'partial'); END",
            "CREATE TRIGGER _journal_no_update_inbox BEFORE UPDATE ON outbox "
            "BEGIN SELECT RAISE(ABORT, 'wrong table'); END",
        ),
        ids=("empty", "partial", "wrong-table"),
    )
    def test_tampered_journal_trigger_semantics_fail_contract(
        self, tmp_path: Path, tampered_sql: str
    ) -> None:
        journal = JournalDB(tmp_path / "tampered-journal.db")
        journal.initialize()
        with journal.connect() as conn:
            conn.execute("DROP TRIGGER _journal_no_update_inbox")
            conn.execute(tampered_sql)
        with pytest.raises(SchemaMigrationError, match="trigger"):
            journal.initialize()

    @pytest.mark.parametrize(
        "tampered_sql",
        (
            "CREATE TRIGGER _no_update_dataset_manifests "
            "BEFORE UPDATE ON dataset_manifests BEGIN SELECT 1; END",
            "CREATE TRIGGER _no_update_dataset_manifests BEFORE UPDATE ON dataset_manifests "
            "WHEN NEW.content_hash <> OLD.content_hash BEGIN SELECT RAISE(ABORT, 'partial'); END",
            "CREATE TRIGGER _no_update_dataset_manifests BEFORE UPDATE ON experiments "
            "BEGIN SELECT RAISE(ABORT, 'wrong table'); END",
        ),
        ids=("empty", "partial", "wrong-table"),
    )
    def test_tampered_meta_trigger_semantics_fail_contract(
        self, tmp_path: Path, tampered_sql: str
    ) -> None:
        meta = MetaDB(tmp_path / "tampered-meta.db")
        meta.initialize()
        with meta.connect() as conn:
            conn.execute("DROP TRIGGER _no_update_dataset_manifests")
            conn.execute(tampered_sql)
        with pytest.raises(RuntimeError, match="trigger"):
            meta.initialize()

    @pytest.mark.parametrize(
        "trigger_name,tampered_sql",
        (
            (
                "_journal_order_command_cas",
                "CREATE TRIGGER _journal_order_command_cas BEFORE UPDATE ON order_commands "
                "FOR EACH ROW WHEN NEW.state_version <> OLD.state_version + 1 "
                "OR NEW.updated_at < OLD.updated_at BEGIN "
                "SELECT RAISE(ABORT, 'weak CAS'); END",
            ),
            (
                "_journal_reservation_projection",
                "CREATE TRIGGER _journal_reservation_projection BEFORE UPDATE ON risk_reservations "
                "FOR EACH ROW WHEN NEW.reservation_id <> OLD.reservation_id "
                "OR NEW.account_id <> OLD.account_id OR NEW.intent_ids <> OLD.intent_ids "
                "OR NEW.instrument_id <> OLD.instrument_id OR NEW.side <> OLD.side "
                "OR NEW.reserved_quantity <> OLD.reserved_quantity "
                "OR NEW.reserved_margin <> OLD.reserved_margin "
                "OR NEW.risk_snapshot_version <> OLD.risk_snapshot_version "
                "OR NEW.expires_at <> OLD.expires_at OR NEW.created_at <> OLD.created_at "
                "BEGIN SELECT RAISE(ABORT, 'weak reservation'); END",
            ),
            (
                "_journal_outbox_projection",
                "CREATE TRIGGER _journal_outbox_projection BEFORE UPDATE ON outbox FOR EACH ROW "
                "WHEN NEW.outbox_id <> OLD.outbox_id OR NEW.event_type <> OLD.event_type "
                "OR NEW.payload <> OLD.payload OR NEW.created_at <> OLD.created_at "
                "BEGIN SELECT RAISE(ABORT, 'weak outbox'); END",
            ),
        ),
        ids=("cas-transition-branch", "reservation-notional", "outbox-aggregate-id"),
    )
    def test_tampered_journal_critical_trigger_semantics_fail_contract(
        self, tmp_path: Path, trigger_name: str, tampered_sql: str
    ) -> None:
        journal = JournalDB(tmp_path / f"tampered-{trigger_name}.db")
        journal.initialize()
        with journal.connect() as conn:
            conn.execute(f"DROP TRIGGER {trigger_name}")
            conn.execute(tampered_sql)
        with pytest.raises(SchemaMigrationError, match="trigger"):
            journal.initialize()

    def test_comment_cannot_forge_journal_cas_contract(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "commented-cas.db")
        journal.initialize()
        with journal.connect() as conn:
            official_sql = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' "
                "AND name = '_journal_order_command_cas'"
            ).fetchone()[0]
            conn.execute("DROP TRIGGER _journal_order_command_cas")
            conn.execute(
                "/* "
                + official_sql
                + " */ CREATE TRIGGER _journal_order_command_cas "
                "BEFORE UPDATE ON order_commands FOR EACH ROW "
                "WHEN NEW.state_version <> OLD.state_version + 1 "
                "OR NEW.updated_at < OLD.updated_at BEGIN "
                "SELECT RAISE(ABORT, 'commented weak CAS'); END"
            )
        with pytest.raises(SchemaMigrationError, match="trigger"):
            journal.initialize()

    def test_tampered_meta_experiment_projection_fails_contract(self, tmp_path: Path) -> None:
        meta = MetaDB(tmp_path / "tampered-experiments.db")
        meta.initialize()
        with meta.connect() as conn:
            conn.execute("DROP TRIGGER _no_update_experiments")
            conn.execute(
                "CREATE TRIGGER _no_update_experiments BEFORE UPDATE ON experiments FOR EACH ROW "
                "WHEN COALESCE(NEW.run_type, '') <> COALESCE(OLD.run_type, '') "
                "OR COALESCE(NEW.strategy_id, '') <> COALESCE(OLD.strategy_id, '') "
                "OR NEW.version_no <> OLD.version_no "
                "OR COALESCE(NEW.params, '') <> COALESCE(OLD.params, '') "
                "OR COALESCE(NEW.data_version, '') <> COALESCE(OLD.data_version, '') "
                "OR COALESCE(NEW.feature_version, '') <> COALESCE(OLD.feature_version, '') "
                "OR COALESCE(NEW.engine_config, '') <> COALESCE(OLD.engine_config, '') "
                "OR COALESCE(NEW.environment_lock, '') <> COALESCE(OLD.environment_lock, '') "
                "OR COALESCE(NEW.start_ts, '') <> COALESCE(OLD.start_ts, '') "
                "OR COALESCE(NEW.end_ts, '') <> COALESCE(OLD.end_ts, '') "
                "BEGIN SELECT RAISE(ABORT, 'weak experiment'); END"
            )
        with pytest.raises(RuntimeError, match="trigger"):
            meta.initialize()

    def test_failed_legacy_upgrade_restores_original_schema_and_is_retryable(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "failed-legacy-journal.db"
        with sqlite3.connect(path) as conn:
            conn.executescript(
                """
                CREATE TABLE schema_migrations (migration_id TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE risk_reservations (
                    reservation_id TEXT PRIMARY KEY, account_id TEXT NOT NULL, intent_ids TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, reserved_quantity TEXT NOT NULL,
                    reserved_notional TEXT NOT NULL, reserved_margin TEXT NOT NULL,
                    risk_snapshot_version INTEGER NOT NULL, status TEXT NOT NULL,
                    expires_at TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE order_commands (
                    command_id TEXT PRIMARY KEY, client_order_id TEXT NOT NULL UNIQUE,
                    account_id TEXT NOT NULL, reservation_id TEXT NOT NULL, allocation_plan_id TEXT NOT NULL,
                    instrument_id TEXT NOT NULL, side TEXT NOT NULL, quantity TEXT NOT NULL,
                    order_type TEXT NOT NULL, limit_price TEXT, status TEXT NOT NULL,
                    state_version INTEGER NOT NULL, created_at TEXT NOT NULL
                );
                INSERT INTO risk_reservations VALUES
                    ('res-fail', 'acc-fail', '[]', 'BTC', 'BUY', '1', '1', '1', 1,
                     'ACTIVE', '2026-08-05T13:00:00+00:00', '2026-08-05T12:00:00+00:00');
                INSERT INTO order_commands VALUES
                    ('cmd-fail', 'client-fail', 'acc-fail', 'res-fail', 'alloc-fail',
                     'BTC', 'BUY', '1', 'MARKET', NULL, 'BROKEN', 0, '2026-08-05T12:00:00+00:00');
                """
            )
        journal = JournalDB(path)
        for _ in range(2):
            with pytest.raises(SchemaMigrationError, match="original schema restored"):
                journal.initialize()
            with sqlite3.connect(path) as conn:
                assert conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='order_commands'"
                ).fetchone()
                assert conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='_legacy_order_commands'"
                ).fetchone() is None
                assert conn.execute(
                    "SELECT status FROM order_commands WHERE command_id = 'cmd-fail'"
                ).fetchone()[0] == "BROKEN"
                assert conn.execute(
                    "SELECT COUNT(*) FROM schema_migrations WHERE migration_id = 'm0-v05-journal'"
                ).fetchone()[0] == 0


class TestV05JournalIntegrity:
    @staticmethod
    def _seed_order_graph(conn: sqlite3.Connection) -> None:
        conn.execute("INSERT INTO journal_accounts (account_id) VALUES (?)", ("acc-1",))
        conn.execute(
            "INSERT INTO allocation_plans "
            "(allocation_plan_id, account_id, instrument_id, input_intents, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("alloc-1", "acc-1", "BTC-USDT-PERP", "[\"intent-1\"]", NOW.isoformat()),
        )
        conn.execute(
            "INSERT INTO risk_reservations "
            "(reservation_id, account_id, intent_ids, instrument_id, side, reserved_quantity, "
            "reserved_notional, reserved_margin, risk_snapshot_version, status, expires_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "res-1", "acc-1", "[\"intent-1\"]", "BTC-USDT-PERP", "BUY", "1", "10", "5", 1,
                "ACTIVE", (NOW + timedelta(hours=1)).isoformat(), NOW.isoformat(),
            ),
        )
        conn.execute(
            "INSERT INTO order_commands "
            "(command_id, client_order_id, account_id, reservation_id, allocation_plan_id, "
            "instrument_id, side, quantity, order_type, status, state_version, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmd-1", "client-1", "acc-1", "res-1", "alloc-1", "BTC-USDT-PERP", "BUY", "1",
                "MARKET", "CREATED", 0, NOW.isoformat(), NOW.isoformat(),
            ),
        )

    def test_foreign_keys_exist_and_orphans_are_rejected(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "journal.db")
        journal.initialize()
        with journal.connect() as conn:
            for table in (
                "risk_reservations",
                "order_commands",
                "execution_reports",
                "fills",
                "account_ledger_entries",
                "strategy_ledger_entries",
                "reconciliation_runs",
            ):
                assert conn.execute(f"PRAGMA foreign_key_list({table})").fetchall(), table
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    "INSERT INTO risk_reservations "
                    "(reservation_id, account_id, intent_ids, instrument_id, side, reserved_quantity, "
                    "reserved_notional, reserved_margin, risk_snapshot_version, status, expires_at, created_at) "
                    "VALUES ('orphan', 'missing', '[]', 'BTC', 'BUY', '1', '1', '1', 1, 'ACTIVE', ?, ?)",
                    (NOW.isoformat(), NOW.isoformat()),
                )

    def test_inbox_events_reservations_and_outbox_are_protected(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "journal.db")
        journal.initialize()
        with journal.transaction() as conn:
            self._seed_order_graph(conn)
            conn.execute("INSERT INTO inbox (event_id, event_type, received_at, payload) VALUES (?, ?, ?, ?)",
                         ("evt-1", "DATA_AVAILABLE", NOW.isoformat(), "{}"))
            conn.execute("INSERT INTO order_state_events "
                         "(event_id, command_id, to_status, state_version, observed_at, payload) "
                         "VALUES (?, ?, ?, ?, ?, ?)",
                         ("ose-1", "cmd-1", "CREATED", 0, NOW.isoformat(), "{}"))
            conn.execute("INSERT INTO outbox "
                         "(outbox_id, aggregate_id, event_type, payload, created_at) VALUES (?, ?, ?, ?, ?)",
                         ("out-1", "cmd-1", "ORDER_COMMAND_CREATED", "{}", NOW.isoformat()))
        with journal.connect() as conn:
            with pytest.raises(sqlite3.DatabaseError, match="INSERT-ONLY"):
                conn.execute("UPDATE inbox SET payload = '{}' WHERE event_id = 'evt-1'")
            with pytest.raises(sqlite3.DatabaseError, match="INSERT-ONLY"):
                conn.execute("DELETE FROM inbox WHERE event_id = 'evt-1'")
            with pytest.raises(sqlite3.DatabaseError, match="INSERT-ONLY"):
                conn.execute("UPDATE order_state_events SET payload = '{}' WHERE event_id = 'ose-1'")
            with pytest.raises(sqlite3.DatabaseError, match="INSERT-ONLY"):
                conn.execute("DELETE FROM risk_reservations WHERE reservation_id = 'res-1'")
            conn.execute("UPDATE outbox SET published_at = ? WHERE outbox_id = 'out-1'", (NOW.isoformat(),))
            with pytest.raises(sqlite3.DatabaseError, match="只允许发布投影"):
                conn.execute("UPDATE outbox SET payload = '{\"changed\":true}' WHERE outbox_id = 'out-1'")

    def test_database_rejects_illegal_order_state(self, tmp_path: Path) -> None:
        journal = JournalDB(tmp_path / "journal.db")
        journal.initialize()
        with journal.transaction() as conn:
            self._seed_order_graph(conn)
        with journal.connect() as conn:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE order_commands SET status = 'NOT_A_STATUS', state_version = 1 "
                             "WHERE command_id = 'cmd-1'")
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute("UPDATE order_commands SET status = 'FILLED', state_version = 2 "
                             "WHERE command_id = 'cmd-1'")
