"""订单与成交模型（见 docs/02 第 3.4 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import MarginMode, OrderSide, OrderStatus, OrderType
from qps.core.models.validators import validate_utc_datetime


class Order(BaseModel):
    """订单"""

    order_id: str = Field(description="本地订单 ID")
    client_order_id: str = Field(description="幂等键，格式 qps-<deployment>-<uuid8>")
    account_id: str = Field(description="归属账户")
    deployment_id: str = Field(description="归属部署")
    instrument_id: str = Field(description="标的")
    side: OrderSide = Field(description="买卖方向")
    order_type: OrderType = Field(description="订单类型")
    quantity: Decimal = Field(description="委托量")
    filled_quantity: Decimal = Field(default=Decimal("0"), description="已成交量")
    avg_price: Decimal | None = Field(default=None, description="成交均价")
    limit_price: Decimal | None = Field(default=None, description="限价")
    leverage: Decimal | None = Field(default=None, description="永续杠杆")
    margin_mode: MarginMode | None = Field(default=None, description="逐仓/全仓")
    status: OrderStatus = Field(description="订单状态")
    reject_reason: str | None = Field(default=None, description="拒单原因")
    timestamps: dict[str, str] = Field(
        default_factory=dict,
        description="各时间点（created/submitted/acked/filled/canceled/expired）",
    )

    model_config = {"frozen": True}

class Trade(BaseModel):
    """成交记录"""

    trade_id: str = Field(description="成交唯一 ID")
    order_id: str = Field(description="关联订单 ID")
    instrument_id: str = Field(description="标的")
    side: OrderSide = Field(description="买卖方向")
    quantity: Decimal = Field(description="成交数量")
    price: Decimal = Field(description="成交价格")
    fee: Decimal = Field(default=Decimal("0"), description="手续费")
    fee_currency: str = Field(default="USDT", description="手续费币种")
    realized_pnl: Decimal | None = Field(default=None, description="已实现盈亏")
    ts: datetime = Field(description="成交时间（UTC）")
    exchange_trade_id: str | None = Field(default=None, description="交易所成交 ID")

    model_config = {"frozen": True}

    @field_validator("ts")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)
