"""Trusted, Release-derived PIT DecisionInput materialization."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast

from qps.core.errors import DataError
from qps.core.sdk.formal import VerifiedDecisionMaterialization, content_hash

_BARS_TYPES = {"normalized.bars", "bars", "feature_input.bars"}
_FEATURE_TYPES = {"features", "feature.output", "normalized.features"}
_SUPPLEMENTAL_TYPES = {
    "normalized.funding": "funding",
    "funding": "funding",
    "normalized.mark_price": "mark_price",
    "mark_price": "mark_price",
    "normalized.open_interest": "open_interest",
    "open_interest": "open_interest",
}
_MATERIALIZER_SCHEMA = "formal-pit-materializer-v1"


def _interval_delta(interval: str) -> timedelta:
    try:
        value, unit = int(interval[:-1]), interval[-1]
    except (TypeError, ValueError) as exc:
        raise DataError(detail=f"不支持的 formal interval: {interval}") from exc
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    if value <= 0 or unit not in multipliers:
        raise DataError(detail=f"不支持的 formal interval: {interval}")
    return timedelta(seconds=value * multipliers[unit])


def _row(value: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", value.model_dump(mode="json", exclude_none=False))


class FormalDecisionMaterializer:
    """The concrete trust root for formal Runtime and EventDriven inputs."""

    def __init__(self, *, data_port: Any, manifest_store: Any, feature_registry: Any) -> None:
        if data_port is None or manifest_store is None or feature_registry is None:
            raise ValueError("FormalDecisionMaterializer 必须注入 PIT data、ManifestStore、FeatureRegistry")
        self._data_port = data_port
        self._manifest_store = manifest_store
        self._feature_registry = feature_registry

    def _sources(self, *, release: Any, instrument_id: str, interval: str) -> tuple[
        str, str, dict[str, str], tuple[tuple[str, str], ...], tuple[tuple[str, str], ...],
    ]:
        dataset_ids = tuple(release.dataset_manifest_ids)
        feature_ids = tuple(release.feature_set_ids)
        if not dataset_ids or not feature_ids or len(set(dataset_ids)) != len(dataset_ids) \
                or len(set(feature_ids)) != len(feature_ids):
            raise DataError(detail="Release formal source/materialization 集合为空或重复")
        if instrument_id not in release.allowed_instruments or interval not in release.allowed_timeframes:
            raise DataError(detail="DecisionInput 标的/周期不在 Release 允许范围")

        bars: list[str] = []
        supplemental: dict[str, str] = {}
        source_hashes: list[tuple[str, str]] = []
        for manifest_id in dataset_ids:
            manifest = self._manifest_store.get(manifest_id)
            verified = self._manifest_store.verify_pit_manifest(
                manifest, expected_dataset_types={manifest.dataset_type}, instrument_id=instrument_id,
                interval=interval, require_formal=True)
            if verified.manifest_id != manifest_id or not verified.content_hash:
                raise DataError(detail="Release source manifest ID/hash 不可信")
            source_hashes.append((manifest_id, verified.content_hash))
            if verified.dataset_type in _BARS_TYPES:
                bars.append(manifest_id)
            elif verified.dataset_type in _SUPPLEMENTAL_TYPES:
                key = _SUPPLEMENTAL_TYPES[verified.dataset_type]
                if key in supplemental:
                    raise DataError(detail=f"每种 {key} 数据最多允许一个 manifest")
                supplemental[key] = manifest_id
            else:
                raise DataError(detail=f"不支持的正式 DecisionInput 数据类型: {verified.dataset_type}")
        if len(bars) != 1:
            raise DataError(detail="Release 必须恰有一个 formal bars manifest")

        output_ids: set[str] = set()
        materialization_hashes: list[tuple[str, str]] = []
        input_ids: set[str] = set()
        for materialization_id in feature_ids:
            materialization = self._feature_registry.get_materialization_sync(materialization_id)
            if materialization.materialization_id != materialization_id or not materialization.content_hash:
                raise DataError(detail="FeatureMaterialization ID/hash 不可信")
            input_ids.update(materialization.input_manifest_ids)
            output = self._manifest_store.get(materialization.output_manifest_id)
            verified_output = self._manifest_store.verify_pit_manifest(
                output, expected_dataset_types=_FEATURE_TYPES, instrument_id=instrument_id,
                interval=interval, require_formal=True)
            if verified_output.content_hash != materialization.content_hash:
                raise DataError(detail="FeatureMaterialization output hash 不匹配")
            output_ids.add(verified_output.manifest_id)
            materialization_hashes.append((materialization_id, materialization.content_hash))
        if input_ids != set(dataset_ids):
            raise DataError(detail="FeatureMaterialization source lineage 与 Release manifest 不一致")
        if len(output_ids) != 1:
            raise DataError(detail="Release 必须解析到唯一 formal feature output manifest")
        return bars[0], next(iter(output_ids)), supplemental, tuple(source_hashes), tuple(materialization_hashes)

    async def derive(self, *, release: Any, decision_time: datetime,
                     instrument_id: str, interval: str) -> VerifiedDecisionMaterialization:
        if decision_time.tzinfo is None or decision_time.utcoffset() != UTC.utcoffset(decision_time):
            raise DataError(detail="DecisionInput decision_time 必须是 UTC")
        decision_time = decision_time.astimezone(UTC)
        bars_manifest, feature_manifest, supplemental, source_hashes, materialization_hashes = self._sources(
            release=release, instrument_id=instrument_id, interval=interval)
        window = _interval_delta(interval)
        start = decision_time - window
        end = decision_time + timedelta(microseconds=1)
        bars = await self._data_port.query(
            instrument_id, start, end, decision_time=decision_time, manifest_id=bars_manifest)
        visible_bars = [bar for bar in bars if bar.open_time <= decision_time and bar.available_at is not None]
        if not visible_bars:
            raise DataError(detail="PIT source 在 decision_time 没有可用 decision bar")
        bar = max(visible_bars, key=lambda item: item.open_time)
        feature_end = bar.open_time + window
        features = await self._data_port.query_features(
            instrument_id, [], bar.open_time, feature_end,
            decision_time=decision_time, manifest_id=feature_manifest)
        if not features:
            raise DataError(detail="PIT source 在 decision_time 没有可用 feature snapshot")
        feature_values: dict[str, Any] = {}
        latest_features: dict[str, Any] = {}
        for feature in features:
            if feature.ts <= bar.open_time and (
                    feature.feature_name not in latest_features
                    or latest_features[feature.feature_name].ts <= feature.ts):
                latest_features[feature.feature_name] = feature
        if not latest_features:
            raise DataError(detail="feature snapshot 未覆盖 decision bar")
        for name, feature in sorted(latest_features.items()):
            feature_values[name] = float(feature.value)

        supplemental_rows: dict[str, list[dict[str, Any]]] = {}
        for kind, manifest_id in sorted(supplemental.items()):
            if kind == "funding":
                values = await self._data_port.query_funding_rates(
                    instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)
            elif kind == "mark_price":
                values = await self._data_port.query_mark_prices(
                    instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)
            else:
                values = await self._data_port.query_open_interest(
                    instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id)
            supplemental_rows[kind] = [_row(value) for value in values]

        source_rows = {
            "bar": _row(bar),
            "features": [_row(item) for _name, item in sorted(latest_features.items())],
            "supplemental": supplemental_rows,
        }
        availability = [bar.available_at, *(item.available_at for item in latest_features.values())]
        for values in supplemental_rows.values():
            for value in values:
                availability.append(datetime.fromisoformat(str(value["available_at"])))
        if any(value is None or value > decision_time for value in availability):
            raise DataError(detail="trusted PIT source 包含 future/缺失 available_at")
        available_at = max(value for value in availability if value is not None)
        payload = {
            "bar": source_rows["bar"],
            "features": {instrument_id: feature_values},
            "supplemental": supplemental_rows,
        }
        return VerifiedDecisionMaterialization(
            decision_time=decision_time, instrument_id=instrument_id, interval=interval,
            payload=payload, available_at=available_at,
            source_manifest_ids=tuple(release.dataset_manifest_ids),
            feature_materialization_ids=tuple(release.feature_set_ids),
            source_manifest_hashes=source_hashes,
            feature_materialization_hashes=materialization_hashes,
            source_row_hash=content_hash(source_rows),
            materializer_schema_version=_MATERIALIZER_SCHEMA,
        )
