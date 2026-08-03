# 个人加密货币中频 AI 量化交易系统架构设计

> 版本：v0.4（新增：事件驱动多 Agent 架构、规则版新闻风控提前至 M5/M6、Coordinator 战略层边界、风险模式状态机；并同步 MVP 里程碑编号）
> 定位：个人单机使用的量化研究与自动交易平台，非 SaaS、非多用户
> 市场：加密货币（现货 Spot + 永续合约 Perpetual Futures），MVP 优先支持主流交易所
> 频率：中频；默认研究频率 5m / 15m / 1h（1m 仅用于候选策略精细验证），不考虑毫秒级高频，但架构不排斥未来扩展
> 范围：总体架构、模块拆分、领域模型、数据模型、生命周期、交易流程、回测、风控、Broker 抽象、MVP 路线、评审意见
> 非目标：不写代码、不讨论商业模式、不讨论营销、不讨论具体赚钱策略

---

## 目录

0. [需求确认与实施优先级](#0-需求确认与实施优先级)
1. [总体架构设计](#1-总体架构设计)
2. [模块详细拆分](#2-模块详细拆分)
3. [核心领域模型](#3-核心领域模型)
4. [数据模型设计](#4-数据模型设计)
5. [策略生命周期设计](#5-策略生命周期设计)
6. [交易流程设计](#6-交易流程设计)
7. [回测系统设计](#7-回测系统设计)
8. [风控设计](#8-风控设计)
9. [Broker 抽象设计](#9-broker-抽象设计)
10. [MVP 开发路线](#10-mvp-开发路线)
11. [当前设计存在的问题和改进建议](#11-当前设计存在的问题和改进建议)
12. [附录](#12-附录)

---

## 0. 需求确认与实施优先级

### 0.1 已确认的需求决策

| # | 决策项 | 确认结论 |
| --- | --- | --- |
| 1 | 首选交易所 | Binance 第一接入目标（API 生态成熟、流动性较高、现货与永续均支持、公开数据丰富）；MVP 顺序：Paper Broker → Binance Adapter → 后续 OKX / Bybit；所有交易所必须通过 Adapter 抽象接入，核心系统不直接依赖任何交易所 |
| 2 | 历史数据 | MVP 不购买付费数据，使用交易所公开 API（REST 历史 K 线 + WebSocket 实时行情）；设计 Data Adapter 端口，未来支持付费源替换；当前重点是打通「数据采集 → 策略研究 → 回测 → 模拟交易」闭环，而非数据覆盖极限 |
| 3 | 回测频率 | 分层策略：默认研究频率 5m / 15m / 1h（数据量可控、回测快、适合快速迭代）；1m 不作默认全量回测，仅用于候选策略精细验证、滑点测试、成交模拟优化 |
| 4 | 控制台 | MVP 只需要 CLI：启动任务、回测执行、策略管理、查看实验结果、查看交易状态；Web 控制台延期，系统稳定后再开发 |
| 5 | 部署 | 个人本机量化工作站：本地数据存储、定时任务、回测任务、模拟交易、实盘运行；暂不考虑云部署，但架构预留未来迁移小型服务器的可能 |
| 6 | 运行模式 | **多策略组合模式**：多个策略同时运行（趋势、套利、网格等），通过 Portfolio Manager 进行资金分配、策略权重管理、仓位合并、风险暴露控制；暂不实现复杂组合优化算法，但领域模型支持未来扩展 |

### 0.2 实施优先级（已确认）

1. Research System 闭环
2. Backtest Engine
3. Strategy Framework
4. Paper Trading
5. Portfolio Management
6. Risk Engine
7. Live Trading

**不提前开发**：Web 控制台、多交易所接入、云部署、付费数据系统、高频交易基础设施。

### 0.3 决策对架构的影响

- **Binance 先行**：第一个行情/实盘 Adapter 以 Binance 为实现基准，同时以契约测试保证 OKX / Bybit 等可后插，核心系统只依赖端口；
- **分层回测频率**：数据采集、特征计算、回测配置的默认频率为 5m / 15m / 1h；1m 作为候选验证专用频率，由配置控制，不改变架构；
- **多策略组合**：Portfolio Manager 从「账户管理」升级为一等模块（见 2.4.6），策略权重、资金分配、组合敞口进入核心领域模型（见 3.5）；
- **CLI 优先**：控制平面以 CLI 命令集为核心交付物，Web 控制台仅为未来可选视图；
- **本机部署**：模块化单体 + 独立 worker 进程即可；目录规范（config / data / state 分离）保证未来可整体迁移到小型服务器；
- **免费数据优先**：数据质量策略先服务「闭环可跑通」，付费源通过 Data Adapter 端口未来接入。

---

## 1. 总体架构设计

### 1.1 系统定位与闭环

本系统是「个人加密货币中频量化研究与交易平台」：

- 研究系统负责**发现与验证策略**（离线、可并行、结果不可变）；
- 交易系统负责**运行已验证策略**（在线、低延迟、强风控）；
- 两者通过「不可变策略版本包」与「发布门禁」交互，各自独立演化；
- 实盘运行结果回流到研究系统，形成「研究 → 验证 → 运行 → 反馈 → 再研究」的迭代闭环；
- 当前阶段不接 LLM，但所有未来 AI Agent 需要的工件与端口现在就定义好；
- 系统形态为**事件驱动的多 Agent 量化系统**：News Agent（外部信息）与 Market Agent（行情状态）通过结构化事件驱动 Coordinator（战略决策），Quant Engine（策略/组合/风控/执行）负责验证与执行；Agent 按事件触发，不永久在线。

### 1.2 架构总览（文字图）

```
┌─────────────────────────────── 控制平面 ────────────────────────────────┐
│  CLI / Web 控制台 │ Scheduler 调度器 │ Lifecycle Manager 生命周期管理器  │
│  配置管理 │ 任务管理 │ 人工干预入口 │ Kill Switch（全局/策略级）          │
│  Agent 网关（News / Market / Coordinator，事件驱动，M7 起）            │
└──────────────┬───────────────────────────────────────────────┬──────────┘
               │ 发布/门禁/指令                                  │ 运行指令
┌──────────────▼────────────────┐              ┌───────────────▼──────────┐
│  Research System 研究系统      │              │  Trading System 交易系统  │
│                               │  策略包      │                         │
│  · 数据分析 / 特征引擎         │  (不可变)    │  · Market Data Gateway    │
│  · Strategy Framework(插件化)  │────────────►│  · Strategy Runtime(隔离)  │
│  · Backtest Engine(双模式)     │              │  · Signal Engine          │
│  · Optimizer(网格/贝叶斯/遗传) │  实验反馈    │  · Risk Engine(核心)       │
│  · Experiment Management      │◄────────────│  · Execution Engine        │
│  · Strategy Registry(生命周期) │              │  · Portfolio Manager      │
│  · 评估/门禁                   │              │  · Monitoring             │
└──────────────┬────────────────┘              └───────────────┬──────────┘
               │                                              │
               └──────────────┬───────────────────────────────┘
                              │
                  ┌───────────▼───────────┐
                  │   Data Infrastructure │
                  │  Raw → Normalized →   │
                  │  Feature Store / 元数据│
                  └───────────┬───────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
┌─────────▼─────────┐ ┌───────▼────────┐ ┌───────▼────────┐
│  Market Adapter   │ │ Exchange Adapter│ │ Broker Adapter │
│  行情接入(WS/REST) │ │ 交易所连接/限频  │ │ 交易/账户抽象   │
│  Binance/OKX/Bybit│ │ 认证/重连       │ │ Paper / 实盘   │
└───────────────────┘ └────────────────┘ └────────────────┘
          │                   │                   │
          └───────────────────┼───────────────────┘
                              ▼
                    交易所公开 API / WebSocket

横向公共设施：事件总线 │ 配置中心 │ 结构化日志 │ 指标/告警 │ 审计日志
```

事件总线提供**高优先级事件通道**：新闻事件（News Event）与风险模式变更（Risk Mode Change）以最高优先级同步投递到风控引擎，不受普通异步队列拥塞影响（详见 2.7 与 8.5 节）。

### 1.3 三层 Adapter 的职责划分（关键设计）

| Adapter | 职责 | 说明 |
| --- | --- | --- |
| Market Adapter | 市场行情数据接入：K 线、Tick、订单簿快照、资金费率、持仓量、标记价格 | 把多交易所行情归一化为统一 schema；数据只管「看」，不管「交易」 |
| Exchange Adapter | 交易所连接层：REST/WebSocket 连接、签名鉴权、限频管理、断线重连、心跳 | 被 Market Adapter 与 Broker Adapter 复用；不包含业务语义 |
| Broker Adapter | 交易与账户抽象：下单/撤单/查询、成交回报、账户/持仓查询 | Paper Broker 与 Exchange Broker 实现同一接口；上层不感知模拟/实盘 |

依赖方向：`系统内部模块 → Broker Adapter → Exchange Adapter → 交易所`；`系统内部模块 → Market Adapter → Exchange Adapter → 交易所`。

这样拆分的原因：

- **市场数据与交易解耦**：同一套数据管线可同时服务研究系统（离线补数据）与交易系统（实时订阅）；
- **连接层复用**：Binance 一个连接实现同时支撑行情订阅与下单，限频/重连逻辑只写一遍；
- **可扩展性**：新增交易所 = 新增三个 Adapter 的实现（或组合复用，如共用 Market 数据源、单独实现 Broker），不改任何上层模块。

### 1.4 分层与依赖方向

| 层 | 模块 | 关键约束 |
| --- | --- | --- |
| 控制平面 | CLI/控制台、Scheduler、Lifecycle Manager、配置、任务、人工干预、Kill Switch | 只做编排与门禁，不参与策略计算与交易关键路径 |
| 研究系统 | 数据分析、特征引擎、策略框架、回测、优化、实验管理、策略注册表 | 离线、可并行、结果不可变 |
| 交易系统 | 行情网关、策略运行时、信号引擎、风控、执行、组合管理、监控 | 在线、关键路径短、强审计 |
| 数据基础设施 | Raw / Normalized / Feature Store / 元数据库 | 统一 schema、版本化、可复现 |
| Broker 层 | Paper Broker / Exchange Broker | 统一接口、可热切换 |
| 公共设施 | 事件总线、配置、日志、指标、审计 | 最底层依赖，不反向依赖业务模块 |

依赖规则：

- 控制平面 → 研究系统 / 交易系统 → 数据基础设施 / Broker 层 → 公共设施；
- 研究系统与交易系统**互不依赖**，只通过策略包与实验工件交互；
- 所有业务模块只依赖端口（接口定义），实现通过依赖注入组装；
- **禁止反向依赖**：数据层不依赖策略层，Broker 层不依赖风控层。

### 1.5 设计原则与强制约束

1. **模块化单体优先**：个人单机部署，先做成一个进程内的模块化应用；只有明确瓶颈（回测 CPU 密集、实盘需要隔离）才拆独立进程，且按 9 节演进路径走。
2. **策略不能直接操作交易接口**：

   - 错误：`Strategy: buyBTC()`；
   - 正确：策略只输出「交易意图（Trading Intent）」：`{symbol, direction, action, quantity}`，再经过：

   `Strategy → Portfolio Management → Risk Engine → Execution Engine → Broker Adapter`

3. **不可变性**：策略版本、数据批次、实验记录、回测配置一经生成不可修改；修改即新版本。
4. **可复现**：任何结论（回测指标、风控事件、实盘反馈）必须能指向一个实验/运行记录，记录完整上下文（数据版本 + 特征版本 + 策略版本 + 参数 + 环境锁）。
5. **AI-Ready 但不依赖 LLM**：AI 端口、工件 schema、Agent 网关现在定义，MVP 用本地规则实现占位；AI 永远不能绕过 Risk Engine 与 Execution Engine。
6. **效率优先、不为性能过早复杂化**：用缓存、列存、并行、内存态解决性能问题，不引入分布式系统。
7. **代码规范**：文档用中文，代码标识符用英文，关键结构均有文档注释。

---

## 2. 模块详细拆分

### 2.1 控制平面（Control Plane）

#### 2.1.1 CLI / Web 控制台

- **已确认：MVP 只做 CLI**，优先覆盖：启动任务、回测执行、策略管理、查看实验结果、查看交易状态；
- CLI 命令集示例：`research backtest`、`strategy promote`、`trade status`、`risk freeze` 等；
- Web 控制台延期到系统稳定后（M6+）再开发；无论 CLI 还是未来控制台，都不提供绕过风控/审计的隐藏通道；
- 所有人工操作都写审计日志。

#### 2.1.2 Scheduler（调度器）

- 定时任务：数据增量采集、特征重算、每日回测巡检、持仓巡检、对账、报告生成；
- 任务定义：类型、触发时间（cron/间隔）、参数快照、超时、失败策略（重试/告警/跳过）；
- 每次运行生成 TaskRun 记录。

#### 2.1.3 Lifecycle Manager（生命周期管理器）

- 策略状态机的唯一操作入口（见第 5 章）；
- 所有迁移必须附带证据（实验 ID / 报告 ID / 人工指令），生成 LifecycleTransition 事件与审计记录。

#### 2.1.4 配置管理

- 分层：`默认配置 → 用户配置 → 环境变量 → 运行时覆盖`；
- 系统配置（交易所、数据源、风控参数、调度）与策略参数分离；策略参数属于策略版本，随版本不可变；
- 敏感信息（API Key/Secret）只从环境变量或系统密钥环读取，禁止进配置文件、日志与实验记录。

#### 2.1.5 任务管理

- 任务队列（进程内异步队列）：长任务（回测/优化/数据批量下载）可提交、可查询、可取消；
- 回测/优化任务以独立 worker 进程执行，避免阻塞实盘主进程；
- 任务状态机：`PENDING → RUNNING → SUCCEEDED / FAILED / CANCELED`。

#### 2.1.6 人工干预入口与 Kill Switch

- 操作集：全局暂停/恢复、策略级暂停/恢复、撤单、平仓、Kill Switch；
- Kill Switch 语义：冻结所有策略信号、撤销在途订单、只保留人工平仓通道；恢复必须显式人工操作，不能程序自动恢复；
- 所有干预写入审计日志（谁、何时、做了什么、为什么）。

### 2.2 数据基础设施（Data Infrastructure）

#### 2.2.1 数据源与 Adapter

- **已确认：MVP 不购买付费数据**，优先使用交易所公开免费 API：REST 历史 K 线 + WebSocket 实时行情 + Funding Rate + Open Interest 公开接口；
- Market Adapter 输出统一数据 schema，上层不感知数据来自哪个交易所；
- 预留付费数据源端口（**Data Adapter 抽象**）：Data Adapter 与 Market Adapter 共享同一数据出口，未来付费源接入时上层零改动；
- 当前阶段重点是打通完整研究-交易闭环，不追求数据覆盖极限；数据深度按需渐进扩展。

#### 2.2.2 存储分层

| 层 | 存储 | 内容 | 特点 |
| --- | --- | --- | --- |
| Raw | Parquet（原始文件）+ 采集日志 | 交易所原始返回（K 线/Tick/资金费率/持仓量/订单簿快照） | 只增不改，按 source+date 分区 |
| Normalized | Parquet + DuckDB 索引 | 统一 schema：OHLCV bar、Tick trade、funding rate、open interest、mark price | 回测与特征计算的主数据源 |
| Feature Store | Parquet / DuckDB | 特征计算结果（技术指标、衍生因子） | 特征版本化，可增量重算 |
| Meta | SQLite | 标的元数据、交易日历（加密货币 7×24，含维护时间）、策略版本、实验记录、运行状态 | 强一致、事务化 |
| State | SQLite + 内存 | 实盘运行状态：订单、持仓、账户、风控状态 | 热路径内存，异步持久化 |

#### 2.2.3 数据质量与一致性

- 每批数据带质量报告：缺口检测、异常值（跳空/负量/价格突变）、时间戳乱序、复权/价格源一致性、资金费率与持仓量缺失率；
- 标记 `suspect` 的数据默认不进回测，可人工放行并记录；
- 数据版本号贯穿全链路：实验记录必须注明数据版本与特征版本；
- 加密货币特殊性：交易所维护/故障期会有数据空洞，回测与实盘都要有「数据空洞策略」（跳过、插值并标记、暂停交易）。

### 2.3 研究系统模块（Research System）

#### 2.3.1 数据分析模块

- 提供历史数据探索、统计摘要、相关分析、可视化导出；
- 与特征引擎共用数据访问层，保证「分析看到的数据」与「回测用的数据」完全一致；
- 支持市场状态标记（趋势/震荡/波动率状态）——这些标记作为工件输出，未来是 Market Analyst Agent 的输入。

#### 2.3.2 Feature Engine（特征引擎）

- 输入：Normalized 数据；输出：特征快照（FeatureSet）；
- 特征分类：
  - 价格特征：收益率、动量、移动平均、布林带、ATR 等；
  - 成交量特征：成交量变化率、买卖量比（taker 占比）、成交量加权价格；
  - 波动率特征：已实现波动率、波动率锥、波动率聚集指标；
  - 衍生品特征：资金费率（当前/历史分布）、持仓量（OI 及其变化率）、基差、标记价格 vs 最新价偏离、资金费率与价格相关性；
  - 技术指标：MACD、RSI、KDJ、量价背离类指标（作为基础库，具体策略自行选用）；
- 强制约束：**确定性 + 无未来函数**；特征只允许使用「截至当前 bar」的数据；
- 特征版本化：特征定义变更 = 新版本，旧版本保留可复现；
- 增量计算：新数据到达只重算受影响的标的与窗口；
- 回测特征与实盘特征必须由同一管线产出，杜绝「回测有、实盘没有」的字段。

#### 2.3.3 Strategy Framework（策略框架）

- 策略插件化：每个策略是一个插件包（代码 + 参数 schema + 依赖清单 + 数据/特征依赖声明）；
- 统一策略接口（回测与实盘同一套实现，禁止两套代码）：
  - `initialize(context)`：初始化；
  - `on_bar(context, bar)` / `on_tick(context, tick)`：行情回调；
  - `on_funding(context, funding)` / `on_oi(context, oi)`：衍生品数据回调（永续策略需要）；
  - `on_intent_result(context, result)`：接收自身信号的执行反馈（可选，用于自适应策略）；
  - 唯一输出：`TradingIntent`；
- 内置策略基类与模板，覆盖典型风格：
  - 趋势策略（Trend）：通道突破、均线系统、动量；
  - 均值回归（Mean Reversion）：布林回归、RSI 反转、价差回归；
  - 套利策略（Arbitrage）：资金费率套利、期现套利、跨所价差（MVP 后期）——注意套利策略需要「组合级仓位」支持；
  - 网格策略（Grid）：价格网格 + 资金费率收益增强（现货/永续变体）；
  - AI 生成策略（预留）：未来由 Strategy Agent 产出的策略与人工策略完全同构（同一脚手架、同一门禁）。
- 脚手架生成器：模板生成「策略骨架 + 参数 schema + 默认回测配置 + 冒烟测试」，保证所有策略结构统一、可机器生成（这是未来 AI 生成策略的前提）。

#### 2.3.4 Backtest Engine（回测引擎）

双模式（详见第 7 章）：

| 模式 | 用途 | 特点 |
| --- | --- | --- |
| 向量化模式 | 参数粗筛、因子验证 | 快（万级参数搜索），忽略部分执行细节 |
| 事件驱动模式 | 候选策略精测、上线前验证 | 贴近实盘：撮合、手续费、滑点、资金费率、杠杆、保证金、强平 |

#### 2.3.5 Optimizer（参数优化器）

- 搜索算法可插拔：网格搜索 → 随机搜索 → 贝叶斯优化 → 遗传算法（按 M3/M6 逐步引入）；
- 目标函数可配置：默认组合指标（收益/回撤/夏普加权），支持多目标帕累托输出；
- 强制 walk-forward：时间滚动训练/验证，输出参数稳定性分析；
- 并行：多进程按参数分片执行，进程数/内存可配置；
- 每次优化生成 OptimizationRun：搜索空间、每次评估指标、最优参数集及样本外表现。

#### 2.3.6 Experiment Management（实验管理）

- 类似 ML 实验管理：每次回测/优化都是 ExperimentRecord：
  - 实验 ID、策略版本、数据版本、特征版本、参数集、引擎配置、环境锁文件、结果指标、时间戳、状态；
- 记录不可变，提供「重跑验证」：任取历史实验 ID 在当前环境重建并对比；
- 提供对比视图：同一策略不同参数、不同版本、回测 vs 实盘反馈的横向对比（这是未来 AI Backtest Agent 的输入）。

#### 2.3.7 Strategy Registry（策略注册表）

- 管理策略元数据、所有版本、生命周期状态（Draft / Backtesting / Validated / Paper Trading / Production / Deprecated + 异常状态 Paused / Disabled / Archived）；
- 状态迁移只能通过 Lifecycle Manager；
- 版本与状态分离：指标结论绑定具体版本与数据版本，新版本不继承旧版本结论。

### 2.4 交易系统模块（Trading System）

#### 2.4.1 Market Data Gateway（实时行情网关）

- 基于 WebSocket 订阅：K 线、Tick（trade）、订单簿增量/快照、资金费率、持仓量、标记价格；
- 能力：订阅管理、心跳、断线重连（指数退避）、乱序缓冲、时钟校准（exchange_ts vs received_ts）、去重去抖；
- 统一事件流进入事件总线，策略运行时与风控引擎消费同一份数据，保证风控不滞后；
- 行情事件在关键路径上提供「零拷贝视图」引用，避免重复序列化。

#### 2.4.2 Strategy Runtime（策略运行时）

- 每个运行中的策略一个独立进程（MVP 至少独立 asyncio task，逐步进程化），崩溃不影响其他策略与主系统；
- 运行时沙箱：策略只获得三类能力——读行情/特征（只读）、写交易意图（唯一输出）、读自身状态（只读）；
- 运行前静态检查：import 白名单、禁止网络/文件写/线程/子进程（这是未来 AI 生成代码的安全底线）；
- 热更新：版本切换 = 停旧进程 + 起新进程；MVP 采用「重启式切换」保证简单可靠，持仓上下文通过 Portfolio Manager 恢复；
- 运行时提供策略健康状态：心跳、处理时延、异常次数。

#### 2.4.3 Signal Engine（信号引擎）

- 接收策略产生的 TradingIntent，做规范化：格式校验、字段完整性、方向/数量合法性；
- 去重与限频：同一 bar 重复意图取最新有效值；每策略/每标的单位时间意图上限；
- 意图有效期检查：过期意图作废（防止「旧信号在行情恢复后突然下单」）；
- 意图绑定策略版本与部署 ID，旧版本下线后其未处理意图作废；
- 输出标准化意图交给 Portfolio Management。

#### 2.4.4 Risk Engine（风控引擎）

系统核心之一，详细设计见第 8 章。要点：

- 四级规则：账户级 / 策略级 / 订单级 / 市场级；
- 强制同步检查：所有交易意图必须先过 `pre_trade_check`，拒绝不可绕过；
- 风控状态（当日盈亏、触发计数、冷却、持仓集中度）内存 + 异步持久化，重启恢复后才允许交易；
- 全局 Kill Switch 与策略级冻结；
- 每个规则版本化，规则变更审计。

#### 2.4.5 Execution Engine（执行引擎）

- 职责：把风控通过的意图转成订单 → 路由 → 发出 → 跟踪 → 成交处理 → 撤单/重试；
- 幂等：订单唯一 client_order_id；超时先查询后重发，杜绝重复下单；
- 订单状态机全覆盖：`NEW → PENDING_SUBMIT → PENDING_ACK → WORKING → PARTIALLY_FILLED / FILLED / CANCELED / REJECTED / EXPIRED`；
- 订单类型：市价、限价（MVP）；TWAP/VWAP 拆分（后期可选）；
- 异常降级：拒单 → 记录并告警，按策略策略回退（撤单/暂停该策略）；
- 成交回报处理：`ExecutionReport → Trade → Position/Account 更新`。

#### 2.4.6 Portfolio Manager（多策略组合管理）

**定位（已确认）**：系统采用多策略组合运行模式，Portfolio Manager 是一等模块，负责把「多个策略的意图」翻译并合并为「账户级可执行的动作」，同时管理资金分配与组合风险暴露。

在交易链路中的位置：

```
Strategy A ──意图──┐
Strategy B ──意图──┼──► Portfolio Manager ──标准化订单请求──► Risk Engine ──► Execution Engine
Strategy C ──意图──┘           │
                               ├─ 意图→订单换算（精度/最小量/保证金/余额）
                               ├─ 资金分配与策略权重管理
                               ├─ 仓位合并（同标同向合并、反向对冲/抵消）
                               └─ 组合敞口控制（按币种/方向/策略聚合）
```

职责：

- **账户与持仓管理**：账户（模拟/实盘、现货/合约子账户、币种余额）、持仓、已实现/未实现盈亏、保证金与杠杆状态；
- **意图 → 订单换算**：把策略意图转化为可执行订单的尺寸（对齐交易所精度、最小下单量、名义金额约束、可用余额/保证金约束）——意图是「想要的」，订单是「能做的」；
- **资金分配与策略权重**：维护每个策略的资金分配（StrategyAllocation：权重/额度/币种），新意图按分配额度校验；MVP 用静态权重与额度，不做组合优化算法；
- **仓位合并**：多个策略对同一标的方向相同 → 合并为一笔订单并共享持仓；方向相反 → 按配置允许对冲/抵消（趋势与套利策略可能同标反向，需显式语义）；
- **风险暴露控制**：按币种/方向/策略维度聚合敞口，输出 PortfolioRisk 快照；与风控引擎配合（组合级规则可落在 Risk Engine 账户级，或由 Portfolio Manager 预检后交风控终审）；
- **多视图**：按策略看持仓、按账户看持仓、按币种看敞口；
- **扩展点（已确认暂不实现）**：复杂组合优化（均值-方差、风险平价等）未来可基于 StrategyAllocation 与 PortfolioRisk 数据模型扩展，不需要改动交易链路；
- **快照机制**：周期性账户/持仓/组合风险快照，用于对账与事后回放。

#### 2.4.7 Monitoring（监控）

- 系统状态：进程存活、行情流健康（延迟/断连次数）、任务队列积压、磁盘/内存；
- 策略状态：运行中/暂停/崩溃/风控冻结、信号频率、意图被拒次数；
- 交易状态：在途订单、卡单、拒单率、成交价偏差、持仓偏离、资金费率收支累计；
- 告警通道（个人）：日志规则 + 桌面通知/Telegram 预留端口；
- 对账：每日（或按需）本地状态 vs 交易所快照，差异告警并生成对账报告。

### 2.5 Broker 层（Broker Layer）

详见第 9 章。MVP 优先实现 Paper Broker，实盘 Exchange Broker 接口先行定义、实现后置。

### 2.6 公共设施（Cross-cutting）

- 事件总线：进程内事件总线，支持同步直连（低延迟路径）与异步队列（解耦路径）；事件带 event_id 幂等、schema 版本；实盘事件异步落盘供重放；
- **高优先级事件通道**：新闻事件、风险模式变更等防御性事件走同步直连通道，可打断普通处理流程（优先级高于行情/信号类事件）；
- 结构化日志：JSON lines，统一字段（时间、模块、策略版本、订单/信号 ID），敏感字段脱敏；
- 指标：回测耗时、信号→订单延迟、风控拦截次数、拒单率、持仓偏离度；
- 审计日志：所有状态迁移、风控触发、人工干预、规则变更、Agent 任务。

### 2.7 事件驱动多 Agent 架构（News / Market / Coordinator）

> 本节定义系统未来接入 LLM 后的整体形态。当前阶段（M0~M6）不接 LLM，但架构、事件 schema、端口与规则版实现现在就落地，保证 M7 只是「替换实现」而非「重新设计」。

#### 2.7.1 设计原则

1. **规则优先，LLM 增强**：确定性风险（交易所状态、费率变化）由规则处理；LLM 只负责非结构化信息理解（新闻语义、市场解释）。规则负责执行，LLM 负责理解，两者不互相替代；
2. **事件驱动激活**：Agent 不永久在线对话；每次运行 = 明确输入工件 + 明确输出工件，由调度器（定时）或事件（重大事件触发）确定性唤醒；
3. **结构化输出**：所有 Agent 输出必须是固定 schema 的工件（结构化事件/意图），禁止输出自然语言给下游；
4. **分层权限**：News Agent 拥有**响应优先级**（高优先级事件通道、可打断普通流程、可触发风险模式切换），但**没有交易权**（不能下单、不能改风控参数、不能绕过策略系统）；Coordinator 拥有**战略层意图权**（目标敞口/风险姿态/策略偏好），但**不直接下订单**，资金动作由 Portfolio Manager 计算、Risk Engine 裁决；
5. **可审计**：每个 Agent 决策必须附带理由引用（来源事件/状态工件），输入输出全部留痕。

#### 2.7.2 三个 Agent 的角色定义

| Agent | 输入 | 输出 | 权限边界 |
| --- | --- | --- | --- |
| News Agent（新闻情报） | 分层新闻源（官方公告 / RSS / 未来社媒） | `NewsEvent`（结构化市场事件） | 高优先级事件通道；可触发风险模式切换；**无交易权、无参数修改权** |
| Market Agent（市场状态） | 行情/特征/衍生品数据 | `MarketStateReport`（趋势/波动/流动性/交易环境） | 只读数据；输出市场状态工件 |
| Coordinator Agent（战略决策，类基金经理） | NewsEvent + MarketStateReport + 当前持仓/风险状态 + 策略运行结果 | `PortfolioIntent`（目标风险姿态/目标敞口/策略偏好） | 战略层意图；**不能直接下订单**，不能跳过 Portfolio Manager 与 Risk Engine |

三者协作关系：

```
News Agent ──NewsEvent──┐
                        ├──► Coordinator Agent ──PortfolioIntent──► Portfolio Manager
Market Agent ──MarketState──┘            （战略层）                      （资产层：仓位计划）
                                                                    │
                                                                    ▼
                                                              Risk Engine（最终裁决）
                                                                    │
                                                                    ▼
                                                              Execution Engine（订单层）
```

#### 2.7.3 新闻管线（News Pipeline）

```
新闻源（分层）
    Binance/OKX 官方公告 │ RSS（CoinDesk/Cointelegraph/The Block） │ 社媒（后期）
        │
        ▼
News Collector（轮询/监听，官方源 30~60s 一档，RSS 60~120s）
        │
        ▼
Raw News Storage（原始新闻落盘，不可变，含来源/时间/原文）
        │
        ▼
News Processing（规则抽取：类型分类、资产映射、关键词命中）
        │
        ▼
News Agent（LLM 版：理解非结构化文本，输出结构化事件；规则版为占位实现）
        │
        ▼
NewsEvent（固定 schema，见 2.7.4）
        │
        ▼
事件总线（高优先级通道）→ Risk Mode Controller / Coordinator
```

数据源分层策略：

| 层 | 来源 | 特点 | 用途 | 接入阶段 |
| --- | --- | --- | --- | --- |
| L1 官方源 | Binance 公告/状态页、OKX 公告 | 慢但可靠 | 交易所风险、规则变化、上下架、维护 | M5/M6（规则版） |
| L2 新闻聚合 | CoinDesk、Cointelegraph、The Block（RSS/API） | 较快，需 source+timestamp+url | 市场情绪、宏观事件 | M6（规则版）/ M7（LLM 版） |
| L3 社交媒体 | X/Twitter、Telegram、Reddit | 最快但噪声最大 | 舆情（须多源交叉验证） | M7 后期 |

#### 2.7.4 NewsEvent 结构化事件（固定 schema）

```json
{
  "id": "event_001",
  "schema_version": 1,
  "type": "exchange_hack",
  "state": "candidate",
  "assets": ["BTC", "ETH"],
  "direction": "negative",
  "impact": {"severity": 8, "duration_hours": 24},
  "confidence": 0.85,
  "published_at": "2026-08-03T10:00:00Z",
  "detected_at": "2026-08-03T10:00:31Z",
  "source_refs": [
    {"name": "Binance Announcement", "url": "https://...", "time": "2026-08-03T10:00:00Z"}
  ],
  "raw": "原文标题/摘要"
}
```

关键语义：

- `state`：`candidate → confirmed → retracted`；**撤回是一等公民**——事件被证伪后必须能回滚风险模式；
- `type` 使用受控词表（`exchange_status / exchange_hack / regulatory_risk / listing_delisting / maintenance / market_news / macro_event` 等），禁止自由文本；
- `impact.severity` 1~10，`duration_hours` 为影响窗口（事件过期机制）；
- `confidence` 为**合成置信度**：分层先验（官方源 0.9 起、RSS 0.6 起、社媒 0.3 起）+ 多源一致性加成 + LLM 抽取置信度，不是 LLM 直接报数；
- `published_at`（源发布时间）与 `detected_at`（系统检测时间）分离，用于评估管线延迟；
- `source_refs` 为数组（多源交叉验证），`raw` 保留原文供审计与抽取质量评估。

#### 2.7.5 风险模式切换（确定性入口 + 滞回出口）

- 事件 → 风险模式映射用**静态规则表**（配置）：`event_type + severity + confidence` → `NORMAL / CAUTION / DEFENSIVE / HALT` 及对应动作（禁止新仓、降杠杆等）；LLM 只产出事件，不写参数；
- 状态机带**滞回**（防抖）：事件撤回后不立即回 NORMAL，需满足「撤回 + 波动率回落 + 观察窗口」后逐级恢复；`HALT` 只允许人工恢复；
- 风险模式状态落库并广播 `RISK_MODE_CHANGED` 事件；详细状态机见 8.5 节。

#### 2.7.6 Agent 激活模型（非永久在线）

| 触发方式 | 适用 Agent | 说明 |
| --- | --- | --- |
| 定时调度 | Market Agent | 每 5m 或每根 bar 计算市场状态（规则内核，未来 LLM 增强） |
| 事件触发 | News Agent | 新新闻到达即处理（官方源轮询/订阅推送） |
| 事件触发（阈值规则判定） | Coordinator | NewsEvent severity ≥ 阈值、市场 regime 变化、组合风控状态变化——**由规则判定"是否重大"，不让 LLM 自唤醒** |

#### 2.7.7 Coordinator 输出（PortfolioIntent）与边界

Coordinator 输出「战略层投资意图」，走同一条交易链路，不绕过任何关卡：

```json
{
  "intent_id": "coord_001",
  "source": "coordinator",
  "market_regime": {"trend": "bearish", "volatility": "high", "volume": "abnormal"},
  "portfolio_action": {
    "target_exposure": 0.3,
    "reduce_risk": true,
    "valid_until": "2026-08-04T10:00:00Z"
  },
  "strategy_preference": [
    {"strategy_id": "trend_breakout", "action": "KEEP"},
    {"strategy_id": "mean_reversion", "action": "DISABLE"}
  ],
  "reason_refs": ["event_001", "market_state_20260803T1000"]
}
```

边界规则：

- **层间职责**：Coordinator 定"姿态"（目标敞口、风险等级、策略偏好）；Portfolio Manager 定"动作"（当前持仓 → 目标权重的仓位调整计划，校验风险预算/换手/资金分配）；Execution Engine 定"订单"（卖多少、什么价、市价/限价、是否拆单）；Risk Engine 做最终裁决；
- **`strategy_preference` 走软生效**：通过 `ctx.regime`（策略上下文注入市场状态，软信号）与 Portfolio/Risk 层的额度收紧（硬约束）落地；Coordinator 不直接调用生命周期管理器禁用策略；
- **优先级语义**：`reduce_risk=true` 的 PortfolioIntent 优先级高于普通策略意图（可打断/覆盖），但仍必须过账户级风控；冲突时由 Risk Engine 裁决；
- **有效期**：`portfolio_action.valid_until` 必填，过期后姿态失效需重新评估，防止旧判断永久锁仓；
- **理由留痕**：`reason_refs` 引用触发事件与状态工件，供审计。

#### 2.7.8 与现有系统及路线图的映射

- 现有五个 Agent（Market Analyst / Strategy / Backtest / Optimization / Risk）保留；新增 **News Agent** 与 **Coordinator**，共七个角色；
- **规则版新闻风控提前到 M5/M6**（不依赖 LLM）：M4 实现风险模式状态机，M5 Paper 阶段提供新闻模拟器并验证「事件 → 模式 → 减仓」链路，M6 实盘准备接入官方公告/RSS 真实源；
- M7 之后：LLM News Agent **替代/增强**规则抽取（理解非结构化文本），Coordinator / Market Agent 换 LLM 实现；入口出口不变，上游零改动。

---

## 3. 核心领域模型

### 3.1 标的与市场

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| Instrument | `instrument_id, internal_symbol, asset_class(spot/perpetual), base_asset, quote_asset, exchange, contract_size, tick_size, lot_size, min_notional, maker_fee, taker_fee, funding_interval, margin_mode(isolated/cross), max_leverage, status` | 系统内部统一标的口径 |
| SymbolMapping | `instrument_id, exchange, exchange_symbol, is_primary` | 内部符号 ↔ 各交易所符号映射 |
| ExchangeMeta | `exchange_id, name, rate_limits, ws_endpoints, rest_endpoints, maintenance_schedule` | 交易所元信息与限频规则 |

### 3.2 市场数据

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| Bar | `instrument_id, open_time(UTC), interval, open, high, low, close, volume, quote_volume, taker_buy_volume, trades_count, source, quality, data_version` | 统一 OHLCV，open_time 为主键 |
| Tick | `instrument_id, ts, price, quantity, side(buy/sell), exchange_ts, received_ts` | 逐笔成交（中频回测可选） |
| OrderBookSnapshot | `instrument_id, ts, bids[], asks[], exchange_ts` | 订单簿快照（用于滑点/深度估计，可低频采样） |
| FundingRate | `instrument_id, funding_ts, rate, predicted_rate?, settlement_ts, interval` | 资金费率：历史与实时 |
| OpenInterest | `instrument_id, ts, oi_value, oi_contracts, exchange_ts` | 持仓量 |
| MarkPrice | `instrument_id, ts, mark_price, index_price, exchange_ts` | 标记价格（强平/资金费率基准） |
| FeatureSet | `instrument_id, ts, feature_name, value, feature_version, data_version` | 特征快照 |

### 3.3 策略与版本

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| Strategy | `strategy_id, name, description, style(trend/mr/arb/grid/ai), lifecycle_status, tags[], account_binding, source(man/agent), created_at, updated_at` | 策略主记录（可变） |
| StrategyVersion | `strategy_id, version_no, code_ref(git commit+path), params_schema, data_dependencies, feature_dependencies, runtime_constraints, asset_class_support(spot/perpetual), content_hash, immutable` | 不可变策略包 |
| StrategyParams | `strategy_id, version_no, params, params_hash` | 具体参数集 |
| Deployment | `deployment_id, strategy_id, version_no, params, account_id, mode(paper/production), status, started_at, stopped_at` | 一次运行部署 |

### 3.4 交易意图、订单与成交

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| TradingIntent | `intent_id, strategy_id, version_no, deployment_id, instrument_id, direction(long/short/flat), action(open/increase/reduce/close/flip), quantity 或 notional, order_type(market/limit), limit_price?, intent_priority, valid_until, reason_tags[], raw_payload` | 策略唯一输出物，见第 6 章 |
| Order | `order_id, client_order_id, account_id, deployment_id, instrument_id, side(buy/sell), order_type, quantity, filled_quantity, avg_price, limit_price?, margin_mode?, leverage?, status, timestamps[], reject_reason?` | 订单（状态机见 3.8） |
| Trade | `trade_id, order_id, instrument_id, side, quantity, price, fee, fee_currency, realized_pnl?, ts, exchange_trade_id` | 一次成交 |

### 3.5 账户与持仓

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| Account | `account_id, mode(paper/production), account_type(spot/perpetual/sub), exchange_ref, balances[{asset, free, locked, equity}], total_equity, status` | 资金账户（可含子账户） |
| Position | `position_id, account_id, instrument_id, side(long/short), quantity, entry_price, avg_cost, mark_price, unrealized_pnl, realized_pnl, leverage, margin_mode, liquidation_price, maintenance_margin, updated_ts` | 持仓（永续含强平相关信息） |
| Snapshot | `account/position 全量 + snapshot_ts` | 周期快照，用于对账与回放 |
| StrategyAllocation | `allocation_id, account_id, strategy_id, weight, capital_limit, currency, enabled, updated_at` | 策略资金分配与权重（组合管理，MVP 静态，未来可扩展优化算法） |
| PortfolioRisk | `account_id, snapshot_ts, total_notional, exposure_by_symbol, exposure_by_strategy, net_exposure_by_symbol, concentration, combined_leverage` | 组合风险快照（按币种/策略聚合敞口） |

### 3.6 实验与评估

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| BacktestRun | `run_id, strategy_id, version_no, params, data_range, data_version, feature_version, engine_config, environment_lock, trades_ref, equity_curve_ref, metrics, immutable` | 一次回测 |
| OptimizationRun | `run_id, strategy_id, version_no, search_space, objective, algorithm, evaluations[], best_params, best_metrics, walkforward_report` | 一次参数搜索 |
| ReportCard | `metrics(见下) + 结论 + gate_results` | 标准化评估卡片 |
| Metrics | `total_return, annualized_return, max_drawdown, sharpe, win_rate, volatility, trade_count, profit_factor, calmar, sortino, avg_holding_time, turnover, max_consecutive_losses, funding_pnl(永续), liquidation_count(永续)` | 必含：收益率、最大回撤、夏普、胜率、波动率、交易次数 |

### 3.7 风控与审计

| 模型 | 关键字段 | 说明 |
| --- | --- | --- |
| RiskRule | `rule_id, level(account/strategy/order/market), type, params, enabled, version` | 风控规则定义 |
| RiskCheckResult | `check_id, intent_id/order_id, rule_results[], verdict(allow/block/modify), ts` | 每次风控检查记录 |
| RiskEvent | `event_id, type(breach/cooldown/freeze/killswitch), severity, context, triggered_at, action_taken` | 风控触发事件 |
| AuditLog | `log_id, actor(man/agent/system), action, target_type, target_id, before, after, ts, reason` | 全系统审计 |

### 3.8 订单状态机

```
              ┌────────┐
              │  NEW   │
              └───┬────┘
                  ▼
         ┌────────────────┐
         │ PENDING_SUBMIT │──超时/本地校验失败→ REJECTED
         └───────┬────────┘
                 ▼
         ┌────────────────┐
         │  PENDING_ACK   │──交易所拒单────→ REJECTED
         └───────┬────────┘
                 ▼
         ┌────────────────┐
         │    WORKING     │──部分成交──→ PARTIALLY_FILLED(回到 WORKING)
         └───────┬────────┘
                 │
    ┌────────────┴────────────┐
完全成交                    撤单/超时
    │                          │
┌───▼────┐              ┌──────▼──────┐
│ FILLED │              │  CANCELED   │──→ EXPIRED(超时未成交)
└────────┘              └─────────────┘
```

任何迁移产生 OrderUpdate 事件；全部状态、时间戳、原因落 Event Log。

---

## 4. 数据模型设计

### 4.1 存储选型与理由

| 用途 | 选型 | 理由 |
| --- | --- | --- |
| 历史行情（Raw/Normalized/Feature） | Parquet 文件 + DuckDB 查询 | 列存压缩、扫描快；单机百 GB 级无压力；免运维 |
| 元数据/实验/策略注册表 | SQLite | 事务一致、单文件备份、个人场景足够 |
| 实盘运行状态 | SQLite + 内存缓存 | 热路径不碰磁盘；崩溃后从落库恢复 |
| 配置 | YAML + 环境变量 | 人类可读、分层覆盖 |

### 4.2 Raw 层

- 按 `source/exchange / instrument / data_type(kline/tick/funding/oi/orderbook) / date` 分区；
- 字段保留交易所原始口径（原始符号、原始时间格式），不做业务转换；
- 只增不改；采集时记录采集时间与批次 ID。

### 4.3 Normalized 层（统一口径）

#### K 线表（示例字段）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instrument_id | string | 内部统一标的 ID |
| open_time | timestamp(UTC) | K 线开盘时间（主键之一） |
| interval | string | 1m/5m/15m/1h |
| open / high / low / close | decimal | 价格 |
| volume | decimal | 成交量（base） |
| quote_volume | decimal | 成交额（quote） |
| taker_buy_volume | decimal | 主动买量 |
| trades_count | int | 成交笔数 |
| data_version | string | 数据批次版本 |
| quality | string | ok / suspect |

#### 衍生品数据表

- FundingRate：`instrument_id, funding_ts, rate, predicted_rate, settlement_ts`；
- OpenInterest：`instrument_id, ts, oi_value, oi_contracts`；
- MarkPrice：`instrument_id, ts, mark_price, index_price`；
- OrderBookSnapshot（低频采样，如每 1m/5m 一次）：`instrument_id, ts, depth_levels, bid_prices[], bid_sizes[], ask_prices[], ask_sizes[]`（用于回测滑点/深度建模）。

### 4.4 Feature Store

- 宽表存储（`instrument_id, ts, feature1..featureN, feature_version`）适合批量回测读取；
- 特征注册表：`feature_name, compute_version, input_window, params, description`；
- 每个特征批次记录 `data_version + feature_version`，保证可复现；
- 增量计算：以 `(instrument_id, feature_name, 最新已算时间点)` 为断点。

### 4.5 Meta 层（SQLite）

- 表：`instruments, symbol_mappings, exchange_meta, strategies, strategy_versions, strategy_params, strategy_allocations, portfolio_risk_snapshots, deployments, experiments(backtest/optimization), report_cards, risk_rules, risk_events, audit_log, tasks, artifacts`；
- 外键约束：实验 → 策略版本 → 参数集；部署 → 策略版本 + 账户；
- 所有历史记录只允许 INSERT 与软删除，不允许 UPDATE 覆盖。

### 4.6 数据管线

```
交易所 REST/WS
   │ Market Adapter
   ▼
Raw（原始快照，按批次落盘）
   │ 清洗/校验/归一化（统一符号、时区、精度、复权口径）
   ▼
Normalized（Parquet，按 instrument+interval 组织）
   │ Feature Pipeline（版本化、无未来函数、增量）
   ▼
Feature Store
   │
   ├──► 研究系统（回测/优化/实验）
   └──► 交易系统（实盘特征，与回测同管线同实现）
```

数据版本策略：任一上游变更（源、清洗规则、特征定义）都必须产生新版本并触发「受影响实验重跑」通知，不允许静默替换。

---

## 5. 策略生命周期设计

### 5.1 状态机

```
┌───────┐  创建(脚手架/导入) ┌────────────┐  提交回测 ┌───────────┐
│ Draft │──────────────────►│ Backtesting│──────────►│ Validated │
└───┬───┘                   └────────────┘           └─────┬─────┘
    │ 代码/参数变更 = 新版本                                  │ 满足 Paper 门禁
    ▼                                                        ▼
┌─────────┐   放弃       ┌────────────┐  观察期满  ┌────────────┐
│ Archived│◄────────────│ Deprecated │◄───────────│ PaperTrading│
└─────────┘             └─────┬──────┘            └─────┬──────┘
        ▲                    │ 退役                     │ 满足 Production 门禁
        │                    │                          ▼
        │              ┌─────┴──────┐            ┌────────────┐
        │              │  Paused    │◄───────────│ Production │
        └──────────────│  Disabled  │            └──────┬─────┘
                        └────────────┘                   │ 异常/风控/连续亏损/人工
                                                          ▼
                                                    (Paused 或 Deprecated)
```

### 5.2 状态定义

| 状态 | 含义 | 可做什么 |
| --- | --- | --- |
| Draft | 草稿 | 本地回测、调试 |
| Backtesting | 回测/优化中 | 全量回测、参数搜索 |
| Validated | 验证通过 | 进入 Paper Trading |
| Paper Trading | 模拟盘 | 连接 Paper Broker 验证运行时与风控 |
| Production | 实盘 | 连接实盘 Broker，全程风控约束 |
| Paused | 暂停 | 冻结信号；在途订单保留或撤单（可配置） |
| Disabled | 禁用 | 强制停止（风控触发/故障），需人工诊断后恢复 |
| Deprecated | 退役 | 只读，保留历史 |
| Archived | 归档 | 元数据保留，代码可清理 |

### 5.3 迁移门禁

| 迁移 | 前置条件 | 证据 |
| --- | --- | --- |
| Draft → Backtesting | 静态检查通过、参数 schema 校验、冒烟回测无异常 | 静态检查报告 + 冒烟结果 |
| Backtesting → Validated | 事件驱动精测完成；样本外指标达标；最小交易次数达标；无未来函数告警；数据/特征版本齐全；结果可重跑 | ReportCard + 重跑验证 |
| Validated → Paper Trading | 绑定模拟账户；风控规则配置齐全；资金分配约束满足 | 风控配置快照 + 部署记录 |
| Paper Trading → Production | 模拟运行 ≥ 配置天数；无风控事故；回测-模拟偏差在容忍范围；人工确认 | Paper 运行报告 + 人工审批 |
| Production → Paused/Deprecated | 人工触发 / 风控熔断 / 连续亏损超阈值 / 策略失效（如资金费率结构变化、市场规则变化） | RiskEvent / 审计记录 |

设计要点：

- 状态结论永远绑定「版本 + 数据版本 + 特征版本」；
- AI 生成策略默认 Draft，与人工策略走同一门禁；
- 升级到 Production 的最终节点固定为人工确认（未来 AI 只能提交建议工件，不能替代门禁）；
- 退役策略可被 Fork 出新版本重新研究，旧记录保留（未来 AI 分析历史策略的语料）。

---

## 6. 交易流程设计

### 6.1 交易意图模型（核心设计）

策略不产生订单，只产生意图（Trading Intent）：

```
{
  "intent_id": "...",
  "strategy_id": "...",
  "deployment_id": "...",
  "instrument": {"internal_symbol": "BTC-USDT-PERP", "exchange": "binance"},
  "direction": "long" | "short" | "flat",        // 目标方向
  "action": "open" | "increase" | "reduce" | "close" | "flip",
  "quantity": 0.5,                                 // 或 notional（名义金额）
  "order_type": "market" | "limit",
  "limit_price": 65000,                            // limit 时必填
  "intent_priority": "normal",
  "valid_until": "2026-08-02T12:05:00Z",           // 过期作废
  "reason_tags": ["trend_breakout", "risk_reduce"],
  "raw_payload": "..."                             // 策略原始输出，审计用
}
```

设计意图：

- **意图是「想要的」，订单是「能做的」**：数量换算、精度对齐、最小下单量、保证金/余额校验都发生在 Portfolio Management 与风控，策略作者不需要也不允许触碰交易所细节；
- 意图字段白名单化，Signal Engine 拒绝未知字段（防止策略夹带私货）；
- `valid_until` 防止陈旧意图在行情恢复后执行；
- `reason_tags` 让风控与监控能按意图语义做细粒度规则（如「reduce 类意图永不拦截」例外需要显式配置）。

### 6.2 交易全流程时序

```
① 行情到达 Market Data Gateway（WebSocket）
② 事件总线广播 Bar/Tick/Funding/OI 到策略运行时 + 风控（同一事件流）
③ 策略运行时 on_bar/on_funding 计算，产出 TradingIntent
④ Signal Engine 校验/去重/限频 → 标准化意图
⑤ Portfolio Manager 检查：
     · 目标持仓 vs 当前持仓 → 计算实际应下单的方向与数量
     · 仓位合并：多策略同标同向合并、反向对冲/抵消（按配置）
     · 精度/最小量/名义金额对齐
     · 账户余额/可用保证金约束（现货余额 vs 合约保证金）
     · 策略权重/资金分配额度校验
     · 多策略叠加后的组合敞口检查
⑥ Risk Engine pre_trade_check：
     · 账户级 → 策略级 → 订单级 → 市场级逐项检查
     · 任一规则阻断 → 记录 RiskCheckResult + RiskEvent → 流程终止
⑦ Execution Engine 生成 Order（client_order_id）→ Broker Adapter 发出
⑧ Broker/交易所返回 ACK / 成交回报 ExecutionReport
⑨ Trade 落库 → Position/Account 更新 → 事件广播
⑩ Monitoring 指标更新；事件异步落盘（Event Log）
⑪ 每日/周期对账：本地状态 vs 交易所快照
```

### 6.3 永续合约特殊流程

- 保证金模式：支持逐仓（Isolated）与全仓（Cross）的建模，策略绑定账户时声明；
- 杠杆：每个意图携带（或账户级默认）杠杆约束，风控按「名义价值 / 权益」校验；
- 资金费率：策略需要感知当前/预测资金费率（on_funding 回调）；实盘按结算时点实际收支，回测按历史结算序列模拟；
- 强平：实盘由交易所执行；系统内必须能预测强平价并设置「接近强平自动减仓」保护（风控规则）；
- 标记价格：未实现盈亏、强平判断、风控回撤计算统一用 mark price，避免最新价插针误触发。

### 6.4 异常处理

| 异常 | 处理 |
| --- | --- |
| 下单超时 | 先查询订单状态再决定重发，禁止盲目重发 |
| 拒单 | 记录原因，告警，按策略级策略回退（撤单/暂停该策略） |
| WS 断线 | 重连 + 数据空洞策略：暂停开仓类信号，只允许减仓/平仓类意图（可配置） |
| 行情延迟超阈值 | 风控触发市场级规则，冻结开仓 |
| 执行引擎崩溃 | 重启后从持久化状态恢复，恢复前不自动交易 |
| 成交价偏差超阈值 | 记录 + 告警 + 触发对账 |

### 6.5 延迟预算（个人单机参考）

- 行情接收 → 策略回调：< 50ms（本地 WS 到本地进程）；
- 信号 → 风控通过 → 订单发出：< 10ms（全内存关键路径）；
- 订单 ACK → 成交回报处理：取决于交易所，系统内处理 < 5ms；
- 关键路径禁止数据库访问；数据库写入全部异步。

---

## 7. 回测系统设计

### 7.1 双引擎

| 维度 | 向量化引擎 | 事件驱动引擎 |
| --- | --- | --- |
| 用途 | 参数粗筛、因子验证、空间探索 | 候选策略精测、上线前验证 |
| 速度 | 极快（万级参数） | 较慢（贴近实盘） |
| 撮合 | 按 bar 收盘价 / 下一 bar 开盘价（简化） | 逐事件撮合，支持部分成交、排队假设 |
| 成本模型 | 简化（统一费率+固定滑点） | 完整（见 7.3） |
| 输出 | 摘要指标 | 完整逐笔交易 + 权益曲线 + 明细 |

流程约定：`向量化粗筛 → 事件驱动精测 → walk-forward 稳定性验证 → OOS 终评`，两层都通过才可进入 Validated。

频率分层（已确认）：

- 默认研究频率：**5m / 15m / 1h**（符合中频定位，数据量可控、回测快、适合策略快速迭代）；
- **1m 数据**：不作为默认全量回测，仅用于「候选策略精细验证、滑点测试、成交模拟优化」；
- 更高频数据：后续按性能需求扩展，只涉及数据层与引擎的配置扩展，不改变架构设计。

### 7.2 撮合与订单执行模型

- 撮合时点可配置：`bar 收盘 / 下一 bar 开盘 / 下下一 bar（信号滞后假设）/ tick 级`；
- 默认基准（上线前）：**下一 bar 开盘 + 保守滑点**，杜绝「用未来 bar 成交」的幻觉；
- 订单类型支持：市价、限价（可挂单/不成交）；限价未成交可标记为「悬单」或按策略策略转市价；
- 部分成交与排队：事件驱动模式支持深度片段建模（用订单簿快照近似），MVP 可先用「固定滑点 + 全成交」简化，逐步细化。

### 7.3 成本模型（加密货币完整性）

| 成本项 | 说明 |
| --- | --- |
| 手续费 | maker/taker 按费率表；可配置 VIP 等级；永续按名义价值计费 |
| 滑点 | 固定比例（可配置）或基于订单簿深度的动态滑点（快照建模） |
| 资金费率 | 按结算序列模拟：多头支付/收取、空头反向；记录累计 funding_pnl；支持「费率按结算间隔收取」的口径 |
| 杠杆与保证金 | 名义价值 = 数量 × 价格；所需保证金 = 名义 / 杠杆；逐仓/全仓两种模式 |
| 强平 | 按标记价格 + 维持保证金率计算强平价；触发即平仓（记录 liquidation_count，作为硬指标） |
| 最小下单与精度 | 交易所最小量、数量精度、价格精度、最小名义金额 |

强平模拟是永续策略回测的**必选项而非可选项**：不回测强平的策略，实盘等于裸奔。

### 7.4 数据口径与样本划分

- 回测输入 = Normalized 数据 + Feature 数据，带版本；
- 训练/验证/样本外（OOS）三段固定约定；walk-forward 滚动窗口输出参数稳定性；
- 防止未来函数的三道防线：
  1. 特征管线统一入口，禁止策略内自算原始数据；
  2. 事件驱动引擎对「截至 t」做断言校验；
  3. 复跑对比（同一实验重跑结果一致）。
- 结果记录：BacktestRun 含逐笔交易、权益曲线摘要、指标全集、成本明细（手续费/滑点/资金费率分列）、环境锁。

### 7.5 性能设计

| 手段 | 说明 |
| --- | --- |
| 向量化优先 | 粗筛用向量化，Polars/NumPy 级实现，避免事件循环开销 |
| 列存 + 分区 | Parquet 按 instrument+interval+date 分区，只读需要的时间片 |
| 并行搜索 | Optimizer 按参数分片多进程；结果增量写盘，中途可查看 |
| 缓存去重 | 同一（策略版本+参数+数据版本+特征版本）结果哈希去重 |
| 预加载 | 热数据预加载到内存；超大区间用内存映射 |
| 快速失败 | 参数/数据校验前置；单次失败不影响整个搜索批次 |

参考目标：单标的 1 年 1m 数据向量化回测秒级完成；事件驱动精测百万 bar 分钟级；千级参数搜索分钟~小时级（取决于并行度与模式）。

---

## 8. 风控设计

### 8.1 定位

风控是交易系统**不可绕过的强制关卡**：任何交易意图在执行前必须通过风控检查；风控拒绝意味着流程终止，策略无法绕过；风控规则本身版本化、可审计、可热更新（更新需人工确认并记录）。

### 8.2 四级规则体系

| 层级 | 覆盖范围 | 示例规则 |
| --- | --- | --- |
| 账户级 | 全部策略 / 全部标的 | 总名义敞口上限（按币种聚合）、总权益最大回撤熔断、单日最大亏损线、现金/保证金余额底线、组合级杠杆上限 |
| 策略级 | 单个策略 | 单策略仓位上限（按币种/按名义）、单策略日亏损上限、信号/交易频率上限、冷启动观察期、连续亏损暂停 |
| 订单级 | 单笔意图/订单 | 单笔名义金额上限、数量/价格合理性（跳变检测）、限价偏离现价最大百分比、精度与最小量校验 |
| 市场级 | 全部策略 / 当前行情 | 极端波动（价格插针、ATR 突变）、行情延迟超阈值、WS 断线、交易所维护公告、流动性不足（深度快照） |

### 8.3 检查流程

```
TradingIntent
   │
   ▼
Portfolio 预检（余额/精度/组合敞口，软约束先行）
   │
   ▼
Risk Engine: pre_trade_check(intent)
   │  ├─ 账户级规则 → 通过/阻断
   │  ├─ 策略级规则 → 通过/阻断
   │  ├─ 订单级规则 → 通过/阻断/修改（如限价调整）
   │  └─ 市场级规则 → 通过/阻断
   │
   ├─ allow → Execution Engine
   └─ block → RiskCheckResult + RiskEvent（记录原因与触发规则）
```

设计要点：

- **拒绝也要记录**：每次 block 都写 RiskEvent 并通知监控，重复被拒的策略进入「审查状态」；
- **冷却与熔断**：规则支持冷却（如单日亏损到 80% 线后限制开仓）与熔断（触发即冻结策略/全系统）；
- **强平保护**：永续持仓接近强平价时，风控强制触发减仓意图（或阻止加仓），不允许「赌强平不发生」；
- **状态持久化**：风控计数（当日盈亏、触发次数、冷却计时）内存 + 异步落库；重启后先恢复再开放交易，杜绝「重启清零绕过风控」；
- **Kill Switch**：全局一键冻结；与恢复分离（恢复需人工显式操作）；
- **审计**：规则变更、手动放行、熔断触发全部审计。

### 8.4 风控规则 schema（要点）

```
RiskRule {
  rule_id, level, type,
  params（阈值、窗口、动作：block/reduce/freeze/alarm）,
  enabled, version, updated_by, updated_at
}
```

规则变更 = 新版本；版本间可对比（未来 AI Risk Agent 分析「哪条规则拦了多少单、误伤了多少信号」时依赖此对比能力）。

### 8.5 风险模式状态机（事件驱动）

风险模式是账户级的运行姿态，由**确定性规则**驱动（新闻事件/行情异常/组合状态），LLM 不直接写参数：

```
                ┌────────────┐
  事件阈值未达   │  NORMAL    │
   ────────────►│  (常规)     │
                └─────┬──────┘
                      │ 事件 severity/confidence 达阈值
                      ▼
                ┌────────────┐
                │  CAUTION   │── 事件升级/连续触发 ──► ┌──────────┐
                │  (谨慎)    │                        │ DEFENSIVE│
                └─────┬──────┘◄───────────────────────│ (防御)   │
                      │                               └─────┬────┘
                      │ 撤回+观察窗口+波动率回落（逐级恢复） │ 极端事件/组合风险
                      ▼                                     ▼
                ┌────────────┐                          ┌────────┐
                │  NORMAL    │◄──── 恢复（需人工）────── │  HALT  │
                └────────────┘                          │ (停机) │
                                                        └────────┘
```

规则：

| 项目 | 规则 |
| --- | --- |
| 进入 | 静态映射表：`event_type + severity + confidence` → 目标模式与动作（禁止新仓、降杠杆、强减仓等）；行情异常（延迟/跳变）与组合风控状态（接近亏损线）也可触发 |
| 退出 | 事件撤回 + 波动率回落 + 观察窗口（如 2h）后逐级恢复（DEFENSIVE → CAUTION → NORMAL），防抖滞回，禁止立即回跳 |
| HALT | 只允许人工恢复（与 Kill Switch 同语义）；进程重启后从持久化状态恢复，恢复前冻结交易 |
| 记录 | 每次切换广播 `RISK_MODE_CHANGED` 事件、落 `RiskModeSnapshot`（mode/entered_at/triggered_by/reason）并写审计日志 |
| 生效 | 模式对策略与订单生效：DEFENSIVE 下开仓类意图被风控 BLOCK、杠杆上限下调；CAUTION 下仅收紧新仓 |

风险模式与四级规则的关系：模式是**账户级横切状态**，四级规则仍逐单检查；模式改变规则的生效阈值（如 DEFENSIVE 下单笔限额减半），不替换规则引擎。

---

## 9. Broker 抽象设计

### 9.1 统一接口契约

Paper Broker 与 Exchange Broker 实现**完全一致**的接口，上层系统通过配置选择，支持热切换：

| 方法 | 契约 |
| --- | --- |
| `connect() / disconnect()` | 连接管理；Paper 为本地撮合器，Exchange 为交易所 API |
| `place_order(order) → broker_ref` | 下单，幂等（同一 client_order_id 重复调用不重复下单） |
| `cancel_order(client_order_id)` | 撤单 |
| `get_order(client_order_id)` | 订单状态查询（超时恢复用） |
| `get_account()` | 账户余额查询 |
| `get_positions()` | 持仓查询 |
| `on_execution_report(callback)` | 成交回报订阅（Exchange 为 WS/轮询，Paper 为本地事件） |
| `get_funding_rates(instrument, since)` | 资金费率历史（回测与实盘共用） |
| `status()` | 连接与健康状态 |

附加契约：

- 所有方法返回不可变 DTO，字段与领域模型一致；
- 所有交易所异常（限频、网络、业务拒绝）统一包装为 BrokerException（类型化），上层按类型降级；
- 限频管理在 Exchange Adapter 层统一实现（令牌桶），Broker Adapter 不重复实现。

### 9.2 Paper Broker（MVP 优先）

- 本地撮合器：基于历史数据重放（研究用）与实时模拟（Paper Trading 用）；
- 模拟能力：
  - 手续费（maker/taker 费率可配置）；
  - 滑点（固定或基于订单簿快照）；
  - 部分成交与限价未成交（可配置）；
  - 资金费率结算（按真实历史费率或固定费率）；
  - 保证金与强平（复用回测引擎的保证金/强平模型，保证回测-模拟口径一致）；
  - 撮合延迟（可配置毫秒级，模拟网络延迟）；
- Paper 模式是回测与实盘的**中间验证层**：同一套成本模型，但运行在实时行情与真实时间轴上；
- Paper 账户与实盘账户数据隔离，不能混用。

### 9.3 Exchange Broker（实盘）

- 实现统一接口，内部依赖 Exchange Adapter（签名、限频、重连）；
- 核心保障：幂等、查询优先于重发、拒单回传结构化原因、成交回报不丢（本地事件日志 + 对账兜底）；
- 杠杆/保证金/订单类型按交易所能力映射（Broker Adapter 负责「统一意图 → 交易所参数」转换）；
- MVP 先接入 1 家（建议 Binance 现货 + 永续），跑通后再扩展 OKX / Bybit。

### 9.4 适配器接入清单与契约测试

- 每个 Broker Adapter 必须通过统一的契约测试套件（用 Paper Broker 作为基准实现）；
- 契约测试覆盖：下单/撤单/查询/回报/异常路径/幂等；
- 新增交易所 = 实现 Adapter + 跑通契约测试 + Paper 环境全链路演练，不修改上层任何模块。

---

## 10. MVP 开发路线

> 每阶段可独立交付、可验证；按依赖顺序推进。

### 10.0 优先级映射（已确认）

阶段推进遵循：Research System 闭环 → Backtest Engine → Strategy Framework → Paper Trading → Portfolio Management → Risk Engine → Live Trading（M0~M3 共同构成 Research System 闭环，与第 0.2 节一致）。

明确不提前开发：Web 控制台、多交易所接入、云部署、付费数据系统、高频交易基础设施、LLM 接入（M7 前不接 LLM；规则版新闻风控除外，它不依赖 LLM）。

里程碑编号与提示词一致（prompts/M*.md）：

| 里程碑 | 主题 | 说明 |
| --- | --- | --- |
| M0 | 基础框架 | 模型/事件/配置/数据库/端口 |
| M1 | 市场数据 | Binance 采集 → 规范化 → 特征 |
| M2 | 策略框架 | 策略 SDK 与注册表 |
| M3 | 回测系统 | 双引擎 + 成本模型 + 优化 + 实验 |
| M4 | 组合与风控 | Portfolio Manager + Risk Engine + 风险模式状态机 |
| M5 | 模拟交易 | Paper Broker + 生命周期 + 新闻模拟器 + 事件 → 风险链路 |
| M6 | 实盘交易准备 | Binance 实盘 + 对账/监控/Kill Switch + **规则版新闻风控（官方公告/RSS 真实源）** |
| M7 | AI Agent 增强 | LLM News/Market/Coordinator + 五个研究类 Agent 占位 → LLM 化 |

| 阶段 | 范围 | 里程碑/验收 |
| --- | --- | --- |
| M0 骨架与领域基础（1~2 周） | Monorepo、配置中心、结构化日志、事件总线（事件 schema 定义）、核心领域模型落地、元数据库 Schema | 事件总线可收发示例事件；模型可落库读回 |
| M1 数据管道（2~4 周） | Binance 公开 REST 历史 K 线 + WebSocket 实时行情（默认 5m/15m/1h，1m 按需）；Raw → Normalized；交易日历/维护窗口处理；数据质量报告；基础特征管线（价格/成交量/波动率/资金费率/OI） | 一条「采集 → 规范化 → 特征」链路可重复跑通且可复现 |
| M2 策略框架（2~3 周） | 策略脚手架（create 模板：trend/mean_reversion/grid）；strategy.yaml 解析与 params_schema 校验；BaseStrategy + StrategyContext（含 ctx.regime）+ emit_intent；静态检查沙箱；Strategy Registry（save_version/哈希/幂等）；demo_mean_reversion 示例策略 | 三种模板可生成并入库；静态检查拦截 5 类违规；emit_intent 校验覆盖所有拒绝分支；demo 策略入库 + 冒烟通过 |
| M3 回测系统（3~5 周） | 向量化回测引擎 + 事件驱动回测引擎（成本模型：手续费/滑点/资金费率/杠杆/保证金/强平）；Metrics + ReportCard（含成本分列/OOS）；实验存储与 rerun 确定性；Optimizer（网格/随机 → 贝叶斯）+ walk-forward + OOS（**不可关闭**）；research/experiment CLI | 回测确定性（rerun 逐位一致）；强平/资金费率用例；未来函数拦截；千级参数搜索 + 固定 seed 可复现；**3 标的 5m + demo 策略产出完整 ReportCard（研究闭环演示）** |
| M4 组合与风控（3~4 周） | 生命周期状态机 + 统计门禁（自动判定）；Paper Broker；风控引擎基础规则（四级规则 MVP 集）+ **风险模式状态机（NORMAL/CAUTION/DEFENSIVE/HALT，滞回）**；策略运行时隔离；Signal Engine；Execution Engine（Paper）；Portfolio Manager 基础（资金分配/权重/仓位合并/敞口聚合）；**多策略并行运行** | 多个验证过的策略同时进入 Paper Trading 连续运行；风控可拦截越界意图；仓位合并与敞口聚合正确；风险模式切换与滞回恢复正确 |
| M5 模拟交易（3~4 周） | 生命周期门禁与 Paper 全链路；**新闻模拟器（`qps news inject`）与「事件 → 风险模式 → 减仓」链路验证**；规则版新闻管线骨架（官方源采集 / Raw 存储 / 规则抽取 / NewsEvent）；监控与告警 | Paper 连续运行 24h 无异常；注入合成新闻事件可正确触发风险模式切换与组合减仓；撤回事件可回滚模式（滞回） |
| M6 实盘交易准备（3~6 周） | 实盘 Broker Adapter（1 家，Binance 现货+永续）；执行引擎实盘化（幂等/重试/超时/撤单）；Portfolio Manager 实盘化（多策略合并下单）；对账；监控/告警/Kill Switch；**规则版新闻风控（官方公告轮询 30~60s + RSS，NewsEvent 真实源，事件 → 模式延迟 < 1min，撤回处理）** | 小资金、多策略组合（≥2 个策略）、低频率实盘运行一周无异常且对账一致；真实公告事件可触发风险模式且延迟达标 |
| M7 AI Agent 增强（2~4 周） | Artifact Store；Agent 网关端口（含触发类型：定时/事件）；News / Market / Coordinator 三 Agent 与五个研究类 Agent 的本地占位实现 → 逐步 LLM 化（News 理解非结构化文本、Coordinator 战略意图、Market 市场解释）；策略脚手架「可机器生成」化；风控规则版本对比；性能压测 | 模拟「新闻事件 → Coordinator 综合 → PortfolioIntent → 组合调仓 → 风控裁决」全流程；Agent 产出均为不可变工件且无交易句柄 |

### MVP 总体验收标准

1. 一个策略可从生成走到 Paper Trading（统计门禁自动判定，无人工审核节点），全流程可追溯、可重跑；
2. 实盘路径有强制风控（四级规则 + 风险模式）+ 人工 Kill Switch + 对账；
3. 未接 LLM 时，所有 AI 端口已有占位实现，接口定义不依赖任何 LLM 技术；
4. 回测、Paper、实盘三方成本口径一致（手续费/滑点/资金费率/强平）。
5. 多策略组合模式下，资金分配、仓位合并、敞口聚合与控制均工作正常。
6. 规则版新闻风控可用合成与真实事件驱动风险模式切换，撤回与滞回行为正确。

---

## 11. 当前设计存在的问题和改进建议

### 11.1 数据类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| 历史数据覆盖不全 | 免费 API 深度/历史范围有限（如仅 1000 根 K 线），导致回测窗口过短 | 尽早建立「增量补数据」机制，从接入第一天就持续积累本地数据 |
| 交易所数据空洞 | 维护/故障期缺数据，实盘与回测口径不一致 | 数据空洞策略显式化（跳过/标记/暂停开仓），回测报告注明空洞区间 |
| 幸存者偏差 | 只研究当前主流币对，忽略已下架币对，结论偏乐观 | 数据采集保留「当时存在」的标的全集；实验记录标的池构成 |
| 资金费率/OI 历史缺失 | 永续策略必需的数据免费源覆盖有限 | 优先保证核心标的的 funding/OI 数据；缺失数据回测时强制标记并提示 |

### 11.2 策略科学类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| 过拟合 | 参数搜索越多，样本内越漂亮 | 强制 walk-forward + OOS 门禁；参数平原检查；目标函数含稳定性惩罚 |
| 回测-实盘落差 | 滑点、深度、费率假设不准 | 上线前保守成本模型；Paper 阶段对比回测预期；反馈闭环持续校准成本参数 |
| 资金费率拖累 | 永续策略长期持仓被费率侵蚀 | 指标必须单列 funding_pnl；策略评估加入「费率盈亏占比」 |
| 强平被低估 | 回测不模拟强平 → 实盘爆仓 | 强平模拟设为回测必选项；liquidation_count 作为上线硬指标（>0 需人工评估） |
| 策略同质化 | 多策略叠加形成隐藏的单一敞口 | 组合级敞口聚合（按币种/方向）+ 集中度规则 |

### 11.3 实盘执行类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| 重复下单 | 网络超时重试导致双单 | client_order_id 幂等 + 查询优先 |
| 卡单/回报丢失 | 订单状态未知、WS 回报断档 | 订单超时巡检 + 主动查询 + 对账兜底 |
| 风控状态丢失 | 进程崩溃后计数清零 | 风控状态异步持久化 + 启动恢复后冻结交易 |
| 行情时钟/乱序 | 本地时间与交易所时间错位 | 事件带 exchange_ts + received_ts，统一按 exchange_ts 处理 |
| 合约插针 | 最新价瞬时异常触发错误信号/风控 | 未实现盈亏与风控统一用 mark price |

### 11.4 工程类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| 单机单点 | 断电/磁盘故障丢失运行状态 | 状态库定期备份；事件日志双副本；启动恢复流程演练 |
| 单体膨胀 | 回测内存峰值影响实盘 | M3 起回测/优化 worker 独立进程；实盘关键路径与离线任务物理隔离 |
| 环境漂移 | 依赖升级导致回测结果变化 | 环境锁文件随实验记录；升级后重跑基线实验 |
| 适配器兼容性 | 交易所 API 变更/限频变化 | 契约测试套件 + 适配器单点隔离；接口变更灰度验证 |

### 11.5 安全类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| 策略代码执行风险 | AI 生成的代码可能有恶意/错误行为 | 运行前静态检查（import 白名单、禁网络/文件/线程）；先在 Paper 全流程验证 |
| 密钥泄露 | API Key 进入日志/配置文件 | 密钥只走环境变量/密钥环；日志脱敏；实验记录禁存敏感字段 |
| 误操作 | 人工干预误平仓/误撤单 | 干预二次确认 + 审计；Kill Switch 与恢复分离 |

### 11.6 AI 接入类风险

| 风险 | 说明 | 缓解建议 |
| --- | --- | --- |
| AI 建议不可审计 | 来源与依据不可追溯 | Agent 任务即工件（输入/输出/版本全留痕）；AI 产出默认 Draft |
| AI 生成代码质量参差 | 直接进回测浪费计算 | 先静态检查 + 冒烟回测（小样本）再进正式流程 |
| 门禁自动化稀释 | 「人工确认」形同虚设 | 人工节点最小集固定（升级 Production 必须人工）；门禁证据链完整 |
| 新闻幻觉/假新闻 | LLM 或规则误判事件，触发错误风险模式 | 分层置信度合成 + 多源交叉验证 + `state=retracted` 撤回机制；高风险动作（HALT）只人工恢复 |
| 风险模式抖动 | 真假事件交替导致模式反复切换 | 滞回状态机（撤回 + 观察窗口 + 波动率回落）+ 逐级恢复，禁止立即回跳 |
| Agent 永久在线 | 对话式多 Agent 常驻成本高、状态混乱 | 事件驱动激活 + 无共享对话状态 + 工件化输入输出 |

### 11.7 评审总结与改进建议

1. **把「实验/工件」当一等公民**：所有可分析对象（回测、优化、风控事件、实盘反馈）都是不可变、带版本、带 schema 的记录。这是可复现的根基，也是未来 AI 唯一可靠的输入。落地时不要为了省事跳过。
2. **先建风控再碰实盘**：M4 模拟阶段把风控、审计、Kill Switch 全部建完，M5 只是切换 Broker 适配器，不是「边实盘边补风控」。
3. **口径一致性是生命线**：回测、Paper、实盘三方的成本模型（手续费/滑点/资金费率/强平）必须同一套实现；任何偏差都要有显式的对账与解释流程。
4. **交易链路保持短**：实盘路径（行情 → 意图 → 风控 → 执行）设计为直线，分析/报告/存储全部挂在旁边异步执行，不进关键路径。
5. **为退役设计一等流程**：持续亏损、费率结构变化、市场规则变化都是常态；退役决策要有数据依据（运行反馈 vs 回测预期），并保留历史供 AI 分析。
6. **AI 的边界提前画死**：AI 不持有交易句柄、不直接下单、不绕过风控；AI 产出只能落到 Draft/建议工件。这条边界要在代码结构与接口契约层面强制（而不是靠约定）。

---

## 12. 附录

### 12.1 技术栈建议（个人单机）

| 组件 | 选型 | 理由 |
| --- | --- | --- |
| 语言 | Python 3.12+ | 研究生态 + AI 生态最顺；性能热点用扩展解决 |
| 数据处理 | Polars + NumPy + Numba | 向量化回测与特征计算；Polars 惰性计算适合大数据 |
| 回测/优化 | 自研双引擎（见第 7 章）+ 多进程并行 | 可完全控制成本模型与数据口径 |
| 存储 | Parquet + DuckDB + SQLite | 列存扫描快、单机免运维、事务一致 |
| 实时 | asyncio + websockets | 单进程高并发连接，中频足够 |
| 配置/校验 | YAML + Pydantic | 分层配置 + schema 校验（模型、意图、规则） |
| 测试 | pytest（含契约测试套件） | 适配器/引擎核心必须有测试保障 |
| 版本管理 | Git（monorepo，策略代码与系统同仓分目录） | 策略版本 = git commit + 路径，天然不可变 |
| 未来热点 | Rust/Cython 扩展（可选） | 回测热循环或行情解码不达标时定向优化，不改变架构 |

### 12.2 术语表

| 术语 | 含义 |
| --- | --- |
| Trading Intent | 交易意图：策略输出、风控/执行前的标准化请求 |
| Paper Broker | 模拟撮合与账户系统，用于 Paper Trading |
| Exchange Adapter | 交易所连接层：签名、限频、重连 |
| Market Adapter | 行情接入层：K 线/Tick/Funding/OI/深度 |
| Broker Adapter | 交易与账户抽象，统一 Paper/实盘接口 |
| OOS | Out-of-Sample 样本外数据，用于最终验证 |
| Walk-Forward | 滚动时间窗口的训练/验证方法，用于参数稳定性 |
| Funding Rate | 永续合约资金费率，多头与空头之间定期结算 |
| Mark Price | 标记价格，用于未实现盈亏与强平判断 |
| OI | Open Interest 持仓量 |

### 12.3 需求确认记录

| 决策项 | 结论 | 对应章节 |
| --- | --- | --- |
| 交易所 | Binance 先行（Paper Broker → Binance Adapter → OKX / Bybit）；全部经 Adapter 接入，核心系统不依赖具体交易所 | 1.3 / 9 / M1 / M6 |
| 历史数据 | 免费公开 API，不购买付费数据；Data Adapter 端口预留 | 2.2.1 / 4.6 |
| 回测频率 | 默认 5m / 15m / 1h；1m 仅用于候选精测、滑点测试、成交模拟 | 7.1 |
| 控制台 | MVP 只做 CLI（任务、回测、策略管理、实验结果、交易状态），Web 延期 | 2.1.1 / M7 |
| 部署 | 个人本机量化工作站；预留迁移小型服务器的可能（目录与配置可移植） | 0.3 / 12.1 |
| 运行模式 | 多策略组合，Portfolio Manager 为一等模块；暂不做组合优化算法 | 2.4.6 / 3.5 |
| 新闻风控 | 规则版提前至 M5/M6（官方公告 + RSS，无 LLM）；LLM 版 M7 增强/替代 | 2.7 / 8.5 / M5 / M6 |
| Coordinator 边界 | 战略层 + 组合层意图（目标敞口/风险姿态/策略偏好），不直接下订单；Portfolio Manager 负责资金动作 | 2.7.7 / 6 |
| Agent 激活 | 事件驱动（定时 + 事件触发），不永久在线；唤醒用规则判定，LLM 不自我唤醒 | 2.7.6 |
| 风险模式 | 账户级运行姿态 NORMAL/CAUTION/DEFENSIVE/HALT，确定性入口 + 滞回出口，HALT 只人工 | 8.5 |

其余开放问题（不阻塞开发，可后续再定）：策略初始资金分配方式（等权 / 人工指定 / 按回测绩效）、CLI 命令集完整清单、回测引擎性能基准。

---

*本文档为 v0.4 架构设计，需求确认项已固化（见第 0 章与 12.3 节）。面向代码开发的实现规格见 [docs/README.md](docs/README.md) 及 docs/01~07 各文档（工程规范、领域模型与数据 Schema、接口契约、策略 SDK、事件定义、配置与 CLI、实施指南与验收标准）。事件驱动多 Agent 与新闻风控的详细规格见 docs/02 §3、docs/03 §12、docs/05、docs/06 及 prompts/M4~M7。*
