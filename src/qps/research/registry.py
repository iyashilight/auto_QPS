"""Durable strategy registry backed by the canonical MetaDB schema."""

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
from qps.infra.storage.meta_db import MetaDB


class StrategyCodePack:
    """Validated, deterministic strategy package input."""

    def __init__(self, strategy_id: str, code_ref: str, code_content: str,
                 params_schema: dict[str, Any], data_dependencies: list[str] | None = None,
                 feature_dependencies: list[str] | None = None,
                 runtime_constraints: dict[str, Any] | None = None,
                 asset_class_support: list[AssetClass] | None = None,
                 source: StrategySource = StrategySource.MANUAL, name: str | None = None,
                 style: str | None = None) -> None:
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

    @property
    def content_hash(self) -> str:
        payload = {
            "code_ref": self.code_ref,
            "code_content": self.code_content,
            "params_schema": self.params_schema,
            "data_dependencies": self.data_dependencies,
            "feature_dependencies": self.feature_dependencies,
            "runtime_constraints": self.runtime_constraints,
            "asset_class_support": [item.value for item in self.asset_class_support],
        }
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


class StrategyRegistry:
    """StrategyRegistryPort implementation with no private strategy DDL."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self._sandbox = Sandbox()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        MetaDB(self.db_path).initialize()
        conn = self._get_connection()
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(strategy_versions)")}
            if "code_content" not in columns:
                conn.execute("ALTER TABLE strategy_versions ADD COLUMN code_content TEXT NOT NULL DEFAULT ''")
            conn.commit()
        finally:
            conn.close()

    async def save_version(self, code_pack: dict[str, Any]) -> StrategyVersion:
        try:
            pack = StrategyCodePack(
                strategy_id=code_pack["strategy_id"], code_ref=code_pack.get("code_ref", "local:unknown"),
                code_content=code_pack["code_content"], params_schema=code_pack.get("params_schema", {}),
                data_dependencies=code_pack.get("data_dependencies", []),
                feature_dependencies=code_pack.get("feature_dependencies", []),
                runtime_constraints=code_pack.get("runtime_constraints", {}),
                asset_class_support=code_pack.get("asset_class_support"),
                name=code_pack.get("name"), style=code_pack.get("style"),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise DataError(detail=f"策略代码包格式错误: {exc}") from exc
        existing = self._find_by_content_hash(pack.strategy_id, pack.content_hash)
        if existing is not None:
            return existing
        allowed = set(pack.runtime_constraints.get("allow_imports", []))
        result = Sandbox(allowed_modules=allowed or None, strategy_package=pack.strategy_id).check(pack.code_content)
        if not result.passed:
            raise DataError(detail=f"静态检查失败: {json.dumps(result.to_dict())}")
        from qps.research.yaml_parser import validate_schema_structure
        try:
            validate_schema_structure(pack.params_schema)
        except Exception as exc:
            raise DataError(detail=f"params_schema 校验失败: {exc}") from exc
        self._ensure_strategy(pack.strategy_id, pack.name, pack.style)
        version_no = self._next_version_no(pack.strategy_id)
        created_at = datetime.now(UTC).isoformat()
        conn = self._get_connection()
        try:
            conn.execute(
                "INSERT INTO strategy_versions (strategy_id, version_no, code_ref, code_content, params_schema, "
                "data_dependencies, feature_dependencies, runtime_constraints, asset_class_support, content_hash, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (pack.strategy_id, version_no, pack.code_ref, pack.code_content,
                 self._json(pack.params_schema), self._json(pack.data_dependencies),
                 self._json(pack.feature_dependencies), self._json(pack.runtime_constraints),
                 self._json([item.value for item in pack.asset_class_support]), pack.content_hash,
                 pack.source.value, created_at),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DataError(detail=f"策略版本写入冲突: {exc}") from exc
        finally:
            conn.close()
        return StrategyVersion(strategy_id=pack.strategy_id, version_no=version_no, code_ref=pack.code_ref,
            code_content=pack.code_content, params_schema=pack.params_schema,
            data_dependencies=pack.data_dependencies, feature_dependencies=pack.feature_dependencies,
            runtime_constraints=pack.runtime_constraints, asset_class_support=pack.asset_class_support,
            content_hash=pack.content_hash, source=pack.source, created_at=datetime.fromisoformat(created_at))

    async def get_version(self, strategy_id: str, version_no: int) -> StrategyVersion:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM strategy_versions WHERE strategy_id=? AND version_no=?",
                               (strategy_id, version_no)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise DataError(detail=f"版本不存在: {strategy_id}@{version_no}")
        return self._row_to_version(row)

    async def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        conn = self._get_connection()
        try:
            rows = conn.execute("SELECT * FROM strategy_versions WHERE strategy_id=? ORDER BY version_no",
                                (strategy_id,)).fetchall()
        finally:
            conn.close()
        return [self._row_to_version(row) for row in rows]

    async def list_strategies(self, status: str | None = None) -> list[Strategy]:
        conn = self._get_connection()
        try:
            query = "SELECT * FROM strategies"
            args: tuple[str, ...] = ()
            if status:
                query += " WHERE lifecycle_status=?"
                args = (status,)
            rows = conn.execute(query + " ORDER BY created_at DESC", args).fetchall()
        finally:
            conn.close()
        from qps.core.constants import StrategyLifecycleStatus, StrategyStyle
        return [Strategy(strategy_id=row["strategy_id"], name=row["name"], description=row["description"],
            style=StrategyStyle(row["style"]), lifecycle_status=StrategyLifecycleStatus(row["lifecycle_status"]),
            tags=json.loads(row["tags"]) if row["tags"] else [], account_binding=row["account_binding"],
            source=StrategySource(row["source"]), created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"])) for row in rows]

    def _find_by_content_hash(self, strategy_id: str, content_hash: str) -> StrategyVersion | None:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT * FROM strategy_versions WHERE strategy_id=? AND content_hash=?",
                               (strategy_id, content_hash)).fetchone()
        finally:
            conn.close()
        return None if row is None else self._row_to_version(row)

    def _ensure_strategy(self, strategy_id: str, name: str, style: str) -> None:
        now = datetime.now(UTC).isoformat()
        conn = self._get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO strategies (strategy_id,name,style,source,created_at,updated_at,tags) VALUES (?,?,?,?,?,?,?)",
                         (strategy_id, name, style, StrategySource.MANUAL.value, now, now, "[]"))
            conn.commit()
        finally:
            conn.close()

    def _next_version_no(self, strategy_id: str) -> int:
        conn = self._get_connection()
        try:
            row = conn.execute("SELECT COALESCE(MAX(version_no),0)+1 FROM strategy_versions WHERE strategy_id=?",
                               (strategy_id,)).fetchone()
        finally:
            conn.close()
        return int(row[0])

    @staticmethod
    def _json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> StrategyVersion:
        return StrategyVersion(strategy_id=row["strategy_id"], version_no=row["version_no"],
            code_ref=row["code_ref"], code_content=row["code_content"],
            params_schema=json.loads(row["params_schema"]), data_dependencies=json.loads(row["data_dependencies"]),
            feature_dependencies=json.loads(row["feature_dependencies"]), runtime_constraints=json.loads(row["runtime_constraints"]),
            asset_class_support=[AssetClass(item) for item in json.loads(row["asset_class_support"])],
            content_hash=row["content_hash"], source=StrategySource(row["source"]),
            created_at=datetime.fromisoformat(row["created_at"]))
