"""意图构建器（见 docs/04 第 5 节）

负责构建和校验 TradingIntent，发出 INTENT_CREATED 事件。
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from collections.abc import Callable

from qps.core.constants import (
    Action,
    Direction,
    IntentPriority,
    OrderType,
    is_valid_direction_action,
)
from qps.core.events import EventEnvelope, EventType, make_event_id


class IntentValidationError(Exception):
    """意图校验错误"""

    def __init__(self, reason: str, field: str | None = None) -> None:
        self.reason = reason
        self.field = field
        super().__init__(f"意图校验失败: {reason} (字段: {field})" if field else f"意图校验失败: {reason}")


class IntentBuilder:
    """意图构建器

    负责：
    1. 补全 intent_id, strategy_id, version_no, deployment_id, valid_until
    2. 执行字段白名单校验
    3. 发出 INTENT_CREATED 事件
    """

    # 白名单字段（策略可填写的字段）
    WHITELISTED_FIELDS: ClassVar[set[str]] = {
        "direction",
        "action",
        "quantity",
        "notional",
        "order_type",
        "limit_price",
        "instrument_id",
        "intent_priority",
        "reason_tags",
        "raw_payload",
    }

    def __init__(
        self,
        strategy_id: str,
        version_no: int,
        deployment_id: str,
        default_valid_until_seconds: int = 600,
        event_bus: Callable[[dict[str, Any]], None] | None = None,
        clock: datetime | None = None,
        allowed_instruments: set[str] | None = None,
        release_id: str | None = None,
        interval: str | None = None,
        deterministic_ids: bool = False,
        input_snapshot_ref: str | None = None,
    ) -> None:
        """初始化意图构建器

        Args:
            strategy_id: 策略 ID
            version_no: 版本号
            deployment_id: 部署实例 ID
            default_valid_until_seconds: 默认有效期（秒）
            event_bus: 事件总线回调（可选）
        """
        self._strategy_id = strategy_id
        self._version_no = version_no
        self._deployment_id = deployment_id
        self._default_valid_until_seconds = default_valid_until_seconds
        self._event_bus = event_bus
        self._clock = clock or datetime.now(UTC)
        self._allowed_instruments = allowed_instruments
        self._release_id = release_id
        self._interval = interval
        self._deterministic_ids = deterministic_ids
        self._input_snapshot_ref = input_snapshot_ref

    def build_and_emit(self, intent: dict[str, Any]) -> EventEnvelope:
        """构建并发出意图

        Args:
            intent: 策略提供的意图字典

        Returns:
            发出的事件信封

        Raises:
            IntentValidationError: 校验失败时抛出
        """
        # 1. 校验白名单字段
        self._validate_whitelist(intent)

        # 2. 校验必填字段
        self._validate_required_fields(intent)

        # 3. 校验方向/动作组合
        self._validate_direction_action(intent)

        # 4. 校验 quantity/notional 二选一
        self._validate_quantity_or_notional(intent)

        # 5. 校验 LIMIT 订单
        self._validate_limit_order(intent)

        # 6. 校验 reason_tags 格式
        self._validate_reason_tags(intent)
        if self._allowed_instruments is not None and intent.get("instrument_id") not in self._allowed_instruments:
            raise IntentValidationError("标的不在 Release 允许范围", field="instrument_id")

        # 7. 补全字段
        completed = self._complete_intent(intent)

        # 8. 发出事件
        event = self._create_event(completed)

        if self._event_bus:
            self._event_bus({
                "event": event.model_dump(),
                "intent": completed,
            })

        return event

    def _validate_whitelist(self, intent: dict[str, Any]) -> None:
        """校验白名单字段"""
        for key in intent:
            if key not in self.WHITELISTED_FIELDS:
                raise IntentValidationError(
                    f"未知字段: {key}",
                    field=key,
                )

    def _validate_required_fields(self, intent: dict[str, Any]) -> None:
        """校验必填字段"""
        required = ["direction", "action", "order_type"]
        for field in required:
            if field not in intent or intent[field] is None:
                raise IntentValidationError(
                    f"缺少必填字段: {field}",
                    field=field,
                )

    def _validate_direction_action(self, intent: dict[str, Any]) -> None:
        """校验 direction + action 合法组合"""
        direction = Direction(intent["direction"])
        action = Action(intent["action"])

        if not is_valid_direction_action(direction, action):
            raise IntentValidationError(
                f"非法 direction/action 组合: {direction.value} + {action.value}",
                field="direction/action",
            )

    def _validate_quantity_or_notional(self, intent: dict[str, Any]) -> None:
        """校验 quantity 与 notional 二选一"""
        has_quantity = "quantity" in intent and intent["quantity"] is not None
        has_notional = "notional" in intent and intent["notional"] is not None

        if has_quantity and has_notional:
            raise IntentValidationError(
                "quantity 与 notional 不能同时指定，二选一",
                field="quantity/notional",
            )

        if not has_quantity and not has_notional:
            raise IntentValidationError(
                "quantity 与 notional 必须指定其一",
                field="quantity/notional",
            )

        # 校验值 > 0
        if has_quantity:
            qty = Decimal(str(intent["quantity"]))
            if qty <= 0:
                raise IntentValidationError(
                    "quantity 必须大于 0",
                    field="quantity",
                )

        if has_notional:
            notional = Decimal(str(intent["notional"]))
            if notional <= 0:
                raise IntentValidationError(
                    "notional 必须大于 0",
                    field="notional",
                )

    def _validate_limit_order(self, intent: dict[str, Any]) -> None:
        """校验 LIMIT 订单必须有 limit_price"""
        order_type = OrderType(intent["order_type"])

        if order_type == OrderType.LIMIT:
            if "limit_price" not in intent or intent["limit_price"] is None:
                raise IntentValidationError(
                    "LIMIT 订单必须指定 limit_price",
                    field="limit_price",
                )

            limit_price = Decimal(str(intent["limit_price"]))
            if limit_price <= 0:
                raise IntentValidationError(
                    "limit_price 必须大于 0",
                    field="limit_price",
                )

    def _validate_reason_tags(self, intent: dict[str, Any]) -> None:
        """校验 reason_tags 格式"""
        if "reason_tags" not in intent:
            return

        tags = intent["reason_tags"]
        if not isinstance(tags, list):
            raise IntentValidationError(
                "reason_tags 必须是列表",
                field="reason_tags",
            )

        pattern = re.compile(r"^[a-z0-9_]+$")
        for tag in tags:
            if not isinstance(tag, str) or not pattern.match(tag):
                raise IntentValidationError(
                    f"reason_tags 格式错误: {tag}，只允许 [a-z0-9_]+",
                    field="reason_tags",
                )

    def _complete_intent(self, intent: dict[str, Any]) -> dict[str, Any]:
        """补全意图字段"""
        stable = hashlib.sha256(json.dumps(intent, sort_keys=True, default=str).encode()).hexdigest()[:16]
        completed = {
            "intent_id": f"intent-{stable}" if self._deterministic_ids else str(uuid.uuid4()),
            "strategy_id": self._strategy_id,
            "version_no": self._version_no,
            "deployment_id": self._deployment_id,
            **intent,
        }
        if self._release_id is not None:
            completed["release_id"] = self._release_id
        if self._interval is not None:
            completed["interval"] = self._interval

        # 补全 valid_until
        if "valid_until" not in completed:
            now = self._clock
            completed["valid_until"] = (now + timedelta(seconds=self._default_valid_until_seconds)).isoformat()

        # 补全默认值
        if "intent_priority" not in completed:
            completed["intent_priority"] = IntentPriority.NORMAL.value

        if "reason_tags" not in completed:
            completed["reason_tags"] = []

        if "raw_payload" not in completed:
            completed["raw_payload"] = {}

        return completed

    def _create_event(self, intent: dict[str, Any]) -> EventEnvelope:
        """创建 INTENT_CREATED 事件"""
        event_material: dict[str, Any] = {"intent": intent}
        if self._input_snapshot_ref is not None:
            event_material["input_snapshot_ref"] = self._input_snapshot_ref
            event_material["release_id"] = self._release_id
        stable = hashlib.sha256(json.dumps(event_material, ensure_ascii=False, sort_keys=True,
                                           separators=(",", ":"), default=str).encode()).hexdigest()[:8]
        occurred_at = self._clock
        return EventEnvelope(
            event_id=make_event_id(stable if self._deterministic_ids else uuid.uuid4().hex[:8]),
            event_type=EventType.INTENT_CREATED,
            schema_version=1,
            ts=occurred_at.isoformat(),
            occurred_at=occurred_at,
            recorded_at=occurred_at,
            source=f"strategy_runtime.{self._strategy_id}",
            payload={"intent": intent},
        )
