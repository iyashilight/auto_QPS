"""冒烟测试 - 演示均值回归策略

测试：
- 初始化
- feature 缓存未命中跳过信号
- INTENT_CREATED 事件捕获
- 状态恢复（ctx.state）
"""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# 确保可以导入策略模块
sys.path.insert(0, str(Path(__file__).parent.parent))

from demo_mean_reversion.strategy import Strategy
from qps.core.models import Bar
from qps.core.sdk import StrategyContext


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
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 50, "entry_z": 2.0, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
    )
    strategy.initialize(ctx)
    assert True  # 初始化不应抛异常


def test_feature_cache_miss_skips_signal():
    """测试特征缓存未命中时跳过信号"""
    events_captured: list[dict[str, Any]] = []

    def event_bus(data: dict[str, Any]) -> None:
        events_captured.append(data)

    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 50, "entry_z": 2.0, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
        feature_pipeline=None,  # 模拟缓存未命中
        event_bus=event_bus,
    )
    strategy.initialize(ctx)

    # 发送一根 K 线
    bar = make_bar(67000.0)
    strategy.on_bar(ctx, bar)

    # 特征缓存未命中，不应产生任何意图
    assert len(events_captured) == 0
    # 验证日志中有缓存未命中的记录
    logs = ctx.get_logs()
    assert any("特征缓存未命中" in log["message"] for log in logs)


def test_emit_intent_via_event_bus():
    """测试通过 event_bus 捕获 INTENT_CREATED"""
    events_captured: list[dict[str, Any]] = []

    def event_bus(data: dict[str, Any]) -> None:
        events_captured.append(data)

    # 创建一个返回正常特征值的 pipeline
    def feature_pipeline(instrument_id: str, names: list[str]) -> dict[str, float]:
        return {"returns": -0.03, "mean_return_20": 0.001, "realized_vol_20": 0.02}

    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 50, "entry_z": 2.0, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
        feature_pipeline=feature_pipeline,
        event_bus=event_bus,
    )
    strategy.initialize(ctx)

    # 发送一根 K 线
    # Z-Score = (returns - mean_return) / (vol / sqrt(105120))
    # = (-0.03 - 0.001) / (0.02 / 324.23) ≈ -0.031 / 0.0000617 ≈ -502
    # 绝对值 >= entry_z=2.0，触发 LONG OPEN
    # 让我们用更合理的值
    def extreme_pipeline(instrument_id: str, names: list[str]) -> dict[str, float]:
        # Z-Score = (-0.05 - 0.0) / (0.02 / 324.23) ≈ -810 → 触发 LONG
        return {"returns": -0.05, "mean_return_20": 0.0, "realized_vol_20": 0.02}

    ctx._feature_pipeline = extreme_pipeline
    bar = make_bar(67000.0)
    strategy.on_bar(ctx, bar)

    # 应该产生开仓意图
    assert len(events_captured) >= 1
    intent = events_captured[0]["intent"]
    assert intent["direction"] == "LONG"  # 负向偏离，做多
    assert intent["action"] == "OPEN"
    assert intent["order_type"] == "MARKET"
    assert "quantity" in intent


def test_state_persistence_across_callbacks():
    """测试跨回调状态持久化（ctx.state）"""
    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 50, "entry_z": 2.0, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
    )
    strategy.initialize(ctx)

    # 初始状态应为空持仓
    assert ctx.state.get("position_side") is None

    # 保存状态
    saved = ctx.save_state()
    assert saved["position_side"] is None

    # 模拟重启后恢复
    ctx.restore_state({"position_side": "LONG", "entry_price": "67000"})
    assert ctx.state["position_side"] == "LONG"
    # entry_price 在 state 中存储为字符串，需要转换
    assert str(ctx.state["entry_price"]) == "67000"


if __name__ == "__main__":
    test_initialize()
    print("✅ test_initialize passed")

    test_feature_cache_miss_skips_signal()
    print("✅ test_feature_cache_miss_skips_signal passed")

    test_emit_intent_via_event_bus()
    print("✅ test_emit_intent_via_event_bus passed")

    test_state_persistence_across_callbacks()
    print("✅ test_state_persistence_across_callbacks passed")

    print("\n🎉 所有测试通过!")
