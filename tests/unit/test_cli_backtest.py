"""M3.2 CLI 端到端测试：backtest → list-runs → experiment show → compare → rerun → optimize

使用 Typer CliRunner + mock 数据 + 临时数据目录，真实调用 CLI 命令。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

from typer.testing import CliRunner

if TYPE_CHECKING:
    from pathlib import Path

from qps.control.cli.app import app
from qps.core.models.market import Bar

runner = CliRunner()


def _make_bar(
    open_time: datetime,
    open_price: Decimal = Decimal("50000"),
    close: Decimal = Decimal("50200"),
    high: Decimal = Decimal("50500"),
    low: Decimal = Decimal("49500"),
) -> Bar:
    return Bar(
        instrument_id="binance-btc-usdt-perp",
        interval="5m",
        open_time=open_time,
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=Decimal("100"),
        quote_volume=Decimal("100") * close,
        trades=100,
        ts=0,
        data_version="v1",
    )


def _make_bars_sequence(start: datetime, count: int) -> list[Bar]:
    """生成测试 K 线序列"""
    import random

    bars: list[Bar] = []
    rng = random.Random(42)
    price = 50000.0
    for i in range(count):
        change = rng.gauss(0, 0.02)
        open_price = Decimal(str(round(price, 2)))
        price *= 1 + change * 0.01
        close_price = Decimal(str(round(price, 2)))
        high = max(open_price, close_price) * Decimal("1.001")
        low = min(open_price, close_price) * Decimal("0.999")
        bars.append(_make_bar(
            open_time=start + __import__("datetime").timedelta(minutes=5 * i),
            open_price=open_price,
            close=close_price,
            high=high,
            low=low,
        ))
    return bars


class TestCLIBacktest:
    """CLI backtest 端到端"""

    def test_backtest_run(self, tmp_path: Path) -> None:
        """qps research backtest 成功执行"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)

        # Mock build_container 和 DataProvider
        from qps.data.provider import DataProvider
        from qps.infra.storage.meta_db import MetaDB
        from qps.research.experiments import ExperimentStore

        mock_dp = MagicMock(spec=DataProvider)
        mock_dp.get_bars = AsyncMock(return_value=bars)
        mock_dp.get_funding_rates = AsyncMock(return_value=[])
        mock_dp.get_mark_prices = AsyncMock(return_value=[])

        mock_container = MagicMock()
        mock_container.config = MagicMock()
        mock_container.config.data_dir = tmp_path
        mock_container.meta_db = MetaDB(tmp_path / "meta.db")
        mock_container.meta_db.initialize()
        mock_container.meta_db.setup_insert_only_constraints()

        store = ExperimentStore(meta_db=mock_container.meta_db, data_dir=tmp_path)

        def get_port(name: str) -> object:
            if name == "data_provider":
                return mock_dp
            if name == "experiment_store":
                return store
            raise KeyError(name)

        mock_container.get_port = MagicMock(side_effect=get_port)

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--range", "2026-01-01:2026-01-31",
                "--json",
            ])

        assert result.exit_code == 0, f"CLI 回测失败: {result.output}"
        output = json.loads(result.output)
        assert output["status"] == "SUCCEEDED"
        assert "run_id" in output

    def test_backtest_with_cost_config(self, tmp_path: Path) -> None:
        """qps research backtest --cost-config 成功"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 20)

        from qps.data.provider import DataProvider
        from qps.infra.storage.meta_db import MetaDB
        from qps.research.experiments import ExperimentStore

        mock_dp = MagicMock(spec=DataProvider)
        mock_dp.get_bars = AsyncMock(return_value=bars)
        mock_dp.get_funding_rates = AsyncMock(return_value=[])
        mock_dp.get_mark_prices = AsyncMock(return_value=[])

        mock_container = MagicMock()
        mock_container.config = MagicMock()
        mock_container.config.data_dir = tmp_path
        mock_container.meta_db = MetaDB(tmp_path / "meta.db")
        mock_container.meta_db.initialize()
        mock_container.meta_db.setup_insert_only_constraints()

        store = ExperimentStore(meta_db=mock_container.meta_db, data_dir=tmp_path)

        def get_port(name: str) -> object:
            if name == "data_provider":
                return mock_dp
            if name == "experiment_store":
                return store
            raise KeyError(name)

        mock_container.get_port = MagicMock(side_effect=get_port)

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--cost-config", '{"maker_fee": "0.05", "taker_fee": "0.06"}',
                "--range", "2026-01-01:2026-01-31",
                "--json",
            ])

        assert result.exit_code == 0, f"CLI 回测失败: {result.output}"


class TestCLIExperiment:
    """CLI experiment 端到端"""

    def test_list_runs(self, tmp_path: Path) -> None:
        """qps research list-runs 成功"""
        from qps.infra.storage.meta_db import MetaDB

        mock_container = MagicMock()
        mock_container.config = MagicMock()
        mock_container.config.data_dir = tmp_path
        mock_container.meta_db = MetaDB(tmp_path / "meta.db")
        mock_container.meta_db.initialize()
        mock_container.meta_db.setup_insert_only_constraints()

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, ["research", "list-runs", "--json"])

        assert result.exit_code == 0

    def test_experiment_show(self, tmp_path: Path) -> None:
        """qps experiment show 成功"""
        from qps.infra.storage.meta_db import MetaDB

        mock_container = MagicMock()
        mock_container.config = MagicMock()
        mock_container.config.data_dir = tmp_path
        mock_container.meta_db = MetaDB(tmp_path / "meta.db")
        mock_container.meta_db.initialize()
        mock_container.meta_db.setup_insert_only_constraints()

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, ["experiment", "show", "nonexistent_run_id"])

        # 不存在的 run_id 应返回错误
        assert result.exit_code != 0 or "失败" in result.output


class TestCLIOptimize:
    """CLI optimize 端到端"""

    def test_optimize_empty_search_space_exit_code_2(self) -> None:
        """qps research optimize 空 search_space 退出码 2"""
        result = runner.invoke(app, [
            "research", "optimize", "test_strat",
            "--search-space", "{}",
        ])
        assert result.exit_code == 2, f"空 search_space 应返回退出码 2，实际 {result.exit_code}"
        assert "搜索空间不能为空" in result.output

    def test_optimize_no_search_space_exit_code_2(self) -> None:
        """qps research optimize 不提供 search_space 退出码 2"""
        result = runner.invoke(app, [
            "research", "optimize", "test_strat",
        ])
        assert result.exit_code == 2, f"无 search_space 应返回退出码 2，实际 {result.exit_code}"
