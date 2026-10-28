# ADR-017: M3 v0.5 可信研究设计门

## 状态

已替代（v1 设计门，2026-08-08）。由 ADR-018 的
`M3-v05-research-credibility-v2` 设计门替代；本文件、v1 矩阵和 review package 保留为历史追溯。

## 背景

历史 M3 的双引擎、成本与 Experiment Store 是 v0.4 实现事实，不足以证明 v0.5 研究结论可信。尤其是直接调用引擎、调用方选择单个预算、普通进程读取盲测数据，以及以 AlphaScore 自动晋升，都会绕过多重试验和 PIT 治理。

## 决策

### 正式调用图

```text
Research CLI / optimizer / script / future AI
  -> ResearchRunService.register_and_reserve_trial
  -> ResearchBudgetPort.reserve_trial (GLOBAL + STRATEGY_FAMILY + FEATURE_FAMILY + ACTOR，单事务)
  -> ReleaseStore + ManifestTrust + FeatureRegistry + historical rule/cost resolver
  -> EventDrivenBacktestEngine
  -> append-only ResearchRun / TrialOutcome / artifacts
  -> EvidenceReportBuilder
  -> optional BlindChallengePort.run (separate blind runner and reveal budget)
  -> append-only EvidenceReport
  -> human-selected evidence_report_id -> ReleaseBuilder
```

只有该调用图的事件驱动 run 可成为 EvidenceReport 或 Release 的 `evidence_report_id`。向量化、旧直接 engine 调用和没有预注册/预留的 run 仅可作为诊断，必须被标记为 non-formal，不能进入报告或晋升路径。

### 数据、rerun 与成本

ResearchRunService 只接受冻结 StrategyRelease 和显式 UTC scope。每个 decision 通过正式 PIT 端口读取，强制 `available_at <= decision_time`、标的/周期隔离及历史有效的 fee、funding、mark、margin/instrument rule。rerun 固定 Release/archive、manifest 与 feature materialization 内容 hash、规则/成本版本、环境锁、seed 和 clock；trades、equity 和 report 分别生成可比对的内容 hash。撮合、部分成交、延迟、失效、成本、强平和容量假设均进入不可变 run/report artifact。

### 预算、试验与恢复

`reserve_trial` 是唯一正式预算入口。它以 registration/candidate/actor 和请求额度解析所有有效作用域，并在一个存储事务中写入所有预留；超过任一额度、重复 trial、相似 candidate 归属冲突或盲测额度不足均 fail-closed。预留后写入只增 trial 生命周期：`REGISTERED -> RESERVED -> RUNNING -> {SUCCEEDED|FAILED|CANCELLED|TIMED_OUT}`。崩溃恢复和 retry 只能追加终态或重用同一 reservation，不得删除、退款、重置 trial 数或产生第二份消费。

candidate family 使用版本化相似度策略，同时比对代码/参数结构、Feature lineage、信号序列和暴露相关性；改名、微调及近重复均归入既有 family。普通调用方不能传入任意 budget_id 以缩小统计口径。

### 盲测与报告

Blind 数据、范围、答案和映射只由独立 challenge runner 的受限数据域读取；Research CLI、optimizer、策略进程、普通 ExperimentStore 查询和未来 RAG 均不可访问。runner 只接收冻结 Release 和 trial ref，先原子消耗独立 blind reveal 额度，再追加结果 hash、pass/fail 和已揭示标记。结果一经揭示，原区间永久失去 blind 状态，额度不可恢复。FeatureEvaluation 按 feature version、strategy family、experiment 和 dataset split 追加；blind 评价不回写普通 Registry，也不得汇成跨 split/family 的全局分数。

EvidenceReport 始终分别保存 EvidenceQuality、ExpectedEdge 与 RiskEstimate，包含基准、成本压力、容量、不确定性、多重试验调整、failure criteria 和局限。AlphaScore 仅为兼容展示。报告只表示研究证据；ReleaseBuilder 仅可引用人工明确选择的 append-only report，绝不自动审批、部署、分配资本或模拟 Forward Paper。

## 约束与非目标

- M3 不实现 Paper/Live、Broker、Portfolio、Risk 或真实日历 Forward Paper；这些属于 M4 及以后。
- 所有跨模块访问均经 `src/qps/core/ports/`；实现可保留旧接口兼容读取，但旧入口不得成为 formal 路径。
- 关键 PIT、预算和 Blind ACL enforcement point 必须各有集成/对抗性证明，预算恢复及这些关键控制至少各有一项 mutation 或 recovery 证明。

## 影响

M3 需扩展已有 ResearchBudgetPort 的正式语义并增加 BlindChallengePort；新增的 typed request/result 应在 `core` models/ports 定义、infra 实现存储与访问隔离，research/control 只经端口使用。该变更不修改已验收 M0/M1/M2 的历史结论；对它们的契约增补由 M3 新矩阵重新证明。
