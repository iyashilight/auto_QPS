# ADR-015：M2 v0.5 Runtime / Release / State 设计门提案

## 状态

设计门已批准，代码已达到 `CODE_COMPLETE`。D3 的真实 persisted fixture 曾暴露单一
bars manifest 被错误复用于 funding/mark-price 查询；随后代码改为从 Release 解析 typed
source。该修复新增稳定的多数据集 lineage 语义，故本 ADR 的 2026-08-08 修订冻结
`M2-v05-runtime-release-state-v5`，取代 v4。v4 及之前的证据只作历史留存，不能移植为
v5 通过结论。capability-positive Linux 与真实 persisted formal/CLI fixture 证据尚未齐全，
因此机器派生状态为 `EVIDENCE_BLOCKED`，不得进入独立架构复审。设计门通过、代码完成或
本地三绿均不等于 M2 v0.5 验收通过；只有 Review Compiler 生成固定 Review 包后 Reviewer
才能受理。不得生成第三个编号修复 Prompt。

## 背景

M2 v0.5 第二次定向架构复审未通过。问题不是单个断言或测试缺失，而是正式执行路径仍存在多个可绕过的边界：

- Runtime worker 可以构造自定义 Context，把未经 `IntentBuilder` 校验的任意对象作为成功 Intent 返回；
- Release archive 的 `parameters.json` 被篡改后，`ReleaseStore.get_release()` 没有依据归档内容拒绝；
- 正式回测调用图仍直接使用 legacy Context 和 feature callback；
- State 的 canonical lineage、提交前崩溃恢复和重复 Intent 语义未形成一个可证明的持久化协议；
- OS 隔离只能证明当前主机能力不足时 fail-closed，尚未有文件根、进程组、网络和资源限制的正向证明；
- formal dependency positive fixture 与 CLI 端到端的失败无副作用证据不足。

因此本 ADR 重新定义 M2 的执行边界和证据要求。它修订并取代 ADR-014 中“可选 strict Context、运行时自定义 Context、只校验部分 archive 文件”的实现性表述；ADR-014 保留为历史记录。

本 ADR 的 D1～D7 是设计层分组；机器验收已拆为
`tasks/M2-v05-strategy-framework/matrix.yaml` 中的 v3 原子行，并显式声明
`depends_on`、证明类型、执行环境和单一权威控制点。v3 进一步拆开 State
rollback/replay、网络/文件根/资源/进程清理、Release lineage 与 CLI restart，
避免一个通过结果掩盖同族安全性质。v1/v2 继续保留用于追溯，不再直接决定当前状态。

## 决策

### 1. 统一正式执行内核

Runtime 和正式回测必须共用同一个 `FormalStrategyExecutor` 和 `FormalDecisionInputFactory`。两者只在驱动方式上不同：Runtime 由 IPC 提供一个输入快照，回测由事件循环按决策时点提供一个输入快照。

正式调用图固定为：

```text
canonical Release
  -> FormalDecisionInputFactory
  -> FormalStrategyExecutor
  -> strict StrategyContext + IntentBuilder
  -> validated IntentEnvelope
  -> canonical StateCommit
```

`event_driven.py` 不得再直接构造带 `feature_pipeline` 的 legacy `StrategyContext`。legacy Context 只能由显式命名的历史/单元测试适配器创建，不能被正式 DI、Runtime 或正式回测发现或调用。

### 2. Intent 必须在 child 和 parent 两侧形成闭环

策略代码只能调用正式 Context 的 `emit_intent()`。该方法接收受约束的 `IntentRequest`（或由 SDK 解析为该模型的 JSON object），内部唯一调用 `IntentBuilder`，并只向策略暴露不可变的 `Intent` 结果视图。

worker 禁止定义自己的 Context、`emit_intent` 收集器或 raw Intent 输出分支。worker 只负责加载 Release 提供的执行入口，并通过 `FormalStrategyExecutor` 运行。

父进程收到结果后必须重新执行 canonical decode/validation：

- schema/version、字段白名单、UTC `decision_time`、Decimal、数量/价格语义；
- `release_id`、strategy/version、deployment、instrument、interval 和能力清单；
- Intent 数量上限及“一次输入最多一个确定性 Intent”规则；
- `intent_id/event_id = H(release_id, deployment_id, input_snapshot_ref, canonical_intent)`；
- 任何失败都返回结构化 strategy failure，不能返回 `type=result`。

这样即使 child 被篡改、使用旧 SDK 或绕过 Context，parent 也不会把非法对象提升为成功 Intent。

### 3. Release 以内容清单为信任根

每个归档必须包含 canonical `release-manifest.json`，清单至少记录：

- `release_id`、strategy/version、代码入口和 SDK protocol version；
- `parameters.json`、`environment.json`、evidence report 的 SHA-256、大小和文件模式；
- 依赖对象的完整 ID、hash、instrument/interval、formal status、输入 manifest 集合；
- dataset manifest、FeatureMaterialization、cost model、capabilities 的 canonical hash 和 lineage；
- archive file set，明确禁止 symlink、路径穿越、额外文件和目录外文件。

`ReleaseStore.get_release()` 每次读取都必须重新扫描归档并比较每个文件 hash/size/mode、manifest hash 以及数据库中的 Release 字段。只要任一项不匹配，就返回 `DataError`；不能以数据库行存在、目录可读或 Release 状态为成功依据。

生产 `ReleaseBuilder` 不暴露可绕过资格门的 `require_dependencies=False`。离线测试使用独立 fake store/fixture，不能改变正式 builder 的语义。归档和 MetaDB marker 必须采用临时目录、fsync、原子 rename、单事务 marker；任何失败后不得留下可被 `get_release()` 接受的 archive 或成功记录。

### 4. 正式 PIT 输入为预物化、固定时点的值对象

`FormalDecisionInputFactory` 接收已验证 Release、PIT manifest、`decision_time`、instrument 和 interval，返回不可变 `DecisionInput`：

- 所有数据查询显式传递 `available_at <= decision_time`；
- feature 值在父进程按该决策时点物化，child/策略不能传入任意 callback 或时间范围；
- 输入携带 source manifest、feature materialization、schema 和 content hash；
- `DecisionInput` 的引用和 hash 进入 Intent 与 State lineage。

策略 Context 只提供 `read_feature(name, instrument)` 读取当前 `DecisionInput`，不提供无界查询、candidate manifest、未来时间或可变 reader。固定 clock 只能返回当前输入的 `decision_time`。

### 4.1 正式回测的 typed dataset routing

正式回测请求只携带 `release_id`、UTC `decision_time` 和 caller 已选择的 bars
`manifest_id`。引擎必须加载不可变 Release，并从其依赖中解析所有其他数据来源；caller
不得提供 funding、mark-price、feature output 或任意 dataset-type 到 manifest 的映射。

- Release 必须恰有一个 formal bars manifest，且请求 `manifest_id` 必须与其完全相等；
- Release 的每个 feature materialization 必须解析到同一个 formal feature output manifest；
- Release 对每种 supplemental dataset type 最多声明一个 formal manifest。引擎只能把
  funding manifest 传给 funding 查询、mark-price manifest 传给 mark-price 查询，禁止将
  bars manifest 复用为 supplemental input；
- 当 `CostConfig.funding_rate_source=REAL` 时，formal funding manifest 是必需依赖；缺失、
  候选、重复、跨标的/周期或 hash/quality/lineage 不匹配必须使回测失败，不能以零 funding
  继续；
- 当正式回测启用 liquidation/mark-price 语义时，formal mark-price manifest 是必需依赖。
  若阶段只批准 bars mark-to-market，必须在 `CostConfig` 和 Release manifest 中显式声明该
  选择；不得把未声明的缺失 mark-price 静默当作 bars；
- 所有已解析的 source ID 与 content hash 必须进入 `BacktestRun.engine_config`、
  `DecisionInput` 和审计 lineage，以支持重放与篡改验证。

该规则不新增 caller-facing supplemental manifest 参数，也不允许从目录扫描“最新”数据。它
把多数据集选择固定为 Release 的不可变内容，而非 CLI 或运行时的可变输入。

### 5. State 采用 prepare/commit 日志协议

State 不再以一个“快照表”同时承担输入、输出和恢复语义。规范化持久化记录分为：

- `state_attempt`：`attempt_id`、deployment/release/schema、input ref/hash、previous state hash、canonical state、canonical Intent、Intent/event hash、decision/recorded time；
- `state_commit`：唯一 `(deployment_id, input_snapshot_ref)`、commit version、attempt hash 和 commit time。

父进程必须先完成 result canonical validation，再在一个 SQLite 事务内写入 attempt 和 commit。事务提交前崩溃视为未提交，重试同一 input；若已有 commit，直接返回原始 canonical Intent/State，不再次执行策略；同一 input 的内容不一致或 Release/schema 不兼容必须失败并冻结 deployment。

State hash 使用明确的 canonical JSON/MessagePack 编码，禁止 `default=str`。hash 链为 `H(schema, release, input, previous_state_hash, state, intent)`；schema 变更必须有显式 migration handler，不能静默以新代码恢复旧 State。

### 6. OS 隔离必须由能力后端和正向测试证明

Runtime 抽象 `IsolationBackend`，生产实现要求 Linux：network namespace、受限文件根、显式环境 allowlist、CPU/AS/NPROC 或 cgroup 限制、独立 session/process group 和可验证的 kill/cleanup。archive 复制或挂载到专用临时根后，child 的 cwd 必须位于该根，且不能跟随 symlink 越界。

能力探测失败继续 fail-closed。`require_os_isolation=False` 不再属于生产配置，仅可用于协议单测。正向验收必须在具备 namespace 能力的 Linux runner 实测：child 无网、无法读根目录/父工作区/secret、超时杀死整个进程组、同 deployment 故障不影响另一个 deployment。

### 7. CLI 只验证真实正式链路

CLI e2e 使用独立 `QPS_DATA_DIR` 和真实 formal manifest/feature materialization fixture，依次覆盖 `create -> check -> package/release -> show -> approve`。每个失败场景都断言非零退出码、结构化错误、无 archive/marker/audit 假成功；成功路径断言 archive 内容 hash、审批审计和重启后可见性。CLI 不得通过测试专用 builder flag 取得正式成功。

## 不在本设计范围

- 不实现 M3 Research Budget、M4 Broker/Portfolio/Risk 或任何交易副作用；
- 不把 OS 隔离能力不足转成降级运行；
- 不删除 legacy SDK，只禁止它进入 M2 正式执行图；
- 不在本 ADR 中宣布 M2 通过。

## 设计门验收矩阵

| ID | 设计问题 | 必须批准的证据 |
| --- | --- | --- |
| D1 | Intent 不可绕过 | child raw/非法字段、错误身份、多个 Intent 均被 parent 判为 strategy failure；合法 Intent 的 deterministic ID 可重放 |
| D2 | Release 内容可信 | 修改 parameters/environment/code、增加文件、symlink、路径穿越、删文件后 `get_release()` 均失败；失败无 marker/orphan |
| D3 | 正式 Context 与 typed lineage 唯一 | formal backtest 与 Runtime 均调用同一 executor/input factory；bars、feature、funding、mark-price 均从 Release 的 typed source 解析；错误类型复用、缺失 REAL funding 或未声明 mark-price 均 fail-closed |
| D4 | State 可恢复 | prepare 前/后崩溃、重复输入、重复 commit、坏 hash、schema/release 不兼容均有确定结果；恢复返回原始 Intent/audit lineage |
| D5 | OS 隔离可证明 | capability-positive Linux runner 完成 network/path/secret/resource/process-group 矩阵；当前无能力主机仍 fail-closed |
| D6 | lineage 完整 | Release dataset 集合与 Feature 输入集合逐项相等且 hash/quality/source lineage 验证通过；缺任一依赖拒绝 |
| D7 | CLI 无副作用 | 真实 formal fixture 的成功/失败/审批/重启 e2e，检查退出码、数据库、archive 和 audit |

## 实施前需要架构审查者确认的问题

以下选择由本次设计门冻结，实施不得改为隐式兼容方案：

1. `IntentRequest` 使用新增的不可变 core value object，但字段语义复用现有 IntentBuilder 白名单；child 和 parent 均通过同一 `FormalIntentBuilder` 生成/验证同一 `ValidatedIntent`，不得传 raw dict 作为成功结果。
2. `DecisionInput` 使用可寻址的 immutable materialization artifact，而不是只存在于进程内的对象；其 canonical hash、source manifest、feature materialization、instrument、interval 和 decision-time 必须进入 Release/State lineage。
3. `state_attempt`/`state_commit` 使用 JournalDB canonical 表和显式 schema migration；MetaDB 只保存 Release/依赖元数据。prepare、commit、Intent/audit lineage 必须在同一 JournalDB 事务边界内完成，不能继续以独立进程内字典或未登记 SQLite 表作为事实来源。
4. 文件隔离采用 Linux network namespace + mount namespace 专用临时根（复制或只读挂载 archive）+ 独立 session/process group；网络、文件根或资源限制任一能力不可用即拒绝启动，不提供生产降级路径。能力正向测试必须在 capability-positive Linux runner 执行。
5. 正式回测复用 `FormalDecisionInputFactory` 和 `FormalStrategyExecutor`，但策略可以在父进程执行；Runtime child 也必须调用同一 executor。两条路径的 Context、Intent、state 和错误语义必须相同，只有驱动方式不同。
6. 正式回测的 bars manifest 是唯一 caller 选择；funding、mark-price 和 feature output 只能由 Release 解析。缺失 REAL funding 或启用 mark-price/liquidation 语义时缺失 mark-price 均 fail-closed，不能用空集合或 bars 代替。

### v5 设计修订结论（2026-08-08）

- D3 的真实 fixture 证明旧单一 `manifest_id` 调用图无法表达多数据集 PIT lineage；本修订选择 Release-derived typed routing，而不增加任意 caller manifest map。
- 旧 v4 只证明共享 executor/input factory，未独立证明 supplemental type routing 与缺失成本数据的 fail-closed 语义，属于设计遗漏。v5 新增两条原子外部证明，并使 CLI formal success 依赖它们。
- v5 由架构审查者冻结；代码开发者必须重新执行全部受影响证明。未形成 `PREFLIGHT_PASSED` 前不得独立 Review。

### v7 payload provenance 设计修订（2026-08-08）

v6 的 `DecisionInput` 虽携带 Release、manifest 与 materialization hash，且 Runtime 只接受
artifact ref，但 `FormalDecisionInputFactory.create(payload=...)` 仍允许任意父进程以正确元数据
持久化伪造 payload。artifact 自洽不等于 payload 来自 formal PIT 数据；因此 v6 的
`PREFLIGHT_PASSED` 不能作为本问题的通过结论。本修订冻结
`M2-v05-runtime-release-state-v7`，取代 v6；v6 package、矩阵和 evidence 仅保留历史追溯。

v7 的唯一正式输入调用图为：

```text
canonical Release + UTC decision_time + instrument/interval
  -> FormalDecisionInputFactory.materialize(...)
  -> Release-derived typed source resolver
  -> trusted PointInTimeDataPort / Feature snapshot reader
  -> VerifiedDecisionMaterialization (payload + source-row hashes + manifest/materialization hashes)
  -> immutable DecisionInput artifact
  -> FormalDecisionInputFactory.load_verified(..., re-derives materialization)
  -> Runtime / EventDriven FormalStrategyExecutor
```

冻结规则如下：

1. 正式 API 不得有接受 `payload`、`available_at`、source/feature ID 或 hash 后返回可执行
   `DecisionInput` 的 public `create` 分支。Runtime 只接收 ref；EventDriven 只传 Release、
   decision_time、instrument、interval。测试 fake 必须是明确命名的非生产 adapter，不能被生产 DI
   或 capability runner 使用。
2. Factory 必须拥有或通过明确 core port 调用 trusted `PointInTimeDataPort`。它从 Release
   解析唯一 bars、feature output 与需要的 supplemental source，以 `available_at <= decision_time`
   查询并构造 payload；caller 不能选择 manifest、feature map、bar、时间范围或 hash。
3. artifact 除 Release/manifest/materialization hash 外，必须记录 canonical source-row hash
   （至少 decision bar 与 feature snapshot）、materializer schema/version、decision request 和
   canonical payload hash。Factory 的 `load_verified` 必须从同一 immutable source 重新导出
   materialization 并逐项比较；攻击者即使重算 artifact 的内部 hash，也不能使伪造 payload
   被 Runtime 或 State replay 接受。
4. Runtime 在 replay 前完成 `load_verified`；artifact 缺失、删除、篡改、无法重导、数据行
   不可见、候选/跨范围/质量或 lineage 不符均 fail-closed，且不执行 child、不新增 Intent/State。
5. EventDriven formal backtest 必须使用同一 materialize/load_verified 语义，禁止直接构造
   `DecisionInput` 或手写 payload/hash；vectorized formal 继续拒绝。
6. v7 新增 D3 trusted materialization、D6 payload provenance、D6 provenance replay 三个原子行；
   persisted formal/CLI 行依赖这些行。实现后从空 `evidence-results-v7.yaml` 重新取证，不能迁移
   v6 passed 记录。

### 设计门审查结论（2026-08-06）

- D1～D7 已覆盖上一轮 P0/P1：Intent 绕过、archive 篡改、legacy Context、state 崩溃/重放、OS 能力、lineage 一致性和 CLI 无副作用。
- 以上冻结选择关闭了“实现时自由选择”的接口和存储分叉；实现前必须同步 core model/ports、JournalDB schema migration、ADR-015 和测试矩阵。
- `require_dependencies=False` 只能从正式 ReleaseBuilder API 删除；测试必须注入明确命名的 fake dependency port，不得复用生产 builder 的绕过开关。
- 非法 Intent、Release/DecisionInput/hash 不匹配、超时、崩溃和隔离能力失败均必须使当前 deployment 进入 FAILED/FROZEN 并禁止输出成功结果；不得影响其他 deployment，也不得自动恢复交易权限。
- 本结论只批准设计门，M2 仍保持“未通过/返回设计门”状态，直到 D1～D7 的实现证据全部完成并通过独立复审。

## 通过设计门后的实施顺序

1. 先更新 core SDK/ports、事件和 State/Release schema 文档及迁移；
2. 实现 `FormalDecisionInputFactory`、`FormalStrategyExecutor` 和双侧 Intent validation；
3. 实现 Release content manifest/read-time verification 与 lineage equality；
4. 实现 State prepare/commit/replay 和 fault injection；
5. 实现 IsolationBackend，补齐 capability-positive runner；
6. 最后接 CLI e2e，运行冻结矩阵和三绿门禁，再提交架构复审。
