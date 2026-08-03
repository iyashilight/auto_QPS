"""Exchange Adapter 端口（见 docs/03 第 4 节）

最低层端口：负责签名、限频、重连、心跳。
异常：BrokerAuthError / BrokerRateLimitError / BrokerNetworkError / BrokerBusinessError。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime


@runtime_checkable
class ExchangeAdapterPort(Protocol):
    """Exchange Adapter 端口：交易所底层连接

    语义要点：
    - 最低层端口：签名、限频、重连、心跳
    - request 超时必须抛 BrokerNetworkError
    - 限频触发抛 BrokerRateLimitError
    - 断线自动重连（指数退避）
    - 所有返回为交易所原始结构（dict），规范化在上一层完成
    """

    async def connect(self, credentials: dict[str, str] | None = None) -> None:
        """连接交易所

        前置：未连接
        后置：连接成功
        异常：BrokerAuthError / BrokerNetworkError
        """
        ...

    async def disconnect(self) -> None:
        """断开连接"""
        ...

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """发送 REST 请求

        前置：已连接
        后置：返回交易所原始响应
        异常：BrokerNetworkError / BrokerRateLimitError / BrokerBusinessError
        """
        ...

    async def subscribe(
        self,
        channel: str,
        payload: dict[str, Any],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """订阅 WebSocket 频道

        前置：已连接
        后置：返回订阅 ID
        """
        ...

    async def get_server_time(self) -> datetime:
        """获取交易所服务器时间"""
        ...

    def rate_limiter(self) -> dict[str, Any]:
        """获取限频器状态

        后置：返回 RateLimiter 信息（剩余配额、重置时间等）
        """
        ...

    def status(self) -> dict[str, Any]:
        """查询适配器连接状态

        后置：返回 AdapterStatus {connected, last_heartbeat, reconnect_count, latency_ms}
        """
        ...
