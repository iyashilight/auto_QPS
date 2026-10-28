"""M3.3 CLI 端到端测试：backtest → list-runs → experiment show/compare/rerun → optimize

使用 Typer CliRunner + mock 数据 + 临时数据目录，真实调用 CLI 命令。
覆盖成功路径和错误路径。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
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


def _build_cli_container(tmp_path: Path, bars: list[Bar] | None = None) -> MagicMock:
    """构建 CLI 测试用 mock 容器"""
    from qps.data.provider import DataProvider
    from qps.infra.storage.meta_db import MetaDB
    from qps.research.experiments import ExperimentStore

    mock_dp = MagicMock(spec=DataProvider)
    mock_dp.get_bars = AsyncMock(return_value=bars or [])
    mock_dp.get_funding_rates = AsyncMock(return_value=[])
    mock_dp.get_mark_prices = AsyncMock(return_value=[])
    mock_dp.get_features = AsyncMock(return_value=[])

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
        if name == "strategy_registry":
            raise KeyError("strategy_registry 未注册")
        raise KeyError(name)

    mock_container.get_port = MagicMock(side_effect=get_port)

    return mock_container


class TestCLIBacktest:
    """CLI backtest 端到端"""

    def test_backtest_requires_manifest_and_utc_decision_time(self) -> None:
        missing = runner.invoke(app, ["research", "backtest", "test_strat"])
        assert missing.exit_code == 2
        assert "manifest-id" in missing.output

        non_utc = runner.invoke(app, [
            "research", "backtest", "test_strat",
            "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T08:00:00+08:00",
        ])
        assert non_utc.exit_code == 2
        assert "UTC" in non_utc.output

    @pytest.mark.parametrize(
        "range_value",
        [
            "2026-01-01T00:00:00:2026-01-02T00:00:00Z",
            "2026-01-01T08:00:00+08:00:2026-01-02T00:00:00Z",
            "2026-01-02T00:00:00Z:2026-01-01T00:00:00Z",
        ],
    )
    def test_backtest_range_rejects_invalid_utc_before_container(self, range_value: str) -> None:
        with patch("qps.di.build_container") as build_container:
            result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00Z",
                "--range", range_value,
            ])
        assert result.exit_code == 2
        assert "range" in result.output
        build_container.assert_not_called()

    def test_backtest_run(self, tmp_path: Path) -> None:
        """qps research backtest 成功执行"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)
        mock_container = _build_cli_container(tmp_path, bars=bars)

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
                "--json",
            ])

        assert result.exit_code == 0, f"CLI 回测失败: {result.output}"
        output = json.loads(result.output)
        assert output["status"] == "SUCCEEDED"
        assert "run_id" in output

    def test_backtest_with_cost_config(self, tmp_path: Path) -> None:
        """qps research backtest --cost-config 成功"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 20)
        mock_container = _build_cli_container(tmp_path, bars=bars)

        with patch("qps.di.build_container", return_value=mock_container):
            result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--cost-config", '{"maker_fee": "0.05", "taker_fee": "0.06"}',
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
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

    def test_experiment_show_nonexistent(self, tmp_path: Path) -> None:
        """qps experiment show 不存在的 run_id 应返回错误"""
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
        assert result.exit_code != 0 or "失败" in result.output or "不存在" in result.output

    def test_experiment_show_success(self, tmp_path: Path) -> None:
        """qps experiment show 成功路径：先回测创建 run → show 该 run_id"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)
        mock_container = _build_cli_container(tmp_path, bars=bars)

        # 先执行回测创建 run
        with patch("qps.di.build_container", return_value=mock_container):
            bt_result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
                "--json",
            ])
        assert bt_result.exit_code == 0, f"回测失败: {bt_result.output}"
        bt_output = json.loads(bt_result.output)
        run_id = bt_output["run_id"]

        # 再 show 该 run_id
        with patch("qps.di.build_container", return_value=mock_container):
            show_result = runner.invoke(app, [
                "experiment", "show", run_id, "--json",
            ])

        assert show_result.exit_code == 0, f"show 失败: {show_result.output}"
        show_output = json.loads(show_result.output)
        assert show_output["run_id"] == run_id
        assert show_output["status"] == "SUCCEEDED"

    def test_experiment_compare_success(self, tmp_path: Path) -> None:
        """qps experiment compare 成功路径：两次回测 → compare 两个 run_id（含 OOS 列）"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 50)
        mock_container = _build_cli_container(tmp_path, bars=bars)

        # 第一次回测
        with patch("qps.di.build_container", return_value=mock_container):
            bt1_result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
                "--json",
            ])
        assert bt1_result.exit_code == 0, f"回测1失败: {bt1_result.output}"
        run_id_1 = json.loads(bt1_result.output)["run_id"]

        # 第二次回测（不同参数）
        with patch("qps.di.build_container", return_value=mock_container):
            bt2_result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--params", '{"entry_z": 1.5, "position_size": "0.2"}',
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
                "--json",
            ])
        assert bt2_result.exit_code == 0, f"回测2失败: {bt2_result.output}"
        run_id_2 = json.loads(bt2_result.output)["run_id"]

        # compare 两个 run
        with patch("qps.di.build_container", return_value=mock_container):
            compare_result = runner.invoke(app, [
                "experiment", "compare", run_id_1, run_id_2, "--json",
            ])

        assert compare_result.exit_code == 0, f"compare 失败: {compare_result.output}"
        compare_output = json.loads(compare_result.output)
        assert run_id_1 in compare_output["run_ids"]
        assert run_id_2 in compare_output["run_ids"]
        # 含 OOS 列
        oos_metrics = [m for m in compare_output["metric_names"] if m.startswith("oos_")]
        assert len(oos_metrics) > 0, f"compare 应含 OOS 指标列，实际 {compare_output['metric_names']}"

    def test_experiment_rerun_success(self, tmp_path: Path) -> None:
        """qps experiment rerun 成功路径：先回测 → rerun 该 run_id"""
        bars = _make_bars_sequence(datetime(2026, 1, 1, tzinfo=UTC), 30)
        mock_container = _build_cli_container(tmp_path, bars=bars)

        # 先执行回测创建 run
        with patch("qps.di.build_container", return_value=mock_container):
            bt_result = runner.invoke(app, [
                "research", "backtest", "test_strat",
                "--manifest-id", "manifest-test", "--decision-time", "2026-01-01T00:00:00+00:00",
                "--range", "2026-01-01T00:00:00Z:2026-01-31T00:00:00Z",
                "--json",
            ])
        assert bt_result.exit_code == 0, f"回测失败: {bt_result.output}"
        bt_output = json.loads(bt_result.output)
        run_id = bt_output["run_id"]

        # rerun 该 run_id
        with patch("qps.di.build_container", return_value=mock_container):
            rerun_result = runner.invoke(app, [
                "experiment", "rerun", run_id, "--json",
            ])

        assert rerun_result.exit_code == 0, f"rerun 失败: {rerun_result.output}"
        rerun_output = json.loads(rerun_result.output)
        assert rerun_output["status"] == "SUCCEEDED"
        assert rerun_output["run_id"] != run_id, "rerun 应产生新 run_id"


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

    def test_optimize_success(self, tmp_path: Path) -> None:
        """qps research optimize 成功路径：budget=10（%10==0 边界）全链路完成（M3.4 P0-3 回归）"""
        import uuid as uuid_lib

        from qps.core.constants import ExperimentStatus as ExpStatus
        from qps.core.models.experiment import BacktestRun, DataRange, Metrics
        from qps.infra.storage.meta_db import MetaDB
        from qps.research.backtest.event_driven import EventDrivenBacktestEngine
        from qps.research.experiments import ExperimentStore

        db = MetaDB(tmp_path / "meta.db")
        db.initialize()
        db.setup_insert_only_constraints()
        store = ExperimentStore(meta_db=db, data_dir=tmp_path)

        container = MagicMock()
        container.config = MagicMock()
        container.config.data_dir = tmp_path

        engine = EventDrivenBacktestEngine(container)

        async def run(req):
            metrics = Metrics(
                total_return=Decimal("0.1"), annualized_return=Decimal("0.2"),
                max_drawdown=Decimal("0.1"), sharpe=Decimal("1.5"), win_rate=Decimal("0.6"),
                volatility=Decimal("0.2"), trade_count=10, profit_factor=Decimal("2"),
            )
            return BacktestRun(
                run_id=f"bt_mock_{uuid_lib.uuid4().hex[:8]}", strategy_id="s", version_no=1,
                params={}, data_range=DataRange(
                    start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 2, 1, tzinfo=UTC),
                ),
                data_version="v1", feature_version="v1", engine_config={},
                metrics=metrics, status=ExpStatus.SUCCEEDED,
                created_at=datetime.now(UTC),
            )
        engine.run = run  # type: ignore[method-assign]

        def get_port(name: str) -> object:
            if name == "experiment_store":
                return store
            if name == "backtest_engine":
                return engine
            raise KeyError(name)

        container.get_port = MagicMock(side_effect=get_port)

        with patch("qps.di.build_container", return_value=container):
            result = runner.invoke(app, [
                "research", "optimize", "test_strat",
                "--algorithm", "grid",
                "--budget", "10",
                "--search-space", '{"entry_z": {"low": 1.0, "high": 2.0, "step": 0.1}}',
                "--range", "2026-01-01:2026-07-01",
                "--seed", "42",
            ])

        assert result.exit_code == 0, f"optimize 应成功: {result.output}\n{result.exception}"
        assert "优化完成" in result.output
        # DB 最终 status = SUCCEEDED
        with db.connect() as conn:
            rows = conn.execute(
                "SELECT status FROM experiments WHERE run_type='OPTIMIZATION'",
            ).fetchall()
        assert rows and rows[-1]["status"] == "SUCCEEDED", f"DB status 应为 SUCCEEDED，实际 {rows}"
