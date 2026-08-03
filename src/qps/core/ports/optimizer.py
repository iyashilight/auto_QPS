"""优化器端口（见 docs/03 第 8 节）"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qps.core.models.experiment import OptimizationRun


@runtime_checkable
class OptimizerPort(Protocol):
    """优化器端口

    语义要点：
    - 搜索过程增量写盘（evaluations 实时可查）
    - stop 优雅终止当前 worker
    - 结果必须含 best_params 及样本外指标
    - 随机算法必须接受固定 seed
    """

    async def search(self, request: dict[str, Any]) -> OptimizationRun:
        """执行参数搜索

        前置：request 含 strategy_version, search_space, objective, algorithm, budget
        后置：返回 OptimizationRun（含 best_params 和 best_metrics）
        """
        ...

    async def stop(self, run_id: str) -> None:
        """停止搜索（幂等）

        前置：run_id 存在
        后置：优雅终止当前 worker
        """
        ...

    async def status(self, run_id: str) -> OptimizationRun:
        """查询搜索状态

        前置：run_id 存在
        """
        ...
