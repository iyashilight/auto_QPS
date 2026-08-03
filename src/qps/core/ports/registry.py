"""策略注册表与生命周期端口（见 docs/03 第 9 节）"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from qps.core.models.strategy import Strategy, StrategyVersion


@runtime_checkable
class StrategyRegistryPort(Protocol):
    """策略注册表端口

    语义要点：
    - save_version 幂等（同 content_hash 返回既有版本）
    - 版本号自动递增
    """

    async def save_version(self, code_pack: dict[str, Any]) -> StrategyVersion:
        """保存策略版本

        前置：code_pack 含 strategy_id, code_ref, params_schema 等
        后置：同 content_hash 返回既有版本，否则创建新版本
        """
        ...

    async def get_version(self, strategy_id: str, version_no: int) -> StrategyVersion:
        """获取策略版本

        前置：版本存在
        异常：DataError（版本不存在）
        """
        ...

    async def list_versions(self, strategy_id: str) -> list[StrategyVersion]:
        """列出策略的所有版本"""
        ...

    async def list_strategies(
        self, status: str | None = None
    ) -> list[Strategy]:
        """列出策略

        Args:
            status: 按状态过滤（可选）
        """
        ...


@runtime_checkable
class LifecycleManagerPort(Protocol):
    """生命周期管理端口

    语义要点：
    - transition 是状态迁移唯一入口
    - 目标状态非法或无证据 → ValueError
    - 每次迁移写 AuditLog 并广播 LIFECYCLE_TRANSITION 事件
    - 证据必须是实验/报告/部署 ID
    """

    async def transition(
        self,
        strategy_id: str,
        target_status: str,
        evidence_refs: list[str],
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """策略状态迁移

        前置：当前状态到 target_status 迁移合法
        后置：更新策略状态；写 AuditLog；广播事件
        异常：ValueError（非法迁移或无证据）
        """
        ...
