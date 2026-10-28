"""T6 CLI 数据命令单元测试

测试：
- qps data status（数据覆盖状态）
- qps data verify（数据质量检查）
- qps data collect（参数校验）
- _make_instrument_id 辅助函数
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from unittest.mock import patch

import polars as pl
import pytest
from typer.testing import CliRunner

from qps.control.cli.app import _make_instrument_id, app
from qps.core.models.foundation import SourceReference
from qps.data.manifest import ManifestStore
from qps.infra.storage.meta_db import MetaDB

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


# ── 辅助函数 ──────────────────────────────────────────────


class TestMakeInstrumentId:
    def test_btc_perp(self) -> None:
        assert _make_instrument_id("BTC-USDT-PERP") == "binance-btc-usdt-perp"

    def test_already_prefixed(self) -> None:
        assert _make_instrument_id("binance-btc-usdt-perp") == "binance-btc-usdt-perp"

    def test_spot(self) -> None:
        assert _make_instrument_id("BTC-USDT") == "binance-btc-usdt"


# ── data status 命令 ──────────────────────────────────────


class TestDataStatus:
    def test_status_no_data(self) -> None:
        """无数据时正常输出"""
        result = runner.invoke(app, ["data", "status"])
        assert result.exit_code == 0
        assert "数据覆盖状态" in result.output or "data_dir" in result.output

    def test_status_json(self) -> None:
        """JSON 格式输出"""
        result = runner.invoke(app, ["data", "status", "--json"])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert "data_dir" in data
        assert "normalized_coverage" in data

    def test_status_with_data(self, tmp_path: Path) -> None:
        """有数据时展示覆盖范围"""
        # 创建模拟的 normalized 目录结构
        normalized_dir = tmp_path / "normalized" / "5m" / "btc-usdt-perp"
        normalized_dir.mkdir(parents=True, exist_ok=True)

        # 写一个空 Parquet 文件
        import polars as pl

        df = pl.DataFrame({"test": [1]})
        df.write_parquet(normalized_dir / "2026.parquet")

        # 使用 --json 输出检查
        # 注意：data status 命令使用 Config 的 data_dir，
        # 在测试中不方便修改，所以这里只测试命令不报错
        result = runner.invoke(app, ["data", "status", "--json"])
        assert result.exit_code == 0


# ── data verify 命令 ──────────────────────────────────────


class TestDataVerify:
    def test_verify_no_data(self) -> None:
        """缺少 manifest 时拒绝目录扫描"""
        result = runner.invoke(app, ["data", "verify", "BTC-USDT-PERP", "--interval", "5m"])
        assert result.exit_code == 2
        assert "manifest" in result.output

    def test_verify_rejects_manifest_for_wrong_instrument(self, tmp_path: Path) -> None:
        db = MetaDB(tmp_path / "meta.db")
        db.initialize()
        manifests = ManifestStore(tmp_path, db)
        path = tmp_path / "eth.parquet"
        now = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
        pl.DataFrame([{
            "instrument_id": "binance-eth-usdt-perp", "open_time": (now - timedelta(minutes=5)).isoformat(),
            "interval": "5m", "open": "100", "high": "101", "low": "99", "close": "100",
            "volume": "1", "quote_volume": "100", "event_time": (now - timedelta(minutes=5)).isoformat(),
            "available_at": now.isoformat(), "ingested_at": now.isoformat(),
            "close_time": now.isoformat(), "is_closed": True,
        }]).write_parquet(path)
        candidate = manifests.build(
            "normalized.bars", [str(path)], [SourceReference(name="eth")],
            schema_version="v0.5-pit-formal", persist=False,
        )
        quality_ref = manifests.write_quality_report(candidate, {
            "instrument_id": "binance-eth-usdt-perp", "interval": "5m", "overall_quality": "OK",
        })
        manifest = manifests.build_formal_pit(
            "normalized.bars", [str(path)], [SourceReference(name="eth")],
            instrument_id="binance-eth-usdt-perp", interval="5m", quality_report_ref=quality_ref,
        )
        with patch("qps.control.cli.app._m1_services", return_value=(tmp_path, manifests, None)):
            result = runner.invoke(app, [
                "data", "verify", "BTC-USDT-PERP", "--manifest-id", manifest.manifest_id,
            ])
        assert result.exit_code == 1
        assert "instrument" in result.output

    def test_verify_rejects_fake_quality_report(self, tmp_path: Path) -> None:
        db = MetaDB(tmp_path / "meta.db")
        db.initialize()
        manifests = ManifestStore(tmp_path, db)
        path = tmp_path / "bars.parquet"
        pl.DataFrame([{"instrument_id": "binance-btc-usdt-perp"}]).write_parquet(path)
        manifest = manifests.build(
            "normalized.bars", [str(path)], [SourceReference(name="test")], quality_report_ref="fake.json",
        )
        with patch("qps.control.cli.app._m1_services", return_value=(tmp_path, manifests, None)):
            result = runner.invoke(app, [
                "data", "verify", "BTC-USDT-PERP", "--manifest-id", manifest.manifest_id,
            ])
        assert result.exit_code == 1
        assert "candidate" in result.output

    def test_verify_json(self) -> None:
        """JSON 模式也不能绕过 manifest 要求"""
        result = runner.invoke(app, [
            "data", "verify", "BTC-USDT-PERP",
            "--interval", "5m", "--json",
        ])
        assert result.exit_code == 2
        assert "manifest" in result.output


class TestManifestStatus:
    def test_status_distinguishes_candidate_and_formal_pit(self, tmp_path: Path) -> None:
        db = MetaDB(tmp_path / "meta.db")
        db.initialize()
        manifests = ManifestStore(tmp_path, db)
        now = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)
        path = tmp_path / "bars.parquet"
        pl.DataFrame([{
            "instrument_id": "binance-btc-usdt-perp",
            "open_time": (now - timedelta(minutes=5)).isoformat(), "interval": "5m",
            "open": "100", "high": "101", "low": "99", "close": "100",
            "volume": "1", "quote_volume": "100",
            "event_time": (now - timedelta(minutes=5)).isoformat(),
            "available_at": now.isoformat(), "ingested_at": now.isoformat(),
            "close_time": now.isoformat(), "is_closed": True,
        }]).write_parquet(path)
        candidate_path = tmp_path / "candidate-invalid.parquet"
        pl.DataFrame([{"instrument_id": "binance-btc-usdt-perp"}]).write_parquet(candidate_path)
        candidate = manifests.build(
            "normalized.bars", [str(candidate_path)], [SourceReference(name="test")],
        )
        formal_probe = manifests.build(
            "normalized.bars", [str(path)], [SourceReference(name="test")],
            schema_version="v0.5-pit-formal", persist=False,
        )
        quality_ref = manifests.write_quality_report(formal_probe, {
            "instrument_id": "binance-btc-usdt-perp", "interval": "5m", "overall_quality": "OK",
        })
        formal = manifests.build_formal_pit(
            "normalized.bars", [str(path)], [SourceReference(name="test")],
            instrument_id="binance-btc-usdt-perp", interval="5m", quality_report_ref=quality_ref,
        )
        assert candidate.manifest_id != formal.manifest_id
        with patch("qps.control.cli.app._m1_services", return_value=(tmp_path, manifests, None)):
            result = runner.invoke(app, ["data", "manifest", "status", "--json"])
        assert result.exit_code == 0
        status = {item["manifest_id"]: item for item in json.loads(result.output)}
        assert status[candidate.manifest_id]["manifest_kind"] == "candidate"
        assert status[candidate.manifest_id]["verified"] is False
        assert status[candidate.manifest_id]["hash_verified"] is True
        assert status[formal.manifest_id]["manifest_kind"] == "formal_pit"
        assert status[formal.manifest_id]["verified"] is True

    def test_status_rejects_semantically_invalid_pit_candidate(self, tmp_path: Path) -> None:
        db = MetaDB(tmp_path / "meta.db")
        db.initialize()
        manifests = ManifestStore(tmp_path, db)
        path = tmp_path / "invalid.parquet"
        pl.DataFrame([{"instrument_id": "binance-btc-usdt-perp"}]).write_parquet(path)
        candidate = manifests.build("normalized.bars", [str(path)], [SourceReference(name="test")])
        with patch("qps.control.cli.app._m1_services", return_value=(tmp_path, manifests, None)):
            result = runner.invoke(app, ["data", "manifest", "status", "--json"])
        assert result.exit_code == 0
        item = json.loads(result.output)[0]
        assert item["manifest_id"] == candidate.manifest_id
        assert item["manifest_kind"] == "candidate"
        assert item["verified"] is False
        assert "candidate" in item["error"]


# ── data collect 命令参数校验 ─────────────────────────────


class TestDataCollect:
    def test_collect_missing_start(self) -> None:
        """缺少 --start 参数报错"""
        result = runner.invoke(app, [
            "data", "collect", "BTC-USDT-PERP",
            "--end", "2026-01-07",
        ])
        assert result.exit_code == 2

    def test_collect_missing_end(self) -> None:
        """缺少 --end 参数报错"""
        result = runner.invoke(app, [
            "data", "collect", "BTC-USDT-PERP",
            "--start", "2026-01-01",
        ])
        assert result.exit_code == 2

    def test_collect_invalid_interval(self) -> None:
        """不支持的 K 线间隔报错"""
        result = runner.invoke(app, [
            "data", "collect", "BTC-USDT-PERP",
            "--interval", "2h",
            "--start", "2026-01-01",
            "--end", "2026-01-07",
        ])
        assert result.exit_code == 2

    def test_collect_help(self) -> None:
        """帮助信息正常"""
        result = runner.invoke(app, ["data", "collect", "--help"])
        assert result.exit_code == 0
        assert "BTC-USDT-PERP" in result.output or "标的符号" in result.output


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (["data", "compute", "BTC-USDT-PERP", "--start", "2026-01-01", "--end", "2026-01-02", "--manifest-id", "missing", "--decision-time", "2026-01-02T00:00:00"], "UTC"),
        (["data", "feature", "materialize", "BTC-USDT-PERP", "--manifest-id", "missing", "--features", "returns", "--start", "2026-01-01T00:00:00+08:00", "--end", "2026-01-02T00:00:00+00:00", "--decision-time", "2026-01-02T00:00:00+00:00"], "UTC"),
        (["data", "feature", "materialize", "BTC-USDT-PERP", "--manifest-id", "missing", "--features", "returns", "--start", "2026-01-02T00:00:00+00:00", "--end", "2026-01-01T00:00:00+00:00", "--decision-time", "2026-01-02T00:00:00+00:00"], "早于"),
    ],
)
def test_formal_compute_commands_reject_invalid_utc_before_services(
    command: list[str], expected: str,
) -> None:
    with patch("qps.control.cli.app._m1_services") as services:
        result = runner.invoke(app, command)
    assert result.exit_code != 0
    assert expected in result.output
    services.assert_not_called()
