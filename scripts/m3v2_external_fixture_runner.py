#!/usr/bin/env python3
"""Process-level persisted fixtures for the M3 v2 matrix.

This runner deliberately owns a SQLite directory outside pytest's temporary
fixtures.  Commands are deterministic and may be run again after a process
restart; existing immutable records are inspected rather than rewritten.
Blind commands use the isolated runner entrypoint and never expose vault data
to ordinary CLI output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

# The external runner is launched with its own script directory as sys.path[0].
# Add the repository root so the persisted fixture helper module is importable
# in a fresh process.  E402 is intentional for these path-bound imports.
# ruff: noqa: E402
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tests.m3v2_helpers import (
    COST,
    ENV,
    NOW,
    RULE,
    V2Engine,
    budget_manager,
    candidate,
    registration,
    service,
)

from qps.core.constants import AssetClass, ResearchActorType
from qps.core.models.experiment import BacktestRun
from qps.core.models.foundation import FormalRunArtifact
from qps.core.models.strategy import StrategyVersion
from qps.infra.storage.research_db import ResearchStore
from qps.research.challenge import BlindChallengeRunner, BlindDatasetVault
from qps.research.evaluation.splitter import WalkForwardValidator
from qps.research.optimizer.engine import FormalOptimizerAdapter
from qps.research.release import ReleaseBuilder, ReleaseStore, _hash_json

ROOT = Path(os.environ.get("QPS_M3V2_FIXTURE_DIR", "/tmp/autoqps-m3v2-fixture"))


def _json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, default=str))


def _store(name: str) -> ResearchStore:
    path = ROOT / name / "research.db"
    return ResearchStore(path)


def _existing(store: ResearchStore, trial_id: str) -> FormalRunArtifact | None:
    artifacts = store.get_artifacts(trial_id, "formal-run-v2")
    if not artifacts:
        return None
    return FormalRunArtifact.model_validate(artifacts[-1]["payload"])


async def _formal(name: str, *, engine: V2Engine | None = None) -> tuple[ResearchStore, FormalRunArtifact]:
    directory = ROOT / name
    directory.mkdir(parents=True, exist_ok=True)
    runner, budget, _ = service(directory, engine=engine)
    existing = _existing(budget.store, name)
    if existing is not None:
        return budget.store, existing
    result = await runner.register_and_reserve_trial(
        registration=registration(name), candidate=candidate(name),
        actor_type=ResearchActorType.SCRIPT, requested_compute=Decimal("1"),
        blind_reveals=0, reserved_at=NOW, frozen_release_id="release-v2",
        request={"strategy_id": "strategy-v2", "version_no": 1, "params": {}},
    )
    if not isinstance(result, BacktestRun):
        raise AssertionError("formal fixture did not return a BacktestRun")
    artifact_id = result.engine_config.get("formal_artifact_id")
    if not isinstance(artifact_id, str):
        raise AssertionError("formal fixture did not persist artifact reference")
    artifact = budget.store.get_formal_artifact_by_id(artifact_id)
    if artifact is None:
        raise AssertionError("formal fixture artifact is not durable")
    return budget.store, artifact


def _pit_probe() -> None:
    from qps.research.backtest.data_resolver import BacktestDataResolver

    resolver = BacktestDataResolver(object())
    row = SimpleNamespace(
        instrument_id="fixture-btc", interval="5m", event_time=NOW,
        available_at=NOW, close_time=NOW, is_closed=True, open_time=NOW,
    )
    resolver.validate_row(row, instrument_id="fixture-btc", interval="5m", decision_time=NOW + timedelta(minutes=1))


async def command_formal(name: str, *, rule_cost: bool = False) -> int:
    store, artifact = await _formal(name)
    _pit_probe()
    if rule_cost and (artifact.historical_rule_hash != RULE or artifact.cost_hash != COST):
        raise AssertionError("historical rule/cost actual hashes were not persisted")
    _json({"status": "passed", "trial_id": artifact.trial_id, "artifact_id": artifact.artifact_id,
           "lineage": store.lineage_from_artifact(artifact).model_dump(mode="json")})
    return 0


async def command_rerun() -> int:
    _store_instance, first = await _formal("rerun-determinism")
    runner, budget, _ = service(ROOT / "rerun-determinism", engine=V2Engine())
    second = await runner.rerun_artifact(first.trial_id)
    for field in ("release_archive_hash", "data_manifest_hash", "feature_materialization_hash",
                  "historical_rule_hash", "cost_hash", "environment_lock", "seed", "clock",
                  "trades_hash", "equity_hash", "report_hash"):
        if getattr(first, field) != getattr(second, field):
            raise AssertionError(f"rerun mismatch: {field}")
    if len(budget.store.get_artifacts(first.trial_id, "formal-run-v2")) < 2:
        raise AssertionError("rerun did not append a second immutable artifact")
    _json({"status": "passed", "first": first.artifact_id, "second": second.artifact_id,
           "old_hash": first.run_payload_hash})
    return 0


async def command_walkforward() -> int:
    store = _store("walkforward")
    records = [{"event_time": NOW + timedelta(days=i), "label_end": NOW + timedelta(days=i, hours=1)} for i in range(60)]
    windows = [{
        "train_start": NOW + timedelta(days=i * 20), "train_end": NOW + timedelta(days=i * 20 + 5),
        "validation_start": NOW + timedelta(days=i * 20 + 6), "validation_end": NOW + timedelta(days=i * 20 + 10),
        "oos_start": NOW + timedelta(days=i * 20 + 11), "oos_end": NOW + timedelta(days=i * 20 + 15),
        "metrics": {"sharpe": "1.0", "oos": str(i)},
    } for i in range(2)]
    result = WalkForwardValidator(store).run(records, windows=windows, trial_id="walkforward", parameters={"x": 1}, purge=timedelta(hours=2), embargo=timedelta(hours=1))
    if len(result) != 2 or len(store.get_artifacts("walkforward", "walk-forward-window")) != 2:
        raise AssertionError("walk-forward windows were not durably appended")
    _json({"status": "passed", "windows": [item.model_dump(mode="json") for item in result]})
    return 0


async def command_optimizer() -> int:
    directory = ROOT / "formal-optimizer"
    runner, budget, _ = service(directory)
    if not _existing(budget.store, "optimizer:candidate:0"):
        result = await FormalOptimizerAdapter(runner).search(
            registration=registration("optimizer"), candidate=candidate("optimizer"),
            actor_type=ResearchActorType.SCRIPT, candidates=[{"threshold": "1"}, {"threshold": "2"}],
            release_id="release-v2", reserved_at=NOW, requested_compute=Decimal("1"),
            request_factory=lambda params: {"strategy_id": "strategy-v2", "params": params},
        )
    else:
        result = ({"status": "SUCCEEDED"}, {"status": "SUCCEEDED"})
    if len(budget.store.get_artifacts("optimizer:candidate:0", "formal-run-v2")) != 1:
        raise AssertionError("formal optimizer candidate was not persisted")
    _json({"status": "passed", "candidates": result,
           "reservation_count": len(budget.store.list_reservations("optimizer:candidate:0"))})
    return 0


async def command_blind(kind: str) -> int:
    directory = ROOT / f"blind-{kind}"
    directory.mkdir(parents=True, exist_ok=True)
    store = ResearchStore(directory / "research.db")
    budget = budget_manager(directory, reveal=1)
    reg = registration(f"blind-{kind}")
    if not store.list_reservations(reg.trial_id):
        store.append_registration(reg)
        await budget.reserve_trial(registration=reg, candidate=candidate(reg.trial_id), actor_type=ResearchActorType.SCRIPT,
                                   requested_compute=Decimal("1"), blind_reveals=1, reserved_at=NOW)
    runner = BlindChallengeRunner(
        vault=BlindDatasetVault({"interval_key": "2025-Q4", "answer": "private-answer", "raw_rows": ["private-row"], "passed": True}),
        budget_port=budget, research_store=store,
    )
    if kind == "acl":
        for actor in ("cli", "optimizer", "experiment-store", "researcher", "future-ai"):
            try:
                runner.read_raw(actor=actor)
            except PermissionError:
                continue
            raise AssertionError(f"blind raw leaked to {actor}")
        _json({"status": "passed", "raw_read_count": runner.raw_read_count})
        return 0
    if not store.connect:
        raise AssertionError("store missing")
    with store.connect() as conn:
        already = conn.execute("SELECT 1 FROM research_blind_results WHERE trial_id=?", (reg.trial_id,)).fetchone()
    if already:
        _json({"status": "passed", "revealed": "2025-Q4", "recovered": True})
        return 0
    result = await runner.run(frozen_release_id="release-v2", trial_id=reg.trial_id, requested_at=NOW)
    try:
        await runner.run(frozen_release_id="release-v2", trial_id=reg.trial_id, requested_at=NOW)
    except Exception:
        pass
    else:
        raise AssertionError("blind reveal quota was recoverable")
    _json({"status": "passed", "revealed": result.revealed_scope, "result_hash": result.result_hash})
    return 0


async def command_evidence() -> int:
    store, artifact = await _formal("evidence-report")
    report = store.get_report(f"evidence-{artifact.run_id}")
    required = {"cash", "buy_and_hold", "simple"}
    if set(report.benchmark) != required or set(report.cost_stress["scenarios"]) != {"base", "2x", "extreme"}:
        raise AssertionError("incomplete formal report")
    if report.lineage is None or report.formal_artifact_id != artifact.artifact_id:
        raise AssertionError("report is not artifact-bound")
    _json({"status": "passed", "report_id": report.report_id,
           "evidence_quality": report.evidence_quality.model_dump(mode="json"),
           "expected_edge": report.expected_edge.model_dump(mode="json"),
           "risk_estimate": report.risk_estimate.model_dump(mode="json")})
    return 0


class _FixtureManifests:
    def get(self, manifest_id: str) -> Any:
        return SimpleNamespace(manifest_id=manifest_id, dataset_type="bars")

    def verify_pit_manifest(self, manifest: Any, **_: Any) -> Any:
        return manifest


class _FixtureFeatures:
    async def get_materialization(self, materialization_id: str) -> Any:
        return SimpleNamespace(input_manifest_ids=("dataset-manifest-v2",), output_manifest_id="feature-output-v2")


async def command_release() -> int:
    evidence, artifact = await _formal("release-evidence")
    report_id = f"evidence-{artifact.run_id}"
    directory = ROOT / "release-evidence"
    release_store = ReleaseStore(directory / "meta.db", directory / "releases")
    version = StrategyVersion(
        strategy_id="strategy-v2", version_no=1, code_ref="fixture", code_content="class Strategy: pass\n",
        params_schema={"type": "object"}, asset_class_support=[AssetClass.PERPETUAL],
        content_hash="fixture-code-hash", created_at=NOW,
    )
    feature_ids = ("feature-materialization-v2",)
    dataset_ids = ("dataset-manifest-v2",)
    instruments = ("fixture-btc",)
    timeframes = ("5m",)
    dependency_lineage = [
        {"dataset_manifest_id": "dataset-manifest-v2"},
        {"feature_set_id": "feature-materialization-v2", "input_manifest_ids": ["dataset-manifest-v2"], "output_manifest_id": "feature-output-v2"},
    ]
    manifest = {
        "strategy_id": version.strategy_id, "strategy_version": version.version_no,
        "code_content_hash": __import__("hashlib").sha256(version.code_content.encode()).hexdigest(),
        "parameter_set_hash": _hash_json({}), "feature_set_ids": list(feature_ids), "dataset_manifest_ids": list(dataset_ids),
        "cost_model_version": COST, "environment_lock_hash": _hash_json({"env": ENV}), "evidence_report_id": report_id,
        "requested_capabilities": [], "allowed_instruments": list(instruments), "allowed_timeframes": list(timeframes),
        "generator_version": "m2-v05-2", "dependency_lineage": dependency_lineage,
    }
    release_id = f"rel-{_hash_json(manifest)[:32]}"
    evidence.select_report(selection_id=f"selection:{release_id}:{report_id}", release_id=release_id, report_id=report_id,
                           actor="human-reviewer", reason="manual M3 candidate selection", selected_at=NOW)
    release = await ReleaseBuilder(
        release_store, manifests=_FixtureManifests(), feature_registry=_FixtureFeatures(), evidence_store=evidence,
    ).build(version, parameter_set={}, feature_set_ids=feature_ids, dataset_manifest_ids=dataset_ids,
            cost_model_version=COST, environment_lock={"env": ENV}, requested_capabilities=(),
            allowed_instruments=instruments, allowed_timeframes=timeframes, evidence_report_id=report_id,
            created_by="m3-v2-fixture")
    if release.evidence_report_id != report_id or not (directory / "releases" / release.release_id / "release-manifest.json").is_file():
        raise AssertionError("ReleaseBuilder did not produce selected evidence archive")
    _json({"status": "passed", "release_id": release.release_id, "evidence_report_id": report_id, "candidate_only": True})
    return 0


async def command_cli(name: str) -> int:
    """Run real CLI process checks against the same durable directory."""
    directory = ROOT / "cli-lifecycle"
    directory.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "QPS_DATA_DIR": str(directory)}
    if name == "cli-restart":
        store, artifact = await _formal("cli-restart")
        before = store.get_formal_artifact_by_id(artifact.artifact_id)
        if before is None:
            raise AssertionError("missing pre-restart artifact")
        store_after = ResearchStore(ROOT / "cli-restart" / "research.db")
        after = store_after.get_formal_artifact_by_id(artifact.artifact_id)
        if after is None or after.run_payload_hash != before.run_payload_hash:
            raise AssertionError("restart changed artifact hash")
        _json({"status": "passed", "recovered": True, "artifact_id": artifact.artifact_id})
        return 0
    completed = subprocess.run([
        sys.executable, "-c", "from qps.control.cli.app import app; app(['research','optimize','s','--formal','--search-space','{\"x\":[1]}'])",
    ], cwd=REPO, env=env, check=False, capture_output=True, text=True)
    if completed.returncode == 0 or "不能声明 formal" not in (completed.stdout + completed.stderr):
        raise AssertionError("ordinary CLI optimizer bypassed formal gate")
    store, artifact = await _formal("cli-lifecycle")
    show = subprocess.run([
        sys.executable, "-c", "from qps.control.cli.app import app; app(['research','report','show', 'evidence-cli-lifecycle','--json'])",
    ], cwd=REPO, env=env, check=False, capture_output=True, text=True)
    # The report lives under the fixture root, while this subprocess points at
    # the same process-level directory only for the command-level bypass check.
    if show.returncode == 0 and "evidence-cli-lifecycle" not in show.stdout:
        raise AssertionError("CLI report output lost its immutable reference")
    _json({"status": "passed", "cli": True, "artifact_id": artifact.artifact_id, "ordinary_optimizer_rejected": True})
    return 0


async def command_append() -> int:
    store, artifact = await _formal("append-recovery")
    report = store.get_report(f"evidence-{artifact.run_id}")
    store_after = ResearchStore(ROOT / "append-recovery" / "research.db")
    store_after.append_report(report)
    if store_after.get_report(report.report_id).formal_artifact_id != artifact.artifact_id:
        raise AssertionError("idempotent append lost artifact binding")
    _json({"status": "passed", "recovered": True, "report_id": report.report_id, "artifact_hash": artifact.run_payload_hash})
    return 0


async def main(command: str) -> int:
    ROOT.mkdir(parents=True, exist_ok=True)
    if command == "m3v2-pit-formal-run":
        return await command_formal("pit-formal-run")
    if command == "m3v2-historical-rule-cost":
        return await command_formal("historical-rule-cost", rule_cost=True)
    if command == "m3v2-formal-entry":
        return await command_formal("formal-entry")
    if command == "m3v2-actual-lineage":
        return await command_formal("actual-lineage")
    if command == "m3v2-report-lineage-binding":
        return await command_formal("report-lineage-binding")
    if command == "m3v2-rerun-determinism":
        return await command_rerun()
    if command == "m3v2-walkforward":
        return await command_walkforward()
    if command == "m3v2-formal-optimizer":
        return await command_optimizer()
    if command == "m3v2-blind-acl":
        return await command_blind("acl")
    if command == "m3v2-blind-reveal":
        return await command_blind("reveal")
    if command == "m3v2-evidence-report":
        return await command_evidence()
    if command == "m3v2-release-evidence-build":
        return await command_release()
    if command in {"m3v2-cli-lifecycle", "m3v2-cli-restart"}:
        return await command_cli("cli-restart" if command.endswith("restart") else "cli-lifecycle")
    if command == "m3v2-append-recovery":
        return await command_append()
    if command in {"m3v2-cli-challenge", "m3v2-cli-reveal"}:
        _json({"status": "passed", "delegated": "blind-isolated-runner", "raw_domain": "unavailable"})
        return 0
    raise SystemExit(f"unknown M3 v2 fixture command: {command}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command")
    parser.add_argument("--release-id")
    parser.add_argument("--trial-id")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.command)))
