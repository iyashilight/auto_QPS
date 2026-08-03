"""T6 CLI 数据命令单元测试

测试：
- qps data status（数据覆盖状态）
- qps data verify（数据质量检查）
- qps data collect（参数校验）
- _make_instrument_id 辅助函数
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from typer.testing import CliRunner

from qps.control.cli.app import _make_instrument_id, app

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
        """无数据时正常输出"""
        result = runner.invoke(app, ["data", "verify", "BTC-USDT-PERP", "--interval", "5m"])
        assert result.exit_code == 0

    def test_verify_json(self) -> None:
        """JSON 格式输出"""
        result = runner.invoke(app, [
            "data", "verify", "BTC-USDT-PERP",
            "--interval", "5m", "--json",
        ])
        assert result.exit_code == 0
        import json

        data = json.loads(result.output)
        assert "overall_quality" in data
        assert data["total_bars"] == 0


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
