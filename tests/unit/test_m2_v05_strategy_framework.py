from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from qps.core.constants import BrokerMode
from qps.core.errors import BacktestError, DataError
from qps.core.models.strategy import Deployment
from qps.core.sdk.formal import (
    DecisionInput,
    FormalDecisionInputFactory,
    FormalStrategyExecutor,
    VerifiedDecisionMaterialization,
    content_hash,
)
from qps.research.backtest.vectorized import VectorizedBacktestEngine
from qps.research.registry import StrategyRegistry
from qps.research.release import ReleaseBuilder, ReleaseStore
from qps.trading.runtime.process_runtime import IsolatedStrategyRuntime
from qps.trading.runtime.state import CommittedState, StateStore


class OfflineReleaseFixtureBuilder(ReleaseBuilder):
    """Named test fixture; production ReleaseBuilder has no dependency bypass."""

    async def _verify_dependencies(self, feature_ids, dataset_ids, instruments, timeframes):
        return


TEST_DECISION_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class RuntimeInputFactory:
    """Explicit unit adapter; production Runtime requires the real factory ports."""

    async def load_verified(self, *, release, input_snapshot_ref):
        payload = {"heartbeat": True}
        return DecisionInput(
            input_snapshot_ref=input_snapshot_ref, decision_time=TEST_DECISION_TIME,
            instrument_id=release.allowed_instruments[0], interval=release.allowed_timeframes[0],
            payload=payload, source_manifest_ids=tuple(release.dataset_manifest_ids),
            feature_materialization_ids=tuple(release.feature_set_ids), available_at=TEST_DECISION_TIME,
            source_manifest_hashes=tuple((item, f"hash-{item}") for item in release.dataset_manifest_ids),
            feature_materialization_hashes=tuple((item, f"hash-{item}") for item in release.feature_set_ids),
            source_row_hash=content_hash({"heartbeat": True}), materializer_schema_version="unit-fake-v1",
            decision_request={"decision_time": TEST_DECISION_TIME.isoformat(),
                              "instrument_id": release.allowed_instruments[0],
                              "interval": release.allowed_timeframes[0]},
        )


class ReplayRejectingRuntimeInputFactory(RuntimeInputFactory):
    """Unit adapter proving Runtime validates provenance before State replay."""

    def __init__(self) -> None:
        self.calls = 0

    async def load_verified(self, *, release, input_snapshot_ref):
        self.calls += 1
        if self.calls > 1:
            raise ValueError("trusted source 已删除")
        return await super().load_verified(release=release, input_snapshot_ref=input_snapshot_ref)


class TrustedMaterializer:
    """Explicit unit-only adapter; it cannot be discovered by production DI."""

    async def derive(self, *, release, decision_time, instrument_id, interval):
        payload = {"bar": {"instrument_id": instrument_id, "close": "1"},
                   "features": {instrument_id: {"signal": 1.0}}, "supplemental": {}}
        source_rows = {"bar": payload["bar"], "features": [{"feature_name": "signal", "value": "1"}],
                       "supplemental": {}}
        return VerifiedDecisionMaterialization(
            decision_time=decision_time, instrument_id=instrument_id, interval=interval,
            payload=payload, available_at=decision_time,
            source_manifest_ids=tuple(release.dataset_manifest_ids),
            feature_materialization_ids=tuple(release.feature_set_ids),
            source_manifest_hashes=tuple((item, "source-hash") for item in release.dataset_manifest_ids),
            feature_materialization_hashes=tuple((item, "feature-hash") for item in release.feature_set_ids),
            source_row_hash=content_hash(source_rows), materializer_schema_version="unit-trusted-v1")


class TrustedManifestStore:
    def __init__(self):
        self.manifests = {
            "m1": SimpleNamespace(manifest_id="m1", dataset_type="bars", content_hash="source-hash"),
            "f-output": SimpleNamespace(
                manifest_id="f-output", dataset_type="features", content_hash="feature-hash"),
        }

    def get(self, manifest_id):
        return self.manifests[manifest_id]

    def verify_pit_manifest(self, manifest, **_kwargs):
        return manifest


class TrustedFeatureRegistry:
    def get_materialization_sync(self, materialization_id):
        if materialization_id != "f1":
            raise KeyError(materialization_id)
        return SimpleNamespace(
            materialization_id="f1", input_manifest_ids=("m1",), output_manifest_id="f-output",
            content_hash="feature-hash")


def test_persisted_formal_backtest_routes_release_artifacts(tmp_path: Path) -> None:
    """D3 regression: bars manifest must never be reused as funding/feature input."""
    result = subprocess.run(
        [sys.executable, "scripts/m2_external_fixture_runner.py", "m2-formal-backtest-call-graph"],
        cwd=Path(__file__).parents[2],
        env=os.environ | {"QPS_M2_FIXTURE_DIR": str(tmp_path / "fixture")},
        capture_output=True,
        check=False,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '"backtest_status": "SUCCEEDED"' in result.stdout
    assert '"signal_source": "strategy"' in result.stdout
    assert '"decision_input_artifacts": []' not in result.stdout


@pytest.mark.asyncio
async def test_release_survives_registry_restart_and_workdir_change(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "release_probe", "code_ref": "local:probe",
        "code_content": "class Strategy:\n    def initialize(self, ctx):\n        self.ready = True\n    def on_snapshot(self, ctx, snapshot):\n        return None\n", "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",),
        dataset_manifest_ids=("m1",), cost_model_version="c1", environment_lock={"python": "3"},
        requested_capabilities=(), allowed_instruments=("BTC",), allowed_timeframes=("5m",),
        evidence_report_id="e1", created_by="test",
    )
    (tmp_path / "strategies").mkdir()
    (tmp_path / "strategies" / "work.py").write_text("changed", encoding="utf-8")
    loaded = await ReleaseStore(db).get_release(release.release_id)
    assert loaded.manifest_hash == release.manifest_hash
    assert loaded.code_content_hash == release.code_content_hash


@pytest.mark.asyncio
async def test_runtime_uses_archive_and_isolates_ipc(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "runtime_probe", "code_ref": "local:probe",
        "code_content": "class Strategy:\n    def initialize(self, ctx):\n        self.ready = True\n    def on_snapshot(self, ctx, snapshot):\n        return None\n", "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test",
    )
    runtime = IsolatedStrategyRuntime(store, require_os_isolation=False,
        state_store=StateStore(tmp_path / "runtime-state.db"), input_factory=RuntimeInputFactory())
    deployment = Deployment(deployment_id="dep1", strategy_id="runtime_probe", version_no=1,
        params={}, account_id="account", mode=BrokerMode.PAPER, release_id=release.release_id)
    await runtime.start(deployment)
    assert (await runtime.status("dep1"))["status"] == "RUNNING"
    with pytest.raises(BacktestError, match="不接受调用方 input_snapshot"):
        await runtime.execute("dep1", decision_time=TEST_DECISION_TIME,
            input_snapshot={"arbitrary": "future"}, input_snapshot_ref="untrusted")
    result = await runtime.execute("dep1", decision_time=TEST_DECISION_TIME,
        input_snapshot_ref="input-persisted")
    assert result["protocol_version"] == 1
    replay = await runtime.execute("dep1", decision_time=TEST_DECISION_TIME,
        input_snapshot_ref="input-persisted")
    assert replay["input_snapshot_ref"] == result["input_snapshot_ref"]
    assert replay["input_hash"] == result["input_hash"]
    assert replay["lineage_hash"] == result["lineage_hash"]
    await runtime.stop("dep1")
    assert (await runtime.status("dep1"))["status"] == "STOPPED"


@pytest.mark.asyncio
async def test_runtime_rejects_illegal_intent_in_child(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "bad_intent", "code_ref": "local:probe",
        "code_content": "class Strategy:\n    def on_snapshot(self, ctx, snapshot):\n        ctx.emit_intent({'unexpected': 1})\n",
        "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")
    runtime = IsolatedStrategyRuntime(store, require_os_isolation=False,
        state_store=StateStore(tmp_path / "bad-state.db"), input_factory=RuntimeInputFactory())
    deployment = Deployment(deployment_id="bad-dep", strategy_id="bad_intent", version_no=1,
        params={}, account_id="account", mode=BrokerMode.PAPER, release_id=release.release_id)
    await runtime.start(deployment)
    with pytest.raises(BacktestError, match=r"策略进程故障|STRATEGY_FAILED|意图校验失败"):
        await runtime.execute("bad-dep", decision_time=datetime(2026, 1, 1, tzinfo=UTC),
            input_snapshot_ref="input-persisted")
    await runtime.stop("bad-dep")


@pytest.mark.asyncio
async def test_runtime_replay_revalidates_materialized_payload_provenance(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "replay_provenance", "code_ref": "local:probe",
        "code_content": "class Strategy:\n    def on_snapshot(self, ctx, snapshot):\n        return None\n",
        "params_schema": {"type": "object"},
    })
    release = await OfflineReleaseFixtureBuilder(ReleaseStore(db)).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")
    states = StateStore(tmp_path / "replay-state.db")
    factory = ReplayRejectingRuntimeInputFactory()
    runtime = IsolatedStrategyRuntime(ReleaseStore(db), require_os_isolation=False,
        state_store=states, input_factory=factory)
    deployment = Deployment(deployment_id="replay-dep", strategy_id="replay_provenance", version_no=1,
        params={}, account_id="account", mode=BrokerMode.PAPER, release_id=release.release_id)
    await runtime.start(deployment)
    try:
        first = await runtime.execute("replay-dep", decision_time=TEST_DECISION_TIME,
                                      input_snapshot_ref="input-persisted")
        with pytest.raises(BacktestError, match="DecisionInput artifact 校验失败"):
            await runtime.execute("replay-dep", decision_time=TEST_DECISION_TIME,
                                  input_snapshot_ref="input-persisted")
        committed = states.replay("replay-dep", "input-persisted")
        assert committed is not None and committed.input_hash == first["input_hash"]
        assert factory.calls == 2
    finally:
        await runtime.stop(deployment.deployment_id)


@pytest.mark.asyncio
async def test_release_read_rejects_parameter_tamper(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "tamper_probe", "code_ref": "local:probe",
        "code_content": "class Strategy: pass\n", "params_schema": {"type": "object", "properties": {"x": {"type": "integer"}}},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={"x": 1}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")
    parameters = store.archive_root / release.release_id / "parameters.json"
    parameters.chmod(0o644)
    parameters.write_text('{"x":2}', encoding="utf-8")
    parameters.chmod(0o444)
    with pytest.raises(DataError, match=r"hash|不匹配"):
        await store.get_release(release.release_id)


@pytest.mark.asyncio
async def test_decision_input_factory_persists_release_lineage_and_rejects_tamper(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "input_probe", "code_ref": "local:probe",
        "code_content": "class Strategy: pass\n", "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")
    factory = FormalDecisionInputFactory(tmp_path / "inputs", materializer=TrustedMaterializer())
    decision_input = await factory.materialize(
        release=release, decision_time=TEST_DECISION_TIME, instrument_id="BTC", interval="5m")
    assert (await factory.load_verified(
        release=release, input_snapshot_ref=decision_input.input_snapshot_ref)).lineage_hash == decision_input.lineage_hash
    path = tmp_path / "inputs" / f"{decision_input.input_snapshot_ref}.json"
    path.chmod(0o644)
    path.write_text(path.read_text(encoding="utf-8").replace('"close":"1"', '"close":"2"'), encoding="utf-8")
    path.chmod(0o444)
    with pytest.raises(ValueError, match="hash/lineage"):
        await factory.load_verified(release=release, input_snapshot_ref=decision_input.input_snapshot_ref)


@pytest.mark.asyncio
async def test_formal_materializer_rejects_raw_payload_and_untrusted_artifact(tmp_path: Path) -> None:
    release = SimpleNamespace(release_id="release-1", dataset_manifest_ids=("m1",), feature_set_ids=("f1",),
                              allowed_instruments=("BTC",), allowed_timeframes=("5m",))
    factory = FormalDecisionInputFactory(tmp_path / "inputs", materializer=TrustedMaterializer())
    assert not hasattr(factory, "create")
    with pytest.raises(TypeError):
        await factory.materialize(release=release, decision_time=TEST_DECISION_TIME, instrument_id="BTC",
                                  interval="5m", payload={"forged": True})
    with pytest.raises(ValueError, match="不存在"):
        await factory.load_verified(release=release, input_snapshot_ref="input-forged")


@pytest.mark.asyncio
async def test_formal_factory_rejects_self_consistent_forged_payload_artifact(tmp_path: Path) -> None:
    release = SimpleNamespace(release_id="release-1", dataset_manifest_ids=("m1",), feature_set_ids=("f1",),
                              allowed_instruments=("BTC",), allowed_timeframes=("5m",))
    factory = FormalDecisionInputFactory(tmp_path / "inputs", materializer=TrustedMaterializer())
    original = await factory.materialize(
        release=release, decision_time=TEST_DECISION_TIME, instrument_id="BTC", interval="5m")
    forged = DecisionInput(
        input_snapshot_ref=original.input_snapshot_ref, decision_time=original.decision_time,
        instrument_id=original.instrument_id, interval=original.interval,
        payload={"bar": {"instrument_id": "BTC", "close": "999999"},
                 "features": {"BTC": {"future_signal": 1.0}}, "supplemental": {}},
        source_manifest_ids=original.source_manifest_ids,
        feature_materialization_ids=original.feature_materialization_ids,
        available_at=original.available_at, source_manifest_hashes=original.source_manifest_hashes,
        feature_materialization_hashes=original.feature_materialization_hashes,
        source_row_hash=content_hash({"forged": True}), materializer_schema_version=original.materializer_schema_version,
        decision_request=original.decision_request)
    path = tmp_path / "inputs" / f"{original.input_snapshot_ref}.json"
    path.chmod(0o644)
    path.write_text(json.dumps(factory._document(release=release, decision_input=forged), sort_keys=True,
                               separators=(",", ":")), encoding="utf-8")
    path.chmod(0o444)
    with pytest.raises(ValueError, match="无法从可信 PIT source 重导"):
        await factory.load_verified(release=release, input_snapshot_ref=original.input_snapshot_ref)


def test_runtime_requires_durable_state_store(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="StateStore"):
        IsolatedStrategyRuntime(ReleaseStore(tmp_path / "meta.db"), require_os_isolation=False)


@pytest.mark.asyncio
async def test_runtime_default_fails_closed_when_network_namespace_unavailable(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "secure_probe", "code_ref": "local:probe",
        "code_content": "class Strategy: pass\n", "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test",
    )
    runtime = IsolatedStrategyRuntime(
        store, state_store=StateStore(tmp_path / "secure-state.db"), input_factory=RuntimeInputFactory())
    deployment = Deployment(deployment_id="secure1", strategy_id="secure_probe", version_no=1,
        params={}, account_id="account", mode=BrokerMode.PAPER, release_id=release.release_id)
    with pytest.raises(BacktestError, match="隔离能力不可用"):
        await runtime.start(deployment)


@pytest.mark.asyncio
async def test_runtime_rejects_strategy_initialize_error(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "initialize_error", "code_ref": "local:probe",
        "code_content": (
            "class Strategy:\n"
            "    def initialize(self, ctx):\n"
            "        raise RuntimeError('INITIALIZE_FAILED')\n"
        ),
        "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    release = await OfflineReleaseFixtureBuilder(store).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1",
        created_by="test",
    )
    runtime = IsolatedStrategyRuntime(
        store, require_os_isolation=False, state_store=StateStore(tmp_path / "initialize-state.db"),
        input_factory=RuntimeInputFactory(),
    )
    deployment = Deployment(
        deployment_id="initialize-error", strategy_id="initialize_error", version_no=1,
        params={}, account_id="account", mode=BrokerMode.PAPER, release_id=release.release_id,
    )

    await runtime.start(deployment)
    with pytest.raises(BacktestError, match="INITIALIZE_FAILED"):
        await runtime.execute(deployment.deployment_id, decision_time=TEST_DECISION_TIME,
                              input_snapshot_ref="input-persisted")
    await runtime.stop(deployment.deployment_id)
    assert await runtime.status(deployment.deployment_id) == {
        "status": "STOPPED", "deployment_id": deployment.deployment_id,
    }


@pytest.mark.asyncio
async def test_vectorized_engine_rejects_formal_pit_execution() -> None:
    engine = VectorizedBacktestEngine(SimpleNamespace(_research_requires_pit=True))
    request = {
        "strategy_id": "formal_probe",
        "version_no": 1,
        "params": {},
        "instruments": ["BTC"],
        "interval": "5m",
        "start": "2026-01-01T00:00:00+00:00",
        "end": "2026-01-02T00:00:00+00:00",
        "data_version": "data-v1",
        "feature_version": "feature-v1",
        "manifest_id": "manifest-v1",
        "release_id": "release-v1",
        "decision_time": "2026-01-02T00:00:00+00:00",
    }

    with pytest.raises(BacktestError, match="正式 PIT 回测只能使用 EventDriven"):
        await engine._execute_backtest(engine._parse_request(request), "run-v1")


def test_state_store_is_append_only_and_replay_idempotent(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.db")
    snapshot = CommittedState("dep", "rel", "input-1", 1, content_hash({"n": 1}),
        content_hash(None), {"n": 1}, input_hash="input-hash")
    assert store.commit(snapshot) == snapshot
    assert store.commit(snapshot) == snapshot
    assert store.replay("dep", "input-1") == snapshot
    with pytest.raises(DataError, match="内容冲突"):
        store.commit(CommittedState("dep", "rel", "input-1", 1, content_hash({"n": 1}), content_hash(None),
            {"n": 1}, input_hash="changed-input"))


def test_state_schema_is_registered_in_journal_contract(tmp_path: Path) -> None:
    StateStore(tmp_path / "journal-state.db")
    with sqlite3.connect(tmp_path / "journal-state.db") as conn:
        assert conn.execute("SELECT schema_version FROM schema_migrations WHERE migration_id='m2-v05-state'").fetchone() == (1,)
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='_journal_no_delete_state_commits'").fetchone() == (1,)


def test_state_prepare_crash_rolls_back_and_replay_keeps_intent(tmp_path: Path) -> None:
    calls = []
    def crash(stage: str) -> None:
        calls.append(stage)
        raise RuntimeError("injected crash")
    store = StateStore(tmp_path / "crash.db", fault_hook=crash)
    snapshot = CommittedState("dep", "rel", "input-1", 1, content_hash({"n": 1}),
        content_hash({"intent_id": "i1"}), {"n": 1}, input_hash="input-hash",
        event_id="evt_i1", intent={"intent_id": "i1"}, decision_time="2026-01-01T00:00:00+00:00")
    with pytest.raises(RuntimeError, match="injected crash"):
        store.commit(snapshot)
    assert calls == ["after_prepare"]
    assert store.replay("dep", "input-1") is None
    stable = StateStore(tmp_path / "crash.db")
    assert stable.commit(snapshot) == snapshot
    assert stable.replay("dep", "input-1").intent == {"intent_id": "i1"}


def test_formal_executor_uses_fixed_decision_input_and_sdk_intent_builder() -> None:
    executor = FormalStrategyExecutor(strategy_id="s", version_no=1, deployment_id="d",
        release_id="rel-1", params={}, instruments=("BTC",), interval="5m")
    strategy = type("Strategy", (), {
        "on_snapshot": lambda self, ctx, snapshot: ctx.emit_intent({
            "direction": "LONG", "action": "OPEN", "quantity": "1", "order_type": "MARKET",
            "instrument_id": "BTC"})
    })()
    decision_input = DecisionInput("input-1", datetime(2026, 1, 1, tzinfo=UTC), "BTC", "5m",
        {"value": 1}, ("manifest-1",), ("feature-1",), TEST_DECISION_TIME,
        (("manifest-1", "manifest-hash"),), (("feature-1", "feature-hash"),), "row-hash", "unit-v1",
        {"decision_time": TEST_DECISION_TIME.isoformat(), "instrument_id": "BTC", "interval": "5m"})
    result = executor.execute(strategy, decision_input, {},
        lambda ctx: strategy.on_snapshot(ctx, decision_input.payload))
    assert result["event"]["payload"]["intent"]["release_id"] == "rel-1"
    assert result["lineage_hash"] == decision_input.lineage_hash


@pytest.mark.asyncio
async def test_release_builder_requires_real_dependency_services(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "qualified_probe", "code_ref": "local:probe",
        "code_content": "class Strategy: pass\n", "params_schema": {"type": "object"},
    })
    store = ReleaseStore(db)
    with pytest.raises(DataError, match="formal ManifestStore"):
        await ReleaseBuilder(store).build(version, parameter_set={}, feature_set_ids=("missing",),
            dataset_manifest_ids=("missing",), cost_model_version="c1", environment_lock={},
            requested_capabilities=(), allowed_instruments=("BTC",), allowed_timeframes=("5m",),
            evidence_report_id="e1", created_by="test")


@pytest.mark.asyncio
async def test_release_builder_requires_feature_dataset_lineage_equality(tmp_path: Path) -> None:
    db = tmp_path / "meta.db"
    version = await StrategyRegistry(db).save_version({
        "strategy_id": "lineage_probe", "code_ref": "local:probe",
        "code_content": "class Strategy: pass\n", "params_schema": {"type": "object"},
    })

    class Manifests:
        def get(self, manifest_id):
            return SimpleNamespace(dataset_type="bars")
        def verify_pit_manifest(self, manifest, **kwargs):
            return manifest

    class Features:
        async def get_materialization(self, materialization_id):
            return SimpleNamespace(input_manifest_ids=("m1",), output_manifest_id="fout")

    store = ReleaseStore(db)
    release = await ReleaseBuilder(store, manifests=Manifests(), feature_registry=Features()).build(
        version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("m1",),
        cost_model_version="c1", environment_lock={}, requested_capabilities=(),
        allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")
    assert release.release_id.startswith("rel-")
    with pytest.raises(DataError, match="lineage"):
        await ReleaseBuilder(store, manifests=Manifests(), feature_registry=Features()).build(
            version, parameter_set={}, feature_set_ids=("f1",), dataset_manifest_ids=("other",),
            cost_model_version="c1", environment_lock={}, requested_capabilities=(),
            allowed_instruments=("BTC",), allowed_timeframes=("5m",), evidence_report_id="e1", created_by="test")


def test_release_store_reconciles_orphan_archive_on_restart(tmp_path: Path) -> None:
    archive_root = tmp_path / "releases"
    orphan = archive_root / "rel-orphan"
    orphan.mkdir(parents=True)
    (orphan / "code.py").write_text("orphan", encoding="utf-8")
    store = ReleaseStore(tmp_path / "meta.db", archive_root)
    assert not orphan.exists()
    assert store.reconcile_archives() == []
