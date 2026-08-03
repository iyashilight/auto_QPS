"""策略运行时端口（见 docs/03 第 11 节）"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qps.core.models.strategy import Deployment


@runtime_checkable
class StrategyRuntimePort(Protocol):
    """策略运行时端口

    语义要点：
    - 每个 deployment 独立进程/task
    - stop 后未处理意图作废
    - 启动前执行静态检查，不通过则启动失败
    - 崩溃自动重启仅恢复进程，不恢复交易权限
    """

    async def start(self, deployment: Deployment) -> None:
        """启动策略运行

        前置：静态检查通过
        后置：deployment 状态变为 RUNNING
        异常：BacktestError（静态检查失败）
        """
        ...

    async def stop(self, deployment_id: str) -> None:
        """停止策略运行（幂等）

        前置：deployment_id 存在
        后置：未处理意图作废
        """
        ...

    async def restart(self, deployment_id: str) -> None:
        """重启策略运行

        前置：deployment_id 存在
        后置：进程重启，交易权限不自动恢复
        """
        ...

    async def status(self, deployment_id: str) -> dict[str, Any]:
        """查询运行时状态

        返回 RuntimeStatus：{status, heartbeat_ts, last_bar_ts, error_count, last_error}
        """
        ...
