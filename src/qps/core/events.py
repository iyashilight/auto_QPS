"""事件信封与事件类型常量（见 05 文档）

本阶段只定义常量与信封模型，事件总线实现在 infra/event_bus.py。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """事件类型常量"""

    # 行情事件
    BAR_RECEIVED = "bar.received"
    TICK_RECEIVED = "tick.received"
    FUNDING_RATE_RECEIVED = "funding_rate.received"
    OPEN_INTEREST_RECEIVED = "open_interest.received"
    MARK_PRICE_RECEIVED = "mark_price.received"
    ORDER_BOOK_SNAPSHOT = "order_book.snapshot"

    # 策略事件
    TRADING_INTENT_CREATED = "trading_intent.created"

    # 信号事件
    SIGNAL_NORMALIZED = "signal.normalized"
    SIGNAL_REJECTED = "signal.rejected"

    # 风控事件
    RISK_CHECK_PASSED = "risk_check.passed"
    RISK_CHECK_BLOCKED = "risk_check.blocked"
    RISK_BREACH = "risk.breach"
    RISK_FREEZE = "risk.freeze"
    RISK_UNFREEZE = "risk.unfreeze"
    KILL_SWITCH_TRIGGERED = "risk.kill_switch"

    # 订单事件
    ORDER_SUBMITTED = "order.submitted"
    ORDER_ACKNOWLEDGED = "order.acknowledged"
    ORDER_PARTIALLY_FILLED = "order.partially_filled"
    ORDER_FILLED = "order.filled"
    ORDER_CANCELED = "order.canceled"
    ORDER_REJECTED = "order.rejected"
    ORDER_EXPIRED = "order.expired"

    # 账户/持仓事件
    POSITION_OPENED = "position.opened"
    POSITION_CLOSED = "position.closed"
    POSITION_UPDATED = "position.updated"
    ACCOUNT_UPDATED = "account.updated"

    # 策略生命周期事件
    STRATEGY_STATUS_CHANGED = "strategy.status_changed"
    DEPLOYMENT_STARTED = "deployment.started"
    DEPLOYMENT_STOPPED = "deployment.stopped"

    # 系统事件
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPED = "system.stopped"
    DATA_GAP_DETECTED = "data.gap_detected"
    WS_DISCONNECTED = "system.ws_disconnected"
    WS_RECONNECTED = "system.ws_reconnected"


class EventEnvelope(BaseModel):
    """事件信封：所有事件必须包装在此信封中投递"""

    event_id: str = Field(description="事件唯一 ID，幂等键")
    event_type: EventType = Field(description="事件类型")
    timestamp: str = Field(description="事件时间（UTC ISO 8601）")
    source: str = Field(description="事件来源模块")
    version: str = Field(default="1.0", description="事件 schema 版本")
    payload: dict[str, Any] = Field(default_factory=dict, description="事件载荷")
    correlation_id: str | None = Field(default=None, description="关联 ID（链路追踪）")
