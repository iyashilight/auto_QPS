# 加密货币中频 AI 量化系统架构设计 v0.5

> 状态：实施基线（Architecture Baseline）  
> 日期：2026-08-05  
> 修订：ADR-011 增补 Strategy Health、Capital Allocation、Market Risk、Feature Registry、Research Budget、Market Chaos 与 Broker 扩展边界  
> 适用范围：个人单机、单用户、单交易所起步的现货与永续合约中频量化系统  
> 权威关系：本文件是后续实施顺序、边界和验收门禁的主架构。`加密货币中频AI量化系统架构设计.md`（v0.4）与 `加密货币中频AI量化系统架构设计-第二期.md`（v0.4-2）保留为历史方案和需求来源；与本文件冲突时，以本文件为准。

---

## 0. 执行摘要

本项目技术上可行，但成功条件不是尽快把回测、Agent、新闻、组合优化和实盘接口全部连起来，而是先建立四个不可妥协的事实基础：

1. **数据在当时是否真的可见**：所有研究输入具有 point-in-time 语义，能够区分事件发生、系统获知和系统入库的时间；
2. **研究结论是否真的可信**：开发集、验证集、盲测集和前向 Paper 隔离，优化器与 AI 永远不能读取盲测答案；
3. **订单与资金状态是否真的可恢复**：任何外部下单副作用发生前，意图、风控预留和订单命令已经持久化；重启后先对账，不能依赖内存恢复真相；
4. **多策略收益是否真的可归因**：交易所真实账户账本与策略虚拟账本分离，合并订单的成交、费用、资金费率和滑点可确定性分摊。

因此，系统被划分为两个具有明确发布边界的产品：

- **Research Platform（研究平台）**：数据版本、特征、策略开发、回测、优化、实验治理与候选发布；
- **Live Trading Kernel（实时交易内核）**：实时数据、策略运行、风险预留、持久化订单工作流、Broker 接入、账本与对账。

两者不能共享可变运行状态。研究平台只能发布不可变的 `StrategyRelease`；实时内核只运行经过人工批准、内容哈希固定的 Release。AI 在 M7 前不参与，在 M7 也只增强离线研究，不进入确定性交易控制面。

### 0.1 v0.5 的关键调整

| 旧方案倾向 | v0.5 决策 |
| --- | --- |
| 回测、Paper、Live 共用抽象并可热切换 | 共用领域语义和契约，不做运行中热切换；Paper 到 Live 是显式部署与审批 |
| 下单关键路径以内存状态为主、异步落盘 | Broker 副作用前必须持久化命令；订单工作流采用 journal/outbox + 幂等键 |
| 多策略先合并目标仓位 | 先建立策略虚拟账本与成交分摊，再允许多策略共用真实账户 |
| Alpha Score 直接驱动权重 | 拆分 `EvidenceQuality`、`ExpectedEdge`、`RiskEstimate`，不得把研究质量当预期收益 |
| 单一 Market Regime 枚举 | 趋势、波动、流动性、压力四个正交维度，带置信度、作用域与 TTL |
| RAG 自动积累经验并参与进化 | append/supersede、来源与时间可追溯；盲测数据隔离；AI 结果默认 Draft |
| 早期迁移 PostgreSQL | SQLite + Parquet + DuckDB 保持默认，仅由可测量瓶颈触发迁移 |
| 本机 Kill Switch 足以保障安全 | 本机停机 + 交易所原生保护 + 带外告警/撤单路径构成分层故障控制 |
| 策略上线后只看账户盈亏 | 独立 Strategy Health 控制面，区分健康判断、运行处置和重新研究 |
| 组合层只做 Intent 净额合并 | 慢周期 Capital Allocation Policy 与实时 AllocationPlan 分离 |
| 风控只依赖静态阈值 | 硬规则继续兜底，叠加相关性、因子、CVaR 和压力情景模型 |

### 0.2 重新排序后的交付主线

```text
M-1 工程与可复现地基
  -> M0 领域契约与持久化骨架
  -> M1 Point-in-Time 数据真实性
  -> M2 策略 SDK 与不可变发布物
  -> M3 可信研究与盲测治理
  -> M4 持久化交易内核 + 单策略 Paper
  -> M5 单策略小资金实盘
  -> M6 多策略虚拟账本与组合
  -> M7 AI 离线研究增强
```

现有 M0～M3 的验收结论属于 v0.4 基线。切换 v0.5 后，应按新版 Prompt 做差距审计，只修复新增门禁未满足的部分；不得把旧验收结论直接视为新版通过，也不要求无意义地重写已经符合新契约的实现。

---

## 1. 目标、非目标与成功标准

### 1.1 目标

- 支持 5 分钟至 4 小时级别的现货和永续策略研究、Paper 与低频实盘；
- 在单交易所、单真实账户起步，逐步扩展至多策略；
- 任一研究结论都能复现到策略代码、参数、数据、特征、成本模型和运行环境；
- 任一订单、成交、资金变化和风险决策都有可审计的因果链；
- 进程崩溃、网络超时、重复消息和交易所返回不确定时，不重复扩大风险；
- 在没有 AI 的情况下形成完整、可靠的研究与交易闭环；
- AI 后续接入时只能提出研究候选，不能突破发布、风险与执行边界。

### 1.2 非目标

- 亚秒级或高频交易；
- 跨交易所套利、做市和复杂智能路由；
- 面向多租户的 SaaS 平台；
- 自动把自然语言或新闻直接转换为订单；
- 用回测评分替代人工资本分配与运行风险控制；
- 第一阶段部署 Kubernetes、Kafka、微服务集群或分布式数据库。

### 1.3 成功标准

成功不是“模块齐全”，而是以下证据同时成立：

- **可复现**：相同 Release、数据清单与运行参数得到确定性一致结果；
- **无泄漏**：模拟时刻后才可见的数据无法被策略、优化器、特征或 RAG 查询；
- **可恢复**：在下单前、下单超时、部分成交和落盘前后注入崩溃，重启后都能对账收敛；
- **可归因**：真实账户权益变化可与订单、成交、费用、资金费率及策略虚拟账本对平；
- **可控损失**：单笔、单日、总敞口、杠杆、数据陈旧、对账异常等风险规则能阻止新增风险；
- **渐进上线**：单策略 Paper、单策略小资金 Live、多策略按顺序逐级放行，每一级都有明确回退条件。

---

## 2. 架构原则与系统不变量

### 2.1 十条原则

1. **时间真实性优先于数据丰富度**：没有可用时间的数据不能进入严肃回测；
2. **外部副作用先记账后执行**：下单、撤单等 Broker 调用前必须有持久化命令；
3. **交易所是真实资产状态的最终来源**：本地不能凭内存推断真实仓位；
4. **本地账本是意图、归因与审计的最终来源**：交易所不会替系统保存策略级归因；
5. **至少一次投递，业务幂等处理**：不追求虚假的 exactly-once；
6. **研究与实盘通过不可变发布物连接**：不共享工作目录中的可变策略代码；
7. **策略只表达意图**：策略无 Broker、账户密钥、真实余额或风控修改权限；
8. **能力显式协商**：Paper 与 Live 可以实现同一端口，但不假设支持能力相同；
9. **先单策略证明闭环，再增加组合复杂性**；
10. **复杂基础设施由瓶颈触发，不由路线图日期触发**。

### 2.2 不变量

以下规则必须由模型约束、数据库约束和测试共同保证：

- 每个 `StrategyRelease` 的内容不可修改，修改产生新 release_id；
- 每个 Broker 命令具有稳定、可重试的 `client_order_id`；
- 同一交易所账户的订单状态变更由单一 account sequencer 串行提交；
- `Fill` 按 `(venue, account_id, trade_id)` 或经验证的替代复合键幂等；
- 未完成对账前，运行状态不得进入 `TRADING_ENABLED`；
- 风险预留与未完成订单同时计入敞口，不允许只按已成交仓位计算风险；
- 策略查询只能读取 `available_at <= decision_time` 的数据；
- 盲测集的范围和结果对调参进程、RAG 索引和策略生成进程不可见；
- 资金使用 `Decimal`，时间使用 UTC aware datetime；
- 真实交易账户默认禁止提现，API Key 最小权限并启用 IP 白名单；
- AI 输出最多到 `DRAFT`，不可自动晋升、改变权重、解除冻结或直接下单。

---

## 3. 总体架构

### 3.1 两个产品、一个发布边界

```text
┌──────────────────────── Research Platform ────────────────────────┐
│ Raw/Normalized/PIT Data -> Feature Sets -> Backtest/Optimizer     │
│ -> Experiment Registry -> Evidence Review -> Release Builder      │
└──────────────────────────────┬────────────────────────────────────┘
                               │ immutable StrategyRelease
                               │ hash + manifest + approval
┌──────────────────────────────▼────────────────────────────────────┐
│                     Live Trading Kernel                           │
│ Market Ingest -> Strategy Runtime -> Intent -> Risk Reservation   │
│ -> Durable Order Workflow -> Broker -> Fill -> Ledger/Reconcile   │
└───────────────────────────────────────────────────────────────────┘
```

研究平台允许试错、批量计算和失败重跑；实时内核要求确定性、有限状态、可恢复和最小依赖。两者可以位于同一仓库和同一主机，但运行进程、数据目录、权限与生命周期必须分离。

完整系统由研究面、慢周期控制面、实时执行面和反馈面组成：

```text
Research Plane
Data -> Feature Registry -> Experiments -> StrategyRelease
                                      |
                                      v
Slow Control Plane
Approval + Strategy Health + Market Risk Model
         -> Portfolio Construction -> CapitalAllocationPolicy
                                      |
                                      v
Real-time Execution Plane
Market Data -> Strategy Runtime -> Intent -> AllocationPlan
            -> RiskReservation -> Durable Order Workflow -> Broker
            -> Ledger -> Reconciliation
                                      |
                                      v
Feedback Plane
Execution/Ledger/Market Telemetry -> Strategy Health -> Research Queue
```

慢周期控制面不逐单计算：它生成有有效期、可审计的 Policy 和 Risk Snapshot。实时执行面只应用这些已批准工件并执行硬风控。反馈面可以自动降险或提出重新研究请求，但不能修改 Release、自动加资或解除冻结。

### 3.2 模块边界

```text
qps.core       领域值对象、状态机、事件、端口；不依赖基础设施
qps.infra      SQLite/journal/outbox、时钟、日志、配置、审计
qps.data       历史与实时数据接入、规范化、PIT 查询、特征物化
qps.broker     Paper/Live adapter、能力声明、交易所协议映射
qps.research   回测、优化、实验、验证、Release 构建
qps.trading    runtime、portfolio、risk、execution、ledger、reconcile
qps.control    CLI、进程编排、人工审批和运维入口
```

依赖方向采用稳定内核向外扩展的规则。`core` 不依赖任何其他业务模块；上层模块通过 `core/ports` 使用能力。不能把“所有调用必须经过端口”机械化为模块内部也层层 Protocol；端口只用于跨所有权边界、外部系统和需要替换/测试的能力。

### 3.3 运行进程

最小可靠部署建议为四类进程：

| 进程 | 责任 | 是否持有交易密钥 |
| --- | --- | --- |
| `qps-data` | 行情采集、校时、规范化、数据质量 | 否 |
| `qps-research` | 回测、优化、实验、Release 构建 | 否 |
| `qps-trader` | 策略运行、风险、订单、账本、对账 | 是，仅交易权限 |
| `qps-watchdog` | 独立心跳、告警、带外停机/撤单触发 | 可选受限撤单权限 |

MVP 可在单机运行，但 `qps-research` 与 `qps-trader` 不得在同一进程。交易密钥不能暴露给研究进程或策略子进程。

### 3.4 故障域

- 数据采集失败：停止产生新决策，保留撤单和减仓能力；
- 策略失败：隔离单个策略实例，不影响账本、对账和风险保护；
- Broker API 失败：命令保持 UNKNOWN/PENDING 状态，禁止盲目重发不同 client_order_id；
- SQLite/journal 失败：禁止产生新的 Broker 副作用；
- 对账失败：冻结新增风险，只允许人工批准的 reduce-only 操作；
- 研究进程失败：不得影响实时内核运行；
- 主机失联：依赖交易所原生止损/止盈、低杠杆和 watchdog，而非假设本机还能执行 Kill Switch。

---

## 4. 不可变发布边界

### 4.1 StrategyRelease

`StrategyRelease` 是研究进入 Paper/Live 的唯一输入，不允许运行时直接指向 `strategies/` 工作目录。

```text
StrategyRelease
  release_id
  strategy_id
  strategy_version
  code_content_hash
  parameter_set_hash
  feature_set_ids[]
  dataset_manifest_ids[]
  cost_model_version
  environment_lock_hash
  evidence_report_id
  requested_capabilities[]
  allowed_instruments[]
  allowed_timeframes[]
  created_at
  created_by
  approval_status
  approved_at
  approved_by
```

发布物至少包含：

- 策略源码归档及内容哈希；
- 参数、依赖、解释器和包锁文件；
- 输入特征定义及数据口径；
- 研究证据摘要与所有关联 experiment_id；
- 允许交易的标的、周期、方向、最大仓位和 Broker 能力；
- 可机器校验的 manifest；
- 人工审批记录。

### 4.2 生命周期

```text
DRAFT -> RESEARCH_VALIDATED -> PAPER_APPROVED -> PAPER_RUNNING
      -> LIVE_CANDIDATE -> LIVE_APPROVED -> LIVE_RUNNING
      -> FROZEN -> RETIRED
```

- 生命周期单向推进；重新修改代码或参数必须创建新 Release；
- `FROZEN` 可由风险或运维自动触发，但 `unfreeze` 必须人工执行；
- `PAPER_RUNNING -> LIVE_RUNNING` 不是切换一个 adapter，而是新建 Live deployment；
- Live 回滚指向上一个已批准 Release 或完全停机，不允许回滚到可变目录。

### 4.3 发布门禁

Release 至少通过：

1. 清单与内容哈希完整；
2. 代码检查、类型检查、单元测试和确定性回放；
3. 数据与特征均有 PIT 声明；
4. 研究报告包含基准、成本、容量、稳健性和失败条件；
5. Blind Challenge 仅由独立验证命令执行，结果写入 append-only 记录；
6. Forward Paper 达到最短日历周期和最小独立决策数；
7. 人工批准允许的资本与能力范围。

---

## 5. Point-in-Time 数据架构

### 5.1 时间语义

所有可能参与决策的数据记录至少区分：

| 字段 | 含义 |
| --- | --- |
| `event_time` | 市场事件实际发生或统计窗口结束时间 |
| `available_at` | 该数据按数据源语义最早能被策略知道的时间 |
| `ingested_at` | 本系统实际接收到并持久化的时间 |
| `valid_from` / `valid_to` | 元数据或规则在现实世界中的有效区间 |

回测默认使用 `available_at` 过滤，实盘使用实际接收时钟。不能用 `ingested_at` 代替历史可用时间，也不能因当前已知规则去改写历史。

### 5.2 K 线语义

- K 线在 `close_time` 之前不可用；
- 策略的 `decision_time` 默认为该 bar 的 `close_time`；
- 订单最早在下一可交易时点成交，除非模型明确使用 close auction 且有证据支持；
- 未收盘 bar 必须标记 `is_closed=false`，不能进入默认研究数据集；
- 修订、补洞和来源切换产生新 data batch，不覆盖旧批次。

### 5.3 市场元数据历史

价格步长、数量步长、最小名义金额、杠杆档位、保证金规则、合约乘数、费率等级和标的状态均按有效期保存。回测撮合与订单验证必须使用决策时点有效的规则，而非当前交易所规则。

```text
InstrumentRuleVersion
  venue, instrument_id
  tick_size, lot_size, min_notional
  contract_multiplier
  max_leverage, margin_tiers
  maker_fee, taker_fee
  valid_from, valid_to
  source_ref, ingested_at
```

### 5.4 数据分层

```text
Raw immutable payloads
  -> Normalized canonical records
  -> Quality-certified PIT datasets
  -> Versioned feature sets
  -> DatasetManifest
```

- Raw 层保存可审计原文，追加写；
- Normalized 层统一字段、单位、方向和时区，但不丢来源；
- Certified 层记录缺口、重复、异常值、时间漂移和修订状态；
- Feature 层记录计算代码哈希、输入 manifest、窗口和 warm-up；
- DatasetManifest 固定分区清单、内容哈希、schema 版本和质量报告。

### 5.5 PIT 查询契约

任何研究/策略查询都必须携带 `as_of` 或 `decision_time`：

```python
load_bars(instrument, timeframe, start, end, *, as_of)
load_feature(feature_id, instrument, start, end, *, as_of)
load_instrument_rules(instrument, *, effective_at, known_at)
```

测试必须构造“事件早发生、晚到达”的数据，证明在 `available_at` 前查询不到；仅检查时间排序不等价于无未来函数。

### 5.6 数据质量门禁

- 连续性、重复、OHLC 约束、成交量非负；
- server time 偏差与本地时钟漂移；
- WebSocket 序列号缺口与 REST 回补闭环；
- bar 关闭状态和迟到数据；
- funding rate 生效时点与实际结算区间；
- instrument rules 的有效期覆盖；
- 数据版本哈希稳定，重跑不静默覆盖；
- 数据陈旧达到阈值时，实时内核进入 `DATA_STALE` 风险模式。

### 5.7 Feature Registry

Feature Registry 管理“特征是什么、如何计算、物化在哪里、在哪些实验中被评价”，避免用文件名和口头约定维持特征资产。

特征治理拆成三个不可混淆的实体：

```text
FeatureDefinition
  feature_id, semantic_name, description
  formula_or_code_hash
  input_schema, source_types
  window, warmup, frequency
  available_at_rule
  owner, version, status

FeatureMaterialization
  materialization_id, feature_id, feature_version
  input_manifest_ids[]
  output_manifest_id
  coverage, quality_report_id
  computed_at, content_hash

FeatureEvaluation
  evaluation_id, feature_id, feature_version
  strategy_family_id, experiment_id
  dataset_split, evidence_metrics
  evaluated_at
```

- Definition 表达不可变语义；公式、窗口、来源或可用时间规则变化即新版本；
- Materialization 表达一次可重放的计算结果，不能把缓存路径当版本；
- Evaluation 是特定实验和数据切分下的证据，不能在 Feature 上维护一个全局“历史表现分数”；
- Blind Challenge 派生评价不得回写开发可见 Registry；
- Registry 记录使用该特征的 StrategyRelease 和 lineage，但删除/退役特征不能破坏历史 Release；
- Feature 状态可为 `DRAFT / ACTIVE / DEPRECATED / RETIRED`，状态变化只影响新研究，不改写既有实验。

---

## 6. 可信研究体系

### 6.1 四层验证数据

```text
Development -> Validation -> Blind Challenge -> Forward Paper
```

| 层 | 用途 | 谁可访问结果 |
| --- | --- | --- |
| Development | 特征、逻辑和参数初始开发 | 研究者、优化器、AI |
| Validation | 模型选择、walk-forward、稳健性比较 | 研究者、优化器、AI |
| Blind Challenge | 对最终候选做一次或受预算限制的挑战 | 独立验证命令；开发进程不可读原始答案 |
| Forward Paper | 真实时间顺序、真实数据延迟与运行稳定性 | 交易内核与人工评审 |

当 Blind Challenge 被用于改策略后，该区间就不再是盲测，应登记为已揭示并轮换新的保留区间。系统必须记录每个策略家族的试验次数，不能通过改名规避多重试验统计。

### 6.2 实验注册

每次实验开始前创建不可变 Trial 记录：

```text
TrialRegistration
  trial_id, strategy_family_id
  hypothesis
  allowed_datasets
  parameter_space
  primary_metric
  rejection_criteria
  seed
  registered_at
```

实验结束后追加结果，不修改原假设。失败实验同样保留。优化器使用固定预算，所有候选都计入 trial count，不能只保存优胜者。

### 6.3 研究报告的三个独立判断

禁止把多个含义不同的维度揉成一个可直接驱动资金的 Alpha Score：

- `EvidenceQuality`：数据质量、样本量、稳定性、泄漏风险、可复现程度；
- `ExpectedEdge`：相对明确基准、扣除成本后的预期超额收益及置信区间；
- `RiskEstimate`：回撤、尾部风险、杠杆敏感性、流动性与容量风险。

三者可用于筛选和人工审批，但资金权重不能由任一单值直接映射。组合分配还必须考虑策略间相关性、共同拥挤、换手、容量和当前账户风险。

### 6.4 必测稳健性

- 相对 buy-and-hold、现金、简单趋势/均值基准；
- 手续费、滑点、funding、延迟的压力情景；
- 参数邻域稳定性，而非只看最优点；
- 不同市场阶段、不同标的与不同交易方向；
- purged/embargo 的时间序列交叉验证（特征窗口重叠时）；
- 概率化或调整后的 Sharpe、PBO/过拟合风险或等价多重检验控制；
- 成交量占比、盘口深度假设与策略容量；
- 缺失数据、迟到数据、极端跳空和交易所规则变化。

### 6.5 研究失败条件

在开始实验前明确失败条件，例如：

- 扣除 2 倍基准成本后无正收益；
- 参数邻域表现断崖式变化；
- Blind Challenge 未达到预注册指标；
- 收益集中于极少数交易或单一月份；
- 容量低于计划资金规模；
- Paper 中信号、订单或数据延迟明显偏离回测假设。

失败候选进入 `REJECTED`，不得通过降低门槛或反复查看盲测结果恢复为候选。

### 6.6 Research Budget Manager

过拟合风险来自总假设搜索规模，不只来自 AI。因此研究预算统一约束人工、脚本、优化器和 AI：

```text
ResearchBudget
  budget_id
  scope: GLOBAL | STRATEGY_FAMILY | FEATURE_FAMILY | ACTOR
  period
  max_trials, max_compute, max_blind_reveals
  similarity_policy_version
  approved_by, valid_from, valid_to

BudgetConsumption
  consumption_id, budget_id
  actor_type: HUMAN | SCRIPT | OPTIMIZER | AI
  trial_id, candidate_fingerprint
  compute_cost, blind_reveals
  consumed_at
```

- 每个候选在运行前原子占用预算，失败、取消和超时也计数；
- 相似度同时参考代码/参数结构、特征集合、交易逻辑、信号序列和暴露相关性，不能只比较文本；
- 近重复候选仍计入总 trial count，并可提高统计惩罚或直接拒绝；
- 所有失败保留原因、输入和 lineage，不能只保留优胜者；
- Blind reveal 使用独立、更严格的不可恢复预算；
- AI 在 M7 只能消费 M3 已建立的预算，不能自行提高额度或创建新预算。

---

## 7. 策略 SDK 与运行隔离

### 7.1 策略职责

策略接收只读 `StrategyContext`，输出 `PositionIntent` 或 `TargetExposureIntent`。策略不得：

- 访问 Broker adapter 或账户密钥；
- 直接创建 Order；
- 修改组合/风控状态；
- 读取 `decision_time` 之后的数据；
- 在模块级保存无法序列化的可变状态；
- 发起网络请求、启动子进程或写任意文件。

### 7.2 StrategyContext

上下文至少包含：

```text
strategy_instance_id
release_id
decision_time
market_snapshot_ref
feature_snapshot_ref
virtual_positions (read-only)
approved_parameters (read-only)
deterministic_state
```

同一输入事件与同一状态必须产生相同输出。策略状态更新采用事件日志或带版本快照，重启后可确定性恢复。

### 7.3 隔离边界

AST 静态检查用于开发反馈，不是安全边界。Live 策略至少在独立子进程运行，并采用：

- 独立低权限 OS 用户或等价沙箱；
- 无交易密钥、默认无网络；
- 只读 Release 包；
- 资源限制、调用超时和进程终止；
- 仅通过结构化 IPC 接收市场快照并返回 Intent；
- runtime 对输出进行 schema、范围和时效校验。

对于个人单机 MVP，可先实现子进程 + 资源限制；容器化不是强制条件，但密钥隔离不可省略。

---

## 8. 回测与 Paper 的一致性边界

### 8.1 共享什么

回测、Paper、Live 共享：

- 领域模型与状态机；
- Strategy SDK 与 StrategyRelease；
- 成本模型接口；
- 风控规则定义；
- instrument rules 解释；
- 事件与审计格式。

### 8.2 不强行共享什么

- 历史批处理引擎与实时事件循环；
- 历史撮合模型与交易所真实撮合；
- Paper Broker 与 Live Broker 的所有能力；
- 回测优化吞吐路径与 Live 持久化关键路径。

追求“同一份代码”不能以隐藏时序差异为代价。需要共享的是语义和测试向量，而不是所有运行实现。

### 8.3 回测执行语义

- 多标的数据按可用时间合并，不按未来已知完整时间轴预加载给策略；
- 信号在 `decision_time` 形成，撮合不能发生在信号可见之前；
- 限价单按可验证的 bar/盘口模型处理，不能默认触价即全部成交；
- 部分成交、撤单延迟、订单过期和最小数量规则可配置；
- funding 按持仓覆盖的实际结算点计算；
- 强平按历史有效保证金档位、mark price 与额外费用计算；
- 结果必须区分 gross PnL、fee、slippage、funding、liquidation、borrow 等成本。

### 8.4 Paper 的定位

Paper 主要验证实时系统，而不是再次证明历史收益：

- 实时数据延迟与缺口处理；
- 策略调度、状态恢复和 Release 一致性；
- 风险预留与订单状态机；
- 告警、冻结、对账和运维流程；
- 模拟成交假设与真实盘口的偏差。

“运行 24 小时”只能作为稳定性冒烟，不是晋升实盘的充分证据。晋升还应满足最小日历周期、独立决策数、异常演练和人工复核。

---

## 9. Live Trading Kernel

### 9.1 确定性主链路

```text
MarketEvent
  -> StrategyRuntime
  -> PositionIntent
  -> IntentNetting / AllocationPlan
  -> RiskEvaluation
  -> RiskReservation
  -> DurableOrderCommand
  -> BrokerSubmission
  -> ExecutionReport
  -> FillAllocation
  -> AccountLedger + StrategyLedger
  -> Reconciliation
```

任何旁路都不能创建订单。新闻、人工命令、AI 和 Coordinator 只能产生受限的上层意图或风险模式请求，最终都必须进入同一风险与执行链路。

### 9.2 Account Sequencer

同一 `(venue, account_id)` 的下列操作由一个逻辑 actor/sequencer 串行化：

- 风险快照版本检查；
- 风险预留创建、消费和释放；
- OrderCommand 创建；
- ExecutionReport 应用；
- Fill 入账与分摊；
- 对账差异处理；
- 交易启停状态切换。

策略计算和行情处理可以并行，但改变真实账户风险状态的提交必须串行。这样消除“两个策略同时通过旧风险快照，各自下单后总敞口超限”的 TOCTOU 问题。

### 9.3 风险快照版本

每次 RiskEvaluation 绑定：

```text
risk_snapshot_version
account_ledger_version
market_snapshot_version
intent_set_hash
evaluated_at
expires_at
```

提交 OrderCommand 前必须确认版本未过期。若期间发生新成交、撤单、价格剧烈变化或风险模式切换，旧批准失效并重新评估。

### 9.4 运行模式

```text
BOOTING
  -> RECONCILING
  -> OBSERVE_ONLY
  -> TRADING_ENABLED
  -> RISK_REDUCE_ONLY
  -> FROZEN
  -> STOPPED
```

- 启动后只能进入 `RECONCILING`；
- 对账一致且数据新鲜后进入 `OBSERVE_ONLY`；
- 只有显式授权才能进入 `TRADING_ENABLED`；
- 可自动降级到 `RISK_REDUCE_ONLY` 或 `FROZEN`；
- 从降级状态恢复到 `TRADING_ENABLED` 必须人工确认。

---

## 10. 风险预留与风险引擎

### 10.1 风险计算边界

风险引擎计算的是最坏可执行敞口，而不是当前已成交仓位：

```text
effective_exposure = filled_positions
                   + open_order_exposure
                   + active_reservations
                   + proposed_intent_exposure
```

如果只计算已成交仓位，在订单尚未回报时并发意图会重复占用同一风险预算。

### 10.2 RiskReservation

```text
RiskReservation
  reservation_id
  account_id
  intent_ids[]
  instrument_id
  side
  reserved_quantity
  reserved_notional
  reserved_margin
  risk_snapshot_version
  status: ACTIVE | PARTIALLY_CONSUMED | CONSUMED | RELEASED | EXPIRED
  expires_at
  created_at
```

- 预留与 OrderCommand 在同一持久化事务中绑定；
- 成交时按 Fill 消费；
- 撤单确认、拒单或过期后释放；
- UNKNOWN 订单不能提前释放，必须先对账确认；
- 重启后从持久化记录恢复，不能重新推断出另一套预算。

### 10.3 风险层级

| 层级 | 典型规则 |
| --- | --- |
| Intent | 单策略允许标的、方向、信号时效、最大变化率 |
| Order | 数量/名义金额、价格偏离、最小规则、reduce-only、一致性 |
| Instrument | 净/总敞口、杠杆、集中度、未完成订单数量 |
| Account | 总杠杆、保证金利用率、日损失、回撤、可用余额 |
| System | 数据陈旧、Broker 错误率、对账差异、时钟漂移、磁盘/DB 健康 |

### 10.4 规则优先级

硬规则不可被策略、AI 或人工配置中的普通参数覆盖。建议优先级：

```text
Emergency Freeze
  > Exchange/Account Safety
  > System Health
  > Portfolio Limits
  > Strategy Limits
  > Strategy Intent
```

### 10.5 reduce-only 语义

- reduce-only 的判断基于交易所已确认仓位和未完成订单；
- 不能仅看本地策略虚拟仓位；
- 当账户状态陈旧或对账不一致时，只允许可证明不会扩大绝对仓位的操作；
- 若交易所不提供可靠 reduce-only，系统应撤销冲突订单并将操作降级为人工处理。

---

## 11. 持久化订单工作流

### 11.1 核心规则

**Broker 副作用之前必须有 DurableOrderCommand。** 数据库不可用或事务失败时，系统宁可不下单，也不能先下单后补记录。

建议采用本地 ACID 数据库中的 journal/outbox：

1. 在单事务中写入 Intent 决策、RiskReservation、OrderCommand 和 Outbox 事件；
2. 提交成功后，由 dispatcher 读取待发送命令；
3. 使用稳定 `client_order_id` 调用 Broker；
4. 保存响应或超时结果；
5. WebSocket/REST 回报进入 inbox，经幂等校验后更新状态机；
6. 任何 UNKNOWN 状态通过查询与对账解决，不生成新业务订单。

### 11.2 OrderCommand

```text
OrderCommand
  command_id
  client_order_id
  account_id
  release_id
  allocation_plan_id
  reservation_id
  instrument_id
  side
  order_type
  quantity
  limit_price
  stop_price
  time_in_force
  reduce_only
  state
  attempt_count
  broker_order_id
  created_at
  updated_at
  state_version
```

`client_order_id` 从账户、部署、意图集合和命令序号稳定生成，必须满足交易所长度与字符限制。重试同一业务命令必须复用同一 ID。

### 11.3 完整订单状态机

```text
CREATED
  -> RISK_RESERVED
  -> SUBMITTING
  -> PENDING_ACK
  -> WORKING
  -> PARTIALLY_FILLED
  -> FILLED

WORKING/PARTIALLY_FILLED
  -> CANCEL_PENDING
  -> CANCELED

SUBMITTING/PENDING_ACK
  -> UNKNOWN
  -> RECONCILING
  -> WORKING | PARTIALLY_FILLED | FILLED | REJECTED | CANCELED

CREATED/RISK_RESERVED/PENDING_ACK
  -> REJECTED | EXPIRED
```

说明：

- HTTP timeout 不是 `REJECTED`，而是 `UNKNOWN`；
- cancel 请求成功发送不是 `CANCELED`，而是 `CANCEL_PENDING`；
- `PARTIALLY_FILLED` 后取消仍需保存累计成交；
- 状态更新使用 compare-and-set `state_version`，过期回报不能倒退状态；
- 终态后到达的新 Fill 仍必须入账，并触发状态修正与告警；
- 交易所状态映射必须保留原始 payload 以供审计。

### 11.4 幂等与重复处理

- 命令幂等键：`client_order_id`；
- Fill 幂等键：优先 `(venue, account_id, trade_id)`；
- ExecutionReport 幂等键：交易所 update id，缺失时使用规范化内容哈希并记录风险；
- 账本分录幂等键：`source_type + source_id + entry_type`；
- 消费者以 inbox 表记录已应用事件，不依赖内存 set；
- 重放事件应得到同一状态，不重复扣费或释放预留。

### 11.5 崩溃矩阵

必须通过故障注入覆盖：

| 崩溃点 | 恢复行为 |
| --- | --- |
| 写命令前 | 没有外部副作用，可重新计算 |
| 事务提交后、发送前 | dispatcher 发送同一命令 |
| Broker 接收后、本地收到响应前 | 使用同一 client_order_id 查询，不创建新订单 |
| ACK 后、状态落盘前 | WebSocket/REST 对账恢复订单 |
| 部分成交后、Fill 入账前 | 交易所 trade history 补回并幂等入账 |
| 撤单发送后、确认前 | 标记 CANCEL_PENDING/UNKNOWN，查询最终状态 |
| 账本更新中 | ACID 事务保证分录与状态一起提交或一起回滚 |

---

## 12. 账本、成交分摊与多策略

### 12.1 两层账本

系统维护两个不同真相：

1. **Account Ledger**：真实交易所账户的订单、成交、仓位、余额、费用和资金费率；
2. **Strategy Ledger**：每个策略实例的虚拟仓位、分配成交、成本和归因收益。

Account Ledger 必须与交易所对平；Strategy Ledger 必须与 AllocationPlan 和 Account Ledger 的分配总和对平。

### 12.2 为什么不能只存净仓位

假设策略 A 目标 `+1 BTC`，策略 B 目标 `-0.6 BTC`，真实账户只需 `+0.4 BTC`。如果只记录真实净仓位：

- 无法知道 A 和 B 各自承担多少风险；
- B 平仓时可能错误地卖出真实账户，反而扩大空头；
- 手续费、funding 和滑点无法归因；
- 停用 A 后无法计算组合应交易的增量。

因此每次 netting 必须先产生 `AllocationPlan`，再产生真实订单。

### 12.3 AllocationPlan

```text
AllocationPlan
  allocation_plan_id
  account_id
  instrument_id
  input_intents[]
  pre_trade_virtual_positions
  target_virtual_positions
  account_delta
  allocation_rule_version
  created_at
```

内部相反意图可先做 virtual crossing，但必须显式记录每个策略的虚拟成交价格和成本规则。若产生真实订单，真实 Fill 按预先固定规则分配，不能事后挑选最有利策略。

### 12.4 分摊规则

默认规则建议：

- 同方向需求按未满足数量比例分配；
- 每个 Fill 到达时使用剩余权重增量分配；
- 数量 rounding 由最小 lot 决定，尾差按稳定顺序分配；
- 手续费按实际分配成交比例；
- funding 按结算时点虚拟持仓名义价值；
- 滑点归因基于各策略 decision price 与实际分配价格；
- internal crossing 使用明确的参考价格，并单列“避免的外部成本”，不伪造真实收益。

### 12.5 对平不变量

对每个账户、标的和时点：

```text
sum(strategy_virtual_position) == account_position + documented_residual
sum(allocated_fill_qty) == real_fill_qty
sum(allocated_fees) == account_fee
sum(allocated_funding) == account_funding
```

`documented_residual` 只允许来自最小交易单位、人工交易或迁移差异，必须有原因码和清理计划。

### 12.6 多策略启用条件

在 M6 之前真实账户只能运行一个交易策略 Release。多策略启用必须证明：

- 分摊算法性质测试和回放测试通过；
- 部分成交、跨方向、撤单、rounding 和 funding 对平；
- 单策略停用不会破坏其他策略目标；
- Account/Strategy Ledger 每日自动对平；
- 组合风险同时考虑虚拟策略和真实账户敞口；
- 人工交易被检测并隔离为 residual，而非静默归给策略。

### 12.7 Portfolio Construction 与 Capital Allocation

组合管理分成慢周期控制面和实时执行面，二者不能用同一个“权重计算器”混合：

```text
慢周期控制面
EvidenceReport + StrategyHealth + PortfolioRiskSnapshot + Capacity
  -> PortfolioConstruction
  -> CapitalAllocationPolicy (immutable, versioned, approved)

实时执行面
Strategy Intents
  -> Capital Envelope Check
  -> Intent Netting / AllocationPlan
  -> RiskReservation
  -> Durable Order Workflow
```

`CapitalAllocationPolicy` 至少包含：

```text
policy_id, account_id, policy_version
eligible_deployments[]
capital_envelope_by_strategy
risk_budget_by_strategy
instrument/gross/net limits
turnover_budget
capacity_constraints
portfolio_risk_snapshot_id
valid_from, expires_at
approval_record_id
```

- EvidenceQuality 只决定资格或置信折扣，不能直接映射权重；
- ExpectedEdge、RiskEstimate、相关性、因子暴露、容量、换手和当前健康状态共同进入构建；
- 自动流程可以减资、限速或冻结，不能自动加资、解冻或扩大允许标的；
- 新策略从小资本 envelope 开始，扩大额度生成新 Policy 并审批；
- Policy 过期、风险快照陈旧或健康恶化时，实时路径只能降级；
- M4/M5 单策略也使用退化为单项的 Policy，以避免 M6 再改交易契约。

---

## 13. 对账与恢复

### 13.1 启动对账

`qps-trader` 每次启动：

1. 加载本地非终态订单、预留、账本版本和部署状态；
2. 获取交易所 server time、余额、仓位、open orders 与近期 trades；
3. 通过 client_order_id/broker_order_id 匹配订单；
4. 补录缺失 ACK、Fill、费用和 funding；
5. 标记本地存在但交易所不存在的异常订单；
6. 计算账户与策略账本差异；
7. 差异为零或在批准容差内，才允许进入 `OBSERVE_ONLY`；
8. 人工确认后才进入 `TRADING_ENABLED`。

### 13.2 周期对账

- WebSocket 是低延迟更新源，REST 是校验与恢复源；
- 每个固定周期做 open orders、positions、balances 增量核对；
- 每日做完整 trade/funding/fee 对账并生成不可变报告；
- WebSocket 序列中断立即标记状态陈旧，并触发 REST 恢复；
- 对账差异超限时冻结新增风险，不自动用“补一笔交易”消除差异。

### 13.3 冲突处理

| 冲突 | 处理原则 |
| --- | --- |
| 交易所有订单、本地无记录 | 导入为 `EXTERNAL_OR_UNKNOWN`，冻结并人工确认 |
| 本地 WORKING、交易所无订单 | 查历史订单和 trades；未确认前保持 RECONCILING |
| 仓位数量不一致 | 交易所真实仓位优先，差额进入 residual，不篡改历史分录 |
| 费用/资金费率缺失 | 追加补充分录并关联来源记录 |
| 重复 Fill | 幂等忽略并保留重复观测审计 |

### 13.4 恢复点

- journal/outbox 与账本使用 SQLite WAL、定期 checkpoint 和备份；
- 数据文件使用 manifest + hash 验证；
- Release 包独立归档；
- 恢复演练必须从备份启动并完成交易所对账，而不只是验证文件可解压；
- 规定 RPO/RTO：MVP 建议账本 RPO=0（已提交事务）、历史研究数据 RPO<=24h，人工恢复 RTO<=30min。

---

## 14. Broker 与环境晋升

### 14.1 公共最小契约

Paper 和 Live Broker 共享最小语义：

- 查询 server time、instrument rules、balances、positions；
- submit/cancel/get/list orders；
- 获取 trades/fills 与 funding；
- 流式 market data 和 execution reports；
- 使用 client_order_id 进行幂等关联。

### 14.2 能力声明

Broker adapter 必须显式报告：

```text
BrokerCapabilities
  supported_order_types
  supports_reduce_only
  supports_client_order_lookup
  supports_batch_cancel
  supports_native_stop
  supports_hedge_mode
  execution_report_sequence_semantics
  rate_limits
```

Release 请求能力超出目标 Broker 支持范围时，部署在启动前失败，不能运行时静默降级。

### 14.3 公共能力与交易所扩展

禁止构造一个含大量可选字段的“万能 Broker”。端口分为：

- `BrokerPort`：下单、撤单、查询、成交和账户快照等公共最小能力；
- `MarginConfigurationPort`：逐仓/全仓、杠杆和保证金配置；
- `PositionModePort`：单向/双向持仓模式；
- `NativeProtectionOrderPort`：交易所原生止损/止盈等保护能力；
- 其他扩展只有在第二个实际使用方出现或目标交易所确有必要时新增。

扩展端口只由 deployment/control 层在部署和运维阶段使用，Strategy、Portfolio 和通用 Order Workflow 不得 import Binance/OKX 等具体扩展。禁止通过无约束 `extra_params` 绕过领域校验。能力缺失必须在启动前失败或采用文档化的保守替代方案。

### 14.4 Paper 到 Live

环境晋升是新的 Deployment：

```text
Paper Deployment
  -> evidence review
  -> Live Candidate config
  -> credential/capability check
  -> testnet rehearsal (如可用)
  -> observe-only shadow
  -> manual approval
  -> small-capital Live Deployment
```

禁止在运行进程中把 `paper=true` 改成 `paper=false`。Live 使用独立配置、数据目录、账户 ID、批准记录和资本上限。

---

## 15. Kill Switch 与分层安全

### 15.1 Kill Switch 不是单个按钮

分层控制包括：

1. **进程内风险冻结**：停止新开仓，撤销非 reduce-only 订单；
2. **独立 watchdog**：主进程失去心跳时发告警，按预案请求撤单；
3. **交易所原生保护**：关键仓位使用交易所托管止损/止盈或条件单；
4. **账户安全**：禁止提现、IP 白名单、独立子账户、低杠杆、逐仓优先；
5. **人工带外路径**：交易所 App/Web 和独立告警渠道可在主机宕机时操作。

### 15.2 自动动作边界

- 网络不稳定时反复市价平仓可能扩大风险，自动动作必须幂等并受状态确认约束；
- “撤单”与“平仓”是不同阶段，先确认真实 open orders；
- 对未知仓位默认进入人工升级，除非预先批准的紧急 reduce-only 策略能证明不扩大风险；
- watchdog 不运行策略、不调整组合，只执行受限应急动作。

### 15.3 必演练故障

- 主进程崩溃、主机重启；
- WebSocket 断流、REST timeout、DNS 失败；
- Broker 已受理但客户端超时；
- 数据陈旧、系统时钟偏移；
- SQLite 写失败、磁盘空间不足；
- 部分成交后撤单；
- 交易所报告与本地账本不一致；
- 告警通道失效。

---

## 16. 市场状态与事件情报

### 16.1 多轴 MarketState

单一 `BULL/BEAR/SIDEWAYS/CRISIS` 枚举会混淆趋势、波动和流动性。v0.5 使用正交维度：

```text
MarketState
  scope: GLOBAL | VENUE | ASSET | INSTRUMENT
  scope_id
  trend: UP | DOWN | RANGE | UNKNOWN
  volatility: LOW | NORMAL | HIGH | EXTREME | UNKNOWN
  liquidity: DEEP | NORMAL | THIN | DISLOCATED | UNKNOWN
  stress: NORMAL | ELEVATED | CRISIS | UNKNOWN
  confidence_by_axis
  evidence_refs[]
  observed_at
  valid_until
  model_or_rule_version
```

策略可以读取状态，但不能把它当作没有误差的事实。状态过期后回到 UNKNOWN，并采用保守风险配置。

### 16.2 状态与资金分配

- 状态主要用于限制风险、缩短订单有效期、降低杠杆和拒绝流动性不足的交易；
- 不能因分类器认为“牛市”就直接提升整体风险预算；
- 状态模型必须单独验证误判成本和切换频率；
- 组合分配使用连续风险估计为主，离散状态只作为约束或情景标签；
- 全局状态不能无条件覆盖单标的状态。

### 16.3 News/Event 的权限

新闻或事件系统只允许：

- 提高某个风险检查的优先级；
- 请求进入 `RISK_REDUCE_ONLY` 或 `FROZEN`；
- 生成带 TTL、置信度和来源的 `EventAssessment`；
- 触发人工复核。

它不能：

- 直接下单或指定订单价格/数量；
- 提高风险上限、解除冻结；
- 自动把情绪分数转换为仓位；
- 在来源不可追溯或事件已过期时继续影响决策。

### 16.4 Market Risk Model

Market Risk Model 是慢周期风险控制面，输出版本化 `PortfolioRiskSnapshot`，为资本配置和 Risk Engine 提供情景输入；它不能替代硬风控。

建议覆盖：

- 策略/标的收益相关性及估计不稳定度；
- BTC/ETH 市场 beta、方向、波动、流动性、funding/basis 等因子暴露；
- VaR 与 Expected Shortfall/CVaR（只作统计估计，不作损失保证）；
- 流动性调整后的平仓损失；
- 策略集中度、共同拥挤、容量和换手；
- 历史与合成压力情景下的组合损失。

```text
PortfolioRiskSnapshot
  snapshot_id, account_id
  position_and_policy_hash
  estimation_window, model_version
  covariance_and_uncertainty
  factor_exposures
  var, expected_shortfall
  scenario_losses
  liquidity_adjusted_loss
  observed_at, expires_at
```

- 数据不足、模型失效或快照过期时返回 UNKNOWN，并使用更保守的资本 envelope；
- 相关性和因子暴露只允许降低可用风险，不得自动突破仓位、杠杆、保证金和日损失硬限制；
- M5 单策略先使用基础 beta/流动性/压力损失，M6 再启用完整跨策略模型；
- 模型参数、窗口和情景库均版本化，并通过历史外样本与 chaos scenario 校准。

---

## 17. AI 与 RAG 架构

### 17.1 M7 的正确定位

AI 是 Research Platform 的候选生成和解释工具，不是 Live Trading Kernel 的组成部分。M7 首批能力：

- 对实验失败进行结构化归因草稿；
- 检索相似策略、市场阶段和已知失败模式；
- 生成新的 `DRAFT` 假设、参数空间或特征候选；
- 汇总研究证据，供人工审批；
- 对新闻文本生成离线标签，用于后续规则/模型研究。

### 17.2 禁止路径

```text
LLM/RAG -X-> Broker
LLM/RAG -X-> RiskLimit mutation
LLM/RAG -X-> StrategyRelease approval
LLM/RAG -X-> Blind Challenge raw result
LLM/RAG -X-> Live runtime dynamic code loading
```

### 17.3 RAG 记录治理

RAG 文档不能原地覆盖：

```text
KnowledgeRecord
  record_id
  content_hash
  source_refs[]
  source_available_at
  created_at
  supersedes_record_id
  status: ACTIVE | SUPERSEDED | RETRACTED
  scope
  model_version
  embedding_version
```

- 错误知识通过 `SUPERSEDED/RETRACTED` 追加修正；
- 每次生成记录检索到的 record_id 和 prompt/model 版本；
- 回测时只检索 `source_available_at <= simulation_time` 的内容；
- Blind Challenge 数据及派生结论不进入开发索引；
- 研究候选必须经过与人工候选相同的试验预算和发布门禁。

### 17.4 AI 评估

评估 AI 的指标不是语言质量，而是：

- 提议是否可执行、可证伪且不重复；
- 是否减少研究时间或提高候选通过率；
- 是否增加试验数量与过拟合风险；
- 来源引用是否准确；
- 在固定数据截止点重放是否一致；
- 是否遵守权限和盲测隔离。

在这些指标没有稳定证据前，不增加 AI 在系统中的权限。

---

## 18. 存储设计与演进

### 18.1 默认组合

| 数据 | 默认存储 |
| --- | --- |
| Raw/Normalized/Features | 分区 Parquet + manifest |
| 研究查询 | DuckDB 读取 Parquet |
| 元数据、实验、Release | SQLite |
| 实时 journal/outbox/inbox/order/ledger | 独立 SQLite（WAL） |
| 日志与指标 | 结构化文件 + 本地 metrics exporter |

研究元数据库与交易 journal 建议使用不同 SQLite 文件，避免批量研究写入影响下单事务。

### 18.2 SQLite 运行约束

- 交易库单 writer，由 account sequencer 所有；
- WAL 模式、`synchronous=FULL` 或经过故障测试的等价设置；
- 事务保持短小，不在事务内调用网络；
- 明确 busy timeout、磁盘空间和 fsync 延迟告警；
- 定期 backup/checkpoint，并做实际恢复演练；
- append-only 表通过触发器/权限与审计测试保护。

### 18.3 PostgreSQL 迁移触发器

只有满足下列一项并有基准数据时才评估迁移：

- 需要多主机并发写入；
- SQLite p99 写延迟持续超过实时预算；
- 单 writer 成为已测量瓶颈；
- 需要远程高可用而单机故障预算不可接受；
- 数据量或查询并发使日常运维不可控。

迁移采用单次切换或受控 CDC，不做应用层无事务双写。迁移前先定义源、目标、回滚点和一致性校验。

---

## 19. 可观测性与审计

### 19.1 关联标识

每条关键日志/事件至少携带可用字段：

```text
trace_id
deployment_id
release_id
strategy_instance_id
intent_id
reservation_id
command_id
client_order_id
broker_order_id
allocation_plan_id
ledger_tx_id
```

### 19.2 指标

- market data lag、缺口数、时钟偏移；
- strategy decision latency、超时和失败；
- risk rejection/reservation 数量与占用；
- submit/cancel latency、UNKNOWN orders、重复回报；
- 本地与交易所仓位/余额差异；
- journal 写延迟、磁盘空间、WAL 大小；
- PnL 分解、费用、funding、滑点和 residual；
- Release/Deployment 状态与冻结原因。

### 19.3 告警等级

| 等级 | 示例 | 默认动作 |
| --- | --- | --- |
| INFO | Release 部署、日终报告 | 记录 |
| WARNING | 数据延迟上升、单次 API 重试 | 通知、观察 |
| HIGH | UNKNOWN order、对账差异、数据过期 | 禁止新增风险 |
| CRITICAL | 保证金危险、账本不可写、主进程失联 | 冻结、带外通知、执行批准的应急预案 |

告警必须去重、升级并可确认。不能依赖交易进程自身作为唯一通知发送者。

### 19.4 审计链

```text
Market/Data Snapshot
  -> Strategy Decision
  -> Intent
  -> Risk Decision + Rule Versions
  -> Reservation
  -> OrderCommand
  -> Broker Raw Response
  -> ExecutionReport/Fill
  -> Allocation
  -> Ledger Entries
  -> Reconciliation Report
```

审计记录追加写，敏感字段脱敏。API Secret 永不进入日志、异常、Release 或实验记录。

### 19.5 Strategy Health 与生命周期闭环

Strategy Health 是消费交易遥测的慢周期控制面，不参与每笔订单的同步关键路径。它回答“策略是否仍按批准假设工作”，并把运行反馈送回 Research Platform。

为避免一个状态机混合事实与动作，拆成三个维度：

```text
HealthAssessment
  HEALTHY | WATCH | DEGRADED | UNKNOWN

DeploymentState
  ACTIVE | THROTTLED | REDUCE_ONLY | FROZEN | RETIRED

ResearchDisposition
  NONE | REVIEW_REQUIRED | RESEARCH_REQUIRED
```

健康评估至少覆盖：

- 扣除真实成本后的 edge 相对批准区间是否衰减；
- Sharpe、胜率、收益集中度与回撤变化，但必须带最小样本和不确定性；
- 滑点、成交率、拒单率、延迟和 funding 等执行/成本漂移；
- 特征、数据质量、市场状态和因子暴露漂移；
- 相关性、风险贡献、容量和换手变化；
- Paper/Live 与研究假设之间的偏差。

```text
StrategyHealthAssessment
  assessment_id, deployment_id, release_id
  observation_window, independent_decision_count
  metric_snapshot, expected_ranges
  health_state, confidence
  reason_codes[], evidence_refs[]
  assessed_at, valid_until

LifecycleAction
  action_id, assessment_id
  action: NONE | THROTTLE | REDUCE_ONLY | FREEZE | REQUEST_RESEARCH | RETIRE
  policy_version, previous_state, target_state
  approval_requirement, executed_at
```

- 不能用一次滚动 Sharpe 穿越阈值直接宣判策略死亡；使用最小独立决策数、置信区间、滞回和冷却期；
- 性能衰减、执行质量衰减和市场结构变化必须分开归因；
- 自动动作只能保持或降低风险：THROTTLE、REDUCE_ONLY、FREEZE；
- 加资、解冻、重新上线和扩大标的必须形成新 CapitalAllocationPolicy/Approval；
- `RESEARCH_REQUIRED` 进入研究队列，产出新 Release 后重新走完整门禁，不能修改线上 Release；
- Health Monitor 故障或数据不足时状态为 UNKNOWN，并按保守 Policy 运行，不能假定 HEALTHY。

---

## 20. 核心领域模型、事件与端口

### 20.1 必备领域模型

| 领域 | 模型 |
| --- | --- |
| 数据 | `DataBatch`, `DatasetManifest`, `FeatureDefinition`, `FeatureMaterialization`, `FeatureEvaluation`, `InstrumentRuleVersion` |
| 研究 | `TrialRegistration`, `ResearchBudget`, `BudgetConsumption`, `ExperimentRun`, `EvidenceReport`, `BlindChallengeResult` |
| 发布 | `StrategyRelease`, `Deployment`, `ApprovalRecord`, `StrategyHealthAssessment`, `LifecycleAction` |
| 意图 | `PositionIntent`, `CapitalAllocationPolicy`, `AllocationPlan`, `RiskDecision`, `RiskReservation` |
| 执行 | `OrderCommand`, `ExecutionReport`, `Fill`, `BrokerCapabilities` |
| 账本 | `AccountLedgerEntry`, `StrategyLedgerEntry`, `PositionSnapshot` |
| 运维 | `ReconciliationRun`, `RiskModeTransition`, `IncidentRecord`, `MarketChaosScenario`, `ChaosRun` |
| 情报 | `MarketState`, `PortfolioRiskSnapshot`, `EventAssessment`, `KnowledgeRecord` |

字段细节以 `docs/02` 为实现契约。v0.5 落地时必须先更新 docs/02，再改代码；本文给出的是必须表达的语义，不替代完整 schema。

### 20.2 关键事件

```text
DATA_AVAILABLE
DATA_STALE
FEATURE_REGISTERED / FEATURE_DEPRECATED
RESEARCH_BUDGET_RESERVED / RESEARCH_BUDGET_EXHAUSTED
STRATEGY_DECISION_RECORDED
INTENT_CREATED / INTENT_REJECTED
RISK_RESERVED / RESERVATION_RELEASED
ORDER_COMMAND_CREATED
ORDER_SUBMIT_REQUESTED
ORDER_STATE_CHANGED
EXECUTION_REPORT_RECEIVED
FILL_RECORDED / FILL_ALLOCATED
LEDGER_POSTED
RECONCILIATION_STARTED / COMPLETED / FAILED
RISK_MODE_CHANGED
RELEASE_APPROVED / DEPLOYMENT_STARTED / DEPLOYMENT_FROZEN
STRATEGY_HEALTH_CHANGED / RESEARCH_REQUIRED
CAPITAL_ALLOCATION_POLICY_APPROVED
PORTFOLIO_RISK_UPDATED
CHAOS_RUN_COMPLETED
```

事件信封至少含 `event_id`、`event_type`、`schema_version`、`occurred_at`、`recorded_at`、`source`、`correlation_id`、`causation_id` 和 payload。事件总线只保证至少一次，消费者必须幂等。

### 20.3 核心端口

- `PointInTimeDataPort`：按 as_of 查询历史可见数据；
- `FeatureRegistryPort` / `FeatureStorePort`：管理特征语义、物化 lineage，并加载 PIT 安全特征；
- `ExperimentStorePort`：append-only 试验与结果；
- `ResearchBudgetPort`：在实验开始前原子预留、消费和查询统一研究预算；
- `ReleaseStorePort`：构建、校验和读取不可变 Release；
- `BrokerPort`：最小订单/查询/流式契约；
- `MarginConfigurationPort` / `PositionModePort` / `NativeProtectionOrderPort`：部署阶段的能力型交易所扩展；
- `OrderJournalPort`：事务性命令、状态和 outbox；
- `LedgerPort`：原子追加分录与快照查询；
- `ReconciliationPort`：账户快照、差异和处置记录；
- `StrategyHealthPort`：读取健康评估并提交只会降低风险的生命周期请求；
- `PortfolioConstructionPort` / `MarketRiskModelPort`：生成版本化资本 Policy 与风险快照；
- `ChaosScenarioPort`：运行可重放的市场/交易所异常场景并保存结果；
- `ClockPort`：测试可控的 UTC 时钟；
- `AlertPort`：与交易进程解耦的通知。

端口签名与错误语义必须在 `docs/03` 固化。不能用 `dict[str, Any]` 逃避领域契约。

---

## 21. 配置、安全与权限

### 21.1 配置层次

```text
code defaults
  < versioned non-secret config
  < deployment config
  < environment variables / secret provider
  < emergency runtime state (only safer direction)
```

运行时配置只能自动向更保守方向变化。提高杠杆、日损失上限、允许标的或资本规模必须创建新批准记录。

### 21.2 密钥管理

- 研究和数据进程没有交易 Secret；
- Live key 仅有读取和交易权限，无提现；
- watchdog 如持有 key，应使用单独、能力更小的凭据；
- 日志与异常统一过滤敏感 header、query 和 payload；
- 启动时验证 key 权限，发现提现权限立即拒绝运行；
- 定期轮换并演练吊销。

### 21.3 人工操作

所有会改变实盘状态的 CLI 命令要求：

- 显示账户、环境、Release 和动作摘要；
- Live 环境二次明确确认，支持非交互审批 token 而不是模糊 `--yes`；
- 写入 operator、时间、前后状态和理由；
- 幂等执行；
- 高风险操作提供 dry-run。

---

## 22. 测试策略

### 22.1 测试金字塔

- 领域状态机和分摊规则：大量性质测试与单元测试；
- adapter：固定录制样例 + mock server 契约测试；
- 数据：PIT、迟到、缺口、元数据有效期测试；
- 研究：确定性、泄漏探针、盲测访问控制、多重试验统计测试；
- 内核：journal/outbox、崩溃点、重启和对账集成测试；
- 端到端：单 Release 从实时输入到 Paper Fill/账本；
- Live 前：testnet 或最小订单演练，随后小资金受限运行。

### 22.2 必须独立验证的性质

```text
重复任何事件不会重复记账
任何状态重放不会倒退终态
任何已发送订单都能在本地找到提交前记录
任何预留最终都被消费或释放
任何真实 Fill 都被完整分配或进入显式 residual
所有虚拟持仓之和与真实仓位在容差内一致
任何 as_of 查询都不返回未来才可见的数据
任何 Blind Challenge 数据都不能被开发/AI 进程读取
```

### 22.3 门禁

每个里程碑结束至少运行：

```bash
ruff check src tests
mypy src
pytest
```

交易内核阶段还必须运行故障注入和恢复测试；Live 晋升还必须提供对账、Kill Switch、告警和手工回退演练证据。测试数量不是验收标准，关键不变量是否被真实执行路径覆盖才是。

### 22.4 Market Chaos Testing

软件故障注入只证明系统能恢复进程状态，不能证明极端市场中风险仍受控。Market Chaos Scenario Library 至少包含：

- 10 分钟下跌 20%、向上/向下瞬时跳空；
- spread 与滑点扩大 10～50 倍、盘口深度消失；
- 订单只能部分成交、止损触发但长时间无法成交；
- funding 异常、mark/index 显著偏离、保证金档位突变；
- 行情继续更新但订单 API 高延迟/限频，或相反；
- 多策略同时去杠杆并争用同一流动性；
- 交易所保护单拒绝、撤单与成交交叉、状态回报延迟。

```text
MarketChaosScenario
  scenario_id, version
  market_path, liquidity_path
  funding_and_margin_path
  broker_fault_schedule
  expected_invariants[]

ChaosRun
  chaos_run_id, scenario_id
  release_or_portfolio_policy_id
  system_version, seed
  observed_actions, invariant_results
  worst_loss, freeze_latency
  run_at
```

验收关注系统行为边界而非“是否盈利”：停止新增风险、无双单、Reservation 不低估、UNKNOWN 可收敛、账本守恒、冻结延迟达标、最坏损失不超过批准情景上限。bar 级模拟无法保证真实极端行情成交，因此结果必须保守，并与小资金 Live 风险上限配套。

---

## 23. 里程碑实施计划

### M-1：工程与可复现地基

目标：建立依赖锁、模块边界、质量门禁、测试组织、日志与 ADR 流程。

退出条件：干净环境可安装；CLI 可启动；三项质量门禁通过；架构决策可追踪。现有实现可复用，切换 v0.5 时只做差距审计。

### M0：领域契约与持久化骨架

目标：先定义 v0.5 核心模型、事件、端口和数据库迁移机制，不实现业务引擎。

必须新增或核对：PIT 时间字段、Feature Registry、Research Budget、StrategyRelease、Strategy Health、CapitalAllocationPolicy、PortfolioRiskSnapshot、RiskReservation、完整 OrderState、AllocationPlan、双账本、Reconciliation、BrokerCapabilities/扩展端口和 Chaos 模型。

退出条件：docs/02/03/05 与代码契约一致；状态机和数据库约束测试通过；迁移可重复执行。

### M1：Point-in-Time 数据真实性

目标：完成可审计的数据采集、历史元数据、PIT 查询、Feature Registry 和版本化 manifest。

退出条件：迟到数据测试证明 available_at 前不可见；历史 instrument rules 可回放；Raw 不覆盖；FeatureDefinition/Materialization lineage 完整且历史 Release 可追溯；数据陈旧能触发风险事件。

### M2：策略 SDK 与不可变发布物

目标：策略只能生成 Intent；状态确定性可恢复；Release 可构建、校验和审批；Live 策略隔离边界可用。

退出条件：策略无 Broker/密钥；future-data 探针失败；同输入重放一致；工作目录修改不影响已构建 Release；越权/超时策略被隔离。

### M3：可信研究与盲测治理

目标：PIT 回测、FeatureEvaluation、统一 Research Budget、实验注册、成本/容量、walk-forward、Blind Challenge 与 Evidence Report。

退出条件：优化器无法读取盲测；人工/脚本/优化器/AI 共用 family trial budget，相似候选不可重置计数；报告分离 EvidenceQuality/ExpectedEdge/RiskEstimate；基准、置信区间和压力情景齐全；Forward Paper 候选可生成但不自动晋升。

### M4：持久化交易内核与单策略 Paper

目标：完成 account sequencer、单策略 CapitalAllocationPolicy、风险预留、journal/outbox、订单状态机、Paper Broker、账本、健康遥测、启动/周期对账和 Chaos Testing v1；只运行一个策略。

退出条件：崩溃矩阵与核心市场 chaos scenario 通过；UNKNOWN/部分成交/撤单可收敛；任何 Broker 副作用前有持久化记录；Paper 账户对平；健康遥测可追溯；重启默认冻结；至少完成规定的稳定性与异常演练。

### M5：单策略小资金实盘

目标：在独立部署中通过公共 Broker + 能力型扩展接入一个 Live Broker，以单 Release、单策略、白名单标的、低杠杆和硬资本上限运行；启用 Strategy Health 的自动降险闭环。

退出条件：capability/extension check、testnet/最小订单、observe-only、人工审批、对账、Strategy Health 的 WATCH/DEGRADED/UNKNOWN 与自动降险、带外告警、Kill Switch 和 Chaos 准入演练通过；连续运行期间无无法解释账本差异。未达到统计样本时只能称“运行验证通过”，不能声称策略盈利有效。

### M6：多策略虚拟账本与组合

目标：加入慢周期 Portfolio Construction、CapitalAllocationPolicy、Market Risk Model、AllocationPlan、策略虚拟账本、合并订单和成交/费用/funding 分摊。

退出条件：相关性/因子/CVaR/流动性压力和模型 UNKNOWN 降级可验证；资本 Policy 不使用单一评分直接映射；所有对平不变量与性质测试通过；多策略共同去杠杆 Chaos 测试通过；策略独立停启安全；多策略先 Paper 再小资金 Live。

### M7：AI 离线研究增强

目标：在研究平台加入治理后的 RAG、候选生成和证据总结；AI 只能通过现有 Feature Registry 与 Research Budget 工作，不改变 Live Kernel 权限模型。

退出条件：时间截止点检索、来源、supersede/retract、盲测隔离和 prompt/model 版本可追溯；AI 候选与人工候选使用同一预算、相似度与发布门禁；权限测试证明无法触达 Broker、风险修改和发布审批。

---

## 24. 上线准入与回退

### 24.1 Paper 准入

- Release 完整且已批准；
- 研究报告无未解决 P0/P1；
- 所需 Broker 能力已声明；
- 数据 freshness、风险和订单工作流测试通过；
- 单策略 CapitalAllocationPolicy 已批准，健康遥测可用；
- 核心 Market Chaos Scenario 不变量通过；
- 资本、标的和运行时段明确。

### 24.2 Live 准入

- Paper 覆盖足够的日历周期与独立决策事件；
- 故障演练、重启对账、告警和带外操作通过；
- Strategy Health 的 WATCH/DEGRADED/UNKNOWN 与自动降险演练通过；
- 目标 Broker 的公共能力与必要扩展能力均已验证；
- 使用独立 Live deployment 与最小权限 key；
- 初始资本损失可完全承受，硬上限不依赖策略自律；
- 人工确认残余风险、模型假设与回退步骤。

### 24.3 自动回退条件

自动降级到 `RISK_REDUCE_ONLY` 或 `FROZEN`：

- market data 超过陈旧阈值；
- 账本无法持久化；
- 出现无法解析的 UNKNOWN order；
- 对账差异超过容差；
- 日损失、回撤、保证金或杠杆越限；
- Broker 错误率或时钟偏差越限；
- 策略输出超时、schema 非法或频率异常。
- Strategy Health 为 DEGRADED/UNKNOWN 且持续超过批准策略；
- PortfolioRiskSnapshot 过期、模型失败或压力损失超限。

自动回退不能自动恢复。恢复需要根因记录、对账通过和人工确认。

---

## 25. 架构风险与待决策项

### 25.1 仍然存在的风险

- 单机仍有共同故障域，watchdog 与交易所原生保护只能降低而不能消除风险；
- bar 级回测不能准确模拟剧烈行情中的盘口与队列，需保守滑点和小资金验证；
- 交易所 API 语义可能变化，adapter 契约测试与 capability check 必须持续维护；
- Blind Challenge 对个人项目存在操作泄漏风险，需要工具权限隔离而非只靠自律；
- 多策略虚拟分摊是会计模型，不代表策略在独立账户能获得完全相同成交；
- AI 可能增加假设搜索规模，从而加重过拟合，必须统计总试验预算。

### 25.2 实施前需由用户确定的参数

以下不是架构空白，但必须在进入相应里程碑前固化为配置/ADR：

1. 首个交易所、账户模式（单向/双向）及逐仓/全仓策略；
2. 首批标的、周期和最大初始资本；
3. 最大总杠杆、单标的敞口、日损失和回撤阈值；
4. Paper 最短日历周期和最小独立决策数；
5. Blind Challenge 数据隔离方式与揭示预算；
6. watchdog 是否使用独立主机/网络及是否具备撤单权限；
7. SQLite 的实测延迟预算、备份周期和 RPO/RTO；
8. M6 的成交分摊规则与 residual 容差。
9. Strategy Health 的最小独立决策数、滞回、冷却期和自动降险策略；
10. Market Risk Model 因子集合、压力情景库和 UNKNOWN 降级幅度；
11. Research Budget 的周期、family 归属、相似度阈值和 Blind reveal 额度。

### 25.3 明确延后

- 多交易所和跨场所路由；
- PostgreSQL/消息队列迁移；
- Web 控制台；
- 自动策略进化、自动加资与自动解冻；
- 在线 LLM 或自然语言交易；
- 高频盘口仿真与低延迟基础设施。

---

## 26. v0.5 采用规则

1. 本文件批准后，先更新 `prompts/00-project-context.md` 与 M0～M7 Prompt；
2. docs/02/03/05/07 尚未更新前，它们描述的是已实现 v0.4 契约；任何 v0.5 代码变更必须先用 ADR 和文档明确差异；
3. 对现有 M0～M3 逐阶段执行差距审计，符合项保留证据，缺失项形成修复任务；
4. M4 不得在 PIT、Blind Challenge、StrategyRelease 最小边界尚未满足时进入实盘方向开发；
5. 架构状态、实际代码状态和验收状态必须在 `HANDOVER.md` 分开记录，禁止用“设计完成”替代“实现完成”。

本版本的核心判断是：**先建立可信事实、可恢复交易和可审计归因，再扩大策略数量、资本规模和 AI 自主程度。**
