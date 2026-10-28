# ADR-018：M3 v0.5 全生命周期可信研究 v2 设计门

## 状态

已接受（设计门，2026-08-08）。本 ADR 取代 ADR-017 对
`M3-v05-research-credibility-v1` 的实施基线；v1 的 review package、证据和失败记录保留为
历史，不能迁移为 v2 通过证据。

## 背景

M3 v1 的 16 行矩阵与 `PREFLIGHT_PASSED` package 只能证明已登记的局部控制。对正式调用图
重新审查后，同一 formal lineage 控制在定向修复后仍可由缺失 engine actual lineage 与伪造
report lineage 绕过；按 review budget 必须回设计门。全阶段覆盖审查又确认 v1 未覆盖滚动
walk-forward/OOS、受统一预算约束的 optimizer、真实 CLI 生命周期、报告完整性、
`ReleaseBuilder.build()` 的 archive 链路及所有研究记录的恢复语义。

这些不是再加一条断言即可修复的问题：M3 必须把“候选、一次运行、报告和可交给 M4 的 Release
candidate”绑定为一条可证明、可重放、只增的研究事实链。

## 决策

### 1. v2 正式研究图与唯一权威 lineage

```text
Research CLI / script / optimizer / future AI
  -> TrialRegistration + CandidateDescriptor
  -> ResearchRunService.register_and_reserve_trial
  -> server-side family resolution + GLOBAL/STRATEGY_FAMILY/FEATURE_FAMILY/ACTOR atomic reservation
  -> frozen StrategyRelease + trusted PIT sources + historical rule/cost resolver
  -> EventDriven formal run
  -> immutable FormalRunArtifact (actual lineage + output hashes)
  -> EvidenceReport derived from verified FormalRunArtifact
  -> optional local isolated BlindChallenge runner
  -> append-only human selection
  -> ReleaseBuilder.build() candidate only
```

`FormalRunArtifact` 是唯一的实际 lineage 权威来源。registration 只表达预期，不能补齐或覆盖
引擎实际值。artifact 至少固化 Release/archive、manifest、FeatureMaterialization、历史规则、
成本版本的内容 hash，以及环境锁、seed、UTC clock、trades/equity/report hash 和 trial/run
绑定。实际字段缺失、与 registration 不等、artifact/report 被篡改或 report 与 artifact 不一致
都必须 fail-closed。

`EvidenceReportBuilder` 不接受调用方自由提供的 lineage；它从已验证 artifact 派生。存储层在
append report 时再次验证 trial、run、release、artifact 和 report lineage 的 canonical equality。
rerun 只追加新的 run/artifact，并逐项比较冻结输入和输出 hash；不得覆盖或合成来源 hash。

### 2. 完整 M3 验证层与研究预算

Development、Validation（含至少两个 UTC 推进的 walk-forward OOS 窗口）和 Blind Challenge
必须分层。每个窗口记录边界、输入 hash、参数、指标和失败原因；purge/embargo 不能关闭或由
调用方降级。Forward Paper 属于 M4：M3 只可生成候选，不伪造日历时间结果。

所有人工、script、optimizer 和未来 AI 候选均先登记，再由服务端解析 family 并在一个事务中
预留 GLOBAL、STRATEGY_FAMILY、FEATURE_FAMILY、ACTOR 四作用域预算。formal optimizer 可为
“候选生成器 + 每候选进入 ResearchRunService”的适配器，不能把 legacy `Optimizer.search()` 的
非正式运行称为 M3 证据。失败、取消、超时、崩溃和 retry 均保留且计数；不能退款、重置或只保存
赢家。

### 3. 证据、报告、Release 和 CLI

正式 EventDriven 必须端到端使用 PIT 数据和历史有效的 instrument/cost rule，记录撮合、部分成交、
延迟、过期、费用、滑点、funding、借贷、强平与容量假设。报告必须实际包含并校验：基准、成本
压力、容量、样本/区间不确定性、多重试验调整、预注册 failure criteria、局限，以及独立的
`EvidenceQuality`、`ExpectedEdge`、`RiskEstimate`。默认零值或占位文本不是完成证据。

M3 CLI 必须覆盖 register、formal-backtest、formal optimizer candidate、report/show/compare、rerun、
challenge/reveal 和重启后的同库可见性；普通/legacy run 保持 non-formal。只有人工追加选择的
formal report 才能由真实 `ReleaseBuilder.build()` 固化到 archive；这只产生 M4 候选，绝不自动
approve/deploy/allocate capital。

registration、reservation、trial event、run、artifact、report、FeatureEvaluation、blind result 与
selection 都是 append-only；相同内容幂等、不同内容冲突，UPDATE/DELETE、部分失败和重启均不得
改写既有 hash。

### 4. 本地项目的隔离与外部接口边界

本项目是个人单机自用项目。v2 不引入公网攻击防护、网络 namespace、远程多租户隔离或真实交易所
凭证要求。盲测的本地受限 runner、文件数据域和普通调用者的读取拒绝仍是研究完整性边界，必须
保留。默认使用可重复的 persisted local fixture；只有某项证明确实需要真实外部数据/接口时，
代码开发者按 v2 Prompt 的最小只读请求模板向项目负责人暂停请求，未获提供即保持
`EVIDENCE_BLOCKED`。

### 5. 冻结的 v2 任务和矩阵

`tasks/M3-v05-research-credibility-v2/` 使用 matrix ID
`M3-v05-research-credibility-v2`，从空 evidence 重新取证。其 20 个原子行覆盖：PIT/历史规则和
正式入口、actual lineage、rerun、撮合成本、容量、walk-forward、预算恢复、trial lifecycle、
optimizer、family、blind ACL/reveal、FeatureEvaluation、报告完整性、ReleaseBuilder、CLI 与
append-only recovery。v1 passed 记录不得复制。

## 非目标

- 不实现 M4 Paper 的真实日历时间、M5 Live、Broker、Portfolio 或 Risk；
- 不自动批准 Release、部署或分配资本；
- 不用本地 unit test 代替未来需要的真实外部接口事实。

## 影响

实现应先同步 core model/port、研究契约和 SQLite append-only schema，再接入 service、engine、
optimizer、report、ReleaseBuilder 与 CLI。代码开发者只能在 v2 矩阵的冻结范围内实施并至多声明
`CODE_COMPLETE`；全部新证据通过 preflight 后才可进入独立 Review。M2 当前
`EVIDENCE_BLOCKED`/catalog 漂移是独立前置治理项，不能被本 ADR 改写。

## 日期

2026-08-08
