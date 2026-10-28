"""Immutable dataset manifests and PIT partition verification."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from qps.core.constants import FeatureStatus
from qps.core.errors import DataError
from qps.core.models.foundation import (
    DatasetManifest,
    FeatureDefinition,
    FeatureEvaluation,
    FeatureMaterialization,
    SourceReference,
)

if TYPE_CHECKING:
    from qps.infra.storage.meta_db import MetaDB


class ManifestStore:
    """Build and verify manifests without changing their referenced files."""

    def __init__(self, data_dir: Path, meta_db: MetaDB) -> None:
        self._data_dir = data_dir.resolve()
        self._meta_db = meta_db

    def build(
        self,
        dataset_type: str,
        partition_refs: list[str] | tuple[str, ...],
        source_refs: list[SourceReference] | tuple[SourceReference, ...],
        *,
        schema_version: str = "v0.5",
        quality_report_ref: str | None = None,
        created_at: datetime | None = None,
        persist: bool = True,
    ) -> DatasetManifest:
        """Build a hash-only candidate manifest.

        ``build`` proves only the referenced bytes and may be persisted for
        diagnostics or later promotion.  PIT research evidence must be created
        through :meth:`build_formal_pit`, which verifies the full semantic
        contract before persistence.
        """
        refs = tuple(self._normalize_ref(ref) for ref in partition_refs)
        if not refs:
            raise DataError(detail="manifest 至少需要一个 partition")
        digest = self._content_hash(refs)
        manifest = DatasetManifest(
            manifest_id=f"manifest-{digest[:24]}",
            dataset_type=dataset_type,
            schema_version=schema_version,
            partition_refs=refs,
            content_hash=digest,
            source_refs=tuple(source_refs),
            quality_report_ref=quality_report_ref,
            created_at=created_at or datetime.now(UTC),
        )
        if persist:
            self._meta_db.save_manifest(manifest)
        return manifest

    def build_formal_pit(
        self,
        dataset_type: str,
        partition_refs: list[str] | tuple[str, ...],
        source_refs: list[SourceReference] | tuple[SourceReference, ...],
        *,
        instrument_id: str,
        interval: str | None = None,
        quality_report_ref: str,
        created_at: datetime | None = None,
        persist: bool = True,
    ) -> DatasetManifest:
        """Verify and persist a PIT manifest that is eligible for research.

        The report must have been written against the same hash-only candidate
        first.  This makes the candidate/formal transition explicit and rejects
        malformed PIT evidence before it reaches MetaDB.
        """
        if not self.is_pit_dataset(dataset_type):
            raise DataError(detail=f"非 PIT 数据集不能创建 formal PIT manifest: {dataset_type}")
        candidate = self.build(
            dataset_type, partition_refs, source_refs, schema_version="v0.5-pit-formal",
            quality_report_ref=quality_report_ref, created_at=created_at, persist=False,
        )
        self.verify_pit_manifest(
            candidate, expected_dataset_types={dataset_type}, instrument_id=instrument_id,
            interval=interval,
        )
        if persist:
            self._meta_db.save_manifest(candidate)
        return candidate

    @staticmethod
    def is_formal_pit_manifest(manifest: DatasetManifest) -> bool:
        """Return whether a manifest was persisted through the formal PIT entry point."""
        return manifest.schema_version == "v0.5-pit-formal"

    def get(self, manifest_id: str) -> DatasetManifest:
        return self._meta_db.get_manifest(manifest_id)

    @staticmethod
    def is_pit_dataset(dataset_type: str) -> bool:
        return dataset_type in {
            "normalized.bars", "bars", "feature_input.bars", "normalized.funding", "funding",
            "normalized.open_interest", "open_interest", "normalized.mark_price", "mark_price",
            "features", "feature.output", "normalized.features",
        }

    def verify(self, manifest: DatasetManifest | str) -> DatasetManifest:
        resolved = self.get(manifest) if isinstance(manifest, str) else manifest
        actual = self._content_hash(resolved.partition_refs)
        if actual != resolved.content_hash:
            raise DataError(
                detail=f"manifest content hash 不匹配: {resolved.manifest_id} "
                f"expected={resolved.content_hash} actual={actual}"
            )
        return resolved

    def verify_pit_manifest(
        self,
        manifest: DatasetManifest | str,
        *,
        expected_dataset_types: set[str],
        instrument_id: str | None = None,
        interval: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        require_time_coverage: bool = False,
        as_of: datetime | None = None,
        require_formal: bool = False,
        _visited: set[str] | None = None,
    ) -> DatasetManifest:
        """Verify hash integrity and the semantic contract for formal PIT use."""
        resolved = self.verify(manifest)
        if require_formal and not self.is_formal_pit_manifest(resolved):
            raise DataError(detail=f"candidate manifest 不可用于正式研究: {resolved.manifest_id}")
        if resolved.dataset_type not in expected_dataset_types:
            raise DataError(detail=f"manifest 数据类型不匹配: {resolved.dataset_type}")
        self._verify_quality_report(resolved, instrument_id=instrument_id, interval=interval)
        visited = set() if _visited is None else _visited
        if resolved.manifest_id in visited:
            raise DataError(detail=f"manifest lineage 存在循环: {resolved.manifest_id}")
        visited.add(resolved.manifest_id)

        frames = self._read_manifest_frames(resolved)
        required = self._pit_columns(resolved.dataset_type)
        has_time_coverage = False
        for index, frame in enumerate(frames):
            missing = sorted(required.difference(frame.columns))
            if missing:
                raise DataError(
                    detail=f"PIT partition 缺少字段: {resolved.partition_refs[index]}: {', '.join(missing)}"
                )
            has_time_coverage = self._validate_pit_frame(
                resolved, frame, instrument_id=instrument_id, interval=interval,
                start=start, end=end, as_of=as_of, require_formal=require_formal, visited=visited,
            ) or has_time_coverage

        if instrument_id is not None and not any(
            any(str(value) == instrument_id for value in frame.get_column("instrument_id").to_list())
            for frame in frames
        ):
            raise DataError(f"manifest 不包含请求标的: {instrument_id}")
        if require_time_coverage and start is not None and end is not None and not has_time_coverage:
            raise DataError("manifest 不覆盖请求时间范围")
        return resolved

    def write_quality_report(
        self, manifest: DatasetManifest, payload: dict[str, Any], *, ref: str | None = None,
    ) -> str:
        """Persist a content-addressed quality report for a candidate manifest."""
        report_ref = ref or f"quality_reports/{manifest.manifest_id}.json"
        report_path = self._data_dir / self._normalize_ref(report_ref)
        report = dict(payload)
        report.update({
            "manifest_id": manifest.manifest_id,
            "content_hash": manifest.content_hash,
            "dataset_type": manifest.dataset_type,
        })
        encoded = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        report_path.parent.mkdir(parents=True, exist_ok=True)
        if report_path.exists() and report_path.read_text(encoding="utf-8") != encoded:
            raise DataError(detail=f"quality report 不可变冲突: {report_ref}")
        if not report_path.exists():
            temp_path = report_path.with_name(f".{report_path.name}.tmp")
            temp_path.write_text(encoded, encoding="utf-8")
            os.replace(temp_path, report_path)
        return self._normalize_ref(report_ref)

    @staticmethod
    def _pit_columns(dataset_type: str) -> set[str]:
        if dataset_type in {"normalized.bars", "bars", "feature_input.bars"}:
            return {
                "instrument_id", "open_time", "interval", "event_time", "available_at",
                "ingested_at", "close_time", "is_closed",
            }
        if dataset_type in {"normalized.funding", "funding"}:
            return {"instrument_id", "funding_ts", "rate", "available_at", "ingested_at"}
        if dataset_type in {"normalized.open_interest", "open_interest", "normalized.mark_price", "mark_price"}:
            value = "oi_value" if "open_interest" in dataset_type else "mark_price"
            return {"instrument_id", "ts", value, "available_at", "ingested_at"}
        if dataset_type in {"features", "feature.output", "normalized.features"}:
            return {
                "instrument_id", "ts", "feature_name", "value", "feature_version",
                "available_at", "decision_time", "source_manifest_id",
            }
        raise DataError(detail=f"不支持 PIT 语义验证的数据类型: {dataset_type}")

    def _read_manifest_frames(self, manifest: DatasetManifest) -> list[pl.DataFrame]:
        frames: list[pl.DataFrame] = []
        for ref in manifest.partition_refs:
            try:
                frames.append(pl.read_parquet(self.resolve_partition(ref)))
            except Exception as exc:
                raise DataError(detail=f"读取 manifest partition 失败: {ref}", cause=exc) from exc
        return frames

    def _verify_quality_report(
        self, manifest: DatasetManifest, *, instrument_id: str | None, interval: str | None,
    ) -> None:
        ref = manifest.quality_report_ref
        if ref is None or not ref.strip():
            raise DataError(detail=f"manifest 缺少 quality_report_ref: {manifest.manifest_id}")
        try:
            report_path = self.resolve_partition(ref)
            payload = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, DataError) as exc:
            raise DataError(detail=f"quality report 无法读取: {ref}", cause=exc) from exc
        if not isinstance(payload, dict):
            raise DataError(detail=f"quality report 格式无效: {ref}")
        required = {"manifest_id", "content_hash", "dataset_type", "instrument_id", "interval", "overall_quality"}
        if not required.issubset(payload):
            missing = sorted(required.difference(payload))
            raise DataError(detail=f"quality report 缺少字段: {', '.join(missing)}")
        if payload["manifest_id"] != manifest.manifest_id or payload["content_hash"] != manifest.content_hash:
            raise DataError(detail=f"quality report 与 manifest 不匹配: {manifest.manifest_id}")
        if payload["dataset_type"] != manifest.dataset_type:
            raise DataError(detail="quality report dataset_type 不匹配")
        if instrument_id is not None and payload["instrument_id"] != instrument_id:
            raise DataError(detail="quality report instrument_id 不匹配")
        if interval is not None and payload["interval"] != interval:
            raise DataError(detail="quality report interval 不匹配")
        if payload["overall_quality"] not in {"OK", "SUSPECT"}:
            raise DataError(detail="quality report overall_quality 无效")

    def _validate_pit_frame(
        self,
        manifest: DatasetManifest,
        frame: pl.DataFrame,
        *,
        instrument_id: str | None,
        interval: str | None,
        start: datetime | None,
        end: datetime | None,
        as_of: datetime | None,
        require_formal: bool,
        visited: set[str],
    ) -> bool:
        dataset_type = manifest.dataset_type
        time_field = "open_time" if dataset_type in {"normalized.bars", "bars", "feature_input.bars"} else (
            "funding_ts" if dataset_type in {"normalized.funding", "funding"} else "ts"
        )
        has_time_coverage = False
        for row in frame.iter_rows(named=True):
            raw_instrument = row.get("instrument_id")
            if raw_instrument is None or str(raw_instrument).strip() in {"", "null", "None"}:
                raise DataError("PIT 记录缺少有效 instrument_id")
            if interval is not None and "interval" in row and str(row["interval"]) != interval:
                raise DataError(f"manifest interval 不匹配: expected={interval}")
            event_time = self._utc_datetime(row[time_field], time_field)
            available_at = self._utc_datetime(row["available_at"], "available_at")
            if "ingested_at" in row:
                ingested_at = self._utc_datetime(row["ingested_at"], "ingested_at")
                if ingested_at < available_at:
                    raise DataError("PIT 时间语义无效: ingested_at 早于 available_at")
            if dataset_type in {"normalized.bars", "bars", "feature_input.bars"}:
                event = self._utc_datetime(row["event_time"], "event_time")
                close = self._utc_datetime(row["close_time"], "close_time")
                if available_at < event or close <= event_time or not isinstance(row["is_closed"], bool):
                    raise DataError("Bar PIT timing 语义无效")
            elif dataset_type in {"normalized.funding", "funding"}:
                settlement = row.get("settlement_ts")
                if settlement not in (None, "null"):
                    self._utc_datetime(settlement, "settlement_ts")
            elif dataset_type in {"features", "feature.output", "normalized.features"}:
                row_decision_time = self._utc_datetime(row["decision_time"], "decision_time")
                if row_decision_time < event_time or row_decision_time < available_at:
                    raise DataError("FeatureSet decision_time 早于 ts 或 available_at")
                if as_of is not None and row_decision_time > as_of:
                    raise DataError("FeatureSet decision_time 晚于请求 decision_time")
                source_id = row.get("source_manifest_id")
                if source_id is None or str(source_id).strip() in {"", "null", "None"}:
                    raise DataError("FeatureSet 缺少有效 source_manifest_id")
                declared_sources = {
                    reference.reference for reference in manifest.source_refs if reference.reference
                }
                if not declared_sources or str(source_id) not in declared_sources:
                    raise DataError("FeatureSet source_manifest_id 不属于 manifest lineage")
                source = self.get(str(source_id))
                if source.manifest_id not in visited:
                    self.verify_pit_manifest(
                        source, expected_dataset_types={"normalized.bars", "bars", "feature_input.bars"},
                        require_formal=require_formal,
                        _visited=visited,
                    )
            if start is not None and end is not None and not (start <= event_time < end):
                continue
            has_time_coverage = True
        return has_time_coverage

    @staticmethod
    def _utc_datetime(value: object, field: str) -> datetime:
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        except (TypeError, ValueError) as exc:
            raise DataError(detail=f"PIT 字段无效: {field}", cause=exc) from exc
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise DataError(detail=f"PIT 字段必须是 UTC 时间: {field}")
        return parsed.astimezone(UTC)

    def _normalize_ref(self, ref: str) -> str:
        path = Path(ref)
        if path.is_absolute():
            try:
                return path.resolve().relative_to(self._data_dir).as_posix()
            except ValueError as exc:
                raise DataError(detail=f"partition 不在 data_dir 内: {ref}") from exc
        normalized = Path(ref).as_posix()
        if normalized.startswith("../") or normalized == "..":
            raise DataError(detail=f"partition 路径非法: {ref}")
        return normalized

    def resolve_partition(self, ref: str) -> Path:
        path = (self._data_dir / ref).resolve()
        try:
            path.relative_to(self._data_dir)
        except ValueError as exc:
            raise DataError(detail=f"partition 路径越界: {ref}") from exc
        if not path.is_file():
            raise DataError(detail=f"manifest 引用的 partition 不存在: {ref}")
        return path

    def _content_hash(self, refs: tuple[str, ...]) -> str:
        digest = hashlib.sha256()
        for ref in refs:
            path = self.resolve_partition(ref)
            digest.update(ref.encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        return digest.hexdigest()


class PersistentFeatureRegistry:
    """Durable FeatureRegistryPort backed by MetaDB."""

    def __init__(self, meta_db: MetaDB, manifests: ManifestStore) -> None:
        self._meta_db = meta_db
        self._manifests = manifests

    async def register_definition(self, definition: FeatureDefinition) -> None:
        self._meta_db.save_feature_definition(definition)

    async def register_materialization(self, materialization: FeatureMaterialization) -> None:
        definition = self._meta_db.get_feature_definition(
            materialization.feature_id, materialization.feature_version
        )
        if definition.status.value in {"DEPRECATED", "RETIRED"}:
            raise DataError(detail=f"特征已退役，禁止新 materialization: {definition.feature_id}@{definition.version}")
        for manifest_id in materialization.input_manifest_ids:
            input_manifest = self._manifests.get(manifest_id)
            if self._manifests.is_pit_dataset(input_manifest.dataset_type):
                self._manifests.verify_pit_manifest(
                    input_manifest, expected_dataset_types={input_manifest.dataset_type}, require_formal=True,
                )
            else:
                self._manifests.verify(input_manifest)
        output_manifest = self._manifests.get(materialization.output_manifest_id)
        if self._manifests.is_pit_dataset(output_manifest.dataset_type):
            self._manifests.verify_pit_manifest(
                output_manifest, expected_dataset_types={output_manifest.dataset_type}, require_formal=True,
            )
        else:
            self._manifests.verify(output_manifest)
        self._meta_db.save_feature_materialization(materialization)

    async def register_materialization_batch(
        self,
        definitions: list[FeatureDefinition],
        output_manifest: DatasetManifest,
        materializations: list[FeatureMaterialization],
    ) -> None:
        """Persist a complete multi-feature lineage in one MetaDB transaction."""
        if not definitions or not materializations:
            raise DataError(detail="materialization batch 不能为空")
        for materialization in materializations:
            definition = next(
                (item for item in definitions
                 if item.feature_id == materialization.feature_id
                 and item.version == materialization.feature_version), None,
            )
            if definition is None:
                definition = self._meta_db.get_feature_definition(
                    materialization.feature_id, materialization.feature_version
                )
            if definition.status.value in {"DEPRECATED", "RETIRED"}:
                raise DataError(detail=f"特征已退役，禁止新 materialization: {definition.feature_id}@{definition.version}")
            for manifest_id in materialization.input_manifest_ids:
                input_manifest = self._manifests.get(manifest_id)
                if self._manifests.is_pit_dataset(input_manifest.dataset_type):
                    self._manifests.verify_pit_manifest(
                    input_manifest, expected_dataset_types={input_manifest.dataset_type}, require_formal=True,
                    )
                else:
                    self._manifests.verify(input_manifest)
            if materialization.output_manifest_id != output_manifest.manifest_id:
                raise DataError(detail="materialization output manifest 不一致")
        if self._manifests.is_pit_dataset(output_manifest.dataset_type):
            self._manifests.verify_pit_manifest(
                output_manifest, expected_dataset_types={output_manifest.dataset_type}, require_formal=True,
            )
        else:
            self._manifests.verify(output_manifest)
        self._meta_db.save_feature_batch(definitions, output_manifest, materializations)

    async def record_evaluation(self, evaluation: FeatureEvaluation) -> None:
        raise DataError(detail="M1 不实现 FeatureEvaluation；请由 M3 研究治理写入")

    async def get_definition(self, feature_id: str, version: int) -> FeatureDefinition:
        return self._meta_db.get_feature_definition(feature_id, version)

    async def list_definitions(self, feature_id: str | None = None) -> list[FeatureDefinition]:
        return self._meta_db.list_feature_definitions(feature_id)

    async def list_materializations(self, feature_id: str | None = None) -> list[FeatureMaterialization]:
        return self._meta_db.list_feature_materializations(feature_id)

    async def get_materialization(self, materialization_id: str) -> FeatureMaterialization:
        return self._meta_db.get_feature_materialization(materialization_id)

    def get_materialization_sync(self, materialization_id: str) -> FeatureMaterialization:
        """Read immutable materialization metadata for synchronous trust-root checks."""
        return self._meta_db.get_feature_materialization(materialization_id)

    async def deprecate(self, feature_id: str, version: int, *, created_at: datetime | None = None) -> FeatureDefinition:
        """Create a new immutable retired version; leave the old version readable."""
        current = self._meta_db.get_feature_definition(feature_id, version)
        retired = FeatureDefinition.model_validate(
            current.model_dump(mode="json")
            | {"version": version + 1, "status": FeatureStatus.DEPRECATED,
               "created_at": created_at or datetime.now(UTC)}
        )
        await self.register_definition(retired)
        return retired
