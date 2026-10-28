"""Raw → Normalized 数据管线

实现：
- Raw 落盘：data/raw/binance/<symbol>/<data_type>/<date>/（Parquet，只增不改，记录批次 ID）
- Normalized：统一符号映射、时间对齐、精度规整、异常清洗
- 数据质量检查：缺口检测、跳变/异常值、时间戳乱序 → 质量报告
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import polars as pl

from qps.core.constants import DataQuality
from qps.core.errors import DataError
from qps.core.models.market import Bar, FundingRate, OpenInterest

if TYPE_CHECKING:
    from pathlib import Path

logger = logging.getLogger(__name__)


# ── 质量报告 ──────────────────────────────────────────────


class QualityReport:
    """数据质量报告

    记录数据管线处理过程中发现的质量问题。
    """

    def __init__(self, instrument_id: str, interval: str, data_version: str) -> None:
        self.instrument_id = instrument_id
        self.interval = interval
        self.data_version = data_version
        self.total_bars: int = 0
        self.gaps: list[tuple[datetime, datetime]] = []  # (缺口开始, 缺口结束)
        self.out_of_order: int = 0  # 乱序条数
        self.price_jumps: list[tuple[datetime, Decimal]] = []  # (时间, 跳变幅度)
        self.suspect_bars: int = 0
        self.ok_bars: int = 0

    @property
    def overall_quality(self) -> DataQuality:
        """整体质量判定"""
        if self.out_of_order > 0 or len(self.gaps) > 0 or self.suspect_bars > 0:
            return DataQuality.SUSPECT
        return DataQuality.OK

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "instrument_id": self.instrument_id,
            "interval": self.interval,
            "data_version": self.data_version,
            "total_bars": self.total_bars,
            "ok_bars": self.ok_bars,
            "suspect_bars": self.suspect_bars,
            "gap_count": len(self.gaps),
            "gaps": [(s.isoformat(), e.isoformat()) for s, e in self.gaps],
            "out_of_order": self.out_of_order,
            "price_jump_count": len(self.price_jumps),
            "overall_quality": self.overall_quality.value,
        }

    def __repr__(self) -> str:
        return (
            f"QualityReport({self.instrument_id}, {self.interval}, "
            f"total={self.total_bars}, ok={self.ok_bars}, "
            f"suspect={self.suspect_bars}, gaps={len(self.gaps)}, "
            f"ooo={self.out_of_order}, quality={self.overall_quality.value})"
        )


# ── Raw 落盘 ──────────────────────────────────────────────


class RawWriter:
    """Raw 数据落盘

    写入 data/raw/<source>/<symbol>/<data_type>/<YYYY-MM-DD>/part-000.parquet
    只增不改：已有分区不覆盖。
    """

    def __init__(self, raw_dir: Path, source: str = "binance") -> None:
        """初始化 Raw Writer

        Args:
            raw_dir: Raw 数据根目录（如 data/raw/）
            source: 数据源名称
        """
        self._raw_dir = raw_dir / source
        self._source = source

    def write_bars(
        self,
        symbol: str,
        bars: list[Bar],
        batch_id: str | None = None,
        mode: str = "merge",
    ) -> list[Path]:
        """写入 K 线数据到 Raw 层

        按日期分区，每个分区一个 Parquet 文件。
        分区路径包含 interval 维度，避免不同周期混入同一文件。

        Args:
            symbol: 交易所符号（如 btcusdt-perp）
            bars: K 线数据列表
            batch_id: 批次 ID（用于 data_version）
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径列表
        """
        if not bars:
            return []

        # 从第一根 K 线获取 interval
        interval = bars[0].interval

        # 按日期分组
        by_date: dict[date, list[Bar]] = {}
        for bar in bars:
            d = bar.open_time.date()
            by_date.setdefault(d, []).append(bar)

        # 生成 data_version
        if batch_id is None:
            batch_id = f"{symbol}_{datetime.now(tz=UTC).isoformat()}"
        data_version = self._make_data_version(batch_id)

        written_files: list[Path] = []

        for d, day_bars in sorted(by_date.items()):
            # 分区路径包含 interval 维度，避免不同周期混入同一文件
            partition_dir = self._raw_dir / symbol / "klines" / interval / d.isoformat()
            partition_dir.mkdir(parents=True, exist_ok=True)

            existing_file = partition_dir / "part-000.parquet"

            if existing_file.exists():
                if mode == "skip":
                    logger.debug("Raw 分区已存在，跳过: %s", existing_file)
                    continue
                elif mode in ("merge", "append"):
                    # 合并/追加模式：读取已有数据，合并后去重（按 interval + open_time）
                    existing_df = pl.read_parquet(existing_file)
                    new_df = self._bars_to_dataframe(day_bars, data_version)
                    # 按 interval + open_time 去重，保留最新的
                    merged = pl.concat([existing_df, new_df])
                    merged = merged.sort(["interval", "open_time"]).unique(subset=["interval", "open_time"], keep="last")
                    target = self._revision_path(partition_dir, batch_id)
                    merged.write_parquet(target)
                    written_files.append(target)
                    logger.debug("追加 Raw K 线: %s (%d bars)", target, len(merged))
                    continue
                # overwrite 模式继续执行

            # 转为 Polars DataFrame
            df = self._bars_to_dataframe(day_bars, data_version)

            # 写入 Parquet
            target = existing_file if not existing_file.exists() else self._revision_path(partition_dir, batch_id)
            df.write_parquet(target)
            written_files.append(target)
            logger.debug("写入 Raw K 线: %s (%d bars)", target, len(day_bars))

        return written_files

    def write_funding_rates(
        self,
        symbol: str,
        rates: list[FundingRate],
        batch_id: str | None = None,
        mode: str = "merge",
    ) -> list[Path]:
        """写入资金费率到 Raw 层

        Args:
            symbol: 交易所符号（如 btcusdt-perp）
            rates: FundingRate 列表
            batch_id: 批次 ID
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径列表
        """
        if not rates:
            return []

        by_date: dict[date, list[FundingRate]] = {}
        for rate in rates:
            d = rate.funding_ts.date()
            by_date.setdefault(d, []).append(rate)

        if batch_id is None:
            batch_id = f"{symbol}_funding_{datetime.now(tz=UTC).isoformat()}"
        data_version = self._make_data_version(batch_id)

        written_files: list[Path] = []
        for d, day_rates in sorted(by_date.items()):
            partition_dir = self._raw_dir / symbol / "funding" / d.isoformat()
            partition_dir.mkdir(parents=True, exist_ok=True)

            existing_file = partition_dir / "part-000.parquet"

            if existing_file.exists():
                if mode == "skip":
                    logger.debug("Raw funding 分区已存在，跳过: %s", existing_file)
                    continue
                elif mode == "merge":
                    # 合并模式：读取已有数据，合并后按 funding_ts 去重
                    existing_df = pl.read_parquet(existing_file)
                    new_rows = [
                        {
                            "instrument_id": r.instrument_id,
                            "funding_ts": r.funding_ts.isoformat(),
                            "rate": str(r.rate),
                            "predicted_rate": str(r.predicted_rate) if r.predicted_rate else None,
                            "interval": r.interval,
                            "data_version": data_version,
                        }
                        for r in day_rates
                    ]
                    new_df = pl.DataFrame(new_rows)
                    merged = pl.concat([existing_df, new_df])
                    merged = merged.sort("funding_ts").unique(subset=["funding_ts"], keep="last")
                    target = self._revision_path(partition_dir, batch_id)
                    merged.write_parquet(target)
                    written_files.append(target)
                    logger.debug("合并 Raw 资金费率: %s (%d rates)", target, len(merged))
                    continue
                # overwrite 模式继续执行

            rows = [
                {
                    "instrument_id": r.instrument_id,
                    "funding_ts": r.funding_ts.isoformat(),
                    "rate": str(r.rate),
                    "predicted_rate": str(r.predicted_rate) if r.predicted_rate else None,
                    "interval": r.interval,
                    "data_version": data_version,
                }
                for r in day_rates
            ]
            df = pl.DataFrame(rows)
            target = existing_file if not existing_file.exists() else self._revision_path(partition_dir, batch_id)
            df.write_parquet(target)
            written_files.append(target)
            logger.debug("写入 Raw 资金费率: %s (%d rates)", target, len(day_rates))

        return written_files

    def write_open_interest(
        self,
        symbol: str,
        oi_list: list[OpenInterest],
        batch_id: str | None = None,
        mode: str = "merge",
    ) -> list[Path]:
        """写入持仓量到 Raw 层

        Args:
            symbol: 交易所符号（如 btcusdt）
            oi_list: OpenInterest 列表
            batch_id: 批次 ID
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径列表
        """
        if not oi_list:
            return []

        by_date: dict[date, list[OpenInterest]] = {}
        for oi in oi_list:
            d = oi.ts.date()
            by_date.setdefault(d, []).append(oi)

        if batch_id is None:
            batch_id = f"{symbol}_oi_{datetime.now(tz=UTC).isoformat()}"
        data_version = self._make_data_version(batch_id)

        written_files: list[Path] = []
        for d, day_oi in sorted(by_date.items()):
            partition_dir = self._raw_dir / symbol / "open_interest" / d.isoformat()
            partition_dir.mkdir(parents=True, exist_ok=True)

            existing_file = partition_dir / "part-000.parquet"

            if existing_file.exists():
                if mode == "skip":
                    logger.debug("Raw OI 分区已存在，跳过: %s", existing_file)
                    continue
                elif mode == "merge":
                    # 合并模式：读取已有数据，合并后按 ts 去重
                    existing_df = pl.read_parquet(existing_file)
                    new_rows = [
                        {
                            "instrument_id": o.instrument_id,
                            "ts": o.ts.isoformat(),
                            "oi_value": str(o.oi_value),
                            "oi_contracts": str(o.oi_contracts) if o.oi_contracts else None,
                            "exchange_ts": o.exchange_ts.isoformat() if o.exchange_ts else None,
                            "data_version": data_version,
                        }
                        for o in day_oi
                    ]
                    new_df = pl.DataFrame(new_rows)
                    merged = pl.concat([existing_df, new_df])
                    merged = merged.sort("ts").unique(subset=["ts"], keep="last")
                    target = self._revision_path(partition_dir, batch_id)
                    merged.write_parquet(target)
                    written_files.append(target)
                    logger.debug("合并 Raw OI: %s (%d records)", target, len(merged))
                    continue
                # overwrite 模式继续执行

            rows = [
                {
                    "instrument_id": o.instrument_id,
                    "ts": o.ts.isoformat(),
                    "oi_value": str(o.oi_value),
                    "oi_contracts": str(o.oi_contracts) if o.oi_contracts else None,
                    "exchange_ts": o.exchange_ts.isoformat() if o.exchange_ts else None,
                    "data_version": data_version,
                }
                for o in day_oi
            ]
            df = pl.DataFrame(rows)
            target = existing_file if not existing_file.exists() else self._revision_path(partition_dir, batch_id)
            df.write_parquet(target)
            written_files.append(target)
            logger.debug("写入 Raw OI: %s (%d records)", target, len(day_oi))

        return written_files

    def _bars_to_dataframe(self, bars: list[Bar], data_version: str) -> pl.DataFrame:
        """将 Bar 列表转为 Polars DataFrame"""
        rows = [
            {
                "instrument_id": bar.instrument_id,
                "open_time": bar.open_time.isoformat(),
                "interval": bar.interval,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": str(bar.volume),
                "quote_volume": str(bar.quote_volume),
                "taker_buy_volume": str(bar.taker_buy_volume),
                "trades_count": bar.trades_count,
                "data_version": data_version,
                "quality": bar.quality.value,
                "event_time": bar.event_time.isoformat() if bar.event_time else None,
                "available_at": bar.available_at.isoformat() if bar.available_at else None,
                "ingested_at": bar.ingested_at.isoformat() if bar.ingested_at else None,
                "close_time": bar.close_time.isoformat() if bar.close_time else None,
                "is_closed": bar.is_closed,
                "source": self._source,
                "batch_id": data_version,
                "request_id": data_version,
                "source_event_time": bar.event_time.isoformat() if bar.event_time else bar.open_time.isoformat(),
                "raw_payload": json.dumps(
                    bar.raw_payload if bar.raw_payload is not None else bar.model_dump(mode="json"),
                    ensure_ascii=False, sort_keys=True,
                ),
            }
            for bar in bars
        ]
        return pl.DataFrame(rows)

    @staticmethod
    def _revision_path(partition_dir: Path, batch_id: str) -> Path:
        """Return a new immutable revision path for an already existing partition."""
        digest = hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:20]
        candidate = partition_dir / f"part-{digest}.parquet"
        suffix = 1
        while candidate.exists():
            candidate = partition_dir / f"part-{digest}-{suffix}.parquet"
            suffix += 1
        return candidate

    @staticmethod
    def _make_data_version(batch_id: str) -> str:
        """生成数据版本号"""
        short_hash = hashlib.sha256(batch_id.encode()).hexdigest()[:8]
        return f"binance-{short_hash}"


# ── Normalized 管线 ───────────────────────────────────────


class Normalizer:
    """Raw → Normalized 转换

    处理：
    - 统一符号映射
    - 时间对齐（确保 UTC、对齐到间隔边界）
    - 精度规整
    - 异常清洗（零成交量、负价格等）
    """

    # 价格跳变阈值（相对前一根 K 线的收盘价）
    _PRICE_JUMP_THRESHOLD = Decimal("0.10")  # 10%

    def __init__(self, normalized_dir: Path) -> None:
        """初始化 Normalizer

        Args:
            normalized_dir: Normalized 数据根目录
        """
        self._normalized_dir = normalized_dir

    def normalize_bars(
        self,
        bars: list[Bar],
        instrument_id: str,
        interval: str,
    ) -> tuple[list[Bar], QualityReport]:
        """规范化 K 线数据

        Args:
            bars: 原始 K 线列表
            instrument_id: 统一标的 ID
            interval: K 线间隔

        Returns:
            (规范化后的 Bar 列表, 质量报告)
        """
        report = QualityReport(
            instrument_id=instrument_id,
            interval=interval,
            data_version=bars[0].data_version if bars else "unknown",
        )
        report.total_bars = len(bars)

        if not bars:
            return [], report

        # 1. 时间对齐：确保按 open_time 排序
        sorted_bars = sorted(bars, key=lambda b: b.open_time)

        # 2. 检测乱序
        for i in range(1, len(sorted_bars)):
            if sorted_bars[i].open_time <= sorted_bars[i - 1].open_time:
                report.out_of_order += 1

        # 3. 异常清洗与质量标记
        normalized: list[Bar] = []
        prev_close: Decimal | None = None

        for bar in sorted_bars:
            quality = DataQuality.OK
            reasons: list[str] = []

            # 零成交量检查
            if bar.volume <= 0:
                reasons.append("零/负成交量")

            # 价格合理性检查
            if bar.high < bar.low:
                reasons.append("high < low")
            if bar.open <= 0 or bar.close <= 0:
                reasons.append("零/负价格")

            # 价格跳变检查
            if prev_close is not None and prev_close > 0:
                jump = abs(bar.close - prev_close) / prev_close
                if jump > self._PRICE_JUMP_THRESHOLD:
                    report.price_jumps.append((bar.open_time, jump))
                    reasons.append(f"价格跳变 {jump:.2%}")

            # 标记质量
            if reasons:
                quality = DataQuality.SUSPECT
                report.suspect_bars += 1
                logger.debug(
                    "SUSPECT K 线: %s %s %s, 原因: %s",
                    bar.instrument_id,
                    bar.interval,
                    bar.open_time.isoformat(),
                    ", ".join(reasons),
                )
            else:
                report.ok_bars += 1

            # 重建 Bar（统一 instrument_id 和质量标记）
            normalized_bar = Bar(
                instrument_id=instrument_id,
                open_time=bar.open_time,
                interval=interval,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
                quote_volume=bar.quote_volume,
                taker_buy_volume=bar.taker_buy_volume,
                trades_count=bar.trades_count,
                data_version=bar.data_version,
                quality=quality,
                event_time=bar.event_time,
                available_at=bar.available_at,
                ingested_at=bar.ingested_at,
                close_time=bar.close_time,
                is_closed=bar.is_closed,
                decision_time=bar.decision_time,
            )
            normalized.append(normalized_bar)
            prev_close = bar.close

        # 4. 缺口检测
        report.gaps = self._detect_gaps(sorted_bars, interval)

        return normalized, report

    def write_normalized_bars(
        self,
        bars: list[Bar],
        instrument_id: str,
        interval: str,
        mode: str = "merge",
    ) -> Path:
        """写入 Normalized K 线数据

        按年分片：data/normalized/<interval>/<instrument>/<YYYY>.parquet

        Args:
            bars: 规范化后的 K 线列表
            instrument_id: 内部标的 ID
            interval: K 线间隔
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'append'（追加）

        Returns:
            写入的文件路径
        """
        if not bars:
            raise DataError(detail="无数据可写入")

        # 使用 instrument_id 中的符号部分作为目录名
        # 如 binance-btc-usdt-perp → btc-usdt-perp
        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id

        last_path: Path | None = None

        # 按年分组处理
        by_year: dict[int, list[Bar]] = {}
        for bar in bars:
            year = bar.open_time.year
            by_year.setdefault(year, []).append(bar)

        for year, year_bars in sorted(by_year.items()):
            out_dir = self._normalized_dir / interval / symbol_dir
            out_dir.mkdir(parents=True, exist_ok=True)

            out_path = out_dir / f"{year}.parquet"

            if out_path.exists() and mode == "merge":
                # 合并模式：读取已有数据，合并后去重
                existing_df = pl.read_parquet(out_path)
                new_rows = [
                    {
                        "instrument_id": bar.instrument_id,
                        "open_time": bar.open_time.isoformat(),
                        "interval": bar.interval,
                        "open": str(bar.open),
                        "high": str(bar.high),
                        "low": str(bar.low),
                        "close": str(bar.close),
                        "volume": str(bar.volume),
                        "quote_volume": str(bar.quote_volume),
                        "taker_buy_volume": str(bar.taker_buy_volume),
                        "trades_count": bar.trades_count,
                        "data_version": bar.data_version,
                        "quality": bar.quality.value,
                        "event_time": bar.event_time.isoformat() if bar.event_time else None,
                        "available_at": bar.available_at.isoformat() if bar.available_at else None,
                        "ingested_at": bar.ingested_at.isoformat() if bar.ingested_at else None,
                        "close_time": bar.close_time.isoformat() if bar.close_time else None,
                        "is_closed": bar.is_closed,
                        "raw_lineage": bar.data_version,
                        "raw_payload": json.dumps(
                            bar.raw_payload if bar.raw_payload is not None else bar.model_dump(mode="json"),
                            ensure_ascii=False, sort_keys=True,
                        ),
                    }
                    for bar in year_bars
                ]
                new_df = pl.DataFrame(new_rows)
                # 按 open_time 去重，保留最新的（通过 data_version 判断）
                merged = pl.concat([existing_df, new_df])
                merged = merged.sort("open_time").unique(subset=["open_time"], keep="last")
                target = self._revision_path(out_dir, year, year_bars)
                merged.write_parquet(target)
                last_path = target
                logger.debug("合并 Normalized K 线: %s (%d bars)", target, len(merged))
            else:
                # 覆盖或追加模式
                rows = [
                    {
                        "instrument_id": bar.instrument_id,
                        "open_time": bar.open_time.isoformat(),
                        "interval": bar.interval,
                        "open": str(bar.open),
                        "high": str(bar.high),
                        "low": str(bar.low),
                        "close": str(bar.close),
                        "volume": str(bar.volume),
                        "quote_volume": str(bar.quote_volume),
                        "taker_buy_volume": str(bar.taker_buy_volume),
                        "trades_count": bar.trades_count,
                        "data_version": bar.data_version,
                        "quality": bar.quality.value,
                        "event_time": bar.event_time.isoformat() if bar.event_time else None,
                        "available_at": bar.available_at.isoformat() if bar.available_at else None,
                        "ingested_at": bar.ingested_at.isoformat() if bar.ingested_at else None,
                        "close_time": bar.close_time.isoformat() if bar.close_time else None,
                        "is_closed": bar.is_closed,
                        "raw_lineage": bar.data_version,
                        "raw_payload": json.dumps(
                            bar.raw_payload if bar.raw_payload is not None else bar.model_dump(mode="json"),
                            ensure_ascii=False, sort_keys=True,
                        ),
                    }
                    for bar in year_bars
                ]
                df = pl.DataFrame(rows)
                if mode == "append" and out_path.exists():
                    # 追加模式也要去重（按 open_time）
                    existing_df = pl.read_parquet(out_path)
                    df = pl.concat([existing_df, df])
                    df = df.sort("open_time").unique(subset=["open_time"], keep="last")
                target = out_path if not out_path.exists() else self._revision_path(out_dir, year, year_bars)
                df.write_parquet(target)
                last_path = target
                logger.debug("写入 Normalized K 线: %s (%d bars)", target, len(year_bars))

        if last_path is None:
            raise DataError(detail="无数据可写入")
        return last_path

    @staticmethod
    def _revision_path(out_dir: Path, year: int, bars: list[Bar]) -> Path:
        payload = "|".join(
            f"{bar.open_time.isoformat()}:{bar.data_version}:{bar.close}" for bar in bars
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
        path = out_dir / f"{year}-{digest}.parquet"
        suffix = 1
        while path.exists():
            path = out_dir / f"{year}-{digest}-{suffix}.parquet"
            suffix += 1
        return path

    @staticmethod
    def _detect_gaps(bars: list[Bar], interval: str) -> list[tuple[datetime, datetime]]:
        """检测 K 线缺口

        Args:
            bars: 按 open_time 排序的 K 线列表
            interval: K 线间隔

        Returns:
            缺口列表 [(缺口开始, 缺口结束)]
        """
        if len(bars) < 2:
            return []

        interval_td = _interval_to_timedelta(interval)
        gaps: list[tuple[datetime, datetime]] = []

        for i in range(1, len(bars)):
            expected = bars[i - 1].open_time + interval_td
            actual = bars[i].open_time

            if actual > expected:
                gaps.append((expected, actual))

        return gaps

    def normalize_funding_rates(
        self,
        rates: list[FundingRate],
        instrument_id: str,
    ) -> tuple[list[FundingRate], QualityReport]:
        """规范化资金费率数据

        Args:
            rates: 原始资金费率列表
            instrument_id: 统一标的 ID

        Returns:
            (规范化后的 FundingRate 列表, 质量报告)
        """
        report = QualityReport(
            instrument_id=instrument_id,
            interval="8h",
            data_version=rates[0].data_version if rates else "unknown",
        )
        report.total_bars = len(rates)

        if not rates:
            return [], report

        # 按时间排序
        sorted_rates = sorted(rates, key=lambda r: r.funding_ts)

        # 异常清洗与质量标记
        normalized: list[FundingRate] = []

        for rate in sorted_rates:
            reasons: list[str] = []

            # 费率合理性检查
            if abs(rate.rate) > Decimal("1.0"):
                reasons.append(f"费率异常: {rate.rate}")

            # 标记质量
            if reasons:
                report.suspect_bars += 1
                logger.debug(
                    "SUSPECT 资金费率: %s %s, 原因: %s",
                    rate.instrument_id,
                    rate.funding_ts.isoformat(),
                    ", ".join(reasons),
                )
            else:
                report.ok_bars += 1

            # 重建 FundingRate（统一 instrument_id）
            normalized_rate = FundingRate(
                instrument_id=instrument_id,
                funding_ts=rate.funding_ts,
                rate=rate.rate,
                predicted_rate=rate.predicted_rate,
                interval=rate.interval,
            )
            normalized.append(normalized_rate)

        return normalized, report

    def write_normalized_funding_rates(
        self,
        rates: list[FundingRate],
        instrument_id: str,
        mode: str = "merge",
    ) -> Path:
        """写入 Normalized 资金费率数据

        按年分片：data/normalized/funding/<instrument>/<YYYY>.parquet

        Args:
            rates: 规范化后的资金费率列表
            instrument_id: 内部标的 ID
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径
        """
        if not rates:
            raise DataError(detail="无数据可写入")

        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id

        last_path: Path | None = None

        # 按年分组处理
        by_year: dict[int, list[FundingRate]] = {}
        for rate in rates:
            year = rate.funding_ts.year
            by_year.setdefault(year, []).append(rate)

        for year, year_rates in sorted(by_year.items()):
            out_dir = self._normalized_dir / "funding" / symbol_dir
            out_dir.mkdir(parents=True, exist_ok=True)

            out_path = out_dir / f"{year}.parquet"

            if out_path.exists() and mode == "merge":
                # 合并模式：读取已有数据，合并后去重
                existing_df = pl.read_parquet(out_path)
                new_rows = [
                    {
                        "instrument_id": r.instrument_id,
                        "funding_ts": r.funding_ts.isoformat(),
                        "rate": str(r.rate),
                        "predicted_rate": str(r.predicted_rate) if r.predicted_rate else None,
                        "interval": r.interval,
                    }
                    for r in year_rates
                ]
                new_df = pl.DataFrame(new_rows)
                merged = pl.concat([existing_df, new_df])
                merged = merged.sort("funding_ts").unique(subset=["funding_ts"], keep="last")
                merged.write_parquet(out_path)
                last_path = out_path
                logger.debug("合并 Normalized 资金费率: %s (%d rates)", out_path, len(merged))
            else:
                rows = [
                    {
                        "instrument_id": r.instrument_id,
                        "funding_ts": r.funding_ts.isoformat(),
                        "rate": str(r.rate),
                        "predicted_rate": str(r.predicted_rate) if r.predicted_rate else None,
                        "interval": r.interval,
                    }
                    for r in year_rates
                ]
                df = pl.DataFrame(rows)
                df.write_parquet(out_path)
                last_path = out_path
                logger.debug("写入 Normalized 资金费率: %s (%d rates)", out_path, len(year_rates))

        if last_path is None:
            raise DataError(detail="无数据可写入")
        return last_path

    def normalize_open_interest(
        self,
        oi_list: list[OpenInterest],
        instrument_id: str,
    ) -> tuple[list[OpenInterest], QualityReport]:
        """规范化持仓量数据

        Args:
            oi_list: 原始持仓量列表
            instrument_id: 统一标的 ID

        Returns:
            (规范化后的 OpenInterest 列表, 质量报告)
        """
        report = QualityReport(
            instrument_id=instrument_id,
            interval="5m",
            data_version=oi_list[0].data_version if oi_list else "unknown",
        )
        report.total_bars = len(oi_list)

        if not oi_list:
            return [], report

        # 按时间排序
        sorted_oi = sorted(oi_list, key=lambda o: o.ts)

        # 异常清洗与质量标记
        normalized: list[OpenInterest] = []

        for oi in sorted_oi:
            reasons: list[str] = []

            # OI 值合理性检查
            if oi.oi_value <= 0:
                reasons.append("OI 值为负或零")

            # 标记质量
            if reasons:
                report.suspect_bars += 1
                logger.debug(
                    "SUSPECT 持仓量: %s %s, 原因: %s",
                    oi.instrument_id,
                    oi.ts.isoformat(),
                    ", ".join(reasons),
                )
            else:
                report.ok_bars += 1

            # 重建 OpenInterest（统一 instrument_id）
            normalized_oi = OpenInterest(
                instrument_id=instrument_id,
                ts=oi.ts,
                oi_value=oi.oi_value,
                oi_contracts=oi.oi_contracts,
                exchange_ts=oi.exchange_ts,
            )
            normalized.append(normalized_oi)

        return normalized, report

    def write_normalized_open_interest(
        self,
        oi_list: list[OpenInterest],
        instrument_id: str,
        mode: str = "merge",
    ) -> Path:
        """写入 Normalized 持仓量数据

        按年分片：data/normalized/open_interest/<instrument>/<YYYY>.parquet

        Args:
            oi_list: 规范化后的持仓量列表
            instrument_id: 内部标的 ID
            mode: 写入模式 - 'merge'（合并去重，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径
        """
        if not oi_list:
            raise DataError(detail="无数据可写入")

        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id

        last_path: Path | None = None

        # 按年分组处理
        by_year: dict[int, list[OpenInterest]] = {}
        for oi in oi_list:
            year = oi.ts.year
            by_year.setdefault(year, []).append(oi)

        for year, year_oi in sorted(by_year.items()):
            out_dir = self._normalized_dir / "open_interest" / symbol_dir
            out_dir.mkdir(parents=True, exist_ok=True)

            out_path = out_dir / f"{year}.parquet"

            if out_path.exists() and mode == "merge":
                # 合并模式：读取已有数据，合并后去重
                existing_df = pl.read_parquet(out_path)
                new_rows = [
                    {
                        "instrument_id": o.instrument_id,
                        "ts": o.ts.isoformat(),
                        "oi_value": str(o.oi_value),
                        "oi_contracts": str(o.oi_contracts) if o.oi_contracts else None,
                        "exchange_ts": o.exchange_ts.isoformat() if o.exchange_ts else None,
                    }
                    for o in year_oi
                ]
                new_df = pl.DataFrame(new_rows)
                merged = pl.concat([existing_df, new_df])
                merged = merged.sort("ts").unique(subset=["ts"], keep="last")
                merged.write_parquet(out_path)
                last_path = out_path
                logger.debug("合并 Normalized OI: %s (%d records)", out_path, len(merged))
            else:
                rows = [
                    {
                        "instrument_id": o.instrument_id,
                        "ts": o.ts.isoformat(),
                        "oi_value": str(o.oi_value),
                        "oi_contracts": str(o.oi_contracts) if o.oi_contracts else None,
                        "exchange_ts": o.exchange_ts.isoformat() if o.exchange_ts else None,
                    }
                    for o in year_oi
                ]
                df = pl.DataFrame(rows)
                df.write_parquet(out_path)
                last_path = out_path
                logger.debug("写入 Normalized OI: %s (%d records)", out_path, len(year_oi))

        if last_path is None:
            raise DataError(detail="无数据可写入")
        return last_path


# ── 数据质量检查 ──────────────────────────────────────────


class QualityChecker:
    """数据质量检查器

    对已落盘的 Normalized 数据执行质量检查：
    - 缺口检测
    - 跳变/异常值
    - 时间戳乱序
    - 资金费率与 OI 缺失率
    """

    def __init__(self, normalized_dir: Path) -> None:
        self._normalized_dir = normalized_dir

    def check_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> QualityReport:
        """对 K 线数据执行质量检查

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            start: 检查开始时间
            end: 检查结束时间

        Returns:
            质量报告
        """
        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id
        data_dir = self._normalized_dir / interval / symbol_dir

        report = QualityReport(
            instrument_id=instrument_id,
            interval=interval,
            data_version="check",
        )

        if not data_dir.exists():
            return report

        # 读取相关年份的 Parquet 文件
        all_bars: list[Bar] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if parquet_path.exists():
                try:
                    df = pl.read_parquet(parquet_path)
                    for row in df.iter_rows(named=True):
                        bar_open_time = datetime.fromisoformat(row["open_time"])
                        if start <= bar_open_time < end:
                            all_bars.append(Bar(
                                instrument_id=row["instrument_id"],
                                open_time=bar_open_time,
                                interval=row["interval"],
                                open=Decimal(row["open"]),
                                high=Decimal(row["high"]),
                                low=Decimal(row["low"]),
                                close=Decimal(row["close"]),
                                volume=Decimal(row["volume"]),
                                quote_volume=Decimal(row["quote_volume"]),
                                taker_buy_volume=Decimal(row.get("taker_buy_volume", "0")),
                                trades_count=int(row.get("trades_count", 0)),
                                data_version=row.get("data_version", "unknown"),
                                quality=DataQuality(row.get("quality", "OK")),
                            ))
                except Exception:
                    logger.exception("读取 Normalized 数据失败: %s", parquet_path)
                    continue

        report.total_bars = len(all_bars)
        report.ok_bars = sum(1 for b in all_bars if b.quality == DataQuality.OK)
        report.suspect_bars = sum(1 for b in all_bars if b.quality == DataQuality.SUSPECT)

        # 缺口检测
        sorted_bars = sorted(all_bars, key=lambda b: b.open_time)
        report.gaps = Normalizer._detect_gaps(sorted_bars, interval)

        # 乱序检测
        for i in range(1, len(sorted_bars)):
            if sorted_bars[i].open_time <= sorted_bars[i - 1].open_time:
                report.out_of_order += 1

        return report

    def check_funding_rates(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """对资金费率数据执行质量检查

        Args:
            instrument_id: 内部标的 ID
            start: 检查开始时间
            end: 检查结束时间

        Returns:
            质量报告字典
        """
        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id
        data_dir = self._normalized_dir / "funding" / symbol_dir

        result: dict[str, Any] = {
            "instrument_id": instrument_id,
            "total_expected": 0,
            "total_actual": 0,
            "missing_rate": 0.0,
            "suspect_count": 0,
        }

        if not data_dir.exists():
            result["missing_rate"] = 1.0
            return result

        # 计算预期资金费率数量（8h 间隔）
        # Binance 资金费率结算点为 00:00, 08:00, 16:00 UTC
        # 需将 start 对齐到最近的结算点
        expected_interval = timedelta(hours=8)
        # 对齐到最近的 Binance 结算点（00:00, 08:00, 16:00 UTC）
        hours_offset = (start.hour % 8) * 3600 + start.minute * 60 + start.second
        aligned_start = start + timedelta(seconds=(8 * 3600 - hours_offset) % (8 * 3600))
        current = aligned_start
        while current < end:
            result["total_expected"] += 1
            current += expected_interval

        # 读取实际数据
        all_rates: list[FundingRate] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if parquet_path.exists():
                try:
                    df = pl.read_parquet(parquet_path)
                    for row in df.iter_rows(named=True):
                        funding_ts = datetime.fromisoformat(row["funding_ts"])
                        if start <= funding_ts < end:
                            all_rates.append(FundingRate(
                                instrument_id=row["instrument_id"],
                                funding_ts=funding_ts,
                                rate=Decimal(row["rate"]),
                                predicted_rate=Decimal(row["predicted_rate"]) if row.get("predicted_rate") else None,
                                interval=row.get("interval", "8h"),
                            ))
                except Exception:
                    logger.exception("读取 Normalized 资金费率失败: %s", parquet_path)
                    continue

        result["total_actual"] = len(all_rates)
        result["missing_rate"] = 1.0 - (len(all_rates) / result["total_expected"]) if result["total_expected"] > 0 else 1.0
        result["suspect_count"] = sum(1 for r in all_rates if abs(r.rate) > Decimal("1.0"))

        return result

    def check_open_interest(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        period: str = "5m",
    ) -> dict[str, Any]:
        """对持仓量数据执行质量检查

        Args:
            instrument_id: 内部标的 ID
            start: 检查开始时间
            end: 检查结束时间
            period: OI 数据间隔（默认 5m）

        Returns:
            质量报告字典
        """
        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id
        data_dir = self._normalized_dir / "open_interest" / symbol_dir

        result: dict[str, Any] = {
            "instrument_id": instrument_id,
            "total_expected": 0,
            "total_actual": 0,
            "missing_rate": 0.0,
            "suspect_count": 0,
        }

        if not data_dir.exists():
            result["missing_rate"] = 1.0
            return result

        # 计算预期持仓量数量（根据 period 参数）
        expected_interval = _interval_to_timedelta(period)
        current = start
        while current < end:
            result["total_expected"] += 1
            current += expected_interval

        # 读取实际数据
        all_oi: list[OpenInterest] = []
        for year in range(start.year, end.year + 1):
            parquet_path = data_dir / f"{year}.parquet"
            if parquet_path.exists():
                try:
                    df = pl.read_parquet(parquet_path)
                    for row in df.iter_rows(named=True):
                        ts = datetime.fromisoformat(row["ts"])
                        if start <= ts < end:
                            all_oi.append(OpenInterest(
                                instrument_id=row["instrument_id"],
                                ts=ts,
                                oi_value=Decimal(row["oi_value"]),
                                oi_contracts=Decimal(row["oi_contracts"]) if row.get("oi_contracts") else None,
                                exchange_ts=datetime.fromisoformat(row["exchange_ts"]) if row.get("exchange_ts") else None,
                            ))
                except Exception:
                    logger.exception("读取 Normalized 持仓量失败: %s", parquet_path)
                    continue

        result["total_actual"] = len(all_oi)
        result["missing_rate"] = 1.0 - (len(all_oi) / result["total_expected"]) if result["total_expected"] > 0 else 1.0
        result["suspect_count"] = sum(1 for o in all_oi if o.oi_value <= 0)

        return result


# ── 工具函数 ──────────────────────────────────────────────


def _interval_to_timedelta(interval: str) -> timedelta:
    """将 K 线间隔字符串转为 timedelta"""
    mapping: dict[str, timedelta] = {
        "1m": timedelta(minutes=1),
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
    }
    return mapping.get(interval, timedelta(minutes=5))
