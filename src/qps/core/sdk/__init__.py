"""策略 SDK 核心模块（见 docs/04 第 3-5 节）

提供策略基类、上下文、意图输出等核心能力。
"""

from qps.core.sdk.base_strategy import BaseStrategy
from qps.core.sdk.context import (
    FormalFeatureReader,
    MarketRegimeView,
    PositionView,
    StrategyContext,
)
from qps.core.sdk.formal import (
    DecisionInput,
    FormalDecisionInputFactory,
    FormalStrategyExecutor,
    VerifiedDecisionMaterialization,
)
from qps.core.sdk.intent_builder import IntentBuilder

__all__ = [
    "BaseStrategy",
    "DecisionInput",
    "FormalDecisionInputFactory",
    "FormalFeatureReader",
    "FormalStrategyExecutor",
    "IntentBuilder",
    "MarketRegimeView",
    "PositionView",
    "StrategyContext",
    "VerifiedDecisionMaterialization",
]
