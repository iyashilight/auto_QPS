# ADR-006: 二期架构（v0.4-2）选择性并入一期里程碑

## 背景

`加密货币中频AI量化系统架构设计-第二期.md`（v0.4-2）在 v0.4 基础上提出 Market Intelligence Layer、Research Layer、Alpha Score、LLM 分层、Onchain Agent、Lifecycle Feedback、Learning Loop 与 PostgreSQL 存储升级。文档明确"不调整 M0~M7 编号与内容"，各模块标注了建议落地阶段。

经审查判定：一期（M0~M7）红线 R7（本阶段不做付费数据/多交易所等）与 R11（M7 前不接 LLM）不可逾越，因此**选择性并入**不依赖 LLM、不依赖付费数据源、且文档自身标注落地阶段在 M3~M6 的模块；其余模块保持二期范围，只登记不实现。

## 决策

### 并入一期（随里程碑实现）

| 模块 | 并入里程碑 | 说明 |
|------|-----------|------|
| Alpha Score（维度定义 + 信号级评分） | M3 | 随 ReportCard 产出，按 run_id 可追溯（R5）；策略级评分 M4、动态权重 M5、实盘校准 M6 启用 |
| Regime Detection（规则版） | M4 | 与风险模式状态机同步；阈值判定输出 MarketRegime，注入 `StrategyContext.regime` |
| Event Analysis（规则版）+ Sentiment（基础版） | M5 | 并入新闻模拟器/规则版新闻管线；新闻事件带 direction/severity/duration，sentiment 聚合新闻+funding+OI |
| 数据来源分级 taxonomy | 现在 | docs/02 §3.9 登记 L0~L6；L2 仅供 M3 滑点建模假设，不采集 |

### 预登记（契约先行，不实现）

docs/05 事件目录新增（schema/语义入契约，未实现前禁止广播）：
`REGIME_CHANGED`（M4）、`MARKET_STATE_UPDATED`（M5）、`ALPHA_DECAY_DETECTED`（M5）、`PERFORMANCE_DEVIATION`（M5）、`STRATEGY_UNDERPERFORMING`（M5）。

### 保持二期范围（不并入一期实现）

- Vector Memory / RAG / Embedding / LLM 研究闭环 / LLM 失败分析 / Strategy Evolution：依赖 LLM 或向量库，M7+（R11）；
- Onchain Agent：真实数据源为付费 API，M7+（R7）；规则版模拟最早 M5，收益/成本比低，不提前；
- Learning Loop / Methodology Update / System Assessment：需足够历史数据与 LLM，M7+；
- PostgreSQL + pgvector 迁移：一期选型 SQLite 是刻意的（本机单用户）；迁移窗口定在 **M4 结束后**（按二期 §22.4 双写迁移），届时 MetaDB 成为唯一 schema 入口，一并消化 registry 自建 DDL 的 M4 待办。

## 约束

- 里程碑编号不变；并入项必须进入对应里程碑提示词与 docs/07 验收清单；
- 新增模型/事件先入 docs/02、docs/05，再实现（契约先行，R12）；
- 任何后续契约变更须更新本文档或新增 ADR，与代码同一 commit。

## 状态

已接受（2026-08-04，Codex 架构审查）。
