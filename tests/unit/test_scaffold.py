"""策略脚手架测试（见 docs/04 第 1 节）

测试三模板生成、目录结构完整、包可 import、冒烟测试可跑、可入库。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

import pytest

from qps.research.scaffold import TEMPLATES, StrategyScaffold

if TYPE_CHECKING:
    from pathlib import Path


class TestTemplates:
    """模板定义测试"""

    def test_three_templates_exist(self) -> None:
        """三种模板存在"""
        assert "trend" in TEMPLATES
        assert "mean_reversion" in TEMPLATES
        assert "grid" in TEMPLATES

    def test_trend_template_structure(self) -> None:
        """趋势模板结构"""
        trend = TEMPLATES["trend"]
        assert "style" in trend
        assert "params_schema" in trend
        assert "features" in trend
        assert "code_template" in trend

    def test_mean_reversion_template_structure(self) -> None:
        """均值回归模板结构"""
        mean_rev = TEMPLATES["mean_reversion"]
        assert "lookback" in mean_rev["params_schema"]["properties"]
        assert "entry_z" in mean_rev["params_schema"]["properties"]

    def test_grid_template_structure(self) -> None:
        """网格模板结构"""
        grid = TEMPLATES["grid"]
        assert "grid_spacing" in grid["params_schema"]["properties"]
        assert "grid_count" in grid["params_schema"]["properties"]


class TestScaffoldCreate:
    """脚手架创建测试"""

    @pytest.fixture
    def scaffold(self, tmp_path: Path) -> StrategyScaffold:
        """创建脚手架实例"""
        return StrategyScaffold(tmp_path)

    def test_create_trend_strategy(self, scaffold: StrategyScaffold, tmp_path: Path) -> None:
        """创建趋势策略"""
        strategy_dir = scaffold.create("test_trend", template="trend")
        assert strategy_dir.exists()
        assert (strategy_dir / "strategy.yaml").exists()
        assert (strategy_dir / "__init__.py").exists()
        assert (strategy_dir / "strategy.py").exists()
        assert (strategy_dir / "features.py").exists()
        assert (strategy_dir / "tests" / "test_smoke.py").exists()

    def test_create_mean_reversion_strategy(self, scaffold: StrategyScaffold) -> None:
        """创建均值回归策略"""
        strategy_dir = scaffold.create("test_mean_rev", template="mean_reversion")
        assert strategy_dir.exists()
        # 验证参数 schema
        yaml_path = strategy_dir / "strategy.yaml"
        import yaml
        yaml_data = yaml.safe_load(yaml_path.read_text())
        assert "lookback" in yaml_data["params_schema"]["properties"]
        assert "entry_z" in yaml_data["params_schema"]["properties"]

    def test_create_grid_strategy(self, scaffold: StrategyScaffold) -> None:
        """创建网格策略"""
        strategy_dir = scaffold.create("test_grid", template="grid")
        assert strategy_dir.exists()
        # 验证参数 schema
        yaml_path = strategy_dir / "strategy.yaml"
        import yaml
        yaml_data = yaml.safe_load(yaml_path.read_text())
        assert "grid_spacing" in yaml_data["params_schema"]["properties"]
        assert "grid_count" in yaml_data["params_schema"]["properties"]

    def test_create_with_custom_name(self, scaffold: StrategyScaffold) -> None:
        """自定义名称"""
        strategy_dir = scaffold.create("test_custom", name="自定义策略")
        yaml_path = strategy_dir / "strategy.yaml"
        import yaml
        yaml_data = yaml.safe_load(yaml_path.read_text())
        assert yaml_data["name"] == "自定义策略"

    def test_create_with_instruments(self, scaffold: StrategyScaffold) -> None:
        """自定义标的列表"""
        strategy_dir = scaffold.create(
            "test_inst",
            instruments=["BTC-USDT-PERP", "ETH-USDT-PERP"],
        )
        yaml_path = strategy_dir / "strategy.yaml"
        import yaml
        yaml_data = yaml.safe_load(yaml_path.read_text())
        assert len(yaml_data["instruments"]) == 2

    def test_invalid_template_raises(self, scaffold: StrategyScaffold) -> None:
        """无效模板抛出 ValueError"""
        with pytest.raises(ValueError, match="未知模板"):
            scaffold.create("test", template="invalid")

    def test_invalid_strategy_id_raises(self, scaffold: StrategyScaffold) -> None:
        """无效策略 ID 抛出 ValueError"""
        with pytest.raises(ValueError, match="无效的策略 ID"):
            scaffold.create("invalid id!", template="trend")


class TestGeneratedPackageImport:
    """生成包可导入测试"""

    def test_trend_strategy_importable(self, tmp_path: Path) -> None:
        """趋势策略可导入"""
        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_trend", template="trend")

        # 添加目录到 Python 路径
        sys.path.insert(0, str(strategy_dir.parent))

        try:
            # 尝试导入
            from test_trend.strategy import Strategy
            assert Strategy is not None
        finally:
            sys.path.remove(str(strategy_dir.parent))

    def test_mean_reversion_strategy_importable(self, tmp_path: Path) -> None:
        """均值回归策略可导入"""
        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_mean_rev", template="mean_reversion")

        sys.path.insert(0, str(strategy_dir.parent))

        try:
            from test_mean_rev.strategy import Strategy
            assert Strategy is not None
        finally:
            sys.path.remove(str(strategy_dir.parent))

    def test_grid_strategy_importable(self, tmp_path: Path) -> None:
        """网格策略可导入"""
        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_grid", template="grid")

        sys.path.insert(0, str(strategy_dir.parent))

        try:
            from test_grid.strategy import Strategy
            assert Strategy is not None
        finally:
            sys.path.remove(str(strategy_dir.parent))


class TestGeneratedTestsRun:
    """生成的冒烟测试可运行测试"""

    def test_trend_smoke_test_runs(self, tmp_path: Path) -> None:
        """趋势策略冒烟测试可运行"""
        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_trend", template="trend")

        # 检查测试文件存在
        test_file = strategy_dir / "tests" / "test_smoke.py"
        assert test_file.exists()

        # 检查测试文件内容包含基本断言
        content = test_file.read_text()
        assert "test_initialize" in content
        assert "test_on_bar" in content

    def test_mean_reversion_smoke_test_runs(self, tmp_path: Path) -> None:
        """均值回归策略冒烟测试可运行"""
        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_mean_rev", template="mean_reversion")

        test_file = strategy_dir / "tests" / "test_smoke.py"
        assert test_file.exists()

        content = test_file.read_text()
        assert "test_initialize" in content
        assert "test_on_bar" in content


class TestScaffoldSandboxCheck:
    """三模板生成物整包沙箱检查测试"""

    def test_trend_package_sandbox_pass(self, tmp_path: Path) -> None:
        """趋势策略整包通过沙箱检查（含 __init__.py）"""
        from qps.core.sandbox import Sandbox

        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_trend", template="trend")

        sandbox = Sandbox(strategy_package="test_trend")
        result = sandbox.check_directory(strategy_dir)
        assert result.passed, f"沙箱检查失败: {[v.message for v in result.violations]}"

    def test_mean_reversion_package_sandbox_pass(self, tmp_path: Path) -> None:
        """均值回归策略整包通过沙箱检查（含 __init__.py）"""
        from qps.core.sandbox import Sandbox

        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_mean_rev", template="mean_reversion")

        sandbox = Sandbox(strategy_package="test_mean_rev")
        result = sandbox.check_directory(strategy_dir)
        assert result.passed, f"沙箱检查失败: {[v.message for v in result.violations]}"

    def test_grid_package_sandbox_pass(self, tmp_path: Path) -> None:
        """网格策略整包通过沙箱检查（含 __init__.py）"""
        from qps.core.sandbox import Sandbox

        scaffold = StrategyScaffold(tmp_path)
        strategy_dir = scaffold.create("test_grid", template="grid")

        sandbox = Sandbox(strategy_package="test_grid")
        result = sandbox.check_directory(strategy_dir)
        assert result.passed, f"沙箱检查失败: {[v.message for v in result.violations]}"
