#!/usr/bin/env python3
"""Persisted M3 fixture runner.

Each command uses a durable SQLite file under ``QPS_M3_FIXTURE_DIR`` and a
fresh process. It is intentionally separate from ordinary pytest fixtures so
restart/recovery and blind-domain boundaries are exercised at process level.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from qps.core.constants import ReleaseStatus, ResearchActorType, ResearchScope, TrialStatus
from qps.core.models.experiment import BacktestRun, DataRange, Metrics
from qps.core.models.foundation import (
    CandidateDescriptor,
    ResearchBudget,
    StrategyRelease,
    TrialRegistration,
)
from qps.infra.storage.research_db import ResearchStore
from qps.research.budget import ResearchBudgetManager
from qps.research.challenge import BlindChallengeRunner, BlindDatasetVault
from qps.research.research_run_service import ResearchRunService

ROOT = Path(os.environ.get("QPS_M3_FIXTURE_DIR", "/tmp/autoqps-m3-fixture"))
NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FixtureReleaseStore:
    def __init__(self, release: StrategyRelease) -> None:
        self.release = release

    async def get_release(self, release_id: str) -> StrategyRelease:
        if release_id != self.release.release_id:
            raise RuntimeError("unknown release")
        return self.release


class PersistedEventDrivenFixture:
    """A tiny persisted-input EventDriven adapter for the external proof.

    The production engine is separately guarded by ``run_formal``. This
    fixture supplies deterministic materialized bars and metrics while still
    passing through the exact ResearchRunService gate and append-only store.
    """

    async def run_formal(self, request: dict[str, Any], *, trial_id: str, release: Any) -> BacktestRun:
        run_id = f"fixture-run-{trial_id}"
        metrics = Metrics(
            total_return=Decimal("0.04"), annualized_return=Decimal("0.04"),
            max_drawdown=Decimal("0.08"), sharpe=Decimal("0.9"),
            win_rate=Decimal("0.55"), volatility=Decimal("0.12"),
            trade_count=12, profit_factor=Decimal("1.3"),
            funding_pnl=Decimal("-0.01"), liquidation_count=0,
            cost_breakdown={"fees": "1", "slippage": "2", "funding": "3", "borrowing": "0", "liquidation": "0"},
        )
        return BacktestRun(
            run_id=run_id, strategy_id="fixture-strategy", version_no=1,
            data_range=DataRange(start=NOW, end=NOW + timedelta(hours=1)),
            data_version="fixture-data-v1", feature_version="fixture-feature-v1",
            engine_config={"formal": True, "formal_pit": True, "release_id": release.release_id,
                           "trades_hash": "trades-fixture", "equity_hash": "equity-fixture",
                           "report_hash": "report-fixture", "rule_version": "rule-v1",
                           "cost_version": "cost-v1", "historical_rule_hash": "rule-v1",
                           "cost_hash": "cost-v1", "clock": "fixed-clock",
                           "capacity": {"participation_rate": "0.1", "volume_assumption": "fixture", "planned_capital": "10000"},
                           "benchmark": {"cash": "0", "buy_and_hold": "0", "simple": "0"}},
            environment_lock="env-fixture-v1", metrics=metrics,
            status="SUCCEEDED", created_at=NOW,
        )


def _release() -> StrategyRelease:
    return StrategyRelease(
        release_id="fixture-release-v1", strategy_id="fixture-strategy", strategy_version=1,
        code_content_hash="code-hash", parameter_set_hash="params-hash",
        feature_set_ids=("feature-materialization-v1",), dataset_manifest_ids=("bars-manifest-v1",),
        cost_model_version="cost-v1", environment_lock_hash="env-fixture-v1",
        evidence_report_id="not-yet-selected", allowed_instruments=("fixture-btc",),
        allowed_timeframes=("5m",), manifest_hash="archive-hash-v1", status=ReleaseStatus.FROZEN,
        created_at=NOW, created_by="fixture",
    )


def _budgets(store: ResearchStore, *, blind: bool = False) -> ResearchBudgetManager:
    manager = ResearchBudgetManager(store)
    end = NOW + timedelta(days=2)
    values = [
        ("global", ResearchScope.GLOBAL, "global"),
        ("family", ResearchScope.STRATEGY_FAMILY, "family-fixture"),
        ("feature", ResearchScope.FEATURE_FAMILY, "feature-family-fixture"),
        ("actor", ResearchScope.ACTOR, ResearchActorType.SCRIPT.value),
    ]
    for budget_id, scope, ref in values:
        manager.add_budget(ResearchBudget(
            budget_id=f"{budget_id}-m3", scope=scope, scope_ref=ref, period="fixture",
            max_trials=10, max_compute=Decimal("100"), max_blind_reveals=2 if blind else 0,
            similarity_policy_version="family-v1", approved_by="fixture",
            valid_from=NOW - timedelta(hours=1), valid_to=end,
        ))
    return manager


def _registration(trial_id: str, *, blind: bool = False) -> tuple[TrialRegistration, CandidateDescriptor]:
    candidate = CandidateDescriptor(
        candidate_id=f"candidate-{trial_id}", candidate_fingerprint=f"fp-{trial_id}",
        strategy_family_id="family-fixture", feature_family_id="feature-family-fixture",
        code_structure_hash="code-structure-v1", parameter_structure_hash="params-structure-v1",
        feature_lineage=("feature-family-fixture",), signal_sequence_hash="signal-v1",
        exposure_fingerprint="exposure-v1",
    )
    registration = TrialRegistration(
        trial_id=trial_id, strategy_family_id="family-fixture", hypothesis="fixture hypothesis",
        allowed_datasets=("bars-manifest-v1",), parameter_space={"threshold": ["1", "2"]},
        primary_metric="sharpe", rejection_criteria={"max_drawdown": "0.3"}, seed=7,
        registered_at=NOW, utc_scope=(NOW, NOW + timedelta(hours=1)), release_id="fixture-release-v1",
        data_manifest_hash="data-hash-v1", feature_materialization_hash="feature-hash-v1",
        rule_version="rule-v1", cost_version="cost-v1", environment_lock="env-fixture-v1",
        historical_rule_hash="rule-v1", cost_hash="cost-v1", clock_id="fixed-clock", registered_by="fixture",
    )
    return registration, candidate


async def formal_entry() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(ROOT / "research.db")
    try:
        if store.latest_trial_status("formal-entry") == TrialStatus.SUCCEEDED:
            print(json.dumps({"status": "passed", "formal": True, "persisted": str(ROOT / "research.db"), "recovered": True}))
            return 0
    except Exception:
        pass
    budget = _budgets(store)
    release = _release()
    service = ResearchRunService(
        research_store=store, budget_port=budget, release_store=FixtureReleaseStore(release),
        event_driven_engine=PersistedEventDrivenFixture(),
    )
    registration, candidate = _registration("formal-entry")
    result = await service.register_and_reserve_trial(
        registration=registration, candidate=candidate, actor_type=ResearchActorType.SCRIPT,
        requested_compute=Decimal("1"), blind_reveals=0, reserved_at=NOW,
        frozen_release_id=release.release_id, request={"strategy_id": "fixture-strategy", "version_no": 1},
    )
    assert isinstance(result, BacktestRun) and result.engine_config["formal"] is True
    assert store.latest_trial_status("formal-entry") == TrialStatus.SUCCEEDED
    assert store.get_report("evidence-fixture-run-formal-entry").formal is True
    print(json.dumps({"status": "passed", "formal": True, "persisted": str(ROOT / "research.db")}))
    return 0


async def blind_acl() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(ROOT / "blind.db")
    budget = _budgets(store, blind=True)
    registration, candidate = _registration("blind-acl", blind=True)
    store.append_registration(registration)
    await budget.reserve_trial(registration=registration, candidate=candidate, actor_type=ResearchActorType.SCRIPT,
                               requested_compute=Decimal("1"), blind_reveals=1, reserved_at=NOW)
    runner = BlindChallengeRunner(
        vault=BlindDatasetVault({"interval_key": "2025-Q4", "answer": "secret", "raw_rows": ["secret"]}),
        budget_port=budget, research_store=store,
    )
    try:
        runner.read_raw(actor="optimizer")
    except PermissionError:
        pass
    else:
        raise AssertionError("optimizer 读取 blind raw 必须拒绝")
    print(json.dumps({"status": "passed", "raw_read_count": runner.raw_read_count}))
    return 0


async def blind_reveal() -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(ROOT / "blind-reveal.db")
    with store.connect() as conn:
        if conn.execute("SELECT 1 FROM research_blind_results WHERE trial_id = ?", ("blind-reveal",)).fetchone() is not None:
            print(json.dumps({"status": "passed", "revealed": "2025-Q4", "recovered": True}))
            return 0
    budget = _budgets(store, blind=True)
    registration, candidate = _registration("blind-reveal", blind=True)
    store.append_registration(registration)
    await budget.reserve_trial(registration=registration, candidate=candidate, actor_type=ResearchActorType.SCRIPT,
                               requested_compute=Decimal("1"), blind_reveals=1, reserved_at=NOW)
    runner = BlindChallengeRunner(vault=BlindDatasetVault({"interval_key": "2025-Q4", "answer": "secret", "passed": True}), budget_port=budget, research_store=store)
    result = await runner.run(frozen_release_id="fixture-release-v1", trial_id="blind-reveal", requested_at=NOW)
    assert result.revealed_scope == "2025-Q4"
    try:
        await runner.run(frozen_release_id="fixture-release-v1", trial_id="blind-reveal", requested_at=NOW)
    except Exception:
        pass
    else:
        raise AssertionError("reveal quota 不可恢复")
    print(json.dumps({"status": "passed", "revealed": result.revealed_scope}))
    return 0


async def main(command: str) -> int:
    if command == "m3-formal-research-entry":
        return await formal_entry()
    if command == "m3-blind-acl":
        return await blind_acl()
    if command == "m3-blind-reveal":
        return await blind_reveal()
    raise SystemExit(f"unknown M3 fixture command: {command}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.command)))
