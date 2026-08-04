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
from qps.core.models.market import Bar, FundingRate, OpenInterest
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
    data_version: str = "binance-test",
) -> FundingRate:
    return FundingRate(
        instrument_id="binance-btc-usdt-perp",
        funding_ts=funding_ts,
        rate=rate,
        data_version=data_version,
    )


def _make_oi(
    ts: datetime,
    oi_value: Decimal = Decimal("1000000"),
    data_version: str = "binance-test",
) -> OpenInterest:
    return OpenInterest(
        instrument_id="binance-btc-usdt-perp",
        ts=ts,
        oi_value=oi_value,
        data_version=data_version,
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
        """已有分区不覆盖（skip 模式）"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        bars = [_make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))]

        # 第一次写入
        files1 = writer.write_bars("btcusdt-perp", bars)
        assert len(files1) == 1

        # 第二次写入相同日期（skip 模式应跳过）
        files2 = writer.write_bars("btcusdt-perp", bars, mode="skip")
        assert len(files2) == 0

        # append 模式应合并去重
        files3 = writer.write_bars("btcusdt-perp", bars, mode="append")
        assert len(files3) == 1  # 返回已存在的文件路径

    def test_write_bars_empty(self, tmp_path: Path) -> None:
        """空数据不写入"""
        writer = RawWriter(raw_dir=tmp_path / "raw", source="binance")
        files = writer.write_bars("btcusdt-perp", [])
        assert files == []

    def test_write_bars_merge_default_keeps_existing_rows(self, tmp_path: Path) -> None:
        """默认 merge 模式：已有分区合并去重而非覆盖（M3.4 M1 遗留回归）"""
        raw_dir = tmp_path / "raw"
        writer = RawWriter(raw_dir=raw_dir, source="binance")

        bar1 = _make_bar(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
        bar2 = _make_bar(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC))

        files1 = writer.write_bars("btcusdt-perp", [bar1])
        assert len(files1) == 1

        # 第二次默认（merge）写入：不同 bar 应合并，已有 bar 不应被覆盖丢失
        files2 = writer.write_bars("btcusdt-perp", [bar2])
        assert len(files2) == 1

        import polars as pl
        df = pl.read_parquet(files2[0])
        assert df.height == 2, f"merge 后应保留 2 根 bar，实际 {df.height}"

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


# ── FundingRate 规范化测试 ─────────────────────────────────


class TestFundingRateNormalization:
    def test_normalize_funding_rates_sorts_by_time(self, tmp_path: Path) -> None:
        """资金费率按时间排序"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        rates = [
            _make_funding_rate(datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)),
            _make_funding_rate(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_funding_rates(rates, "binance-btc-usdt-perp")
        assert result[0].funding_ts < result[1].funding_ts

    def test_normalize_empty_funding_rates(self, tmp_path: Path) -> None:
        """空数据返回空列表"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")
        result, report = normalizer.normalize_funding_rates([], "test")
        assert result == []
        assert report.total_bars == 0

    def test_detect_abnormal_funding_rate(self, tmp_path: Path) -> None:
        """异常费率标记为 SUSPECT（>1.0%）"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        rates = [
            _make_funding_rate(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), rate=Decimal("2.0")),
        ]

        _, report = normalizer.normalize_funding_rates(rates, "binance-btc-usdt-perp")
        assert report.suspect_bars == 1

    def test_write_normalized_funding_rates(self, tmp_path: Path) -> None:
        """写入 Normalized 资金费率"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        rates = [
            _make_funding_rate(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_funding_rate(datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_funding_rates(rates, "binance-btc-usdt-perp")
        path = normalizer.write_normalized_funding_rates(result, "binance-btc-usdt-perp")

        assert path.exists()
        assert path.name == "2026.parquet"


# ── OpenInterest 规范化测试 ────────────────────────────────


class TestOpenInterestNormalization:
    def test_normalize_oi_sorts_by_time(self, tmp_path: Path) -> None:
        """持仓量按时间排序"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        oi_list = [
            _make_oi(datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)),
            _make_oi(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_open_interest(oi_list, "binance-btc-usdt-perp")
        assert result[0].ts < result[1].ts

    def test_normalize_empty_oi(self, tmp_path: Path) -> None:
        """空数据返回空列表"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")
        result, report = normalizer.normalize_open_interest([], "test")
        assert result == []
        assert report.total_bars == 0

    def test_detect_negative_oi(self, tmp_path: Path) -> None:
        """负 OI 值标记为 SUSPECT"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        oi_list = [
            _make_oi(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), oi_value=Decimal("-100")),
        ]

        _, report = normalizer.normalize_open_interest(oi_list, "binance-btc-usdt-perp")
        assert report.suspect_bars == 1

    def test_write_normalized_oi(self, tmp_path: Path) -> None:
        """写入 Normalized 持仓量"""
        normalizer = Normalizer(normalized_dir=tmp_path / "normalized")

        oi_list = [
            _make_oi(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_oi(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
        ]

        result, _ = normalizer.normalize_open_interest(oi_list, "binance-btc-usdt-perp")
        path = normalizer.write_normalized_open_interest(result, "binance-btc-usdt-perp")

        assert path.exists()
        assert path.name == "2026.parquet"


# ── FundingRate/OI 质量检查测试 ────────────────────────────


class TestFundingRateQualityCheck:
    def test_check_nonexistent_funding(self, tmp_path: Path) -> None:
        """不存在的数据返回缺失率 100%"""
        checker = QualityChecker(normalized_dir=tmp_path / "normalized")
        result = checker.check_funding_rates(
            "binance-btc-usdt-perp",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert result["missing_rate"] == 1.0

    def test_check_funding_with_aligned_start(self, tmp_path: Path) -> None:
        """从 Binance 结算点（00:00 UTC）开始计算预期数量"""
        normalized_dir = tmp_path / "normalized"
        normalizer = Normalizer(normalized_dir=normalized_dir)

        # 写入 3 条资金费率（00:00, 08:00, 16:00 UTC）
        rates = [
            _make_funding_rate(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_funding_rate(datetime(2026, 1, 1, 8, 0, 0, tzinfo=UTC)),
            _make_funding_rate(datetime(2026, 1, 1, 16, 0, 0, tzinfo=UTC)),
        ]
        result, _ = normalizer.normalize_funding_rates(rates, "binance-btc-usdt-perp")
        normalizer.write_normalized_funding_rates(result, "binance-btc-usdt-perp")

        checker = QualityChecker(normalized_dir=normalized_dir)
        # start 对齐到 00:00 UTC（Binance 结算点）
        result = checker.check_funding_rates(
            "binance-btc-usdt-perp",
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC),
        )
        assert result["total_expected"] == 3  # 00:00, 08:00, 16:00
        assert result["total_actual"] == 3
        assert result["missing_rate"] == 0.0


class TestOpenInterestQualityCheck:
    def test_check_nonexistent_oi(self, tmp_path: Path) -> None:
        """不存在的数据返回缺失率 100%"""
        checker = QualityChecker(normalized_dir=tmp_path / "normalized")
        result = checker.check_open_interest(
            "binance-btc-usdt-perp",
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
        assert result["missing_rate"] == 1.0

    def test_check_oi_with_period(self, tmp_path: Path) -> None:
        """使用 period 参数计算预期数量"""
        normalized_dir = tmp_path / "normalized"
        normalizer = Normalizer(normalized_dir=normalized_dir)

        # 写入 3 条 OI（每 5m）
        oi_list = [
            _make_oi(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)),
            _make_oi(datetime(2026, 1, 1, 0, 5, 0, tzinfo=UTC)),
            _make_oi(datetime(2026, 1, 1, 0, 10, 0, tzinfo=UTC)),
        ]
        result, _ = normalizer.normalize_open_interest(oi_list, "binance-btc-usdt-perp")
        normalizer.write_normalized_open_interest(result, "binance-btc-usdt-perp")

        checker = QualityChecker(normalized_dir=normalized_dir)
        result = checker.check_open_interest(
            "binance-btc-usdt-perp",
            datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 0, 15, 0, tzinfo=UTC),
            period="5m",
        )
        assert result["total_expected"] == 3  # 0:00, 0:05, 0:10
        assert result["total_actual"] == 3
        assert result["missing_rate"] == 0.0
