# ADR-004 事件驱动多 Agent 架构与规则版新闻风控

## 状态

已接受

## 背景

2026-08-03 设计讨论确认两件事：

1. 系统形态定义为**事件驱动的多 Agent 量化系统**：News Agent（外部信息）与 Market Agent（行情状态）通过结构化事件驱动 Coordinator（战略决策），Quant Engine 负责验证与执行；Agent 按事件触发，不永久在线；
2. 规则版新闻风控**提前到 M5/M6**（不依赖 LLM），LLM 版在 M7 增强/替代非结构化理解。

## 决策

1. **规则优先，LLM 增强**：确定性风险（交易所状态、费率变化）由规则处理；LLM 只负责非结构化信息理解（新闻语义、市场解释）。规则负责执行，LLM 负责理解，两者不互相替代；
2. **权限分层**：
   - News Agent 拥有**响应优先级**（高优先级事件通道、可打断普通流程、可触发风险模式切换），**没有交易权**（不能下单、不能改风控参数、不能绕过策略系统）；
   - Coordinator 拥有**战略层意图权**（目标敞口/风险姿态/策略偏好 → `CoordinatorIntent`），**不直接下订单**；Portfolio Manager 负责仓位计算，Risk Engine 最终裁决；
3. **事件驱动激活**：Agent 由确定性规则触发（定时调度 / 事件阈值），不永久在线、无共享对话状态；每个 Agent 运行 = 明确输入工件 + 明确输出工件；
4. **NewsEvent 固定 schema**：`type` 受控词表、`state(candidate/confirmed/retracted)`（撤回是一等公民）、合成置信度（分层先验 + 多源一致 + LLM 抽取）、`published_at/detected_at` 分离、`source_refs[]` 多源交叉验证、`raw` 保留原文；
5. **风险模式状态机**：账户级运行姿态 `NORMAL / CAUTION / DEFENSIVE / HALT`，确定性入口（静态映射表 `event_type + severity + confidence → mode/actions`）+ 滞回出口（撤回 + 观察窗口 + 波动率回落，逐级恢复）；`HALT` 只人工恢复；
6. **新闻数据源分层**：L1 官方源（交易所公告，最可信）→ L2 RSS/新闻聚合（带 source/timestamp/url/confidence）→ L3 社媒（后期，必须多源交叉验证）；
7. **路线**：M4 实现风险模式状态机；M5 新闻模拟器 + 事件→风险链路；M6 规则版真实源接入（官方公告轮询 30~60s + RSS，事件→模式延迟 < 1min）；M7 三决策 Agent + 五个研究类 Agent 本地占位 → 未来 LLM 只替换 handler 实现；
8. **Coordinator 边界**：`strategy_preference` 软生效（`ctx.regime` 软信号 + Portfolio/Risk 额度硬约束）；`portfolio_action.valid_until` 必填；`reason_refs` 必填；防御性意图优先级高于普通策略意图但必须过账户级风控。

## 影响

- 架构文档升级 v0.4：新增 §2.7（事件驱动多 Agent）与 §8.5（风险模式状态机）；MVP 里程碑编号统一为 M0~M7；
- docs/02 新增 NewsEvent / CoordinatorIntent / RiskMode / RiskModeSnapshot 模型（§3.7~3.8）；
- docs/03 新增 NewsCollector / NewsPipeline / NewsEventStore / RiskModeController / Coordinator 端口（§10、§12）；
- docs/05 新增 NEWS_EVENT_DETECTED / NEWS_EVENT_UPDATED / RISK_MODE_CHANGED / COORDINATOR_INTENT_CREATED 事件类型与高优先级通道语义；
- docs/06 新增 news 配置段、事件→模式映射表、风险模式滞回配置、`qps news inject/status/events` 与 `qps risk mode` 命令；
- prompts M4~M7 与 docs/07 按新编号重写；
- CLAUDE.md 与 prompts/00 增加 R11/R12 红线。

## 备选方案

1. **三个 LLM 投票决定买卖**：否决——LLM 不应拥有交易权，系统按「事件优先级 + 分层职责」设计；
2. **保留人工审核节点**：否决（采纳用户意见）——用「人工定义的约束」（统计门禁 + 风控规则 + Kill Switch）替代每次审核，迭代链路无人工等待；异常兜底（HALT、规则外暂停）保留人工接管通道；
3. **新闻 LLM 与规则版同期上线**：否决——规则版先行（M5/M6）验证链路与数据质量，LLM 只做非结构化增强（M7 起）；
4. **Coordinator 直接调仓**：否决——LLM 不应掌握精确资金控制；仓位计算、风险预算、换手约束由 Portfolio Manager 负责。

## 日期

2026-08-03
