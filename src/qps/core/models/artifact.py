"""AI Agent 工件模型（M6 占位，见 docs/02 第 3.7 节）"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.models.validators import validate_utc_datetime


class Artifact(BaseModel):
    """AI Agent 工件（artifact_id 唯一，不可变，M6 实现）"""

    artifact_id: str = Field(description="工件唯一 ID")
    artifact_type: str = Field(description="工件类型")
    schema_version: str = Field(default="1", description="schema 版本")
    content: dict[str, Any] = Field(default_factory=dict, description="工件内容")
    strategy_id: str | None = Field(default=None, description="关联策略 ID")
    run_id: str | None = Field(default=None, description="关联实验 ID")
    agent_role: str | None = Field(default=None, description="Agent 角色")
    created_at: datetime = Field(description="创建时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)
