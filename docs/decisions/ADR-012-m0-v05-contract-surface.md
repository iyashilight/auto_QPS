# ADR-012: M0 v0.5 领域契约与持久化骨架

## 状态

已接受；M0.2 修复已交付，待架构审查者复核。

## 背景

v0.4 的 M0～M3 已经提供基础 Pydantic 模型、旧订单状态、事件信封、MetaDB 和部分 Protocol，但这些实现不能仅凭名称视为满足 v0.5。M0 需要先建立可执行的差距基线，并在不破坏既有元数据的前提下补齐研究发布、PIT、持久化订单、双账本和控制面契约。

## T0 差距审计

| 领域/对象 | 状态 | 现有证据（代码/测试） | v0.5 要求 | 影响文件 | 优先级 | 处理结论 |
|---|---|---|---|---|---|---|
| UTC 时间 | GAP | `src/qps/core/models/validators.py:12`；既有模型测试只拒绝 naive | 所有核心时间必须 UTC aware；事件时间统一 | `validators.py`、foundation models、tests | P0 | 严格拒绝非 UTC offset，新增回归测试 |
| PIT 数据/Manifest | GAP | `market.py:14` 只有 `open_time`；无 `available_at`/manifest | `event_time/available_at/ingested_at`、validity、hash、source refs、bar close/decision 语义 | `models/foundation.py`、docs/02/03 | P0 | 新增强类型数据契约和 PIT 端口 |
| Feature Registry | GAP | `data/features/engine.py:55` 是运行时注册器，无 Definition/Materialization/Evaluation 持久化契约 | 三实体分离且不可变、lineage 可追溯 | foundation models/ports、MetaDB | P0 | 新增模型、端口和元库表 |
| Research Budget/Trial | GAP | 现有 optimizer 只接收预算整数；无统一消费记录 | 人工/脚本/优化器/AI 共享预算；候选预占、失败计数、blind reveal 隔离 | foundation models/ports、MetaDB | P0 | 新增注册/预算模型和 append-only 表 |
| Evidence/Blind | GAP | 旧 `ReportCard/AlphaScore` 在 `experiment.py`；无三维证据和盲测记录 | EvidenceQuality、ExpectedEdge、RiskEstimate 分离；AlphaScore 不作资金契约 | foundation models、docs/02 | P1 | 新增三类证据模型和报告模型 |
| StrategyRelease/Approval | GAP | `StrategyVersion` 和旧 `Deployment` 存在；无内容固定 manifest/审批 | Release 不可变，manifest、审批和部署引用固定 release | foundation models/ports、MetaDB | P0 | 新增 Release/Approval/DeploymentBinding 模型与表 |
| Strategy Health | GAP | 只有旧 `DeploymentStatus` | Health、Deployment、Research 状态分离；自动动作只能降险 | foundation models/ports、MetaDB | P1 | 新增健康评估和生命周期动作模型 |
| Capital/Risk | GAP | `PortfolioRisk` 是旧摘要模型；无版本 Policy/Reservation | 慢周期 policy、risk snapshot、reservation 和过期语义 | foundation models/ports、MetaDB | P0 | 新增强类型模型和表 |
| Order state/CAS | GAP | `constants.py:341` 是 v0.4 状态机，无 UNKNOWN/CANCEL_PENDING/CAS | 完整状态机、state_version、过期拒绝、终态迟到 Fill | constants/order/foundation models | P0 | 保留旧状态兼容，新增 v0.5 状态和 `OrderState` CAS 模型 |
| Execution/Fill/Broker capabilities | GAP | `broker_adapter.py:21` 返回 `dict[str, Any]` | 强类型 command/report/fill/capabilities；原始回报可审计 | foundation models/ports、docs/03 | P0 | 新增公共 Broker/OrderJournal 端口 |
| 双账本/对账 | GAP | `account.py` 只有账户/仓位摘要；`reconciliation.py` 非持久契约 | Account/Strategy ledger、residual、reconciliation run | foundation models/ports、journal DDL | P0 | 新增模型、端口和交易库表 |
| Event envelope | GAP | `events.py:63` 只有 `ts`，payload 为无约束字典 | occurred/recorded/correlation/causation，至少一次幂等 | `events.py`、docs/05、tests | P0 | 新字段兼容 `ts`，新增关键事件清单 |
| MetaDB | SATISFIED（部分） | `meta_db.py:65` fresh/idempotent；`test_meta_db.py` | 研究元数据可 append-only，迁移不可吞错 | `meta_db.py`、migration tests | P1 | 保留旧库；增加 v0.5 表、schema version 和显式迁移 |
| Trading journal | GAP | 无独立 journal 数据库 | WAL、outbox/inbox/order/reservation/ledger/reconciliation，append-only | `trading_journal.py`、tests | P0 | 新增独立 JournalDB，fresh/upgrade/rerun 测试 |
| Chaos | GAP | 无场景/运行模型 | 版本化 scenario/run 与预期不变量 | foundation models/ports、MetaDB | P1 | 新增契约，不实现执行器 |
| 端口 | GAP | 旧 data/broker/trading Protocol 混合旧字典类型 | M0 指定 PIT、Registry、Budget、Release、Health、Policy/Risk、Chaos、Journal、Ledger、Reconciliation、Alert | `core/ports/*.py`、docs/03 | P0 | 新增 `v05.py` 端口，只定义签名，不实现业务 |

## 决策

1. 新 v0.5 模型集中在 `qps.core.models.foundation`，只依赖标准库/Pydantic；既有 v0.4 模型保留，兼容字段不作为 v0.5 验收证据。
2. `OrderStatus.NEW/PENDING_SUBMIT` 保留用于读取旧记录；新 Durable Order Workflow 使用 `CREATED -> RISK_RESERVED -> SUBMITTING -> PENDING_ACK`，并包含 `UNKNOWN/RECONCILING/CANCEL_PENDING`。两套值不得在新状态机中隐式混用。
3. 研究元数据和交易 journal 分库。MetaDB 负责 registry/research/release 元数据，JournalDB 负责订单、预留、outbox/inbox、双账本和对账。两者均由显式 schema version 迁移，迁移错误向上抛出。
4. 事件信封保留只读兼容属性 `ts`，规范字段为 `occurred_at` 与 `recorded_at`；事件 `event_id` 是 inbox 幂等键，不能依赖进程内集合。
5. M0 只实现模型、状态机、事件、端口、DDL/迁移与契约测试；不实现 Broker 调用、撮合、回测、风控规则或策略逻辑。

## 验收边界

本 ADR 的代码交付只能证明 v0.5 M0 契约可导入、非法值可拒绝、迁移可重复执行和持久化骨架可约束关键不变量。Feature 计算、PIT 数据采集、Strategy Release 构建、订单发送、账本业务处理和 Chaos 执行分别属于后续里程碑。

## 日期

2026-08-05

## M0.1 修订记录

架构复审发现的基础安全边界已在同一契约范围内修订：JournalDB 增加父表和 `ON DELETE RESTRICT` 外键，补齐 inbox/order state event/预留/outbox 的投影与 append-only 触发器；订单状态 CHECK、合法迁移、CAS、单调时间和终态迟到 Fill 规则在模型与数据库同时执行；`StringMap` 和 EventEnvelope payload 改为递归只读表示，同时保留旧调用方的 map/list 访问兼容性；MetaDB/JournalDB 增加显式 schema version、旧库升级、重跑和漂移拒绝测试。该修订仍只覆盖 M0 契约和持久化骨架，不实现任何 Broker、撮合、账本业务或研究业务。

## M0.2 修订记录

迁移判定改为显式 schema contract：JournalDB/MetaDB 逐表核验列、主键、唯一键、CHECK、外键目标与删除语义、索引及 append-only/projection 触发器；只有无 v0.5 表的 fresh、完整受支持 legacy 和完整 current schema 可以继续，部分约束 schema 或 migration version 与结构不一致立即失败。成功 migration 标记在完整验证之后写入，失败路径不写成功版本；旧库升级保留 `_legacy_*` 备份表，部署流程在升级前执行文件备份，失败使用备份回退。

`StringMap` 改为递归不可变 map value object，并通过显式 serializer/validator 保持 JSON object dump/load 和确定性键顺序。ExecutionReport 的原始回报改用递归不可变 `RawJson`，支持完整 JSON object/array/scalar round-trip，拒绝 Decimal、datetime、set 和自定义对象。EventEnvelope 继续保留旧 map 访问边界，未扩大核心业务模型的无约束 `dict[str, Any]`。

## M0.3 修订记录

schema contract 不再只检查保护触发器名称；JournalDB 和 MetaDB 从 `sqlite_master.sql` 读取定义、规范化空白和大小写后，核验目标表、`BEFORE UPDATE`/`BEFORE DELETE`、`RAISE(ABORT, ...)`、不可变字段比较以及 CAS/允许投影的关键片段。同名空触发器、错表触发器或放宽投影字段的定义会使初始化失败。此做法是针对受控 DDL 的语义 fingerprint，并非任意等价 SQL 的形式化证明。

为避开 SQLite `executescript()` 的隐式提交边界，MetaDB 与 JournalDB 初始化在变更前创建内存 SQLite 快照。legacy rename、DDL、数据复制、触发器安装、contract 验证或 migration marker 写入任一步失败时，连接恢复该快照并抛出包含“original schema restored”的迁移异常；因此不会留下新表与 `_legacy_*` 共存的无 marker 半成品，原始 fixture 可以安全重试。

## M0.4 修订记录

M0.3 的关键 trigger 校验由少量片段升级为受控 DDL 的规范化完整安全条件集合。JournalDB 的 order command CAS fingerprint 覆盖全部命令不可变列、版本递增、时间单调和完整合法状态迁移分支；reservation fingerprint 覆盖 account、intent、instrument、side、全部金额、risk snapshot、expiry、created_at；outbox fingerprint 覆盖除 `published_at` 外的全部内容列；insert-only trigger 覆盖目标、事件、`FOR EACH ROW`、无条件 `RAISE(ABORT, ...)`。MetaDB 的 experiments projection 同样覆盖全部内容列，其余 insert-only 表使用完整无条件定义。

校验规则对 `sqlite_master.sql` 做空白与大小写规范化，并将 SQLite 省略的默认逐行触发语义规范为 `FOR EACH ROW`，再逐 trigger 比较完整安全条件集合；缺少任何字段、状态分支、WHEN 条件或 RAISE 都失败即停，不自动重建或覆盖未知定义。该集合只接受仓库受控 DDL；后续安全语义变更必须提升 schema version，并通过新的 ADR 与显式迁移引入。M0.3 的 SQLite snapshot 失败恢复、legacy retry 和 migration marker 约束保持不变。

## M0.5 修订记录

M0.4 的字段片段校验升级为 canonical SQL 精确比较。contract 从受控 DDL 在内存 SQLite 中生成官方 trigger 定义；读取既有 `sqlite_master.sql` 时先按词法状态剥离 `--` 和 `/* */` 注释，再保留字符串字面量、WHEN 条件、布尔运算符、列比较和 RAISE body，规范化空白/大小写，并补入 SQLite 默认的逐行语义。规范化后的完整定义必须逐 trigger 等值匹配，不能用注释、RAISE message、字符串常量或不可达分支携带缺失安全条件。

该实现的边界是仓库受控 DDL 的 canonical contract，不尝试证明任意手写等价 SQL；任何安全语义变更必须提升 schema version，并通过 ADR 与显式迁移引入。M0.4 的完整 CAS/预留/outbox/insert-only 字段矩阵、snapshot 失败恢复、legacy retry 和 migration marker 约束保持不变。
