"""全局常量：时间、精度、状态枚举"""

from enum import StrEnum


class AssetClass(StrEnum):
    """资产类别"""

    SPOT = "spot"
    PERPETUAL = "perpetual"


class OrderSide(StrEnum):
    """订单方向"""

    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    """订单状态"""

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

    MARKET = "market"
    LIMIT = "limit"


class Direction(StrEnum):
    """持仓方向"""

    LONG = "long"
    SHORT = "short"
    FLAT = "flat"


class Action(StrEnum):
    """交易动作"""

    OPEN = "open"
    INCREASE = "increase"
    REDUCE = "reduce"
    CLOSE = "close"
    FLIP = "flip"


class StrategyLifecycleStatus(StrEnum):
    """策略生命周期状态

    枚举值（value）与架构文档 5 章状态机定义一致，保持领域模型稳定。
    """

    DRAFT = "Draft"
    BACKTESTING = "Backtesting"
    VALIDATED = "Validated"
    PAPER_TRADING = "PaperTrading"
    PRODUCTION = "Production"
    PAUSED = "Paused"
    DISABLED = "Disabled"
    DEPRECATED = "Deprecated"
    ARCHIVED = "Archived"


class StrategyStyle(StrEnum):
    """策略风格"""

    TREND = "trend"
    MEAN_REVERSION = "mean_reversion"
    ARBITRAGE = "arbitrage"
    GRID = "grid"
    AI = "ai"


class MarginMode(StrEnum):
    """保证金模式"""

    ISOLATED = "ISOLATED"
    CROSS = "CROSS"


class TaskStatus(StrEnum):
    """任务状态"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class DataQuality(StrEnum):
    """数据质量标记"""

    OK = "ok"
    SUSPECT = "suspect"


# 时间与精度常量（使用 tuple 保证不可变）
DEFAULT_INTERVALS: tuple[str, ...] = ("1m", "5m", "15m", "1h")
DEFAULT_RESEARCH_INTERVALS: tuple[str, ...] = ("5m", "15m", "1h")
UTC_TIMEZONE: str = "UTC"

# 精度
PRICE_DECIMAL_PLACES: int = 8
QUANTITY_DECIMAL_PLACES: int = 8
