#!/usr/bin/env python3
"""Build and exercise the persisted M2 formal-fixture evidence environment."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

FIXTURE_VERSION = "m2-v05-persisted-fixture-7"
INSTRUMENT = "binance-btc-usdt-perp"
INTERVAL = "5m"
DECISION_TIME = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
CLI_STRATEGY_ID = "m2_cli_fixture"
CLI_STATE_NAME = "cli-lifecycle-state.json"


def _root() -> Path:
    value = os.environ.get("QPS_M2_FIXTURE_DIR")
    return Path(value).resolve() if value else Path("/tmp/autoqps-m2-v05-persisted-fixture")


def _write_bars(data_dir: Path) -> Path:
    import polars as pl

    path = data_dir / "normalized" / INTERVAL / "btc-usdt-perp" / "fixture.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for index in range(12):
        opened = datetime(2026, 1, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=5 * index)
        rows.append({
            "instrument_id": INSTRUMENT,
            "open_time": opened.isoformat(),
            "interval": INTERVAL,
            "open": str(Decimal("100") + index),
            "high": str(Decimal("101") + index),
            "low": str(Decimal("99") + index),
            "close": str(Decimal("100.5") + index),
            "volume": "10",
            "quote_volume": "1000",
            "taker_buy_volume": "5",
            "trades_count": 10,
            "data_version": "m2-fixture",
            "quality": "OK",
            "event_time": opened.isoformat(),
            "available_at": (opened + timedelta(minutes=5)).isoformat(),
            "ingested_at": (opened + timedelta(minutes=5)).isoformat(),
            "close_time": (opened + timedelta(minutes=5)).isoformat(),
            "is_closed": True,
            "decision_time": (opened + timedelta(minutes=5)).isoformat(),
        })
    pl.DataFrame(rows).write_parquet(path)
    return path


def _write_supplemental(data_dir: Path, dataset: str) -> Path:
    import polars as pl

    path = data_dir / "normalized" / dataset / "btc-usdt-perp" / "fixture.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for index in range(12):
        ts = datetime(2026, 1, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=5 * index)
        common = {
            "instrument_id": INSTRUMENT, "interval": INTERVAL,
            "available_at": (ts + timedelta(minutes=5)).isoformat(),
            "ingested_at": (ts + timedelta(minutes=5)).isoformat(),
            "data_version": "m2-fixture", "event_time": ts.isoformat(),
        }
        if dataset == "funding":
            rows.append(common | {"funding_ts": ts.isoformat(), "rate": "0.0001",
                                  "settlement_ts": ts.isoformat()})
        else:
            rows.append(common | {"ts": ts.isoformat(), "mark_price": str(Decimal("100.5") + index)})
    pl.DataFrame(rows).write_parquet(path)
    return path


def _write_unknown(data_dir: Path) -> Path:
    import polars as pl

    path = data_dir / "normalized" / "unknown" / "btc-usdt-perp" / "fixture.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({
        "instrument_id": [INSTRUMENT], "ts": [datetime(2026, 1, 1, 10, 0, tzinfo=UTC).isoformat()],
        "oi_value": ["1"], "available_at": [DECISION_TIME.isoformat()],
        "ingested_at": [DECISION_TIME.isoformat()], "interval": [INTERVAL],
    }).write_parquet(path)
    return path


async def _build_fixture(root: Path) -> dict[str, str]:
    from qps.core.models.foundation import (
        FeatureDefinition,
        FeatureMaterialization,
        SourceReference,
    )
    from qps.data.features.engine import FeatureEngine, register_mvp_features
    from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
    from qps.data.provider import DataProvider
    from qps.infra.storage.meta_db import MetaDB
    from qps.research.registry import StrategyRegistry
    from qps.research.release import ReleaseBuilder, ReleaseStore

    data_dir = root / "data"
    marker = root / "fixture.json"
    if marker.is_file():
        saved = json.loads(marker.read_text(encoding="utf-8"))
        if saved.get("fixture_version") == FIXTURE_VERSION:
            return {key: str(value) for key, value in saved["ids"].items()}
    if root.exists():
        shutil.rmtree(root)
    data_dir.mkdir(parents=True)
    db = MetaDB(data_dir / "meta.db")
    db.initialize()
    manifests = ManifestStore(data_dir, db)
    bars_path = _write_bars(data_dir)
    funding_path = _write_supplemental(data_dir, "funding")
    mark_path = _write_supplemental(data_dir, "mark_price")
    unknown_path = _write_unknown(data_dir)
    candidate = manifests.build("normalized.bars", [str(bars_path)], [SourceReference(name="m2-fixture")], persist=False)
    quality_ref = manifests.write_quality_report(candidate, {
        "instrument_id": INSTRUMENT, "interval": INTERVAL, "overall_quality": "OK",
    })
    bars_manifest = manifests.build_formal_pit(
        "normalized.bars", [str(bars_path)], [SourceReference(name="m2-fixture")],
        instrument_id=INSTRUMENT, interval=INTERVAL, quality_report_ref=quality_ref,
    )
    supplemental_manifests = {}
    for dataset, path in (("funding", funding_path), ("mark_price", mark_path)):
        candidate = manifests.build(
            f"normalized.{dataset}", [str(path)], [SourceReference(name="m2-fixture")], persist=False)
        report = manifests.write_quality_report(candidate, {
            "instrument_id": INSTRUMENT, "interval": INTERVAL, "overall_quality": "OK",
        })
        supplemental_manifests[dataset] = manifests.build_formal_pit(
            f"normalized.{dataset}", [str(path)], [SourceReference(name="m2-fixture")],
            instrument_id=INSTRUMENT, interval=INTERVAL, quality_report_ref=report,
        )
    registry = PersistentFeatureRegistry(db, manifests)
    engine = FeatureEngine(
        features_dir=data_dir / "features", data_port=DataProvider(data_dir, db, allow_legacy=False),
        registry_port=registry, manifest_store=manifests, allow_legacy_compute=False,
    )
    register_mvp_features(engine)
    materialization = await engine.materialize(
        INSTRUMENT, INTERVAL, ["returns"], datetime(2026, 1, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 11, tzinfo=UTC), decision_time=DECISION_TIME,
        input_manifest_id=bars_manifest.manifest_id,
    )
    formal_definition = FeatureDefinition(
        feature_id="returns_formal", semantic_name="returns_formal",
        description="M2 typed-lineage fixture feature", formula_or_code_hash="m2-fixture-returns",
        input_schema={"dataset_type": "formal.pit"},
        source_types=("normalized.bars", "normalized.funding", "normalized.mark_price"),
        window=2, warmup=1, frequency=INTERVAL, available_at_rule="input_pit",
        owner="m2-external-runner", version=1, created_at=DECISION_TIME,
    )
    await registry.register_definition(formal_definition)
    formal_materialization = FeatureMaterialization(
        materialization_id=f"materialization-returns-formal-{materialization.output_manifest_id}",
        feature_id="returns_formal", feature_version=1,
        input_manifest_ids=(bars_manifest.manifest_id,
                            supplemental_manifests["funding"].manifest_id,
                            supplemental_manifests["mark_price"].manifest_id),
        output_manifest_id=materialization.output_manifest_id, coverage=Decimal("1"),
        quality_report_id="feature-engine-v0.5", computed_at=DECISION_TIME,
        content_hash=materialization.content_hash,
    )
    await registry.register_materialization(formal_materialization)
    code_content = (
        "from qps.core.sdk import BaseStrategy\n\n"
        "class Strategy(BaseStrategy):\n"
        "    def initialize(self, ctx):\n"
        "        pass\n"
        "    def on_bar(self, ctx, bar):\n"
        "        pass\n"
    )
    strategy_path = data_dir.parent / "strategies" / "m2_fixture" / "strategy.py"
    strategy_path.parent.mkdir(parents=True, exist_ok=True)
    strategy_path.write_text(code_content, encoding="utf-8")
    strategy = await StrategyRegistry(data_dir / "meta.db").save_version({
        "strategy_id": "m2_fixture", "code_ref": "local:m2_fixture",
        "code_content": code_content,
        "params_schema": {"type": "object"}, "feature_dependencies": ["returns"],
    })
    release = await ReleaseBuilder(
        ReleaseStore(data_dir / "meta.db"), manifests=manifests, feature_registry=registry,
    ).build(
        strategy, parameter_set={}, feature_set_ids=(formal_materialization.materialization_id,),
        dataset_manifest_ids=(bars_manifest.manifest_id,
                              supplemental_manifests["funding"].manifest_id,
                              supplemental_manifests["mark_price"].manifest_id), cost_model_version="m2-fixture",
        environment_lock={"fixture": FIXTURE_VERSION}, requested_capabilities=(),
        allowed_instruments=(INSTRUMENT,), allowed_timeframes=(INTERVAL,),
        evidence_report_id="m2-fixture-evidence", created_by="m2-external-runner",
    )

    async def build_missing_supplemental(name: str, dataset_ids: tuple[str, ...]) -> str:
        definition = FeatureDefinition(
            feature_id=f"returns_{name}", semantic_name=f"returns_{name}",
            description=f"M2 {name} negative fixture feature", formula_or_code_hash=f"m2-fixture-{name}",
            input_schema={"dataset_type": "formal.pit"},
            source_types=tuple(manifests.get(item).dataset_type for item in dataset_ids),
            window=2, warmup=1, frequency=INTERVAL, available_at_rule="input_pit",
            owner="m2-external-runner", version=1, created_at=DECISION_TIME,
        )
        await registry.register_definition(definition)
        materialized = FeatureMaterialization(
            materialization_id=f"materialization-{name}-{materialization.output_manifest_id}",
            feature_id=definition.feature_id, feature_version=1, input_manifest_ids=dataset_ids,
            output_manifest_id=materialization.output_manifest_id, coverage=Decimal("1"),
            quality_report_id="feature-engine-v0.5", computed_at=DECISION_TIME,
            content_hash=materialization.content_hash,
        )
        await registry.register_materialization(materialized)
        missing_release = await ReleaseBuilder(
            ReleaseStore(data_dir / "meta.db"), manifests=manifests, feature_registry=registry,
        ).build(
            strategy, parameter_set={}, feature_set_ids=(materialized.materialization_id,),
            dataset_manifest_ids=dataset_ids, cost_model_version=f"m2-fixture-{name}",
            environment_lock={"fixture": FIXTURE_VERSION, "negative": name}, requested_capabilities=(),
            allowed_instruments=(INSTRUMENT,), allowed_timeframes=(INTERVAL,),
            evidence_report_id=f"m2-fixture-{name}", created_by="m2-external-runner",
        )
        return missing_release.release_id

    missing_funding_release_id = await build_missing_supplemental(
        "missing-funding", (bars_manifest.manifest_id, supplemental_manifests["mark_price"].manifest_id),
    )
    missing_mark_release_id = await build_missing_supplemental(
        "missing-mark", (bars_manifest.manifest_id, supplemental_manifests["funding"].manifest_id),
    )

    async def build_variant(name: str, dataset_ids: tuple[str, ...]) -> str:
        definition = FeatureDefinition(
            feature_id=f"returns_{name}", semantic_name=f"returns_{name}",
            description=f"M2 {name} typed routing fixture", formula_or_code_hash=f"m2-fixture-{name}",
            input_schema={"dataset_type": "formal.pit"},
            source_types=tuple(manifests.get(item).dataset_type for item in dataset_ids),
            window=2, warmup=1, frequency=INTERVAL, available_at_rule="input_pit",
            owner="m2-external-runner", version=1, created_at=DECISION_TIME,
        )
        await registry.register_definition(definition)
        variant_materialization = FeatureMaterialization(
            materialization_id=f"materialization-{name}-{materialization.output_manifest_id}",
            feature_id=definition.feature_id, feature_version=1, input_manifest_ids=tuple(dict.fromkeys(dataset_ids)),
            output_manifest_id=materialization.output_manifest_id, coverage=Decimal("1"),
            quality_report_id="feature-engine-v0.5", computed_at=DECISION_TIME,
            content_hash=materialization.content_hash,
        )
        await registry.register_materialization(variant_materialization)
        variant = await ReleaseBuilder(
            ReleaseStore(data_dir / "meta.db"), manifests=manifests, feature_registry=registry,
        ).build(
            strategy, parameter_set={}, feature_set_ids=(variant_materialization.materialization_id,),
            dataset_manifest_ids=dataset_ids, cost_model_version=f"m2-fixture-{name}",
            environment_lock={"fixture": FIXTURE_VERSION, "variant": name}, requested_capabilities=(),
            allowed_instruments=(INSTRUMENT,), allowed_timeframes=(INTERVAL,),
            evidence_report_id=f"m2-fixture-{name}", created_by="m2-external-runner",
        )
        return variant.release_id

    unknown_candidate = manifests.build(
        "normalized.open_interest", [str(unknown_path)], [SourceReference(name="m2-fixture")], persist=False,
    )
    unknown_quality_ref = manifests.write_quality_report(unknown_candidate, {
        "instrument_id": INSTRUMENT, "interval": INTERVAL, "overall_quality": "OK",
    })
    unknown_manifest = unknown_candidate.model_copy(update={
        "schema_version": "v0.5-pit-formal", "quality_report_ref": unknown_quality_ref,
    })
    db.save_manifest(unknown_manifest)
    duplicate_funding_release_id = await build_variant(
        "duplicate-funding", (bars_manifest.manifest_id, supplemental_manifests["funding"].manifest_id,
                               supplemental_manifests["funding"].manifest_id, supplemental_manifests["mark_price"].manifest_id),
    )
    unknown_type_release_id = await build_variant(
        "unknown-type", (bars_manifest.manifest_id, supplemental_manifests["funding"].manifest_id,
                          supplemental_manifests["mark_price"].manifest_id, unknown_manifest.manifest_id),
    )
    ids = {
        "data_dir": str(data_dir), "bars_manifest_id": bars_manifest.manifest_id,
        "feature_materialization_id": formal_materialization.materialization_id,
        "feature_output_manifest_id": materialization.output_manifest_id,
        "funding_manifest_id": supplemental_manifests["funding"].manifest_id,
        "mark_price_manifest_id": supplemental_manifests["mark_price"].manifest_id,
        "missing_funding_release_id": missing_funding_release_id,
        "missing_mark_release_id": missing_mark_release_id,
        "duplicate_funding_release_id": duplicate_funding_release_id,
        "unknown_type_release_id": unknown_type_release_id,
        "release_id": release.release_id, "release_manifest_hash": release.manifest_hash,
    }
    marker.write_text(json.dumps({"fixture_version": FIXTURE_VERSION, "ids": ids}, sort_keys=True), encoding="utf-8")
    return ids


async def _formal_call_graph(ids: dict[str, str]) -> int:
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.release import ReleaseStore

    data_dir = Path(ids["data_dir"])
    config = Config(env_vars={"QPS_DATA_DIR": str(data_dir)})
    container = build_container(config)
    release_store = ReleaseStore(data_dir / "meta.db")
    container.register_port("release_store", release_store)
    result = await EventDrivenBacktestEngine(container).run({
        "strategy_id": "m2_fixture", "version_no": 1, "params": {}, "instruments": [INSTRUMENT],
        "interval": INTERVAL, "start": "2026-01-01T10:05:00+00:00", "end": "2026-01-01T11:00:00+00:00",
        "data_version": "m2-fixture", "feature_version": ids["feature_materialization_id"],
        "manifest_id": ids["bars_manifest_id"], "release_id": ids["release_id"],
        "decision_time": DECISION_TIME.isoformat(),
    })
    artifacts = list((data_dir / "decision-inputs").glob("*.json"))
    print(json.dumps({
        "fixture": ids, "backtest_status": result.status.value,
        "engine_config": result.engine_config,
        "decision_input_artifacts": [path.name for path in artifacts],
    }, default=str, sort_keys=True))
    return 0 if result.status.value == "SUCCEEDED" and artifacts else 1


async def _formal_required_supplemental(ids: dict[str, str]) -> int:
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.release import ReleaseStore

    data_dir = Path(ids["data_dir"])
    config = Config(env_vars={"QPS_DATA_DIR": str(data_dir)})
    container = build_container(config)
    container.register_port("release_store", ReleaseStore(data_dir / "meta.db"))
    base = {
        "strategy_id": "m2_fixture", "version_no": 1, "params": {}, "instruments": [INSTRUMENT],
        "interval": INTERVAL, "start": "2026-01-01T10:05:00+00:00", "end": "2026-01-01T11:00:00+00:00",
        "data_version": "m2-fixture", "feature_version": ids["feature_materialization_id"],
        "manifest_id": ids["bars_manifest_id"], "decision_time": DECISION_TIME.isoformat(),
    }
    results = []
    for release_id, expected in ((ids["missing_funding_release_id"], "REAL funding"),
                                 (ids["missing_mark_release_id"], "mark-price")):
        result = await EventDrivenBacktestEngine(container).run(base | {"release_id": release_id})
        results.append({"release_id": release_id, "status": result.status.value, "error": result.engine_config.get("error")})
        if result.status.value != "FAILED" or expected not in str(result.engine_config.get("error")):
            print(json.dumps({"results": results}, sort_keys=True))
            return 1
    print(json.dumps({"results": results, "status": "FAIL_CLOSED"}, sort_keys=True))
    return 0


async def _formal_typed_dataset_routing(ids: dict[str, str]) -> int:
    from qps.data.provider import DataProvider
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.release import ReleaseStore

    data_dir = Path(ids["data_dir"])
    container = build_container(Config(env_vars={"QPS_DATA_DIR": str(data_dir)}))
    container.register_port("release_store", ReleaseStore(data_dir / "meta.db"))
    delegate = container.get_port("data_provider")
    calls: list[dict[str, str]] = []

    class RecordingProvider(DataProvider):
        def __init__(self, wrapped):
            super().__init__(wrapped._data_dir, meta_db=wrapped._meta_db, allow_legacy=False)
            self._wrapped = wrapped

        async def query(self, instrument_id, start, end, *, decision_time, manifest_id):
            calls.append({"kind": "bars", "manifest_id": manifest_id})
            return await delegate.query(instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)

        async def query_funding_rates(self, instrument_id, start, end, *, decision_time, manifest_id):
            calls.append({"kind": "funding", "manifest_id": manifest_id})
            return await delegate.query_funding_rates(
                instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)

        async def query_mark_prices(self, instrument_id, start, end, *, decision_time, manifest_id):
            calls.append({"kind": "mark_price", "manifest_id": manifest_id})
            return await delegate.query_mark_prices(
                instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)

        async def query_features(self, instrument_id, names, start, end, *, decision_time, manifest_id):
            calls.append({"kind": "features", "manifest_id": manifest_id})
            return await delegate.query_features(
                instrument_id, names, start, end, decision_time=decision_time, manifest_id=manifest_id)

        def __getattr__(self, name):
            return getattr(delegate, name)

    container.replace_port("data_provider", RecordingProvider(delegate))
    request = {
        "strategy_id": "m2_fixture", "version_no": 1, "params": {}, "instruments": [INSTRUMENT],
        "interval": INTERVAL, "start": "2026-01-01T10:05:00+00:00", "end": "2026-01-01T11:00:00+00:00",
        "data_version": "m2-fixture", "feature_version": ids["feature_materialization_id"],
        "manifest_id": ids["bars_manifest_id"], "release_id": ids["release_id"],
        "decision_time": DECISION_TIME.isoformat(),
        "funding_manifest_id": ids["bars_manifest_id"],
        "mark_price_manifest_id": ids["bars_manifest_id"],
        "feature_manifest_id": ids["bars_manifest_id"],
    }
    result = await EventDrivenBacktestEngine(container).run(request)
    expected = {
        "bars": ids["bars_manifest_id"], "funding": ids["funding_manifest_id"],
        "mark_price": ids["mark_price_manifest_id"],
    }
    if result.status.value != "SUCCEEDED":
        print(json.dumps({"status": "FAILED", "error": result.engine_config.get("error")}, sort_keys=True))
        return 1
    for kind, manifest_id in expected.items():
        typed_calls = [call for call in calls if call["kind"] == kind]
        if not typed_calls or any(call["manifest_id"] != manifest_id for call in typed_calls):
            print(json.dumps({"status": "FAILED", "calls": calls}, sort_keys=True))
            return 1
    if any(call["manifest_id"] == ids["bars_manifest_id"] for call in calls if call["kind"] in {"funding", "mark_price", "features"}):
        print(json.dumps({"status": "FAILED", "reason": "bars manifest reused", "calls": calls}, sort_keys=True))
        return 1
    if result.engine_config["formal_feature_manifest_id"] != ids["feature_output_manifest_id"]:
        return 1

    async def expect_failed(release_id: str, altered: dict[str, object], text: str) -> dict[str, object]:
        failed = await EventDrivenBacktestEngine(container).run(request | {"release_id": release_id} | altered)
        error = str(failed.engine_config.get("error"))
        return {"release_id": release_id, "status": failed.status.value, "error": error,
                "passed": failed.status.value == "FAILED" and text in error}

    negative_cases = [
        (ids["duplicate_funding_release_id"], {}, "每种 funding 数据最多允许一个 manifest"),
        (ids["unknown_type_release_id"], {}, "不支持的正式回测数据类型"),
        (ids["release_id"], {"manifest_id": ids["funding_manifest_id"]}, "唯一的 formal bars manifest"),
        (ids["release_id"], {"instruments": ["other-instrument"]}, "quality report instrument_id"),
    ]
    negative_results = [
        await expect_failed(release_id, altered, expected_text)
        for release_id, altered, expected_text in negative_cases
    ]
    if not all(bool(item["passed"]) for item in negative_results):
        print(json.dumps({"status": "FAILED", "calls": calls, "negative_results": negative_results}, sort_keys=True))
        return 1
    print(json.dumps({"status": "TYPED_ROUTING_VERIFIED", "calls": calls,
                      "resolved": expected, "feature_output": ids["feature_output_manifest_id"],
                      "negative_cases": len(negative_cases)}, sort_keys=True))
    return 0


async def _formal_lineage(ids: dict[str, str]) -> int:
    from qps.data.manifest import ManifestStore
    from qps.infra.storage.meta_db import MetaDB
    from qps.research.release import ReleaseStore

    if await _formal_call_graph(ids) != 0:
        return 1
    data_dir = Path(ids["data_dir"])
    db = MetaDB(data_dir / "meta.db")
    manifests = ManifestStore(data_dir, db)
    release = await ReleaseStore(data_dir / "meta.db").get_release(ids["release_id"])
    artifacts = list((data_dir / "decision-inputs").glob("*.json"))
    expected_ids = list(release.dataset_manifest_ids)
    expected_hashes = {manifest_id: manifests.get(manifest_id).content_hash for manifest_id in expected_ids}
    checked = 0
    for artifact in artifacts:
        document = json.loads(artifact.read_text(encoding="utf-8"))
        if document["source_manifest_ids"] != expected_ids:
            return 1
        hashes = {item[0]: item[1] for item in document["source_manifest_hashes"]}
        if hashes != expected_hashes or document["feature_materialization_ids"] != list(release.feature_set_ids):
            return 1
        checked += 1
    print(json.dumps({"status": "LINEAGE_VERIFIED", "artifacts": checked,
                      "release_id": release.release_id, "source_manifest_hashes": expected_hashes}, sort_keys=True))
    return 0


async def _runtime_formal_payload_provenance(ids: dict[str, str]) -> int:
    """Exercise the same trusted materializer through Runtime and EventDriven."""
    from qps.core.constants import BrokerMode
    from qps.core.models.strategy import Deployment
    from qps.core.sdk.formal import DecisionInput, FormalDecisionInputFactory
    from qps.data.formal_materializer import FormalDecisionMaterializer
    from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
    from qps.data.provider import DataProvider
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.infra.storage.meta_db import MetaDB
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.release import ReleaseStore
    from qps.trading.runtime.process_runtime import IsolatedStrategyRuntime
    from qps.trading.runtime.state import StateStore

    data_dir = Path(ids["data_dir"])
    db = MetaDB(data_dir / "meta.db")
    manifests = ManifestStore(data_dir, db)
    registry = PersistentFeatureRegistry(db, manifests)
    release_store = ReleaseStore(data_dir / "meta.db")
    release = await release_store.get_release(ids["release_id"])
    factory = FormalDecisionInputFactory(
        data_dir / "provenance-inputs", materializer=FormalDecisionMaterializer(
            data_port=DataProvider(data_dir, db, allow_legacy=False), manifest_store=manifests,
            feature_registry=registry))
    runtime_input = await factory.materialize(
        release=release, decision_time=DECISION_TIME, instrument_id=INSTRUMENT, interval=INTERVAL)
    verified = await factory.load_verified(
        release=release, input_snapshot_ref=runtime_input.input_snapshot_ref)
    runtime = IsolatedStrategyRuntime(
        release_store, require_os_isolation=False, state_store=StateStore(data_dir / "provenance-state.db"),
        input_factory=factory)
    deployment = Deployment(deployment_id="m2-provenance", strategy_id="m2_fixture", version_no=1,
        params={}, account_id="m2-fixture", mode=BrokerMode.PAPER, release_id=release.release_id)
    await runtime.start(deployment)
    try:
        runtime_result = await runtime.execute(
            deployment.deployment_id, decision_time=DECISION_TIME,
            input_snapshot_ref=runtime_input.input_snapshot_ref)
    finally:
        await runtime.stop(deployment.deployment_id)
    container = build_container(Config(env_vars={"QPS_DATA_DIR": str(data_dir)}))
    container.register_port("release_store", release_store)
    backtest = await EventDrivenBacktestEngine(container).run({
        "strategy_id": "m2_fixture", "version_no": 1, "params": {}, "instruments": [INSTRUMENT],
        "interval": INTERVAL, "start": "2026-01-01T10:05:00+00:00", "end": "2026-01-01T11:00:00+00:00",
        "data_version": "m2-fixture", "feature_version": ids["feature_materialization_id"],
        "manifest_id": ids["bars_manifest_id"], "release_id": release.release_id,
        "decision_time": DECISION_TIME.isoformat(),
    })
    formal_document = json.loads((data_dir / "decision-inputs" / f"{runtime_input.input_snapshot_ref}.json").read_text())
    if backtest.status.value != "SUCCEEDED" or runtime_result["input_hash"] != verified.payload_hash \
            or formal_document["payload_hash"] != verified.payload_hash \
            or formal_document["source_row_hash"] != verified.source_row_hash:
        print(json.dumps({"status": "FAILED", "runtime": runtime_result, "backtest": backtest.status.value}, default=str))
        return 1
    forged = DecisionInput(
        input_snapshot_ref=runtime_input.input_snapshot_ref, decision_time=runtime_input.decision_time,
        instrument_id=runtime_input.instrument_id, interval=runtime_input.interval,
        payload={"bar": {"instrument_id": INSTRUMENT, "close": "999999"},
                 "features": {INSTRUMENT: {"future_signal": 1.0}}, "supplemental": {}},
        source_manifest_ids=runtime_input.source_manifest_ids,
        feature_materialization_ids=runtime_input.feature_materialization_ids,
        available_at=runtime_input.available_at, source_manifest_hashes=runtime_input.source_manifest_hashes,
        feature_materialization_hashes=runtime_input.feature_materialization_hashes,
        source_row_hash="forged-row-hash", materializer_schema_version=runtime_input.materializer_schema_version,
        decision_request=runtime_input.decision_request)
    artifact = data_dir / "provenance-inputs" / f"{runtime_input.input_snapshot_ref}.json"
    artifact.chmod(0o644)
    artifact.write_text(json.dumps(factory._document(release=release, decision_input=forged), sort_keys=True,
                                   separators=(",", ":")), encoding="utf-8")
    artifact.chmod(0o444)
    try:
        await factory.load_verified(release=release, input_snapshot_ref=runtime_input.input_snapshot_ref)
    except ValueError:
        rejected = True
    else:
        rejected = False
    print(json.dumps({"status": "PAYLOAD_PROVENANCE_VERIFIED", "runtime_ref": runtime_input.input_snapshot_ref,
                      "payload_hash": verified.payload_hash, "source_row_hash": verified.source_row_hash,
                      "forged_rejected": rejected}, sort_keys=True))
    return 0 if rejected else 1


def _cli_executable() -> str:
    candidate = Path(sys.executable).with_name("qps")
    return str(candidate) if candidate.exists() else "qps"


def _run_cli(root: Path, arguments: list[str]) -> dict[str, object]:
    """Run one formal CLI command in a fresh OS process."""
    data_dir = root / "data"
    workspace = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["QPS_DATA_DIR"] = str(data_dir)
    environment.pop("QPS_M2_FIXTURE_DIR", None)
    completed = subprocess.run(
        [_cli_executable(), *arguments], cwd=workspace, env=environment,
        capture_output=True, text=True, check=False, timeout=30,
    )
    return {
        "command": arguments,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _last_json(text: str) -> dict[str, object]:
    for line in reversed(text.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"CLI 没有输出 JSON object: {text[-500:]}")


def _release_names(data_dir: Path) -> list[str]:
    releases = data_dir / "releases"
    return sorted(path.name for path in releases.iterdir()) if releases.exists() else []


def _archive_fingerprint(data_dir: Path, release_id: str) -> dict[str, object]:
    archive = data_dir / "releases" / release_id
    manifest = json.loads((archive / "release-manifest.json").read_text(encoding="utf-8"))
    files = {}
    for path in sorted(archive.iterdir()):
        if path.name == "release-manifest.json":
            continue
        files[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"manifest_hash": manifest["manifest_hash"], "files": files}


def _approval_records(data_dir: Path, release_id: str) -> list[dict[str, str]]:
    with sqlite3.connect(data_dir / "meta.db") as connection:
        rows = connection.execute(
            "SELECT approval_id, release_id, status, approved_by, reason, created_at "
            "FROM approval_records WHERE release_id=? ORDER BY created_at, approval_id",
            (release_id,),
        ).fetchall()
    return [
        {"approval_id": row[0], "release_id": row[1], "status": row[2],
         "approved_by": row[3], "reason": row[4], "created_at": row[5]}
        for row in rows
    ]


def _audit_records(data_dir: Path, release_id: str) -> list[tuple[str, str]]:
    with sqlite3.connect(data_dir / "meta.db") as connection:
        return [
            (str(row[0]), str(row[1])) for row in connection.execute(
                "SELECT target_id, action FROM audit_log WHERE target_id=? ORDER BY ts, log_id",
                (release_id,),
            ).fetchall()
        ]


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _write_cli_state(root: Path, state: dict[str, object]) -> None:
    path = root / CLI_STATE_NAME
    document = dict(state)
    document["state_hash"] = _canonical_hash(document)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    temporary.chmod(0o600)
    os.replace(temporary, path)
    if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_uid != os.getuid():
        raise RuntimeError("CLI lifecycle state 权限或 owner 不符合要求")


def _read_cli_state(root: Path) -> dict[str, object]:
    path = root / CLI_STATE_NAME
    if not path.is_file():
        raise RuntimeError(f"缺少上一阶段 CLI lifecycle state: {path}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600 or path.stat().st_uid != os.getuid():
        raise RuntimeError("CLI lifecycle state owner/mode 不符合要求")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise RuntimeError("CLI lifecycle state 不是 JSON object")
    expected = document.pop("state_hash", None)
    if not isinstance(expected, str) or _canonical_hash(document) != expected:
        raise RuntimeError("CLI lifecycle state 内容 hash 不匹配")
    document["state_hash"] = expected
    if Path(str(document.get("root"))).resolve() != root.resolve():
        raise RuntimeError("CLI lifecycle state root 不匹配")
    return document


async def _cli_formal_lifecycle(ids: dict[str, str], root: Path) -> int:
    data_dir = Path(ids["data_dir"])
    params = json.dumps({"lookback": 50, "atr_mult": 2.0, "position_size": 0.1}, sort_keys=True)
    environment_lock = json.dumps({"fixture": FIXTURE_VERSION}, sort_keys=True)
    dataset_ids = ",".join((ids["bars_manifest_id"], ids["funding_manifest_id"], ids["mark_price_manifest_id"]))
    before_releases = _release_names(data_dir)
    commands: list[dict[str, object]] = []

    def run(arguments: list[str]) -> dict[str, object]:
        result = _run_cli(root, arguments)
        commands.append(result)
        return result

    create = run(["strategy", "create", CLI_STRATEGY_ID, "--template", "trend",
                  "--instruments", "BTC-USDT-PERP"])
    if create["exit_code"] != 0 or "版本号: 1" not in str(create["stdout"]):
        raise RuntimeError(f"CLI create 失败: {create}")
    check = run(["strategy", "check", CLI_STRATEGY_ID, "--version", "1"])
    if check["exit_code"] != 0 or _last_json(str(check["stdout"])).get("static_check") != "passed":
        raise RuntimeError(f"CLI check 失败: {check}")
    package = run(["strategy", "package", CLI_STRATEGY_ID, "--version", "1", "--confirm"])
    if package["exit_code"] != 0 or _last_json(str(package["stdout"])).get("status") != "READY":
        raise RuntimeError(f"CLI package 失败: {package}")

    failed = run(["strategy", "release", CLI_STRATEGY_ID, "--version", "1",
                  "--dataset-manifest", "missing-manifest", "--feature-set",
                  ids["feature_materialization_id"], "--instrument", INSTRUMENT,
                  "--timeframe", INTERVAL, "--params", params,
                  "--environment-lock", environment_lock, "--evidence-report",
                  "m2-cli-negative", "--confirm"])
    after_failed_releases = _release_names(data_dir)
    if failed["exit_code"] == 0 or after_failed_releases != before_releases:
        raise RuntimeError(f"CLI failed release left side effects: {failed}")

    release_command = run(["strategy", "release", CLI_STRATEGY_ID, "--version", "1",
                           "--dataset-manifest", dataset_ids, "--feature-set",
                           ids["feature_materialization_id"], "--instrument", INSTRUMENT,
                           "--timeframe", INTERVAL, "--params", params,
                           "--environment-lock", environment_lock, "--evidence-report",
                           "m2-cli-formal", "--confirm"])
    if release_command["exit_code"] != 0:
        raise RuntimeError(f"CLI release 失败: {release_command}")
    release_payload = _last_json(str(release_command["stdout"]))
    release_id = str(release_payload["release_id"])
    shown = run(["strategy", "show", release_id])
    if shown["exit_code"] != 0:
        raise RuntimeError(f"CLI show 失败: {shown}")
    shown_payload = _last_json(str(shown["stdout"]))
    archive = _archive_fingerprint(data_dir, release_id)
    if (shown_payload.get("release_id") != release_id
            or shown_payload.get("manifest_hash") != archive["manifest_hash"]
            or shown_payload.get("dataset_manifest_ids") != dataset_ids.split(",")
            or shown_payload.get("feature_set_ids") != [ids["feature_materialization_id"]]):
        raise RuntimeError(f"CLI show formal lineage/hash 不匹配: {shown_payload}")
    approved = run(["strategy", "approve", release_id, "--status", "APPROVED",
                    "--reason", "M2 formal lifecycle", "--actor", "m2-runner", "--confirm"])
    if approved["exit_code"] != 0:
        raise RuntimeError(f"CLI approve 失败: {approved}")
    approval_payload = _last_json(str(approved["stdout"]))
    approvals = _approval_records(data_dir, release_id)
    audits = _audit_records(data_dir, release_id)
    approval_id = str(approval_payload["approval_id"])
    if not any(item["approval_id"] == approval_id and item["status"] == "APPROVED" for item in approvals):
        raise RuntimeError("approval record 未持久化")
    if (release_id, "RELEASE_APPROVAL") not in audits:
        raise RuntimeError("approval audit 未持久化")
    _write_cli_state(root, {
        "root": str(root.resolve()), "data_dir": str(data_dir.resolve()),
        "release_id": release_id, "manifest_hash": archive["manifest_hash"],
        "archive": archive, "dataset_manifest_ids": dataset_ids.split(","),
        "feature_set_ids": [ids["feature_materialization_id"]],
        "approval_id": approval_id, "approval_count": len(approvals),
    })
    print(json.dumps({
        "status": "CLI_FORMAL_LIFECYCLE_VERIFIED", "release_id": release_id,
        "archive": archive, "approval_id": approval_id, "approval_audit": True,
        "failed_operation": {"exit_code": failed["exit_code"], "no_side_effect": True},
        "commands": [{"command": item["command"], "exit_code": item["exit_code"],
                       "stdout": str(item["stdout"])[-1000:], "stderr": str(item["stderr"])[-1000:]}
                      for item in commands],
    }, ensure_ascii=False, sort_keys=True))
    return 0


async def _cli_restart_visibility(root: Path) -> int:
    state = _read_cli_state(root)
    data_dir = Path(str(state["data_dir"]))
    release_id = str(state["release_id"])
    result = _run_cli(root, ["strategy", "show", release_id])
    if result["exit_code"] != 0:
        raise RuntimeError(f"restart 后 CLI show 失败: {result}")
    shown = _last_json(str(result["stdout"]))
    archive = _archive_fingerprint(data_dir, release_id)
    approvals = _approval_records(data_dir, release_id)
    audits = _audit_records(data_dir, release_id)
    if (shown.get("release_id") != release_id
            or shown.get("manifest_hash") != state["manifest_hash"]
            or shown.get("dataset_manifest_ids") != state["dataset_manifest_ids"]
            or shown.get("feature_set_ids") != state["feature_set_ids"]
            or archive != state["archive"]
            or not any(item["approval_id"] == state["approval_id"] for item in approvals)
            or (release_id, "RELEASE_APPROVAL") not in audits):
        raise RuntimeError(f"restart persisted visibility mismatch: {shown}")
    print(json.dumps({
        "status": "CLI_RESTART_VISIBILITY_VERIFIED", "release_id": release_id,
        "manifest_hash": shown["manifest_hash"], "archive": archive,
        "approval_id": state["approval_id"], "approval_audit": True,
        "new_process_exit_code": result["exit_code"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=[
        "m2-formal-backtest-call-graph", "m2-persisted-formal-lineage",
        "m2-formal-typed-dataset-routing", "m2-formal-required-supplemental",
        "m2-cli-formal-lifecycle", "m2-cli-restart-visibility", "m2-runtime-formal-payload-provenance",
    ])
    args = parser.parse_args()
    try:
        if args.command == "m2-cli-restart-visibility":
            return asyncio.run(_cli_restart_visibility(_root()))
        ids = asyncio.run(_build_fixture(_root()))
        if args.command == "m2-formal-backtest-call-graph":
            return asyncio.run(_formal_call_graph(ids))
        if args.command == "m2-formal-typed-dataset-routing":
            return asyncio.run(_formal_typed_dataset_routing(ids))
        if args.command == "m2-formal-required-supplemental":
            return asyncio.run(_formal_required_supplemental(ids))
        if args.command == "m2-persisted-formal-lineage":
            return asyncio.run(_formal_lineage(ids))
        if args.command == "m2-runtime-formal-payload-provenance":
            return asyncio.run(_runtime_formal_payload_provenance(ids))
        if args.command == "m2-cli-formal-lifecycle":
            return asyncio.run(_cli_formal_lifecycle(ids, _root()))
        raise RuntimeError(f"unsupported command: {args.command}")
    except Exception as exc:
        print(json.dumps({"command": args.command, "error_type": type(exc).__name__, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
