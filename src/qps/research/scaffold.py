"""策略脚手架生成器（见 docs/04 第 1 节）

生成策略的标准目录结构和模板文件。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from qps.core.constants import AssetClass, StrategyStyle

# ── 模板定义 ────────────────────────────────────────────────

TEMPLATES: dict[str, dict[str, Any]] = {
    "trend": {
        "style": StrategyStyle.TREND,
        "description": "趋势跟踪策略",
        "params_schema": {
            "type": "object",
            "required": ["lookback", "atr_mult", "position_size"],
            "properties": {
                "lookback": {
                    "type": "integer",
                    "minimum": 20,
                    "maximum": 200,
                    "default": 50,
                    "step": 10,
                    "description": "通道回看周期",
                },
                "atr_mult": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 5.0,
                    "default": 2.0,
                    "step": 0.1,
                    "description": "ATR 倍数",
                },
                "position_size": {
                    "type": "number",
                    "minimum": 0.01,
                    "maximum": 0.5,
                    "default": 0.1,
                    "step": 0.01,
                    "description": "仓位大小",
                },
            },
        },
        "features": ["atr_14", "ema_50"],
        "code_template": '''"""趋势跟踪策略"""

from qps.core.sdk import BaseStrategy, StrategyContext
from qps.core.models import Bar


class Strategy(BaseStrategy):
    """通道突破趋势策略"""

    def initialize(self, ctx: StrategyContext) -> None:
        """初始化"""
        self.lookback = ctx.params.get("lookback", 50)
        self.atr_mult = ctx.params.get("atr_mult", 2.0)
        self.position_size = ctx.params.get("position_size", 0.1)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """K 线回调"""
        # TODO: 实现趋势逻辑
        pass
''',
    },
    "mean_reversion": {
        "style": StrategyStyle.MEAN_REVERSION,
        "description": "均值回归策略",
        "params_schema": {
            "type": "object",
            "required": ["lookback", "entry_z", "exit_z", "position_size"],
            "properties": {
                "lookback": {
                    "type": "integer",
                    "minimum": 20,
                    "maximum": 200,
                    "default": 50,
                    "step": 10,
                    "description": "均线回看周期",
                },
                "entry_z": {
                    "type": "number",
                    "minimum": 1.0,
                    "maximum": 3.0,
                    "default": 2.0,
                    "step": 0.1,
                    "description": "入场 Z-Score 阈值",
                },
                "exit_z": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "default": 0.5,
                    "step": 0.1,
                    "description": "出场 Z-Score 阈值",
                },
                "position_size": {
                    "type": "number",
                    "minimum": 0.01,
                    "maximum": 0.5,
                    "default": 0.1,
                    "step": 0.01,
                    "description": "仓位大小",
                },
            },
        },
        "features": ["returns", "realized_vol_20"],
        "code_template": '''"""均值回归策略"""

from qps.core.sdk import BaseStrategy, StrategyContext
from qps.core.models import Bar


class Strategy(BaseStrategy):
    """均值回归策略"""

    def initialize(self, ctx: StrategyContext) -> None:
        """初始化"""
        self.lookback = ctx.params.get("lookback", 50)
        self.entry_z = ctx.params.get("entry_z", 2.0)
        self.exit_z = ctx.params.get("exit_z", 0.5)
        self.position_size = ctx.params.get("position_size", 0.1)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """K 线回调"""
        # TODO: 实现均值回归逻辑
        pass
''',
    },
    "grid": {
        "style": StrategyStyle.GRID,
        "description": "网格交易策略",
        "params_schema": {
            "type": "object",
            "required": ["grid_spacing", "grid_count", "position_size"],
            "properties": {
                "grid_spacing": {
                    "type": "number",
                    "minimum": 0.001,
                    "maximum": 0.1,
                    "default": 0.01,
                    "step": 0.001,
                    "description": "网格间距（百分比）",
                },
                "grid_count": {
                    "type": "integer",
                    "minimum": 5,
                    "maximum": 50,
                    "default": 10,
                    "step": 1,
                    "description": "网格数量",
                },
                "position_size": {
                    "type": "number",
                    "minimum": 0.01,
                    "maximum": 0.5,
                    "default": 0.1,
                    "step": 0.01,
                    "description": "每格仓位大小",
                },
            },
        },
        "features": [],
        "code_template": '''"""网格交易策略"""

from qps.core.sdk import BaseStrategy, StrategyContext
from qps.core.models import Bar


class Strategy(BaseStrategy):
    """网格交易策略"""

    def initialize(self, ctx: StrategyContext) -> None:
        """初始化"""
        self.grid_spacing = ctx.params.get("grid_spacing", 0.01)
        self.grid_count = ctx.params.get("grid_count", 10)
        self.position_size = ctx.params.get("position_size", 0.1)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """K 线回调"""
        # TODO: 实现网格逻辑
        pass
''',
    },
}


class StrategyScaffold:
    """策略脚手架生成器"""

    def __init__(self, strategies_dir: Path | str) -> None:
        """初始化

        Args:
            strategies_dir: 策略目录路径（应为仓库根目录下的 strategies/）
        """
        self.strategies_dir = Path(strategies_dir)

    def create(
        self,
        strategy_id: str,
        template: str = "trend",
        name: str | None = None,
        instruments: list[str] | None = None,
    ) -> Path:
        """创建策略脚手架

        Args:
            strategy_id: 策略 ID（与目录名一致）
            template: 模板类型（trend/mean_reversion/grid）
            name: 显示名（可选，默认使用 template 的描述）
            instruments: 标的列表（可选，默认 ["BTC-USDT-PERP"]）

        Returns:
            策略目录路径

        Raises:
            ValueError: 模板不存在或策略 ID 无效
        """
        if template not in TEMPLATES:
            raise ValueError(f"未知模板: {template}，支持: {list(TEMPLATES.keys())}")

        # 验证策略 ID
        if not strategy_id or not strategy_id.replace("_", "").isalnum():
            raise ValueError(f"无效的策略 ID: {strategy_id}，只允许字母、数字和下划线")

        template_data = TEMPLATES[template]

        # 创建目录结构
        strategy_dir = self.strategies_dir / strategy_id
        tests_dir = strategy_dir / "tests"
        strategy_dir.mkdir(parents=True, exist_ok=True)
        tests_dir.mkdir(exist_ok=True)

        # 生成 strategy.yaml
        yaml_content = self._generate_yaml(strategy_id, name, template_data, instruments)
        (strategy_dir / "strategy.yaml").write_text(yaml_content, encoding="utf-8")

        # 生成 __init__.py
        init_content = f'"""{template_data["description"]}策略"""\n\nfrom {strategy_id}.strategy import Strategy\n\n__all__ = ["Strategy"]\n'
        (strategy_dir / "__init__.py").write_text(init_content, encoding="utf-8")

        # 生成 strategy.py
        strategy_content = template_data["code_template"]
        (strategy_dir / "strategy.py").write_text(strategy_content, encoding="utf-8")

        # 生成 features.py（可选）
        features_content = '"""策略私有特征函数"""\n\n# TODO: 注册私有特征到 Feature Pipeline\n'
        (strategy_dir / "features.py").write_text(features_content, encoding="utf-8")

        # 生成测试模板
        test_content = self._generate_test(strategy_id, template_data)
        (tests_dir / "test_smoke.py").write_text(test_content, encoding="utf-8")

        return strategy_dir

    def _generate_yaml(
        self,
        strategy_id: str,
        name: str | None,
        template_data: dict[str, Any],
        instruments: list[str] | None,
    ) -> str:
        """生成 strategy.yaml 内容"""
        display_name = name or template_data["description"]
        default_instruments = instruments or ["BTC-USDT-PERP"]

        import yaml as yaml_lib

        yaml_dict = {
            "strategy_id": strategy_id,
            "name": display_name,
            "style": template_data["style"].value,
            "instruments": default_instruments,
            "interval": "5m",
            "asset_class_support": [AssetClass.PERPETUAL.value],
            "params_schema": template_data["params_schema"],
            "features": template_data["features"],
            "runtime_constraints": {
                "allow_network": False,
                "allow_file_io": False,
                "allow_threads": False,
                "allow_subprocess": False,
                "allow_imports": ["numpy", "polars", "qps.core.models", "qps.core.sdk"],
            },
        }

        return str(yaml_lib.dump(yaml_dict, allow_unicode=True, sort_keys=False))

    def _generate_test(self, strategy_id: str, template_data: dict[str, Any]) -> str:
        """生成冒烟测试模板"""
        return f'''"""冒烟测试 - {template_data["description"]}"""

import pytest
from datetime import UTC, datetime
from decimal import Decimal

from {strategy_id}.strategy import Strategy
from qps.core.sdk import StrategyContext
from qps.core.models import Bar


def make_bar(close: float, open_time: datetime | None = None) -> Bar:
    """创建测试 K 线"""
    if open_time is None:
        open_time = datetime(2026, 1, 1, tzinfo=UTC)
    return Bar(
        instrument_id="binance-btc-usdt-perp",
        open_time=open_time,
        interval="5m",
        open=Decimal(str(close - 100)),
        high=Decimal(str(close + 100)),
        low=Decimal(str(close - 100)),
        close=Decimal(str(close)),
        volume=Decimal("1000"),
        quote_volume=Decimal("100000"),
        data_version="test",
    )


def test_initialize():
    """测试初始化"""
    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="{strategy_id}",
        version_no=1,
        deployment_id="dep_test",
        params={{"lookback": 50, "atr_mult": 2.0, "position_size": 0.1}},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
    )
    strategy.initialize(ctx)
    assert True  # 初始化不应抛异常


def test_on_bar():
    """测试 K 线回调"""
    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="{strategy_id}",
        version_no=1,
        deployment_id="dep_test",
        params={{"lookback": 50, "atr_mult": 2.0, "position_size": 0.1}},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
    )
    strategy.initialize(ctx)

    bar = make_bar(67000.0)
    strategy.on_bar(ctx, bar)
    # TODO: 验证意图输出
'''
