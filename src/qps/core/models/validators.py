"""共享 Pydantic 字段验证器

提取各模型中重复的验证逻辑为共享函数，避免 15+ 处重复。
"""

from __future__ import annotations

from datetime import datetime


def validate_utc_datetime(v: datetime) -> datetime:
    """验证 datetime 必须带时区信息（UTC）

    用法：
        @field_validator("ts")
        @classmethod
        def _validate_ts_timezone(cls, v: datetime) -> datetime:
            return validate_utc_datetime(v)
    """
    if v.tzinfo is None:
        raise ValueError("必须带时区信息（UTC）")
    return v


def validate_optional_utc_datetime(v: datetime | None) -> datetime | None:
    """验证可选 datetime：非 None 时必须带时区信息（UTC）"""
    if v is not None and v.tzinfo is None:
        raise ValueError("必须带时区信息（UTC）")
    return v
