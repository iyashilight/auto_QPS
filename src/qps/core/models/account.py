"""账户、持仓与组合模型（见 docs/02 第 3.5 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import (
    AccountMode,
    AccountStatus,
    AccountType,
    Direction,
    MarginMode,
)
from qps.core.models.validators import validate_utc_datetime


class Balance(BaseModel):
    """账户余额"""

    asset: str = Field(description="资产，如 USDT")
    free: Decimal = Field(description="可用余额")
    locked: Decimal = Field(default=Decimal("0"), description="冻结余额")
    equity: Decimal = Field(default=Decimal("0"), description="权益")

    model_config = {"frozen": True}

class Account(BaseModel):
    """账户（account_id 唯一）"""

    account_id: str = Field(description="账户唯一 ID")
    mode: AccountMode = Field(description="账户模式")
    account_type: AccountType = Field(description="账户类型")
    exchange_ref: str | None = Field(default=None, description="交易所引用")
    balances: list[Balance] = Field(default_factory=list, description="余额列表")
    total_equity: Decimal = Field(default=Decimal("0"), description="总权益")
    status: AccountStatus = Field(default=AccountStatus.ACTIVE, description="账户状态")

    model_config = {"frozen": True}

class Position(BaseModel):
    """持仓（position_id 唯一）"""

    position_id: str = Field(description="持仓唯一 ID")
    account_id: str = Field(description="归属账户")
    instrument_id: str = Field(description="标的")
    side: Direction = Field(description="持仓方向")
    quantity: Decimal = Field(description="持仓数量")
    entry_price: Decimal = Field(description="开仓均价")
    avg_cost: Decimal | None = Field(default=None, description="平均成本")
    mark_price: Decimal | None = Field(default=None, description="标记价格")
    unrealized_pnl: Decimal | None = Field(default=None, description="未实现盈亏")
    realized_pnl: Decimal = Field(default=Decimal("0"), description="已实现盈亏")
    leverage: Decimal | None = Field(default=None, description="杠杆")
    margin_mode: MarginMode | None = Field(default=None, description="保证金模式")
    liquidation_price: Decimal | None = Field(default=None, description="强平价格")
    maintenance_margin: Decimal | None = Field(default=None, description="维持保证金")
    updated_ts: datetime = Field(description="更新时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("updated_ts")
    @classmethod
    def _validate_updated_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class Snapshot(BaseModel):
    """账户快照（对账/回放用）"""

    snapshot_ts: datetime = Field(description="快照时间（UTC）")
    accounts: list[Account] = Field(default_factory=list, description="全量账户")
    positions: list[Position] = Field(default_factory=list, description="全量持仓")
    portfolio_risk: dict[str, Any] | None = Field(default=None, description="组合风险")

    model_config = {"frozen": True}

    @field_validator("snapshot_ts")
    @classmethod
    def _validate_snapshot_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class StrategyAllocation(BaseModel):
    """组合资金分配（MVP 静态）"""

    allocation_id: str = Field(description="唯一 ID")
    account_id: str = Field(description="账户 ID")
    strategy_id: str = Field(description="策略 ID")
    weight: Decimal = Field(ge=0, le=1, description="权重（0~1）")
    capital_limit: Decimal = Field(description="该策略占用资金上限")
    currency: str = Field(default="USDT", description="计价币种")
    enabled: bool = Field(default=True, description="是否参与分配")

    model_config = {"frozen": True}

class PortfolioRisk(BaseModel):
    """组合风险快照（account_id + snapshot_ts）"""

    account_id: str = Field(description="账户 ID")
    snapshot_ts: datetime = Field(description="快照时间（UTC）")
    total_notional: Decimal = Field(description="总名义金额")
    exposure_by_symbol: dict[str, Decimal] = Field(default_factory=dict, description="按标的敞口")
    exposure_by_strategy: dict[str, Decimal] = Field(default_factory=dict, description="按策略敞口")
    net_exposure_by_symbol: dict[str, Decimal] = Field(default_factory=dict, description="按标的净敞口")
    concentration: Decimal | None = Field(default=None, description="集中度")
    combined_leverage: Decimal | None = Field(default=None, description="组合杠杆")

    model_config = {"frozen": True}

    @field_validator("snapshot_ts")
    @classmethod
    def _validate_snapshot_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)
