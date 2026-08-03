"""回测引擎端口（见 docs/03 第 7 节）

前置条件：BacktestRequest 全字段显式。
后置条件：run 返回 BacktestRun；失败返回 FAILED 记录而非抛异常。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qps.core.models.experiment import BacktestRun


@runtime_checkable
class BacktestEnginePort(Protocol):
    """回测引擎端口

    语义要点：
    - run 返回 BacktestRun；失败必须返回 FAILED 记录
    - rerun(run_id)：用原请求原环境重跑，结果必须一致（确定性）
    - run_batch 并行执行；单任务失败不影响批次
    """

    async def run(self, request: dict[str, Any]) -> BacktestRun:
        """执行回测

        前置：request 全字段显式（数据版本、特征版本、撮合模式、成本配置等）
        后置：返回 BacktestRun（SUCCEEDED 时含 metrics/trades_ref）
        """
        ...

    async def run_batch(
        self,
        requests: list[dict[str, Any]],
        max_workers: int,
    ) -> list[BacktestRun]:
        """批量执行回测

        前置：max_workers 受配置上限约束
        后置：单任务失败不影响批次其他任务
        """
        ...

    async def rerun(self, run_id: str) -> BacktestRun:
        """重跑实验（确定性）

        前置：run_id 存在
        后置：结果必须与原运行一致
        """
        ...
