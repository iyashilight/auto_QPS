"""T1 常量与枚举测试：枚举值、ID 前缀、订单状态机、生命周期状态机、direction/action 组合"""

from __future__ import annotations

import pytest

from qps.core.constants import (
    BACKTEST_RUN_ID_PREFIX,
    CLIENT_ORDER_ID_PREFIX,
    EVENT_ID_PREFIX,
    ORDER_TERMINAL_STATES,
    AccountMode,
    AccountStatus,
    AccountType,
    Action,
    AdapterConnectionStatus,
    AssetClass,
    AuditActor,
    BacktestEngineMode,
    BrokerMode,
    DataQuality,
    DeploymentStatus,
    Direction,
    ExperimentStatus,
    ExperimentType,
    FundingRateSource,
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
    SlippageModel,
    StrategyLifecycleStatus,
    StrategySource,
    StrategyStyle,
    TaskStatus,
    is_valid_direction_action,
    is_valid_lifecycle_transition,
    is_valid_order_transition,
    validate_lifecycle_transition,
    validate_order_transition,
)

# ── 枚举值正确性 ──────────────────────────────────────────


class TestEnumValues:
    """枚举值与 docs/02 定义一致"""

    def test_asset_class(self) -> None:
        assert AssetClass.SPOT == "SPOT"
        assert AssetClass.PERPETUAL == "PERPETUAL"

    def test_order_side(self) -> None:
        assert OrderSide.BUY == "BUY"
        assert OrderSide.SELL == "SELL"

    def test_order_status(self) -> None:
        assert OrderStatus.NEW == "NEW"
        assert OrderStatus.PENDING_SUBMIT == "PENDING_SUBMIT"
        assert OrderStatus.PENDING_ACK == "PENDING_ACK"
        assert OrderStatus.WORKING == "WORKING"
        assert OrderStatus.PARTIALLY_FILLED == "PARTIALLY_FILLED"
        assert OrderStatus.FILLED == "FILLED"
        assert OrderStatus.CANCELED == "CANCELED"
        assert OrderStatus.REJECTED == "REJECTED"
        assert OrderStatus.EXPIRED == "EXPIRED"

    def test_order_type(self) -> None:
        assert OrderType.MARKET == "MARKET"
        assert OrderType.LIMIT == "LIMIT"

    def test_direction(self) -> None:
        assert Direction.LONG == "LONG"
        assert Direction.SHORT == "SHORT"
        assert Direction.FLAT == "FLAT"

    def test_action(self) -> None:
        assert Action.OPEN == "OPEN"
        assert Action.INCREASE == "INCREASE"
        assert Action.REDUCE == "REDUCE"
        assert Action.CLOSE == "CLOSE"
        assert Action.FLIP == "FLIP"

    def test_strategy_lifecycle_status(self) -> None:
        assert StrategyLifecycleStatus.DRAFT == "DRAFT"
        assert StrategyLifecycleStatus.BACKTESTING == "BACKTESTING"
        assert StrategyLifecycleStatus.VALIDATED == "VALIDATED"
        assert StrategyLifecycleStatus.PAPER_TRADING == "PAPER_TRADING"
        assert StrategyLifecycleStatus.PRODUCTION == "PRODUCTION"
        assert StrategyLifecycleStatus.PAUSED == "PAUSED"
        assert StrategyLifecycleStatus.DISABLED == "DISABLED"
        assert StrategyLifecycleStatus.DEPRECATED == "DEPRECATED"
        assert StrategyLifecycleStatus.ARCHIVED == "ARCHIVED"

    def test_strategy_style(self) -> None:
        assert StrategyStyle.TREND == "TREND"
        assert StrategyStyle.MEAN_REVERSION == "MEAN_REVERSION"
        assert StrategyStyle.ARBITRAGE == "ARBITRAGE"
        assert StrategyStyle.GRID == "GRID"
        assert StrategyStyle.AI == "AI"
        assert StrategyStyle.OTHER == "OTHER"

    def test_strategy_source(self) -> None:
        assert StrategySource.MANUAL == "MANUAL"
        assert StrategySource.AGENT == "AGENT"

    def test_margin_mode(self) -> None:
        assert MarginMode.ISOLATED == "ISOLATED"
        assert MarginMode.CROSS == "CROSS"

    def test_instrument_status(self) -> None:
        assert InstrumentStatus.ACTIVE == "ACTIVE"
        assert InstrumentStatus.DELISTED == "DELISTED"
        assert InstrumentStatus.SUSPENDED == "SUSPENDED"

    def test_data_quality(self) -> None:
        assert DataQuality.OK == "OK"
        assert DataQuality.SUSPECT == "SUSPECT"

    def test_broker_mode(self) -> None:
        assert BrokerMode.PAPER == "PAPER"
        assert BrokerMode.PRODUCTION == "PRODUCTION"

    def test_account_mode(self) -> None:
        assert AccountMode.PAPER == "PAPER"
        assert AccountMode.PRODUCTION == "PRODUCTION"

    def test_account_type(self) -> None:
        assert AccountType.SPOT == "SPOT"
        assert AccountType.PERPETUAL == "PERPETUAL"
        assert AccountType.SUB == "SUB"

    def test_account_status(self) -> None:
        assert AccountStatus.ACTIVE == "ACTIVE"
        assert AccountStatus.FROZEN == "FROZEN"
        assert AccountStatus.CLOSED == "CLOSED"

    def test_deployment_status(self) -> None:
        assert DeploymentStatus.CREATED == "CREATED"
        assert DeploymentStatus.RUNNING == "RUNNING"
        assert DeploymentStatus.PAUSED == "PAUSED"
        assert DeploymentStatus.STOPPED == "STOPPED"
        assert DeploymentStatus.CRASHED == "CRASHED"

    def test_experiment_status(self) -> None:
        assert ExperimentStatus.PENDING == "PENDING"
        assert ExperimentStatus.RUNNING == "RUNNING"
        assert ExperimentStatus.SUCCEEDED == "SUCCEEDED"
        assert ExperimentStatus.FAILED == "FAILED"
        assert ExperimentStatus.CANCELED == "CANCELED"

    def test_experiment_type(self) -> None:
        assert ExperimentType.BACKTEST == "BACKTEST"
        assert ExperimentType.OPTIMIZATION == "OPTIMIZATION"

    def test_task_status(self) -> None:
        assert TaskStatus.PENDING == "PENDING"
        assert TaskStatus.RUNNING == "RUNNING"
        assert TaskStatus.SUCCEEDED == "SUCCEEDED"
        assert TaskStatus.FAILED == "FAILED"
        assert TaskStatus.CANCELED == "CANCELED"

    def test_risk_verdict(self) -> None:
        assert RiskVerdict.ALLOW == "ALLOW"
        assert RiskVerdict.BLOCK == "BLOCK"
        assert RiskVerdict.MODIFY == "MODIFY"

    def test_risk_level(self) -> None:
        assert RiskLevel.ACCOUNT == "ACCOUNT"
        assert RiskLevel.STRATEGY == "STRATEGY"
        assert RiskLevel.ORDER == "ORDER"
        assert RiskLevel.MARKET == "MARKET"

    def test_risk_event_type(self) -> None:
        assert RiskEventType.BREACH == "BREACH"
        assert RiskEventType.COOLDOWN == "COOLDOWN"
        assert RiskEventType.FREEZE == "FREEZE"
        assert RiskEventType.KILLSWITCH == "KILLSWITCH"

    def test_risk_severity(self) -> None:
        assert RiskSeverity.LOW == "LOW"
        assert RiskSeverity.MEDIUM == "MEDIUM"
        assert RiskSeverity.HIGH == "HIGH"
        assert RiskSeverity.CRITICAL == "CRITICAL"

    def test_audit_actor(self) -> None:
        assert AuditActor.MAN == "MAN"
        assert AuditActor.AGENT == "AGENT"
        assert AuditActor.SYSTEM == "SYSTEM"

    def test_intent_priority(self) -> None:
        assert IntentPriority.NORMAL == "NORMAL"
        assert IntentPriority.URGENT == "URGENT"

    def test_optimization_algorithm(self) -> None:
        assert OptimizationAlgorithm.GRID == "GRID"
        assert OptimizationAlgorithm.RANDOM == "RANDOM"
        assert OptimizationAlgorithm.BAYESIAN == "BAYESIAN"
        assert OptimizationAlgorithm.GENETIC == "GENETIC"

    def test_backtest_engine_mode(self) -> None:
        assert BacktestEngineMode.VECTORIZED == "VECTORIZED"
        assert BacktestEngineMode.EVENT_DRIVEN == "EVENT_DRIVEN"

    def test_slippage_model(self) -> None:
        assert SlippageModel.FIXED == "FIXED"
        assert SlippageModel.DEPTH == "DEPTH"

    def test_funding_rate_source(self) -> None:
        assert FundingRateSource.REAL == "REAL"
        assert FundingRateSource.FIXED == "FIXED"

    def test_adapter_connection_status(self) -> None:
        assert AdapterConnectionStatus.CONNECTED == "CONNECTED"
        assert AdapterConnectionStatus.DISCONNECTED == "DISCONNECTED"
        assert AdapterConnectionStatus.RECONNECTING == "RECONNECTING"


# ── ID 前缀 ───────────────────────────────────────────────


class TestIdPrefixes:
    """ID 前缀常量"""

    def test_event_id_prefix(self) -> None:
        assert EVENT_ID_PREFIX == "evt_"

    def test_client_order_id_prefix(self) -> None:
        assert CLIENT_ORDER_ID_PREFIX == "qps-"

    def test_backtest_run_id_prefix(self) -> None:
        assert BACKTEST_RUN_ID_PREFIX == "bt_"


# ── 订单状态机 ─────────────────────────────────────────────


class TestOrderStateMachine:
    """订单状态机迁移校验"""

    def test_valid_transitions(self) -> None:
        """合法迁移"""
        assert is_valid_order_transition(OrderStatus.NEW, OrderStatus.PENDING_SUBMIT)
        assert is_valid_order_transition(OrderStatus.PENDING_SUBMIT, OrderStatus.PENDING_ACK)
        assert is_valid_order_transition(OrderStatus.PENDING_SUBMIT, OrderStatus.CANCELED)
        assert is_valid_order_transition(OrderStatus.PENDING_SUBMIT, OrderStatus.REJECTED)
        assert is_valid_order_transition(OrderStatus.PENDING_ACK, OrderStatus.WORKING)
        assert is_valid_order_transition(OrderStatus.PENDING_ACK, OrderStatus.REJECTED)
        assert is_valid_order_transition(OrderStatus.WORKING, OrderStatus.PARTIALLY_FILLED)
        assert is_valid_order_transition(OrderStatus.WORKING, OrderStatus.FILLED)
        assert is_valid_order_transition(OrderStatus.WORKING, OrderStatus.CANCELED)
        assert is_valid_order_transition(OrderStatus.WORKING, OrderStatus.EXPIRED)
        assert is_valid_order_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED)
        assert is_valid_order_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.CANCELED)

    def test_invalid_transitions(self) -> None:
        """非法迁移"""
        # NEW 不能直接到 WORKING
        assert not is_valid_order_transition(OrderStatus.NEW, OrderStatus.WORKING)
        # FILLED 是终态
        assert not is_valid_order_transition(OrderStatus.FILLED, OrderStatus.CANCELED)
        # CANCELED 是终态
        assert not is_valid_order_transition(OrderStatus.CANCELED, OrderStatus.WORKING)
        # REJECTED 是终态
        assert not is_valid_order_transition(OrderStatus.REJECTED, OrderStatus.NEW)
        # EXPIRED 是终态
        assert not is_valid_order_transition(OrderStatus.EXPIRED, OrderStatus.WORKING)
        # 不能从 WORKING 回到 NEW
        assert not is_valid_order_transition(OrderStatus.WORKING, OrderStatus.NEW)

    def test_validate_raises_on_invalid(self) -> None:
        """非法迁移抛出 ValueError"""
        with pytest.raises(ValueError, match="订单状态迁移非法"):
            validate_order_transition(OrderStatus.FILLED, OrderStatus.CANCELED)

        with pytest.raises(ValueError, match="订单状态迁移非法"):
            validate_order_transition(OrderStatus.NEW, OrderStatus.WORKING)

    def test_validate_ok_on_valid(self) -> None:
        """合法迁移不抛异常"""
        validate_order_transition(OrderStatus.NEW, OrderStatus.PENDING_SUBMIT)
        validate_order_transition(OrderStatus.WORKING, OrderStatus.FILLED)

    def test_terminal_states(self) -> None:
        """终态集合正确"""
        assert OrderStatus.FILLED in ORDER_TERMINAL_STATES
        assert OrderStatus.CANCELED in ORDER_TERMINAL_STATES
        assert OrderStatus.REJECTED in ORDER_TERMINAL_STATES
        assert OrderStatus.EXPIRED in ORDER_TERMINAL_STATES
        assert OrderStatus.WORKING not in ORDER_TERMINAL_STATES
        assert OrderStatus.NEW not in ORDER_TERMINAL_STATES

    def test_self_transition_not_allowed(self) -> None:
        """状态不能迁移到自身（除 PARTIALLY_FILLED）"""
        assert not is_valid_order_transition(OrderStatus.NEW, OrderStatus.NEW)
        assert not is_valid_order_transition(OrderStatus.WORKING, OrderStatus.WORKING)
        # PARTIALLY_FILLED → PARTIALLY_FILLED 是合法的（多次部分成交）
        assert is_valid_order_transition(OrderStatus.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED)


# ── 策略生命周期状态机 ──────────────────────────────────────


class TestLifecycleStateMachine:
    """策略生命周期状态机迁移校验"""

    def test_valid_transitions(self) -> None:
        """合法迁移"""
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.DRAFT, StrategyLifecycleStatus.BACKTESTING)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.BACKTESTING, StrategyLifecycleStatus.VALIDATED)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.VALIDATED, StrategyLifecycleStatus.PAPER_TRADING)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.PAPER_TRADING, StrategyLifecycleStatus.PRODUCTION)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.PRODUCTION, StrategyLifecycleStatus.PAUSED)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.PAUSED, StrategyLifecycleStatus.PRODUCTION)
        assert is_valid_lifecycle_transition(StrategyLifecycleStatus.DISABLED, StrategyLifecycleStatus.DRAFT)

    def test_invalid_transitions(self) -> None:
        """非法迁移"""
        # DRAFT 不能直接到 PRODUCTION
        assert not is_valid_lifecycle_transition(StrategyLifecycleStatus.DRAFT, StrategyLifecycleStatus.PRODUCTION)
        # ARCHIVED 是终态
        assert not is_valid_lifecycle_transition(StrategyLifecycleStatus.ARCHIVED, StrategyLifecycleStatus.DRAFT)
        # VALIDATED 不能回退到 DRAFT
        assert not is_valid_lifecycle_transition(StrategyLifecycleStatus.VALIDATED, StrategyLifecycleStatus.DRAFT)

    def test_validate_raises_on_invalid(self) -> None:
        """非法迁移抛出 ValueError"""
        with pytest.raises(ValueError, match="策略生命周期迁移非法"):
            validate_lifecycle_transition(StrategyLifecycleStatus.DRAFT, StrategyLifecycleStatus.PRODUCTION)

    def test_validate_ok_on_valid(self) -> None:
        """合法迁移不抛异常"""
        validate_lifecycle_transition(StrategyLifecycleStatus.DRAFT, StrategyLifecycleStatus.BACKTESTING)


# ── direction/action 合法组合 ──────────────────────────────


class TestDirectionActionCombo:
    """TradingIntent direction + action 合法组合校验"""

    def test_long_with_all_actions(self) -> None:
        """LONG 与所有 action 组合合法"""
        for action in (Action.OPEN, Action.INCREASE, Action.REDUCE, Action.CLOSE, Action.FLIP):
            assert is_valid_direction_action(Direction.LONG, action), f"LONG + {action.value} 应合法"

    def test_short_with_all_actions(self) -> None:
        """SHORT 与所有 action 组合合法"""
        for action in (Action.OPEN, Action.INCREASE, Action.REDUCE, Action.CLOSE, Action.FLIP):
            assert is_valid_direction_action(Direction.SHORT, action), f"SHORT + {action.value} 应合法"

    def test_flat_only_with_close(self) -> None:
        """FLAT 仅与 CLOSE 组合合法"""
        assert is_valid_direction_action(Direction.FLAT, Action.CLOSE)
        # FLAT 与其他 action 不合法
        for action in (Action.OPEN, Action.INCREASE, Action.REDUCE, Action.FLIP):
            assert not is_valid_direction_action(Direction.FLAT, action), f"FLAT + {action.value} 应非法"
