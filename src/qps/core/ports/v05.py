"""v0.5 cross-boundary Protocol contracts.

Only signatures and consistency semantics live here. Implementations belong to
infra/data/broker/research/trading packages.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime
    from decimal import Decimal

    from qps.core.constants import OrderStatus, ResearchActorType, TrialStatus
    from qps.core.models.foundation import (
        AccountLedgerEntry,
        AllocationPlan,
        ApprovalRecord,
        BlindChallengeResult,
        BrokerCapabilities,
        BudgetConsumption,
        CandidateDescriptor,
        CapitalAllocationPolicy,
        ChaosRun,
        DatasetManifest,
        ExecutionReport,
        FeatureDefinition,
        FeatureEvaluation,
        FeatureMaterialization,
        Fill,
        FormalRunArtifact,
        MarketChaosScenario,
        OrderCommand,
        PortfolioRiskSnapshot,
        ReconciliationRun,
        ResearchBudget,
        RiskReservation,
        StrategyHealthAssessment,
        StrategyLedgerEntry,
        StrategyRelease,
        TrialRegistration,
        WalkForwardWindow,
    )
    from qps.core.models.market import Bar, FeatureSet, FundingRate, MarkPrice, OpenInterest
    from qps.core.sdk.formal import VerifiedDecisionMaterialization


@runtime_checkable
class PointInTimeDataPort(Protocol):
    """PIT 查询必须显式带 decision_time/as-of。"""

    async def query(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
        *,
        decision_time: datetime,
        manifest_id: str,
    ) -> list[Bar]:
        """只返回 available_at <= decision_time 的记录。"""
        ...

    async def get_manifest(self, manifest_id: str) -> DatasetManifest:
        ...

    async def query_funding_rates(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[FundingRate]:
        ...

    async def query_open_interest(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[OpenInterest]:
        ...

    async def query_mark_prices(
        self, instrument_id: str, start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[MarkPrice]:
        ...

    async def query_features(
        self, instrument_id: str, feature_names: list[str], start: datetime, end: datetime, *,
        decision_time: datetime, manifest_id: str,
    ) -> list[FeatureSet]:
        ...


@runtime_checkable
class FeatureRegistryPort(Protocol):
    async def register_definition(self, definition: FeatureDefinition) -> None:
        """同一 feature_id/version 只能幂等写入，不得覆盖。"""
        ...

    async def register_materialization(self, materialization: FeatureMaterialization) -> None:
        ...

    async def record_evaluation(self, evaluation: FeatureEvaluation) -> None:
        ...

    async def get_definition(self, feature_id: str, version: int) -> FeatureDefinition:
        ...

    def get_materialization_sync(self, materialization_id: str) -> FeatureMaterialization:
        """读取 immutable materialization 元数据，供同步 trust-root 校验。"""
        ...


@runtime_checkable
class ManifestTrustPort(Protocol):
    """Manifest 的真实对象读取与 formal PIT 校验端口。"""

    def get(self, manifest_id: str) -> DatasetManifest:
        ...

    def verify_pit_manifest(
        self, manifest: DatasetManifest, *, expected_dataset_types: set[str],
        instrument_id: str | None = None, interval: str | None = None,
        require_formal: bool = False,
    ) -> DatasetManifest:
        ...


@runtime_checkable
class FormalDecisionMaterializerPort(Protocol):
    """Only trusted Release-derived PIT reads may produce formal input values."""

    async def derive(
        self, *, release: StrategyRelease, decision_time: datetime,
        instrument_id: str, interval: str,
    ) -> VerifiedDecisionMaterialization:
        ...


@runtime_checkable
class ResearchBudgetPort(Protocol):
    async def reserve_trial(
        self, *, registration: TrialRegistration,
        candidate: CandidateDescriptor,
        actor_type: ResearchActorType,
        requested_compute: Decimal,
        blind_reveals: int,
        reserved_at: datetime,
    ) -> tuple[BudgetConsumption, ...]:
        """服务端解析所有作用域并在一个事务中原子预留。"""
        ...

    async def finalize_trial(
        self, *, trial_id: str, terminal_status: TrialStatus,
        reason: str, finished_at: datetime,
    ) -> None:
        """只追加终态；不得退款、删除或重复扣减。"""
        ...

    async def reserve_reveal(self, *, trial_id: str, requested_at: datetime) -> BudgetConsumption:
        """独立 reveal quota 的不可恢复原子扣除。"""
        ...

    # Legacy methods remain for compatibility reads; formal paths must use the methods above.
    async def reserve(self, budget: ResearchBudget, consumption: BudgetConsumption) -> None:
        """实验启动前原子占用；超额、重复或盲测越界必须失败。"""
        ...

    async def consume(self, consumption: BudgetConsumption) -> None:
        ...

    async def remaining(self, budget_id: str) -> ResearchBudget:
        ...


@runtime_checkable
class FormalResearchPort(Protocol):
    """M3 v2 的唯一 formal research service 边界。"""

    async def run_formal(self, *args: object, **kwargs: object) -> FormalRunArtifact:
        ...

    async def rerun(self, trial_id: str) -> FormalRunArtifact:
        ...


@runtime_checkable
class WalkForwardPort(Protocol):
    """只追加、UTC 单调推进的 walk-forward 验证边界。"""

    async def run(self, *args: object, **kwargs: object) -> tuple[WalkForwardWindow, ...]:
        ...


@runtime_checkable
class BlindChallengePort(Protocol):
    async def run(
        self, *, frozen_release_id: str, trial_id: str,
        requested_at: datetime,
    ) -> BlindChallengeResult:
        """受限 runner 只接收冻结 Release 和 trial reference。"""
        ...


@runtime_checkable
class ReleaseStorePort(Protocol):
    async def save_release(self, release: StrategyRelease) -> None:
        """Release 内容按 release_id/hash 幂等，不允许 UPDATE。"""
        ...

    async def save_approval(self, approval: ApprovalRecord) -> None:
        ...

    async def get_release(self, release_id: str) -> StrategyRelease:
        ...


@runtime_checkable
class StrategyHealthPort(Protocol):
    async def record(self, assessment: StrategyHealthAssessment) -> None:
        ...

    async def latest(self, deployment_id: str) -> StrategyHealthAssessment:
        ...


@runtime_checkable
class PortfolioConstructionPort(Protocol):
    async def build_policy(
        self,
        account_id: str,
        risk_snapshot: PortfolioRiskSnapshot,
    ) -> CapitalAllocationPolicy:
        """只能生成版本化、可审批且不能自动扩大风险的 policy。"""
        ...


@runtime_checkable
class MarketRiskModelPort(Protocol):
    async def snapshot(self, account_id: str, *, observed_at: datetime) -> PortfolioRiskSnapshot:
        ...


@runtime_checkable
class ChaosScenarioPort(Protocol):
    async def register(self, scenario: MarketChaosScenario) -> None:
        ...

    async def run(self, scenario_id: str, *, seed: int, policy_id: str) -> ChaosRun:
        """只执行可重放场景并保存不可变结果。"""
        ...


@runtime_checkable
class OrderJournalPort(Protocol):
    async def create_command(self, command: OrderCommand, reservation: RiskReservation) -> None:
        """在任何 Broker 副作用前同一 ACID 事务持久化 command + reservation + outbox。"""
        ...

    async def transition(
        self,
        command_id: str,
        target_status: OrderStatus,
        *,
        expected_version: int,
        observed_at: datetime,
    ) -> OrderCommand:
        """compare-and-set；过期回报不得覆盖较新状态。"""
        ...

    async def record_report(self, report: ExecutionReport) -> None:
        """以 report 幂等键写 inbox，重复回报不得重复应用。"""
        ...

    async def record_fill(self, fill: Fill) -> None:
        ...


@runtime_checkable
class LedgerPort(Protocol):
    async def append_account(self, entry: AccountLedgerEntry) -> None:
        """source_type/source_id/entry_type 幂等，禁止修改历史分录。"""
        ...

    async def append_strategy(self, entry: StrategyLedgerEntry) -> None:
        ...

    async def allocation(self, plan: AllocationPlan, fill: Fill) -> tuple[StrategyLedgerEntry, ...]:
        ...


@runtime_checkable
class ReconciliationPort(Protocol):
    async def start(self, account_id: str) -> ReconciliationRun:
        ...

    async def complete(self, run: ReconciliationRun) -> ReconciliationRun:
        ...


@runtime_checkable
class BrokerPort(Protocol):
    async def capabilities(self) -> BrokerCapabilities:
        ...

    async def submit(self, command: OrderCommand) -> ExecutionReport:
        """必须复用 command.client_order_id；UNKNOWN 不得换 ID 重发。"""
        ...

    async def cancel(self, client_order_id: str) -> ExecutionReport:
        ...

    async def get_order(self, client_order_id: str) -> ExecutionReport:
        ...

    async def fills(self, client_order_id: str) -> list[Fill]:
        ...


@runtime_checkable
class AlertPort(Protocol):
    async def emit(self, level: str, message: str, *, correlation_id: str) -> None:
        ...


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime:
        """始终返回 UTC aware 时间；测试实现可固定。"""
        ...
