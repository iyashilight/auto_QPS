"""端口定义（Protocol），跨模块调用的唯一合法接口

按 docs/03 定义，只写签名与 docstring，禁止任何实现。
"""

from qps.core.ports.backtest_engine import BacktestEnginePort
from qps.core.ports.broker_adapter import BrokerAdapterPort
from qps.core.ports.data_provider import DataProviderPort
from qps.core.ports.exchange_adapter import ExchangeAdapterPort
from qps.core.ports.feature_pipeline import FeaturePipelinePort
from qps.core.ports.market_adapter import MarketAdapterPort
from qps.core.ports.optimizer import OptimizerPort
from qps.core.ports.registry import LifecycleManagerPort, StrategyRegistryPort
from qps.core.ports.runtime import StrategyRuntimePort
from qps.core.ports.services import (
    AgentGatewayPort,
    ExperimentStorePort,
    MonitoringPort,
    SchedulerPort,
)
from qps.core.ports.trading import (
    ExecutionEnginePort,
    PortfolioManagerPort,
    RiskEnginePort,
    SignalEnginePort,
)
from qps.core.ports.v05 import (
    AlertPort,
    BlindChallengePort,
    BrokerPort,
    ChaosScenarioPort,
    ClockPort,
    FeatureRegistryPort,
    FormalResearchPort,
    LedgerPort,
    ManifestTrustPort,
    MarketRiskModelPort,
    OrderJournalPort,
    PointInTimeDataPort,
    PortfolioConstructionPort,
    ReconciliationPort,
    ReleaseStorePort,
    ResearchBudgetPort,
    StrategyHealthPort,
    WalkForwardPort,
)

__all__ = [
    "AgentGatewayPort",
    "AlertPort",
    "BacktestEnginePort",
    "BlindChallengePort",
    "BrokerAdapterPort",
    "BrokerPort",
    "ChaosScenarioPort",
    "ClockPort",
    "DataProviderPort",
    "ExchangeAdapterPort",
    "ExecutionEnginePort",
    "ExperimentStorePort",
    "FeaturePipelinePort",
    "FeatureRegistryPort",
    "FormalResearchPort",
    "LedgerPort",
    "LifecycleManagerPort",
    "ManifestTrustPort",
    "MarketAdapterPort",
    "MarketRiskModelPort",
    "MonitoringPort",
    "OptimizerPort",
    "OrderJournalPort",
    "PointInTimeDataPort",
    "PortfolioConstructionPort",
    "PortfolioManagerPort",
    "ReconciliationPort",
    "ReleaseStorePort",
    "ResearchBudgetPort",
    "RiskEnginePort",
    "SchedulerPort",
    "SignalEnginePort",
    "StrategyHealthPort",
    "StrategyRegistryPort",
    "StrategyRuntimePort",
    "WalkForwardPort",
]
