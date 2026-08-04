"""全局常量：时间、精度、状态枚举、ID 前缀、订单状态机

枚举值与 docs/02、docs/05 定义保持一致。
"""

from __future__ import annotations

from enum import StrEnum

# ── ID 前缀 ────────────────────────────────────────────────

EVENT_ID_PREFIX: str = "evt_"
CLIENT_ORDER_ID_PREFIX: str = "qps-"
BACKTEST_RUN_ID_PREFIX: str = "bt_"
DEPLOYMENT_ID_PREFIX: str = "dep_"
ORDER_ID_PREFIX: str = "ord_"
TRADE_ID_PREFIX: str = "trd_"
POSITION_ID_PREFIX: str = "pos_"
ALLOCATION_ID_PREFIX: str = "alloc_"
RULE_ID_PREFIX: str = "rule_"
TASK_ID_PREFIX: str = "task_"
ARTIFACT_ID_PREFIX: str = "art_"
LOG_ID_PREFIX: str = "log_"
CHECK_ID_PREFIX: str = "chk_"
RISK_EVENT_ID_PREFIX: str = "re_"
RUN_ID_PREFIX: str = "run_"
JOB_ID_PREFIX: str = "job_"


# ── 时间与精度常量 ─────────────────────────────────────────

DEFAULT_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h")
DEFAULT_RESEARCH_INTERVALS: tuple[str, ...] = ("5m", "15m", "1h")
UTC_TIMEZONE: str = "UTC"

# 精度
PRICE_DECIMAL_PLACES: int = 8
QUANTITY_DECIMAL_PLACES: int = 8

# 资金费率默认间隔
DEFAULT_FUNDING_INTERVAL: str = "8h"


# ── 枚举定义 ──────────────────────────────────────────────


class AssetClass(StrEnum):
    """资产类别"""

    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"


class OrderSide(StrEnum):
    """订单方向"""

    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    """订单状态（状态机见 _ORDER_TRANSITIONS）"""

    NEW = "NEW"
    PENDING_SUBMIT = "PENDING_SUBMIT"
    PENDING_ACK = "PENDING_ACK"
    WORKING = "WORKING"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderType(StrEnum):
    """订单类型"""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class Direction(StrEnum):
    """持仓方向"""

    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class Action(StrEnum):
    """交易动作"""

    OPEN = "OPEN"
    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    CLOSE = "CLOSE"
    FLIP = "FLIP"


class StrategyLifecycleStatus(StrEnum):
    """策略生命周期状态

    枚举值与架构文档状态机定义一致，保持领域模型稳定。
    """

    DRAFT = "DRAFT"
    BACKTESTING = "BACKTESTING"
    VALIDATED = "VALIDATED"
    PAPER_TRADING = "PAPER_TRADING"
    PRODUCTION = "PRODUCTION"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"
    DEPRECATED = "DEPRECATED"
    ARCHIVED = "ARCHIVED"


class StrategyStyle(StrEnum):
    """策略风格"""

    TREND = "TREND"
    MEAN_REVERSION = "MEAN_REVERSION"
    ARBITRAGE = "ARBITRAGE"
    GRID = "GRID"
    AI = "AI"
    OTHER = "OTHER"


class StrategySource(StrEnum):
    """策略来源"""

    MANUAL = "MANUAL"
    AGENT = "AGENT"


class MarginMode(StrEnum):
    """保证金模式"""

    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class InstrumentStatus(StrEnum):
    """标的状态"""

    ACTIVE = "ACTIVE"
    DELISTED = "DELISTED"
    SUSPENDED = "SUSPENDED"


class DataQuality(StrEnum):
    """数据质量标记"""

    OK = "OK"
    SUSPECT = "SUSPECT"


class BrokerMode(StrEnum):
    """Broker 运行模式"""

    PAPER = "PAPER"
    PRODUCTION = "PRODUCTION"


class AccountMode(StrEnum):
    """账户模式"""

    PAPER = "PAPER"
    PRODUCTION = "PRODUCTION"


class AccountType(StrEnum):
    """账户类型"""

    SPOT = "SPOT"
    PERPETUAL = "PERPETUAL"
    SUB = "SUB"


class AccountStatus(StrEnum):
    """账户状态"""

    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class DeploymentStatus(StrEnum):
    """部署状态"""

    CREATED = "CREATED"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    STOPPED = "STOPPED"
    CRASHED = "CRASHED"


class ExperimentStatus(StrEnum):
    """实验状态"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class ExperimentType(StrEnum):
    """实验类型"""

    BACKTEST = "BACKTEST"
    OPTIMIZATION = "OPTIMIZATION"


class TaskStatus(StrEnum):
    """任务状态"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class RiskVerdict(StrEnum):
    """风控判定结果"""

    ALLOW = "ALLOW"
    BLOCK = "BLOCK"
    MODIFY = "MODIFY"


class RiskLevel(StrEnum):
    """风控规则层级"""

    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"
    ORDER = "ORDER"
    MARKET = "MARKET"


class RiskEventType(StrEnum):
    """风控事件类型"""

    BREACH = "BREACH"
    COOLDOWN = "COOLDOWN"
    FREEZE = "FREEZE"
    KILLSWITCH = "KILLSWITCH"


class RiskSeverity(StrEnum):
    """风控事件严重程度"""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class AuditActor(StrEnum):
    """审计日志操作者"""

    MAN = "MAN"
    AGENT = "AGENT"
    SYSTEM = "SYSTEM"


class IntentPriority(StrEnum):
    """意图优先级"""

    NORMAL = "NORMAL"
    URGENT = "URGENT"


class OptimizationAlgorithm(StrEnum):
    """优化算法"""

    GRID = "GRID"
    RANDOM = "RANDOM"
    BAYESIAN = "BAYESIAN"
    GENETIC = "GENETIC"


class BacktestEngineMode(StrEnum):
    """回测引擎模式"""

    VECTORIZED = "VECTORIZED"
    EVENT_DRIVEN = "EVENT_DRIVEN"


class SlippageModel(StrEnum):
    """滑点模型"""

    FIXED = "FIXED"
    DEPTH = "DEPTH"


class FundingRateSource(StrEnum):
    """资金费率来源"""

    REAL = "REAL"
    FIXED = "FIXED"


class AlphaTrend(StrEnum):
    """Alpha Score 趋势"""

    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    DECLINING = "DECLINING"


class WeightAdjustment(StrEnum):
    """推荐权重调整"""

    INCREASE = "INCREASE"
    MAINTAIN = "MAINTAIN"
    REDUCE = "REDUCE"
    PAUSE = "PAUSE"


class FillTiming(StrEnum):
    """撮合时点"""

    BAR_CLOSE = "BAR_CLOSE"
    NEXT_BAR_OPEN = "NEXT_BAR_OPEN"
    BAR_AFTER_NEXT = "BAR_AFTER_NEXT"


class AdapterConnectionStatus(StrEnum):
    """适配器连接状态"""

    CONNECTED = "CONNECTED"
    DISCONNECTED = "DISCONNECTED"
    RECONNECTING = "RECONNECTING"


# ── 订单状态机 ─────────────────────────────────────────────

# 合法迁移表：key = 当前状态，value = 允许迁移到的状态集合
_ORDER_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.NEW: frozenset({OrderStatus.PENDING_SUBMIT}),
    OrderStatus.PENDING_SUBMIT: frozenset({OrderStatus.PENDING_ACK, OrderStatus.CANCELED, OrderStatus.REJECTED}),
    OrderStatus.PENDING_ACK: frozenset({OrderStatus.WORKING, OrderStatus.REJECTED, OrderStatus.CANCELED}),
    OrderStatus.WORKING: frozenset({
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
    }),
    OrderStatus.PARTIALLY_FILLED: frozenset({
        OrderStatus.WORKING,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.FILLED,
        OrderStatus.CANCELED,
        OrderStatus.EXPIRED,
    }),
    # 终态：不允许再迁移
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
}

# 终态集合
ORDER_TERMINAL_STATES: frozenset[OrderStatus] = frozenset({
    OrderStatus.FILLED,
    OrderStatus.CANCELED,
    OrderStatus.REJECTED,
    OrderStatus.EXPIRED,
})


def is_valid_order_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """检查订单状态迁移是否合法

    Args:
        from_status: 当前状态
        to_status: 目标状态

    Returns:
        True 表示迁移合法，False 表示非法
    """
    allowed = _ORDER_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def validate_order_transition(from_status: OrderStatus, to_status: OrderStatus) -> None:
    """校验订单状态迁移，非法时抛出 ValueError

    Args:
        from_status: 当前状态
        to_status: 目标状态

    Raises:
        ValueError: 迁移非法时抛出，包含详细错误信息
    """
    if not is_valid_order_transition(from_status, to_status):
        allowed = _ORDER_TRANSITIONS.get(from_status, frozenset())
        allowed_str = ", ".join(sorted(s.value for s in allowed)) if allowed else "无（终态）"
        msg = (
            f"订单状态迁移非法: {from_status.value} → {to_status.value}。"
            f"当前状态 {from_status.value} 允许的迁移目标: [{allowed_str}]"
        )
        raise ValueError(msg)


# ── 策略生命周期状态机 ──────────────────────────────────────

# 合法迁移表：key = 当前状态，value = 允许迁移到的状态集合
_LIFECYCLE_TRANSITIONS: dict[StrategyLifecycleStatus, frozenset[StrategyLifecycleStatus]] = {
    StrategyLifecycleStatus.DRAFT: frozenset({
        StrategyLifecycleStatus.BACKTESTING,
        StrategyLifecycleStatus.DISABLED,
        StrategyLifecycleStatus.DEPRECATED,
    }),
    StrategyLifecycleStatus.BACKTESTING: frozenset({
        StrategyLifecycleStatus.VALIDATED,
        StrategyLifecycleStatus.DRAFT,
        StrategyLifecycleStatus.DISABLED,
    }),
    StrategyLifecycleStatus.VALIDATED: frozenset({
        StrategyLifecycleStatus.PAPER_TRADING,
        StrategyLifecycleStatus.DISABLED,
        StrategyLifecycleStatus.DEPRECATED,
    }),
    StrategyLifecycleStatus.PAPER_TRADING: frozenset({
        StrategyLifecycleStatus.PRODUCTION,
        StrategyLifecycleStatus.VALIDATED,
        StrategyLifecycleStatus.PAUSED,
        StrategyLifecycleStatus.DISABLED,
    }),
    StrategyLifecycleStatus.PRODUCTION: frozenset({
        StrategyLifecycleStatus.PAUSED,
        StrategyLifecycleStatus.DISABLED,
        StrategyLifecycleStatus.DEPRECATED,
    }),
    StrategyLifecycleStatus.PAUSED: frozenset({
        StrategyLifecycleStatus.PAPER_TRADING,
        StrategyLifecycleStatus.PRODUCTION,
        StrategyLifecycleStatus.DISABLED,
        StrategyLifecycleStatus.DEPRECATED,
    }),
    StrategyLifecycleStatus.DISABLED: frozenset({
        StrategyLifecycleStatus.DRAFT,
        StrategyLifecycleStatus.DEPRECATED,
    }),
    StrategyLifecycleStatus.DEPRECATED: frozenset({
        StrategyLifecycleStatus.ARCHIVED,
    }),
    StrategyLifecycleStatus.ARCHIVED: frozenset(),
}


def is_valid_lifecycle_transition(
    from_status: StrategyLifecycleStatus,
    to_status: StrategyLifecycleStatus,
) -> bool:
    """检查策略生命周期状态迁移是否合法

    Args:
        from_status: 当前状态
        to_status: 目标状态

    Returns:
        True 表示迁移合法，False 表示非法
    """
    allowed = _LIFECYCLE_TRANSITIONS.get(from_status)
    if allowed is None:
        return False
    return to_status in allowed


def validate_lifecycle_transition(
    from_status: StrategyLifecycleStatus,
    to_status: StrategyLifecycleStatus,
) -> None:
    """校验策略生命周期状态迁移，非法时抛出 ValueError

    Args:
        from_status: 当前状态
        to_status: 目标状态

    Raises:
        ValueError: 迁移非法时抛出
    """
    if not is_valid_lifecycle_transition(from_status, to_status):
        allowed = _LIFECYCLE_TRANSITIONS.get(from_status, frozenset())
        allowed_str = ", ".join(sorted(s.value for s in allowed)) if allowed else "无（终态）"
        msg = (
            f"策略生命周期迁移非法: {from_status.value} → {to_status.value}。"
            f"当前状态 {from_status.value} 允许的迁移目标: [{allowed_str}]"
        )
        raise ValueError(msg)


# ── TradingIntent direction/action 合法组合 ──────────────────

# FLAT 仅与 CLOSE 组合；LONG/SHORT 与 OPEN/INCREASE/REDUCE/CLOSE/FLIP 全组合允许
_VALID_DIRECTION_ACTION_COMBOS: frozenset[tuple[Direction, Action]] = frozenset({
    # LONG/SHORT 与所有 action 组合
    *(combo for d in (Direction.LONG, Direction.SHORT) for combo in ((d, Action.OPEN), (d, Action.INCREASE), (d, Action.REDUCE), (d, Action.CLOSE), (d, Action.FLIP))),
    # FLAT 仅与 CLOSE 组合
    (Direction.FLAT, Action.CLOSE),
})


def is_valid_direction_action(direction: Direction, action: Action) -> bool:
    """检查 direction + action 组合是否合法

    Args:
        direction: 持仓方向
        action: 交易动作

    Returns:
        True 表示组合合法
    """
    return (direction, action) in _VALID_DIRECTION_ACTION_COMBOS
