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

__all__ = [
    "AgentGatewayPort",
    "BacktestEnginePort",
    "BrokerAdapterPort",
    "DataProviderPort",
    "ExchangeAdapterPort",
    "ExecutionEnginePort",
    "ExperimentStorePort",
    "FeaturePipelinePort",
    "LifecycleManagerPort",
    "MarketAdapterPort",
    "MonitoringPort",
    "OptimizerPort",
    "PortfolioManagerPort",
    "RiskEnginePort",
    "SchedulerPort",
    "SignalEnginePort",
    "StrategyRegistryPort",
    "StrategyRuntimePort",
]
