"""数据访问端口（见 docs/03 第 2 节）

前置条件：instrument_id 对应标的已存在；start/end 必填。
后置条件：返回结果按时间升序；data_version/feature_version 为空时返回当前默认版本。
异常：DataError（数据缺失/版本不匹配）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from qps.core.models.instrument import ExchangeCalendar, Instrument
    from qps.core.models.market import (
        Bar,
        FeatureSet,
        FundingRate,
        MarkPrice,
        OpenInterest,
        Tick,
    )


@runtime_checkable
class DataProviderPort(Protocol):
    """数据访问端口：历史数据查询

    语义要点：
    - 时间范围 [start, end) 左闭右开；start/end 必填
    - data_version/feature_version 为空时返回当前默认版本并在结果中标注
    - 数据缺失不抛异常，返回空列表
    - get_bars 返回结果按 open_time 升序
    """

    async def get_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
        data_version: str | None = None,
    ) -> list[Bar]:
        """查询 K 线数据

        前置：instrument_id 存在；start < end
        后置：按 open_time 升序；空列表表示无数据
        """
        ...

    async def get_ticks(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[Tick]:
        """查询逐笔成交数据

        前置：instrument_id 存在；start < end
        后置：按 ts 升序；空列表表示无数据
        """
        ...

    async def get_funding_rates(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[FundingRate]:
        """查询资金费率数据

        前置：instrument_id 存在；start < end
        后置：按 funding_ts 升序
        """
        ...

    async def get_open_interest(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[OpenInterest]:
        """查询持仓量数据

        前置：instrument_id 存在；start < end
        """
        ...

    async def get_mark_prices(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[MarkPrice]:
        """查询标记价格数据

        前置：instrument_id 存在；start < end
        """
        ...

    async def get_features(
        self,
        instrument_id: str,
        feature_names: list[str],
        start: datetime,
        end: datetime,
        feature_version: str | None = None,
    ) -> list[FeatureSet]:
        """查询特征数据

        前置：feature_names 非空；start < end
        后置：feature_version 为空时返回当前默认版本
        """
        ...

    async def get_instrument(self, instrument_id: str) -> Instrument:
        """查询标的详情

        前置：instrument_id 存在
        异常：DataError（标的不存在）
        """
        ...

    def calendar(self, exchange: str) -> list[ExchangeCalendar]:
        """查询交易所日历

        前置：exchange 存在
        后置：返回交易日历列表，按日期升序
        """
        ...
