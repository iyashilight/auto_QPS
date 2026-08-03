"""T2 领域模型测试：字段校验、约束、不可变性、TradingIntent 规则"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qps.core.constants import (
    AccountStatus,
    AccountType,
    Action,
    AssetClass,
    AuditActor,
    BrokerMode,
    DataQuality,
    DeploymentStatus,
    Direction,
    ExperimentStatus,
    InstrumentStatus,
    IntentPriority,
    MarginMode,
    OptimizationAlgorithm,
    OrderSide,
    OrderStatus,
    OrderType,
    RiskEventType,
    RiskLevel,
    RiskSeverity,
    RiskVerdict,
    StrategyLifecycleStatus,
    StrategySource,
    StrategyStyle,
    TaskStatus,
)
from qps.core.models.account import (
    Account,
    PortfolioRisk,
    Position,
    StrategyAllocation,
)
from qps.core.models.artifact import Artifact
from qps.core.models.audit import AuditLog
from qps.core.models.experiment import (
    BacktestRun,
    DataRange,
    Metrics,
    OptimizationRun,
    ReportCard,
)
from qps.core.models.instrument import ExchangeMeta, Instrument, SymbolMapping
from qps.core.models.intent import TradingIntent
from qps.core.models.market import (
    Bar,
    FeatureSet,
    FundingRate,
    MarkPrice,
    OpenInterest,
    Tick,
)
from qps.core.models.order import Order, Trade
from qps.core.models.risk import RiskCheckResult, RiskEvent, RiskRule
from qps.core.models.strategy import Deployment, Strategy, StrategyVersion
from qps.core.models.task import TaskRun

# ── 辅助 ──────────────────────────────────────────────────

_T = datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC)


# ── Instrument ─────────────────────────────────────────────


class TestInstrument:
    def test_valid_spot(self) -> None:
        inst = Instrument(
            instrument_id="binance-btc-usdt-spot",
            internal_symbol="BTC-USDT",
            asset_class=AssetClass.SPOT,
            base_asset="BTC",
            quote_asset="USDT",
            exchange="binance",
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.00001"),
            min_notional=Decimal("10"),
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
            status=InstrumentStatus.ACTIVE,
        )
        assert inst.asset_class == AssetClass.SPOT
        assert inst.contract_size is None

    def test_valid_perpetual(self) -> None:
        inst = Instrument(
            instrument_id="binance-btc-usdt-perp",
            internal_symbol="BTC-USDT-PERP",
            asset_class=AssetClass.PERPETUAL,
            base_asset="BTC",
            quote_asset="USDT",
            exchange="binance",
            contract_size=Decimal("0.001"),
            tick_size=Decimal("0.1"),
            lot_size=Decimal("0.001"),
            min_notional=Decimal("5"),
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0004"),
            funding_interval="8h",
            margin_mode=MarginMode.ISOLATED,
            max_leverage=Decimal("125"),
            status=InstrumentStatus.ACTIVE,
        )
        assert inst.contract_size == Decimal("0.001")

    def test_spot_invalid_contract_size(self) -> None:
        """现货 contract_size 不为 1 时拒绝"""
        with pytest.raises(ValidationError, match="contract_size"):
            Instrument(
                instrument_id="binance-btc-usdt-spot",
                internal_symbol="BTC-USDT",
                asset_class=AssetClass.SPOT,
                base_asset="BTC",
                quote_asset="USDT",
                exchange="binance",
                contract_size=Decimal("0.001"),
                tick_size=Decimal("0.01"),
                lot_size=Decimal("0.00001"),
                min_notional=Decimal("10"),
                maker_fee=Decimal("0.001"),
                taker_fee=Decimal("0.001"),
                status=InstrumentStatus.ACTIVE,
            )

    def test_frozen(self) -> None:
        """模型不可变"""
        inst = Instrument(
            instrument_id="test",
            internal_symbol="TEST",
            asset_class=AssetClass.SPOT,
            base_asset="BTC",
            quote_asset="USDT",
            exchange="binance",
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.001"),
            min_notional=Decimal("10"),
            maker_fee=Decimal("0.001"),
            taker_fee=Decimal("0.001"),
            status=InstrumentStatus.ACTIVE,
        )
        with pytest.raises(ValidationError):
            inst.instrument_id = "changed"  # type: ignore[misc]


class TestSymbolMapping:
    def test_valid(self) -> None:
        m = SymbolMapping(
            instrument_id="binance-btc-usdt-perp",
            exchange="binance",
            exchange_symbol="BTCUSDT",
        )
        assert m.is_primary is True


class TestExchangeMeta:
    def test_valid(self) -> None:
        meta = ExchangeMeta(exchange_id="binance")
        assert meta.exchange_id == "binance"
        assert meta.rate_limits == []


# ── Bar ────────────────────────────────────────────────────


class TestBar:
    def test_valid(self) -> None:
        bar = Bar(
            instrument_id="binance-btc-usdt-perp",
            open_time=_T,
            interval="5m",
            open=Decimal("67000"),
            high=Decimal("67100"),
            low=Decimal("66900"),
            close=Decimal("67050"),
            volume=Decimal("100"),
            quote_volume=Decimal("6700000"),
            data_version="binance-3f2a91c4",
        )
        assert bar.quality == DataQuality.OK

    def test_timezone_required(self) -> None:
        """open_time 不带时区拒绝"""
        with pytest.raises(ValidationError, match="时区"):
            Bar(
                instrument_id="test",
                open_time=datetime(2026, 8, 2, 12, 0, 0),  # 无时区
                interval="5m",
                open=Decimal("1"),
                high=Decimal("2"),
                low=Decimal("0.5"),
                close=Decimal("1.5"),
                volume=Decimal("100"),
                quote_volume=Decimal("150"),
                data_version="v1",
            )


class TestTick:
    def test_valid(self) -> None:
        tick = Tick(
            instrument_id="test",
            ts=_T,
            price=Decimal("67000"),
            quantity=Decimal("0.1"),
            side=OrderSide.BUY,
        )
        assert tick.side == OrderSide.BUY

    def test_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            Tick(
                instrument_id="test",
                ts=datetime(2026, 8, 2, 12, 0, 0),
                price=Decimal("1"),
                quantity=Decimal("1"),
                side=OrderSide.BUY,
            )


class TestFundingRate:
    def test_valid(self) -> None:
        fr = FundingRate(
            instrument_id="test",
            funding_ts=_T,
            rate=Decimal("0.0001"),
        )
        assert fr.interval == "8h"


class TestOpenInterest:
    def test_valid(self) -> None:
        oi = OpenInterest(
            instrument_id="test",
            ts=_T,
            oi_value=Decimal("1000000"),
        )
        assert oi.oi_contracts is None


class TestMarkPrice:
    def test_valid(self) -> None:
        mp = MarkPrice(
            instrument_id="test",
            ts=_T,
            mark_price=Decimal("67000"),
        )
        assert mp.index_price is None


class TestFeatureSet:
    def test_valid(self) -> None:
        fs = FeatureSet(
            instrument_id="test",
            ts=_T,
            feature_name="atr_14",
            value=Decimal("500"),
            feature_version="feat-1",
            data_version="binance-3f2a91c4",
        )
        assert fs.feature_name == "atr_14"


# ── Strategy / StrategyVersion ─────────────────────────────


class TestStrategy:
    def test_valid(self) -> None:
        s = Strategy(
            strategy_id="trend_breakout",
            name="通道突破趋势策略",
            style=StrategyStyle.TREND,
            lifecycle_status=StrategyLifecycleStatus.DRAFT,
            created_at=_T,
            updated_at=_T,
        )
        assert s.source == StrategySource.MANUAL

    def test_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            Strategy(
                strategy_id="test",
                name="test",
                style=StrategyStyle.TREND,
                created_at=datetime(2026, 8, 2, 12, 0, 0),
                updated_at=_T,
            )


class TestStrategyVersion:
    def test_valid(self) -> None:
        sv = StrategyVersion(
            strategy_id="trend_breakout",
            version_no=1,
            code_ref="abc123:strategies/trend_breakout/",
            params_schema={"type": "object"},
            asset_class_support=[AssetClass.PERPETUAL],
            content_hash="sha256hash",
            created_at=_T,
        )
        assert sv.version_no == 1

    def test_version_no_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            StrategyVersion(
                strategy_id="test",
                version_no=0,
                code_ref="abc",
                params_schema={},
                asset_class_support=[AssetClass.SPOT],
                content_hash="hash",
                created_at=_T,
            )

    def test_frozen(self) -> None:
        """StrategyVersion 不可变"""
        sv = StrategyVersion(
            strategy_id="test",
            version_no=1,
            code_ref="abc",
            params_schema={},
            asset_class_support=[AssetClass.SPOT],
            content_hash="hash",
            created_at=_T,
        )
        with pytest.raises(ValidationError):
            sv.version_no = 2  # type: ignore[misc]


class TestDeployment:
    def test_valid(self) -> None:
        d = Deployment(
            deployment_id="dep_001",
            strategy_id="trend_breakout",
            version_no=1,
            account_id="acc_001",
            mode=BrokerMode.PAPER,
        )
        assert d.status == DeploymentStatus.CREATED


# ── TradingIntent ──────────────────────────────────────────


class TestTradingIntent:
    def _make_intent(
        self,
        direction: Direction = Direction.LONG,
        action: Action = Action.OPEN,
        quantity: Decimal | None = Decimal("0.05"),
        notional: Decimal | None = None,
        order_type: OrderType = OrderType.MARKET,
        limit_price: Decimal | None = None,
    ) -> TradingIntent:
        return TradingIntent(
            intent_id="6f1c2a1e-9b3d-4f7e-8a2c-1d5e9b7c0a11",
            strategy_id="trend_breakout",
            version_no=3,
            deployment_id="dep_001",
            instrument_id="BTC-USDT-PERP",
            direction=direction,
            action=action,
            quantity=quantity,
            notional=notional,
            order_type=order_type,
            limit_price=limit_price,
            valid_until=datetime(2026, 8, 2, 12, 10, 0, tzinfo=UTC),
        )

    def test_valid_market_order_with_quantity(self) -> None:
        intent = self._make_intent()
        assert intent.quantity == Decimal("0.05")
        assert intent.intent_priority == IntentPriority.NORMAL

    def test_valid_with_notional(self) -> None:
        intent = self._make_intent(quantity=None, notional=Decimal("5000"))
        assert intent.notional == Decimal("5000")

    def test_quantity_and_notional_both_set_rejected(self) -> None:
        """quantity 与 notional 同时指定 → 拒绝"""
        with pytest.raises(ValidationError, match="不能同时指定"):
            self._make_intent(quantity=Decimal("0.05"), notional=Decimal("5000"))

    def test_quantity_and_notional_both_none_rejected(self) -> None:
        """quantity 与 notional 都为空 → 拒绝"""
        with pytest.raises(ValidationError, match="必须指定其一"):
            self._make_intent(quantity=None, notional=None)

    def test_quantity_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="大于 0"):
            self._make_intent(quantity=Decimal("0"))

    def test_notional_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="大于 0"):
            self._make_intent(quantity=None, notional=Decimal("-1"))

    def test_limit_order_requires_limit_price(self) -> None:
        """LIMIT 订单必须指定 limit_price"""
        with pytest.raises(ValidationError, match="limit_price"):
            self._make_intent(order_type=OrderType.LIMIT, limit_price=None)

    def test_limit_price_must_be_positive(self) -> None:
        with pytest.raises(ValidationError, match="大于 0"):
            self._make_intent(order_type=OrderType.LIMIT, limit_price=Decimal("0"))

    def test_invalid_direction_action_combo(self) -> None:
        """FLAT + OPEN → 非法组合"""
        with pytest.raises(ValidationError, match="非法 direction/action"):
            self._make_intent(direction=Direction.FLAT, action=Action.OPEN, quantity=Decimal("1"))

    def test_flat_close_is_valid(self) -> None:
        """FLAT + CLOSE → 合法"""
        intent = self._make_intent(
            direction=Direction.FLAT, action=Action.CLOSE, quantity=Decimal("1")
        )
        assert intent.direction == Direction.FLAT

    def test_valid_until_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            TradingIntent(
                intent_id="test",
                strategy_id="test",
                version_no=1,
                deployment_id="dep_001",
                instrument_id="BTC-USDT-PERP",
                direction=Direction.LONG,
                action=Action.OPEN,
                quantity=Decimal("0.05"),
                order_type=OrderType.MARKET,
                valid_until=datetime(2026, 8, 2, 12, 10, 0),  # 无时区
            )

    def test_frozen(self) -> None:
        intent = self._make_intent()
        with pytest.raises(ValidationError):
            intent.direction = Direction.SHORT  # type: ignore[misc]


# ── Order / Trade ──────────────────────────────────────────


class TestOrder:
    def test_valid(self) -> None:
        order = Order(
            order_id="ord_001",
            client_order_id="qps-dep_001-a1b2c3d4",
            account_id="acc_001",
            deployment_id="dep_001",
            instrument_id="BTC-USDT-PERP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.05"),
            status=OrderStatus.NEW,
        )
        assert order.filled_quantity == Decimal("0")

    def test_frozen(self) -> None:
        order = Order(
            order_id="ord_001",
            client_order_id="qps-dep_001-abc",
            account_id="acc_001",
            deployment_id="dep_001",
            instrument_id="BTC-USDT-PERP",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0.05"),
            status=OrderStatus.NEW,
        )
        with pytest.raises(ValidationError):
            order.status = OrderStatus.FILLED  # type: ignore[misc]


class TestTrade:
    def test_valid(self) -> None:
        trade = Trade(
            trade_id="trd_001",
            order_id="ord_001",
            instrument_id="BTC-USDT-PERP",
            side=OrderSide.BUY,
            quantity=Decimal("0.05"),
            price=Decimal("67000"),
            ts=_T,
        )
        assert trade.fee == Decimal("0")

    def test_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            Trade(
                trade_id="trd_001",
                order_id="ord_001",
                instrument_id="test",
                side=OrderSide.BUY,
                quantity=Decimal("1"),
                price=Decimal("1"),
                ts=datetime(2026, 8, 2, 12, 0, 0),
            )


# ── Account / Position ────────────────────────────────────


class TestAccount:
    def test_valid(self) -> None:
        acc = Account(
            account_id="acc_001",
            mode=BrokerMode.PAPER,
            account_type=AccountType.PERPETUAL,
        )
        assert acc.status == AccountStatus.ACTIVE


class TestPosition:
    def test_valid(self) -> None:
        pos = Position(
            position_id="pos_001",
            account_id="acc_001",
            instrument_id="BTC-USDT-PERP",
            side=Direction.LONG,
            quantity=Decimal("0.05"),
            entry_price=Decimal("67000"),
            updated_ts=_T,
        )
        assert pos.realized_pnl == Decimal("0")

    def test_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            Position(
                position_id="pos_001",
                account_id="acc_001",
                instrument_id="test",
                side=Direction.LONG,
                quantity=Decimal("1"),
                entry_price=Decimal("1"),
                updated_ts=datetime(2026, 8, 2, 12, 0, 0),
            )


class TestStrategyAllocation:
    def test_valid(self) -> None:
        alloc = StrategyAllocation(
            allocation_id="alloc_001",
            account_id="acc_001",
            strategy_id="trend_breakout",
            weight=Decimal("0.5"),
            capital_limit=Decimal("5000"),
        )
        assert alloc.enabled is True

    def test_weight_range(self) -> None:
        """weight 必须在 0~1"""
        with pytest.raises(ValidationError):
            StrategyAllocation(
                allocation_id="alloc_001",
                account_id="acc_001",
                strategy_id="test",
                weight=Decimal("1.5"),
                capital_limit=Decimal("5000"),
            )


class TestPortfolioRisk:
    def test_valid(self) -> None:
        pr = PortfolioRisk(
            account_id="acc_001",
            snapshot_ts=_T,
            total_notional=Decimal("10000"),
        )
        assert pr.exposure_by_symbol == {}


# ── Risk ───────────────────────────────────────────────────


class TestRiskRule:
    def test_valid(self) -> None:
        rule = RiskRule(
            rule_id="rule_001",
            level=RiskLevel.ORDER,
            rule_type="max_notional",
            updated_by="admin",
            updated_at=_T,
        )
        assert rule.enabled is True


class TestRiskCheckResult:
    def test_valid(self) -> None:
        result = RiskCheckResult(
            check_id="chk_001",
            intent_id="intent_001",
            verdict=RiskVerdict.ALLOW,
            ts=_T,
        )
        assert result.rule_results == []


class TestRiskEvent:
    def test_valid(self) -> None:
        event = RiskEvent(
            event_id="re_001",
            event_type=RiskEventType.BREACH,
            severity=RiskSeverity.HIGH,
            triggered_at=_T,
        )
        assert event.action_taken is None


# ── Experiment ─────────────────────────────────────────────


class TestMetrics:
    def test_valid(self) -> None:
        m = Metrics(
            total_return=Decimal("0.18"),
            annualized_return=Decimal("0.36"),
            max_drawdown=Decimal("0.07"),
            sharpe=Decimal("1.9"),
            win_rate=Decimal("0.55"),
            volatility=Decimal("0.42"),
            trade_count=320,
            profit_factor=Decimal("1.5"),
        )
        assert m.funding_pnl is None


class TestBacktestRun:
    def test_valid(self) -> None:
        run = BacktestRun(
            run_id="bt_20260802_1a2b",
            strategy_id="trend_breakout",
            version_no=3,
            data_range=DataRange(start=_T, end=_T),
            data_version="binance-3f2a91c4",
            feature_version="feat-1",
            created_at=_T,
        )
        assert run.status == ExperimentStatus.PENDING


class TestOptimizationRun:
    def test_valid(self) -> None:
        run = OptimizationRun(
            run_id="opt_001",
            strategy_id="trend_breakout",
            version_no=3,
            algorithm=OptimizationAlgorithm.GRID,
            created_at=_T,
        )
        assert run.status == ExperimentStatus.PENDING


class TestReportCard:
    def test_valid(self) -> None:
        rc = ReportCard(
            run_id="bt_001",
            strategy_id="trend_breakout",
            version_no=3,
        )
        assert rc.conclusion is None


# ── TaskRun ────────────────────────────────────────────────


class TestTaskRun:
    def test_valid(self) -> None:
        task = TaskRun(
            task_id="task_001",
            task_type="data.collect",
        )
        assert task.status == TaskStatus.PENDING


# ── AuditLog ───────────────────────────────────────────────


class TestAuditLog:
    def test_valid(self) -> None:
        log = AuditLog(
            log_id="log_001",
            actor=AuditActor.MAN,
            action="promote",
            target_type="strategy",
            target_id="trend_breakout",
            ts=_T,
        )
        assert log.reason is None

    def test_timezone_required(self) -> None:
        with pytest.raises(ValidationError, match="时区"):
            AuditLog(
                log_id="log_001",
                actor=AuditActor.MAN,
                action="test",
                target_type="test",
                target_id="test",
                ts=datetime(2026, 8, 2, 12, 0, 0),
            )


# ── Artifact ───────────────────────────────────────────────


class TestArtifact:
    def test_valid(self) -> None:
        art = Artifact(
            artifact_id="art_001",
            artifact_type="strategy_draft",
            created_at=_T,
        )
        assert art.agent_role is None

    def test_frozen(self) -> None:
        art = Artifact(
            artifact_id="art_001",
            artifact_type="test",
            created_at=_T,
        )
        with pytest.raises(ValidationError):
            art.artifact_type = "changed"  # type: ignore[misc]


# ── 模型导入完整性 ──────────────────────────────────────────


class TestModelImports:
    """验证所有模型可从 qps.core.models 导入"""

    def test_all_models_importable(self) -> None:
        from qps.core.models import (  # noqa: F401
            Account,
            Artifact,
            AuditLog,
            BacktestRun,
            Balance,
            Bar,
            DataRange,
            Deployment,
            ExchangeMeta,
            FeatureSet,
            FundingRate,
            GateResult,
            Instrument,
            MarkPrice,
            Metrics,
            OpenInterest,
            OptimizationRun,
            Order,
            OrderBookLevel,
            OrderBookSnapshot,
            PortfolioRisk,
            Position,
            ReportCard,
            RiskCheckResult,
            RiskEvent,
            RiskRule,
            RuleCheckResult,
            Snapshot,
            Strategy,
            StrategyAllocation,
            StrategyParams,
            StrategyVersion,
            SymbolMapping,
            TaskRun,
            Tick,
            Trade,
            TradingIntent,
        )
