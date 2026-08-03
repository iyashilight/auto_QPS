"""Market Adapter 端口（见 docs/03 第 3 节）

前置条件：适配器未连接时调用 subscribe/fetch 将抛 BrokerNetworkError。
后置条件：subscribe 返回订阅 ID；unsubscribe 幂等。
异常：BrokerNetworkError（连接失败）；BrokerRateLimitError（限频）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from qps.core.models.market import Bar, FundingRate


@runtime_checkable
class MarketAdapterPort(Protocol):
    """Market Adapter 端口：实时行情订阅与历史数据采集

    语义要点：
    - 回调在事件循环内同步执行；回调抛异常只记录不中断订阅
    - subscribe_* 返回订阅 ID；unsubscribe 幂等
    - fetch_historical_bars 用于数据采集（分页/限频由 Exchange Adapter 内部处理）
    """

    async def connect(self) -> None:
        """连接行情数据源

        前置：未连接
        后置：连接成功
        异常：BrokerNetworkError
        """
        ...

    async def disconnect(self) -> None:
        """断开连接

        幂等操作
        """
        ...

    async def subscribe_bars(
        self,
        instrument_id: str,
        interval: str,
        callback: Callable[[Bar], None],
    ) -> str:
        """订阅 K 线数据

        前置：已连接
        后置：返回订阅 ID
        """
        ...

    async def subscribe_funding(
        self,
        instrument_id: str,
        callback: Callable[[FundingRate], None],
    ) -> str:
        """订阅资金费率数据

        前置：已连接
        后置：返回订阅 ID
        """
        ...

    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅

        幂等操作
        """
        ...

    async def fetch_historical_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """采集历史 K 线数据

        前置：已连接
        后置：按 open_time 升序
        异常：BrokerNetworkError；BrokerRateLimitError
        """
        ...

    def status(self) -> dict[str, Any]:
        """查询适配器连接状态

        后置：返回 AdapterStatus {connected, last_heartbeat, reconnect_count, latency_ms}
        """
        ...
