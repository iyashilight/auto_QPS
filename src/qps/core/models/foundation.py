"""v0.5 M0 domain contracts.

These models define immutable records and value constraints only. Persistence and
side effects belong to infrastructure implementations of the ports package.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    PlainSerializer,
    field_validator,
    model_validator,
)
from pydantic_core import CoreSchema, core_schema

from qps.core.constants import (
    ApprovalStatus,
    DatasetSplit,
    DeploymentHealthStatus,
    FeatureStatus,
    HealthAssessmentStatus,
    LedgerType,
    LifecycleActionType,
    OrderSide,
    OrderStatus,
    ReconciliationStatus,
    ReleaseStatus,
    ResearchActorType,
    ResearchDisposition,
    ResearchScope,
    ReservationStatus,
    TrialStatus,
    validate_order_transition,
)
from qps.core.models.validators import validate_utc_datetime


class FrozenJsonArray(tuple[Any, ...]):
    """Immutable JSON array that remains equal to the legacy list form."""

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: Any
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(cls)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, (list, tuple)):
            return tuple(self) == tuple(other)
        return False

    __hash__ = tuple.__hash__


class FrozenJsonObject(Mapping[str, Any]):
    """Immutable JSON object with normal mapping reads and JSON object dumps."""

    __slots__ = ("_items",)

    @classmethod
    def __get_pydantic_core_schema__(
        cls, _source_type: Any, _handler: Any
    ) -> CoreSchema:
        return core_schema.no_info_plain_validator_function(cls)

    def __init__(self, value: Mapping[str, Any] | FrozenJsonObject | None = None) -> None:
        if value is None:
            value = {}
        if not isinstance(value, Mapping):
            raise ValueError("JSON object 必须是字符串键映射")
        items: list[tuple[str, Any]] = []
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON object 的键必须是字符串")
            items.append((key, _freeze_json(item)))
        self._items = tuple(sorted(items))

    def __getitem__(self, key: str) -> Any:
        for item_key, item in self._items:
            if item_key == key:
                return item
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)

    def to_plain(self) -> dict[str, Any]:
        return {key: _thaw_json(item) for key, item in self._items}


class FrozenStringMap(FrozenJsonObject):
    """Immutable string-to-string map used by v0.5 contract fields."""

    def __init__(self, value: Mapping[str, Any] | None = None) -> None:
        if value is None:
            value = {}
        if not isinstance(value, Mapping) or any(
            not isinstance(key, str) or not isinstance(item, str)
            for key, item in value.items()
        ):
            raise ValueError("StringMap 只允许字符串键和值")
        super().__init__(value)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, (FrozenJsonObject, FrozenJsonArray)):
        return value
    if isinstance(value, Mapping):
        return FrozenJsonObject(value)
    if isinstance(value, (list, tuple)):
        return FrozenJsonArray(_freeze_json(item) for item in value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError("JSON value 只能包含 object、array、string、number、boolean 或 null")


def _thaw_json(value: Any) -> Any:
    if isinstance(value, FrozenJsonObject):
        return value.to_plain()
    if isinstance(value, (FrozenJsonArray, tuple)):
        return [_thaw_json(item) for item in value]
    return value


def _freeze_string_map(value: Any) -> FrozenStringMap:
    return FrozenStringMap(value)


def _dump_string_map(value: FrozenStringMap) -> dict[str, str]:
    return {key: item for key, item in value.items()}


def _dump_raw_json(value: Any) -> Any:
    return _thaw_json(value)


StringMap = Annotated[
    FrozenStringMap,
    BeforeValidator(_freeze_string_map),
    PlainSerializer(_dump_string_map, return_type=dict[str, str], when_used="always"),
]
RawJson = Annotated[
    FrozenJsonObject | FrozenJsonArray | str | int | float | bool | None,
    BeforeValidator(_freeze_json),
    PlainSerializer(_dump_raw_json, return_type=Any, when_used="always"),
]


class SourceReference(BaseModel):
    name: str = Field(min_length=1)
    uri: str | None = None
    reference: str | None = None

    model_config = {"frozen": True}


class DatasetManifest(BaseModel):
    manifest_id: str = Field(min_length=1)
    dataset_type: str = Field(min_length=1)
    schema_version: str = Field(min_length=1)
    partition_refs: tuple[str, ...] = Field(min_length=1)
    content_hash: str = Field(min_length=1)
    source_refs: tuple[SourceReference, ...] = Field(min_length=1)
    quality_report_ref: str | None = None
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class InstrumentRuleVersion(BaseModel):
    rule_version_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    rules_hash: str = Field(min_length=1)
    valid_from: datetime
    valid_to: datetime | None = None
    available_at: datetime
    created_at: datetime
    tick_size: Decimal = Field(gt=0)
    lot_size: Decimal = Field(gt=0)
    min_notional: Decimal = Field(ge=0)
    max_leverage: Decimal | None = Field(default=None, gt=0)

    model_config = {"frozen": True}

    @field_validator("valid_from", "valid_to", "available_at", "created_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime | None) -> datetime | None:
        return validate_utc_datetime(value) if value is not None else None

    @model_validator(mode="after")
    def _validity_order(self) -> InstrumentRuleVersion:
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to 必须晚于 valid_from")
        return self


class PointInTimeRecord(BaseModel):
    event_time: datetime
    available_at: datetime
    ingested_at: datetime

    model_config = {"frozen": True}

    @field_validator("event_time", "available_at", "ingested_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _availability_order(self) -> PointInTimeRecord:
        if self.available_at < self.event_time:
            raise ValueError("available_at 不能早于 event_time")
        if self.ingested_at < self.available_at:
            raise ValueError("ingested_at 不能早于 available_at")
        return self


class BarTiming(BaseModel):
    open_time: datetime
    close_time: datetime
    is_closed: bool
    decision_time: datetime

    model_config = {"frozen": True}

    @field_validator("open_time", "close_time", "decision_time")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _bar_order(self) -> BarTiming:
        if self.close_time <= self.open_time:
            raise ValueError("close_time 必须晚于 open_time")
        if self.decision_time < self.open_time:
            raise ValueError("decision_time 不能早于 open_time")
        return self


class FeatureDefinition(BaseModel):
    feature_id: str = Field(min_length=1)
    semantic_name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    formula_or_code_hash: str = Field(min_length=1)
    input_schema: StringMap
    source_types: tuple[str, ...] = Field(min_length=1)
    window: int = Field(gt=0)
    warmup: int = Field(ge=0)
    frequency: str = Field(min_length=1)
    available_at_rule: str = Field(min_length=1)
    owner: str = Field(min_length=1)
    version: int = Field(ge=1)
    status: FeatureStatus = FeatureStatus.DRAFT
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class FeatureMaterialization(BaseModel):
    materialization_id: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    feature_version: int = Field(ge=1)
    input_manifest_ids: tuple[str, ...] = Field(min_length=1)
    output_manifest_id: str = Field(min_length=1)
    coverage: Decimal = Field(ge=0, le=1)
    quality_report_id: str | None = None
    computed_at: datetime
    content_hash: str = Field(min_length=1)

    model_config = {"frozen": True}

    @field_validator("computed_at")
    @classmethod
    def _computed_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class FeatureEvaluation(BaseModel):
    evaluation_id: str = Field(min_length=1)
    feature_id: str = Field(min_length=1)
    feature_version: int = Field(ge=1)
    strategy_family_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    dataset_split: DatasetSplit
    evidence_metrics: RawJson
    evaluated_at: datetime

    model_config = {"frozen": True}

    @field_validator("evaluated_at")
    @classmethod
    def _evaluated_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class CandidateDescriptor(BaseModel):
    """候选的不可变相似性描述；不能只依赖 candidate name。"""

    candidate_id: str = Field(min_length=1)
    candidate_fingerprint: str = Field(min_length=1)
    strategy_family_id: str | None = None
    feature_family_id: str | None = None
    code_structure_hash: str = Field(min_length=1)
    parameter_structure_hash: str = Field(min_length=1)
    feature_lineage: tuple[str, ...] = ()
    signal_sequence_hash: str = Field(min_length=1)
    exposure_fingerprint: str = Field(min_length=1)
    exposure_refs: tuple[str, ...] = ()
    descriptor_version: str = "m3-v05-candidate-v1"

    model_config = {"frozen": True}


class ResearchLineage(BaseModel):
    """正式研究输入的固定 lineage；字段值均参与 rerun 比对。"""

    release_id: str = Field(min_length=1)
    release_archive_hash: str = Field(min_length=1)
    data_manifest_hash: str = Field(min_length=1)
    feature_materialization_hash: str = Field(min_length=1)
    rule_version: str = Field(min_length=1)
    cost_version: str = Field(min_length=1)
    environment_lock: str = Field(min_length=1)
    seed: int
    clock: str = Field(min_length=1)

    model_config = {"frozen": True}


class FormalRunArtifact(BaseModel):
    """实际正式运行的唯一 lineage 权威记录。

    ``TrialRegistration`` 只描述预期来源；这些字段必须由 formal engine
    实际产生并在存储边界再次核验，不能由 registration 补齐。
    """

    artifact_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    release_archive_hash: str = Field(min_length=1)
    data_manifest_hash: str = Field(min_length=1)
    feature_materialization_hash: str = Field(min_length=1)
    historical_rule_hash: str = Field(min_length=1)
    cost_hash: str = Field(min_length=1)
    environment_lock: str = Field(min_length=1)
    seed: int
    clock: str = Field(min_length=1)
    trades_hash: str = Field(min_length=1)
    equity_hash: str = Field(min_length=1)
    report_hash: str = Field(min_length=1)
    canonical_lineage_hash: str = Field(min_length=1)
    run_payload_hash: str = Field(min_length=1)
    metrics: RawJson = None
    capacity: RawJson = None
    cost_breakdown: RawJson = None
    benchmark: RawJson = None
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @property
    def engine_config(self) -> dict[str, Any]:
        """Read-only v1 compatibility projection; never a lineage authority."""
        return {
            "formal": True,
            "formal_artifact_id": self.artifact_id,
            "trial_id": self.trial_id,
            "release_id": self.release_id,
            "release_archive_hash": self.release_archive_hash,
            "data_manifest_hash": self.data_manifest_hash,
            "feature_materialization_hash": self.feature_materialization_hash,
            "historical_rule_hash": self.historical_rule_hash,
            "cost_hash": self.cost_hash,
            "environment_lock": self.environment_lock,
            "random_seed": self.seed,
            "clock": self.clock,
            "trades_hash": self.trades_hash,
            "equity_hash": self.equity_hash,
            "report_hash": self.report_hash,
            "capacity": self.capacity,
            "benchmark": self.benchmark,
        }


class WalkForwardWindow(BaseModel):
    """一个不可变、UTC 单调推进的 walk-forward 窗口。"""

    window_id: str = Field(min_length=1)
    train_start: datetime
    train_end: datetime
    validation_start: datetime
    validation_end: datetime
    oos_start: datetime
    oos_end: datetime
    purge: str = Field(min_length=1)
    embargo: str = Field(min_length=1)
    input_hash: str = Field(min_length=1)
    parameter_hash: str = Field(min_length=1)
    metrics: RawJson
    failure_reason: str | None = None
    status: str = Field(min_length=1)

    model_config = {"frozen": True}

    @field_validator(
        "train_start", "train_end", "validation_start", "validation_end", "oos_start", "oos_end",
    )
    @classmethod
    def _window_times_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _window_order(self) -> WalkForwardWindow:
        values = (
            self.train_start, self.train_end, self.validation_start,
            self.validation_end, self.oos_start, self.oos_end,
        )
        if list(values) != sorted(values) or len(set(values)) != len(values):
            raise ValueError("walk-forward 窗口必须严格按 UTC 单调推进")
        if self.status == "FAILED" and not self.failure_reason:
            raise ValueError("失败的 walk-forward 窗口必须记录 failure_reason")
        return self


class TrialRegistration(BaseModel):
    trial_id: str = Field(min_length=1)
    strategy_family_id: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    allowed_datasets: tuple[str, ...] = Field(min_length=1)
    parameter_space: RawJson
    primary_metric: str = Field(min_length=1)
    rejection_criteria: RawJson
    seed: int
    registered_at: datetime
    candidate_descriptor: CandidateDescriptor | None = None
    utc_scope: tuple[datetime, datetime] | None = None
    release_id: str | None = None
    release_archive_hash: str | None = None
    data_manifest_hash: str | None = None
    feature_materialization_hash: str | None = None
    rule_version: str | None = None
    cost_version: str | None = None
    historical_rule_hash: str | None = None
    cost_hash: str | None = None
    environment_lock: str | None = None
    clock_id: str | None = None
    registered_by: str = "unknown"

    model_config = {"frozen": True}

    @field_validator("registered_at")
    @classmethod
    def _registered_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @field_validator("utc_scope")
    @classmethod
    def _scope_utc(cls, value: tuple[datetime, datetime] | None) -> tuple[datetime, datetime] | None:
        if value is None:
            return None
        start, end = (validate_utc_datetime(item) for item in value)
        if end <= start:
            raise ValueError("utc_scope 必须是严格递增的 UTC 区间")
        return start, end


class TrialEvent(BaseModel):
    """Trial 生命周期的 append-only 事件。"""

    event_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    status: TrialStatus
    reason: str = Field(min_length=1)
    occurred_at: datetime

    model_config = {"frozen": True}

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class ResearchBudget(BaseModel):
    budget_id: str = Field(min_length=1)
    scope: ResearchScope
    scope_ref: str = Field(min_length=1)
    period: str = Field(min_length=1)
    max_trials: int = Field(gt=0)
    max_compute: Decimal = Field(gt=0)
    max_blind_reveals: int = Field(ge=0)
    similarity_policy_version: str = Field(min_length=1)
    approved_by: str = Field(min_length=1)
    valid_from: datetime
    valid_to: datetime

    model_config = {"frozen": True}

    @field_validator("valid_from", "valid_to")
    @classmethod
    def _validity_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _validity_order(self) -> ResearchBudget:
        if self.valid_to <= self.valid_from:
            raise ValueError("预算 valid_to 必须晚于 valid_from")
        return self


class BudgetConsumption(BaseModel):
    consumption_id: str = Field(min_length=1)
    budget_id: str = Field(min_length=1)
    actor_type: ResearchActorType
    trial_id: str = Field(min_length=1)
    candidate_fingerprint: str = Field(min_length=1)
    compute_cost: Decimal = Field(ge=0)
    blind_reveals: int = Field(ge=0)
    consumed_at: datetime
    scope: ResearchScope | None = None
    reservation_id: str | None = None

    model_config = {"frozen": True}

    @field_validator("consumed_at")
    @classmethod
    def _consumed_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class EvidenceQuality(BaseModel):
    data_quality: Decimal = Field(ge=0, le=1)
    sample_size: int = Field(ge=0)
    stability: Decimal = Field(ge=0, le=1)
    leakage_risk: Decimal = Field(ge=0, le=1)
    reproducibility: Decimal = Field(ge=0, le=1)

    model_config = {"frozen": True}


class ExpectedEdge(BaseModel):
    net_expected_return: Decimal
    confidence_low: Decimal
    confidence_high: Decimal
    benchmark_ref: str = Field(min_length=1)

    model_config = {"frozen": True}

    @model_validator(mode="after")
    def _confidence_order(self) -> ExpectedEdge:
        if self.confidence_high < self.confidence_low:
            raise ValueError("confidence_high 不能低于 confidence_low")
        return self


class RiskEstimate(BaseModel):
    max_drawdown: Decimal = Field(ge=0)
    tail_loss: Decimal = Field(ge=0)
    leverage_sensitivity: Decimal = Field(ge=0)
    liquidity_capacity: Decimal = Field(ge=0)

    model_config = {"frozen": True}


class EvidenceReport(BaseModel):
    report_id: str = Field(min_length=1)
    evidence_quality: EvidenceQuality
    expected_edge: ExpectedEdge
    risk_estimate: RiskEstimate
    experiment_refs: tuple[str, ...] = Field(min_length=1)
    created_at: datetime
    formal: bool = False
    benchmark: RawJson = None
    cost_stress: RawJson = None
    capacity: RawJson = None
    uncertainty: RawJson = None
    multiple_testing_adjustment: RawJson = None
    failure_criteria: RawJson = None
    limitations: tuple[str, ...] = ()
    lineage: ResearchLineage | None = None
    formal_artifact_id: str | None = None
    alpha_score: Decimal | None = None
    human_selected: bool = False

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class BlindChallengeResult(BaseModel):
    challenge_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    result_hash: str = Field(min_length=1)
    passed: bool
    revealed_at: datetime
    budget_consumption_id: str = Field(min_length=1)
    revealed_scope: str | None = None
    challenge_version: str = "m3-v05-blind-v1"

    model_config = {"frozen": True}

    @field_validator("revealed_at")
    @classmethod
    def _revealed_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class BlindChallengeRequest(BaseModel):
    frozen_release_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    requested_at: datetime

    model_config = {"frozen": True}

    @field_validator("requested_at")
    @classmethod
    def _requested_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class StrategyRelease(BaseModel):
    release_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    strategy_version: int = Field(ge=1)
    code_content_hash: str = Field(min_length=1)
    parameter_set_hash: str = Field(min_length=1)
    feature_set_ids: tuple[str, ...] = Field(min_length=1)
    dataset_manifest_ids: tuple[str, ...] = Field(min_length=1)
    cost_model_version: str = Field(min_length=1)
    environment_lock_hash: str = Field(min_length=1)
    evidence_report_id: str = Field(min_length=1)
    requested_capabilities: tuple[str, ...] = ()
    allowed_instruments: tuple[str, ...] = Field(min_length=1)
    allowed_timeframes: tuple[str, ...] = Field(min_length=1)
    manifest_hash: str = Field(min_length=1)
    status: ReleaseStatus = ReleaseStatus.DRAFT
    created_at: datetime
    created_by: str = Field(min_length=1)
    approved_at: datetime | None = None
    approved_by: str | None = None

    model_config = {"frozen": True}

    @field_validator("created_at", "approved_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime | None) -> datetime | None:
        return validate_utc_datetime(value) if value is not None else None


class ApprovalRecord(BaseModel):
    approval_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    status: ApprovalStatus
    approved_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class ReleaseDeployment(BaseModel):
    deployment_id: str = Field(min_length=1)
    release_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    status: DeploymentHealthStatus = DeploymentHealthStatus.STARTING
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class StrategyHealthAssessment(BaseModel):
    assessment_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    status: HealthAssessmentStatus
    health_version: int = Field(ge=1)
    metrics: StringMap
    evidence_refs: tuple[str, ...] = ()
    assessed_at: datetime
    valid_until: datetime
    research_disposition: ResearchDisposition = ResearchDisposition.NONE

    model_config = {"frozen": True}

    @field_validator("assessed_at", "valid_until")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _validity_order(self) -> StrategyHealthAssessment:
        if self.valid_until <= self.assessed_at:
            raise ValueError("health valid_until 必须晚于 assessed_at")
        return self


class LifecycleAction(BaseModel):
    action_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    action: LifecycleActionType
    reason: str = Field(min_length=1)
    requested_by: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class PositionIntent(BaseModel):
    intent_id: str = Field(min_length=1)
    deployment_id: str = Field(min_length=1)
    strategy_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    decision_time: datetime
    valid_until: datetime

    model_config = {"frozen": True}

    @field_validator("decision_time", "valid_until")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _validity_order(self) -> PositionIntent:
        if self.valid_until <= self.decision_time:
            raise ValueError("intent valid_until 必须晚于 decision_time")
        return self


class PortfolioRiskSnapshot(BaseModel):
    snapshot_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    position_and_policy_hash: str = Field(min_length=1)
    estimation_window: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    covariance_and_uncertainty: StringMap
    factor_exposures: StringMap
    var: Decimal = Field(ge=0)
    expected_shortfall: Decimal = Field(ge=0)
    scenario_losses: StringMap
    liquidity_adjusted_loss: Decimal = Field(ge=0)
    observed_at: datetime
    expires_at: datetime

    model_config = {"frozen": True}

    @field_validator("observed_at", "expires_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _expiry_order(self) -> PortfolioRiskSnapshot:
        if self.expires_at <= self.observed_at:
            raise ValueError("risk snapshot expires_at 必须晚于 observed_at")
        return self


class CapitalAllocationPolicy(BaseModel):
    policy_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    eligible_deployments: tuple[str, ...] = Field(min_length=1)
    capital_envelope_by_strategy: StringMap
    risk_budget_by_strategy: StringMap
    instrument_limits: StringMap
    turnover_budget: Decimal = Field(ge=0)
    capacity_constraints: StringMap
    portfolio_risk_snapshot_id: str = Field(min_length=1)
    valid_from: datetime
    expires_at: datetime
    approval_record_id: str = Field(min_length=1)

    model_config = {"frozen": True}

    @field_validator("valid_from", "expires_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _expiry_order(self) -> CapitalAllocationPolicy:
        if self.expires_at <= self.valid_from:
            raise ValueError("allocation policy expires_at 必须晚于 valid_from")
        return self


class AllocationPlan(BaseModel):
    allocation_plan_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    input_intents: tuple[str, ...] = Field(min_length=1)
    pre_trade_virtual_positions: StringMap
    target_virtual_positions: StringMap
    account_delta: Decimal
    allocation_rule_version: str = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class RiskReservation(BaseModel):
    reservation_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    intent_ids: tuple[str, ...] = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    reserved_quantity: Decimal = Field(gt=0)
    reserved_notional: Decimal = Field(ge=0)
    reserved_margin: Decimal = Field(ge=0)
    risk_snapshot_version: int = Field(ge=1)
    status: ReservationStatus = ReservationStatus.ACTIVE
    expires_at: datetime
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("expires_at", "created_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    @model_validator(mode="after")
    def _expiry_order(self) -> RiskReservation:
        if self.expires_at <= self.created_at:
            raise ValueError("reservation expires_at 必须晚于 created_at")
        return self


class OrderCommand(BaseModel):
    command_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    reservation_id: str = Field(min_length=1)
    allocation_plan_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    order_type: str = Field(min_length=1)
    limit_price: Decimal | None = Field(default=None, gt=0)
    status: OrderStatus = OrderStatus.CREATED
    state_version: int = Field(default=0, ge=0)
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class OrderState(BaseModel):
    """Versioned order state used by journal compare-and-set updates."""

    order_id: str = Field(min_length=1)
    status: OrderStatus
    state_version: int = Field(ge=0)
    filled_quantity: Decimal = Field(ge=0)
    late_fill_quantity: Decimal = Field(default=Decimal("0"), ge=0)
    updated_at: datetime

    model_config = {"frozen": True}

    @field_validator("updated_at")
    @classmethod
    def _updated_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)

    def transition(
        self,
        target: OrderStatus,
        *,
        expected_version: int,
        updated_at: datetime,
    ) -> OrderState:
        if expected_version != self.state_version:
            raise ValueError("订单状态版本已过期")
        validate_order_transition(self.status, target)
        validate_utc_datetime(updated_at)
        if updated_at < self.updated_at:
            raise ValueError("订单状态更新时间不能倒退")
        return self.model_copy(
            update={"status": target, "state_version": self.state_version + 1, "updated_at": updated_at}
        )

    def record_late_fill(self, quantity: Decimal, *, updated_at: datetime) -> OrderState:
        if quantity <= 0:
            raise ValueError("迟到 Fill 数量必须大于 0")
        validate_utc_datetime(updated_at)
        if self.status not in {OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED}:
            raise ValueError("只有终态订单允许追加迟到 Fill")
        if updated_at < self.updated_at:
            raise ValueError("迟到 Fill 时间不能倒退")
        return self.model_copy(
            update={
                "filled_quantity": self.filled_quantity + quantity,
                "late_fill_quantity": self.late_fill_quantity + quantity,
                "updated_at": updated_at,
            }
        )


class ExecutionReport(BaseModel):
    report_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    broker_order_id: str | None = None
    status: OrderStatus
    event_sequence: int | None = Field(default=None, ge=0)
    raw_payload: RawJson
    received_at: datetime

    model_config = {"frozen": True}

    @field_validator("received_at")
    @classmethod
    def _received_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class Fill(BaseModel):
    fill_id: str = Field(min_length=1)
    venue: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    client_order_id: str = Field(min_length=1)
    trade_id: str = Field(min_length=1)
    instrument_id: str = Field(min_length=1)
    side: OrderSide
    quantity: Decimal = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee: Decimal = Field(ge=0)
    fee_currency: str = Field(min_length=1)
    trade_time: datetime

    model_config = {"frozen": True}

    @field_validator("trade_time")
    @classmethod
    def _trade_time_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class BrokerCapabilities(BaseModel):
    supported_order_types: tuple[str, ...] = Field(min_length=1)
    supports_reduce_only: bool
    supports_client_order_lookup: bool
    supports_batch_cancel: bool
    supports_native_stop: bool
    supports_hedge_mode: bool
    execution_report_sequence_semantics: str = Field(min_length=1)
    rate_limits: StringMap

    model_config = {"frozen": True}


class AccountLedgerEntry(BaseModel):
    ledger_entry_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    asset: str = Field(min_length=1)
    amount: Decimal
    entry_type: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    occurred_at: datetime

    model_config = {"frozen": True}

    @field_validator("occurred_at")
    @classmethod
    def _occurred_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class StrategyLedgerEntry(AccountLedgerEntry):
    strategy_id: str = Field(min_length=1)
    ledger_type: LedgerType = LedgerType.STRATEGY


class ReconciliationRun(BaseModel):
    reconciliation_id: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    status: ReconciliationStatus
    local_version: int = Field(ge=0)
    exchange_snapshot_ref: str | None = None
    difference_refs: tuple[str, ...] = ()
    residual_reason_codes: tuple[str, ...] = ()
    started_at: datetime
    completed_at: datetime | None = None

    model_config = {"frozen": True}

    @field_validator("started_at", "completed_at")
    @classmethod
    def _timestamps_utc(cls, value: datetime | None) -> datetime | None:
        return validate_utc_datetime(value) if value is not None else None


class MarketChaosScenario(BaseModel):
    scenario_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    market_path: str = Field(min_length=1)
    liquidity_path: str = Field(min_length=1)
    funding_and_margin_path: str = Field(min_length=1)
    broker_fault_schedule: tuple[str, ...] = ()
    expected_invariants: tuple[str, ...] = Field(min_length=1)
    created_at: datetime

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _created_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)


class ChaosRun(BaseModel):
    chaos_run_id: str = Field(min_length=1)
    scenario_id: str = Field(min_length=1)
    release_or_policy_id: str = Field(min_length=1)
    system_version: str = Field(min_length=1)
    seed: int
    observed_actions: tuple[str, ...] = ()
    invariant_results: StringMap
    worst_loss: Decimal = Field(ge=0)
    freeze_latency_ms: int = Field(ge=0)
    run_at: datetime

    model_config = {"frozen": True}

    @field_validator("run_at")
    @classmethod
    def _run_at_utc(cls, value: datetime) -> datetime:
        return validate_utc_datetime(value)
