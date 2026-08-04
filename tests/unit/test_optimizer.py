"""M3 优化器 + 实验存储测试"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from qps.core.constants import (
    ExperimentStatus,
    OptimizationAlgorithm,
)
from qps.core.models.experiment import (
    ComparisonReport,
    OptimizationRequest,
    OptimizationRun,
    RunSummary,
    WalkforwardConfigRequest,
)
from qps.research.optimizer.engine import Optimizer

# ── 优化器参数组合生成测试 ───────────────────────────────


class TestOptimizerParamGeneration:
    """参数组合生成测试"""

    def test_grid_search(self) -> None:
        """网格搜索穷举"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={"entry_z": {"low": 1.0, "high": 3.0, "step": 1.0}},
            objective={"name": "sharpe"},
            algorithm=OptimizationAlgorithm.GRID,
            data_version="v1",
            feature_version="v1",
        )
        optimizer = Optimizer.__new__(Optimizer)
        combos = optimizer._generate_param_combinations(req)
        assert len(combos) == 3  # 1.0, 2.0, 3.0
        assert combos[0] == {"entry_z": 1.0}
        assert combos[2] == {"entry_z": 3.0}

    def test_grid_search_multi_param(self) -> None:
        """多参数网格"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={
                "entry_z": {"low": 1.0, "high": 2.0, "step": 1.0},
                "position_size": {"low": 0.1, "high": 0.2, "step": 0.1},
            },
            objective={"name": "sharpe"},
            algorithm=OptimizationAlgorithm.GRID,
            data_version="v1",
            feature_version="v1",
        )
        optimizer = Optimizer.__new__(Optimizer)
        combos = optimizer._generate_param_combinations(req)
        # 2 * 2 = 4 (浮点步长 0.1→0.2)
        assert len(combos) == 4

    def test_random_search_deterministic(self) -> None:
        """随机搜索确定性（固定 seed）"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={"entry_z": {"low": 1.0, "high": 3.0}},
            objective={"name": "sharpe"},
            algorithm=OptimizationAlgorithm.RANDOM,
            budget=10,
            random_seed=42,
            data_version="v1",
            feature_version="v1",
        )
        optimizer = Optimizer.__new__(Optimizer)
        combos1 = optimizer._generate_param_combinations(req)
        combos2 = optimizer._generate_param_combinations(req)
        # 固定 seed → 相同结果
        for c1, c2 in zip(combos1, combos2, strict=True):
            assert c1 == c2

    def test_grid_search_with_values(self) -> None:
        """网格搜索使用 values 列表"""
        req = OptimizationRequest(
            strategy_id="test",
            version_no=1,
            search_space={"entry_z": {"values": [1.0, 1.5, 2.0, 2.5]}},
            objective={"name": "sharpe"},
            algorithm=OptimizationAlgorithm.GRID,
            data_version="v1",
            feature_version="v1",
        )
        optimizer = Optimizer.__new__(Optimizer)
        combos = optimizer._generate_param_combinations(req)
        assert len(combos) == 4


# ── 优化器目标函数测试 ───────────────────────────────────


class TestOptimizerObjective:
    """目标函数测试"""

    def test_default_weights(self) -> None:
        """默认权重"""
        wf_result: dict[str, Any] = {
            "oos_sharpe": Decimal("1.5"),
            "is_sharpe": Decimal("2.0"),
            "oos_max_dd": Decimal("0.1"),
        }
        score = Optimizer._compute_objective_score(wf_result, {})
        # 默认: oos_sharpe*0.6 + is_sharpe*0.2 + oos_max_dd*(-0.2)
        expected = 1.5 * 0.6 + 2.0 * 0.2 + 0.1 * (-0.2)
        assert abs(score - expected) < 0.001

    def test_custom_weights(self) -> None:
        """自定义权重"""
        wf_result: dict[str, Any] = {
            "oos_sharpe": Decimal("1.0"),
            "is_sharpe": Decimal("1.0"),
        }
        objective = {"weights": {"oos_sharpe": 1.0}}
        score = Optimizer._compute_objective_score(wf_result, objective)
        assert abs(score - 1.0) < 0.001


# ── Walk-forward 报告测试 ────────────────────────────────


class TestWalkforwardReport:
    """Walk-forward 报告测试"""

    def test_build_report(self) -> None:
        """报告构建"""
        evaluations: list[dict[str, Any]] = [
            {"params": {"entry_z": 1.0}, "score": "0.5"},
            {"params": {"entry_z": 2.0}, "score": "1.2"},
            {"params": {"entry_z": 3.0}, "score": "0.8"},
        ]
        wf_config = WalkforwardConfigRequest()
        report = Optimizer._build_walkforward_report(evaluations, wf_config)
        assert report["train_len"] == 60
        assert report["oos_len"] == 20
        assert report["total_evaluations"] == 3
        assert report["best_eval_idx"] == 1  # score 1.2 最高


# ── OptimizationRun 模型测试 ─────────────────────────────


class TestOptimizationRun:
    """优化运行记录模型测试"""

    def test_create(self) -> None:
        """创建优化运行记录"""
        run = OptimizationRun(
            run_id="opt_001",
            strategy_id="test",
            version_no=1,
            algorithm=OptimizationAlgorithm.GRID,
            status=ExperimentStatus.PENDING,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert run.run_id == "opt_001"
        assert run.algorithm == OptimizationAlgorithm.GRID
        assert run.evaluations == []

    def test_frozen(self) -> None:
        """不可变"""
        run = OptimizationRun(
            run_id="opt_001",
            strategy_id="test",
            version_no=1,
            algorithm=OptimizationAlgorithm.GRID,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            run.status = ExperimentStatus.SUCCEEDED  # type: ignore[misc]


# ── RunSummary / ComparisonReport 模型测试 ───────────────


class TestRunSummary:
    """运行摘要模型测试"""

    def test_create(self) -> None:
        """创建运行摘要"""
        summary = RunSummary(
            run_id="bt_001",
            run_type="BACKTEST",
            strategy_id="test",
            version_no=1,
            status=ExperimentStatus.SUCCEEDED,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        assert summary.run_id == "bt_001"
        assert summary.total_return is None


class TestComparisonReport:
    """比较报告模型测试"""

    def test_create(self) -> None:
        """创建比较报告"""
        report = ComparisonReport(
            run_ids=["bt_001", "bt_002"],
            metric_names=["sharpe", "max_drawdown"],
            rows=[
                {"run_id": "bt_001", "sharpe": "1.5", "max_drawdown": "0.1"},
                {"run_id": "bt_002", "sharpe": "0.8", "max_drawdown": "0.2"},
            ],
        )
        assert len(report.run_ids) == 2
        assert len(report.rows) == 2
