"""领域模型（Pydantic v2，frozen=True 默认）

按 docs/02 定义，按域分文件：
- instrument.py: Instrument, SymbolMapping, ExchangeMeta, ExchangeCalendar, CalendarBreak
- market.py: Bar, Tick, OrderBookSnapshot, FundingRate, OpenInterest, MarkPrice, FeatureSet
- adapter.py: AdapterStatus
- strategy.py: Strategy, StrategyVersion, StrategyParams, Deployment
- intent.py: TradingIntent
- order.py: Order, Trade
- account.py: Account, Position, Snapshot, StrategyAllocation, PortfolioRisk
- risk.py: RiskRule, RiskCheckResult, RiskEvent
- experiment.py: BacktestRun, OptimizationRun, ReportCard, Metrics
- task.py: TaskRun
- audit.py: AuditLog
- artifact.py: Artifact（M6 占位）
"""

from qps.core.models.account import (
    Account,
    Balance,
    PortfolioRisk,
    Position,
    Snapshot,
    StrategyAllocation,
)
from qps.core.models.adapter import AdapterStatus
from qps.core.models.artifact import Artifact
from qps.core.models.audit import AuditLog
from qps.core.models.experiment import (
    BacktestRun,
    DataRange,
    GateResult,
    Metrics,
    OptimizationRun,
    ReportCard,
)
from qps.core.models.instrument import (
    CalendarBreak,
    ExchangeCalendar,
    ExchangeMeta,
    Instrument,
    SymbolMapping,
)
from qps.core.models.intent import TradingIntent
from qps.core.models.market import (
    Bar,
    FeatureSet,
    FundingRate,
    MarkPrice,
    OpenInterest,
    OrderBookLevel,
    OrderBookSnapshot,
    Tick,
)
from qps.core.models.order import Order, Trade
from qps.core.models.risk import RiskCheckResult, RiskEvent, RiskRule, RuleCheckResult
from qps.core.models.strategy import Deployment, Strategy, StrategyParams, StrategyVersion
from qps.core.models.task import TaskRun

__all__ = [
    # account
    "Account",
    # adapter
    "AdapterStatus",
    # artifact
    "Artifact",
    # audit
    "AuditLog",
    # experiment
    "BacktestRun",
    "Balance",
    # market
    "Bar",
    "CalendarBreak",
    "DataRange",
    "Deployment",
    "ExchangeCalendar",
    "ExchangeMeta",
    "FeatureSet",
    "FundingRate",
    "GateResult",
    # instrument
    "Instrument",
    "MarkPrice",
    "Metrics",
    "OpenInterest",
    "OptimizationRun",
    # order
    "Order",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "PortfolioRisk",
    "Position",
    "ReportCard",
    "RiskCheckResult",
    "RiskEvent",
    # risk
    "RiskRule",
    "RuleCheckResult",
    "Snapshot",
    # strategy
    "Strategy",
    "StrategyAllocation",
    "StrategyParams",
    "StrategyVersion",
    "SymbolMapping",
    # task
    "TaskRun",
    "Tick",
    "Trade",
    # intent
    "TradingIntent",
]
