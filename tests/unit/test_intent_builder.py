"""IntentBuilder 白名单校验测试（见 docs/04 第 5 节）

测试所有拒绝分支、补全字段、INTENT_CREATED 事件载荷。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from qps.core.sdk.intent_builder import (
    IntentBuilder,
    IntentValidationError,
)


class TestWhitelistValidation:
    """白名单校验测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
        )

    def test_unknown_field_rejected(self) -> None:
        """未知字段拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "unknown_field": "value",
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "未知字段" in str(exc_info.value)

    def test_missing_required_direction(self) -> None:
        """缺少必填字段 direction"""
        intent = {
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "缺少必填字段" in str(exc_info.value)

    def test_missing_required_action(self) -> None:
        """缺少必填字段 action"""
        intent = {
            "direction": "LONG",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "缺少必填字段" in str(exc_info.value)

    def test_missing_required_order_type(self) -> None:
        """缺少必填字段 order_type"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "quantity": 0.1,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "缺少必填字段" in str(exc_info.value)


class TestQuantityNotionalMutualExclusion:
    """quantity/notional 二选一测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
        )

    def test_both_quantity_and_notional_rejected(self) -> None:
        """同时指定 quantity 和 notional 拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "notional": 100.0,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "不能同时指定" in str(exc_info.value)

    def test_neither_quantity_nor_notional_rejected(self) -> None:
        """都不指定拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "必须指定其一" in str(exc_info.value)

    def test_quantity_zero_or_negative_rejected(self) -> None:
        """quantity <= 0 拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "必须大于 0" in str(exc_info.value)

    def test_notional_zero_or_negative_rejected(self) -> None:
        """notional <= 0 拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "notional": -100,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "必须大于 0" in str(exc_info.value)

    def test_quantity_valid(self) -> None:
        """quantity 合法值"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["quantity"] == 0.1

    def test_notional_valid(self) -> None:
        """notional 合法值"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "notional": 100.0,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["notional"] == 100.0


class TestLimitOrderValidation:
    """LIMIT 订单校验测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
        )

    def test_limit_without_price_rejected(self) -> None:
        """LIMIT 订单无 limit_price 拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "LIMIT",
            "quantity": 0.1,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "limit_price" in str(exc_info.value)

    def test_limit_with_invalid_price_rejected(self) -> None:
        """LIMIT 订单 limit_price <= 0 拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "LIMIT",
            "quantity": 0.1,
            "limit_price": 0,
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "必须大于 0" in str(exc_info.value)

    def test_limit_with_valid_price(self) -> None:
        """LIMIT 订单有合法 limit_price"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "LIMIT",
            "quantity": 0.1,
            "limit_price": 67000.0,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["limit_price"] == 67000.0


class TestReasonTagsValidation:
    """reason_tags 格式校验测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
        )

    def test_reason_tags_not_list_rejected(self) -> None:
        """reason_tags 不是列表拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "reason_tags": "not_a_list",
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "必须是列表" in str(exc_info.value)

    def test_reason_tags_invalid_format_rejected(self) -> None:
        """reason_tags 格式错误拒绝"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "reason_tags": ["Invalid-Tag"],
        }
        with pytest.raises(IntentValidationError) as exc_info:
            self.builder.build_and_emit(intent)
        assert "格式错误" in str(exc_info.value)

    def test_reason_tags_valid(self) -> None:
        """reason_tags 合法格式"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "reason_tags": ["mean_reversion", "z_score_entry"],
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["reason_tags"] == ["mean_reversion", "z_score_entry"]


class TestIntentCompletion:
    """意图补全字段测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=2,
            deployment_id="dep_test",
        )

    def test_intent_id_completed(self) -> None:
        """intent_id 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert "intent_id" in event.payload["intent"]
        assert len(event.payload["intent"]["intent_id"]) > 0

    def test_strategy_id_completed(self) -> None:
        """strategy_id 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["strategy_id"] == "test_strategy"

    def test_version_no_completed(self) -> None:
        """version_no 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["version_no"] == 2

    def test_deployment_id_completed(self) -> None:
        """deployment_id 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["deployment_id"] == "dep_test"

    def test_valid_until_completed(self) -> None:
        """valid_until 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert "valid_until" in event.payload["intent"]
        # 验证是 ISO 格式时间字符串
        datetime.fromisoformat(event.payload["intent"]["valid_until"])

    def test_default_priority_completed(self) -> None:
        """默认 intent_priority 补全"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["intent_priority"] == "NORMAL"

    def test_custom_priority_preserved(self) -> None:
        """自定义 intent_priority 保留"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "intent_priority": "HIGH",
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["intent_priority"] == "HIGH"

    def test_default_reason_tags_completed(self) -> None:
        """默认 reason_tags 补全为空列表"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["reason_tags"] == []

    def test_default_raw_payload_completed(self) -> None:
        """默认 raw_payload 补全为空字典"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.payload["intent"]["raw_payload"] == {}


class TestEventEmission:
    """事件发射测试"""

    def setup_method(self) -> None:
        """每个测试前初始化"""
        from qps.core.events import EventType

        self.event_type = EventType
        self.builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
        )

    def test_intent_created_event_emitted(self) -> None:
        """INTENT_CREATED 事件发射"""
        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        event = self.builder.build_and_emit(intent)
        assert event.event_type == self.event_type.INTENT_CREATED

    def test_event_bus_called(self) -> None:
        """event_bus 回调被调用"""
        events_captured: list[dict[str, Any]] = []

        def event_bus(data: dict[str, Any]) -> None:
            events_captured.append(data)

        builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
            event_bus=event_bus,
        )

        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        }
        builder.build_and_emit(intent)

        assert len(events_captured) == 1
        assert "event" in events_captured[0]
        assert "intent" in events_captured[0]

    def test_validation_error_does_not_emit_event(self) -> None:
        """校验失败不发射事件"""
        events_captured: list[dict[str, Any]] = []

        def event_bus(data: dict[str, Any]) -> None:
            events_captured.append(data)

        builder = IntentBuilder(
            strategy_id="test_strategy",
            version_no=1,
            deployment_id="dep_test",
            event_bus=event_bus,
        )

        intent = {
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
            "unknown_field": "value",
        }

        with pytest.raises(IntentValidationError):
            builder.build_and_emit(intent)

        assert len(events_captured) == 0
