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

import sqlite3
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


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
        with self.connect() as conn:
            conn.executescript(_DDL)

    def table_exists(self, table_name: str) -> bool:
        """检查表是否存在"""
        with self.connect() as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return cursor.fetchone() is not None

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
                BEGIN
                    SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 UPDATE: {table_name}');
                END
            """)
        # 禁止 DELETE
        conn.execute(f"""
            CREATE TRIGGER IF NOT EXISTS _no_delete_{table_name}
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'INSERT-ONLY 表禁止 DELETE: {table_name}');
            END
        """)

    def setup_insert_only_constraints(self) -> None:
        """设置 insert-only 约束（对 experiments, experiment_events, audit_log）"""
        with self.connect() as conn:
            for table in ("experiments", "experiment_events", "audit_log"):
                self.enforce_insert_only(table, conn)
            conn.commit()


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
