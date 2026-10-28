"""M3 唯一 formal research 入口。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from decimal import Decimal

from qps.core.constants import ResearchActorType, TrialStatus
from qps.core.errors import BacktestError, DataError
from qps.core.models.experiment import BacktestRun
from qps.core.models.foundation import (
    BudgetConsumption,
    CandidateDescriptor,
    FormalRunArtifact,
    ResearchLineage,
    TrialEvent,
    TrialRegistration,
)
from qps.infra.storage.research_db import ResearchStore, canonical_json
from qps.research.candidate_family import CandidateFamilyResolver
from qps.research.evaluation.report import EvidenceReportBuilder


class RegisteredTrial:
    """服务端返回的预注册/预留句柄；不含任何 blind 原始内容。"""

    def __init__(self, registration: TrialRegistration, candidate: CandidateDescriptor,
                 reservations: tuple[BudgetConsumption, ...]) -> None:
        self.registration = registration
        self.candidate = candidate
        self.reservations = reservations

    @property
    def trial_id(self) -> str:
        return self.registration.trial_id

    @property
    def status(self) -> TrialStatus:
        return TrialStatus.RESERVED


class ResearchRunService:
    """所有可晋升研究必须从这里进入；旧 engine 只产生 non-formal 诊断。"""

    def __init__(self, *, research_store: ResearchStore, budget_port: Any,
                 release_store: Any, event_driven_engine: Any,
                 family_resolver: CandidateFamilyResolver | None = None,
                 report_builder: EvidenceReportBuilder | None = None) -> None:
        self.store = research_store
        self.budget = budget_port
        self.release_store = release_store
        self.engine = event_driven_engine
        self.family_resolver = family_resolver or CandidateFamilyResolver(research_store)
        self.report_builder = report_builder or EvidenceReportBuilder(research_store)

    @staticmethod
    def _require_registration(registration: TrialRegistration, candidate: CandidateDescriptor,
                              frozen_release_id: str | None) -> TrialRegistration:
        if registration.candidate_descriptor is not None and registration.candidate_descriptor != candidate:
            raise DataError(detail="trial candidate descriptor 不能在 reserve 时改变")
        if registration.utc_scope is None:
            raise DataError(detail="formal trial 必须声明 UTC scope")
        if frozen_release_id is None and registration.release_id is None:
            raise DataError(detail="formal trial 必须绑定 frozen StrategyRelease")
        if frozen_release_id is not None and registration.release_id not in {None, frozen_release_id}:
            raise DataError(detail="trial Release lineage 与请求不一致")
        missing_lineage = {
            "data_manifest_hash": registration.data_manifest_hash,
            "feature_materialization_hash": registration.feature_materialization_hash,
            "rule_version": registration.rule_version,
            "cost_version": registration.cost_version,
            "historical_rule_hash": registration.historical_rule_hash,
            "cost_hash": registration.cost_hash,
            "environment_lock": registration.environment_lock,
            "clock_id": registration.clock_id,
        }
        if any(not value for value in missing_lineage.values()):
            missing = ", ".join(key for key, value in missing_lineage.items() if not value)
            raise DataError(detail=f"formal trial 缺少可信 lineage: {missing}")
        return registration.model_copy(update={
            "candidate_descriptor": candidate,
            "release_id": frozen_release_id or registration.release_id,
        })

    async def _verify_frozen_release(self, release_id: str) -> Any:
        release = await self.release_store.get_release(release_id)
        status = getattr(release, "status", None)
        if status is not None and getattr(status, "value", status) not in {"FROZEN", "RESEARCH_VALIDATED", "LIVE_CANDIDATE"}:
            raise DataError(detail=f"formal research 只能使用冻结/研究验证 Release: {release_id}")
        return release

    async def register_and_reserve_trial(
        self, *, registration: TrialRegistration, candidate: CandidateDescriptor,
        actor_type: ResearchActorType, requested_compute: Decimal,
        blind_reveals: int, reserved_at: datetime,
        frozen_release_id: str | None = None, request: dict[str, Any] | None = None,
    ) -> RegisteredTrial | BacktestRun:
        """预注册、解析 family、全 scope 原子 reserve；可选 request 仅用于紧接着运行。"""
        registration = self._require_registration(registration, candidate, frozen_release_id)
        release_id = registration.release_id
        assert release_id is not None
        release = await self._verify_frozen_release(release_id)
        family_id = self.family_resolver.resolve(candidate, resolved_at=reserved_at)
        candidate = candidate.model_copy(update={"strategy_family_id": family_id})
        registration = registration.model_copy(update={"strategy_family_id": family_id, "candidate_descriptor": candidate})
        self.store.append_registration(registration)
        reservations = await self.budget.reserve_trial(
            registration=registration, candidate=candidate, actor_type=actor_type,
            requested_compute=requested_compute, blind_reveals=blind_reveals,
            reserved_at=reserved_at,
        )
        self.store.append_trial_event(TrialEvent(
            event_id=f"{registration.trial_id}:RESERVED", trial_id=registration.trial_id,
            status=TrialStatus.RESERVED, reason="all applicable research budgets reserved",
            occurred_at=reserved_at,
        ))
        handle = RegisteredTrial(registration, candidate, reservations)
        if request is None:
            return handle
        return await self._run_formal_run(handle, request=request, release=release)

    async def _run_formal_run(self, trial: RegisteredTrial | str, *, request: dict[str, Any], release: Any | None = None) -> BacktestRun:
        trial_id = trial if isinstance(trial, str) else trial.trial_id
        registration = self.store.get_registration(trial_id)
        if self.store.latest_trial_status(trial_id) != TrialStatus.RESERVED:
            raise DataError(detail=f"formal run 必须从 RESERVED 开始: {trial_id}")
        release_id = registration.release_id
        if release_id is None:
            raise DataError(detail="formal run 缺少 Release lineage")
        release = release or await self._verify_frozen_release(release_id)
        if request.get("release_id") not in {None, release_id}:
            raise DataError(detail="formal request Release 与 registration 不一致")
        request = dict(request)
        request.update({"release_id": release_id, "formal": True, "trial_id": trial_id})
        if registration.utc_scope is not None:
            request.setdefault("start", registration.utc_scope[0].isoformat())
            request.setdefault("end", registration.utc_scope[1].isoformat())
        started_at = datetime.now(UTC)
        self.store.append_trial_event(TrialEvent(
            event_id=f"{trial_id}:RUNNING", trial_id=trial_id, status=TrialStatus.RUNNING,
            reason="formal EventDriven run started", occurred_at=started_at,
        ))
        request_hash = hashlib.sha256(canonical_json(request).encode()).hexdigest()
        self.store.append_artifact(artifact_id=f"{trial_id}:request", trial_id=trial_id,
                                   artifact_type="formal-request", content_hash=request_hash,
                                   payload=request, created_at=started_at)
        try:
            runner = getattr(self.engine, "run_formal", None)
            if runner is None:
                raise BacktestError(detail="formal engine adapter 缺少 run_formal")
            result = await runner(request, trial_id=trial_id, release=release)
            if not isinstance(result, BacktestRun):
                raise BacktestError(detail="formal engine 返回类型非法")
            if result.engine_config.get("formal") is not True:
                raise BacktestError(detail="EventDriven run 未被 formal 入口标记")
            artifact = self._actual_artifact(registration, release, result)
            self.store.append_formal_run_artifact(artifact)
            result = result.model_copy(update={"engine_config": {
                **result.engine_config, "trial_id": trial_id,
                "lineage": self.store.lineage_from_artifact(artifact).model_dump(mode="json"),
                "formal_artifact_id": artifact.artifact_id,
            }})
            if hasattr(self.report_builder, "build_from_formal_run"):
                await self.report_builder.build_from_formal_run(
                    formal_run_artifact_id=artifact.artifact_id,
                )
            else:
                raise BacktestError(detail="formal report builder 缺少 v2 artifact 入口")
            self.store.append_trial_event(TrialEvent(
                event_id=f"{trial_id}:SUCCEEDED", trial_id=trial_id, status=TrialStatus.SUCCEEDED,
                reason="formal run and EvidenceReport appended", occurred_at=datetime.now(UTC),
            ))
            await self.budget.finalize_trial(trial_id=trial_id, terminal_status=TrialStatus.SUCCEEDED,
                                             reason="formal run succeeded", finished_at=datetime.now(UTC))
            return result
        except Exception as exc:
            try:
                self.store.append_trial_event(TrialEvent(
                    event_id=f"{trial_id}:FAILED", trial_id=trial_id, status=TrialStatus.FAILED,
                    reason=str(exc)[:500], occurred_at=datetime.now(UTC),
                ))
                await self.budget.finalize_trial(trial_id=trial_id, terminal_status=TrialStatus.FAILED,
                                                 reason=str(exc)[:500], finished_at=datetime.now(UTC))
            except Exception:
                # Preserve the original failure; a second write cannot turn it into success.
                pass
            if isinstance(exc, BacktestError):
                raise
            raise BacktestError(detail=f"formal research run failed: {exc}") from exc

    async def run_formal(self, trial: RegisteredTrial | str, *, request: dict[str, Any], release: Any | None = None) -> FormalRunArtifact:
        """Typed v2 formal boundary; only the persisted actual artifact escapes."""
        run = await self._run_formal_run(trial, request=request, release=release)
        artifact_id = run.engine_config.get("formal_artifact_id")
        if not isinstance(artifact_id, str):
            raise BacktestError(detail="formal run 缺少 persisted FormalRunArtifact reference")
        artifact = self.store.get_formal_artifact_by_id(artifact_id)
        if artifact is None:
            raise BacktestError(detail="formal artifact reference 无法解析")
        return artifact

    async def run_formal_artifact(
        self, trial: RegisteredTrial | str, *, request: dict[str, Any], release: Any | None = None,
    ) -> FormalRunArtifact:
        """v2 typed boundary; callers receive the verified actual artifact."""
        return await self.run_formal(trial, request=request, release=release)

    def _actual_artifact(
        self, registration: TrialRegistration, release: Any, run: BacktestRun,
    ) -> FormalRunArtifact:
        """Extract actual values from the engine output; never fill from registration."""
        config = run.engine_config
        release_archive_hash = str(config.get("release_archive_hash") or "")
        if release_archive_hash != str(getattr(release, "manifest_hash", "")):
            raise DataError(detail="formal run 缺失或伪造 Release archive actual hash")
        required = {
            "data_manifest_hash": config.get("data_manifest_hash"),
            "feature_materialization_hash": config.get("feature_materialization_hash"),
            "historical_rule_hash": config.get("historical_rule_hash"),
            "cost_hash": config.get("cost_hash"),
            "environment_lock": run.environment_lock or config.get("environment_lock"),
            "random_seed": config.get("random_seed"),
            "clock": config.get("clock"),
            "trades_hash": config.get("trades_hash"),
            "equity_hash": config.get("equity_hash"),
            "report_hash": config.get("report_hash"),
        }
        missing = [key for key, value in required.items() if value in (None, "")]
        if missing:
            raise DataError(detail=f"formal engine 缺少 actual lineage: {', '.join(missing)}")
        metrics = run.metrics.model_dump(mode="json") if run.metrics is not None else None
        if metrics is None:
            raise DataError(detail="formal engine 缺少 actual metrics")
        capacity = config.get("capacity")
        if not isinstance(capacity, dict):
            raise DataError(detail="formal engine 缺少 actual capacity")
        artifact_base: dict[str, Any] = {
            "trial_id": registration.trial_id, "run_id": run.run_id,
            "release_id": str(getattr(release, "release_id", "")),
            "release_archive_hash": release_archive_hash,
            "data_manifest_hash": str(required["data_manifest_hash"]),
            "feature_materialization_hash": str(required["feature_materialization_hash"]),
            "historical_rule_hash": str(required["historical_rule_hash"]),
            "cost_hash": str(required["cost_hash"]),
            "environment_lock": str(required["environment_lock"]),
            "seed": int(str(required["random_seed"])), "clock": str(required["clock"]),
            "trades_hash": str(required["trades_hash"]),
            "equity_hash": str(required["equity_hash"]),
            "report_hash": str(required["report_hash"]),
        }
        lineage = ResearchLineage(
            release_id=str(artifact_base["release_id"]),
            release_archive_hash=str(artifact_base["release_archive_hash"]),
            data_manifest_hash=str(artifact_base["data_manifest_hash"]),
            feature_materialization_hash=str(artifact_base["feature_materialization_hash"]),
            rule_version=str(artifact_base["historical_rule_hash"]),
            cost_version=str(artifact_base["cost_hash"]),
            environment_lock=str(artifact_base["environment_lock"]),
            seed=int(artifact_base["seed"]), clock=str(artifact_base["clock"]),
        )
        payload = canonical_json(run)
        artifact = FormalRunArtifact(
            artifact_id=f"{registration.trial_id}:formal-run:{run.run_id}",
            **artifact_base,
            canonical_lineage_hash=hashlib.sha256(canonical_json(lineage).encode()).hexdigest(),
            run_payload_hash=hashlib.sha256(payload.encode()).hexdigest(),
            metrics=cast("Any", metrics),
            capacity=cast("Any", capacity),
            cost_breakdown=cast("Any", metrics.get("cost_breakdown")),
            benchmark=cast("Any", config.get("benchmark")),
            created_at=datetime.now(UTC),
        )
        return artifact

    async def _rerun_run(self, trial_id: str) -> BacktestRun:
        artifacts = self.store.get_artifacts(trial_id, "formal-request")
        if not artifacts:
            raise DataError(detail=f"formal trial request 不存在: {trial_id}")
        request = artifacts[-1]["payload"]
        # Rerun is a new append-only run artifact but retains the same trial/reservation.
        registration = self.store.get_registration(trial_id)
        release = await self._verify_frozen_release(registration.release_id or "")
        request = {**request, "rerun": True}
        result = await self.engine.run_formal(dict(request), trial_id=trial_id, release=release)
        # Some deterministic engines derive run_id solely from the frozen
        # request.  A rerun is nevertheless a new append-only run, so avoid
        # reusing an existing artifact identity while preserving all output
        # hashes that are compared below.
        existing_runs = self.store.get_artifacts(trial_id, "formal-run-v2")
        existing_ids = {str(item["payload"].get("run_id")) for item in existing_runs if isinstance(item.get("payload"), dict)}
        if result.run_id in existing_ids:
            result = result.model_copy(update={
                "run_id": f"{result.run_id}:rerun:{len(existing_runs) + 1}",
            })
        expected_artifact = self.store.get_artifacts(trial_id, "formal-run-v2")[-1]["payload"]
        expected_config = expected_artifact if isinstance(expected_artifact, dict) else {}
        try:
            actual_artifact = self._actual_artifact(registration, release, result)
        except DataError as exc:
            raise BacktestError(detail=f"rerun lineage 不一致，已 fail-closed: {exc}") from exc
        actual_config = actual_artifact.model_dump(mode="json")
        for key in (
            "release_archive_hash", "data_manifest_hash", "feature_materialization_hash",
            "historical_rule_hash", "cost_hash", "environment_lock", "seed", "clock",
            "trades_hash", "equity_hash", "report_hash",
        ):
            if expected_config.get(key) != actual_config.get(key):
                raise BacktestError(detail=f"rerun {key} 不一致，已 fail-closed")
        self.store.append_formal_run_artifact(actual_artifact)
        await self.report_builder.build_from_formal_run(
            formal_run_artifact_id=actual_artifact.artifact_id,
        )
        return cast("BacktestRun", result.model_copy(update={"engine_config": {
            **result.engine_config, "formal_artifact_id": actual_artifact.artifact_id,
        }}))

    async def rerun(self, trial_id: str) -> FormalRunArtifact:
        """Typed v2 rerun boundary; append and return the new artifact."""
        run = await self._rerun_run(trial_id)
        artifact_id = run.engine_config.get("formal_artifact_id")
        if not isinstance(artifact_id, str):
            raise BacktestError(detail="rerun 未追加 FormalRunArtifact")
        artifact = self.store.get_formal_artifact_by_id(artifact_id)
        if artifact is None:
            raise BacktestError(detail="rerun artifact reference 无法解析")
        return artifact

    async def rerun_artifact(self, trial_id: str) -> FormalRunArtifact:
        """Named alias for callers migrating from the v1 compatibility API."""
        return await self.rerun(trial_id)


__all__ = ["RegisteredTrial", "ResearchRunService"]
