"""事件信封与事件类型常量（见 docs/05）

事件信封统一格式，事件类型与 docs/05 第 2 节清单一致。
事件类型只增不改，新增必须同步更新文档并升级 schema_version。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from qps.core.constants import EVENT_ID_PREFIX


class EventType(StrEnum):
    """事件类型常量（与 docs/05 第 2 节清单一致）"""

    # ── 行情事件 ──────────────────────────────────────────
    BAR_UPDATED = "BAR_UPDATED"
    TICK_RECEIVED = "TICK_RECEIVED"
    FUNDING_UPDATED = "FUNDING_UPDATED"
    OI_UPDATED = "OI_UPDATED"
    MARK_PRICE_UPDATED = "MARK_PRICE_UPDATED"

    # ── 策略/意图事件 ──────────────────────────────────────
    INTENT_CREATED = "INTENT_CREATED"
    INTENT_REJECTED = "INTENT_REJECTED"

    # ── 订单/成交事件 ──────────────────────────────────────
    ORDER_REQUESTED = "ORDER_REQUESTED"
    ORDER_UPDATED = "ORDER_UPDATED"
    EXECUTION_REPORT = "EXECUTION_REPORT"
    TRADE_CREATED = "TRADE_CREATED"

    # ── 持仓/账户事件 ──────────────────────────────────────
    POSITION_UPDATED = "POSITION_UPDATED"
    ACCOUNT_UPDATED = "ACCOUNT_UPDATED"

    # ── 风控事件 ──────────────────────────────────────────
    RISK_EVENT = "RISK_EVENT"

    # ── 生命周期事件 ──────────────────────────────────────
    LIFECYCLE_TRANSITION = "LIFECYCLE_TRANSITION"

    # ── 任务/调度事件 ──────────────────────────────────────
    TASK_RUN = "TASK_RUN"

    # ── AI Agent 事件（M6） ────────────────────────────────
    ARTIFACT_CREATED = "ARTIFACT_CREATED"

    # ── 系统事件 ──────────────────────────────────────────
    SYSTEM_HEALTH = "SYSTEM_HEALTH"


# 事件来源模块常量
EVENT_SOURCE_DATA_GATEWAY = "data.gateway"
EVENT_SOURCE_STRATEGY_RUNTIME = "strategy_runtime"
EVENT_SOURCE_SIGNAL_ENGINE = "signal_engine"
EVENT_SOURCE_PORTFOLIO = "portfolio"
EVENT_SOURCE_RISK = "risk"
EVENT_SOURCE_EXECUTION = "execution"
EVENT_SOURCE_BROKER_PAPER = "broker.paper"
EVENT_SOURCE_LIFECYCLE = "lifecycle"
EVENT_SOURCE_MONITORING = "monitoring"
EVENT_SOURCE_SCHEDULER = "scheduler"
EVENT_SOURCE_AGENT = "agent"


class EventEnvelope(BaseModel):
    """事件信封：所有事件必须包装在此信封中投递

    字段与 docs/05 第 1 节定义一致：
    - event_id: 全局唯一，幂等消费依据，前缀 evt_
    - event_type: 类型常量（见 EventType）
    - schema_version: payload 结构版本，升级只增版本不覆盖
    - ts: 事件产生时间（UTC ISO 8601）
    - source: 产生模块
    - payload: 事件体（按类型定义）
    """

    event_id: str = Field(description="事件唯一 ID，幂等键，前缀 evt_")
    event_type: EventType = Field(description="事件类型")
    schema_version: int = Field(default=1, description="payload 结构版本")
    ts: str = Field(description="事件时间（UTC ISO 8601）")
    source: str = Field(description="事件来源模块")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件载荷")

    model_config = {"frozen": True}

    def to_json(self) -> str:
        """序列化为 JSON 字符串"""
        return self.model_dump_json()

    @classmethod
    def from_json(cls, data: str) -> EventEnvelope:
        """从 JSON 字符串反序列化"""
        return cls.model_validate_json(data)


def make_event_id(uuid_suffix: str) -> str:
    """生成事件 ID（前缀 evt_ + uuid 后缀）

    Args:
        uuid_suffix: UUID 的后 8 位

    Returns:
        格式如 evt_9f2c1a01
    """
    return f"{EVENT_ID_PREFIX}{uuid_suffix}"
