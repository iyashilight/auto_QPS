# ADR-010: v0.5 架构重新基线与里程碑重排

## 背景

对 v0.4 与 v0.4-2 的独立可行性审查发现，原计划在进入 Paper/Live 前缺少四类基础保证：Point-in-Time 数据语义、Blind Challenge 隔离、Broker 副作用前的订单持久化、多策略真实/虚拟双账本。原 M4～M6 又把组合、新闻、Paper、多策略和 Live 紧密交错，导致风险和验收边界不清。

## 决策

1. `加密货币中频AI量化系统架构设计-v0.5.md` 成为当前实施主架构；v0.4 与 v0.4-2 保留为历史方案；
2. 系统拆分为 Research Platform 与 Live Trading Kernel，以不可变 `StrategyRelease` 为唯一发布边界；
3. 数据模型引入 `event_time / available_at / ingested_at` 与历史元数据 `valid_from / valid_to`；
4. 研究分为 Development、Validation、Blind Challenge、Forward Paper，并统计策略 family 级 trial budget；
5. 旧 Alpha Score 不再驱动资金，报告分离 `EvidenceQuality / ExpectedEdge / RiskEstimate`；
6. 交易链路调整为 `Intent -> RiskReservation -> DurableOrderCommand -> Broker -> Fill -> Ledger -> Reconciliation`，Broker 副作用前必须完成本地 ACID 持久化；
7. M4 只做持久化交易内核与单策略 Paper，M5 只做单策略小资金 Live，M6 才做多策略虚拟账本与组合；
8. M7 仅做 AI 离线研究增强，AI 不进入 Live Trading Kernel；
9. SQLite/Parquet/DuckDB 保持默认，PostgreSQL 仅由实测并发、延迟或可用性瓶颈触发，不采用应用层无事务双写。

## 对既有决策的影响

- ADR-004 的“Agent 无交易权”继续有效；Coordinator 直接影响实时组合的计划延后，M7 首先只做离线研究；
- ADR-006 保留历史记录，但其 Alpha Score 动态权重、M4 Regime、M5 Event/Sentiment、M4 后 PostgreSQL 双写窗口被本 ADR 修订；
- ADR-007/008 中 AlphaScore 的已实现 schema 可保留兼容，不再作为自动晋升或资金权重契约；
- ADR-009 的排序语义仍可作为输入规范，但不能单独证明无未来函数；v0.5 还要求 available_at/decision_time 泄漏测试。

## 迁移与验收

- 已完成 M-1～M3 是 v0.4 实现事实，不自动成为 v0.5 验收；
- 按新版 `prompts/M0~M3` 做差距审计，满足项保留、缺口补齐；
- 字段、端口、事件和 DDL 的具体变更由后续 ADR + docs + migration 落地；本 ADR 不声称这些代码已经实现；
- M0～M3 的 v0.5 P0 门禁完成前，不进入 M4 的交易副作用实现。

## 备选方案

- 继续按 v0.4 在 M4/M5 同时实现组合、Regime、新闻与 Paper：拒绝，关键订单/账本基础未先闭环，故障面过大；
- 保持 Alpha Score 并直接映射动态权重：拒绝，研究证据质量与预期收益含义不同，且忽略相关性、容量和试验偏差；
- 立即迁移 PostgreSQL 并应用层双写：拒绝，当前单机单 writer 无实测瓶颈，双写会引入新的不一致状态。

## 状态

已接受（2026-08-05；用户要求生成新版详细架构并同步修改原实施计划）。

## 日期

2026-08-05
