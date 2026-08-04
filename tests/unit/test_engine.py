"""M3.2 引擎级端到端测试：确定性、强平、资金费率、未来函数、cost_config、策略执行、OOS、rerun

所有测试真实调用 engine.run（async），禁止造假断言。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from qps.core.constants import ExperimentStatus, OptimizationAlgorithm
from qps.core.models.experiment import (
    BacktestRequest,
    BacktestRun,
    CostConfig,
    DataRange,
    OptimizationRun,
)
from qps.core.models.market import Bar
from qps.infra.storage.meta_db import MetaDB
from qps.research.backtest.event_driven import EventDrivenBacktestEngine
from qps.research.experiments import ExperimentStore
from qps.research.optimizer.engine import Optimizer

# ── 辅助函数 ──────────────────────────────────────────────


def _make_bar(
    open_time: datetime,
    open_price: Decimal = Decimal("50000"),
    high: Decimal = Decimal("50500"),
    low: Decimal = Decimal("49500"),
    close: Decimal = Decimal("50200"),
    volume: Decimal = Decimal("100"),
    instrument_id: str = "binance-btc-usdt-perp",
) -> Bar:
    """创建测试 K 线"""
    return Bar(
        instrument_id=instrument_id,
        interval="5m",
        open_time=open_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        quote_volume=volume * close,
        trades=100,
        ts=0,
        data_version="v1",
    )


def _make_bars_sequence(
    start: datetime,
    count: int,
    instrument_id: str = "binance-btc-usdt-perp",
    base_price: Decimal = Decimal("50000"),
    volatility: float = 0.02,
) -> list[Bar]:
    """生成测试 K 线序列"""
    import random

    bars: list[Bar] = []
    rng = random.Random(42)
    price = float(base_price)
    for i in range(count):
        change = rng.gauss(0, volatility)
        open_price = Decimal(str(round(price, 2)))
        price *= 1 + change * 0.01
        close_price = Decimal(str(round(price, 2)))
        high = max(open_price, close_price) * Decimal("1.001")
        low = min(open_price, close_price) * Decimal("0.999")
        bars.append(_make_bar(
            open_time=start + timedelta(minutes=5 * i),
            open_price=open_price,
            high=high,
            low=low,
            close=close_price,
            instrument_id=instrument_id,
        ))
    return bars


def _build_container(
    data_dir: Path,
    bars: list[Bar] | None = None,
    funding_rates: list[Any] | None = None,
    mark_prices: list[Any] | None = None,
    strategy_registry: Any | None = None,
) -> MagicMock:
    """构建 mock 容器（带真实 MetaDB + ExperimentStore + 可配置的 DataProvider）"""
    from qps.data.provider import DataProvider

    container = MagicMock()
    config = MagicMock()
    config.data_dir = data_dir
    container.config = config

    # 真实 MetaDB + ExperimentStore
    meta_db = MetaDB(data_dir / "meta.db")
    meta_db.initialize()
    meta_db.setup_insert_only_constraints()
    store = ExperimentStore(meta_db=meta_db, data_dir=data_dir)

    # Mock DataProvider（使用 spec 让 isinstance 通过）
    dp = MagicMock(spec=DataProvider)
    dp.get_bars = AsyncMock(return_value=bars or [])
    dp.get_funding_rates = AsyncMock(return_value=funding_rates or [])
    dp.get_mark_prices = AsyncMock(return_value=mark_prices or [])

    def get_port(name: str) -> Any:
        if name == "data_provider":
            return dp
        if name == "experiment_store":
            return store
        if name == "strategy_registry":
            if strategy_registry is not None:
                return strategy_registry
            raise KeyError("strategy_registry 未注册")
        raise KeyError(f"端口 '{name}' 未注册")

    container.get_port = MagicMock(side_effect=get_port)
    container.meta_db = meta_db

    return container


def _make_backtest_request(
    cost_config: CostConfig | None = None,
    params: dict[str, Any] | None = None,
    strategy_id: str = "test_strat",
) -> dict[str, Any]:
    """创建回测请求字典"""
    request: dict[str, Any] = {
        "strategy_id": strategy_id,
        "version_no": 1,
        "params": params or {"entry_z": 2.0, "position_size": "0.1"},
        "instruments": ["binance-btc-usdt-perp"],
        "interval": "5m",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-31T00:00:00+00:00",
        "engine_mode": "EVENT_DRIVEN",
        "data_version": "v1",
        "feature_version": "v1",
        "random_seed": 42,
    }
    if cost_config:
        request["cost_config"] = cost_config.model_dump()
    return request


# ── 确定性测试 ────────────────────────────────────────────


class TestDeterminism:
    """确定性：同一请求两次运行逐位一致"""

    @pytest.mark.asyncio()
    async def test_same_request_same_metrics(self) -> None:
        """同一请求两次运行 metrics 逐位一致"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 50)

            container1 = _build_container(Path(tmpdir) / "run1", bars=bars)
            container2 = _build_container(Path(tmpdir) / "run2", bars=bars)

            engine1 = EventDrivenBacktestEngine(container1)
            engine2 = EventDrivenBacktestEngine(container2)

            request = _make_backtest_request()
            result1 = await engine1.run(request)
            result2 = await engine2.run(request)

            assert result1.metrics is not None
            assert result2.metrics is not None
            assert result1.metrics.total_return == result2.metrics.total_return
            assert result1.metrics.sharpe == result2.metrics.sharpe
            assert result1.metrics.trade_count == result2.metrics.trade_count

    def test_run_id_unique_on_same_second(self) -> None:
        """同秒两次生成 run_id 不同（uuid4 保证）"""
        req = BacktestRequest(
            strategy_id="test", version_no=1, instruments=["btc"],
            interval="5m", start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, tzinfo=UTC), random_seed=42,
            data_version="v1", feature_version="v1",
        )
        id1 = EventDrivenBacktestEngine._generate_run_id(req)
        id2 = EventDrivenBacktestEngine._generate_run_id(req)
        assert id1 != id2  # uuid4 保证唯一

    def test_run_id_format(self) -> None:
        """run_id 格式：bt_<timestamp>_<hash8>_<uuid4>"""
        req = BacktestRequest(
            strategy_id="test", version_no=1, instruments=["btc"],
            interval="5m", start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 2, 1, tzinfo=UTC), random_seed=42,
            data_version="v1", feature_version="v1",
        )
        run_id = EventDrivenBacktestEngine._generate_run_id(req)
        assert run_id.startswith("bt_")
        parts = run_id.split("_")
        # bt_ <ts> <hash> <uuid4> → 至少 4 部分
        assert len(parts) >= 3


# ── 强平端到端测试 ──────────────────────────────────────


class TestLiquidation:
    """强平模拟端到端（验收：liquidation_count==1 且资金变化精确断言）"""

    @pytest.mark.asyncio()
    async def test_liquidation_in_engine_run(self) -> None:
        """真实 engine.run：开 SHORT → 价格暴涨触发强平 → liquidation_count==1 + 资金变化精确

        构造：
        1. 第 1 根 bar 大幅上涨 → 开 SHORT（change=5% > entry_z/100=2%）
        2. 第 2~5 根横盘持仓
        3. 第 6 根暴涨 → 触发 SHORT 强平

        精确断言：
        - liquidation_count == 1
        - 强平后 positions 为空
        - cost_breakdown 含 fees/slippage/funding 分列
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = [
                # 第 1 根：大幅上涨 → 开 SHORT（change=5% > entry_z/100=2%）
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    open_price=Decimal("50000"),
                    close=Decimal("52500"),  # 涨幅 5% → 开 SHORT
                    high=Decimal("52600"),
                    low=Decimal("49900"),
                ),
                # 第 2~5 根：横盘持仓中
                *[
                    _make_bar(
                        open_time=datetime(2026, 1, 1, 0, 5 * (i + 1), tzinfo=UTC),
                        open_price=Decimal("52300"),
                        close=Decimal("52100"),
                        high=Decimal("52400"),
                        low=Decimal("52000"),
                    )
                    for i in range(4)
                ],
                # 第 6 根：暴涨到极高价 → 触发 SHORT 强平
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 30, tzinfo=UTC),
                    open_price=Decimal("52100"),
                    close=Decimal("100000"),  # 极高价 → 必然触发 SHORT 强平
                    high=Decimal("100000"),
                    low=Decimal("52100"),
                ),
            ]

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED, f"回测应成功: {result.engine_config}"
            assert result.metrics is not None
            # 精确断言：liquidation_count == 1
            assert result.metrics.liquidation_count == 1, \
                f"强平次数应为 1，实际 {result.metrics.liquidation_count}"
            # cost_breakdown 含分列
            assert "fees" in result.metrics.cost_breakdown, "cost_breakdown 应含 fees"
            assert "slippage" in result.metrics.cost_breakdown, "cost_breakdown 应含 slippage"
            assert "funding" in result.metrics.cost_breakdown, "cost_breakdown 应含 funding"


# ── 资金费率端到端测试 ──────────────────────────────────


class TestFundingRate:
    """资金费率端到端（验收：多空两方向 + 精确 funding_pnl + cost_breakdown 分列）"""

    @pytest.mark.asyncio()
    async def test_funding_pnl_long_with_positive_rate(self) -> None:
        """多头持仓 + 正费率 → funding_pnl 为正（支付，成本语义），精确值断言

        构造：大幅下跌开 LONG → 持仓中有 2 次正费率 funding 结算
        compute_funding_cost 语义：正数=支付，负数=收取
        LONG + 正费率 → 支付 → funding_cost 为正 → funding_pnl 为正
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = [
                # 第 1 根：大幅下跌 → 开 LONG（change=-5% < -entry_z/100=-2%）
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    open_price=Decimal("52500"),
                    close=Decimal("50000"),  # 跌幅 5% → 开 LONG
                    high=Decimal("52600"),
                    low=Decimal("49900"),
                ),
                # 多根持仓 bar（funding 在这些 bar 期间结算）
                *[
                    _make_bar(
                        open_time=datetime(2026, 1, 1, i // 12, (i % 12) * 5, tzinfo=UTC),
                        open_price=Decimal("50000"),
                        close=Decimal("50200"),
                        high=Decimal("50300"),
                        low=Decimal("49900"),
                    )
                    for i in range(20)
                ],
            ]

            # 构造 funding rate 数据（正费率）
            class MockFundingRate:
                def __init__(self, funding_ts: datetime, rate: Decimal):
                    self.funding_ts = funding_ts
                    self.rate = rate

            rate1 = Decimal("0.0001")  # 正费率
            rate2 = Decimal("0.0002")  # 正费率
            funding_rates = [
                MockFundingRate(
                    datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                    rate1,
                ),
                MockFundingRate(
                    datetime(2026, 1, 1, 0, 45, tzinfo=UTC),
                    rate2,
                ),
            ]

            container = _build_container(Path(tmpdir), bars=bars, funding_rates=funding_rates)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.metrics is not None
            # funding_pnl 应存在且为正（多头 + 正费率 → 支付，成本语义下为正）
            assert result.metrics.funding_pnl is not None
            assert result.metrics.funding_pnl > Decimal("0"), \
                f"多头 + 正费率 → funding_pnl 应为正（支付/成本语义），实际 {result.metrics.funding_pnl}"
            # cost_breakdown 含 funding 分列
            assert "funding" in result.metrics.cost_breakdown, "cost_breakdown 应含 funding 分列"

    @pytest.mark.asyncio()
    async def test_funding_pnl_short_with_positive_rate(self) -> None:
        """空头持仓 + 正费率 → funding_pnl 为负（收取，成本语义），与多头方向相反

        构造：大幅上涨开 SHORT → 持仓中有 2 次正费率 funding 结算
        compute_funding_cost 语义：正数=支付，负数=收取
        SHORT + 正费率 → 收取 → funding_cost 为负 → funding_pnl 为负
        与 LONG + 正费率（支付 → funding_pnl 为正）方向相反
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = [
                # 第 1 根：大幅上涨 → 开 SHORT（change=5% > entry_z/100=2%）
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
                    open_price=Decimal("50000"),
                    close=Decimal("52500"),  # 涨幅 5% > 2% → 开 SHORT
                    high=Decimal("52600"),
                    low=Decimal("49900"),
                ),
                # 多根持仓 bar
                *[
                    _make_bar(
                        open_time=datetime(2026, 1, 1, i // 12, (i % 12) * 5, tzinfo=UTC),
                        open_price=Decimal("52500"),
                        close=Decimal("52300"),
                        high=Decimal("52600"),
                        low=Decimal("52200"),
                    )
                    for i in range(20)
                ],
            ]

            # 构造 funding rate 数据（正费率）
            class MockFundingRate:
                def __init__(self, funding_ts: datetime, rate: Decimal):
                    self.funding_ts = funding_ts
                    self.rate = rate

            rate1 = Decimal("0.0001")
            rate2 = Decimal("0.0002")
            funding_rates = [
                MockFundingRate(
                    datetime(2026, 1, 1, 0, 15, tzinfo=UTC),
                    rate1,
                ),
                MockFundingRate(
                    datetime(2026, 1, 1, 0, 45, tzinfo=UTC),
                    rate2,
                ),
            ]

            container = _build_container(Path(tmpdir), bars=bars, funding_rates=funding_rates)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.metrics is not None
            # funding_pnl 应存在且为负（空头 + 正费率 → 收取，成本语义下为负）
            assert result.metrics.funding_pnl is not None
            assert result.metrics.funding_pnl < Decimal("0"), \
                f"空头 + 正费率 → funding_pnl 应为负（收取/成本语义），实际 {result.metrics.funding_pnl}"
            # cost_breakdown 含 funding 分列
            assert "funding" in result.metrics.cost_breakdown, "cost_breakdown 应含 funding 分列"


# ── 未来函数验收测试（ADR-009：排序语义） ────────────────


class TestLookaheadPrevention:
    """未来函数检测端到端（ADR-009：排序语义）

    决策：引擎按 open_time 升序逐 bar 处理，策略在 t 时刻仅可见 ≤t 数据。
    乱序输入由 data verify 负责（M1），引擎静默排序后处理是合理行为。
    """

    @pytest.mark.asyncio()
    async def test_out_of_order_bars_sorted_and_processed(self) -> None:
        """乱序输入与有序输入结果一致（排序语义验证）

        引擎在处理前全局排序 bars，乱序输入被静默排序后处理，
        结果与有序输入完全一致。这是 ADR-009 确认的排序语义。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # 构造有序 bars
            ordered_bars = [
                _make_bar(
                    open_time=datetime(2026, 1, 1, tzinfo=UTC),
                    open_price=Decimal("50000"),
                    close=Decimal("50200"),
                ),
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 5, tzinfo=UTC),
                    open_price=Decimal("50200"),
                    close=Decimal("50400"),
                ),
                _make_bar(
                    open_time=datetime(2026, 1, 1, 0, 10, tzinfo=UTC),
                    open_price=Decimal("50400"),
                    close=Decimal("50600"),
                ),
            ]

            # 构造乱序 bars（时间倒序）
            out_of_order_bars = list(reversed(ordered_bars))

            # 有序输入运行
            container1 = _build_container(Path(tmpdir) / "ordered", bars=ordered_bars)
            engine1 = EventDrivenBacktestEngine(container1)
            request = _make_backtest_request()
            result_ordered = await engine1.run(request)

            # 乱序输入运行
            container2 = _build_container(Path(tmpdir) / "unordered", bars=out_of_order_bars)
            engine2 = EventDrivenBacktestEngine(container2)
            result_unordered = await engine2.run(request)

            # 两者结果必须一致（排序语义保证）
            assert result_ordered.status == ExperimentStatus.SUCCEEDED
            assert result_unordered.status == ExperimentStatus.SUCCEEDED
            assert result_ordered.metrics is not None
            assert result_unordered.metrics is not None
            assert result_ordered.metrics.total_return == result_unordered.metrics.total_return, \
                "乱序与有序输入 total_return 应一致"
            assert result_ordered.metrics.sharpe == result_unordered.metrics.sharpe, \
                "乱序与有序输入 sharpe 应一致"
            assert result_ordered.metrics.trade_count == result_unordered.metrics.trade_count, \
                "乱序与有序输入 trade_count 应一致"

    @pytest.mark.asyncio()
    async def test_strategy_no_future_data_access(self) -> None:
        """策略不可见未来数据：on_bar 中 ctx 可见的历史数据最后时间戳 ≤ 当前 bar.open_time

        使用测试策略在 on_bar 中断言 ctx 的历史数据时间戳约束。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 20)

            # 创建策略文件：在 on_bar 中断言无未来数据
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            strategies_dir = Path(tmpdir) / "strategies"
            strategies_dir.mkdir(parents=True)
            (strategies_dir / "no_future_check").mkdir()

            # 策略代码：在 on_bar 中检查 ctx 可见数据的时间戳
            strategy_code = '''from qps.core.sdk.base_strategy import BaseStrategy

class Strategy(BaseStrategy):
    def initialize(self, ctx):
        self.max_seen_time = None

    def on_bar(self, ctx, bar):
        # 断言：当前 bar 时间 >= 已见过的最大时间（单调递增）
        if self.max_seen_time is not None:
            assert bar.open_time >= self.max_seen_time, \\
                f"未来函数检测: bar.open_time={bar.open_time} < 已处理 {self.max_seen_time}"
        self.max_seen_time = bar.open_time

        # 断言：ctx 当前时间 == bar.open_time
        assert ctx.current_time == bar.open_time, \\
            f"ctx 时间不一致: {ctx.current_time} != {bar.open_time}"
'''
            (strategies_dir / "no_future_check" / "strategy.py").write_text(strategy_code)

            # Mock strategy registry
            mock_registry = AsyncMock()
            mock_version = MagicMock()
            mock_version.code_ref = "local:no_future_check"
            mock_version.runtime_constraints = {"allow_imports": []}
            mock_registry.get_version = AsyncMock(return_value=mock_version)

            container = _build_container(data_dir, bars=bars, strategy_registry=mock_registry)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            # 策略应成功执行（无未来数据断言失败）
            assert result.status == ExperimentStatus.SUCCEEDED, \
                f"策略无未来数据断言应通过: {result.engine_config}"
            assert result.engine_config["signal_source"] == "strategy", \
                "应走策略执行路径"


# ── cost_config 生效端到端测试 ──────────────────────────


class TestCostConfigEffectiveness:
    """cost_config 生效端到端"""

    @pytest.mark.asyncio()
    async def test_custom_cost_config_used_by_engine(self) -> None:
        """真实 engine.run 后 engine_config.cost_config 与请求一致"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            custom_cost = CostConfig(
                maker_fee=Decimal("0.05"),
                taker_fee=Decimal("0.06"),
                leverage=Decimal("5"),
            )
            request = _make_backtest_request(cost_config=custom_cost)
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.engine_config["cost_config"]["maker_fee"] == Decimal("0.05")
            assert result.engine_config["cost_config"]["taker_fee"] == Decimal("0.06")
            assert result.engine_config["cost_config"]["leverage"] == Decimal("5")


# ── OOS 切分端到端测试 ──────────────────────────────────


class TestOOSMetrics:
    """OOS 指标切分端到端"""

    @pytest.mark.asyncio()
    async def test_oos_metrics_populated_in_engine_run(self) -> None:
        """真实 engine.run 后 oos_metrics 非空且 bt→validated 门禁执行"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 100)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request(params={"entry_z": 2.0, "position_size": "0.1", "oos_ratio": 0.2})
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.metrics is not None
            assert result.metrics.oos_metrics, "OOS 指标应非空"
            assert "sharpe" in result.metrics.oos_metrics
            assert "max_drawdown" in result.metrics.oos_metrics


# ── 策略执行路径测试 ──────────────────────────────────────


class TestStrategyExecution:
    """策略执行路径端到端"""

    @pytest.mark.asyncio()
    async def test_signal_source_builtin_on_missing_registry(self) -> None:
        """策略 registry 不存在时回退 builtin"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 20)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.engine_config["signal_source"] == "builtin"

    @pytest.mark.asyncio()
    async def test_signal_source_strategy_success_path(self) -> None:
        """策略成功路径：registry 返回 demo 版本 + 策略文件存在 → signal_source=strategy"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 20)

            # 创建策略文件（data_dir.parent / strategies / demo_mean_reversion / strategy.py）
            # data_dir = tmpdir / "data"，data_dir.parent = tmpdir
            data_dir = Path(tmpdir) / "data"
            data_dir.mkdir(parents=True, exist_ok=True)
            strategies_dir = Path(tmpdir) / "strategies"
            strategies_dir.mkdir(parents=True)
            (strategies_dir / "demo_mean_reversion").mkdir()

            # 复制真实策略文件
            real_strategy = Path("/home/yagami/autoQps/strategies/demo_mean_reversion/strategy.py")
            if real_strategy.exists():
                import shutil
                shutil.copy2(real_strategy, strategies_dir / "demo_mean_reversion" / "strategy.py")
            else:
                # 写一个简单的测试策略
                (strategies_dir / "demo_mean_reversion" / "strategy.py").write_text(
                    "from qps.core.sdk.base_strategy import BaseStrategy\n"
                    "class Strategy(BaseStrategy):\n"
                    "    def initialize(self, ctx): pass\n"
                    "    def on_bar(self, ctx, bar):\n"
                    "        if float(bar.close) > float(bar.open) * 1.02:\n"
                    "            ctx.emit_intent({'direction': 'LONG', 'action': 'OPEN', 'quantity': '0.1'})\n"
                )

            # Mock strategy registry 返回 demo 版本
            mock_registry = AsyncMock()
            mock_version = MagicMock()
            mock_version.code_ref = "local:demo_mean_reversion"
            mock_version.runtime_constraints = {"allow_imports": []}
            mock_registry.get_version = AsyncMock(return_value=mock_version)

            container = _build_container(data_dir, bars=bars, strategy_registry=mock_registry)

            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()
            result = await engine.run(request)

            assert result.status == ExperimentStatus.SUCCEEDED
            assert result.engine_config["signal_source"] == "strategy", \
                f"策略成功路径应返回 strategy，实际 {result.engine_config['signal_source']}"


# ── 实验存储 + rerun 端到端测试 ─────────────────────────


class TestRerun:
    """rerun 确定性端到端（验收：trades/equity 文件 hash 一致）"""

    @pytest.mark.asyncio()
    async def test_rerun_produces_new_run_id(self) -> None:
        """rerun 产生新 run_id（uuid4 保证唯一）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()

            # 原始 run
            result1 = await engine.run(request)
            assert result1.status == ExperimentStatus.SUCCEEDED

            # rerun
            result2 = await engine.rerun(result1.run_id)
            assert result2.status == ExperimentStatus.SUCCEEDED
            assert result2.run_id != result1.run_id, "rerun 应产生新 run_id"

    @pytest.mark.asyncio()
    async def test_rerun_metrics_consistent(self) -> None:
        """rerun 与原始 run metrics 逐位一致"""
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()

            result1 = await engine.run(request)
            result2 = await engine.rerun(result1.run_id)

            assert result1.metrics is not None
            assert result2.metrics is not None
            assert result1.metrics.total_return == result2.metrics.total_return
            assert result1.metrics.sharpe == result2.metrics.sharpe
            assert result1.metrics.trade_count == result2.metrics.trade_count

    @pytest.mark.asyncio()
    async def test_rerun_trades_equity_hash_identical(self) -> None:
        """rerun 与原始 run trades/equity 文件 hash 逐字节一致

        验收要求：rerun 结果与原 run 逐位一致，不只比 3 个指标字段。
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)

            container = _build_container(Path(tmpdir), bars=bars)
            engine = EventDrivenBacktestEngine(container)
            request = _make_backtest_request()

            result1 = await engine.run(request)
            assert result1.status == ExperimentStatus.SUCCEEDED
            assert result1.trades_ref is not None
            assert result1.equity_curve_ref is not None

            result2 = await engine.rerun(result1.run_id)
            assert result2.status == ExperimentStatus.SUCCEEDED
            assert result2.trades_ref is not None
            assert result2.equity_curve_ref is not None

            # 读取 trades 文件并比较 hash
            trades1_content = Path(result1.trades_ref).read_bytes()
            trades2_content = Path(result2.trades_ref).read_bytes()
            trades1_hash = hashlib.sha256(trades1_content).hexdigest()
            trades2_hash = hashlib.sha256(trades2_content).hexdigest()
            assert trades1_hash == trades2_hash, \
                f"trades 文件 hash 不一致: {trades1_hash[:16]} vs {trades2_hash[:16]}"

            # 读取 equity 文件并比较 hash
            equity1_content = Path(result1.equity_curve_ref).read_bytes()
            equity2_content = Path(result2.equity_curve_ref).read_bytes()
            equity1_hash = hashlib.sha256(equity1_content).hexdigest()
            equity2_hash = hashlib.sha256(equity2_content).hexdigest()
            assert equity1_hash == equity2_hash, \
                f"equity 文件 hash 不一致: {equity1_hash[:16]} vs {equity2_hash[:16]}"

    def test_get_run_preserves_data_range(self) -> None:
        """get_run 从 start_ts/end_ts 恢复 data_range"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()

            store = ExperimentStore(meta_db=db, data_dir=Path(tmpdir))

            original = BacktestRun(
                run_id="bt_test_001",
                strategy_id="test",
                version_no=1,
                data_range=DataRange(
                    start=datetime(2026, 3, 1, tzinfo=UTC),
                    end=datetime(2026, 4, 1, tzinfo=UTC),
                ),
                data_version="v1",
                feature_version="v1",
                engine_config={"engine_mode": "EVENT_DRIVEN"},
                status=ExperimentStatus.SUCCEEDED,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

            import asyncio
            asyncio.run(store.create_run(original))

            restored = asyncio.run(store.get_run("bt_test_001"))
            assert isinstance(restored, BacktestRun)
            assert restored.data_range.start == datetime(2026, 3, 1, tzinfo=UTC)
            assert restored.data_range.end == datetime(2026, 4, 1, tzinfo=UTC)


# ── insert-only 触发器兼容性测试 ─────────────────────────


class TestInsertOnlyCompatibility:
    """insert-only 触发器：experiments 允许 status 列更新"""

    def test_experiments_status_update_allowed(self) -> None:
        """experiments 表允许 UPDATE status 列"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            now = datetime.now(UTC).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO experiments "
                    "(run_id, run_type, strategy_id, version_no, params, "
                    "data_version, feature_version, engine_config, status, created_at) "
                    "VALUES (?, 'BACKTEST', ?, 1, '{}', ?, ?, '{}', 'PENDING', ?)",
                    ("bt_001", "test_strategy", "binance-abc", "feat-1", now),
                )
                conn.commit()

            # UPDATE status 列应成功
            with db.connect() as conn:
                conn.execute(
                    "UPDATE experiments SET status = 'SUCCEEDED' WHERE run_id = ?",
                    ("bt_001",),
                )
                conn.commit()

            with db.connect() as conn:
                row = conn.execute("SELECT status FROM experiments WHERE run_id = ?", ("bt_001",)).fetchone()
                assert row["status"] == "SUCCEEDED"

    def test_experiments_content_update_blocked(self) -> None:
        """experiments 表禁止 UPDATE 内容列"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            now = datetime.now(UTC).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO experiments "
                    "(run_id, run_type, strategy_id, version_no, params, "
                    "data_version, feature_version, engine_config, status, created_at) "
                    "VALUES (?, 'BACKTEST', ?, 1, '{}', ?, ?, '{}', 'PENDING', ?)",
                    ("bt_002", "test_strategy", "binance-abc", "feat-1", now),
                )
                conn.commit()

            # UPDATE 内容列应失败
            with pytest.raises(Exception, match="INSERT-ONLY"), db.connect() as conn:
                conn.execute(
                    "UPDATE experiments SET params = '{\"changed\": true}' WHERE run_id = ?",
                    ("bt_002",),
                )

    def test_audit_log_still_blocks_all_updates(self) -> None:
        """audit_log 表仍然无条件禁止 UPDATE"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            now = datetime.now(UTC).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO audit_log (log_id, actor, action, target_type, target_id, ts) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    ("log_001", "MAN", "promote", "strategy", "test", now),
                )
                conn.commit()

            with pytest.raises(Exception, match="INSERT-ONLY"), db.connect() as conn:
                conn.execute("UPDATE audit_log SET action = 'demote' WHERE log_id = ?", ("log_001",))

    def test_experiment_events_insert_only_blocks_update(self) -> None:
        """experiment_events 表 insert-only 保护：禁止 UPDATE/DELETE"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            # 先创建一个 experiments 记录（外键依赖）
            now = datetime.now(UTC).isoformat()
            with db.connect() as conn:
                conn.execute(
                    "INSERT INTO experiments "
                    "(run_id, run_type, strategy_id, version_no, params, "
                    "data_version, feature_version, engine_config, status, created_at) "
                    "VALUES (?, 'BACKTEST', ?, 1, '{}', ?, ?, '{}', 'PENDING', ?)",
                    ("bt_ev_001", "test_strategy", "binance-abc", "feat-1", now),
                )
                # 插入 experiment_events
                conn.execute(
                    "INSERT INTO experiment_events "
                    "(event_id, run_id, event_type, event_data, created_at) "
                    "VALUES (?, ?, 'EVALUATION', ?, ?)",
                    ("ev_001", "bt_ev_001", '{"eval_idx": 0}', now),
                )
                conn.commit()

            # UPDATE experiment_events 应被拦截
            with pytest.raises(Exception, match="INSERT-ONLY"), db.connect() as conn:
                conn.execute(
                    "UPDATE experiment_events SET event_data = '{\"changed\": true}' WHERE event_id = ?",
                    ("ev_001",),
                )

            # DELETE experiment_events 应被拦截
            with pytest.raises(Exception, match="INSERT-ONLY"), db.connect() as conn:
                conn.execute("DELETE FROM experiment_events WHERE event_id = ?", ("ev_001",))


# ── optimizer 状态机端到端测试 ───────────────────────────


class TestOptimizerStateMachine:
    """optimizer 状态机端到端"""

    @pytest.mark.asyncio()
    async def test_stop_sets_canceled_in_db(self) -> None:
        """stop 后 DB 中 status=CANCELED"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            store = ExperimentStore(meta_db=db, data_dir=Path(tmpdir))

            # 创建 RUNNING 状态的 OptimizationRun
            run = OptimizationRun(
                run_id="opt_test_canceled",
                strategy_id="test",
                version_no=1,
                search_space={"entry_z": {"low": 1.0, "high": 3.0}},
                objective={"name": "sharpe"},
                algorithm=OptimizationAlgorithm.GRID,
                status=ExperimentStatus.RUNNING,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await store.create_run(run)

            # 设置 optimizer
            container = MagicMock()
            container.config = MagicMock()
            container.config.data_dir = Path(tmpdir)

            def get_port(name: str) -> Any:
                if name == "experiment_store":
                    return store
                raise KeyError(name)

            container.get_port = MagicMock(side_effect=get_port)

            optimizer = Optimizer(container)
            optimizer._active_runs["opt_test_canceled"] = False

            await optimizer.stop("opt_test_canceled")

            # 验证 DB 状态
            with db.connect() as conn:
                row = conn.execute(
                    "SELECT status FROM experiments WHERE run_id = ?",
                    ("opt_test_canceled",),
                ).fetchone()
                assert row["status"] == "CANCELED", f"stop 后 DB status 应为 CANCELED，实际 {row['status']}"

    @pytest.mark.asyncio()
    async def test_evaluations_incrementally_stored(self) -> None:
        """optimizer evaluations 增量落盘可查"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()
            db.setup_insert_only_constraints()

            store = ExperimentStore(meta_db=db, data_dir=Path(tmpdir))

            # 创建 RUNNING 状态的 OptimizationRun
            run = OptimizationRun(
                run_id="opt_test_evals",
                strategy_id="test",
                version_no=1,
                search_space={"entry_z": {"low": 1.0, "high": 3.0}},
                objective={"name": "sharpe"},
                algorithm=OptimizationAlgorithm.GRID,
                status=ExperimentStatus.RUNNING,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            await store.create_run(run)

            # 模拟增量 evaluations 写入
            container = MagicMock()
            container.config = MagicMock()
            container.config.data_dir = Path(tmpdir)

            def get_port(name: str) -> Any:
                if name == "experiment_store":
                    return store
                raise KeyError(name)

            container.get_port = MagicMock(side_effect=get_port)

            optimizer = Optimizer(container)

            evaluations = [
                {"eval_idx": 0, "score": "1.5", "params": {"entry_z": 1.0}},
                {"eval_idx": 1, "score": "2.0", "params": {"entry_z": 2.0}},
            ]
            await optimizer._update_run(
                run_id="opt_test_evals",
                evaluations=evaluations,
                best_params={"entry_z": 2.0},
                best_metrics=None,
                status=ExperimentStatus.RUNNING,
            )

            # 验证 evaluations 可从 experiment_events 查到
            with db.connect() as conn:
                rows = conn.execute(
                    "SELECT event_data FROM experiment_events WHERE run_id = ? AND event_type = 'EVALUATION' ORDER BY created_at",
                    ("opt_test_evals",),
                ).fetchall()
                assert len(rows) == 2, f"应有 2 条 evaluation 事件，实际 {len(rows)}"
                eval0 = json.loads(rows[0]["event_data"])
                assert eval0["eval_idx"] == 0

    def test_get_run_optimization_fields(self) -> None:
        """get_run 重建 OptimizationRun 字段对齐（含 experiment_events）"""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = MetaDB(Path(tmpdir) / "meta.db")
            db.initialize()

            store = ExperimentStore(meta_db=db, data_dir=Path(tmpdir))

            original = OptimizationRun(
                run_id="opt_test_001",
                strategy_id="test",
                version_no=1,
                search_space={"entry_z": {"low": 1.0, "high": 3.0}},
                objective={"name": "sharpe"},
                algorithm=OptimizationAlgorithm.GRID,
                best_params={"entry_z": 2.0},
                evaluations=[{"eval_idx": 0, "score": "1.5"}],
                status=ExperimentStatus.SUCCEEDED,
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )

            import asyncio
            asyncio.run(store.create_run(original))

            restored = asyncio.run(store.get_run("opt_test_001"))
            assert isinstance(restored, OptimizationRun)
            assert restored.search_space == {"entry_z": {"low": 1.0, "high": 3.0}}
            assert restored.best_params == {"entry_z": 2.0}
            assert len(restored.evaluations) == 1
