"""交易链路端口（见 docs/03 第 10 节）

Signal → Portfolio → Risk → Execution
全链路中任一拒绝都要产生事件与日志，不允许静默丢弃。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qps.core.models.account import PortfolioRisk, StrategyAllocation
    from qps.core.models.intent import TradingIntent
    from qps.core.models.market import Bar, FundingRate, Tick
    from qps.core.models.order import Order
    from qps.core.models.risk import RiskCheckResult, RiskRule


@runtime_checkable
class SignalEnginePort(Protocol):
    """Signal Engine 端口

    语义要点：
    - ingest_intent：校验白名单字段、去重限频、有效期检查
    - 拒绝返回 IntentResult(status=REJECTED, reason)
    """

    async def ingest_intent(self, intent: TradingIntent) -> dict[str, Any]:
        """接收交易意图

        前置：intent 字段完整
        后置：通过返回 IntentResult(status=ACCEPTED)；拒绝返回 (status=REJECTED, reason)
        """
        ...


@runtime_checkable
class PortfolioManagerPort(Protocol):
    """Portfolio Manager 端口

    语义要点：
    - on_intent：意图 → 一个或多个 OrderRequest
    - 不通过返回空列表 + 原因（不抛异常）
    """

    async def on_intent(self, intent: TradingIntent) -> list[dict[str, Any]]:
        """处理交易意图

        前置：intent 已通过 Signal Engine
        后置：返回 OrderRequest 列表；不通过返回空列表
        """
        ...

    async def get_allocation(self, strategy_id: str) -> StrategyAllocation:
        """查询策略资金分配"""
        ...

    async def get_portfolio_risk(self, account_id: str) -> PortfolioRisk:
        """查询组合风险快照"""
        ...

    async def merge_positions(self, account_id: str) -> None:
        """合并同标同向持仓"""
        ...


@runtime_checkable
class RiskEnginePort(Protocol):
    """Risk Engine 端口

    语义要点：
    - pre_trade_check：强制同步语义（即使 async，下单前必须完成）
    - verdict=BLOCK 时 ExecutionEngine 禁止提交
    """

    async def pre_trade_check(self, order_request: dict[str, Any]) -> RiskCheckResult:
        """交易前风控检查

        前置：order_request 合法
        后置：返回 RiskCheckResult（verdict=ALLOW/BLOCK/MODIFY）
        """
        ...

    async def on_order_update(self, order: Order) -> None:
        """订单状态更新通知"""
        ...

    async def on_market_update(self, event: Bar | Tick | FundingRate) -> None:
        """市场数据更新通知"""
        ...

    async def register_rule(self, rule: RiskRule) -> None:
        """注册风控规则"""
        ...

    async def status(self) -> dict[str, Any]:
        """查询风控状态"""
        ...


@runtime_checkable
class ExecutionEnginePort(Protocol):
    """Execution Engine 端口

    语义要点：
    - submit_order：risk_result 必须为 ALLOW
    - cancel：按 deployment_id 或 instrument_id 批量取消
    - reconcile：本地 vs 交易所快照对比
    """

    async def submit_order(
        self,
        order_request: dict[str, Any],
        risk_result: RiskCheckResult,
    ) -> Order:
        """提交订单

        前置：risk_result.verdict == ALLOW
        后置：生成 client_order_id 并调 Broker 提交
        异常：RiskBlockError（risk_result 非 ALLOW）
        """
        ...

    async def cancel(
        self,
        deployment_id: str | None = None,
        instrument_id: str | None = None,
    ) -> list[Order]:
        """取消订单

        前置：deployment_id 或 instrument_id 至少一个非空
        后置：返回被取消的订单列表
        """
        ...

    async def reconcile(self, account_id: str) -> dict[str, Any]:
        """对账

        前置：account_id 存在
        后置：返回差异清单 {field, local, exchange, action_suggestion}
        """
        ...

    async def status(self) -> dict[str, Any]:
        """查询执行引擎状态"""
        ...
