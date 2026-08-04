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
- experiment.py: BacktestRun, OptimizationRun, ReportCard, Metrics, AlphaScore, CostConfig, BacktestRequest
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
    AlphaScore,
    BacktestRequest,
    BacktestRun,
    ComparisonReport,
    CostConfig,
    DataRange,
    GateResult,
    Metrics,
    OptimizationRequest,
    OptimizationRun,
    ReportCard,
    RunSummary,
    WalkforwardConfigRequest,
    build_backtest_request,
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
    "Account",
    "AdapterStatus",
    "AlphaScore",
    "Artifact",
    "AuditLog",
    "BacktestRequest",
    "BacktestRun",
    "Balance",
    "Bar",
    "CalendarBreak",
    "ComparisonReport",
    "CostConfig",
    "DataRange",
    "Deployment",
    "ExchangeCalendar",
    "ExchangeMeta",
    "FeatureSet",
    "FundingRate",
    "GateResult",
    "Instrument",
    "MarkPrice",
    "Metrics",
    "OpenInterest",
    "OptimizationRequest",
    "OptimizationRun",
    "Order",
    "OrderBookLevel",
    "OrderBookSnapshot",
    "PortfolioRisk",
    "Position",
    "ReportCard",
    "RiskCheckResult",
    "RiskEvent",
    "RiskRule",
    "RuleCheckResult",
    "RunSummary",
    "Snapshot",
    "Strategy",
    "StrategyAllocation",
    "StrategyParams",
    "StrategyVersion",
    "SymbolMapping",
    "TaskRun",
    "Tick",
    "Trade",
    "TradingIntent",
    "WalkforwardConfigRequest",
    "build_backtest_request",
]
