# M3 v0.5 可信研究正式契约

ADR-017 的 v1 可执行契约补充。已由
[`m3-v05-research-credibility-v2.md`](m3-v05-research-credibility-v2.md) 与 ADR-018 替代；本文件仅用于
追溯 v1 package，不能作为后续实现或验收基线。

## 正式端口

```python
class ResearchBudgetPort(Protocol):
    async def reserve_trial(
        self, *, registration: TrialRegistration,
        candidate: CandidateDescriptor,
        actor_type: ResearchActorType,
        requested_compute: Decimal,
        blind_reveals: int,
        reserved_at: datetime,
    ) -> tuple[BudgetConsumption, ...]: ...

    async def finalize_trial(
        self, *, trial_id: str, terminal_status: TrialStatus,
        reason: str, finished_at: datetime,
    ) -> None: ...

class BlindChallengePort(Protocol):
    async def run(
        self, *, frozen_release_id: str, trial_id: str,
        requested_at: datetime,
    ) -> BlindChallengeResult: ...
```

`CandidateDescriptor` 必须冻结 candidate fingerprint、代码/参数结构 hash、Feature lineage、信号序列与暴露 refs；`TrialStatus` 只允许 `REGISTERED/RESERVED/RUNNING/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT`。普通调用方不传 `budget_id`；`reserve_trial` 服务端解析并在单事务内预留所有适用 GLOBAL、STRATEGY_FAMILY、FEATURE_FAMILY、ACTOR 预算，返回的消费记录均为只增记录。

`finalize_trial` 只能追加终态和原因，不得更新/删除 registration 或 reservation；崩溃恢复及 retry 使用同一个 `trial_id` 和预留。`BlindChallengePort` 的实现位于受限数据域，普通实验、CLI、策略/优化器/AI 身份没有读取 blind 原始数据、范围或答案的端口。

## 正式研究输入与输出

`ResearchRunService.register_and_reserve_trial` 是唯一可产出 formal run 的入口。它只接受冻结 Release 和显式 UTC scope，使用 formal PIT manifest/materialization、历史有效规则/成本、环境锁、seed 与 clock；输出将这些 lineage 及 trades/equity/report content hashes 写入 append-only artifact。

仅 EventDriven formal run 可被 `EvidenceReport` 引用或作为 `StrategyRelease.evidence_report_id` 的候选。向量化、直接 engine、无 registration 或无 reservation 的运行须明确为 `non-formal`，不能进入 Report、Release、审批、部署或资金分配调用图。
