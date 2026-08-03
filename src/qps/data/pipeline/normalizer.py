"""Raw → Normalized 数据管线

实现：
- Raw 落盘：data/raw/binance/<symbol>/<data_type>/<date>/（Parquet，只增不改，记录批次 ID）
- Normalized：统一符号映射、时间对齐、精度规整、异常清洗
- 数据质量检查：缺口检测、跳变/异常值、时间戳乱序 → 质量报告
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import polars as pl

from qps.core.constants import DataQuality
from qps.core.errors import DataError
from qps.core.models.market import Bar, FundingRate

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
        mode: str = "append",
    ) -> list[Path]:
        """写入 K 线数据到 Raw 层

        按日期分区，每个分区一个 Parquet 文件。

        Args:
            symbol: 交易所符号（如 btcusdt-perp）
            bars: K 线数据列表
            batch_id: 批次 ID（用于 data_version）
            mode: 写入模式 - 'append'（追加，默认）、'overwrite'（覆盖）、'skip'（跳过已有）

        Returns:
            写入的文件路径列表
        """
        if not bars:
            return []

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
            partition_dir = self._raw_dir / symbol / "klines" / d.isoformat()
            partition_dir.mkdir(parents=True, exist_ok=True)

            existing_file = partition_dir / "part-000.parquet"

            if existing_file.exists():
                if mode == "skip":
                    logger.debug("Raw 分区已存在，跳过: %s", existing_file)
                    continue
                elif mode == "append":
                    # 追加模式：读取已有数据，合并后去重
                    existing_df = pl.read_parquet(existing_file)
                    new_df = self._bars_to_dataframe(day_bars, data_version)
                    # 按 open_time 去重，保留最新的
                    merged = pl.concat([existing_df, new_df])
                    merged = merged.sort("open_time").unique(subset=["open_time"], keep="last")
                    merged.write_parquet(existing_file)
                    written_files.append(existing_file)
                    logger.debug("追加 Raw K 线: %s (%d bars)", existing_file, len(merged))
                    continue
                # overwrite 模式继续执行

            # 转为 Polars DataFrame
            df = self._bars_to_dataframe(day_bars, data_version)

            # 写入 Parquet
            df.write_parquet(existing_file)
            written_files.append(existing_file)
            logger.debug("写入 Raw K 线: %s (%d bars)", existing_file, len(day_bars))

        return written_files

    def write_funding_rates(
        self,
        symbol: str,
        rates: list[FundingRate],
        batch_id: str | None = None,
    ) -> list[Path]:
        """写入资金费率到 Raw 层"""
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
                continue

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
            df.write_parquet(existing_file)
            written_files.append(existing_file)

        return written_files

    @staticmethod
    def _bars_to_dataframe(bars: list[Bar], data_version: str) -> pl.DataFrame:
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
            }
            for bar in bars
        ]
        return pl.DataFrame(rows)

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
                    }
                    for bar in year_bars
                ]
                new_df = pl.DataFrame(new_rows)
                # 按 open_time 去重，保留最新的（通过 data_version 判断）
                merged = pl.concat([existing_df, new_df])
                merged = merged.sort("open_time").unique(subset=["open_time"], keep="last")
                merged.write_parquet(out_path)
                last_path = out_path
                logger.debug("合并 Normalized K 线: %s (%d bars)", out_path, len(merged))
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
                    }
                    for bar in year_bars
                ]
                df = pl.DataFrame(rows)
                if mode == "append" and out_path.exists():
                    existing_df = pl.read_parquet(out_path)
                    df = pl.concat([existing_df, df])
                df.write_parquet(out_path)
                last_path = out_path
                logger.debug("写入 Normalized K 线: %s (%d bars)", out_path, len(year_bars))

        if last_path is None:
            raise DataError(detail="无数据可写入")
        return last_path

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
