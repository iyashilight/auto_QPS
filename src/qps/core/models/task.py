"""任务运行模型（见 docs/02 第 3.7 节）"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import TaskStatus
from qps.core.models.validators import validate_optional_utc_datetime


class TaskRun(BaseModel):
    """任务运行记录（task_id 唯一）"""

    task_id: str = Field(description="任务唯一 ID")
    task_type: str = Field(description="任务类型")
    params: dict[str, Any] = Field(default_factory=dict, description="任务参数")
    status: TaskStatus = Field(default=TaskStatus.PENDING, description="任务状态")
    started_at: datetime | None = Field(default=None, description="开始时间（UTC）")
    finished_at: datetime | None = Field(default=None, description="完成时间（UTC）")
    error: str | None = Field(default=None, description="错误信息")

    model_config = {"frozen": True}

    @field_validator("started_at", "finished_at")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_optional_utc_datetime(v)
