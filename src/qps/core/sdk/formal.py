"""Formal M2 execution value objects and canonical Intent validation."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping as RuntimeMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from qps.core.ports.v05 import FormalDecisionMaterializerPort

from qps.core.models.foundation import FrozenJsonObject
from qps.core.sdk.context import StrategyContext
from qps.core.sdk.intent_builder import IntentBuilder, IntentValidationError


def canonical_json(value: object) -> str:
    """Encode only JSON values with one stable representation."""
    def plain(item: object) -> object:
        if isinstance(item, FrozenJsonObject):
            return plain(item.to_plain())
        if isinstance(item, RuntimeMapping):
            return {str(key): plain(value) for key, value in item.items()}
        if isinstance(item, tuple | list):
            return [plain(value) for value in item]
        return item
    return json.dumps(plain(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False)


def content_hash(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionInput:
    """One immutable, addressable PIT input supplied to a strategy."""

    input_snapshot_ref: str
    decision_time: datetime
    instrument_id: str
    interval: str
    payload: Mapping[str, Any]
    source_manifest_ids: tuple[str, ...]
    feature_materialization_ids: tuple[str, ...]
    available_at: datetime
    source_manifest_hashes: tuple[tuple[str, str], ...]
    feature_materialization_hashes: tuple[tuple[str, str], ...]
    source_row_hash: str
    materializer_schema_version: str
    decision_request: Mapping[str, Any]
    schema_version: int = 2

    def __post_init__(self) -> None:
        if not self.input_snapshot_ref or not self.instrument_id or not self.interval:
            raise ValueError("DecisionInput 标识和范围不能为空")
        if self.decision_time.tzinfo is None or self.decision_time.utcoffset() != UTC.utcoffset(self.decision_time):
            raise ValueError("DecisionInput decision_time 必须是 UTC")
        if self.available_at.tzinfo is None or self.available_at.utcoffset() != UTC.utcoffset(self.available_at):
            raise ValueError("DecisionInput available_at 必须是 UTC")
        if self.available_at > self.decision_time:
            raise ValueError("DecisionInput available_at 不能晚于 decision_time")
        if not self.source_manifest_ids or not self.feature_materialization_ids:
            raise ValueError("DecisionInput 必须绑定 source manifest 和 feature materialization")
        if not self.source_manifest_hashes or not self.feature_materialization_hashes:
            raise ValueError("DecisionInput 必须携带 source/feature content hash")
        if not self.source_row_hash or not self.materializer_schema_version:
            raise ValueError("DecisionInput 必须携带 source-row hash 与 materializer 版本")
        expected_request = {
            "decision_time": self.decision_time.isoformat(),
            "instrument_id": self.instrument_id,
            "interval": self.interval,
        }
        if dict(self.decision_request) != expected_request:
            raise ValueError("DecisionInput decision request 不符合固定范围")
        object.__setattr__(self, "payload", FrozenJsonObject(self.payload))
        object.__setattr__(self, "decision_request", FrozenJsonObject(self.decision_request))

    @property
    def payload_hash(self) -> str:
        return content_hash(self.payload)

    @property
    def lineage_hash(self) -> str:
        return content_hash({
            "input_snapshot_ref": self.input_snapshot_ref,
            "decision_time": self.decision_time.isoformat(),
            "available_at": self.available_at.isoformat(),
            "instrument_id": self.instrument_id,
            "interval": self.interval,
            "payload_hash": self.payload_hash,
            "source_manifest_ids": self.source_manifest_ids,
            "source_manifest_hashes": self.source_manifest_hashes,
            "feature_materialization_ids": self.feature_materialization_ids,
            "feature_materialization_hashes": self.feature_materialization_hashes,
            "source_row_hash": self.source_row_hash,
            "materializer_schema_version": self.materializer_schema_version,
            "decision_request": self.decision_request,
            "schema_version": self.schema_version,
        })


@dataclass(frozen=True)
class VerifiedDecisionMaterialization:
    """PIT materializer output.  Only a trusted port may produce this value."""

    decision_time: datetime
    instrument_id: str
    interval: str
    payload: Mapping[str, Any]
    available_at: datetime
    source_manifest_ids: tuple[str, ...]
    feature_materialization_ids: tuple[str, ...]
    source_manifest_hashes: tuple[tuple[str, str], ...]
    feature_materialization_hashes: tuple[tuple[str, str], ...]
    source_row_hash: str
    materializer_schema_version: str

    @property
    def decision_request(self) -> dict[str, str]:
        return {
            "decision_time": self.decision_time.isoformat(),
            "instrument_id": self.instrument_id,
            "interval": self.interval,
        }


class FormalDecisionInputFactory:
    """Persist and verify a DecisionInput derived only by a trusted PIT port."""

    def __init__(self, artifact_root: Path | str, *, materializer: FormalDecisionMaterializerPort) -> None:
        if materializer is None:
            raise ValueError("FormalDecisionInputFactory 必须注入可信 PIT materializer")
        self.artifact_root = Path(artifact_root).resolve()
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self._materializer: FormalDecisionMaterializerPort = materializer

    @staticmethod
    def _input_from_materialization(*, release: Any,
                                    materialized: VerifiedDecisionMaterialization) -> DecisionInput:
        provisional = DecisionInput(
            input_snapshot_ref="pending", decision_time=materialized.decision_time,
            instrument_id=materialized.instrument_id, interval=materialized.interval,
            payload=materialized.payload, available_at=materialized.available_at,
            source_manifest_ids=materialized.source_manifest_ids,
            feature_materialization_ids=materialized.feature_materialization_ids,
            source_manifest_hashes=materialized.source_manifest_hashes,
            feature_materialization_hashes=materialized.feature_materialization_hashes,
            source_row_hash=materialized.source_row_hash,
            materializer_schema_version=materialized.materializer_schema_version,
            decision_request=materialized.decision_request)
        ref = f"input-{content_hash({
            "release_id": release.release_id, "lineage_hash": provisional.lineage_hash,
        })[:32]}"
        return DecisionInput(
            input_snapshot_ref=ref, decision_time=materialized.decision_time,
            instrument_id=materialized.instrument_id, interval=materialized.interval,
            payload=materialized.payload, available_at=materialized.available_at,
            source_manifest_ids=materialized.source_manifest_ids,
            feature_materialization_ids=materialized.feature_materialization_ids,
            source_manifest_hashes=materialized.source_manifest_hashes,
            feature_materialization_hashes=materialized.feature_materialization_hashes,
            source_row_hash=materialized.source_row_hash,
            materializer_schema_version=materialized.materializer_schema_version,
            decision_request=materialized.decision_request)

    @staticmethod
    def _document(*, release: Any, decision_input: DecisionInput) -> dict[str, Any]:
        return {
            "release_id": release.release_id, "input_snapshot_ref": decision_input.input_snapshot_ref,
            "decision_time": decision_input.decision_time.isoformat(),
            "instrument_id": decision_input.instrument_id, "interval": decision_input.interval,
            "available_at": decision_input.available_at.isoformat(),
            "payload": cast("FrozenJsonObject", decision_input.payload).to_plain(),
            "source_manifest_ids": list(decision_input.source_manifest_ids),
            "source_manifest_hashes": [list(item) for item in decision_input.source_manifest_hashes],
            "feature_materialization_ids": list(decision_input.feature_materialization_ids),
            "feature_materialization_hashes": [list(item) for item in decision_input.feature_materialization_hashes],
            "source_row_hash": decision_input.source_row_hash,
            "materializer_schema_version": decision_input.materializer_schema_version,
            "decision_request": cast("FrozenJsonObject", decision_input.decision_request).to_plain(),
            "schema_version": decision_input.schema_version,
            "payload_hash": decision_input.payload_hash, "lineage_hash": decision_input.lineage_hash,
        }

    async def materialize(self, *, release: Any, decision_time: datetime, instrument_id: str,
                          interval: str) -> DecisionInput:
        """Derive and persist the only formal DecisionInput creation path."""
        materialized = await self._materializer.derive(
            release=release, decision_time=decision_time, instrument_id=instrument_id, interval=interval)
        decision_input = self._input_from_materialization(release=release, materialized=materialized)
        document = self._document(release=release, decision_input=decision_input)
        target = self.artifact_root / f"{decision_input.input_snapshot_ref}.json"
        if target.exists():
            if target.read_text(encoding="utf-8") != canonical_json(document):
                raise ValueError("DecisionInput artifact 内容冲突")
            return decision_input
        fd, temporary_name = tempfile.mkstemp(prefix=f".{decision_input.input_snapshot_ref}.", suffix=".tmp", dir=self.artifact_root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(canonical_json(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary_name, 0o444)
            os.replace(temporary_name, target)
            directory_fd = os.open(self.artifact_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return decision_input

    async def load_verified(self, *, release: Any, input_snapshot_ref: str) -> DecisionInput:
        path = self.artifact_root / f"{input_snapshot_ref}.json"
        if path.parent != self.artifact_root or path.is_symlink() or not path.is_file():
            raise ValueError("DecisionInput artifact 不存在或路径非法")
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("release_id") != release.release_id:
            raise ValueError("DecisionInput artifact Release 不一致")
        required = {"available_at", "source_manifest_hashes", "feature_materialization_hashes",
                    "source_row_hash", "materializer_schema_version", "decision_request"}
        if not required.issubset(document):
            raise ValueError("DecisionInput artifact 缺少信任根字段")
        if document.get("schema_version") != 2:
            raise ValueError("DecisionInput artifact schema_version 不受支持")
        result = DecisionInput(input_snapshot_ref=document["input_snapshot_ref"],
            decision_time=datetime.fromisoformat(document["decision_time"]),
            available_at=datetime.fromisoformat(document["available_at"]),
            instrument_id=document["instrument_id"], interval=document["interval"],
            payload=document["payload"],
            source_manifest_ids=tuple(document["source_manifest_ids"]),
            feature_materialization_ids=tuple(document["feature_materialization_ids"]),
            schema_version=document["schema_version"],
            source_manifest_hashes=tuple(tuple(item) for item in document["source_manifest_hashes"]),
            feature_materialization_hashes=tuple(tuple(item) for item in document["feature_materialization_hashes"]),
            source_row_hash=document["source_row_hash"],
            materializer_schema_version=document["materializer_schema_version"],
            decision_request=document["decision_request"])
        if result.input_snapshot_ref != input_snapshot_ref or result.payload_hash != document.get("payload_hash") \
                or result.lineage_hash != document.get("lineage_hash"):
            raise ValueError("DecisionInput artifact hash/lineage 不匹配")
        materialized = await self._materializer.derive(
            release=release, decision_time=result.decision_time,
            instrument_id=result.instrument_id, interval=result.interval)
        expected = self._input_from_materialization(release=release, materialized=materialized)
        if self._document(release=release, decision_input=result) != self._document(
                release=release, decision_input=expected):
            raise ValueError("DecisionInput artifact 无法从可信 PIT source 重导")
        return result


class SnapshotFeatureReader:
    """Formal reader backed by the already materialized current input."""

    is_formal = True

    def __init__(self, values: Mapping[str, Mapping[str, Any]]) -> None:
        self._values = values

    def query_features(self, instrument_id: str, names: list[str], *,
                       decision_time: datetime, interval: str) -> dict[str, float]:
        if decision_time.tzinfo is None or decision_time.utcoffset() != UTC.utcoffset(decision_time):
            raise ValueError("feature decision_time 必须是 UTC")
        values = self._values.get(instrument_id, {})
        return {name: float(values[name]) for name in names if name in values}


class FormalStrategyExecutor:
    """Shared strict Context executor used by Runtime and formal backtests."""

    def __init__(self, *, strategy_id: str, version_no: int, deployment_id: str,
                 release_id: str, params: dict[str, Any], instruments: tuple[str, ...],
                 interval: str) -> None:
        self.strategy_id = strategy_id
        self.version_no = version_no
        self.deployment_id = deployment_id
        self.release_id = release_id
        self.params = params
        self.instruments = instruments
        self.interval = interval

    def _context(self, decision_input: DecisionInput, state: dict[str, Any]) -> Any:
        features = decision_input.payload.get("features", {})
        ctx = StrategyContext.for_runtime(
            strategy_id=self.strategy_id, version_no=self.version_no,
            deployment_id=self.deployment_id, release_id=self.release_id,
            params=self.params, instruments=list(self.instruments), interval=self.interval,
            decision_time=decision_input.decision_time,
            input_snapshot_ref=decision_input.input_snapshot_ref,
            feature_reader=SnapshotFeatureReader(features))
        ctx.restore_state(state)
        return ctx

    def initialize(self, strategy: Any, decision_input: DecisionInput) -> dict[str, Any]:
        ctx = self._context(decision_input, {})
        callback = getattr(strategy, "initialize", None)
        if callback is not None:
            callback(ctx)
        return dict(ctx.state)

    def execute(self, strategy: Any, decision_input: DecisionInput, state: dict[str, Any],
                callback: Callable[[Any], None]) -> dict[str, Any]:
        ctx = self._context(decision_input, state)
        callback(ctx)
        events = ctx.emitted_events()
        if len(events) > 1:
            raise ValueError("一次输入只能产生一个 Intent")
        event = events[0].model_dump(mode="json") if events else None
        return {"event": event, "state": dict(ctx.state), "input_snapshot_ref": decision_input.input_snapshot_ref,
                "input_hash": decision_input.payload_hash, "lineage_hash": decision_input.lineage_hash}


def build_validated_intent(*, raw: Mapping[str, Any], strategy_id: str, version_no: int,
                           deployment_id: str, release_id: str, interval: str,
                           instruments: tuple[str, ...], decision_time: datetime,
                           input_snapshot_ref: str) -> dict[str, Any]:
    """Build the canonical Intent and reject any child-supplied completed fields."""
    allowed = set(IntentBuilder.WHITELISTED_FIELDS)
    if any(key not in allowed for key in raw):
        raise IntentValidationError("Intent 含有完成字段或未知字段")
    builder = IntentBuilder(strategy_id=strategy_id, version_no=version_no,
        deployment_id=deployment_id, release_id=release_id, interval=interval,
        allowed_instruments=set(instruments), clock=decision_time, deterministic_ids=True,
        input_snapshot_ref=input_snapshot_ref)
    event = builder.build_and_emit(dict(raw))
    intent = dict(event.payload["intent"])
    expected_event_id = "evt_" + content_hash({
        "release_id": release_id, "deployment_id": deployment_id,
        "input_snapshot_ref": input_snapshot_ref, "intent": intent,
    })[:8]
    if event.event_id != expected_event_id:
        raise IntentValidationError("Intent event_id 不符合输入 lineage")
    if intent["release_id"] != release_id or intent["deployment_id"] != deployment_id:
        raise IntentValidationError("Intent 身份与 Runtime 不一致")
    return {"event": event.model_dump(mode="json"), "intent": intent,
            "intent_hash": content_hash(intent), "event_id": event.event_id}
