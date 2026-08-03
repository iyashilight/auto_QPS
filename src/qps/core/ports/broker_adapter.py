"""Broker Adapter 端口（见 docs/03 第 5 节）

前置条件：已连接。
后置条件：place_order 幂等；cancel_order 幂等。
异常：BrokerBusinessError（拒单）；BrokerNetworkError（超时）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.core.models.account import Account, Position
    from qps.core.models.order import Order


@runtime_checkable
class BrokerAdapterPort(Protocol):
    """Broker Adapter 端口：订单与账户操作

    语义要点：
    - place_order 幂等：同一 client_order_id 不重复下单
    - cancel_order 幂等：订单已成交则返回当前状态
    - on_execution_report：注册回调处理成交/状态回报
    - 交易所拒单必须抛 BrokerBusinessError
    """

    async def connect(self) -> None:
        """连接 Broker

        异常：BrokerAuthError / BrokerNetworkError
        """
        ...

    async def disconnect(self) -> None:
        """断开连接"""
        ...

    async def place_order(self, order: Order) -> dict[str, Any]:
        """提交订单（幂等）

        前置：order.client_order_id 未重复提交
        后置：返回 OrderAck {client_order_id, broker_ref, status}
        异常：BrokerBusinessError（拒单）
        """
        ...

    async def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        """取消订单（幂等）

        前置：订单存在
        后置：返回当前状态；已成交则返回成交状态
        """
        ...

    async def get_order(self, client_order_id: str) -> dict[str, Any]:
        """查询订单状态"""
        ...

    async def get_account(self) -> Account:
        """查询账户信息"""
        ...

    async def get_positions(self) -> list[Position]:
        """查询持仓

        后置：mark_price 为 null 时由上层补标价
        """
        ...

    def on_execution_report(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """注册成交回报回调

        回调必须可重入（WS 重连后会重放部分回报，按 event_id 去重）
        """
        ...

    def on_account_update(self, callback: Callable[[Account], None]) -> None:
        """注册账户更新回调"""
        ...

    def status(self) -> dict[str, Any]:
        """查询适配器连接状态

        后置：返回 AdapterStatus {connected, last_heartbeat, reconnect_count, latency_ms}
        """
        ...
