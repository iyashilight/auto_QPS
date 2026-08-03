"""审计日志模型（见 docs/02 第 3.7 节）"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import AuditActor
from qps.core.models.validators import validate_utc_datetime


class AuditLog(BaseModel):
    """审计日志（log_id 唯一，insert-only）"""

    log_id: str = Field(description="日志唯一 ID")
    actor: AuditActor = Field(description="操作者")
    action: str = Field(description="操作类型")
    target_type: str = Field(description="目标类型")
    target_id: str = Field(description="目标 ID")
    before: dict[str, Any] | None = Field(default=None, description="操作前状态（JSON）")
    after: dict[str, Any] | None = Field(default=None, description="操作后状态（JSON）")
    ts: datetime = Field(description="操作时间（UTC）")
    reason: str | None = Field(default=None, description="操作原因")

    model_config = {"frozen": True}

    @field_validator("ts")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)
