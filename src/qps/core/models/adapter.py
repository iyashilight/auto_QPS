"""适配器模型（见 docs/02 第 3.2 节）"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from qps.core.models.validators import validate_optional_utc_datetime

__all__ = ["AdapterStatus"]


class AdapterStatus(BaseModel):
    """适配器连接状态快照"""

    connected: bool = Field(description="是否已连接")
    last_heartbeat: datetime | None = Field(default=None, description="最近心跳时间（UTC）")
    reconnect_count: int = Field(default=0, ge=0, description="断线重连次数")
    latency_ms: int | None = Field(default=None, ge=0, description="最近延迟（毫秒）")

    model_config = {"frozen": True}

    @field_validator("last_heartbeat")
    @classmethod
    def _validate_last_heartbeat_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_optional_utc_datetime(v)
