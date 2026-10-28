"""Shared v2 persisted research fixtures.

The v2 proof commands use these fixtures only as deterministic local inputs;
the durable authorization, artifact and report boundaries remain production
implementations.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

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
from qps.research.research_run_service import ResearchRunService

NOW = datetime(2026, 1, 1, tzinfo=UTC)
ARCHIVE = "archive-content-v2"
DATA = "dataset-content-v2"
FEATURE = "feature-materialization-content-v2"
RULE = "historical-rule-content-v2"
COST = "cost-model-content-v2"
ENV = "environment-lock-v2"
CLOCK = "fixed-utc-clock-v2"


def candidate(candidate_id: str = "candidate-v2") -> CandidateDescriptor:
    return CandidateDescriptor(
        candidate_id=candidate_id, candidate_fingerprint=f"fingerprint-{candidate_id}",
        strategy_family_id="family-v2", feature_family_id="feature-family-v2",
        code_structure_hash="code-structure-v2", parameter_structure_hash="params-structure-v2",
        feature_lineage=("feature-lineage-v2",), signal_sequence_hash="signal-v2",
        exposure_fingerprint="exposure-v2", exposure_refs=("fixture-btc",),
    )


def registration(trial_id: str = "trial-v2") -> TrialRegistration:
    return TrialRegistration(
        trial_id=trial_id, strategy_family_id="family-v2", hypothesis="v2 fixture hypothesis",
        allowed_datasets=("dataset-manifest-v2",), parameter_space={"threshold": ["1", "2"]},
        primary_metric="sharpe", rejection_criteria={"max_drawdown": "0.3"}, seed=17,
        registered_at=NOW, utc_scope=(NOW, NOW + timedelta(hours=1)), release_id="release-v2",
        release_archive_hash=ARCHIVE, data_manifest_hash=DATA,
        feature_materialization_hash=FEATURE, rule_version=RULE, cost_version=COST,
        historical_rule_hash=RULE, cost_hash=COST, environment_lock=ENV, clock_id=CLOCK,
        registered_by="v2-fixture",
    )


def release() -> StrategyRelease:
    return StrategyRelease(
        release_id="release-v2", strategy_id="strategy-v2", strategy_version=1,
        code_content_hash="code-v2", parameter_set_hash="params-v2",
        feature_set_ids=("feature-materialization-v2",), dataset_manifest_ids=("dataset-manifest-v2",),
        cost_model_version=COST, environment_lock_hash=ENV, evidence_report_id="pending",
        allowed_instruments=("fixture-btc",), allowed_timeframes=("5m",), manifest_hash=ARCHIVE,
        status=ReleaseStatus.FROZEN, created_at=NOW, created_by="v2-fixture",
    )


class ReleaseStoreFake:
    def __init__(self, value: StrategyRelease | None = None) -> None:
        self.value = value or release()

    async def get_release(self, release_id: str) -> StrategyRelease:
        if release_id != self.value.release_id:
            raise ValueError("unknown release")
        return self.value


class V2Engine:
    def __init__(self, *, mode: str = "ok") -> None:
        self.mode = mode
        self.calls = 0

    async def run_formal(self, request: dict[str, Any], *, trial_id: str, release: StrategyRelease) -> BacktestRun:
        self.calls += 1
        run_id = f"v2-run-{self.calls}"
        config: dict[str, Any] = {
            "formal": True, "formal_pit": True, "release_id": release.release_id,
            "release_archive_hash": ARCHIVE, "data_manifest_hash": DATA,
            "feature_materialization_hash": FEATURE, "historical_rule_hash": RULE,
            "cost_hash": COST, "rule_version": RULE, "cost_version": COST,
            "environment_lock": ENV, "random_seed": 17, "clock": CLOCK,
            "trades_hash": "trades-fixed-v2", "equity_hash": "equity-fixed-v2",
            "report_hash": "report-fixed-v2",
            "capacity": {"participation_rate": "0.1", "volume_assumption": "fixture-depth",
                          "planned_capital": "10000", "turnover": "2"},
            "benchmark": {"cash": "0", "buy_and_hold": "0.01", "simple": "0.005"},
        }
        if self.mode == "missing-actual":
            config.pop("historical_rule_hash")
        if self.mode == "synthetic":
            config["data_manifest_hash"] = "dataset-manifest-v2"
        if self.mode == "mismatch":
            config["release_archive_hash"] = "tampered-release"
        metrics = Metrics(
            total_return=Decimal("0.08"), annualized_return=Decimal("0.08"),
            max_drawdown=Decimal("0.12"), sharpe=Decimal("1.2"), win_rate=Decimal("0.55"),
            volatility=Decimal("0.10"), trade_count=24, profit_factor=Decimal("1.4"),
            funding_pnl=Decimal("-0.01"), liquidation_count=0,
            cost_breakdown={"fees": "1", "slippage": "2", "funding": "3", "borrowing": "1", "liquidation": "0"},
        )
        return BacktestRun(
            run_id=run_id, strategy_id=release.strategy_id, version_no=release.strategy_version,
            data_range=DataRange(start=NOW, end=NOW + timedelta(hours=1)),
            data_version=DATA, feature_version=FEATURE, environment_lock=ENV,
            engine_config=config, metrics=metrics, status="SUCCEEDED", created_at=NOW,
        )


def budget_manager(tmp_path: Path, *, reveal: int = 2) -> ResearchBudgetManager:
    store = ResearchStore(tmp_path / "research.db")
    manager = ResearchBudgetManager(store)
    for budget_id, scope, ref in (
        ("global", ResearchScope.GLOBAL, "global"),
        ("family", ResearchScope.STRATEGY_FAMILY, "family-v2"),
        ("feature", ResearchScope.FEATURE_FAMILY, "feature-family-v2"),
        ("actor", ResearchScope.ACTOR, ResearchActorType.SCRIPT.value),
    ):
        manager.add_budget(ResearchBudget(
            budget_id=f"{budget_id}-v2", scope=scope, scope_ref=ref, period="v2",
            max_trials=100, max_compute=Decimal("1000"), max_blind_reveals=reveal,
            similarity_policy_version="family-v2", approved_by="fixture",
            valid_from=NOW - timedelta(hours=1), valid_to=NOW + timedelta(days=2),
        ))
    return manager


def service(tmp_path: Path, *, engine: V2Engine | None = None) -> tuple[ResearchRunService, ResearchBudgetManager, V2Engine]:
    budget = budget_manager(tmp_path)
    selected_engine = engine or V2Engine()
    runner = ResearchRunService(
        research_store=budget.store, budget_port=budget,
        release_store=ReleaseStoreFake(), event_driven_engine=selected_engine,
    )
    return runner, budget, selected_engine
