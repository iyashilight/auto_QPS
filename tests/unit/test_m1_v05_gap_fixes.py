"""M1 v0.5 PIT, immutable manifest and durable feature registry contracts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING

import polars as pl
import pytest

from qps.core.errors import DataError
from qps.core.models.foundation import (
    DatasetManifest,
    FeatureDefinition,
    InstrumentRuleVersion,
    SourceReference,
)
from qps.core.models.market import Bar
from qps.data.features.engine import FeatureEngine, register_mvp_features
from qps.data.health import DataFreshnessMonitor
from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
from qps.data.pipeline.normalizer import Normalizer, RawWriter
from qps.data.provider import DataProvider
from qps.infra.storage.meta_db import MetaDB
from qps.research.backtest.event_driven import EventDrivenBacktestEngine

if TYPE_CHECKING:
    from pathlib import Path


NOW = datetime(2026, 1, 1, 10, 5, tzinfo=UTC)


def _bar(*, available_at: datetime = NOW, closed: bool = True, close: str = "101") -> Bar:
    opened = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    ingested = max(available_at, NOW)
    return Bar(
        instrument_id="binance-btc-usdt-perp",
        open_time=opened,
        interval="5m",
        open=Decimal("100"), high=Decimal("102"), low=Decimal("99"), close=Decimal(close),
        volume=Decimal("10"), quote_volume=Decimal("1000"), data_version="batch-1",
        event_time=opened, available_at=available_at, ingested_at=ingested,
        close_time=opened + timedelta(minutes=5), is_closed=closed,
        decision_time=NOW,
    )


def _manifest_store(tmp_path: Path) -> tuple[MetaDB, ManifestStore]:
    db = MetaDB(tmp_path / "meta.db")
    db.initialize()
    return db, ManifestStore(tmp_path, db)


def _pit_manifest(
    manifests: ManifestStore, dataset_type: str, paths: list[str], sources: list[SourceReference], *,
    formal: bool = True,
) -> DatasetManifest:
    candidate = manifests.build(
        dataset_type, paths, sources, schema_version="v0.5-pit-formal", persist=False,
    )
    frame = pl.read_parquet(paths[0])
    instrument = str(frame["instrument_id"][0]) if "instrument_id" in frame.columns else "binance-btc-usdt-perp"
    interval = str(frame["interval"][0]) if "interval" in frame.columns else (
        "8h" if "funding" in dataset_type else "5m"
    )
    quality_ref = manifests.write_quality_report(candidate, {
        "instrument_id": instrument, "interval": interval, "overall_quality": "OK",
    })
    if not formal:
        return manifests.build(dataset_type, paths, sources, quality_report_ref=quality_ref)
    return manifests.build_formal_pit(
        dataset_type, paths, sources, instrument_id=instrument, interval=interval,
        quality_report_ref=quality_ref,
    )


def test_pit_query_filters_availability_and_unclosed_bars(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    path = tmp_path / "normalized" / "5m" / "btc-usdt-perp" / "batch.parquet"
    path.parent.mkdir(parents=True)
    # The explicit schema proves the old no-PIT format is not silently accepted.
    pl.DataFrame([
        {**_bar(available_at=NOW).model_dump(mode="json")},
        {**_bar(available_at=NOW, closed=False).model_dump(mode="json")},
    ]).write_parquet(path)
    manifest = _pit_manifest(manifests, "normalized.bars", [str(path)], [SourceReference(name="test")])
    provider = DataProvider(tmp_path, db)

    visible = asyncio.run(provider.query(
        "binance-btc-usdt-perp", datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 11, 0, tzinfo=UTC), decision_time=NOW,
        manifest_id=manifest.manifest_id,
    ))
    assert len(visible) == 1

    early = asyncio.run(provider.query(
        "binance-btc-usdt-perp", datetime(2026, 1, 1, 10, 0, tzinfo=UTC),
        datetime(2026, 1, 1, 11, 0, tzinfo=UTC),
        decision_time=NOW - timedelta(minutes=1), manifest_id=manifest.manifest_id,
    ))
    assert early == []


def test_pit_queries_filter_record_instrument_id(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db)

    bars_path = tmp_path / "bars.parquet"
    eth_bar = _bar().model_copy(update={"instrument_id": "binance-eth-usdt-perp"})
    pl.DataFrame([eth_bar.model_dump(mode="json")]).write_parquet(bars_path)
    bars_manifest = _pit_manifest(manifests, "normalized.bars", [str(bars_path)], [SourceReference(name="eth")])
    with pytest.raises(DataError, match="instrument"):
        asyncio.run(provider.query(
            "binance-btc-usdt-perp", NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=bars_manifest.manifest_id,
        ))

    supplemental = [
        ("normalized.funding", {"funding_ts": NOW.isoformat(), "rate": "0.001"}, "query_funding_rates"),
        ("normalized.open_interest", {"ts": NOW.isoformat(), "oi_value": "1"}, "query_open_interest"),
        ("normalized.mark_price", {"ts": NOW.isoformat(), "mark_price": "100"}, "query_mark_prices"),
    ]
    for dataset_type, row, method_name in supplemental:
        row.update({
            "instrument_id": "binance-eth-usdt-perp",
            "available_at": NOW.isoformat(),
            "ingested_at": NOW.isoformat(),
        })
        path = tmp_path / f"{dataset_type}-eth.parquet"
        pl.DataFrame([row]).write_parquet(path)
        manifest = _pit_manifest(manifests, dataset_type, [str(path)], [SourceReference(name="eth")])
        with pytest.raises(DataError, match="instrument"):
            asyncio.run(getattr(provider, method_name)(
                "binance-btc-usdt-perp", NOW - timedelta(hours=1), NOW + timedelta(hours=1),
                decision_time=NOW, manifest_id=manifest.manifest_id,
            ))

    feature_path = tmp_path / "features.parquet"
    pl.DataFrame([{
        "instrument_id": "binance-eth-usdt-perp", "ts": NOW.isoformat(),
        "feature_name": "returns", "value": "0.1", "feature_version": "v1",
        "available_at": NOW.isoformat(), "decision_time": NOW.isoformat(),
        "source_manifest_id": bars_manifest.manifest_id,
    }]).write_parquet(feature_path)
    feature_manifest = _pit_manifest(
        manifests, "features", [str(feature_path)],
        [SourceReference(name="input", reference=bars_manifest.manifest_id)],
    )
    with pytest.raises(DataError, match="instrument"):
        asyncio.run(provider.query_features(
            "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=feature_manifest.manifest_id,
        ))


def test_pit_query_rejects_malformed_timestamp_and_invalid_ingestion_order(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    path = tmp_path / "bars-invalid.parquet"
    row = _bar().model_dump(mode="json")
    row["event_time"] = "not-a-timestamp"
    pl.DataFrame([row]).write_parquet(path)
    manifest = _pit_manifest(
        manifests, "normalized.bars", [str(path)], [SourceReference(name="invalid")], formal=False,
    )
    provider = DataProvider(tmp_path, db)
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(provider.query(
            "binance-btc-usdt-perp", NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=manifest.manifest_id,
        ))

    supplemental_path = tmp_path / "funding-invalid-order.parquet"
    pl.DataFrame([{
        "instrument_id": "binance-btc-usdt-perp", "funding_ts": NOW.isoformat(), "rate": "0.001",
        "available_at": NOW.isoformat(), "ingested_at": (NOW - timedelta(minutes=1)).isoformat(),
    }]).write_parquet(supplemental_path)
    supplemental_manifest = _pit_manifest(
        manifests, "normalized.funding", [str(supplemental_path)], [SourceReference(name="invalid-order")],
        formal=False,
    )
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(provider.query_funding_rates(
            "binance-btc-usdt-perp", NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=supplemental_manifest.manifest_id,
        ))


@pytest.mark.parametrize(
    "method_name",
    ["query", "query_funding_rates", "query_open_interest", "query_mark_prices", "query_features"],
)
def test_all_pit_queries_reject_invalid_windows(tmp_path: Path, method_name: str) -> None:
    provider = DataProvider(tmp_path, MetaDB(tmp_path / "meta.db"))
    method = getattr(provider, method_name)
    kwargs = {"decision_time": NOW, "manifest_id": "missing"}
    if method_name == "query_features":
        args = ("binance-btc-usdt-perp", [], NOW, NOW)
    elif method_name == "query":
        args = ("binance-btc-usdt-perp", NOW, NOW)
    else:
        args = ("binance-btc-usdt-perp", NOW, NOW)
    with pytest.raises(DataError, match="start 必须早于 end"):
        asyncio.run(method(*args, **kwargs))


def test_manifest_semantic_validation_rejects_missing_quality_and_wrong_instrument(tmp_path: Path) -> None:
    _db, manifests = _manifest_store(tmp_path)
    path = tmp_path / "eth-bars.parquet"
    pl.DataFrame([_bar().model_copy(update={"instrument_id": "binance-eth-usdt-perp"}).model_dump(mode="json")]).write_parquet(path)
    no_quality = manifests.build(
        "normalized.bars", [str(path)], [SourceReference(name="eth")], persist=False,
    )
    with pytest.raises(DataError, match="quality_report"):
        manifests.verify_pit_manifest(
            no_quality, expected_dataset_types={"normalized.bars"}, instrument_id="binance-btc-usdt-perp",
        )
    valid = _pit_manifest(manifests, "normalized.bars", [str(path)], [SourceReference(name="eth")])
    with pytest.raises(DataError, match="instrument"):
        manifests.verify_pit_manifest(
            valid, expected_dataset_types={"normalized.bars"}, instrument_id="binance-btc-usdt-perp",
        )


def test_feature_source_manifest_is_required_and_verified(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db)
    source_path = tmp_path / "bars.parquet"
    pl.DataFrame([_bar().model_dump(mode="json")]).write_parquet(source_path)
    source = _pit_manifest(manifests, "normalized.bars", [str(source_path)], [SourceReference(name="bars")])
    feature_path = tmp_path / "features.parquet"
    pl.DataFrame([{
        "instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(), "feature_name": "returns",
        "value": "0.1", "feature_version": "v1", "available_at": NOW.isoformat(),
        "decision_time": NOW.isoformat(), "source_manifest_id": None,
    }]).write_parquet(feature_path)
    feature = _pit_manifest(
        manifests, "features", [str(feature_path)],
        [SourceReference(name="input", reference=source.manifest_id)], formal=False,
    )
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(provider.query_features(
            "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=feature.manifest_id,
        ))

    valid_feature_path = tmp_path / "features-valid.parquet"
    pl.DataFrame([{
        "instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(), "feature_name": "returns",
        "value": "0.1", "feature_version": "v1", "available_at": NOW.isoformat(),
        "decision_time": NOW.isoformat(), "source_manifest_id": source.manifest_id,
    }]).write_parquet(valid_feature_path)
    valid_feature = _pit_manifest(
        manifests, "features", [str(valid_feature_path)],
        [SourceReference(name="input", reference=source.manifest_id)],
    )
    assert len(asyncio.run(provider.query_features(
        "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
        decision_time=NOW, manifest_id=valid_feature.manifest_id,
    ))) == 1
    source_path.write_bytes(source_path.read_bytes() + b"tamper")
    with pytest.raises(DataError, match="hash"):
        asyncio.run(provider.query_features(
            "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=valid_feature.manifest_id,
        ))


@pytest.mark.parametrize(
    ("decision_value", "message"),
    [
        (NOW + timedelta(minutes=1), "晚于请求"),
        ("2026-01-01T10:05:00", "UTC"),
        ("2026-01-01T18:05:00+08:00", "UTC"),
        (None, "无效"),
    ],
)
def test_feature_decision_time_is_formal_pit_boundary(
    tmp_path: Path, decision_value: datetime | str | None, message: str,
) -> None:
    db, manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db, allow_legacy=False)
    source_path = tmp_path / "source-bars.parquet"
    pl.DataFrame([_bar().model_dump(mode="json")]).write_parquet(source_path)
    source = _pit_manifest(manifests, "normalized.bars", [str(source_path)], [SourceReference(name="bars")])
    feature_path = tmp_path / "feature-decision-time.parquet"
    raw_decision = decision_value.isoformat() if isinstance(decision_value, datetime) else decision_value
    pl.DataFrame([{
        "instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(),
        "feature_name": "returns", "value": "0.1", "feature_version": "v1",
        "available_at": NOW.isoformat(), "decision_time": raw_decision,
        "source_manifest_id": source.manifest_id,
    }]).write_parquet(feature_path)
    feature = _pit_manifest(
        manifests, "features", [str(feature_path)],
        [SourceReference(name="input", reference=source.manifest_id)], formal=False,
    )
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(provider.query_features(
            "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=feature.manifest_id,
        ))


def test_feature_query_preserves_row_decision_time_and_formal_build_rejects_invalid_pit(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    source_path = tmp_path / "source-bars.parquet"
    pl.DataFrame([_bar().model_dump(mode="json")]).write_parquet(source_path)
    source = _pit_manifest(manifests, "normalized.bars", [str(source_path)], [SourceReference(name="bars")])
    feature_path = tmp_path / "feature-valid.parquet"
    row_decision = NOW + timedelta(minutes=2)
    pl.DataFrame([{
        "instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(),
        "feature_name": "returns", "value": "0.1", "feature_version": "v1",
        "available_at": NOW.isoformat(), "decision_time": row_decision.isoformat(),
        "source_manifest_id": source.manifest_id,
    }]).write_parquet(feature_path)
    candidate = manifests.build(
        "features", [str(feature_path)], [SourceReference(name="input", reference=source.manifest_id)], persist=False,
    )
    quality_ref = manifests.write_quality_report(candidate, {
        "instrument_id": "binance-btc-usdt-perp", "interval": "5m", "overall_quality": "OK",
    })
    formal = manifests.build_formal_pit(
        "features", [str(feature_path)], [SourceReference(name="input", reference=source.manifest_id)],
        instrument_id="binance-btc-usdt-perp", interval="5m", quality_report_ref=quality_ref,
    )
    values = asyncio.run(DataProvider(tmp_path, db, allow_legacy=False).query_features(
        "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW + timedelta(hours=1),
        decision_time=row_decision, manifest_id=formal.manifest_id,
    ))
    assert values[0].decision_time == row_decision

    invalid_path = tmp_path / "feature-missing-decision-time.parquet"
    pl.DataFrame([{"instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat()}]).write_parquet(invalid_path)
    invalid_candidate = manifests.build(
        "features", [str(invalid_path)], [SourceReference(name="input", reference=source.manifest_id)], persist=False,
    )
    invalid_quality = manifests.write_quality_report(invalid_candidate, {
        "instrument_id": "binance-btc-usdt-perp", "interval": "5m", "overall_quality": "OK",
    })
    with pytest.raises(DataError, match="PIT partition"):
        manifests.build_formal_pit(
            "features", [str(invalid_path)], [SourceReference(name="input", reference=source.manifest_id)],
            instrument_id="binance-btc-usdt-perp", interval="5m", quality_report_ref=invalid_quality,
        )


def test_old_parquet_and_manifest_tamper_fail(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    old_path = tmp_path / "old.parquet"
    pl.DataFrame([{"open_time": "2026-01-01T10:00:00+00:00", "close": "1"}]).write_parquet(old_path)
    manifest = _pit_manifest(
        manifests, "normalized.bars", [str(old_path)], [SourceReference(name="test")], formal=False,
    )
    provider = DataProvider(tmp_path, db)
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(provider.query(
            "binance-btc-usdt-perp", datetime(2026, 1, 1, 10, tzinfo=UTC),
            datetime(2026, 1, 1, 11, tzinfo=UTC), decision_time=NOW,
            manifest_id=manifest.manifest_id,
        ))
    old_path.write_bytes(old_path.read_bytes() + b"tamper")
    with pytest.raises(DataError, match="hash"):
        manifests.verify(manifest)


def test_raw_and_normalized_revisions_do_not_overwrite(tmp_path: Path) -> None:
    bar = _bar()
    raw = RawWriter(tmp_path / "raw")
    first = raw.write_bars("btcusdt-perp", [bar], batch_id="one")
    before = first[0].read_bytes()
    second = raw.write_bars("btcusdt-perp", [_bar(close="103")], batch_id="two")
    assert second[0] != first[0]
    assert first[0].read_bytes() == before

    normalizer = Normalizer(tmp_path / "normalized")
    first_normalized, _ = normalizer.normalize_bars([bar], bar.instrument_id, "5m")
    first_path = normalizer.write_normalized_bars(first_normalized, bar.instrument_id, "5m")
    before_normalized = first_path.read_bytes()
    second_normalized, _ = normalizer.normalize_bars([_bar(close="103")], bar.instrument_id, "5m")
    second_path = normalizer.write_normalized_bars(second_normalized, bar.instrument_id, "5m")
    assert second_path != first_path
    assert first_path.read_bytes() == before_normalized


def test_feature_registry_survives_restart_and_materialization_is_lineage_bound(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    registry = PersistentFeatureRegistry(db, manifests)
    definition = FeatureDefinition(
        feature_id="returns", semantic_name="returns", description="returns",
        formula_or_code_hash="code-1", input_schema={"close": "Decimal"},
        source_types=("normalized.bars",), window=2, warmup=1, frequency="5m",
        available_at_rule="input_pit", owner="test", version=1, created_at=NOW,
    )
    asyncio.run(registry.register_definition(definition))
    asyncio.run(registry.register_definition(definition))
    assert asyncio.run(PersistentFeatureRegistry(db, manifests).get_definition("returns", 1)) == definition

    with pytest.raises(DataError, match="不存在"):
        asyncio.run(registry.register_materialization(
            # output/input manifest validation happens before persistence
            __import__("qps.core.models.foundation", fromlist=["FeatureMaterialization"]).FeatureMaterialization(
                materialization_id="m1", feature_id="returns", feature_version=1,
                input_manifest_ids=("missing",), output_manifest_id="missing", coverage=Decimal("1"),
                computed_at=NOW, content_hash="hash",
            )
        ))


def test_feature_engine_compute_requires_pit_port(tmp_path: Path) -> None:
    engine = FeatureEngine(features_dir=tmp_path / "features")
    register_mvp_features(engine)
    with pytest.raises(DataError, match="PointInTimeDataPort"):
        asyncio.run(engine.compute(
            "binance-btc-usdt-perp", "5m", ["returns"],
            datetime(2026, 1, 1, 10, tzinfo=UTC), NOW,
            decision_time=NOW, manifest_id="manifest-1",
        ))


def test_historical_rule_and_data_stale_recovery(tmp_path: Path) -> None:
    db, _manifests = _manifest_store(tmp_path)
    db.save_instrument_rule(InstrumentRuleVersion(
        rule_version_id="rule-1", instrument_id="binance-btc-usdt-perp", rules_hash="h1",
        valid_from=datetime(2026, 1, 1, tzinfo=UTC), valid_to=datetime(2026, 2, 1, tzinfo=UTC),
        available_at=datetime(2026, 1, 2, tzinfo=UTC), created_at=datetime(2026, 1, 2, tzinfo=UTC),
        tick_size=Decimal("0.1"), lot_size=Decimal("0.001"), min_notional=Decimal("5"),
    ))
    provider = DataProvider(tmp_path, db)
    with pytest.raises(DataError, match="历史 InstrumentRule"):
        asyncio.run(provider.get_instrument_rule(
            "binance-btc-usdt-perp", effective_at=datetime(2026, 1, 1, tzinfo=UTC),
            known_at=datetime(2026, 1, 1, tzinfo=UTC),
        ))
    rule = asyncio.run(provider.get_instrument_rule(
        "binance-btc-usdt-perp", effective_at=datetime(2026, 1, 1, tzinfo=UTC),
        known_at=datetime(2026, 1, 2, tzinfo=UTC),
    ))
    assert rule.tick_size == Decimal("0.1")

    events = []
    monitor = DataFreshnessMonitor(timedelta(minutes=5), events.append)
    stale = monitor.check("bars:btc", NOW - timedelta(minutes=6), now=NOW)
    recovered = monitor.check("bars:btc", NOW, now=NOW)
    assert stale is not None and stale.event_type.value == "DATA_STALE"
    assert recovered is not None and recovered.event_type.value == "DATA_RECOVERED"
    assert monitor.allows_decision("bars:btc")
    assert [event.event_type.value for event in events] == ["DATA_STALE", "DATA_RECOVERED"]


def test_local_collect_manifest_pit_materialize_lineage(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    rows = []
    for index, close in enumerate(("101", "102")):
        bar = _bar(close=close).model_copy(update={
            "open_time": datetime(2026, 1, 1, 10, index * 5, tzinfo=UTC),
            "event_time": datetime(2026, 1, 1, 10, index * 5, tzinfo=UTC),
            "close_time": datetime(2026, 1, 1, 10, (index + 1) * 5, tzinfo=UTC),
        })
        rows.append(bar.model_dump(mode="json"))
    path = tmp_path / "normalized" / "5m" / "btc-usdt-perp" / "batch.parquet"
    path.parent.mkdir(parents=True)
    pl.DataFrame(rows).write_parquet(path)
    input_manifest = _pit_manifest(manifests, "normalized.bars", [str(path)], [SourceReference(name="mock")])

    provider = DataProvider(tmp_path, db)
    registry = PersistentFeatureRegistry(db, manifests)
    engine = FeatureEngine(
        features_dir=tmp_path / "features", data_port=provider,
        registry_port=registry, manifest_store=manifests,
    )
    register_mvp_features(engine)
    materialize_time = NOW + timedelta(minutes=7)
    queried = asyncio.run(provider.query(
        "binance-btc-usdt-perp", datetime(2026, 1, 1, 10, tzinfo=UTC),
        datetime(2026, 1, 1, 12, tzinfo=UTC), decision_time=materialize_time,
        manifest_id=input_manifest.manifest_id,
    ))
    assert len(queried) == 2
    materialization = asyncio.run(engine.materialize(
        "binance-btc-usdt-perp", "5m", ["returns"],
        datetime(2026, 1, 1, 10, tzinfo=UTC), datetime(2026, 1, 1, 12, tzinfo=UTC),
        decision_time=materialize_time, input_manifest_id=input_manifest.manifest_id,
    ))
    assert asyncio.run(registry.get_materialization(materialization.materialization_id)) == materialization
    assert materialization.input_manifest_ids == (input_manifest.manifest_id,)
    assert manifests.verify(materialization.output_manifest_id).content_hash == materialization.content_hash


def test_formal_provider_rejects_legacy_reads(tmp_path: Path) -> None:
    db, _manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db, allow_legacy=False)
    with pytest.raises(DataError, match="legacy"):
        asyncio.run(provider.get_bars(
            "binance-btc-usdt-perp", "5m", NOW - timedelta(hours=1), NOW,
        ))
    with pytest.raises(DataError, match="legacy"):
        asyncio.run(provider.get_features(
            "binance-btc-usdt-perp", ["returns"], NOW - timedelta(hours=1), NOW,
        ))
    engine = FeatureEngine(features_dir=tmp_path / "features", allow_legacy_compute=False)
    register_mvp_features(engine)
    with pytest.raises(DataError, match="compute_with_bars"):
        engine.compute_with_bars("binance-btc-usdt-perp", "5m", ["returns"], [_bar()])


@pytest.mark.parametrize(
    ("dataset_type", "row", "method_name", "message"),
    [
    ("normalized.funding", {"funding_ts": NOW.isoformat(), "rate": "0.001"}, "query_funding_rates", "PIT"),
    ("normalized.open_interest", {"ts": NOW.isoformat(), "oi_value": "1"}, "query_open_interest", "PIT"),
    ("normalized.mark_price", {"ts": NOW.isoformat(), "mark_price": "100"}, "query_mark_prices", "PIT"),
    ],
)
def test_supplemental_old_format_is_typed_rejection(
    tmp_path: Path, dataset_type: str, row: dict[str, str], method_name: str, message: str,
) -> None:
    db, manifests = _manifest_store(tmp_path)
    path = tmp_path / f"{dataset_type}.parquet"
    pl.DataFrame([row]).write_parquet(path)
    manifest = _pit_manifest(
        manifests, dataset_type, [str(path)], [SourceReference(name="old")], formal=False,
    )
    provider = DataProvider(tmp_path, db, allow_legacy=False)
    query = getattr(provider, method_name)
    with pytest.raises(DataError, match="candidate manifest"):
        asyncio.run(query(
            "binance-btc-usdt-perp", NOW - timedelta(hours=1), NOW + timedelta(hours=1),
            decision_time=NOW, manifest_id=manifest.manifest_id,
        ))


def test_supplemental_pit_queries_filter_available_at(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db, allow_legacy=False)
    specs = [
        ("normalized.funding", "funding_ts", {"instrument_id": "binance-btc-usdt-perp", "funding_ts": NOW.isoformat(), "rate": "0.001", "settlement_ts": NOW.isoformat()}),
        ("normalized.open_interest", "ts", {"instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(), "oi_value": "1"}),
        ("normalized.mark_price", "ts", {"instrument_id": "binance-btc-usdt-perp", "ts": NOW.isoformat(), "mark_price": "100"}),
    ]
    for dataset_type, _time_field, row in specs:
        path = tmp_path / f"{dataset_type}-pit.parquet"
        row.update({"available_at": (NOW - timedelta(minutes=1)).isoformat(), "ingested_at": NOW.isoformat()})
        pl.DataFrame([row]).write_parquet(path)
        manifest = _pit_manifest(manifests, dataset_type, [str(path)], [SourceReference(name="pit")])
        method = {
            "normalized.funding": provider.query_funding_rates,
            "normalized.open_interest": provider.query_open_interest,
            "normalized.mark_price": provider.query_mark_prices,
        }[dataset_type]
        result = asyncio.run(method(
            "binance-btc-usdt-perp", NOW - timedelta(minutes=1), NOW + timedelta(minutes=1),
            decision_time=NOW, manifest_id=manifest.manifest_id,
        ))
        assert len(result) == 1


def test_multi_feature_materialization_and_failure_cleanup(tmp_path: Path) -> None:
    db, manifests = _manifest_store(tmp_path)
    path = tmp_path / "normalized" / "5m" / "btc-usdt-perp" / "batch.parquet"
    path.parent.mkdir(parents=True)
    rows = []
    first_ts = datetime(2026, 1, 1, 10, tzinfo=UTC)
    for index in range(12):
        ts = datetime(2026, 1, 1, 10, index * 5, tzinfo=UTC)
        rows.append(_bar().model_copy(update={
            "open_time": ts, "event_time": ts, "close_time": ts + timedelta(minutes=5),
            "available_at": ts + timedelta(minutes=5),
            "ingested_at": ts + timedelta(minutes=5),
            "decision_time": NOW + timedelta(hours=2),
            "close": Decimal(str(100 + index)),
        }).model_dump(mode="json"))
    pl.DataFrame(rows).write_parquet(path)
    input_manifest = _pit_manifest(manifests, "normalized.bars", [str(path)], [SourceReference(name="mock")])
    provider = DataProvider(tmp_path, db)
    registry = PersistentFeatureRegistry(db, manifests)
    engine = FeatureEngine(
        features_dir=tmp_path / "features", data_port=provider,
        registry_port=registry, manifest_store=manifests,
    )
    register_mvp_features(engine)
    materialization = asyncio.run(engine.materialize(
        "binance-btc-usdt-perp", "5m", ["returns", "volume_change"],
        first_ts, NOW + timedelta(hours=2),
        decision_time=NOW + timedelta(hours=2), input_manifest_id=input_manifest.manifest_id,
    ))
    persisted = asyncio.run(registry.list_materializations())
    assert {item.feature_id for item in persisted} == {"returns", "volume_change"}
    assert materialization.output_manifest_id == persisted[0].output_manifest_id

    async def fail_batch(*_args: object) -> None:
        raise DataError(detail="injected registry failure")

    registry.register_materialization_batch = fail_batch  # type: ignore[method-assign]
    with pytest.raises(DataError, match="injected"):
        asyncio.run(engine.materialize(
            "binance-btc-usdt-perp", "5m", ["returns"],
            first_ts, NOW + timedelta(hours=2),
            decision_time=NOW + timedelta(hours=2), input_manifest_id=input_manifest.manifest_id,
        ))
    assert len(db.list_manifests("features")) == 1


def test_freshness_state_survives_restart(tmp_path: Path) -> None:
    db, _manifests = _manifest_store(tmp_path)
    events = []
    monitor = DataFreshnessMonitor(timedelta(minutes=5), events.append, meta_db=db)
    monitor.check("bars:btc", NOW - timedelta(minutes=6), now=NOW)
    restarted = DataFreshnessMonitor(timedelta(minutes=5), meta_db=db)
    assert not restarted.allows_decision("bars:btc")
    recovered = restarted.check("bars:btc", NOW, now=NOW)
    assert recovered is not None and recovered.event_type.value == "DATA_RECOVERED"
    assert DataFreshnessMonitor(timedelta(minutes=5), meta_db=db).allows_decision("bars:btc")


def test_formal_backtest_without_manifest_fails_before_legacy_provider_call(tmp_path: Path) -> None:
    from unittest.mock import MagicMock

    from qps.data.provider import DataProvider
    from qps.research.experiments import ExperimentStore

    db, _manifests = _manifest_store(tmp_path)
    provider = DataProvider(tmp_path, db, allow_legacy=False)
    container = MagicMock()
    container._research_requires_pit = True
    container.get_port.side_effect = lambda name: {
        "data_provider": provider, "experiment_store": ExperimentStore(db, tmp_path),
    }[name]
    result = asyncio.run(EventDrivenBacktestEngine(container).run({
        "strategy_id": "s", "version_no": 1, "instruments": ["binance-btc-usdt-perp"],
        "interval": "5m", "start": NOW.isoformat(), "end": (NOW + timedelta(hours=1)).isoformat(),
        "data_version": "v1", "feature_version": "v1",
    }))
    assert result.status.value == "FAILED"
    assert "manifest_id" in str(result.engine_config["error"])
