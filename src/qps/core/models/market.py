"""市场数据模型（见 docs/02 第 3.2 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

from qps.core.constants import DataQuality, OrderSide
from qps.core.models.foundation import RawJson
from qps.core.models.validators import validate_utc_datetime


class Bar(BaseModel):
    """K 线数据（主键：instrument_id + open_time + interval）"""

    instrument_id: str = Field(description="内部 ID")
    open_time: datetime = Field(description="K 线开盘时间（UTC）")
    interval: str = Field(description="K 线间隔，如 1m/5m/15m/1h")
    open: Decimal = Field(description="开盘价")
    high: Decimal = Field(description="最高价")
    low: Decimal = Field(description="最低价")
    close: Decimal = Field(description="收盘价")
    volume: Decimal = Field(description="成交量（base）")
    quote_volume: Decimal = Field(description="成交额（quote）")
    taker_buy_volume: Decimal = Field(default=Decimal("0"), description="主动买量")
    trades_count: int = Field(default=0, description="成交笔数")
    data_version: str = Field(description="数据批次版本")
    quality: DataQuality = Field(default=DataQuality.OK, description="数据质量标记")
    # v0.4 callers may omit PIT metadata; v0.5 data paths must provide all five.
    event_time: datetime | None = Field(default=None, description="事件时间（UTC）")
    available_at: datetime | None = Field(default=None, description="数据可见时间（UTC）")
    ingested_at: datetime | None = Field(default=None, description="系统接收时间（UTC）")
    close_time: datetime | None = Field(default=None, description="K 线关闭时间（UTC）")
    is_closed: bool | None = Field(default=None, description="K 线是否已关闭")
    decision_time: datetime | None = Field(default=None, description="决策时间锚点（UTC）")
    raw_payload: RawJson = Field(default=None, description="来源原始 payload（只读 JSON）")

    model_config = {"frozen": True}

    @field_validator("open_time")
    @classmethod
    def _validate_open_time_timezone(cls, v: datetime) -> datetime:
        """open_time 必须带时区信息"""
        return validate_utc_datetime(v)

    @field_validator("event_time", "available_at", "ingested_at", "close_time", "decision_time")
    @classmethod
    def _validate_optional_pit_time(cls, v: datetime | None) -> datetime | None:
        return validate_utc_datetime(v) if v is not None else None

    @model_validator(mode="after")
    def _validate_pit_metadata(self) -> Bar:
        pit_values = (
            self.event_time,
            self.available_at,
            self.ingested_at,
            self.close_time,
            self.is_closed,
            self.decision_time,
        )
        if all(value is None for value in pit_values):
            return self
        if any(value is None for value in pit_values):
            raise ValueError("v0.5 Bar 的 PIT/timing 字段必须完整提供")
        assert self.event_time is not None
        assert self.available_at is not None
        assert self.ingested_at is not None
        assert self.close_time is not None
        assert self.decision_time is not None
        if self.available_at < self.event_time:
            raise ValueError("available_at 不能早于 event_time")
        if self.ingested_at < self.available_at:
            raise ValueError("ingested_at 不能早于 available_at")
        if self.close_time <= self.open_time:
            raise ValueError("close_time 必须晚于 open_time")
        if self.decision_time < self.open_time:
            raise ValueError("decision_time 不能早于 open_time")
        return self

class Tick(BaseModel):
    """逐笔成交（主键：instrument_id + ts）"""

    instrument_id: str = Field(description="内部 ID")
    ts: datetime = Field(description="成交时间（UTC）")
    price: Decimal = Field(description="成交价格")
    quantity: Decimal = Field(description="成交数量")
    side: OrderSide = Field(description="成交方向")
    exchange_ts: datetime | None = Field(default=None, description="交易所时间")
    received_ts: datetime | None = Field(default=None, description="接收时间")

    model_config = {"frozen": True}

    @field_validator("ts")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class OrderBookLevel(BaseModel):
    """订单簿单层"""

    price: Decimal = Field(description="价格")
    qty: Decimal = Field(description="数量")

    model_config = {"frozen": True}

class OrderBookSnapshot(BaseModel):
    """订单簿快照（主键：instrument_id + ts）"""

    instrument_id: str = Field(description="内部 ID")
    ts: datetime = Field(description="快照时间（UTC）")
    bids: list[OrderBookLevel] = Field(default_factory=list, description="买盘")
    asks: list[OrderBookLevel] = Field(default_factory=list, description="卖盘")
    exchange_ts: datetime | None = Field(default=None, description="交易所时间")

    model_config = {"frozen": True}

    @field_validator("ts")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class FundingRate(BaseModel):
    """资金费率（主键：instrument_id + funding_ts）"""

    instrument_id: str = Field(description="内部 ID")
    funding_ts: datetime = Field(description="资金费率时间（UTC）")
    rate: Decimal = Field(description="资金费率")
    predicted_rate: Decimal | None = Field(default=None, description="预测费率")
    settlement_ts: datetime | None = Field(default=None, description="结算时间")
    available_at: datetime | None = Field(default=None, description="可用于决策的时间")
    ingested_at: datetime | None = Field(default=None, description="写入时间")
    interval: str = Field(default="8h", description="费率间隔")
    data_version: str = Field(default="unknown", description="数据批次版本")

    model_config = {"frozen": True}

    @field_validator("funding_ts", "settlement_ts", "available_at", "ingested_at")
    @classmethod
    def _validate_funding_ts_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_utc_datetime(v) if v is not None else None

class OpenInterest(BaseModel):
    """持仓量（主键：instrument_id + ts）"""

    instrument_id: str = Field(description="内部 ID")
    ts: datetime = Field(description="时间（UTC）")
    oi_value: Decimal = Field(description="持仓量价值")
    oi_contracts: Decimal | None = Field(default=None, description="持仓量合约数")
    exchange_ts: datetime | None = Field(default=None, description="交易所时间")
    available_at: datetime | None = Field(default=None, description="可用于决策的时间")
    ingested_at: datetime | None = Field(default=None, description="写入时间")
    data_version: str = Field(default="unknown", description="数据批次版本")

    model_config = {"frozen": True}

    @field_validator("ts", "exchange_ts", "available_at", "ingested_at")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_utc_datetime(v) if v is not None else None

class MarkPrice(BaseModel):
    """标记价格（主键：instrument_id + ts）"""

    instrument_id: str = Field(description="内部 ID")
    ts: datetime = Field(description="时间（UTC）")
    mark_price: Decimal = Field(description="标记价格")
    index_price: Decimal | None = Field(default=None, description="指数价格")
    exchange_ts: datetime | None = Field(default=None, description="交易所时间")
    available_at: datetime | None = Field(default=None, description="可用于决策的时间")
    ingested_at: datetime | None = Field(default=None, description="写入时间")

    model_config = {"frozen": True}

    @field_validator("ts", "exchange_ts", "available_at", "ingested_at")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_utc_datetime(v) if v is not None else None

class FeatureSet(BaseModel):
    """特征集（主键：instrument_id + ts + feature_name）"""

    instrument_id: str = Field(description="内部 ID")
    ts: datetime = Field(description="时间（UTC）")
    feature_name: str = Field(description="特征名")
    value: Decimal = Field(description="特征值")
    feature_version: str = Field(description="特征版本")
    data_version: str = Field(description="数据版本")
    available_at: datetime | None = Field(default=None, description="可用于决策的时间")
    source_manifest_id: str | None = Field(default=None, description="输入数据 manifest")
    decision_time: datetime | None = Field(default=None, description="计算时点")

    model_config = {"frozen": True}

    @field_validator("ts", "available_at", "decision_time")
    @classmethod
    def _validate_ts_timezone(cls, v: datetime | None) -> datetime | None:
        return validate_utc_datetime(v) if v is not None else None
