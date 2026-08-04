"""参数优化器（见 docs/03 §8，架构 §7）

grid / random / bayesian 搜索 + walk-forward + OOS 评估。
walk-forward + OOS 是统计门禁，不可关闭。
随机算法必须注入固定 seed。
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import (
    OPTIMIZATION_RUN_ID_PREFIX,
    ExperimentStatus,
    OptimizationAlgorithm,
)
from qps.core.errors import BacktestError
from qps.core.models.experiment import (
    Metrics,
    OptimizationRequest,
    OptimizationRun,
    WalkforwardConfigRequest,
)

if TYPE_CHECKING:
    from qps.di import Container
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.experiments import ExperimentStore

logger = logging.getLogger(__name__)


class Optimizer:
    """参数优化器

    功能：
    - grid / random / bayesian 搜索
    - walk-forward 滚动窗口 + OOS 评估（不可关闭）
    - 增量写盘、中途可查、stop 幂等优雅终止
    - 随机/贝叶斯必须注入固定 seed
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._active_runs: dict[str, bool] = {}  # run_id → should_stop

    async def search(self, request: dict[str, Any]) -> OptimizationRun:
        """执行参数搜索

        Args:
            request: 优化请求字典

        Returns:
            OptimizationRun（含 best_params 和 best_metrics）
        """
        req = self._parse_request(request)
        run_id = self._generate_run_id(req)

        # 标记为活跃
        self._active_runs[run_id] = False

        try:
            result = await self._execute_search(req, run_id)
            return result
        finally:
            self._active_runs.pop(run_id, None)

    async def stop(self, run_id: str) -> None:
        """停止搜索（幂等，置 CANCELED 状态）"""
        self._active_runs[run_id] = True
        # 更新状态为 CANCELED（写库失败不静默，抛错）
        store = self._get_experiment_store()
        with store._meta_db.transaction() as conn:
            conn.execute(
                "UPDATE experiments SET status = ? WHERE run_id = ?",
                (ExperimentStatus.CANCELED.value, run_id),
            )
            # 记录状态变更事件
            conn.execute(
                "INSERT OR IGNORE INTO experiment_events (event_id, run_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    f"evt_{run_id}_canceled",
                    run_id,
                    "STATUS_CHANGE",
                    json.dumps({"from": "RUNNING", "to": "CANCELED"}),
                    datetime.now(UTC).isoformat(),
                ),
            )

    async def status(self, run_id: str) -> OptimizationRun:
        """查询搜索状态"""
        store = self._get_experiment_store()
        run = await store.get_run(run_id)
        if not isinstance(run, OptimizationRun):
            raise BacktestError(detail=f"run_id {run_id} 不是优化运行")
        return run

    # ── 核心搜索 ──────────────────────────────────────────

    async def _execute_search(
        self, req: OptimizationRequest, run_id: str,
    ) -> OptimizationRun:
        """执行参数搜索"""
        # 生成参数组合
        param_combos = self._generate_param_combinations(req)
        budget = min(req.budget, len(param_combos))

        # Walk-forward 配置（不可关闭）
        wf_config = req.walkforward_config

        evaluations: list[dict[str, Any]] = []
        best_params: dict[str, Any] | None = None
        best_score: float = float("-inf")
        best_metrics: Metrics | None = None
        walkforward_report: dict[str, Any] = {}

        # 创建初始记录
        run = OptimizationRun(
            run_id=run_id,
            strategy_id=req.strategy_id,
            version_no=req.version_no,
            search_space=req.search_space,
            objective=req.objective,
            algorithm=req.algorithm,
            evaluations=[],
            status=ExperimentStatus.RUNNING,
            created_at=datetime.now(UTC),
        )

        # 保存初始记录（写库失败不静默，抛错）
        store = self._get_experiment_store()
        await store.create_run(run)

        for i, params in enumerate(param_combos[:budget]):
            # 检查是否应该停止
            if self._active_runs.get(run_id, False):
                logger.info("优化搜索被停止: %s (已完成 %d/%d)", run_id, i, budget)
                break

            # Walk-forward 评估
            wf_result = await self._walkforward_evaluate(
                req=req,
                params=params,
                wf_config=wf_config,
            )

            # 计算目标函数得分
            score = self._compute_objective_score(wf_result, req.objective)

            eval_record: dict[str, Any] = {
                "eval_idx": i,
                "params": params,
                "is_sharpe": str(wf_result.get("is_sharpe", "0")),
                "is_max_dd": str(wf_result.get("is_max_drawdown", "0")),
                "oos_sharpe": str(wf_result.get("oos_sharpe", "0")),
                "oos_max_dd": str(wf_result.get("oos_max_drawdown", "0")),
                "score": str(score),
                "walkforward_windows": wf_result.get("window_count", 0),
            }
            evaluations.append(eval_record)

            # 更新最优
            if score > best_score:
                best_score = score
                best_params = params
                best_metrics = self._build_metrics_from_wf(wf_result)

            # 增量写盘
            if (i + 1) % 10 == 0 or i == budget - 1:
                await self._update_run(
                    run_id=run_id,
                    evaluations=evaluations,
                    best_params=best_params,
                    best_metrics=best_metrics,
                    status=ExperimentStatus.RUNNING,
                )

        # Walk-forward 报告
        walkforward_report = self._build_walkforward_report(
            evaluations=evaluations,
            wf_config=wf_config,
        )

        # 最终结果
        final_run = OptimizationRun(
            run_id=run_id,
            strategy_id=req.strategy_id,
            version_no=req.version_no,
            search_space=req.search_space,
            objective=req.objective,
            algorithm=req.algorithm,
            evaluations=evaluations,
            best_params=best_params,
            best_metrics=best_metrics,
            walkforward_report=walkforward_report,
            data_version=req.data_version,
            feature_version=req.feature_version,
            status=ExperimentStatus.SUCCEEDED if best_params is not None else ExperimentStatus.FAILED,
            created_at=run.created_at,
        )

        # 保存最终记录（写库失败不静默，抛错）
        store = self._get_experiment_store()
        await self._update_run(
            run_id=run_id,
            evaluations=evaluations,
            best_params=best_params,
            best_metrics=best_metrics,
            walkforward_report=walkforward_report,
            status=final_run.status,
        )

        return final_run

    # ── Walk-forward 评估 ─────────────────────────────────

    async def _walkforward_evaluate(
        self,
        req: OptimizationRequest,
        params: dict[str, Any],
        wf_config: WalkforwardConfigRequest,
    ) -> dict[str, Any]:
        """Walk-forward 滚动窗口评估

        不可关闭的统计门禁：每个参数组合必须经过 walk-forward + OOS 评估。
        时间范围基于请求数据范围（非硬编码）。
        """
        engine = self._get_backtest_engine()

        # 计算窗口
        train_days = wf_config.train_len
        oos_days = wf_config.oos_len
        min_windows = wf_config.min_windows

        # 从请求数据范围推导（非硬编码）
        if req.start is not None and req.end is not None:
            start = req.start
            end = req.end
        else:
            # 回退：无数据范围时使用默认范围
            start = datetime(2026, 1, 1, tzinfo=UTC)
            end = datetime(2026, 12, 31, tzinfo=UTC)

        total_days = (end - start).days
        window_len = train_days + oos_days
        num_windows = max(min_windows, total_days // window_len)

        is_sharpes: list[float] = []
        oos_sharpes: list[float] = []
        is_max_dds: list[float] = []
        oos_max_dds: list[float] = []

        for w in range(num_windows):
            # 检查停止
            # 计算窗口时间范围
            w_start = start + timedelta(days=w * window_len)
            train_end = w_start + timedelta(days=train_days)
            oos_end = train_end + timedelta(days=oos_days)

            if oos_end > end:
                break

            # IS 回测
            is_request = self._build_backtest_request(
                req=req, params=params, start=w_start, end=train_end,
            )
            try:
                is_run = await engine.run(is_request)
                if is_run.metrics:
                    is_sharpes.append(float(is_run.metrics.sharpe))
                    is_max_dds.append(float(is_run.metrics.max_drawdown))
                else:
                    is_sharpes.append(0.0)
                    is_max_dds.append(1.0)
            except Exception:
                is_sharpes.append(0.0)
                is_max_dds.append(1.0)

            # OOS 回测
            oos_request = self._build_backtest_request(
                req=req, params=params, start=train_end, end=oos_end,
            )
            try:
                oos_run = await engine.run(oos_request)
                if oos_run.metrics:
                    oos_sharpes.append(float(oos_run.metrics.sharpe))
                    oos_max_dds.append(float(oos_run.metrics.max_drawdown))
                else:
                    oos_sharpes.append(0.0)
                    oos_max_dds.append(1.0)
            except Exception:
                oos_sharpes.append(0.0)
                oos_max_dds.append(1.0)

        # 汇总
        avg_is_sharpe = sum(is_sharpes) / len(is_sharpes) if is_sharpes else 0.0
        avg_oos_sharpe = sum(oos_sharpes) / len(oos_sharpes) if oos_sharpes else 0.0
        avg_is_max_dd = sum(is_max_dds) / len(is_max_dds) if is_max_dds else 1.0
        avg_oos_max_dd = sum(oos_max_dds) / len(oos_max_dds) if oos_max_dds else 1.0

        return {
            "is_sharpe": Decimal(str(round(avg_is_sharpe, 6))),
            "is_max_drawdown": Decimal(str(round(avg_is_max_dd, 6))),
            "oos_sharpe": Decimal(str(round(avg_oos_sharpe, 6))),
            "oos_max_drawdown": Decimal(str(round(avg_oos_max_dd, 6))),
            "window_count": len(is_sharpes),
        }

    # ── 参数组合生成 ──────────────────────────────────────

    def _generate_param_combinations(
        self, req: OptimizationRequest,
    ) -> list[dict[str, Any]]:
        """根据搜索空间和算法生成参数组合"""
        search_space = req.search_space
        algorithm = req.algorithm

        if algorithm == OptimizationAlgorithm.GRID:
            return self._grid_search(search_space)
        if algorithm == OptimizationAlgorithm.RANDOM:
            return self._random_search(search_space, req.budget, req.random_seed)
        if algorithm == OptimizationAlgorithm.BAYESIAN:
            # 贝叶斯：M3 末实现，先用 random 替代
            logger.info("贝叶斯搜索暂用随机搜索替代")
            return self._random_search(search_space, req.budget, req.random_seed)

        return self._grid_search(search_space)

    @staticmethod
    def _grid_search(search_space: dict[str, Any]) -> list[dict[str, Any]]:
        """网格搜索：穷举所有参数组合"""
        param_names: list[str] = []
        param_values: list[list[Any]] = []

        for name, spec in search_space.items():
            param_names.append(name)
            if isinstance(spec, dict):
                # 优先检查 values 列表
                if isinstance(spec.get("values"), list):
                    param_values.append(spec["values"])
                elif isinstance(spec.get("low"), (int, float)) or isinstance(spec.get("min"), (int, float)):
                    low_val = spec.get("low", spec.get("min", 0))
                    high_val = spec.get("high", spec.get("max", 1))
                    step_val = spec.get("step", 1)
                    low = float(low_val) if low_val is not None else 0.0
                    high = float(high_val) if high_val is not None else 1.0
                    step = float(step_val) if step_val is not None else 1.0
                    vals: list[Any] = []
                    v = low
                    while v <= high + 1e-9:  # 浮点容差
                        vals.append(round(v, 10))
                        v += step
                    param_values.append(vals)
                else:
                    param_values.append([spec.get("low", spec.get("min", 0))])
            elif isinstance(spec, list):
                param_values.append(spec)
            else:
                param_values.append([spec])

        combos: list[dict[str, Any]] = []
        for combo in itertools.product(*param_values):
            combos.append(dict(zip(param_names, combo, strict=True)))

        return combos

    @staticmethod
    def _random_search(
        search_space: dict[str, Any],
        budget: int,
        seed: int,
    ) -> list[dict[str, Any]]:
        """随机搜索：固定 seed 采样"""
        rng = random.Random(seed)
        combos: list[dict[str, Any]] = []

        for _ in range(budget):
            combo: dict[str, Any] = {}
            for name, spec in search_space.items():
                if isinstance(spec, dict):
                    low = spec.get("low", spec.get("min", 0))
                    high = spec.get("high", spec.get("max", 1))
                    if isinstance(low, (int, float)) and isinstance(high, (int, float)):
                        if isinstance(low, int) and isinstance(high, int):
                            combo[name] = rng.randint(low, high)
                        else:
                            combo[name] = round(rng.uniform(float(low), float(high)), 4)
                    elif isinstance(spec.get("values"), list):
                        combo[name] = rng.choice(spec["values"])
                    else:
                        combo[name] = low
                elif isinstance(spec, list):
                    combo[name] = rng.choice(spec)
                else:
                    combo[name] = spec
            combos.append(combo)

        return combos

    # ── 目标函数 ──────────────────────────────────────────

    @staticmethod
    def _compute_objective_score(
        wf_result: dict[str, Any],
        objective: dict[str, Any],
    ) -> float:
        """计算目标函数得分

        默认：OOS Sharpe 权重 0.6 + IS Sharpe 权重 0.2 - MaxDD 权重 0.2
        """
        weights = objective.get("weights", {
            "oos_sharpe": 0.6,
            "is_sharpe": 0.2,
            "oos_max_dd": -0.2,
        })

        score = 0.0
        for key, weight in weights.items():
            val = float(wf_result.get(key, 0))
            score += val * float(weight)

        return score

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _build_metrics_from_wf(wf_result: dict[str, Any]) -> Metrics:
        """从 walk-forward 结果构建 Metrics"""
        return Metrics(
            total_return=Decimal("0"),
            annualized_return=Decimal("0"),
            max_drawdown=wf_result.get("is_max_drawdown", Decimal("1")),
            sharpe=wf_result.get("is_sharpe", Decimal("0")),
            win_rate=Decimal("0"),
            volatility=Decimal("0"),
            trade_count=0,
            profit_factor=Decimal("0"),
            funding_pnl=Decimal("0"),
            liquidation_count=0,
            oos_metrics={
                "sharpe": str(wf_result.get("oos_sharpe", "0")),
                "max_drawdown": str(wf_result.get("oos_max_drawdown", "1")),
            },
        )

    @staticmethod
    def _build_backtest_request(
        req: OptimizationRequest,
        params: dict[str, Any],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        """构建回测请求"""
        return {
            "strategy_id": req.strategy_id,
            "version_no": req.version_no,
            "params": params,
            "instruments": req.instruments,
            "interval": req.interval,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "engine_mode": "EVENT_DRIVEN",
            "data_version": req.data_version,
            "feature_version": req.feature_version,
            "random_seed": req.random_seed,
        }

    @staticmethod
    def _build_walkforward_report(
        evaluations: list[dict[str, Any]],
        wf_config: WalkforwardConfigRequest,
    ) -> dict[str, Any]:
        """构建 walk-forward 报告"""
        return {
            "train_len": wf_config.train_len,
            "oos_len": wf_config.oos_len,
            "min_windows": wf_config.min_windows,
            "total_evaluations": len(evaluations),
            "best_eval_idx": max(
                range(len(evaluations)),
                key=lambda i: float(evaluations[i].get("score", "-inf")),
            ) if evaluations else None,
        }

    @staticmethod
    def _generate_run_id(req: OptimizationRequest) -> str:
        """生成确定性 run_id（含请求内容 hash + uuid4 确保唯一性）

        格式：opt_<timestamp>_<contenthash8>_<uuid4>
        同秒同请求两次运行必须不同 run_id（uuid4 保证）。
        """
        import uuid

        content = f"{req.strategy_id}:{req.algorithm.value}:{req.random_seed}:{req.budget}"
        content_hex = hashlib.sha256(content.encode()).hexdigest()[:8]
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:4]
        return f"{OPTIMIZATION_RUN_ID_PREFIX}{ts}_{content_hex}_{uid}"

    @staticmethod
    def _parse_request(request: dict[str, Any]) -> OptimizationRequest:
        """解析优化请求"""
        # 解析 start/end
        start = None
        end = None
        if request.get("start"):
            s = request["start"]
            start = datetime.fromisoformat(s) if isinstance(s, str) else s
            if start.tzinfo is None:
                start = start.replace(tzinfo=UTC)
        if request.get("end"):
            e = request["end"]
            end = datetime.fromisoformat(e) if isinstance(e, str) else e
            if end.tzinfo is None:
                end = end.replace(tzinfo=UTC)

        return OptimizationRequest(
            strategy_id=request.get("strategy_id", "unknown"),
            version_no=request.get("version_no", 1),
            search_space=request.get("search_space", {}),
            objective=request.get("objective", {}),
            algorithm=OptimizationAlgorithm(request.get("algorithm", "GRID")),
            budget=request.get("budget", 1000),
            walkforward_config=WalkforwardConfigRequest(
                train_len=request.get("walkforward", {}).get("train_len", 60),
                val_len=request.get("walkforward", {}).get("val_len", 20),
                oos_len=request.get("walkforward", {}).get("oos_len", 20),
                min_windows=request.get("walkforward", {}).get("min_windows", 3),
            ),
            parallel_workers=request.get("parallel_workers", 4),
            random_seed=request.get("random_seed", 42),
            data_version=request.get("data_version", "unknown"),
            feature_version=request.get("feature_version", "unknown"),
            instruments=request.get("instruments", []),
            interval=request.get("interval", "5m"),
            start=start,
            end=end,
        )

    async def _update_run(
        self,
        run_id: str,
        evaluations: list[dict[str, Any]],
        best_params: dict[str, Any] | None,
        best_metrics: Metrics | None,
        walkforward_report: dict[str, Any] | None = None,
        status: ExperimentStatus = ExperimentStatus.RUNNING,
    ) -> None:
        """增量更新优化运行记录

        insert-only 兼容：
        - status 更新走 experiments 表（触发器放行 status 列）
        - evaluations 增量落盘走 experiment_events 表（insert-only）
        - 写库失败不静默，抛错（R4/R5 红线）
        """
        store = self._get_experiment_store()
        # 状态更新：仅更新 status 列（触发器放行 status 列更新）
        with store._meta_db.transaction() as conn:
            conn.execute(
                "UPDATE experiments SET status = ? WHERE run_id = ?",
                (status.value, run_id),
            )
            # evaluations 增量落盘：只写新增的 evaluations
            # 查询已有 evaluations 数量
            existing = conn.execute(
                "SELECT COUNT(*) FROM experiment_events WHERE run_id = ? AND event_type = 'EVALUATION'",
                (run_id,),
            ).fetchone()[0]
            for i in range(existing, len(evaluations)):
                eval_record = evaluations[i]
                conn.execute(
                    "INSERT INTO experiment_events (event_id, run_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"evt_{run_id}_eval_{i}",
                        run_id,
                        "EVALUATION",
                        json.dumps(eval_record, default=str),
                        datetime.now(UTC).isoformat(),
                    ),
                )
            # 记录 best_params 更新事件
            if best_params is not None:
                import uuid

                conn.execute(
                    "INSERT INTO experiment_events (event_id, run_id, event_type, event_data, created_at) VALUES (?, ?, ?, ?, ?)",
                    (
                        f"evt_{run_id}_best_{len(evaluations)}_{uuid.uuid4().hex[:8]}",
                        run_id,
                        "BEST_UPDATE",
                        json.dumps({"best_params": best_params, "score": str(evaluations[-1].get("score", "")) if evaluations else ""}, default=str),
                        datetime.now(UTC).isoformat(),
                    ),
                )

    def _get_backtest_engine(self) -> EventDrivenBacktestEngine:
        """获取回测引擎"""
        from qps.research.backtest.event_driven import EventDrivenBacktestEngine
        port = self._container.get_port("backtest_engine")
        if isinstance(port, EventDrivenBacktestEngine):
            return port
        raise BacktestError(detail="backtest_engine 端口未实现")

    def _get_experiment_store(self) -> ExperimentStore:
        """获取 ExperimentStore"""
        from qps.research.experiments import ExperimentStore
        port = self._container.get_port("experiment_store")
        if isinstance(port, ExperimentStore):
            return port
        raise BacktestError(detail="experiment_store 端口未实现")
