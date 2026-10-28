"""独立 EvidenceQuality / ExpectedEdge / RiskEstimate 报告。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from qps.core.errors import DataError
from qps.core.models.foundation import (
    EvidenceQuality,
    EvidenceReport,
    ExpectedEdge,
    FormalRunArtifact,
    ResearchLineage,
    RiskEstimate,
)
from qps.infra.storage.research_db import ResearchStore, canonical_json

if TYPE_CHECKING:
    from qps.core.models.experiment import BacktestRun


class EvidenceReportBuilder:
    def __init__(self, store: ResearchStore | None = None) -> None:
        self.store = store

    def build(self, run: BacktestRun, *, lineage: ResearchLineage, trial_id: str,
              benchmark: dict[str, Any] | None = None,
              cost_stress: dict[str, Any] | None = None,
              capacity: dict[str, Any] | None = None,
              uncertainty: dict[str, Any] | None = None,
              multiple_testing_adjustment: dict[str, Any] | None = None,
              failure_criteria: dict[str, Any] | None = None,
              limitations: tuple[str, ...] = ()) -> EvidenceReport:
        if run.engine_config.get("formal") is not True:
            raise DataError(detail="non-formal run 不能生成 EvidenceReport")
        if self.store is None:
            raise DataError(detail="formal EvidenceReport 必须绑定 ResearchStore authorization")
        self.store.verify_formal_run_artifact(trial_id=trial_id, run_id=run.run_id)
        metrics = run.metrics
        if metrics is None:
            raise DataError(detail="formal run 缺少 metrics")
        report_id = f"evidence-{run.run_id}"
        data_quality = Decimal("1") if run.engine_config.get("formal_pit") or run.engine_config.get("release_id") else Decimal("0")
        report = EvidenceReport(
            report_id=report_id,
            evidence_quality=EvidenceQuality(
                data_quality=data_quality, sample_size=metrics.trade_count,
                stability=max(Decimal("0"), min(Decimal("1"), Decimal("1") - metrics.max_drawdown)),
                leakage_risk=Decimal("0") if data_quality == 1 else Decimal("1"),
                reproducibility=Decimal("1") if run.engine_config.get("lineage") else Decimal("0"),
            ),
            expected_edge=ExpectedEdge(
                net_expected_return=metrics.total_return,
                confidence_low=metrics.total_return - metrics.volatility,
                confidence_high=metrics.total_return + metrics.volatility,
                benchmark_ref=(benchmark or {}).get("id", "cash-buy-and-hold"),
            ),
            risk_estimate=RiskEstimate(
                max_drawdown=max(Decimal("0"), metrics.max_drawdown),
                tail_loss=max(Decimal("0"), metrics.max_drawdown + metrics.volatility),
                leverage_sensitivity=max(Decimal("0"), metrics.volatility),
                liquidity_capacity=Decimal(str((capacity or {}).get("capital", "0"))),
            ),
            experiment_refs=(trial_id, run.run_id), created_at=datetime.now(UTC), formal=True,
            benchmark=cast("Any", benchmark or {"id": "cash-buy-and-hold", "return": "0"}),
            cost_stress=cast("Any", cost_stress or {"scenarios": ["base", "2x", "extreme"], "cost_breakdown": metrics.cost_breakdown}),
            capacity=cast("Any", capacity or {"participation_rate": "0", "assumption": "not_estimated"}),
            uncertainty=cast("Any", uncertainty or {"sample_size": metrics.trade_count, "interval": "volatility_proxy"}),
            multiple_testing_adjustment=cast("Any", multiple_testing_adjustment or {"method": "family_trial_penalty", "trial_id": trial_id}),
            failure_criteria=cast("Any", failure_criteria or {"max_drawdown": "0.30", "min_trades": "10"}),
            limitations=limitations or ("AlphaScore 仅兼容展示，不触发审批、部署或资金分配",),
            lineage=lineage,
        )
        return report

    async def build_from_formal_run(self, *, formal_run_artifact_id: str) -> EvidenceReport:
        """Derive a complete report from the verified actual-lineage artifact.

        The caller supplies only an immutable artifact reference.  In particular,
        no lineage, benchmark or capacity values are accepted from the caller.
        """
        if self.store is None:
            raise DataError(detail="formal report 必须绑定 ResearchStore")
        artifact = self.store.get_formal_artifact_by_id(formal_run_artifact_id)
        if artifact is None:
            raise DataError(detail="formal artifact 不存在")
        if not isinstance(artifact, FormalRunArtifact):
            raise DataError(detail="formal artifact 类型非法")
        def plain(value: Any) -> Any:
            return value.to_plain() if hasattr(value, "to_plain") else value

        metrics_payload = plain(artifact.metrics)
        if not isinstance(metrics_payload, dict):
            raise DataError(detail="formal artifact 缺少 actual metrics")
        capacity = plain(artifact.capacity)
        if not isinstance(capacity, dict) or any(
            capacity.get(key) in (None, "", "not_estimated")
            for key in ("participation_rate", "volume_assumption", "planned_capital")
        ):
            raise DataError(detail="formal artifact 缺少可验证 capacity traceability")
        cost_breakdown = plain(artifact.cost_breakdown)
        if not isinstance(cost_breakdown, dict) or not all(
            key in cost_breakdown for key in ("fees", "slippage", "funding", "borrowing", "liquidation")
        ):
            raise DataError(detail="formal artifact 缺少分列成本")
        benchmark = plain(artifact.model_dump(mode="json").get("benchmark"))
        if not isinstance(benchmark, dict) or not {"cash", "buy_and_hold", "simple"}.issubset(benchmark):
            raise DataError(detail="formal artifact benchmark 不完整")
        lineage = self.store.lineage_from_artifact(artifact)
        from qps.core.models.experiment import Metrics
        metrics = Metrics.model_validate(metrics_payload)
        registration = self.store.get_registration(artifact.trial_id)
        base_cost = {key: str(Decimal(str(value))) for key, value in cost_breakdown.items()}
        criteria = plain(registration.rejection_criteria)
        if not isinstance(criteria, dict):
            raise DataError(detail="formal trial 缺少结构化 failure criteria")

        def stressed_cost(multiplier: Decimal) -> dict[str, str]:
            return {key: str((Decimal(value) * multiplier).quantize(Decimal("0.00000001")))
                    for key, value in base_cost.items()}

        report = EvidenceReport(
            report_id=f"evidence-{artifact.run_id}",
            evidence_quality=EvidenceQuality(
                data_quality=Decimal("1"), sample_size=metrics.trade_count,
                stability=max(Decimal("0"), min(Decimal("1"), Decimal("1") - metrics.max_drawdown)),
                leakage_risk=Decimal("0"), reproducibility=Decimal("1"),
            ),
            expected_edge=ExpectedEdge(
                net_expected_return=metrics.total_return,
                confidence_low=metrics.total_return - metrics.volatility,
                confidence_high=metrics.total_return + metrics.volatility,
                benchmark_ref="buy_and_hold",
            ),
            risk_estimate=RiskEstimate(
                max_drawdown=max(Decimal("0"), metrics.max_drawdown),
                tail_loss=max(Decimal("0"), metrics.max_drawdown + metrics.volatility),
                leverage_sensitivity=max(Decimal("0"), metrics.volatility),
                liquidity_capacity=Decimal(str(capacity["planned_capital"])),
            ),
            experiment_refs=(artifact.trial_id, artifact.run_id),
            created_at=artifact.created_at,
            formal=True,
            benchmark=cast("Any", benchmark),
            cost_stress=cast("Any", {"scenarios": {"base": stressed_cost(Decimal("1")),
                                                    "2x": stressed_cost(Decimal("2")),
                                                    "extreme": stressed_cost(Decimal("3"))}}),
            capacity=cast("Any", capacity),
            uncertainty=cast("Any", {"sample_size": metrics.trade_count, "interval": "bootstrap_or_volatility", "method": "fixed-input"}),
            multiple_testing_adjustment=cast("Any", {"method": "family_trial_penalty",
                                                       "family_id": registration.strategy_family_id,
                                                       "trial_id": artifact.trial_id}),
            failure_criteria=cast("Any", {"pre_registered": True, **criteria}),
            limitations=("M3 只生成 M4 candidate，不代表 Forward Paper 或盈利保证",),
            lineage=lineage,
            formal_artifact_id=artifact.artifact_id,
        )
        self.store.append_report(report)
        return report

    def hash_report(self, report: EvidenceReport) -> str:
        return hashlib.sha256(canonical_json(report).encode()).hexdigest()


__all__ = ["EvidenceReportBuilder"]
