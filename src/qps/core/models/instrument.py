"""标的与市场模型（见 docs/02 第 3.1 节）"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, model_validator

from qps.core.constants import AssetClass, InstrumentStatus, MarginMode


class Instrument(BaseModel):
    """交易标的（instrument_id + internal_symbol 全局唯一）"""

    instrument_id: str = Field(description="系统内部唯一 ID，如 binance-btc-usdt-perp")
    internal_symbol: str = Field(description="统一符号，如 BTC-USDT-PERP")
    asset_class: AssetClass = Field(description="资产类别")
    base_asset: str = Field(description="基础资产，如 BTC")
    quote_asset: str = Field(description="计价资产，如 USDT")
    exchange: str = Field(description="交易所，如 binance")
    contract_size: Decimal | None = Field(default=None, description="永续合约面值，现货为 1")
    tick_size: Decimal = Field(description="最小价格变动")
    lot_size: Decimal = Field(description="最小数量精度")
    min_notional: Decimal = Field(description="最小下单名义金额")
    maker_fee: Decimal = Field(description="Maker 费率")
    taker_fee: Decimal = Field(description="Taker 费率")
    funding_interval: str | None = Field(default=None, description="资金费率结算间隔，如 8h")
    margin_mode: MarginMode | None = Field(default=None, description="保证金模式（永续）")
    max_leverage: Decimal | None = Field(default=None, description="最大杠杆（永续）")
    status: InstrumentStatus = Field(description="标的状态")

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _validate_instrument(self) -> Instrument:
        """校验：现货 contract_size 应为 None 或 1"""
        if self.asset_class == AssetClass.SPOT and self.contract_size is not None and self.contract_size != Decimal("1"):
            raise ValueError("现货 contract_size 应为 1 或 None")
        return self


class SymbolMapping(BaseModel):
    """交易所符号映射（instrument_id + exchange 唯一）"""

    instrument_id: str = Field(description="内部 ID")
    exchange: str = Field(description="交易所")
    exchange_symbol: str = Field(description="交易所原始符号，如 BTCUSDT")
    is_primary: bool = Field(default=True, description="是否主映射")

    model_config = {"frozen": True}


class ExchangeMeta(BaseModel):
    """交易所元数据（exchange_id 唯一）"""

    exchange_id: str = Field(description="交易所 ID，如 binance")
    rate_limits: list[dict[str, Any]] = Field(default_factory=list, description="限频规则")
    ws_endpoints: dict[str, str] = Field(default_factory=dict, description="WebSocket 端点")
    rest_endpoints: dict[str, str] = Field(default_factory=dict, description="REST 端点")
    maintenance_schedule: list[dict[str, Any]] = Field(default_factory=list, description="维护窗口")

    model_config = {"frozen": True}
