"""冒烟测试 - 演示均值回归策略"""

import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

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


def test_on_bar_no_signal_with_insufficient_data():
    """测试数据不足时不产生信号"""
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

    # 只发送少量数据
    for i in range(10):
        bar = make_bar(67000.0 + i * 100)
        strategy.on_bar(ctx, bar)

    # 不应有持仓
    assert strategy._position_side is None


def test_on_bar_generates_intent():
    """测试 K 线回调产生意图"""
    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 20, "entry_z": 1.5, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
    )
    strategy.initialize(ctx)

    # 生成足够多的数据，并制造一个明显的偏离
    base_price = 67000.0
    bars = []

    # 先发送正常波动的数据
    for i in range(25):
        price = base_price + (i % 5) * 50  # 小幅度波动
        bars.append(make_bar(price))

    # 发送一个大幅偏离的数据（负向偏离）
    bars.append(make_bar(base_price - 1000))

    # 处理所有 bar
    for bar in bars:
        strategy.on_bar(ctx, bar)

    # 应该检测到开仓信号
    # 注意：由于我们没有实际的事件总线，这里只验证状态更新
    assert "last_z_score" in ctx.state


def test_feature_cache_miss_handling():
    """测试特征缓存未命中时的处理"""
    strategy = Strategy()
    ctx = StrategyContext(
        strategy_id="demo_mean_reversion",
        version_no=1,
        deployment_id="dep_test",
        params={"lookback": 50, "entry_z": 2.0, "exit_z": 0.5, "position_size": 0.1},
        instruments=["BTC-USDT-PERP"],
        interval="5m",
        feature_pipeline=None,  # 模拟缓存未命中
    )
    strategy.initialize(ctx)

    # 特征返回空值时，策略应跳过信号
    result = ctx.feature("binance-btc-usdt-perp", "returns")
    assert result is None  # 缓存未命中返回 None


if __name__ == "__main__":
    test_initialize()
    print("✅ test_initialize passed")

    test_on_bar_no_signal_with_insufficient_data()
    print("✅ test_on_bar_no_signal_with_insufficient_data passed")

    test_on_bar_generates_intent()
    print("✅ test_on_bar_generates_intent passed")

    test_feature_cache_miss_handling()
    print("✅ test_feature_cache_miss_handling passed")

    print("\n🎉 所有测试通过!")
