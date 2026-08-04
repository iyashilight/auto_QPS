# 02 领域模型与数据 Schema

> 适用：所有开发者。M0 阶段按本文件实现 `src/qps/core/models/` 与数据库表。
> 约定：模型用 Pydantic v2 定义；字段名即数据库列名（snake_case）；时间为 UTC；资金/价格为 Decimal。
> 最后更新：2026-08-03

## 1. 模型总览

所有模型位于 `src/qps/core/models/`，按域分文件：

```
models/
├── instrument.py    # Instrument, SymbolMapping, ExchangeMeta, ExchangeCalendar, CalendarBreak
├── market.py        # Bar, Tick, OrderBookSnapshot, FundingRate, OpenInterest, MarkPrice, FeatureSet
├── adapter.py       # AdapterStatus
├── news.py          # NewsEvent, NewsSourceRef, NewsEventType/State/Direction（M5/M6 规则版，M7 LLM 增强）
├── strategy.py      # Strategy, StrategyVersion, StrategyParams, Deployment
├── intent.py        # TradingIntent, CoordinatorIntent（战略层投资意图）
├── order.py         # Order, Trade
├── account.py       # Account, Position, Snapshot, StrategyAllocation, PortfolioRisk
├── risk.py          # RiskRule, RiskCheckResult, RiskEvent, RiskModeSnapshot, RiskMode
├── experiment.py    # BacktestRun, OptimizationRun, ReportCard, Metrics
├── task.py          # TaskRun
├── audit.py         # AuditLog
└── artifact.py      # Artifact（AI Agent 工件，M6）
```

## 2. 通用字段与约束

| 规则 | 说明 |
| --- | --- |
| 时间 | `datetime` 一律 UTC；序列化为 ISO8601 带时区（如 `2026-08-02T12:00:00Z`） |
| 价格/金额 | `Decimal`，精度保留 8 位小数（内部），对外按交易所精度截断 |
| ID | `str`，`uuid4` 或 `int`（数据库自增），ID 生成规则见各表 |
| 枚举 | 用 `StrEnum`（Python 3.11+），值全大写 |
| 版本号 | `str`，格式见 4.5 节 |

## 3. 领域模型字段定义

### 3.1 标的与市场

**Instrument**（`instrument_id, internal_symbol` 全局唯一）

| 字段 | 类型 | 必填 | 说明/示例 |
| --- | --- | --- | --- |
| instrument_id | str | 是 | 系统内部唯一 ID，如 `binance-btc-usdt-perp` |
| internal_symbol | str | 是 | 统一符号，如 `BTC-USDT-PERP` |
| asset_class | enum | 是 | `SPOT` / `PERPETUAL` |
| base_asset / quote_asset | str | 是 | `BTC` / `USDT` |
| exchange | str | 是 | `binance` |
| contract_size | Decimal | 否 | 永续合约面值（如 `0.001`），现货为 `1` |
| tick_size | Decimal | 是 | 最小价格变动 |
| lot_size | Decimal | 是 | 最小数量精度 |
| min_notional | Decimal | 是 | 最小下单名义金额 |
| maker_fee / taker_fee | Decimal | 是 | 费率（如 `0.0002`） |
| funding_interval | str | 否 | 资金费率结算间隔，如 `8h` |
| margin_mode | enum | 否 | `ISOLATED` / `CROSS`（永续） |
| max_leverage | Decimal | 否 | 最大杠杆（永续） |
| status | enum | 是 | `ACTIVE` / `DELISTED` / `SUSPENDED` |

**SymbolMapping**：`instrument_id + exchange` 唯一

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instrument_id | str | 内部 ID |
| exchange | str | 交易所 |
| exchange_symbol | str | 交易所原始符号，如 `BTCUSDT` |
| is_primary | bool | 是否主映射 |

**ExchangeMeta**：`exchange_id` 唯一

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| exchange_id | str | `binance` |
| rate_limits | list[dict] | 限频规则（REST/WS） |
| ws_endpoints / rest_endpoints | dict | 端点 |
| maintenance_schedule | list | 维护窗口（可人工维护） |

**CalendarBreak**：维护/暂停窗口

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| start | datetime(UTC) | 是 | 窗口开始时间 |
| end | datetime(UTC) | 是 | 窗口结束时间（必须 > start） |
| reason | str \| None | 否 | 原因，如 `maintenance` |

**ExchangeCalendar**：每日期一条记录

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| exchange | str | 是 | 交易所/市场 ID，如 `binance` |
| date | date | 是 | 交易日（UTC 日期） |
| session_open | datetime(UTC) | 是 | 开市时间（必须 < session_close） |
| session_close | datetime(UTC) | 是 | 收市时间 |
| breaks | list[CalendarBreak] | 否 | 维护/暂停窗口，默认空列表 |

### 3.2 市场数据

**Bar**（主键：`instrument_id + open_time + interval`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| instrument_id | str | 内部 ID |
| open_time | datetime(UTC) | K 线开盘时间 |
| interval | str | `1m` `5m` `15m` `1h` |
| open / high / low / close | Decimal | 价格 |
| volume | Decimal | 成交量（base） |
| quote_volume | Decimal | 成交额（quote） |
| taker_buy_volume | Decimal | 主动买量 |
| trades_count | int | 成交笔数 |
| data_version | str | 数据批次版本 |
| quality | enum | `OK` / `SUSPECT` |

**Tick**：`instrument_id + ts`；字段 `ts, price, quantity, side(BUY/SELL), exchange_ts, received_ts`

**OrderBookSnapshot**：`instrument_id + ts`；字段 `bids[{price, qty}], asks[{price, qty}], exchange_ts`（低频采样，如每 1m/5m）

**FundingRate**：`instrument_id + funding_ts`；字段 `rate, predicted_rate?, settlement_ts, interval, data_version`（M1.4 新增 data_version）

**OpenInterest**：`instrument_id + ts`；字段 `oi_value, oi_contracts, exchange_ts, data_version`（M1.4 新增 data_version）

**MarkPrice**：`instrument_id + ts`；字段 `mark_price, index_price, exchange_ts`

**FeatureSet**：`instrument_id + ts + feature_name`；字段 `value, feature_version, data_version`

**AdapterStatus**：适配器连接状态快照

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| connected | bool | 是 | 是否已连接 |
| last_heartbeat | datetime(UTC) \| None | 否 | 最近心跳时间，默认 None |
| reconnect_count | int | 否 | 断线重连次数，默认 0，必须 >= 0 |
| latency_ms | int \| None | 否 | 最近延迟（毫秒），默认 None，必须 >= 0 |

### 3.3 策略与版本

**Strategy**（主表，可变）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| strategy_id | str | 是 | 唯一 ID，如 `trend_breakout` |
| name | str | 是 | 显示名 |
| description | str | 否 | 中文说明 |
| style | enum | 是 | `TREND` / `MEAN_REVERSION` / `ARBITRAGE` / `GRID` / `AI` / `OTHER` |
| lifecycle_status | enum | 是 | 见 5 节状态机 |
| tags | list[str] | 否 | 标签 |
| account_binding | str | 否 | 绑定账户 ID（可空，运行时绑定） |
| source | enum | 是 | `MANUAL` / `AGENT` |

**StrategyVersion**（不可变；`strategy_id + version_no` 唯一）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| strategy_id | str | 所属策略 |
| version_no | int | 从 1 递增 |
| code_ref | str | `git commit hash + strategies/<name>/` |
| params_schema | dict | JSON Schema（策略参数声明） |
| data_dependencies | list[str] | 依赖的标的/数据（如 `BTC-USDT-PERP:5m`） |
| feature_dependencies | list[str] | 依赖的特征名清单 |
| runtime_constraints | dict | 静态检查约束（见 04 文档） |
| asset_class_support | list[enum] | `["SPOT"]` / `["PERPETUAL"]` / 两者 |
| content_hash | str | 代码包内容哈希（sha256） |
| source | enum | `MANUAL` / `AGENT` |
| created_at | datetime | 创建时间 |

**StrategyParams**：`strategy_id + version_no + params_hash` 唯一；字段 `params(dict), params_hash`

**Deployment**（一次运行部署）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| deployment_id | str | 唯一 |
| strategy_id / version_no | str/int | 运行对象 |
| params | dict | 参数快照 |
| account_id | str | 绑定账户 |
| mode | enum | `PAPER` / `PRODUCTION` |
| status | enum | `CREATED` / `RUNNING` / `PAUSED` / `STOPPED` / `CRASHED` |

### 3.4 交易意图、订单与成交

**TradingIntent**（策略唯一输出；字段白名单校验）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| intent_id | str | 是 | uuid |
| strategy_id / version_no | str/int | 是 | 来源 |
| deployment_id | str | 是 | 部署实例 |
| instrument_id | str | 是 | 目标标的 |
| direction | enum | 是 | `LONG` / `SHORT` / `FLAT` |
| action | enum | 是 | `OPEN` / `INCREASE` / `REDUCE` / `CLOSE` / `FLIP` |
| quantity | Decimal | 二选一 | 数量（与 notional 二选一，优先 quantity） |
| notional | Decimal | 二选一 | 名义金额（USD 计价） |
| order_type | enum | 是 | `MARKET` / `LIMIT` |
| limit_price | Decimal | 否 | LIMIT 时必填 |
| valid_until | datetime | 是 | 过期作废 |
| intent_priority | enum | 否 | `NORMAL` / `URGENT`（默认 NORMAL） |
| reason_tags | list[str] | 否 | 语义标签（风控细分规则用） |
| raw_payload | dict | 否 | 策略原始输出（审计） |

**Order**（订单）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| order_id | str | 本地订单 ID |
| client_order_id | str | 幂等键（对外下单引用），格式 `qps-<deployment>-<uuid8>` |
| account_id / deployment_id | str | 归属 |
| instrument_id | str | 标的 |
| side | enum | `BUY` / `SELL` |
| order_type | enum | `MARKET` / `LIMIT` |
| quantity / filled_quantity | Decimal | 委托量/已成交量 |
| avg_price | Decimal | 成交均价（未成交为 null） |
| limit_price | Decimal | 限价 |
| leverage | Decimal | 永续杠杆（null=账户默认） |
| margin_mode | enum | 逐仓/全仓 |
| status | enum | 状态机见 3.4.1 |
| reject_reason | str | 拒单原因 |
| timestamps | dict | `created/submitted/acked/filled/canceled/expired` 各时间点 |

订单状态机（枚举 `OrderStatus`）：

`NEW → PENDING_SUBMIT → PENDING_ACK → WORKING ⇄ PARTIALLY_FILLED → FILLED / CANCELED / REJECTED / EXPIRED`

**Trade**：`trade_id` 唯一；字段 `order_id, instrument_id, side, quantity, price, fee, fee_currency, realized_pnl?, ts, exchange_trade_id`

### 3.5 账户、持仓与组合

**Account**：`account_id` 唯一；字段 `mode(PAPER/PRODUCTION), account_type(SPOT/PERPETUAL/SUB), exchange_ref, balances[{asset, free, locked, equity}], total_equity, status`

**Position**：`position_id` 唯一；字段 `account_id, instrument_id, side(LONG/SHORT), quantity, entry_price, avg_cost, mark_price, unrealized_pnl, realized_pnl, leverage, margin_mode, liquidation_price, maintenance_margin, updated_ts`

**Snapshot**：`snapshot_ts`；内容 = 全量账户 + 全量持仓 + 组合风险（对账/回放用）

**StrategyAllocation**（组合资金分配，MVP 静态）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| allocation_id | str | 唯一 |
| account_id / strategy_id | str | 账户与策略 |
| weight | Decimal | 权重（0~1，所有启用的 allocation 之和 ≤ 1） |
| capital_limit | Decimal | 该策略占用资金上限 |
| currency | str | 计价币种（如 `USDT`） |
| enabled | bool | 是否参与分配 |

**PortfolioRisk**（组合风险快照）：`account_id + snapshot_ts`；字段 `total_notional, exposure_by_symbol, exposure_by_strategy, net_exposure_by_symbol, concentration, combined_leverage`

### 3.6 实验与评估

**BacktestRun**

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| run_id | str | 唯一，如 `bt_<timestamp>_<uuid8>` |
| strategy_id / version_no | str/int | 回测对象 |
| params | dict | 参数 |
| data_range | {start, end} | 时间范围 |
| data_version / feature_version | str | 数据与特征版本 |
| engine_config | dict | 引擎配置快照（撮合模式/费率/滑点/杠杆等） |
| environment_lock | str | 环境锁文件 hash |
| trades_ref | str | 逐笔交易文件路径（Parquet/JSONL） |
| equity_curve_ref | str | 权益曲线文件路径 |
| metrics | Metrics | 指标全集 |
| status | enum | `PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED` |
| created_at | datetime | 记录时间 |

**OptimizationRun**：`run_id` 唯一；字段 `strategy_id, version_no, search_space, objective, algorithm, evaluations[], best_params, best_metrics, walkforward_report, status, created_at`

**Metrics**（指标全集，报告与门禁依据）

| 指标 | 类型 | 必含 | 说明 |
| --- | --- | --- | --- |
| total_return / annualized_return | Decimal | 是 | 总收益/年化 |
| max_drawdown | Decimal | 是 | 最大回撤 |
| sharpe | Decimal | 是 | 夏普比率 |
| win_rate | Decimal | 是 | 胜率 |
| volatility | Decimal | 是 | 波动率 |
| trade_count | int | 是 | 交易次数 |
| profit_factor | Decimal | 是 | 盈亏比 |
| calmar / sortino | Decimal | 是 | 辅助指标 |
| avg_holding_time | Decimal | 否 | 平均持仓时长 |
| turnover | Decimal | 否 | 换手率 |
| max_consecutive_losses | int | 否 | 最大连续亏损次数 |
| funding_pnl | Decimal | 永续必填 | 资金费率累计盈亏（单列！） |
| liquidation_count | int | 永续必填 | 强平次数（>0 需人工评估） |
| cost_breakdown | dict | 是 | 手续费/滑点/资金费率分列 |
| oos_metrics | dict | 是 | 样本外指标（门禁依据） |

**ReportCard**：`run_id + strategy_id + version_no`；字段 `metrics, gate_results{draft→bt, bt→validated, ...}, conclusion`

**AlphaScore**（信号/策略评估，二期 v0.4-2 §16 预登记，M3 起随回测产出）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| score_id | str | 唯一 ID |
| strategy_id / version_no | str/int | 评估对象 |
| evaluated_at | datetime(UTC) | 评估时间 |
| run_id | str | 来源回测/优化运行（R5 可追溯） |
| opportunity | Decimal(0~1) | 机会频率 |
| confidence | Decimal(0~1) | 置信度 |
| stability | Decimal(0~1) | 稳定性 |
| market_fit | Decimal(0~1) | 市场适配度 |
| decay_rate | Decimal(0~1) | 衰减率 |
| risk_cost | Decimal(0~1) | 风险成本 |
| composite_score | Decimal(0~1) | 综合评分（公式可配置，可 null） |
| trend | enum | `IMPROVING / STABLE / DECLINING` |
| recommended_weight_adjustment | enum | `INCREASE / MAINTAIN / REDUCE / PAUSE` |

> M3 只要求维度定义 + 信号级评分随 ReportCard 产出；策略级评分、动态权重、实盘校准分别在 M4/M5/M6 启用（见 ADR-006）。

### 3.7 风控与审计

**RiskRule**：`rule_id` 唯一；字段 `level(ACCOUNT/STRATEGY/ORDER/MARKET), rule_type, params(dict), enabled, version, updated_by, updated_at`

**RiskCheckResult**：`check_id` 唯一；字段 `intent_id, rule_results[{rule_id, passed, detail}], verdict(ALLOW/BLOCK/MODIFY), ts`

**RiskEvent**：`event_id` 唯一；字段 `event_type(BREACH/COOLDOWN/FREEZE/KILLSWITCH), severity, context(dict), triggered_at, action_taken`

**RiskMode**（枚举）：`NORMAL / CAUTION / DEFENSIVE / HALT`（账户级运行姿态，状态机见架构文档 §8.5）

**RiskModeSnapshot**：`snapshot_id` 唯一；字段 `account_id, mode(RiskMode), entered_at(UTC), triggered_by(NEWS_EVENT/MARKET_ANOMALY/PORTFOLIO_RISK/MANUAL), reason, event_ref?`（每次模式切换落一条，不可变）

**AuditLog**：`log_id` 唯一；字段 `actor(MAN/AGENT/SYSTEM), action, target_type, target_id, before, after, ts, reason`

**TaskRun**：`task_id` 唯一；字段 `task_type, params, status(PENDING/RUNNING/SUCCEEDED/FAILED/CANCELED), started_at, finished_at, error?`

**Artifact**（M7）：`artifact_id` 唯一；字段 `artifact_type, schema_version, content(dict), strategy_id?, run_id?, agent_role?, created_at`（不可变）

### 3.8 新闻事件与 Agent 工件

> 设计背景见架构文档 §2.7。规则版（M5/M6）只产出规则可识别的结构化事件；LLM 版（M7）扩展非结构化理解，schema 不变。

**NewsSourceRef**：新闻来源引用（多源交叉验证）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| name | str | 是 | 来源名，如 `Binance Announcement` / `CoinDesk` |
| url | str | 否 | 原文链接 |
| time | datetime | 是 | 源发布时间（UTC） |
| tier | enum | 是 | `L1_OFFICIAL` / `L2_RSS` / `L3_SOCIAL`（分层先验用） |

**NewsEvent**（结构化市场事件，`id` 唯一，不可变；状态迁移通过新事件版本表达）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| id | str | 是 | 事件 ID，如 `event_001` |
| schema_version | int | 是 | schema 版本，默认 1 |
| type | enum | 是 | `EXCHANGE_STATUS / EXCHANGE_HACK / REGULATORY_RISK / LISTING_DELISTING / MAINTENANCE / MARKET_NEWS / MACRO_EVENT`（受控词表，可扩展） |
| state | enum | 是 | `CANDIDATE / CONFIRMED / RETRACTED`（撤回是一等公民） |
| assets | list[str] | 是 | 影响标的（内部符号），空列表表示全市场 |
| direction | enum | 是 | `POSITIVE / NEGATIVE / NEUTRAL / UNKNOWN` |
| impact.severity | int | 是 | 1~10 |
| impact.duration_hours | int | 是 | 影响窗口（事件过期机制） |
| confidence | Decimal | 是 | 合成置信度 0~1（分层先验 + 多源一致性 + LLM 抽取，非 LLM 直报） |
| published_at | datetime | 是 | 源发布时间（UTC） |
| detected_at | datetime | 是 | 系统检测时间（UTC，评估管线延迟） |
| source_refs | list[NewsSourceRef] | 是 | 至少一个来源；多源一致性参与置信度合成 |
| raw | str | 是 | 原文/标题摘要（审计与抽取质量评估） |

**CoordinatorIntent**（战略层投资意图，来源固定为 `coordinator`；走交易链路但不直接下单）

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| intent_id | str | 是 | 唯一 ID |
| source | enum | 是 | 固定 `COORDINATOR` |
| market_regime | dict | 是 | `{trend, volatility, volume}`（趋势/波动/成交量状态标记） |
| portfolio_action.target_exposure | Decimal | 是 | 目标总敞口（权益比例 0~1） |
| portfolio_action.reduce_risk | bool | 是 | 是否防御性减险（true 时优先级高于普通策略意图） |
| portfolio_action.target_leverage | Decimal | 否 | 目标杠杆上限（如提供） |
| portfolio_action.valid_until | datetime | 是 | 姿态有效期（必填，过期失效需重估） |
| strategy_preference | list[dict] | 否 | `[{strategy_id, action(ENABLE/DISABLE/KEEP/REDUCE_WEIGHT), weight?}]`（软生效，见架构 §2.7.7） |
| reason_refs | list[str] | 是 | 触发事件/状态工件 ID（审计） |
| raw | dict | 否 | Coordinator 原始输出 |

### 3.9 数据来源分级（二期 v0.4-2 §18 预登记）

> 分级用于数据治理与 Alpha Score 贡献度评估；本阶段只登记 taxonomy，L2+ 采集按各自里程碑推进。

| Level | 内容 | 示例来源 | 落地阶段 |
| --- | --- | --- | --- |
| L0 | 基础行情（K 线/funding/OI） | Binance 免费 API | M1 ✅ 已落地 |
| L1 | 规范化/特征数据 | 内部管线 | M1 ✅ 已落地 |
| L2 | 衍生品深度（订单簿/买卖压力/CVD） | Binance Depth API | M3+（滑点建模假设，MVP 不采集） |
| L3 | 链上数据 | Glassnode/CryptoQuant（付费） | M7+（见二期 §19，本阶段不做） |
| L4 | 新闻数据（官方公告/RSS） | Binance 公告/CoinDesk RSS | M5/M6（一期已规划） |
| L5 | 宏观数据 | FRED/Yahoo Finance | M7+ |
| L6 | 社交媒体 | X/Reddit/Telegram | M7+ 后期 |

### 3.10 市场状态工件（二期 v0.4-2 §14/§24.1 预登记）

> M4 起随 Regime Detection 落地；只读软信号，策略无权修改（docs/04 §4）。

**MarketRegime**（Regime 判定输出）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| regime_id / instrument_id / ts | str/str/datetime(UTC) | 唯一 ID、标的、评估时间 |
| regime | enum | `TRENDING_UP / TRENDING_DOWN / RANGING / HIGH_VOLATILITY / CRISIS / RECOVERY` |
| confidence | Decimal(0~1) | 置信度 |
| volatility_state | enum | `LOW / NORMAL / HIGH / EXTREME` |
| volume_state | enum | `BELOW_AVERAGE / AVERAGE / ABOVE_AVERAGE / ABNORMAL` |
| trend_strength | Decimal(0~1) | 趋势强度 |
| feature_version / data_version | str | 特征/数据版本（R5） |

**SentimentState**（情绪聚合输出，M5）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| sentiment_id / instrument_id / ts | str/str/datetime(UTC) | 唯一 ID、标的、评估时间 |
| score | Decimal(-1~1) | 聚合情绪分（新闻+funding+OI） |
| confidence | Decimal(0~1) | 置信度 |
| components | dict | 各来源分项（news/funding/oi） |
| data_version | str | 数据版本（R5） |

**MarketState**（聚合市场状态，M5 末/M6）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| state_id / ts | str/datetime(UTC) | 唯一 ID、聚合时间 |
| regime / sentiment / events | ref | 引用 MarketRegime / SentimentState / NewsEvent 列表 |
| overall_risk_posture | enum | `NORMAL / CAUTION / DEFENSIVE / HALT` |
| alpha_environment | enum | `FAVORABLE / MODERATE / HOSTILE` |

## 4. 存储 Schema

### 4.1 SQLite 元数据库（`data/meta.db`）

表清单：`instruments, symbol_mappings, exchange_meta, strategies, strategy_versions, strategy_params, strategy_allocations, portfolio_risk_snapshots, deployments, experiments, report_cards, metrics, risk_rules, risk_events, risk_mode_snapshots, news_events, audit_log, tasks, artifacts`

参考 DDL（节选核心表，其余同风格）：

```sql
CREATE TABLE strategies (
    strategy_id       TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    style             TEXT NOT NULL CHECK (style IN ('TREND','MEAN_REVERSION','ARBITRAGE','GRID','AI','OTHER')),
    lifecycle_status  TEXT NOT NULL DEFAULT 'DRAFT',
    tags              TEXT,                 -- JSON array
    account_binding   TEXT,
    source            TEXT NOT NULL DEFAULT 'MANUAL',
    created_at        TEXT NOT NULL,        -- ISO8601 UTC
    updated_at        TEXT NOT NULL
);

CREATE TABLE strategy_versions (
    strategy_id         TEXT NOT NULL,
    version_no          INTEGER NOT NULL,
    code_ref            TEXT NOT NULL,      -- git commit + path
    params_schema       TEXT NOT NULL,      -- JSON Schema
    data_dependencies   TEXT NOT NULL,      -- JSON array
    feature_dependencies TEXT NOT NULL,     -- JSON array
    runtime_constraints TEXT NOT NULL,      -- JSON dict
    asset_class_support TEXT NOT NULL,      -- JSON array
    content_hash        TEXT NOT NULL,
    source              TEXT NOT NULL DEFAULT 'MANUAL',
    created_at          TEXT NOT NULL,
    PRIMARY KEY (strategy_id, version_no),
    FOREIGN KEY (strategy_id) REFERENCES strategies(strategy_id)
);

CREATE TABLE experiments (
    run_id            TEXT PRIMARY KEY,
    run_type          TEXT NOT NULL CHECK (run_type IN ('BACKTEST','OPTIMIZATION')),
    strategy_id       TEXT NOT NULL,
    version_no        INTEGER NOT NULL,
    params            TEXT NOT NULL,        -- JSON
    data_version      TEXT NOT NULL,
    feature_version   TEXT NOT NULL,
    engine_config     TEXT NOT NULL,        -- JSON
    environment_lock  TEXT,
    status            TEXT NOT NULL,
    created_at        TEXT NOT NULL
);

CREATE TABLE report_cards (
    run_id        TEXT PRIMARY KEY,
    strategy_id   TEXT NOT NULL,
    version_no    INTEGER NOT NULL,
    metrics       TEXT NOT NULL,            -- JSON, 字段见 Metrics
    gate_results  TEXT NOT NULL,            -- JSON
    alpha_score   TEXT,                     -- JSON, AlphaScore 模型（M3 起）
    conclusion    TEXT
);

CREATE TABLE risk_rules (
    rule_id      TEXT PRIMARY KEY,
    level        TEXT NOT NULL,
    rule_type    TEXT NOT NULL,
    params       TEXT NOT NULL,             -- JSON
    enabled      INTEGER NOT NULL DEFAULT 1,
    version      INTEGER NOT NULL DEFAULT 1,
    updated_by   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE audit_log (
    log_id      TEXT PRIMARY KEY,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    before      TEXT,                        -- JSON
    after       TEXT,                        -- JSON
    ts          TEXT NOT NULL,
    reason      TEXT
);

CREATE TABLE strategy_allocations (
    allocation_id TEXT PRIMARY KEY,
    account_id    TEXT NOT NULL,
    strategy_id   TEXT NOT NULL,
    weight        TEXT NOT NULL,             -- Decimal as TEXT/NUMERIC
    capital_limit TEXT NOT NULL,
    currency      TEXT NOT NULL DEFAULT 'USDT',
    enabled       INTEGER NOT NULL DEFAULT 1
);
```

索引要求：`experiments(strategy_id, version_no)`、`report_cards(strategy_id, version_no)`、`audit_log(ts)`、`strategy_versions(strategy_id)`。

约束：

- 金额字段以 TEXT/NUMERIC 存 Decimal（SQLite 无精确 Decimal，禁止 REAL）；
- 所有历史记录只 INSERT，不做 UPDATE/DELETE（审计与实验表）；
- `data/` 中元数据库与运行状态库分离：`meta.db`（研究+配置）与 `state.db`（实盘状态）。

### 4.2 运行状态库（`data/state.db`）

表：`orders, trades, positions, accounts, risk_state, deployments, snapshots`

参考 DDL（节选）：

```sql
CREATE TABLE orders (
    order_id         TEXT PRIMARY KEY,
    client_order_id  TEXT NOT NULL UNIQUE,
    account_id       TEXT NOT NULL,
    deployment_id    TEXT NOT NULL,
    instrument_id    TEXT NOT NULL,
    side             TEXT NOT NULL,
    order_type       TEXT NOT NULL,
    quantity         TEXT NOT NULL,
    filled_quantity  TEXT NOT NULL DEFAULT '0',
    avg_price        TEXT,
    limit_price      TEXT,
    leverage         TEXT,
    margin_mode      TEXT,
    status           TEXT NOT NULL,
    reject_reason    TEXT,
    timestamps       TEXT NOT NULL,          -- JSON
    updated_ts       TEXT NOT NULL
);

CREATE TABLE positions (
    position_id        TEXT PRIMARY KEY,
    account_id         TEXT NOT NULL,
    instrument_id      TEXT NOT NULL,
    side               TEXT NOT NULL,
    quantity           TEXT NOT NULL,
    entry_price        TEXT NOT NULL,
    mark_price         TEXT,
    unrealized_pnl     TEXT,
    realized_pnl       TEXT,
    leverage           TEXT,
    margin_mode        TEXT,
    liquidation_price  TEXT,
    updated_ts         TEXT NOT NULL
);

CREATE TABLE risk_state (
    account_id      TEXT PRIMARY KEY,
    day_pnl         TEXT NOT NULL DEFAULT '0',
    day_loss_count  INTEGER NOT NULL DEFAULT 0,
    cooldowns       TEXT NOT NULL DEFAULT '{}',   -- JSON: rule_id -> until_ts
    freeze_flags    TEXT NOT NULL DEFAULT '{}',
    updated_ts      TEXT NOT NULL
);
```

### 4.3 Parquet 数据目录约定

Raw 层（`data/raw/<source>/<exchange>/<instrument>/<data_type>/<YYYY-MM-DD>/`）：

```
data/raw/binance/btcusdt-perp/klines/2026-08-01/part-000.parquet
data/raw/binance/btcusdt-perp/funding/2026-08-01/part-000.parquet
data/raw/binance/btcusdt-perp/open_interest/2026-08-01/part-000.parquet
data/raw/news/binance-announcement/2026-08-01/part-000.parquet   # 原始新闻（含原文/来源/时间）
```

Normalized 层（`data/normalized/<interval>/<instrument>/`）：

```
data/normalized/5m/btc-usdt-perp/2026.parquet   # K 线按年分片
data/normalized/5m/btc-usdt-perp/2027.parquet
```

**资金费率与持仓量 Normalized 分区**（M1.4 新增）：

```
data/normalized/funding/btc-usdt-perp/2026.parquet   # 资金费率按年分片
data/normalized/open_interest/btc-usdt-perp/2026.parquet   # 持仓量按年分片
```

Feature 层（`data/features/<feature_version>/<interval>/`）：同 Normalized 分片方式。

分区与读取规则：

- K 线按 `open_time` 分片；查询必须带时间范围（禁止全表扫描读取）；
- 每个 Parquet 文件头部元数据写入 `data_version` / `feature_version`（或文件名后缀）；
- DuckDB 视图：`bars_5m` 等可直接跨文件查询（研究系统专用）。

### 4.4 事件落盘（`data/event_logs/<YYYY-MM-DD>.jsonl`）

- 每行一个 JSON 事件（信封见 05 文档）；
- 实盘关键事件（订单/成交/风控/生命周期）必须落盘；研究系统事件可选。

### 4.5 版本号规范

| 版本 | 格式 | 示例 |
| --- | --- | --- |
| data_version | `<source>-<batch_sha256前8>` | `binance-3f2a91c4` |
| feature_version | `feat-<n>` 递增 | `feat-7` |
| 策略版本 | 整数递增 | `3` |
| 环境锁 | `env-<lockfile_sha256前8>` | `env-9b41d2aa` |

---

*本文件为 Schema 基线；DDL 为参考实现，字段与约束以表为准。*
