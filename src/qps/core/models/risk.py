"""风控模型（见 docs/02 第 3.7 节）"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import RiskEventType, RiskLevel, RiskSeverity, RiskVerdict
from qps.core.models.validators import validate_utc_datetime


class RiskRule(BaseModel):
    """风控规则（rule_id 唯一）"""

    rule_id: str = Field(description="规则唯一 ID")
    level: RiskLevel = Field(description="规则层级")
    rule_type: str = Field(description="规则类型")
    params: dict[str, Any] = Field(default_factory=dict, description="规则参数")
    enabled: bool = Field(default=True, description="是否启用")
    version: int = Field(default=1, ge=1, description="规则版本")
    updated_by: str = Field(description="更新者")
    updated_at: datetime = Field(description="更新时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("updated_at")
    @classmethod
    def _validate_updated_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class RuleCheckResult(BaseModel):
    """单条规则检查结果"""

    rule_id: str = Field(description="规则 ID")
    passed: bool = Field(description="是否通过")
    detail: str | None = Field(default=None, description="检查详情")

    model_config = {"frozen": True}

class RiskCheckResult(BaseModel):
    """风控检查结果（check_id 唯一）"""

    check_id: str = Field(description="检查唯一 ID")
    intent_id: str = Field(description="关联意图 ID")
    rule_results: list[RuleCheckResult] = Field(default_factory=list, description="各规则检查结果")
    verdict: RiskVerdict = Field(description="风控判定")
    ts: datetime = Field(description="检查时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("ts")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class RiskEvent(BaseModel):
    """风控事件（event_id 唯一）"""

    event_id: str = Field(description="事件唯一 ID")
    event_type: RiskEventType = Field(description="事件类型")
    severity: RiskSeverity = Field(description="严重程度")
    context: dict[str, Any] = Field(default_factory=dict, description="上下文")
    triggered_at: datetime = Field(description="触发时间（UTC）")
    action_taken: str | None = Field(default=None, description="采取的措施")

    model_config = {"frozen": True}

    @field_validator("triggered_at")
    @classmethod
    def _validate_triggered_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)
