"""M3 回测引擎 + 成本模型 + 评估指标测试"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qps.core.constants import (
    AlphaTrend,
    BacktestEngineMode,
    MarginMode,
    OptimizationAlgorithm,
    SlippageModel,
    WeightAdjustment,
)
from qps.core.costs import CostCalculator, TradeCost, cost_config_from_backtest_config
from qps.core.models.experiment import (
    CostConfig,
    Metrics,
    OptimizationRequest,
    build_backtest_request,
)
from qps.research.evaluation import (
    EquityPoint,
    TradeRecord,
    compute_alpha_score,
    compute_metrics,
    generate_report_card,
)

# ── CostConfig 测试 ──────────────────────────────────────


class TestCostConfig:
    """CostConfig 模型测试"""

    def test_default_values(self) -> None:
        """默认值正确"""
        config = CostConfig()
        assert config.maker_fee == Decimal("0.0002")
        assert config.taker_fee == Decimal("0.0004")
        assert config.slippage_model == SlippageModel.FIXED
        assert config.slippage_bps == 3
        assert config.leverage == Decimal("3")
        assert config.margin_mode == MarginMode.ISOLATED
        assert config.liquidation_margin_rate == Decimal("0.005")
        assert config.use_depth_snapshots is False

    def test_frozen(self) -> None:
        """不可变"""
        from pydantic import ValidationError

        config = CostConfig()
        with pytest.raises(ValidationError):
            config.maker_fee = Decimal("0.001")  # type: ignore[misc]


# ── CostCalculator 测试 ──────────────────────────────────


class TestCostCalculator:
    """成本计算器测试"""

    @pytest.fixture()
    def calc(self) -> CostCalculator:
        return CostCalculator(CostConfig())

    def test_compute_fee_taker(self, calc: CostCalculator) -> None:
        """Taker 手续费"""
        fee = calc.compute_fee(Decimal("1"), Decimal("50000"), is_maker=False)
        assert fee == Decimal("50000") * Decimal("0.0004")

    def test_compute_fee_maker(self, calc: CostCalculator) -> None:
        """Maker 手续费"""
        fee = calc.compute_fee(Decimal("1"), Decimal("50000"), is_maker=True)
        assert fee == Decimal("50000") * Decimal("0.0002")

    def test_compute_slippage_fixed(self, calc: CostCalculator) -> None:
        """固定滑点"""
        slippage = calc.compute_slippage(Decimal("1"), Decimal("50000"))
        # 50000 * 3 / 10000 = 15
        assert slippage == Decimal("15")

    def test_apply_slippage_price_buy(self, calc: CostCalculator) -> None:
        """买入滑点价上浮"""
        price = calc.apply_slippage_price(Decimal("50000"), "BUY")
        assert price > Decimal("50000")

    def test_apply_slippage_price_sell(self, calc: CostCalculator) -> None:
        """卖出滑点价下浮"""
        price = calc.apply_slippage_price(Decimal("50000"), "SELL")
        assert price < Decimal("50000")

    def test_compute_margin(self, calc: CostCalculator) -> None:
        """保证金计算"""
        margin = calc.compute_margin(Decimal("1"), Decimal("50000"))
        # 50000 / 3
        assert margin == Decimal("50000") / Decimal("3")

    def test_compute_liquidation_price_long(self, calc: CostCalculator) -> None:
        """多头强平价"""
        liq_price = calc.compute_liquidation_price(
            Decimal("50000"), Decimal("1"), "LONG",
        )
        # 50000 * (1 - 1/3 + 0.005) = 50000 * 0.6717
        assert liq_price < Decimal("50000")

    def test_compute_liquidation_price_short(self, calc: CostCalculator) -> None:
        """空头强平价"""
        liq_price = calc.compute_liquidation_price(
            Decimal("50000"), Decimal("1"), "SHORT",
        )
        assert liq_price > Decimal("50000")

    def test_is_liquidated_long(self, calc: CostCalculator) -> None:
        """多头强平判定"""
        # 极端低价应触发强平
        assert calc.is_liquidated("LONG", Decimal("50000"), Decimal("1")) is True

    def test_is_not_liquidated_long(self, calc: CostCalculator) -> None:
        """多头未强平"""
        assert calc.is_liquidated("LONG", Decimal("50000"), Decimal("49000")) is False

    def test_compute_funding_cost_long_positive(self, calc: CostCalculator) -> None:
        """多头正费率支付"""
        cost = calc.compute_funding_cost(
            Decimal("1"), Decimal("50000"), Decimal("0.0001"), "LONG",
        )
        assert cost > 0  # 多头支付

    def test_compute_funding_cost_short_positive(self, calc: CostCalculator) -> None:
        """空头正费率收取"""
        cost = calc.compute_funding_cost(
            Decimal("1"), Decimal("50000"), Decimal("0.0001"), "SHORT",
        )
        assert cost < 0  # 空头收取

    def test_compute_trade_cost(self, calc: CostCalculator) -> None:
        """完整交易成本"""
        tc = calc.compute_trade_cost(Decimal("1"), Decimal("50000"))
        assert isinstance(tc, TradeCost)
        assert tc.fee > 0
        assert tc.slippage > 0
        assert tc.notional == Decimal("50000")
        assert tc.margin > 0
        assert tc.total == tc.fee + tc.slippage + tc.funding


# ── TradeCost 测试 ───────────────────────────────────────


class TestTradeCost:
    """TradeCost 数据类测试"""

    def test_total(self) -> None:
        """总成本计算"""
        tc = TradeCost(fee=Decimal("10"), slippage=Decimal("5"), funding=Decimal("2"))
        assert tc.total == Decimal("17")

    def test_frozen(self) -> None:
        """不可变"""
        tc = TradeCost()
        with pytest.raises(AttributeError):  # dataclass frozen
            tc.fee = Decimal("100")  # type: ignore[misc]


# ── Metrics 测试 ─────────────────────────────────────────


class TestComputeMetrics:
    """指标计算测试"""

    def test_empty_equity_curve(self) -> None:
        """空权益曲线返回空指标"""
        metrics = compute_metrics([], [], Decimal("10000"))
        assert metrics.total_return == Decimal("0")
        assert metrics.trade_count == 0

    def test_simple_profit(self) -> None:
        """简单盈利场景"""
        trades = [
            TradeRecord(
                instrument_id="btc",
                side="LONG",
                action="CLOSE",
                quantity=Decimal("1"),
                price=Decimal("55000"),
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                pnl=Decimal("5000"),
            ),
        ]
        equity_curve = [
            EquityPoint(datetime(2026, 1, 1, tzinfo=UTC), Decimal("10000"), Decimal("10000"), Decimal("0")),
            EquityPoint(datetime(2026, 1, 2, tzinfo=UTC), Decimal("15000"), Decimal("15000"), Decimal("0")),
        ]
        metrics = compute_metrics(trades, equity_curve, Decimal("10000"))
        assert metrics.total_return == Decimal("0.5")
        assert metrics.trade_count == 1
        assert metrics.win_rate == Decimal("1")

    def test_funding_pnl(self) -> None:
        """资金费率盈亏"""
        trades = [
            TradeRecord(
                instrument_id="btc",
                side="LONG",
                action="FUNDING",
                quantity=Decimal("1"),
                price=Decimal("50000"),
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                funding=Decimal("-5"),  # 收取
            ),
        ]
        equity_curve = [
            EquityPoint(datetime(2026, 1, 1, tzinfo=UTC), Decimal("10000"), Decimal("10000"), Decimal("0")),
            EquityPoint(datetime(2026, 1, 2, tzinfo=UTC), Decimal("10005"), Decimal("10005"), Decimal("0")),
        ]
        metrics = compute_metrics(trades, equity_curve, Decimal("10000"))
        assert metrics.funding_pnl == Decimal("-5")

    def test_liquidation_count(self) -> None:
        """强平次数"""
        trades = [
            TradeRecord(
                instrument_id="btc",
                side="LONG",
                action="CLOSE",
                quantity=Decimal("1"),
                price=Decimal("1000"),
                timestamp=datetime(2026, 1, 1, tzinfo=UTC),
                pnl=Decimal("-49000"),
                is_liquidation=True,
            ),
        ]
        equity_curve = [
            EquityPoint(datetime(2026, 1, 1, tzinfo=UTC), Decimal("10000"), Decimal("10000"), Decimal("0")),
            EquityPoint(datetime(2026, 1, 2, tzinfo=UTC), Decimal("5000"), Decimal("5000"), Decimal("0")),
        ]
        metrics = compute_metrics(trades, equity_curve, Decimal("10000"))
        assert metrics.liquidation_count == 1

    def test_oos_metrics(self) -> None:
        """样本外指标"""
        is_trades = [
            TradeRecord(
                instrument_id="btc", side="LONG", action="CLOSE",
                quantity=Decimal("1"), price=Decimal("55000"),
                timestamp=datetime(2026, 1, 1, tzinfo=UTC), pnl=Decimal("5000"),
            ),
        ]
        oos_trades = [
            TradeRecord(
                instrument_id="btc", side="LONG", action="CLOSE",
                quantity=Decimal("1"), price=Decimal("52000"),
                timestamp=datetime(2026, 6, 1, tzinfo=UTC), pnl=Decimal("2000"),
            ),
        ]
        is_equity = [
            EquityPoint(datetime(2026, 1, 1, tzinfo=UTC), Decimal("10000"), Decimal("10000"), Decimal("0")),
            EquityPoint(datetime(2026, 1, 2, tzinfo=UTC), Decimal("15000"), Decimal("15000"), Decimal("0")),
        ]
        oos_equity = [
            EquityPoint(datetime(2026, 6, 1, tzinfo=UTC), Decimal("10000"), Decimal("10000"), Decimal("0")),
            EquityPoint(datetime(2026, 6, 2, tzinfo=UTC), Decimal("12000"), Decimal("12000"), Decimal("0")),
        ]
        metrics = compute_metrics(is_trades, is_equity, Decimal("10000"), oos_trades, oos_equity)
        assert "sharpe" in metrics.oos_metrics
        assert "max_drawdown" in metrics.oos_metrics


# ── ReportCard 测试 ──────────────────────────────────────


class TestGenerateReportCard:
    """报告卡生成测试"""

    def test_pass_conclusion(self) -> None:
        """通过结论（需要 OOS 数据才能 PASS）"""
        metrics = Metrics(
            total_return=Decimal("0.5"),
            annualized_return=Decimal("0.5"),
            max_drawdown=Decimal("0.1"),
            sharpe=Decimal("1.5"),
            win_rate=Decimal("0.6"),
            volatility=Decimal("0.2"),
            trade_count=50,
            profit_factor=Decimal("2"),
        )
        rc = generate_report_card("bt_001", "strat_1", 1, metrics)
        # 无 OOS 数据时 IS 通过但 OOS 失败
        assert rc.conclusion == "IS_PASS_OOS_FAIL"
        assert len(rc.gate_results) >= 1

    def test_fail_conclusion(self) -> None:
        """失败结论"""
        metrics = Metrics(
            total_return=Decimal("-0.5"),
            annualized_return=Decimal("-0.5"),
            max_drawdown=Decimal("0.5"),
            sharpe=Decimal("-1"),
            win_rate=Decimal("0.2"),
            volatility=Decimal("0.3"),
            trade_count=5,
            profit_factor=Decimal("0.5"),
        )
        rc = generate_report_card("bt_002", "strat_1", 1, metrics)
        assert rc.conclusion == "FAIL"


# ── Alpha Score 测试 ─────────────────────────────────────


class TestComputeAlphaScore:
    """Alpha Score 计算测试"""

    def test_six_dimensions(self) -> None:
        """六维度全部 0~1"""
        metrics = Metrics(
            total_return=Decimal("0.3"),
            annualized_return=Decimal("0.3"),
            max_drawdown=Decimal("0.1"),
            sharpe=Decimal("1.2"),
            win_rate=Decimal("0.6"),
            volatility=Decimal("0.15"),
            trade_count=100,
            profit_factor=Decimal("1.8"),
        )
        score = compute_alpha_score("bt_001", "strat_1", 1, metrics)
        assert Decimal("0") <= score.opportunity <= Decimal("1")
        assert Decimal("0") <= score.confidence <= Decimal("1")
        assert Decimal("0") <= score.stability <= Decimal("1")
        assert Decimal("0") <= score.market_fit <= Decimal("1")
        assert Decimal("0") <= score.decay_rate <= Decimal("1")
        assert Decimal("0") <= score.risk_cost <= Decimal("1")
        assert score.composite_score is None  # M3 默认 null
        assert score.trend == AlphaTrend.STABLE
        assert score.recommended_weight_adjustment == WeightAdjustment.MAINTAIN

    def test_deterministic(self) -> None:
        """确定性：相同输入相同输出"""
        metrics = Metrics(
            total_return=Decimal("0.2"),
            annualized_return=Decimal("0.2"),
            max_drawdown=Decimal("0.15"),
            sharpe=Decimal("0.8"),
            win_rate=Decimal("0.55"),
            volatility=Decimal("0.2"),
            trade_count=30,
            profit_factor=Decimal("1.5"),
        )
        s1 = compute_alpha_score("bt_001", "strat_1", 1, metrics)
        s2 = compute_alpha_score("bt_001", "strat_1", 1, metrics)
        assert s1.opportunity == s2.opportunity
        assert s1.confidence == s2.confidence
        assert s1.stability == s2.stability


# ── BacktestRequest 测试 ─────────────────────────────────


class TestBacktestRequest:
    """回测请求模型测试"""

    def test_build_backtest_request(self) -> None:
        """帮助函数构建请求"""
        req = build_backtest_request(
            strategy_id="test_strat",
            version_no=1,
            params={"entry_z": 2.0},
            instruments=["binance-btc-usdt-perp"],
            interval="5m",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 31, tzinfo=UTC),
        )
        assert req.strategy_id == "test_strat"
        assert req.engine_mode == BacktestEngineMode.EVENT_DRIVEN
        assert req.random_seed == 42

    def test_frozen(self) -> None:
        """不可变"""
        req = build_backtest_request(
            strategy_id="test",
            version_no=1,
            params={},
            instruments=["btc"],
            interval="5m",
            start=datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime(2026, 1, 2, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            req.strategy_id = "other"  # type: ignore[misc]


# ── OptimizationRequest 测试 ─────────────────────────────


class TestOptimizationRequest:
    """优化请求模型测试"""

    def test_walkforward_config(self) -> None:
        """Walk-forward 配置"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={"entry_z": {"low": 1.0, "high": 3.0, "step": 0.5}},
            objective={"name": "sharpe"},
            algorithm=OptimizationAlgorithm.GRID,
            data_version="v1",
            feature_version="v1",
        )
        assert req.walkforward_config.train_len == 60
        assert req.walkforward_config.oos_len == 20
        assert req.budget == 1000

    def test_frozen(self) -> None:
        """不可变"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={},
            objective={},
            algorithm=OptimizationAlgorithm.RANDOM,
            data_version="v1",
            feature_version="v1",
        )
        with pytest.raises(ValidationError):
            req.budget = 500  # type: ignore[misc]


# ── cost_config_from_backtest_config 测试 ─────────────────


class TestCostConfigConversion:
    """BacktestCostConfig → CostConfig 转换测试"""

    def test_conversion(self) -> None:
        """转换正确"""
        from qps.infra.config_models import BacktestCostConfig

        bt_cost = BacktestCostConfig()
        cost_config = cost_config_from_backtest_config(bt_cost)
        assert isinstance(cost_config, CostConfig)
        assert cost_config.maker_fee == bt_cost.maker_fee
        assert cost_config.taker_fee == bt_cost.taker_fee
