"""T3 Raw → Normalized 管线单元测试

测试：
- RawWriter: K 线落盘、资金费率落盘、只增不改
- Normalizer: 时间对齐、异常清洗、质量标记、缺口检测
- QualityChecker: 质量检查
- QualityReport: 整体质量判定
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from qps.core.constants import DataQuality
from qps.core.models.market import Bar, FundingRate
from qps.data.pipeline.normalizer import (
    Normalizer,
    QualityChecker,
    QualityReport,
    RawWriter,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── 辅助函数 ──────────────────────────────────────────────


def _make_bar(
    open_time: datetime,
    close: Decimal = Decimal("67000"),
    volume: Decimal = Decimal("100"),
    quality: DataQuality = DataQuality.OK,
    data_version: str = "binance-test",
) -> Bar:
    return Bar(
        instrument_id="binance-btc-usdt-perp",
        open_time=open_time,
        interval="5m",
        open=Decimal("67000"),
        high=Decimal("67100"),
        low=Decimal("66900"),
        close=close,
        volume=volume,
        quote_volume=Decimal("6700000"),
        data_version=data_version,
        quality=quality,
    )


def _make_funding_rate(
    funding_ts: datetime,
    rate: Decimal = Decimal("0.0001"),
) -> FundingRate:
    return FundingRate(
        instrument_id="binance-btc-usdt-perp",
        funding_ts=funding_ts,
        rate=rate,
    )


# ── RawWriter ─────────────────────────────────────────────


class TestRawWriter:
    def test_write_bars_creates_parquet(self, tmp_path: Path) -> None:
        """K 线落盘创建 Parquet 文件"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
        ]

        files = writer.write_bars("btcusdt-perp", bars)
        assert len(files) == 1
        assert files[0].exists()
        assert files[0].name == "part-000.parquet"

    def test_write_bars_partition_by_date(self, tmp_path: Path) -> None:
        """K 线按日期分区"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC)),
        ]

        files = writer.write_bars("btcusdt-perp", bars)
        assert len(files) == 2

    def test_write_bars_no_overwrite(self, tmp_path: Path) -> None:
        """已有分区不覆盖（只增不改）"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        bars = [_make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))]

        # 第一次写入
        files1 = writer.write_bars("btcusdt-perp", bars)
        assert len(files1) == 1

        # 第二次写入相同日期（应跳过）
        files2 = writer.write_bars("btcusdt-perp", bars)
        assert len(files2) == 0

    def test_write_bars_empty(self, tmp_path: Path) -> None:
        """空数据不写入"""
        writer = RawWriter(raw_dir=tmp_path / "raw", source="binance")
        files = writer.write_bars("btcusdt-perp", [])
        assert files == []

    def test_write_funding_rates(self, tmp_path: Path) -> None:
        """资金费率落盘"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        rates = [
            _make_funding_rate(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_funding_rate(datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)),
        ]

        files = writer.write_funding_rates("btcusdt-perp", rates)
        assert len(files) == 1


# ── Normalizer ────────────────────────────────────────────


class TestNormalizer:
    def test_normalize_bars_sorts_by_time(self, tmp_path: Path) -> None:
        """规范化后按时间排序"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        assert result[0].open_time < result[1].open_time

    def test_normalize_empty_bars(self, tmp_path: Path) -> None:
        """空数据返回空列表"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")
        result, report = normalizer.normalize_bars([], "test", "5m")
        assert result == []
        assert report.total_bars == 0

    def test_detect_zero_volume_as_suspect(self, tmp_path: Path) -> None:
        """零成交量标记为 SUSPECT"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), volume=Decimal("0")),
        ]

        result, report = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        assert result[0].quality == DataQuality.SUSPECT
        assert report.suspect_bars == 1

    def test_detect_high_low_inversion(self, tmp_path: Path) -> None:
        """high < low 标记为 SUSPECT"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bar = Bar(
            instrument_id="test",
            open_time=datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            interval="5m",
            open=Decimal("67000"),
            high=Decimal("66800"),  # high < low
            low=Decimal("66900"),
            close=Decimal("67000"),
            volume=Decimal("100"),
            quote_volume=Decimal("6700000"),
            data_version="v1",
        )

        result, _report = normalizer.normalize_bars([bar], "test", "5m")
        assert result[0].quality == DataQuality.SUSPECT

    def test_detect_price_jump(self, tmp_path: Path) -> None:
        """价格跳变检测（>10%）"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), close=Decimal("67000")),
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC), close=Decimal("80000")),  # 跳变 > 10%
        ]

        result, report = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        assert len(report.price_jumps) == 1
        assert result[1].quality == DataQuality.SUSPECT

    def test_detect_gaps(self, tmp_path: Path) -> None:
        """缺口检测"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        # 0:00 和 0:15 之间有 5m 缺口（少了 0:05 和 0:10）
        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 15, 0, tzinfo=UTC)),
        ]

        _, report = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        assert len(report.gaps) >= 1

    def test_no_gaps_continuous(self, tmp_path: Path) -> None:
        """连续 K 线无缺口"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)),
        ]

        _, report = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        assert len(report.gaps) == 0

    def test_write_normalized_bars(self, tmp_path: Path) -> None:
        """写入 Normalized 数据"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        path = normalizer.write_normalized_bars(result, "binance-btc-usdt-perp", "5m")

        assert path.exists()
        assert path.name == "2026.parquet"


# ── QualityReport ─────────────────────────────────────────


class TestQualityReport:
    def test_overall_quality_ok(self) -> None:
        """无问题 → OK"""
        report = QualityReport("test", "5m", "v1")
        report.total_bars = 100
        report.ok_bars = 100
        assert report.overall_quality == DataQuality.OK

    def test_overall_quality_suspect_with_gaps(self) -> None:
        """有缺口 → SUSPECT"""
        report = QualityReport("test", "5m", "v1")
        report.gaps = [(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))]
        assert report.overall_quality == DataQuality.SUSPECT

    def test_overall_quality_suspect_with_ooo(self) -> None:
        """有乱序 → SUSPECT"""
        report = QualityReport("test", "5m", "v1")
        report.out_of_order = 1
        assert report.overall_quality == DataQuality.SUSPECT

    def test_to_dict(self) -> None:
        """序列化为字典"""
        report = QualityReport("test", "5m", "v1")
        report.total_bars = 100
        report.ok_bars = 95
        report.suspect_bars = 5
        d = report.to_dict()
        assert d["total_bars"] == 100
        assert d["overall_quality"] == "SUSPECT"
        assert d["gap_count"] == 0


# ── QualityChecker ────────────────────────────────────────


class TestQualityChecker:
    def test_check_nonexistent_data(self, tmp_path: Path) -> None:
        """不存在的数据返回空报告"""
        checker = QualityChecker(normalized_dir=tmp_path / "normalized")
        report = checker.check_bars(
            "binance-btc-usdt-perp",
            "5m",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert report.total_bars == 0

    def test_check_written_data(self, tmp_path: Path) -> None:
        """检查已写入的数据"""
        normalized_dir = tmp_path / "normalized"
        normalizer = Normalizer(normalized_dir=normalized_dir)

        bars = [
            _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_bars(bars, "binance-btc-usdt-perp", "5m")
        normalizer.write_normalized_bars(result, "binance-btc-usdt-perp", "5m")

        checker = QualityChecker(normalized_dir=normalized_dir)
        report = checker.check_bars(
            "binance-btc-usdt-perp",
            "5m",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )

        assert report.total_bars == 2
        assert report.ok_bars == 2
