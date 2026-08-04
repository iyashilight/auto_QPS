"""策略注册表（见 docs/03 第 9 节 + docs/04 第 6 节）

实现 StrategyRegistryPort，负责策略版本的存储和管理。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qps.core.constants import AssetClass, StrategySource
from qps.core.errors import DataError
from qps.core.models.strategy import Strategy, StrategyVersion
from qps.core.sandbox import Sandbox


class StrategyCodePack:
    """策略代码包"""

    def __init__(
        self,
        strategy_id: str,
        code_ref: str,
        code_content: str,
        params_schema: dict[str, Any],
        data_dependencies: list[str] | None = None,
        feature_dependencies: list[str] | None = None,
        runtime_constraints: dict[str, Any] | None = None,
        asset_class_support: list[AssetClass] | None = None,
        source: StrategySource = StrategySource.MANUAL,
        name: str | None = None,
        style: str | None = None,
    ) -> None:
        """初始化代码包

        Args:
            strategy_id: 策略 ID
            code_ref: 代码引用（git commit + path）
            code_content: 代码内容
            params_schema: 参数 schema
            data_dependencies: 数据依赖
            feature_dependencies: 特征依赖
            runtime_constraints: 运行时约束
            asset_class_support: 资产类别支持
            source: 来源
            name: 策略显示名（来自 strategy.yaml）
            style: 策略风格（来自 strategy.yaml，如 MEAN_REVERSION）
        """
        self.strategy_id = strategy_id
        self.code_ref = code_ref
        self.code_content = code_content
        self.params_schema = params_schema
        self.data_dependencies = data_dependencies or []
        self.feature_dependencies = feature_dependencies or []
        self.runtime_constraints = runtime_constraints or {}
        self.asset_class_support = asset_class_support or [AssetClass.PERPETUAL]
        self.source = source
        self.name = name or strategy_id
        self.style = style or "OTHER"

        # 计算 content_hash
        self.content_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """计算代码内容哈希"""
        content = f"{self.code_ref}:{self.code_content}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


class StrategyRegistry:
    """策略注册表实现

    实现 StrategyRegistryPort 端口：
    - save_version: 保存策略版本（幂等）
    - get_version: 获取策略版本
    - list_versions: 列出所有版本
    - list_strategies: 列出所有策略

    TODO(M4): 接入 qps.infra.storage.meta_db.MetaDB 统一管理表结构与连接池，
    删除内部自建 DDL，复用 MetaDB 的 canonical 表定义与 insert-only 保护。
    """

    def __init__(self, db_path: Path | str) -> None:
        """初始化注册表

        Args:
            db_path: 数据库文件路径
        """
        self.db_path = Path(db_path)
        # 沙箱使用默认白名单，allow_imports 在 save_version 时从 code_pack 提取
        self._sandbox = Sandbox()
        # TODO(M4): 改用 MetaDB.initialize() 建表，删除 _init_db 自建 DDL
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """初始化数据库表"""
        conn = self._get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS strategies (
                    strategy_id       TEXT PRIMARY KEY,
                    name              TEXT NOT NULL,
                    description      TEXT,
                    style             TEXT NOT NULL,
                    lifecycle_status  TEXT NOT NULL DEFAULT 'DRAFT',
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
                    data_dependencies   TEXT NOT NULL DEFAULT '[]',
                    feature_dependencies TEXT NOT NULL DEFAULT '[]',
                    runtime_constraints TEXT NOT NULL DEFAULT '{}',
                    asset_class_support TEXT NOT NULL DEFAULT '["PERPETUAL"]',
                    content_hash        TEXT NOT NULL,
                    source              TEXT NOT NULL DEFAULT 'MANUAL',
                    created_at          TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version_no),
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE TABLE IF NOT EXISTS strategy_params (
                    strategy_id  TEXT NOT NULL,
                    version_no   INTEGER NOT NULL,
                    params       TEXT NOT NULL,
                    params_hash  TEXT NOT NULL,
                    PRIMARY KEY (strategy_id, version_no, params_hash),
                    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
                );

                CREATE INDEX IF NOT EXISTS idx_strategy_versions_strategy_id
                    ON strategy_versions(strategy_id);
            """)
            conn.commit()
        finally:
            conn.close()

    async def save_version(self, code_pack: dict[str, Any]) -> StrategyVersion:
        """保存策略版本（幂等）

        Args:
            code_pack: 策略代码包字典，包含 strategy_id, code_ref, code_content, params_schema 等

        Returns:
            StrategyVersion 实例

        Raises:
            DataError: 保存失败时抛出
        """
        # 从字典构造内部 StrategyCodePack（用于向后兼容和内部处理）
        internal_pack = StrategyCodePack(
            strategy_id=code_pack["strategy_id"],
            code_ref=code_pack.get("code_ref", "local:unknown"),
            code_content=code_pack["code_content"],
            params_schema=code_pack.get("params_schema", {}),
            data_dependencies=code_pack.get("data_dependencies", []),
            feature_dependencies=code_pack.get("feature_dependencies", []),
            runtime_constraints=code_pack.get("runtime_constraints", {}),
            name=code_pack.get("name"),
            style=code_pack.get("style"),
        )

        # 1. 检查是否已存在相同 content_hash
        existing = self._find_by_content_hash(internal_pack.strategy_id, internal_pack.content_hash)
        if existing:
            return existing

        # 2. 静态检查（使用 runtime_constraints.allow_imports 扩展白名单 + 策略包自导入放行）
        allowed_modules = set()
        if internal_pack.runtime_constraints:
            allowed_modules = set(internal_pack.runtime_constraints.get("allow_imports", []))

        # 创建带自定义白名单和策略包自导入放行的沙箱进行检查
        check_sandbox = Sandbox(
            allowed_modules=allowed_modules if allowed_modules else None,
            strategy_package=internal_pack.strategy_id,
        )
        sandbox_result = check_sandbox.check(internal_pack.code_content)
        if not sandbox_result.passed:
            raise DataError(
                detail=f"静态检查失败: {json.dumps(sandbox_result.to_dict())}",
            )

        # 3. params_schema 校验（验证 schema 结构本身）
        from qps.research.yaml_parser import validate_schema_structure

        try:
            validate_schema_structure(internal_pack.params_schema)
        except Exception as e:
            raise DataError(detail=f"params_schema 校验失败: {e}") from e

        # 4. 获取或创建策略主记录（传入 strategy.yaml 的 name/style）
        self._ensure_strategy(
            internal_pack.strategy_id,
            name=internal_pack.name,
            style=internal_pack.style,
        )

        # 5. 获取下一个版本号
        version_no = self._next_version_no(internal_pack.strategy_id)

        # 6. 插入版本记录
        conn = self._get_connection()
        try:
            now = datetime.now(UTC).isoformat()
            conn.execute(
                """INSERT INTO strategy_versions
                   (strategy_id, version_no, code_ref, params_schema,
                    data_dependencies, feature_dependencies, runtime_constraints,
                    asset_class_support, content_hash, source, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    internal_pack.strategy_id,
                    version_no,
                    internal_pack.code_ref,
                    json.dumps(internal_pack.params_schema),
                    json.dumps(internal_pack.data_dependencies),
                    json.dumps(internal_pack.feature_dependencies),
                    json.dumps(internal_pack.runtime_constraints),
                    json.dumps([ac.value for ac in internal_pack.asset_class_support]),
                    internal_pack.content_hash,
                    internal_pack.source.value,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        # 返回强类型模型
        return StrategyVersion(
            strategy_id=internal_pack.strategy_id,
            version_no=version_no,
            code_ref=internal_pack.code_ref,
            params_schema=internal_pack.params_schema,
            data_dependencies=internal_pack.data_dependencies,
            feature_dependencies=internal_pack.feature_dependencies,
            runtime_constraints=internal_pack.runtime_constraints,
            asset_class_support=internal_pack.asset_class_support,
            content_hash=internal_pack.content_hash,
            source=internal_pack.source,
            created_at=datetime.fromisoformat(now),
        )

    async def get_version(self, strategy_id: str, version_no: int) -> StrategyVersion:
        """获取策略版本

        Args:
            strategy_id: 策略 ID
            version_no: 版本号

        Returns:
            StrategyVersion 实例

        Raises:
            DataError: 版本不存在时抛出
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT * FROM strategy_versions
                   WHERE strategy_id = ? AND version_no = ?""",
                (strategy_id, version_no),
            ).fetchone()

            if row is None:
                raise DataError(detail=f"版本不存在: {strategy_id}@{version_no}")

            return self._row_to_version_model(row)
        finally:
            conn.close()

    async def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        """列出策略的所有版本

        Args:
            strategy_id: 策略 ID

        Returns:
            版本列表（按版本号升序）
        """
        conn = self._get_connection()
        try:
            rows = conn.execute(
                """SELECT * FROM strategy_versions
                   WHERE strategy_id = ?
                   ORDER BY version_no ASC""",
                (strategy_id,),
            ).fetchall()

            return [self._row_to_version_model(row) for row in rows]
        finally:
            conn.close()

    async def list_strategies(self, status: str | None = None) -> list[Strategy]:
        """列出所有策略

        Args:
            status: 按状态过滤（可选）

        Returns:
            策略列表
        """
        conn = self._get_connection()
        try:
            if status:
                rows = conn.execute(
                    """SELECT * FROM strategies
                       WHERE lifecycle_status = ?
                       ORDER BY created_at DESC""",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM strategies
                       ORDER BY created_at DESC"""
                ).fetchall()

            return [self._row_to_strategy_model(row) for row in rows]
        finally:
            conn.close()

    def _row_to_version_model(self, row: sqlite3.Row) -> StrategyVersion:
        """将数据库行转换为 StrategyVersion 模型"""
        from qps.core.constants import AssetClass, StrategySource

        return StrategyVersion(
            strategy_id=row["strategy_id"],
            version_no=row["version_no"],
            code_ref=row["code_ref"],
            params_schema=json.loads(row["params_schema"]),
            data_dependencies=json.loads(row["data_dependencies"]),
            feature_dependencies=json.loads(row["feature_dependencies"]),
            runtime_constraints=json.loads(row["runtime_constraints"]),
            asset_class_support=[AssetClass(ac) for ac in json.loads(row["asset_class_support"])],
            content_hash=row["content_hash"],
            source=StrategySource(row["source"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def _row_to_strategy_model(self, row: sqlite3.Row) -> Strategy:
        """将数据库行转换为 Strategy 模型"""
        from qps.core.constants import StrategyLifecycleStatus, StrategySource, StrategyStyle

        return Strategy(
            strategy_id=row["strategy_id"],
            name=row["name"],
            description=row["description"],
            style=StrategyStyle(row["style"]),
            lifecycle_status=StrategyLifecycleStatus(row["lifecycle_status"]),
            tags=json.loads(row["tags"]) if row["tags"] else [],
            account_binding=row["account_binding"],
            source=StrategySource(row["source"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _find_by_content_hash(self, strategy_id: str, content_hash: str) -> StrategyVersion | None:
        """根据 content_hash 查找版本（用于幂等）"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT * FROM strategy_versions
                   WHERE strategy_id = ? AND content_hash = ?""",
                (strategy_id, content_hash),
            ).fetchone()

            if row:
                return self._row_to_version_model(row)
            return None
        finally:
            conn.close()

    def _ensure_strategy(
        self,
        strategy_id: str,
        name: str | None = None,
        style: str | None = None,
    ) -> None:
        """确保策略主记录存在

        Args:
            strategy_id: 策略 ID
            name: 策略显示名（来自 strategy.yaml，默认用 strategy_id）
            style: 策略风格（来自 strategy.yaml，默认 OTHER）
        """
        conn = self._get_connection()
        try:
            row = conn.execute(
                "SELECT strategy_id FROM strategies WHERE strategy_id = ?",
                (strategy_id,),
            ).fetchone()

            if row is None:
                now = datetime.now(UTC).isoformat()
                conn.execute(
                    """INSERT INTO strategies
                       (strategy_id, name, description, style, lifecycle_status,
                        tags, account_binding, source, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (strategy_id, name or strategy_id, "", style or "OTHER", "DRAFT",
                     "[]", None, "MANUAL", now, now),
                )
                conn.commit()
        finally:
            conn.close()

    def _next_version_no(self, strategy_id: str) -> int:
        """获取下一个版本号"""
        conn = self._get_connection()
        try:
            row = conn.execute(
                """SELECT MAX(version_no) as max_ver
                   FROM strategy_versions
                   WHERE strategy_id = ?""",
                (strategy_id,),
            ).fetchone()

            max_ver = row["max_ver"] if row and row["max_ver"] else 0
            return max_ver + 1
        finally:
            conn.close()
