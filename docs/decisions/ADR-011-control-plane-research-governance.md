# ADR-011: 策略健康、资本配置、市场风险与研究治理增强

## 状态

已接受。

## 背景

v0.5 已解决 Research/Live 分离、不可变 Release、PIT、持久化订单、双账本和 AI 权限等基础问题，但上线后的策略衰减、跨策略资本分配、共同市场风险、特征资产治理和金融极端场景仍缺少明确控制面。若直接把这些能力塞进逐单交易链路，会增加延迟、状态耦合和故障面。

## 决策

1. 增加 Strategy Health 慢周期控制面，分离 `HealthAssessment`、`DeploymentState` 与 `ResearchDisposition`；自动动作只能降险；
2. 增加慢周期 `PortfolioConstruction -> CapitalAllocationPolicy`，与实时 `IntentNetting -> AllocationPlan` 分离；
3. 增加 `MarketRiskModel -> PortfolioRiskSnapshot`，覆盖相关性、因子、VaR/CVaR、流动性和压力情景，但硬风控继续拥有最终否决权；
4. Feature Registry 分为 Definition、Materialization、Evaluation；禁止维护跨策略/切分的全局表现分数；
5. Research Budget 统一约束人工、脚本、优化器和 AI，包含相似候选归属与独立 Blind reveal 额度；
6. 增加版本化 Market Chaos Scenario/Run，验证极端价格、流动性、funding、mark/index 和交易所时序故障；
7. Broker 保持公共最小端口，保证金、仓位模式和原生保护单使用部署阶段的能力型扩展；禁止万能 `extra_params`。

## 里程碑影响

- M0：新增上述模型、事件、端口和迁移契约；
- M1：Feature Registry Definition/Materialization；
- M3：FeatureEvaluation 与统一 Research Budget；
- M4：单策略 Capital Policy、Health telemetry、Chaos Testing v1；
- M5：Broker 扩展、Strategy Health 自动降险和 Live 前 Chaos 准入；
- M6：完整 Portfolio Construction、Market Risk Model 与多策略 Chaos；
- M7：AI 只能消费既有 Registry/Budget，不能提高额度或伪造评价。

## 备选方案

- 把健康、组合优化和风险模型放入每笔订单关键路径：拒绝，模型不稳定或超时会扩大执行故障面；
- 用滚动 Sharpe 单阈值自动冻结/恢复：拒绝，中频样本噪声大且混淆 alpha 与执行衰减；
- 用 VaR/CVaR 替代硬风险：拒绝，加密市场跳空和分布漂移下会系统性低估风险；
- 只给 AI 设置实验预算：拒绝，人工和脚本搜索同样产生多重试验偏差；
- 通用 Broker 增加任意交易所字段：拒绝，会把 venue 语义泄漏到策略和通用执行链。

## 影响

新增控制面和研究元数据会扩大 M0～M6 契约范围，但不改变 Durable Order Workflow、RiskReservation、账本和对账等核心交易不变量。实现必须按阶段推进，不能以设计完成代替代码验收。

## 日期

2026-08-05
