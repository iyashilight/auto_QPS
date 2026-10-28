"""实验记录存储（见 docs/03 §12）

实现 ExperimentStorePort：create_run/get_run/list_runs/compare 落 meta.db。
experiments/report_cards 表 insert-only（R4）。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import ExperimentStatus
from qps.core.errors import DataError
from qps.core.models.experiment import (
    BacktestRun,
    ComparisonReport,
    DataRange,
    Metrics,
    OptimizationRun,
    ReportCard,
    RunSummary,
)

if TYPE_CHECKING:
    from pathlib import Path

    from qps.core.models.foundation import TrialEvent, TrialRegistration
    from qps.infra.storage.meta_db import MetaDB
    from qps.infra.storage.research_db import ResearchStore

logger = logging.getLogger(__name__)


class ExperimentStore:
    """实验存储实现（meta.db）"""

    def __init__(self, meta_db: MetaDB, data_dir: Path) -> None:
        self._meta_db = meta_db
        self._data_dir = data_dir

    async def create_run(self, run: BacktestRun | OptimizationRun) -> None:
        """创建实验记录（insert-only）"""
        # 提取 start/end 时间戳
        start_ts: str | None = None
        end_ts: str | None = None

        if isinstance(run, BacktestRun):
            run_type = "BACKTEST"
            params = json.dumps(run.params, default=_json_default)
            engine_config = json.dumps(run.engine_config, default=_json_default)
            env_lock = run.environment_lock
            start_ts = run.data_range.start.isoformat()
            end_ts = run.data_range.end.isoformat()
        else:
            run_type = "OPTIMIZATION"
            params = json.dumps({
                "search_space": run.search_space,
                "objective": run.objective,
                "algorithm": run.algorithm.value,
                "best_params": run.best_params,
                "evaluations": run.evaluations,
                "walkforward_report": run.walkforward_report,
            }, default=_json_default)
            engine_config = json.dumps({
                "algorithm": run.algorithm.value,
                "walkforward_report": run.walkforward_report,
            }, default=_json_default)
            env_lock = None
            # 优化器从 engine_config 或 search_space 提取时间范围
            if hasattr(run, "start") and run.start is not None:
                start_ts = run.start.isoformat() if hasattr(run.start, "isoformat") else str(run.start)
            if hasattr(run, "end") and run.end is not None:
                end_ts = run.end.isoformat() if hasattr(run.end, "isoformat") else str(run.end)

        with self._meta_db.transaction() as conn:
            conn.execute(
                """INSERT INTO experiments
                   (run_id, run_type, strategy_id, version_no, params,
                    data_version, feature_version, engine_config,
                    environment_lock, start_ts, end_ts, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    run.run_id,
                    run_type,
                    run.strategy_id,
                    run.version_no,
                    params,
                    run.data_version,
                    run.feature_version,
                    engine_config,
                    env_lock,
                    start_ts,
                    end_ts,
                    run.status.value,
                    run.created_at.isoformat(),
                ),
            )

    async def append(self, run: BacktestRun | OptimizationRun) -> None:
        """Append a run through the historical ExperimentStore contract."""
        await self.create_run(run)

    async def get_run(self, run_id: str) -> BacktestRun | OptimizationRun:
        """获取实验记录"""
        with self._meta_db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM experiments WHERE run_id = ?",
                (run_id,),
            ).fetchone()

        if row is None:
            raise DataError(detail=f"实验记录不存在: {run_id}")

        run_type = row["run_type"]
        params = json.loads(row["params"])
        engine_config = json.loads(row["engine_config"])
        created_at = datetime.fromisoformat(row["created_at"])

        # 从 start_ts/end_ts 恢复 data_range
        start_ts = row["start_ts"]
        end_ts = row["end_ts"]
        data_range = DataRange(
            start=datetime.fromisoformat(start_ts) if start_ts else datetime(2026, 1, 1, tzinfo=UTC),
            end=datetime.fromisoformat(end_ts) if end_ts else datetime(2026, 1, 2, tzinfo=UTC),
        )

        if run_type == "BACKTEST":
            # 从 report_cards 恢复 metrics（BacktestRun.metrics）
            metrics = None
            with self._meta_db.connect() as conn:
                card_row = conn.execute(
                    "SELECT metrics FROM report_cards WHERE run_id = ?",
                    (run_id,),
                ).fetchone()
            if card_row is not None and card_row["metrics"]:
                try:
                    metrics = Metrics.model_validate(json.loads(card_row["metrics"]))
                except Exception:
                    logger.exception("恢复 metrics 失败: %s", run_id)
            # trades_ref/equity_curve_ref 从 engine_config 恢复
            trades_ref = engine_config.get("trades_ref")
            equity_curve_ref = engine_config.get("equity_curve_ref")
            return BacktestRun(
                run_id=row["run_id"],
                strategy_id=row["strategy_id"],
                version_no=row["version_no"],
                params=params,
                data_range=data_range,
                data_version=row["data_version"],
                feature_version=row["feature_version"],
                engine_config=engine_config,
                environment_lock=row["environment_lock"],
                trades_ref=trades_ref,
                equity_curve_ref=equity_curve_ref,
                metrics=metrics,
                status=ExperimentStatus(row["status"]),
                created_at=created_at,
            )

        # OptimizationRun：从 params + experiment_events 恢复完整字段
        # 从 experiment_events 读取增量 evaluations
        with self._meta_db.connect() as conn:
            event_rows = conn.execute(
                "SELECT event_data FROM experiment_events WHERE run_id = ? AND event_type = 'EVALUATION' ORDER BY created_at, rowid",
                (run_id,),
            ).fetchall()
        evaluations = [json.loads(r["event_data"]) for r in event_rows] if event_rows else params.get("evaluations", [])

        # 从 experiment_events 读取最新的 best_params
        with self._meta_db.connect() as conn:
            best_row = conn.execute(
                "SELECT event_data FROM experiment_events WHERE run_id = ? AND event_type = 'BEST_UPDATE' ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if best_row:
            best_data = json.loads(best_row["event_data"])
            best_params = best_data.get("best_params", params.get("best_params"))
        else:
            best_params = params.get("best_params")

        return OptimizationRun(
            run_id=row["run_id"],
            strategy_id=row["strategy_id"],
            version_no=row["version_no"],
            search_space=params.get("search_space", {}),
            objective=params.get("objective", {}),
            algorithm=params.get("algorithm", "GRID"),
            evaluations=evaluations,
            best_params=best_params,
            walkforward_report=params.get("walkforward_report"),
            data_version=row["data_version"],
            feature_version=row["feature_version"],
            status=ExperimentStatus(row["status"]),
            created_at=created_at,
        )

    async def list_runs(
        self,
        strategy_id: str | None = None,
        run_type: str | None = None,
    ) -> list[RunSummary]:
        """列出实验记录"""
        query = "SELECT * FROM experiments"
        conditions: list[str] = []
        sql_params: list[str] = []

        if strategy_id:
            conditions.append("strategy_id = ?")
            sql_params.append(strategy_id)
        if run_type:
            conditions.append("run_type = ?")
            sql_params.append(run_type)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY created_at DESC"

        with self._meta_db.connect() as conn:
            rows = conn.execute(query, sql_params).fetchall()

        summaries: list[RunSummary] = []
        for row in rows:
            created_at = datetime.fromisoformat(row["created_at"])
            summaries.append(RunSummary(
                run_id=row["run_id"],
                run_type=row["run_type"],
                strategy_id=row["strategy_id"],
                version_no=row["version_no"],
                status=ExperimentStatus(row["status"]),
                created_at=created_at,
            ))
        return summaries

    async def compare(self, run_ids: list[str]) -> ComparisonReport:
        """比较多个实验运行（含 OOS 列）"""
        rows_data: list[dict[str, Any]] = []
        metric_names_set: set[str] = set()

        for run_id in run_ids:
            with self._meta_db.connect() as conn:
                row = conn.execute(
                    "SELECT * FROM report_cards WHERE run_id = ?",
                    (run_id,),
                ).fetchone()

            if row is None:
                continue

            metrics = json.loads(row["metrics"])
            run_data: dict[str, Any] = {"run_id": run_id}

            for key, value in metrics.items():
                if key == "oos_metrics":
                    oos = value if isinstance(value, dict) else {}
                    for oos_key, oos_val in oos.items():
                        flat_key = f"oos_{oos_key}"
                        run_data[flat_key] = oos_val
                        metric_names_set.add(flat_key)
                elif key == "cost_breakdown":
                    cost = value if isinstance(value, dict) else {}
                    for cost_key, cost_val in cost.items():
                        flat_key = f"cost_{cost_key}"
                        run_data[flat_key] = cost_val
                        metric_names_set.add(flat_key)
                else:
                    run_data[key] = value
                    metric_names_set.add(key)

            rows_data.append(run_data)

        core_metrics = [
            "total_return", "annualized_return", "max_drawdown",
            "sharpe", "win_rate", "volatility", "trade_count",
            "profit_factor", "funding_pnl", "liquidation_count",
        ]
        oos_metrics = sorted(k for k in metric_names_set if k.startswith("oos_"))
        cost_metrics = sorted(k for k in metric_names_set if k.startswith("cost_"))
        metric_names = core_metrics + oos_metrics + cost_metrics

        return ComparisonReport(
            run_ids=run_ids,
            metric_names=metric_names,
            rows=rows_data,
        )

    async def save_report_card(self, report_card: ReportCard) -> None:
        """保存报告卡到 report_cards 表，不允许原地替换历史结果。"""
        metrics_json = json.dumps(
            report_card.metrics.model_dump() if report_card.metrics else {},
            default=_json_default,
        )
        gate_results_json = json.dumps(
            [g.model_dump() for g in report_card.gate_results],
            default=_json_default,
        )
        alpha_score_json = (
            json.dumps(report_card.alpha_score.model_dump(), default=_json_default)
            if report_card.alpha_score
            else None
        )

        with self._meta_db.transaction() as conn:
            existing = conn.execute(
                "SELECT strategy_id, version_no, metrics, gate_results, alpha_score, conclusion "
                "FROM report_cards WHERE run_id = ?",
                (report_card.run_id,),
            ).fetchone()
            values = (
                report_card.strategy_id,
                report_card.version_no,
                metrics_json,
                gate_results_json,
                alpha_score_json,
                report_card.conclusion,
            )
            if existing is not None:
                if tuple(existing) != values:
                    raise DataError(detail=f"report card 内容冲突，历史记录不可覆盖: {report_card.run_id}")
                return
            conn.execute(
                """INSERT INTO report_cards
                   (run_id, strategy_id, version_no, metrics, gate_results, alpha_score, conclusion)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (report_card.run_id, *values),
            )


class TrialRegistry:
    """M3 trial registry facade backed by the append-only research store."""

    def __init__(self, store: ResearchStore) -> None:
        self._store = store

    def append(self, registration: TrialRegistration) -> None:
        self._store.append_registration(registration)

    def append_event(self, event: TrialEvent) -> None:
        self._store.append_trial_event(event)


def _json_default(obj: Any) -> Any:
    """JSON 序列化默认处理器"""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "value"):
        return obj.value
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")
