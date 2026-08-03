"""交易意图模型（见 docs/02 第 3.4 节 + docs/04 第 5 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from qps.core.constants import (
    Action,
    Direction,
    IntentPriority,
    OrderType,
    is_valid_direction_action,
)
from qps.core.models.validators import validate_utc_datetime


class TradingIntent(BaseModel):
    """策略唯一输出（字段白名单校验）

    校验规则：
    - direction + action 合法组合
    - quantity 与 notional 二选一（同时为空或同时非空 → 拒绝）
    - order_type=LIMIT 时 limit_price 必填且 > 0
    """

    intent_id: str = Field(description="意图唯一 ID（uuid）")
    strategy_id: str = Field(description="来源策略 ID")
    version_no: int = Field(ge=1, description="来源策略版本号")
    deployment_id: str = Field(description="部署实例 ID")
    instrument_id: str = Field(description="目标标的")
    direction: Direction = Field(description="持仓方向")
    action: Action = Field(description="交易动作")
    quantity: Decimal | None = Field(default=None, description="数量（与 notional 二选一，优先 quantity）")
    notional: Decimal | None = Field(default=None, description="名义金额（USD 计价）")
    order_type: OrderType = Field(description="订单类型")
    limit_price: Decimal | None = Field(default=None, description="限价（LIMIT 时必填）")
    valid_until: datetime = Field(description="过期时间（UTC）")
    intent_priority: IntentPriority = Field(default=IntentPriority.NORMAL, description="优先级")
    reason_tags: list[str] = Field(default_factory=list, description="语义标签")
    raw_payload: dict[str, Any] = Field(default_factory=dict, description="策略原始输出（审计）")

    model_config = {"frozen": True}

    @field_validator("valid_until")
    @classmethod
    def _validate_valid_until_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

    @model_validator(mode="after")
    def _validate_intent(self) -> TradingIntent:
        """校验意图合法性"""
        # 1. direction + action 合法组合
        if not is_valid_direction_action(self.direction, self.action):
            raise ValueError(
                f"非法 direction/action 组合: {self.direction.value} + {self.action.value}"
            )

        # 2. quantity 与 notional 二选一
        has_quantity = self.quantity is not None
        has_notional = self.notional is not None
        if has_quantity and has_notional:
            raise ValueError("quantity 与 notional 不能同时指定，二选一")
        if not has_quantity and not has_notional:
            raise ValueError("quantity 与 notional 必须指定其一")

        # 3. quantity > 0
        if has_quantity and self.quantity is not None and self.quantity <= 0:
            raise ValueError("quantity 必须大于 0")

        # 4. notional > 0
        if has_notional and self.notional is not None and self.notional <= 0:
            raise ValueError("notional 必须大于 0")

        # 5. LIMIT 时 limit_price 必填且 > 0
        if self.order_type == OrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("LIMIT 订单必须指定 limit_price")
            if self.limit_price <= 0:
                raise ValueError("limit_price 必须大于 0")

        return self
