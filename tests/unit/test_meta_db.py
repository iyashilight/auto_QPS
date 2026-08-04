"""T5 元数据库测试：建表、外键、金额精度、insert-only 约束"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from qps.infra.storage.meta_db import MetaDB

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def meta_db(tmp_path: Path) -> MetaDB:
    """创建临时元数据库"""
    db = MetaDB(tmp_path / "meta.db")
    db.initialize()
    return db


# ── 建表 ──────────────────────────────────────────────────


class TestMetaDBCreateTables:
    """建表测试"""

    def test_initialize_creates_all_tables(self, meta_db: MetaDB) -> None:
        """建表成功，所有预期表存在"""
        expected_tables = [
            "instruments",
            "symbol_mappings",
            "exchange_meta",
            "strategies",
            "strategy_versions",
            "strategy_params",
            "strategy_allocations",
            "experiments",
            "report_cards",
            "risk_rules",
            "risk_events",
            "audit_log",
            "tasks",
            "artifacts",
            "deployments",
            "portfolio_risk_snapshots",
        ]
        for table in expected_tables:
            assert meta_db.table_exists(table), f"表 {table} 应该存在"

    def test_initialize_idempotent(self, tmp_path: Path) -> None:
        """重复建表不报错"""
        db = MetaDB(tmp_path / "meta2.db")
        db.initialize()
        db.initialize()  # 再来一次
        assert db.table_exists("strategies")


# ── 外键约束 ──────────────────────────────────────────────


class TestMetaDBForeignKeys:
    """外键约束测试"""

    def test_strategy_version_fk(self, meta_db: MetaDB) -> None:
        """strategy_versions 外键：引用不存在的 strategy_id 应失败"""
        with meta_db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO strategy_versions "
                "(strategy_id, version_no, code_ref, params_schema, "
                "data_dependencies, feature_dependencies, runtime_constraints, "
                "asset_class_support, content_hash, created_at) "
                "VALUES (?, 1, 'ref', '{}', '[]', '[]', '{}', '[]', 'hash', ?)",
                ("nonexistent_strategy", datetime.now(UTC).isoformat()),
            )

    def test_strategy_version_fk_with_parent(self, meta_db: MetaDB) -> None:
        """先插入 strategies 再插入 strategy_versions 成功"""
        now = datetime.now(UTC).isoformat()
        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO strategies (strategy_id, name, style, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("trend_breakout", "趋势突破", "TREND", now, now),
            )
            conn.execute(
                "INSERT INTO strategy_versions "
                "(strategy_id, version_no, code_ref, params_schema, "
                "data_dependencies, feature_dependencies, runtime_constraints, "
                "asset_class_support, content_hash, created_at) "
                "VALUES (?, 1, 'ref', '{}', '[]', '[]', '{}', '[]', 'hash', ?)",
                ("trend_breakout", now),
            )
            conn.commit()

            cursor = conn.execute("SELECT COUNT(*) FROM strategy_versions")
            assert cursor.fetchone()[0] == 1


# ── 金额精度 ──────────────────────────────────────────────


class TestMetaDBDecimalPrecision:
    """金额字段精度不丢失"""

    def test_decimal_precision_in_strategy_allocations(self, meta_db: MetaDB) -> None:
        """Decimal 金额通过 TEXT 存储精度不丢失"""
        weight = Decimal("0.33333333")
        capital = Decimal("9999.99999999")

        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO strategy_allocations "
                "(allocation_id, account_id, strategy_id, weight, capital_limit) "
                "VALUES (?, ?, ?, ?, ?)",
                ("alloc_001", "acc_001", "test_strategy", str(weight), str(capital)),
            )
            conn.commit()

            cursor = conn.execute(
                "SELECT weight, capital_limit FROM strategy_allocations WHERE allocation_id = ?",
                ("alloc_001",),
            )
            row = cursor.fetchone()
            assert Decimal(row[0]) == weight
            assert Decimal(row[1]) == capital


# ── insert-only 约束 ──────────────────────────────────────


class TestMetaDBInsertOnly:
    """历史记录 insert-only 约束"""

    def test_experiments_no_update(self, meta_db: MetaDB) -> None:
        """experiments 表禁止内容列 UPDATE（status 列更新允许）"""
        meta_db.setup_insert_only_constraints()

        now = datetime.now(UTC).isoformat()
        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO experiments "
                "(run_id, run_type, strategy_id, version_no, params, "
                "data_version, feature_version, engine_config, status, created_at) "
                "VALUES (?, 'BACKTEST', ?, 1, '{}', ?, ?, '{}', 'PENDING', ?)",
                ("bt_001", "test_strategy", "binance-abc", "feat-1", now),
            )
            conn.commit()

            # UPDATE status 列应成功（R4 语义：status 是状态机迁移的合法通道）
            conn.execute(
                "UPDATE experiments SET status = 'SUCCEEDED' WHERE run_id = ?",
                ("bt_001",),
            )
            conn.commit()

            # UPDATE 内容列应被阻止
            with pytest.raises(Exception, match="INSERT-ONLY"):
                conn.execute(
                    "UPDATE experiments SET params = '{\"changed\": true}' WHERE run_id = ?",
                    ("bt_001",),
                )

    def test_audit_log_no_update(self, meta_db: MetaDB) -> None:
        """audit_log 表禁止 UPDATE"""
        meta_db.setup_insert_only_constraints()

        now = datetime.now(UTC).isoformat()
        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO audit_log (log_id, actor, action, target_type, target_id, ts) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                ("log_001", "MAN", "promote", "strategy", "test", now),
            )
            conn.commit()

            with pytest.raises(Exception, match="INSERT-ONLY"):
                conn.execute(
                    "UPDATE audit_log SET action = 'demote' WHERE log_id = ?",
                    ("log_001",),
                )

    def test_experiments_no_delete(self, meta_db: MetaDB) -> None:
        """experiments 表禁止 DELETE"""
        meta_db.setup_insert_only_constraints()

        now = datetime.now(UTC).isoformat()
        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO experiments "
                "(run_id, run_type, strategy_id, version_no, params, "
                "data_version, feature_version, engine_config, status, created_at) "
                "VALUES (?, 'BACKTEST', ?, 1, '{}', ?, ?, '{}', 'PENDING', ?)",
                ("bt_002", "test_strategy", "binance-abc", "feat-1", now),
            )
            conn.commit()

            with pytest.raises(Exception, match="INSERT-ONLY"):
                conn.execute("DELETE FROM experiments WHERE run_id = ?", ("bt_002",))

    def test_strategies_can_be_updated(self, meta_db: MetaDB) -> None:
        """strategies 表不在 insert-only 约束内，可以更新"""
        now = datetime.now(UTC).isoformat()
        with meta_db.connect() as conn:
            conn.execute(
                "INSERT INTO strategies (strategy_id, name, style, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("test_strategy", "测试策略", "TREND", now, now),
            )
            conn.commit()

            conn.execute(
                "UPDATE strategies SET lifecycle_status = 'BACKTESTING' WHERE strategy_id = ?",
                ("test_strategy",),
            )
            conn.commit()

            cursor = conn.execute(
                "SELECT lifecycle_status FROM strategies WHERE strategy_id = ?",
                ("test_strategy",),
            )
            assert cursor.fetchone()[0] == "BACKTESTING"


# ── 事务 ──────────────────────────────────────────────────


class TestMetaDBTransaction:
    """事务化访问测试"""

    def test_transaction_commit(self, meta_db: MetaDB) -> None:
        """事务正常提交"""
        now = datetime.now(UTC).isoformat()
        with meta_db.transaction() as conn:
            conn.execute(
                "INSERT INTO strategies (strategy_id, name, style, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tx_test", "事务测试", "TREND", now, now),
            )

        assert meta_db.table_exists("strategies")
        with meta_db.connect() as conn:
            cursor = conn.execute("SELECT name FROM strategies WHERE strategy_id = 'tx_test'")
            assert cursor.fetchone() is not None

    def test_transaction_rollback(self, meta_db: MetaDB) -> None:
        """事务异常回滚"""
        now = datetime.now(UTC).isoformat()
        with pytest.raises(ValueError), meta_db.transaction() as conn:
            conn.execute(
                "INSERT INTO strategies (strategy_id, name, style, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("rollback_test", "回滚测试", "TREND", now, now),
            )
            raise ValueError("模拟错误")

        with meta_db.connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM strategies WHERE strategy_id = 'rollback_test'"
            )
            assert cursor.fetchone() is None
