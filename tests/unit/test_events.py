"""T3 事件系统测试：事件信封、事件类型、事件总线（同步/异步/幂等/序列化）"""

from __future__ import annotations

import asyncio

import pytest

from qps.core.events import EventEnvelope, EventType, make_event_id
from qps.infra.event_bus import EventBus, EventBusError

# ── 辅助 ──────────────────────────────────────────────────

def _make_event(
    event_type: EventType = EventType.BAR_UPDATED,
    event_id: str = "evt_9f2c1a01",
    source: str = "data.gateway",
) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        event_type=event_type,
        ts="2026-08-02T12:00:00.123Z",
        source=source,
        payload={"instrument_id": "BTC-USDT-PERP"},
    )


# ── 事件类型常量 ──────────────────────────────────────────


class TestEventType:
    """事件类型与 docs/05 第 2 节清单一致"""

    def test_market_events(self) -> None:
        assert EventType.BAR_UPDATED == "BAR_UPDATED"
        assert EventType.TICK_RECEIVED == "TICK_RECEIVED"
        assert EventType.FUNDING_UPDATED == "FUNDING_UPDATED"
        assert EventType.OI_UPDATED == "OI_UPDATED"
        assert EventType.MARK_PRICE_UPDATED == "MARK_PRICE_UPDATED"

    def test_intent_events(self) -> None:
        assert EventType.INTENT_CREATED == "INTENT_CREATED"
        assert EventType.INTENT_REJECTED == "INTENT_REJECTED"

    def test_order_events(self) -> None:
        assert EventType.ORDER_REQUESTED == "ORDER_REQUESTED"
        assert EventType.ORDER_UPDATED == "ORDER_UPDATED"
        assert EventType.EXECUTION_REPORT == "EXECUTION_REPORT"
        assert EventType.TRADE_CREATED == "TRADE_CREATED"

    def test_position_account_events(self) -> None:
        assert EventType.POSITION_UPDATED == "POSITION_UPDATED"
        assert EventType.ACCOUNT_UPDATED == "ACCOUNT_UPDATED"

    def test_risk_events(self) -> None:
        assert EventType.RISK_EVENT == "RISK_EVENT"

    def test_lifecycle_events(self) -> None:
        assert EventType.LIFECYCLE_TRANSITION == "LIFECYCLE_TRANSITION"

    def test_task_events(self) -> None:
        assert EventType.TASK_RUN == "TASK_RUN"

    def test_agent_events(self) -> None:
        assert EventType.ARTIFACT_CREATED == "ARTIFACT_CREATED"

    def test_system_events(self) -> None:
        assert EventType.SYSTEM_HEALTH == "SYSTEM_HEALTH"


# ── 事件信封 ──────────────────────────────────────────────


class TestEventEnvelope:
    def test_valid(self) -> None:
        event = _make_event()
        assert event.event_id == "evt_9f2c1a01"
        assert event.event_type == EventType.BAR_UPDATED
        assert event.schema_version == 1
        assert event.source == "data.gateway"

    def test_frozen(self) -> None:
        event = _make_event()
        with pytest.raises((TypeError, ValueError)):
            event.event_id = "changed"  # type: ignore[misc]

    def test_json_serialization(self) -> None:
        """事件可 JSON 序列化与反序列化"""
        event = _make_event()
        json_str = event.to_json()
        assert isinstance(json_str, str)
        assert "evt_9f2c1a01" in json_str

        restored = EventEnvelope.from_json(json_str)
        assert restored.event_id == event.event_id
        assert restored.event_type == event.event_type
        assert restored.payload == event.payload

    def test_make_event_id(self) -> None:
        eid = make_event_id("9f2c1a01")
        assert eid == "evt_9f2c1a01"
        assert eid.startswith("evt_")


# ── 事件总线：同步直连 ──────────────────────────────────────


class TestEventBusDirect:
    """同步直连投递测试"""

    def test_sync_delivery(self) -> None:
        """同步直连：回调按序执行"""
        bus = EventBus()
        received: list[EventEnvelope] = []
        bus.subscribe(lambda e: received.append(e), mode="direct")

        event = _make_event()
        bus.publish(event)

        assert len(received) == 1
        assert received[0].event_id == "evt_9f2c1a01"

    def test_sync_order(self) -> None:
        """同步直连：多个事件按序投递"""
        bus = EventBus()
        received: list[str] = []
        bus.subscribe(lambda e: received.append(e.event_id), mode="direct")

        bus.publish(_make_event(event_id="evt_001"))
        bus.publish(_make_event(event_id="evt_002"))
        bus.publish(_make_event(event_id="evt_003"))

        assert received == ["evt_001", "evt_002", "evt_003"]

    def test_sync_callback_exception_propagates(self) -> None:
        """同步直连：回调抛异常向上传播"""
        bus = EventBus()

        def bad_callback(e: EventEnvelope) -> None:
            raise RuntimeError("callback error")

        bus.subscribe(bad_callback, mode="direct")

        with pytest.raises(RuntimeError, match="callback error"):
            bus.publish(_make_event())

    def test_subscribe_by_event_type(self) -> None:
        """按事件类型订阅"""
        bus = EventBus()
        received: list[EventType] = []
        bus.subscribe(
            lambda e: received.append(e.event_type),
            event_type=EventType.ORDER_UPDATED,
            mode="direct",
        )

        bus.publish(_make_event(event_type=EventType.BAR_UPDATED, event_id="evt_bar"))
        bus.publish(_make_event(event_type=EventType.ORDER_UPDATED, event_id="evt_order"))
        bus.publish(_make_event(event_type=EventType.INTENT_CREATED, event_id="evt_intent"))

        assert received == [EventType.ORDER_UPDATED]


# ── 事件总线：幂等 ──────────────────────────────────────────


class TestEventBusIdempotency:
    """幂等消费测试"""

    def test_duplicate_event_id_consumed_once(self) -> None:
        """重复 event_id 只消费一次"""
        bus = EventBus()
        received: list[EventEnvelope] = []
        bus.subscribe(lambda e: received.append(e), mode="direct")

        event = _make_event(event_id="evt_dup001")
        bus.publish(event)
        bus.publish(event)  # 重复发布

        assert len(received) == 1

    def test_is_consumed(self) -> None:
        """is_consumed 正确反映消费状态"""
        bus = EventBus()
        bus.subscribe(lambda e: None, mode="direct")

        assert not bus.is_consumed("evt_001")
        bus.publish(_make_event(event_id="evt_001"))
        assert bus.is_consumed("evt_001")


# ── 事件总线：异步队列 ──────────────────────────────────────


class TestEventBusAsync:
    """异步队列投递测试"""

    @pytest.mark.asyncio
    async def test_async_delivery(self) -> None:
        """异步队列：事件被消费"""
        bus = EventBus()
        received: list[EventEnvelope] = []

        async def async_callback(e: EventEnvelope) -> None:
            received.append(e)

        bus.subscribe(async_callback, mode="queue")

        event = _make_event()
        await bus.publish_async(event)

        # 等待队列消费
        await asyncio.sleep(0.2)

        assert len(received) == 1
        assert received[0].event_id == "evt_9f2c1a01"

    @pytest.mark.asyncio
    async def test_async_idempotency(self) -> None:
        """异步队列：重复 event_id 只消费一次"""
        bus = EventBus()
        received: list[EventEnvelope] = []

        async def async_callback(e: EventEnvelope) -> None:
            received.append(e)

        bus.subscribe(async_callback, mode="queue")

        event = _make_event(event_id="evt_dup002")
        await bus.publish_async(event)
        await bus.publish_async(event)  # 重复

        await asyncio.sleep(0.2)

        assert len(received) == 1


# ── 事件总线：取消订阅 ──────────────────────────────────────


class TestEventBusUnsubscribe:
    def test_unsubscribe(self) -> None:
        """取消订阅后不再收到事件"""
        bus = EventBus()
        received: list[EventEnvelope] = []
        sub_id = bus.subscribe(lambda e: received.append(e), mode="direct")

        bus.publish(_make_event(event_id="evt_001"))
        assert len(received) == 1

        bus.unsubscribe(sub_id)
        bus.publish(_make_event(event_id="evt_002"))
        assert len(received) == 1  # 不再收到

    def test_unsubscribe_idempotent(self) -> None:
        """取消订阅幂等"""
        bus = EventBus()
        sub_id = bus.subscribe(lambda e: None, mode="direct")
        bus.unsubscribe(sub_id)
        bus.unsubscribe(sub_id)  # 不报错


# ── 事件总线：非法模式 ──────────────────────────────────────


class TestEventBusInvalidMode:
    def test_invalid_mode_raises(self) -> None:
        """非法投递模式抛出 EventBusError"""
        bus = EventBus()
        with pytest.raises(EventBusError, match="不合法的投递模式"):
            bus.subscribe(lambda e: None, mode="invalid")


# ── 事件总线：重置 ──────────────────────────────────────────


class TestEventBusReset:
    def test_reset(self) -> None:
        """重置后状态清空"""
        bus = EventBus()
        bus.subscribe(lambda e: None, mode="direct")
        bus.publish(_make_event())

        assert bus.is_consumed("evt_9f2c1a01")

        bus.reset()
        assert not bus.is_consumed("evt_9f2c1a01")
