"""数据访问端口实现（见 docs/03 第 2 节）

读取 Normalized/Feature Parquet 文件，实现 DataProviderPort。
所有时间范围 [start, end) 左闭右开。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

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
from qps.data.manifest import ManifestStore
from qps.infra.storage.meta_db import MetaDB

if TYPE_CHECKING:
    from qps.core.models.foundation import DatasetManifest, InstrumentRuleVersion

logger = logging.getLogger(__name__)


class DataProvider:
    """数据访问实现：读取 Parquet 文件

    目录布局：
    - Normalized K 线: data/normalized/<interval>/<symbol>/<YYYY>.parquet
    - Normalized 资金费率: data/normalized/funding/<symbol>/<YYYY>.parquet
    - Normalized OI: data/normalized/open_interest/<symbol>/<YYYY>.parquet
    - 特征: data/features/<feature_version>/<interval>/<symbol>/<YYYY>.parquet
    """

    def __init__(
        self, data_dir: Path, meta_db: MetaDB | None = None, *, allow_legacy: bool = True,
    ) -> None:
        """初始化

        Args:
            data_dir: 数据根目录（如 ./data）
        """
        self._data_dir = data_dir
        self._normalized_dir = data_dir / "normalized"
        self._features_dir = data_dir / "features"
        self._meta_db = meta_db or MetaDB(data_dir / "meta.db")
        self._manifests = ManifestStore(data_dir, self._meta_db)
        self._allow_legacy = allow_legacy

    def _require_legacy(self, operation: str) -> None:
        if not self._allow_legacy:
            raise DataError(
                detail=f"正式 PIT 数据端口禁止 legacy {operation}；必须提供 manifest_id 和 decision_time"
            )

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
        """查询 K 线数据（仅测试/诊断兼容路径）。

        读取 data/normalized/<interval>/<symbol>/<YYYY>.parquet
        """
        self._require_legacy("get_bars")
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

    async def query(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        *,
        decision_time: datetime,
        manifest_id: str,
    ) -> list[Bar]:
        """Read only the partitions named by a verified manifest with PIT filtering."""
        start, end, decision_time = self._validate_pit_request(start, end, decision_time)
        self._manifests.verify_pit_manifest(
            manifest_id,
            expected_dataset_types={"normalized.bars", "bars", "feature_input.bars"},
            instrument_id=instrument_id,
            require_formal=True,
        )
        manifest = self._manifests.get(manifest_id)

        result: list[Bar] = []
        for ref in manifest.partition_refs:
            path = self._manifests.resolve_partition(ref)
            try:
                frame = pl.read_parquet(path)
            except Exception as exc:
                raise DataError(detail=f"读取 manifest partition 失败: {ref}", cause=exc) from exc
            if not {
                "instrument_id", "event_time", "available_at", "ingested_at", "close_time", "is_closed",
            }.issubset(frame.columns):
                raise DataError(detail=f"partition 缺少 PIT 字段，拒绝旧格式: {ref}")
            result.extend(
                self._df_to_bars(
                    frame, instrument_id, start, end, require_pit=True,
                    decision_time=decision_time,
                )
            )
        return sorted(result, key=lambda bar: bar.open_time)

    async def get_manifest(self, manifest_id: str) -> DatasetManifest:
        """Return and verify a persisted manifest before it is used for research."""
        return self._manifests.verify(manifest_id)

    @staticmethod
    def _pit_datetime(value: object, field: str) -> datetime:
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise DataError(detail=f"PIT 字段无效: {field}", cause=exc) from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise DataError(detail=f"PIT 字段必须是 UTC 时间: {field}")
        return parsed.astimezone(UTC)

    @staticmethod
    def _pit_instrument(value: object) -> str:
        if value is None or str(value).strip() in {"", "null", "None"}:
            raise DataError(detail="PIT 记录缺少有效 instrument_id")
        return str(value)

    @staticmethod
    def _ensure_pit_order(available_at: datetime, ingested_at: datetime) -> None:
        if ingested_at < available_at:
            raise DataError(detail="PIT 时间语义无效: ingested_at 早于 available_at")

    @staticmethod
    def _validate_pit_request(
        start: datetime, end: datetime, decision_time: datetime,
    ) -> tuple[datetime, datetime, datetime]:
        values = ((start, "start"), (end, "end"), (decision_time, "decision_time"))
        normalized: list[datetime] = []
        for value, field in values:
            if value.tzinfo is None or value.utcoffset() != timedelta(0):
                raise DataError(detail=f"{field} 必须是 UTC 时间")
            normalized.append(value.astimezone(UTC))
        normalized_start, normalized_end, normalized_decision = normalized
        if normalized_start >= normalized_end:
            raise DataError(detail="start 必须早于 end")
        return normalized_start, normalized_end, normalized_decision

    def _read_pit_manifest(
        self, manifest_id: str, start: datetime, end: datetime, decision_time: datetime, instrument_id: str, *, dataset_types: set[str],
        required_columns: set[str], as_of: datetime | None = None,
    ) -> list[dict[str, object]]:
        start, end, decision_time = self._validate_pit_request(start, end, decision_time)
        manifest = self._manifests.verify_pit_manifest(
            manifest_id, expected_dataset_types=dataset_types, instrument_id=instrument_id,
            # A formal output may legitimately contain rows that become visible
            # after this request.  Row-level filtering below enforces the PIT
            # boundary; rejecting the entire immutable partition would make
            # historical replay impossible once newer rows are appended.
            start=start, end=end, as_of=None, require_formal=True,
        )
        rows: list[dict[str, object]] = []
        for ref in manifest.partition_refs:
            path = self._manifests.resolve_partition(ref)
            try:
                frame = pl.read_parquet(path)
            except Exception as exc:
                raise DataError(detail=f"读取 manifest partition 失败: {ref}", cause=exc) from exc
            if not required_columns.issubset(frame.columns):
                missing = sorted(required_columns.difference(frame.columns))
                raise DataError(detail=f"partition 缺少 PIT 字段，拒绝旧格式: {', '.join(missing)}")
            rows.extend(frame.iter_rows(named=True))
        return rows

    async def query_funding_rates(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[FundingRate]:
        rows = self._read_pit_manifest(
            manifest_id, start, end, decision_time, instrument_id,
            dataset_types={"normalized.funding", "funding"},
            required_columns={"instrument_id", "funding_ts", "rate", "available_at", "ingested_at"},
        )
        result: list[FundingRate] = []
        for row in rows:
            funding_ts = self._pit_datetime(row["funding_ts"], "funding_ts")
            available_at = self._pit_datetime(row["available_at"], "available_at")
            ingested_at = self._pit_datetime(row["ingested_at"], "ingested_at")
            self._ensure_pit_order(available_at, ingested_at)
            source_instrument_id = self._pit_instrument(row["instrument_id"])
            settlement_raw = row.get("settlement_ts")
            settlement_ts = (
                self._pit_datetime(settlement_raw, "settlement_ts")
                if settlement_raw is not None and str(settlement_raw) != "null" else None
            )
            if not (start <= funding_ts < end) or available_at > decision_time or (
                settlement_ts is not None and settlement_ts > decision_time
            ):
                continue
            if source_instrument_id != instrument_id:
                continue
            result.append(FundingRate(
                instrument_id=source_instrument_id,
                funding_ts=funding_ts, rate=Decimal(str(row["rate"])),
                predicted_rate=Decimal(str(row["predicted_rate"]))
                if row.get("predicted_rate") not in (None, "null") else None,
                settlement_ts=settlement_ts, interval=str(row.get("interval", "8h")),
                data_version=str(row.get("data_version", manifest_id)),
                available_at=available_at, ingested_at=ingested_at,
            ))
        return sorted(result, key=lambda item: item.funding_ts)

    async def query_open_interest(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[OpenInterest]:
        rows = self._read_pit_manifest(
            manifest_id, start, end, decision_time, instrument_id,
            dataset_types={"normalized.open_interest", "open_interest"},
            required_columns={"instrument_id", "ts", "oi_value", "available_at", "ingested_at"},
        )
        result: list[OpenInterest] = []
        for row in rows:
            ts = self._pit_datetime(row["ts"], "ts")
            available_at = self._pit_datetime(row["available_at"], "available_at")
            ingested_at = self._pit_datetime(row["ingested_at"], "ingested_at")
            self._ensure_pit_order(available_at, ingested_at)
            source_instrument_id = self._pit_instrument(row["instrument_id"])
            if not (start <= ts < end) or available_at > decision_time:
                continue
            if source_instrument_id != instrument_id:
                continue
            contracts = row.get("oi_contracts")
            result.append(OpenInterest(
                instrument_id=source_instrument_id, ts=ts,
                oi_value=Decimal(str(row["oi_value"])),
                oi_contracts=Decimal(str(contracts)) if contracts not in (None, "null") else None,
                exchange_ts=self._pit_datetime(row["exchange_ts"], "exchange_ts")
                if row.get("exchange_ts") not in (None, "null") else None,
                data_version=str(row.get("data_version", manifest_id)),
                available_at=available_at, ingested_at=ingested_at,
            ))
        return sorted(result, key=lambda item: item.ts)

    async def query_mark_prices(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[MarkPrice]:
        rows = self._read_pit_manifest(
            manifest_id, start, end, decision_time, instrument_id,
            dataset_types={"normalized.mark_price", "mark_price"},
            required_columns={"instrument_id", "ts", "mark_price", "available_at", "ingested_at"},
        )
        result: list[MarkPrice] = []
        for row in rows:
            ts = self._pit_datetime(row["ts"], "ts")
            available_at = self._pit_datetime(row["available_at"], "available_at")
            ingested_at = self._pit_datetime(row["ingested_at"], "ingested_at")
            self._ensure_pit_order(available_at, ingested_at)
            source_instrument_id = self._pit_instrument(row["instrument_id"])
            if not (start <= ts < end) or available_at > decision_time:
                continue
            if source_instrument_id != instrument_id:
                continue
            index_price = row.get("index_price")
            result.append(MarkPrice(
                instrument_id=source_instrument_id, ts=ts,
                mark_price=Decimal(str(row["mark_price"])),
                index_price=Decimal(str(index_price)) if index_price not in (None, "null") else None,
                exchange_ts=self._pit_datetime(row["exchange_ts"], "exchange_ts")
                if row.get("exchange_ts") not in (None, "null") else None,
                available_at=available_at, ingested_at=ingested_at,
            ))
        return sorted(result, key=lambda item: item.ts)

    async def query_features(
        self, instrument_id: str, feature_names: list[str], start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[FeatureSet]:
        rows = self._read_pit_manifest(
            manifest_id, start, end, decision_time, instrument_id,
            dataset_types={"features", "feature.output", "normalized.features"},
            required_columns={
                "instrument_id", "ts", "feature_name", "value", "feature_version",
                "available_at", "decision_time", "source_manifest_id",
            },
            as_of=decision_time,
        )
        result: list[FeatureSet] = []
        names = set(feature_names)
        for row in rows:
            ts = self._pit_datetime(row["ts"], "ts")
            available_at = self._pit_datetime(row["available_at"], "available_at")
            row_decision_time = self._pit_datetime(row["decision_time"], "decision_time")
            source_instrument_id = self._pit_instrument(row["instrument_id"])
            if not (start <= ts < end) or available_at > decision_time or row_decision_time > decision_time:
                continue
            if source_instrument_id != instrument_id:
                continue
            if names and str(row["feature_name"]) not in names:
                continue
            result.append(FeatureSet(
                instrument_id=source_instrument_id, ts=ts,
                feature_name=str(row["feature_name"]), value=Decimal(str(row["value"])),
                feature_version=str(row["feature_version"]), data_version=str(row.get("data_version", manifest_id)),
                available_at=available_at, source_manifest_id=str(row["source_manifest_id"]),
                decision_time=row_decision_time,
            ))
        return sorted(result, key=lambda item: item.ts)

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
        """查询资金费率数据（仅测试/诊断兼容路径）。

        读取 data/normalized/funding/<symbol>/<YYYY>.parquet
        """
        self._require_legacy("get_funding_rates")
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
        """查询持仓量数据（仅测试/诊断兼容路径）。

        读取 data/normalized/open_interest/<symbol>/<YYYY>.parquet
        """
        self._require_legacy("get_open_interest")
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
        """查询标记价格数据（仅测试/诊断兼容路径）。

        读取 data/normalized/mark_price/<symbol>/<YYYY>.parquet
        MVP：若无 mark_price 目录，返回空列表
        """
        self._require_legacy("get_mark_prices")
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
        """查询特征数据（仅测试/诊断兼容路径）。

        读取 data/features/<feature_version>/<interval>/<symbol>/<YYYY>.parquet
        注意：需要 interval 参数，此处从 feature_version 推断或使用默认 "5m"
        """
        self._require_legacy("get_features")
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
        """Legacy display-only placeholder; PIT research must use get_instrument_rule."""
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

    async def get_instrument_rule(
        self,
        instrument_id: str,
        *,
        effective_at: datetime,
        known_at: datetime,
    ) -> InstrumentRuleVersion:
        """Select the historical rule valid at effective_at and known by known_at."""
        return self._meta_db.get_instrument_rule(instrument_id, effective_at, known_at)

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
        *,
        require_pit: bool = False,
        decision_time: datetime | None = None,
    ) -> list[Bar]:
        """将 DataFrame 行转为 Bar 列表"""
        bars: list[Bar] = []
        for row in df.iter_rows(named=True):
            try:
                if require_pit:
                    source_instrument_id = row.get("instrument_id")
                    if source_instrument_id is None:
                        raise DataError(detail="PIT partition 存在缺失 instrument_id")
                    source_instrument_id = DataProvider._pit_instrument(source_instrument_id)
                else:
                    source_instrument_id = str(row.get("instrument_id", instrument_id))

                bar_open_time = (
                    DataProvider._pit_datetime(row["open_time"], "open_time")
                    if require_pit else datetime.fromisoformat(str(row["open_time"]))
                )
                dv = data_version or row.get("data_version", "unknown")
                quality_val = row.get("quality", "OK")

                pit_columns = ("event_time", "available_at", "ingested_at", "close_time", "is_closed")
                if require_pit and not all(row.get(column) is not None for column in pit_columns):
                    raise DataError(detail="PIT partition 存在缺失时间字段")
                event_time = (
                    DataProvider._pit_datetime(row["event_time"], "event_time")
                    if require_pit else datetime.fromisoformat(str(row["event_time"]))
                    if row.get("event_time") is not None else None
                )
                available_at = (
                    DataProvider._pit_datetime(row["available_at"], "available_at")
                    if require_pit else datetime.fromisoformat(str(row["available_at"]))
                    if row.get("available_at") is not None else None
                )
                ingested_at = (
                    DataProvider._pit_datetime(row["ingested_at"], "ingested_at")
                    if require_pit else datetime.fromisoformat(str(row["ingested_at"]))
                    if row.get("ingested_at") is not None else None
                )
                close_time = (
                    DataProvider._pit_datetime(row["close_time"], "close_time")
                    if require_pit else datetime.fromisoformat(str(row["close_time"]))
                    if row.get("close_time") is not None else None
                )
                is_closed = bool(row["is_closed"]) if row.get("is_closed") is not None else None
                if require_pit:
                    assert available_at is not None
                    assert is_closed is not None
                    assert close_time is not None
                    assert ingested_at is not None
                    assert event_time is not None
                    DataProvider._ensure_pit_order(available_at, ingested_at)
                    if available_at < event_time:
                        raise DataError(detail="PIT 时间语义无效: available_at 早于 event_time")
                    if close_time <= bar_open_time:
                        raise DataError(detail="PIT 时间语义无效: close_time 不晚于 open_time")

                if not (start <= bar_open_time < end):
                    continue
                if require_pit:
                    if source_instrument_id != instrument_id:
                        continue
                    assert available_at is not None
                    assert close_time is not None
                    assert is_closed is not None
                    if (
                        available_at > (decision_time or start)
                        or close_time > (decision_time or start)
                        or not is_closed
                    ):
                        continue
                effective_decision_time = decision_time or close_time

                bars.append(Bar(
                    instrument_id=source_instrument_id,
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
                    event_time=event_time,
                    available_at=available_at,
                    ingested_at=ingested_at,
                    close_time=close_time,
                    is_closed=is_closed,
                    decision_time=effective_decision_time,
                ))
            except DataError:
                raise
            except Exception as exc:
                if require_pit:
                    if isinstance(exc, DataError):
                        raise
                    raise DataError(detail="PIT partition 存在无效记录", cause=exc) from exc
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
