"""StrategyRegistry 入库链路测试（见 docs/03 第 9 节）

测试 save_version 幂等、静态检查拒绝、params_schema 校验、强类型模型返回。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from qps.core.errors import DataError
from qps.core.ports.registry import StrategyRegistryPort
from qps.research.registry import StrategyRegistry


class TestSaveVersion:
    """save_version 测试"""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> StrategyRegistry:
        """创建注册表实例"""
        db_path = tmp_path / "test_meta.db"
        return StrategyRegistry(db_path)

    @pytest.fixture
    def code_pack_dict(self) -> dict[str, Any]:
        """创建代码包字典"""
        return {
            "strategy_id": "test_strategy",
            "code_ref": "local:test_strategy",
            "code_content": '''
from __future__ import annotations
from qps.core.sdk import BaseStrategy, StrategyContext

class Strategy(BaseStrategy):
    def initialize(self, ctx: StrategyContext) -> None:
        pass
    def on_bar(self, ctx: StrategyContext, bar) -> None:
        pass
''',
            "params_schema": {
                "type": "object",
                "properties": {},
            },
        }

    @pytest.mark.asyncio
    async def test_save_version_success(self, registry: StrategyRegistry, code_pack_dict: dict[str, Any]) -> None:
        """成功保存版本"""
        result = await registry.save_version(code_pack_dict)
        assert result.strategy_id == "test_strategy"
        assert result.version_no == 1
        assert len(result.content_hash) > 0
        assert result.created_at is not None

    @pytest.mark.asyncio
    async def test_save_version_idempotent(self, registry: StrategyRegistry, code_pack_dict: dict[str, Any]) -> None:
        """同 content_hash 幂等"""
        # 第一次保存
        result1 = await registry.save_version(code_pack_dict)
        # 第二次保存（相同内容）
        result2 = await registry.save_version(code_pack_dict)
        # 应该返回相同版本号
        assert result1.version_no == result2.version_no
        assert result1.content_hash == result2.content_hash

    @pytest.mark.asyncio
    async def test_save_version_increments(self, registry: StrategyRegistry) -> None:
        """不同内容版本号递增"""
        # 创建第一个版本
        code_pack1 = {
            "strategy_id": "test_strategy",
            "code_ref": "v1",
            "code_content": "pass",
            "params_schema": {"type": "object", "properties": {}},
        }
        result1 = await registry.save_version(code_pack1)

        # 创建第二个版本（不同内容）
        code_pack2 = {
            "strategy_id": "test_strategy",
            "code_ref": "v2",
            "code_content": "pass\n# modified",
            "params_schema": {"type": "object", "properties": {}},
        }
        result2 = await registry.save_version(code_pack2)
        assert result2.version_no == result1.version_no + 1

    @pytest.mark.asyncio
    async def test_save_version_invalid_schema_rejected(self, registry: StrategyRegistry) -> None:
        """无效 params_schema 拒绝入库"""
        code_pack = {
            "strategy_id": "invalid_schema",
            "code_ref": "local:invalid_schema",
            "code_content": "pass",
            "params_schema": {"invalid": "schema"},  # 缺少 type: object
        }
        with pytest.raises(DataError) as exc_info:
            await registry.save_version(code_pack)
        assert "params_schema 校验失败" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_save_version_allow_imports_respected(self, registry: StrategyRegistry) -> None:
        """allow_imports 声明生效"""
        code_pack = {
            "strategy_id": "custom_imports",
            "code_ref": "local:custom_imports",
            "code_content": "import pandas as pd",
            "params_schema": {"type": "object", "properties": {}},
            "runtime_constraints": {"allow_imports": ["pandas"]},
        }
        # 应该通过，因为 pandas 在 allow_imports 中
        result = await registry.save_version(code_pack)
        assert result.version_no == 1

    @pytest.mark.asyncio
    async def test_save_version_forbidden_import_rejected(self, registry: StrategyRegistry) -> None:
        """禁止导入模块被拒绝"""
        code_pack = {
            "strategy_id": "forbidden_import",
            "code_ref": "local:forbidden_import",
            "code_content": "import socket",
            "params_schema": {"type": "object", "properties": {}},
        }
        with pytest.raises(DataError) as exc_info:
            await registry.save_version(code_pack)
        assert "静态检查失败" in str(exc_info.value)


class TestGetVersion:
    """get_version 测试"""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> StrategyRegistry:
        """创建注册表实例"""
        db_path = tmp_path / "test_meta.db"
        return StrategyRegistry(db_path)

    @pytest.fixture
    def saved_version(self, registry: StrategyRegistry) -> Any:
        """保存一个版本"""
        code_pack = {
            "strategy_id": "test_strategy",
            "code_ref": "local:test_strategy",
            "code_content": "pass",
            "params_schema": {"type": "object", "properties": {}},
        }
        return asyncio.run(registry.save_version(code_pack))

    @pytest.mark.asyncio
    async def test_get_version_success(self, registry: StrategyRegistry, saved_version: Any) -> None:
        """成功获取版本"""
        result = await registry.get_version("test_strategy", saved_version.version_no)
        assert result.strategy_id == "test_strategy"
        assert result.version_no == saved_version.version_no
        assert result.params_schema is not None

    @pytest.mark.asyncio
    async def test_get_version_not_found(self, registry: StrategyRegistry) -> None:
        """版本不存在抛出 DataError"""
        with pytest.raises(DataError) as exc_info:
            await registry.get_version("nonexistent", 1)
        assert "版本不存在" in str(exc_info.value)


class TestListVersions:
    """list_versions 测试"""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> StrategyRegistry:
        """创建注册表实例"""
        db_path = tmp_path / "test_meta.db"
        return StrategyRegistry(db_path)

    @pytest.mark.asyncio
    async def test_list_versions_empty(self, registry: StrategyRegistry) -> None:
        """空策略返回空列表"""
        result = await registry.list_versions("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_versions_sorted(self, registry: StrategyRegistry) -> None:
        """版本按版本号升序排列"""
        # 保存多个版本
        code_pack1 = {
            "strategy_id": "test_strategy",
            "code_ref": "v1",
            "code_content": "v1",
            "params_schema": {"type": "object", "properties": {}},
        }
        await registry.save_version(code_pack1)

        code_pack2 = {
            "strategy_id": "test_strategy",
            "code_ref": "v2",
            "code_content": "v2",
            "params_schema": {"type": "object", "properties": {}},
        }
        await registry.save_version(code_pack2)

        result = await registry.list_versions("test_strategy")
        assert len(result) == 2
        assert result[0].version_no < result[1].version_no


class TestListStrategies:
    """list_strategies 测试"""

    @pytest.fixture
    def registry(self, tmp_path: Path) -> StrategyRegistry:
        """创建注册表实例"""
        db_path = tmp_path / "test_meta.db"
        return StrategyRegistry(db_path)

    @pytest.mark.asyncio
    async def test_list_strategies_empty(self, registry: StrategyRegistry) -> None:
        """空数据库返回空列表"""
        result = await registry.list_strategies()
        assert result == []

    @pytest.mark.asyncio
    async def test_list_strategies_with_status(self, registry: StrategyRegistry) -> None:
        """按状态过滤"""
        code_pack = {
            "strategy_id": "test_strategy",
            "code_ref": "v1",
            "code_content": "pass",
            "params_schema": {"type": "object", "properties": {}},
        }
        await registry.save_version(code_pack)

        result = await registry.list_strategies()
        assert len(result) >= 1
        assert result[0].strategy_id == "test_strategy"


class TestProtocolConformance:
    """协议一致性测试"""

    def test_registry_is_protocol_instance(self, tmp_path: Path) -> None:
        """StrategyRegistry 是 StrategyRegistryPort 的实例"""
        db_path = tmp_path / "test_meta.db"
        registry = StrategyRegistry(db_path)
        assert isinstance(registry, StrategyRegistryPort)
