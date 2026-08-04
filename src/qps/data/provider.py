"""数据访问端口实现（见 docs/03 第 2 节）

读取 Normalized/Feature Parquet 文件，实现 DataProviderPort。
所有时间范围 [start, end) 左闭右开。
"""

from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import polars as pl

from qps.core.constants import AssetClass, DataQuality, InstrumentStatus, MarginMode
from qps.core.errors import DataError
from qps.core.models.instrument import ExchangeCalendar, Instrument
from qps.core.models.market import (
    Bar,
    FeatureSet,
    FundingRate,
    MarkPrice,
    OpenInterest,
    Tick,
)

logger = logging.getLogger(__name__)


class DataProvider:
    """数据访问实现：读取 Parquet 文件

    目录布局：
    - Normalized K 线: data/normalized/<interval>/<symbol>/<YYYY>.parquet
    - Normalized 资金费率: data/normalized/funding/<symbol>/<YYYY>.parquet
    - Normalized OI: data/normalized/open_interest/<symbol>/<YYYY>.parquet
    - 特征: data/features/<feature_version>/<interval>/<symbol>/<YYYY>.parquet
    """

    def __init__(self, data_dir: Path) -> None:
        """初始化

        Args:
            data_dir: 数据根目录（如 ./data）
        """
        self._data_dir = data_dir
        self._normalized_dir = data_dir / "normalized"
        self._features_dir = data_dir / "features"

    @staticmethod
    def _symbol_dir(instrument_id: str) -> str:
        """从 instrument_id 提取符号目录名

        如 binance-btc-usdt-perp → btc-usdt-perp
        """
        return instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id

    async def get_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
        data_version: str | None = None,
    ) -> list[Bar]:
        """查询 K 线数据

        读取 data/normalized/<interval>/<symbol>/<YYYY>.parquet
        """
        symbol = self._symbol_dir(instrument_id)
        data_dir = self._normalized_dir / interval / symbol

        if not data_dir.exists():
            return []

        all_bars: list[Bar] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if not parquet_path.exists():
                continue

            try:
                df = pl.read_parquet(parquet_path)
                all_bars.extend(self._df_to_bars(df, instrument_id, start, end, data_version))
            except Exception:
                logger.exception("读取 Normalized K 线失败: %s", parquet_path)
                continue

        # 按 open_time 升序
        return sorted(all_bars, key=lambda b: b.open_time)

    async def get_ticks(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[Tick]:
        """查询逐笔成交数据（MVP 不实现，返回空列表）"""
        return []

    async def get_funding_rates(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[FundingRate]:
        """查询资金费率数据

        读取 data/normalized/funding/<symbol>/<YYYY>.parquet
        """
        symbol = self._symbol_dir(instrument_id)
        data_dir = self._normalized_dir / "funding" / symbol

        if not data_dir.exists():
            return []

        all_rates: list[FundingRate] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if not parquet_path.exists():
                continue

            try:
                df = pl.read_parquet(parquet_path)
                all_rates.extend(self._df_to_funding_rates(df, instrument_id, start, end))
            except Exception:
                logger.exception("读取 Normalized 资金费率失败: %s", parquet_path)
                continue

        return sorted(all_rates, key=lambda f: f.funding_ts)

    async def get_open_interest(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[OpenInterest]:
        """查询持仓量数据

        读取 data/normalized/open_interest/<symbol>/<YYYY>.parquet
        """
        symbol = self._symbol_dir(instrument_id)
        data_dir = self._normalized_dir / "open_interest" / symbol

        if not data_dir.exists():
            return []

        all_oi: list[OpenInterest] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if not parquet_path.exists():
                continue

            try:
                df = pl.read_parquet(parquet_path)
                all_oi.extend(self._df_to_open_interest(df, instrument_id, start, end))
            except Exception:
                logger.exception("读取 Normalized OI 失败: %s", parquet_path)
                continue

        return sorted(all_oi, key=lambda o: o.ts)

    async def get_mark_prices(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[MarkPrice]:
        """查询标记价格数据

        读取 data/normalized/mark_price/<symbol>/<YYYY>.parquet
        MVP：若无 mark_price 目录，返回空列表
        """
        symbol = self._symbol_dir(instrument_id)
        data_dir = self._normalized_dir / "mark_price" / symbol

        if not data_dir.exists():
            return []

        all_mp: list[MarkPrice] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if not parquet_path.exists():
                continue

            try:
                df = pl.read_parquet(parquet_path)
                all_mp.extend(self._df_to_mark_prices(df, instrument_id, start, end))
            except Exception:
                logger.exception("读取 Normalized 标记价格失败: %s", parquet_path)
                continue

        return sorted(all_mp, key=lambda m: m.ts)

    async def get_features(
        self,
        instrument_id: str,
        feature_names: list[str],
        start: datetime,
        end: datetime,
        feature_version: str | None = None,
    ) -> list[FeatureSet]:
        """查询特征数据

        读取 data/features/<feature_version>/<interval>/<symbol>/<YYYY>.parquet
        注意：需要 interval 参数，此处从 feature_version 推断或使用默认 "5m"
        """
        if feature_version is None:
            feature_version = "feat-1"

        # 尝试推断 interval（从 feature_version 目录下的子目录名）
        symbol = self._symbol_dir(instrument_id)
        fv_dir = self._features_dir / feature_version

        if not fv_dir.exists():
            return []

        # 寻找可用 interval 目录
        interval = "5m"  # 默认
        for child in fv_dir.iterdir():
            if child.is_dir() and child.name in ("1m", "5m", "15m", "1h"):
                interval = child.name
                break

        data_dir = fv_dir / interval / symbol
        if not data_dir.exists():
            return []

        all_features: list[FeatureSet] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if not parquet_path.exists():
                continue

            try:
                df = pl.read_parquet(parquet_path)
                all_features.extend(
                    self._df_to_features(df, instrument_id, start, end, feature_names)
                )
            except Exception:
                logger.exception("读取特征数据失败: %s", parquet_path)
                continue

        return sorted(all_features, key=lambda f: f.ts)

    async def get_instrument(self, instrument_id: str) -> Instrument:
        """查询标的详情（基于 instrument_id 推断）"""
        # 从 instrument_id 推断属性
        # 格式: binance-btc-usdt-perp / binance-btc-usdt
        parts = instrument_id.split("-")
        if len(parts) < 3:
            raise DataError(detail=f"无法解析 instrument_id: {instrument_id}")

        exchange = parts[0]
        base = parts[1].upper()
        quote = parts[2].upper()
        is_perp = len(parts) > 3 and parts[3].upper() == "PERP"

        return Instrument(
            instrument_id=instrument_id,
            internal_symbol=f"{base}-{quote}-PERP" if is_perp else f"{base}-{quote}",
            asset_class=AssetClass.PERPETUAL if is_perp else AssetClass.SPOT,
            base_asset=base,
            quote_asset=quote,
            exchange=exchange,
            tick_size=Decimal("0.01"),
            lot_size=Decimal("0.001"),
            min_notional=Decimal("5"),
            maker_fee=Decimal("0.0002"),
            taker_fee=Decimal("0.0004"),
            funding_interval="8h" if is_perp else None,
            margin_mode=MarginMode.ISOLATED if is_perp else None,
            max_leverage=Decimal("125") if is_perp else None,
            status=InstrumentStatus.ACTIVE,
        )

    def calendar(self, exchange: str) -> list[ExchangeCalendar]:
        """查询交易所日历（加密货币 24/7，返回空列表）"""
        return []

    # ── DataFrame 转换方法 ──────────────────────────────────

    @staticmethod
    def _df_to_bars(
        df: pl.DataFrame,
        instrument_id: str,
        start: datetime,
        end: datetime,
        data_version: str | None = None,
    ) -> list[Bar]:
        """将 DataFrame 行转为 Bar 列表"""
        bars: list[Bar] = []
        for row in df.iter_rows(named=True):
            try:
                bar_open_time = datetime.fromisoformat(row["open_time"])
                if not (start <= bar_open_time < end):
                    continue

                dv = data_version or row.get("data_version", "unknown")
                quality_val = row.get("quality", "OK")

                bars.append(Bar(
                    instrument_id=row.get("instrument_id", instrument_id),
                    open_time=bar_open_time,
                    interval=row.get("interval", "5m"),
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=Decimal(str(row["volume"])),
                    quote_volume=Decimal(str(row["quote_volume"])),
                    taker_buy_volume=Decimal(str(row.get("taker_buy_volume", "0"))),
                    trades_count=int(row.get("trades_count", 0)),
                    data_version=dv,
                    quality=DataQuality(quality_val) if isinstance(quality_val, str) else DataQuality.OK,
                ))
            except Exception:
                logger.debug("跳过无效 Bar 行: %s", row)
                continue
        return bars

    @staticmethod
    def _df_to_funding_rates(
        df: pl.DataFrame,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[FundingRate]:
        """将 DataFrame 行转为 FundingRate 列表"""
        rates: list[FundingRate] = []
        for row in df.iter_rows(named=True):
            try:
                funding_ts = datetime.fromisoformat(row["funding_ts"])
                if not (start <= funding_ts < end):
                    continue

                predicted = row.get("predicted_rate")
                rates.append(FundingRate(
                    instrument_id=row.get("instrument_id", instrument_id),
                    funding_ts=funding_ts,
                    rate=Decimal(str(row["rate"])),
                    predicted_rate=Decimal(str(predicted)) if predicted and str(predicted) != "null" else None,
                    data_version=row.get("data_version", "unknown"),
                ))
            except Exception:
                logger.debug("跳过无效 FundingRate 行: %s", row)
                continue
        return rates

    @staticmethod
    def _df_to_open_interest(
        df: pl.DataFrame,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[OpenInterest]:
        """将 DataFrame 行转为 OpenInterest 列表"""
        oi_list: list[OpenInterest] = []
        for row in df.iter_rows(named=True):
            try:
                ts = datetime.fromisoformat(row["ts"])
                if not (start <= ts < end):
                    continue

                oi_contracts = row.get("oi_contracts")
                oi_list.append(OpenInterest(
                    instrument_id=row.get("instrument_id", instrument_id),
                    ts=ts,
                    oi_value=Decimal(str(row["oi_value"])),
                    oi_contracts=Decimal(str(oi_contracts)) if oi_contracts and str(oi_contracts) != "null" else None,
                    data_version=row.get("data_version", "unknown"),
                ))
            except Exception:
                logger.debug("跳过无效 OpenInterest 行: %s", row)
                continue
        return oi_list

    @staticmethod
    def _df_to_mark_prices(
        df: pl.DataFrame,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[MarkPrice]:
        """将 DataFrame 行转为 MarkPrice 列表"""
        mps: list[MarkPrice] = []
        for row in df.iter_rows(named=True):
            try:
                ts = datetime.fromisoformat(row["ts"])
                if not (start <= ts < end):
                    continue

                index_price = row.get("index_price")
                mps.append(MarkPrice(
                    instrument_id=row.get("instrument_id", instrument_id),
                    ts=ts,
                    mark_price=Decimal(str(row["mark_price"])),
                    index_price=Decimal(str(index_price)) if index_price and str(index_price) != "null" else None,
                ))
            except Exception:
                logger.debug("跳过无效 MarkPrice 行: %s", row)
                continue
        return mps

    @staticmethod
    def _df_to_features(
        df: pl.DataFrame,
        instrument_id: str,
        start: datetime,
        end: datetime,
        feature_names: list[str],
    ) -> list[FeatureSet]:
        """将 DataFrame 行转为 FeatureSet 列表"""
        name_set = set(feature_names)
        features: list[FeatureSet] = []
        for row in df.iter_rows(named=True):
            try:
                ts = datetime.fromisoformat(row["ts"])
                if not (start <= ts < end):
                    continue

                fname = row.get("feature_name", "")
                if name_set and fname not in name_set:
                    continue

                features.append(FeatureSet(
                    instrument_id=row.get("instrument_id", instrument_id),
                    ts=ts,
                    feature_name=fname,
                    value=Decimal(str(row["value"])),
                    feature_version=row.get("feature_version", "unknown"),
                    data_version=row.get("data_version", "unknown"),
                ))
            except Exception:
                logger.debug("跳过无效 FeatureSet 行: %s", row)
                continue
        return features
