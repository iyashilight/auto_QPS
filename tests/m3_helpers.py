"""Shared M3 proof fixtures; external persisted proofs live in scripts/."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from qps.core.constants import ReleaseStatus, ResearchActorType, ResearchScope
from qps.core.models.experiment import BacktestRun, DataRange, Metrics
from qps.core.models.foundation import (
    CandidateDescriptor,
    ResearchBudget,
    StrategyRelease,
    TrialRegistration,
)
from qps.infra.storage.research_db import ResearchStore
from qps.research.budget import ResearchBudgetManager

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def candidate(name: str = "candidate") -> CandidateDescriptor:
    return CandidateDescriptor(
        candidate_id=name, candidate_fingerprint=f"fp-{name}", strategy_family_id="family-1",
        feature_family_id="feature-1", code_structure_hash="code-1", parameter_structure_hash="params-1",
        feature_lineage=("feature-1",), signal_sequence_hash="signal-1", exposure_fingerprint="exposure-1",
        exposure_refs=("btc",),
    )


def registration(trial_id: str = "trial-1", *, release_id: str = "release-1") -> TrialRegistration:
    return TrialRegistration(
        trial_id=trial_id, strategy_family_id="family-1", hypothesis="test hypothesis",
        allowed_datasets=("manifest-1",), parameter_space={"threshold": ["1", "2"]},
        primary_metric="sharpe", rejection_criteria={"max_drawdown": "0.3"}, seed=42,
        registered_at=NOW, utc_scope=(NOW, NOW + timedelta(hours=1)), release_id=release_id,
        data_manifest_hash="data-1", feature_materialization_hash="feature-1",
        rule_version="rule-1", cost_version="cost-1", environment_lock="env-1", clock_id="clock-1",
        historical_rule_hash="rule-1", cost_hash="cost-1",
        registered_by="test",
    )


def budget_manager(tmp_path: Any, *, reveal: int = 2) -> ResearchBudgetManager:
    store = ResearchStore(tmp_path / "research.db")
    manager = ResearchBudgetManager(store)
    for budget_id, scope, ref in (
        ("global", ResearchScope.GLOBAL, "global"),
        ("family", ResearchScope.STRATEGY_FAMILY, "family-1"),
        ("feature", ResearchScope.FEATURE_FAMILY, "feature-1"),
        ("actor", ResearchScope.ACTOR, ResearchActorType.SCRIPT.value),
    ):
        manager.add_budget(ResearchBudget(
            budget_id=budget_id, scope=scope, scope_ref=ref, period="test",
            max_trials=10, max_compute=Decimal("100"), max_blind_reveals=reveal,
            similarity_policy_version="family-v1", approved_by="test",
            valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(days=1),
        ))
    return manager


def release(release_id: str = "release-1") -> StrategyRelease:
    return StrategyRelease(
        release_id=release_id, strategy_id="strategy-1", strategy_version=1,
        code_content_hash="code", parameter_set_hash="params", feature_set_ids=("feature-1",),
        dataset_manifest_ids=("manifest-1",), cost_model_version="cost-1", environment_lock_hash="env-1",
        evidence_report_id="report-1", allowed_instruments=("btc",), allowed_timeframes=("5m",),
        manifest_hash="archive-1", status=ReleaseStatus.FROZEN, created_at=NOW, created_by="test",
    )


class ReleaseStoreFake:
    def __init__(self, value: StrategyRelease) -> None:
        self.value = value

    async def get_release(self, release_id: str) -> StrategyRelease:
        if release_id != self.value.release_id:
            raise ValueError("unknown release")
        return self.value


class FormalEngineFake:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.mismatch = mismatch
        self.calls = 0

    async def run_formal(self, request: dict[str, Any], *, trial_id: str, release: StrategyRelease) -> BacktestRun:
        self.calls += 1
        return BacktestRun(
            run_id=f"run-{self.calls}", strategy_id="strategy-1", version_no=1,
            data_range=DataRange(start=NOW, end=NOW + timedelta(hours=1)), data_version="data-1",
            feature_version="feature-1", environment_lock="env-1",
            engine_config={"formal": True, "formal_pit": True, "release_archive_hash": "archive-2" if self.mismatch else "archive-1",
                           "data_manifest_hash": "data-1", "feature_materialization_hash": "feature-1",
                           "rule_version": "rule-1", "cost_version": "cost-1", "environment_lock": "env-1",
                           "historical_rule_hash": "rule-1", "cost_hash": "cost-1", "random_seed": 42,
                           "clock": "clock-1", "trades_hash": "trades-1", "equity_hash": "equity-1", "report_hash": "report-1",
                           "capacity": {"participation_rate": "0.1", "volume_assumption": "fixture", "planned_capital": "10000"},
                           "benchmark": {"cash": "0", "buy_and_hold": "0", "simple": "0"}},
            metrics=Metrics(total_return=Decimal("0.1"), annualized_return=Decimal("0.1"), max_drawdown=Decimal("0.1"),
                            sharpe=Decimal("1"), win_rate=Decimal("0.5"), volatility=Decimal("0.1"), trade_count=10,
                            profit_factor=Decimal("1"), cost_breakdown={"fees": "1", "slippage": "1", "funding": "1", "borrowing": "0", "liquidation": "0"}), status="SUCCEEDED", created_at=NOW,
        )
