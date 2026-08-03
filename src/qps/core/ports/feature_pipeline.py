"""Feature Pipeline 端口（见 docs/03 第 6 节）

前置条件：特征函数必须已注册。
后置条件：无未来函数；compute_latest 结果缓存。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from typing import Any

    from qps.core.models.market import FeatureSet


@runtime_checkable
class FeaturePipelinePort(Protocol):
    """Feature Pipeline 端口：特征注册与计算

    语义要点：
    - 特征函数必须是纯函数，禁止 IO
    - 无未来函数校验：框架校验特征函数只消费截至当前 bar 的数据
    - compute_latest 用于实盘关键路径（结果缓存）
    - 特征注册 = 新版本；相同 name 再次注册视为新版本
    """

    def register_feature(
        self,
        name: str,
        func: Callable[..., Any],
        input_window: int,
        params: dict[str, Any],
    ) -> None:
        """注册特征函数

        前置：name 非空；func 为纯函数
        后置：相同 name 再次注册视为新版本
        """
        ...

    async def compute(
        self,
        instrument_id: str,
        interval: str,
        feature_names: list[str],
        start: datetime,
        end: datetime,
    ) -> list[FeatureSet]:
        """批量计算特征

        前置：feature_names 中所有特征已注册
        后置：按 ts 升序
        """
        ...

    async def compute_latest(
        self,
        instrument_id: str,
        interval: str,
        feature_names: list[str],
    ) -> dict[str, float]:
        """计算最新特征值（缓存）

        前置：feature_names 非空
        后置：返回 {feature_name: value}
        """
        ...

    def list_versions(self, feature_name: str) -> list[str]:
        """列出特征的所有版本"""
        ...
