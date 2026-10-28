"""策略与版本模型（见 docs/02 第 3.3 节）"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import (
    AssetClass,
    BrokerMode,
    DeploymentHealthStatus,
    DeploymentStatus,
    StrategyLifecycleStatus,
    StrategySource,
    StrategyStyle,
)
from qps.core.models.validators import validate_utc_datetime


class Strategy(BaseModel):
    """策略主表（可变）"""

    strategy_id: str = Field(description="唯一 ID，如 trend_breakout")
    name: str = Field(description="显示名")
    description: str | None = Field(default=None, description="中文说明")
    style: StrategyStyle = Field(description="策略风格")
    lifecycle_status: StrategyLifecycleStatus = Field(
        default=StrategyLifecycleStatus.DRAFT, description="生命周期状态"
    )
    tags: list[str] = Field(default_factory=list, description="标签")
    account_binding: str | None = Field(default=None, description="绑定账户 ID")
    source: StrategySource = Field(default=StrategySource.MANUAL, description="来源")
    created_at: datetime = Field(description="创建时间（UTC）")
    updated_at: datetime = Field(description="更新时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class StrategyVersion(BaseModel):
    """策略版本（不可变；strategy_id + version_no 唯一）

    版本一旦创建不可修改，无 setter、无 mutable 字段。
    """

    strategy_id: str = Field(description="所属策略")
    version_no: int = Field(ge=1, description="版本号，从 1 递增")
    code_ref: str = Field(description="git commit hash + strategies/<name>/")
    code_content: str = Field(default="", description="可恢复的不可变策略代码包内容")
    params_schema: dict[str, Any] = Field(description="JSON Schema（策略参数声明）")
    data_dependencies: list[str] = Field(default_factory=list, description="依赖的标的/数据")
    feature_dependencies: list[str] = Field(default_factory=list, description="依赖的特征名清单")
    runtime_constraints: dict[str, Any] = Field(default_factory=dict, description="静态检查约束")
    asset_class_support: list[AssetClass] = Field(description="支持的资产类别")
    content_hash: str = Field(description="代码包内容哈希（sha256）")
    source: StrategySource = Field(default=StrategySource.MANUAL, description="来源")
    created_at: datetime = Field(description="创建时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class StrategyParams(BaseModel):
    """策略参数快照（strategy_id + version_no + params_hash 唯一）"""

    strategy_id: str = Field(description="所属策略")
    version_no: int = Field(ge=1, description="版本号")
    params: dict[str, Any] = Field(description="参数字典")
    params_hash: str = Field(description="参数哈希")

    model_config = {"frozen": True}

class Deployment(BaseModel):
    """策略部署实例（一次运行部署）"""

    deployment_id: str = Field(description="唯一部署 ID")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    params: dict[str, Any] = Field(default_factory=dict, description="参数快照")
    account_id: str = Field(description="绑定账户")
    mode: BrokerMode = Field(description="运行模式")
    status: DeploymentStatus = Field(default=DeploymentStatus.CREATED, description="部署状态")
    release_id: str | None = Field(default=None, description="v0.5 不可变 Release ID")
    health_status: DeploymentHealthStatus | None = Field(default=None, description="v0.5 运行状态")
    created_at: datetime | None = Field(default=None, description="部署创建时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime | None) -> datetime | None:
        return validate_utc_datetime(value) if value is not None else None
