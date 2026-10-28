"""实验存储 / 监控 / 调度 / Agent 网关端口（见 docs/03 第 12 节）"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.core.events import EventEnvelope
    from qps.core.models.artifact import Artifact
    from qps.core.models.experiment import BacktestRun, OptimizationRun
    from qps.core.models.task import TaskRun


@runtime_checkable
class ExperimentStorePort(Protocol):
    """实验存储端口

    语义要点：
    - compare 输出同构指标并列表格（含 OOS 列）
    """

    async def create_run(self, run: BacktestRun | OptimizationRun) -> None:
        """创建实验记录

        前置：run_id 唯一
        """
        ...

    async def get_run(self, run_id: str) -> BacktestRun | OptimizationRun:
        """查询实验记录

        前置：run_id 存在
        """
        ...

    async def list_runs(
        self,
        strategy_id: str | None = None,
        run_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """列出实验记录

        Args:
            strategy_id: 按策略过滤
            run_type: 按类型过滤
        """
        ...

    async def compare(self, run_ids: list[str]) -> dict[str, Any]:
        """对比多个实验

        前置：run_ids 非空且全部存在
        后置：返回同构指标对比报告（含 OOS 列）
        """
        ...


@runtime_checkable
class MonitoringPort(Protocol):
    """监控端口

    语义要点：
    - record 必须非阻塞（异步队列）
    - 禁止阻塞交易关键路径
    """

    async def record(self, event: EventEnvelope) -> None:
        """记录指标/事件（非阻塞）"""
        ...

    async def snapshot(self, kind: str) -> None:
        """拍摄快照

        Args:
            kind: "account" / "position" / "portfolio" / "system"
        """
        ...

    async def alert(self, level: str, message: str, context: dict[str, Any]) -> None:
        """发送告警

        Args:
            level: 告警级别
            message: 告警消息
            context: 上下文信息
        """
        ...


@runtime_checkable
class SchedulerPort(Protocol):
    """调度器端口

    语义要点：
    - schedule 返回 job_id
    - 任务执行记录 TaskRun
    """

    async def schedule(self, job: dict[str, Any]) -> str:
        """调度任务

        前置：job 含 job_type, cron|interval, params
        后置：返回 job_id
        """
        ...

    async def cancel(self, job_id: str) -> None:
        """取消任务"""
        ...

    async def list_jobs(self) -> list[dict[str, Any]]:
        """列出所有任务"""
        ...


@runtime_checkable
class AgentGatewayPort(Protocol):
    """Agent 网关端口（M7 离线研究增强）

    语义要点：
    - M7 实现，提供持久化本地 handler
    - Agent 产出 Artifact 不可变，默认只影响 Draft 状态
    """

    async def submit_task(self, agent_role: str, task_spec: dict[str, Any]) -> TaskRun:
        """提交 Agent 任务

        前置：agent_role 已注册 handler
        后置：返回 TaskRun
        """
        ...

    async def read_artifact(self, artifact_id: str) -> Artifact:
        """读取 AI 工件

        前置：artifact_id 存在
        """
        ...

    async def write_artifact(self, artifact: Artifact) -> str:
        """写入 AI 工件

        前置：artifact 字段完整
        后置：返回 artifact_id；Artifact 不可变
        """
        ...

    async def register_handler(self, agent_role: str, handler: Callable[..., Any]) -> None:
        """注册 Agent 处理器

        前置：agent_role 非空
        """
        ...
