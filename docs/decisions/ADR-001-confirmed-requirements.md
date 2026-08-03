# ADR-001 需求确认决策（M-1 前置）

## 状态

已接受

## 背景

2026-08-02 用户确认了影响实现细节的 6 项需求决策（原为架构文档 12.3 节待确认项），并明确实施优先级与不做清单。

## 决策

1. **交易所**：Binance 第一接入目标；顺序为 Paper Broker → Binance Adapter → 后续 OKX/Bybit；所有交易所必须通过 Adapter 抽象接入，核心系统不依赖具体交易所；
2. **历史数据**：MVP 不购买付费数据，使用交易所公开 API（REST 历史 K 线 + WebSocket 实时行情）；预留 Data Adapter 端口，未来支持付费源；
3. **回测频率**：默认研究频率 5m / 15m / 1h；1m 不作默认全量回测，仅用于候选策略精细验证、滑点测试、成交模拟优化；
4. **控制台**：MVP 只做 CLI（任务、回测、策略管理、实验结果、交易状态）；Web 控制台延期；
5. **部署**：个人本机量化工作站；预留迁移小型服务器的可能（config/data/state 目录可移植、无云依赖）；
6. **运行模式**：多策略组合模式，Portfolio Manager 为一等模块（资金分配、策略权重、仓位合并、风险暴露控制）；暂不实现复杂组合优化算法，但领域模型支持扩展。

## 实施优先级

1. Research System 闭环 → 2. Backtest Engine → 3. Strategy Framework → 4. Paper Trading → 5. Portfolio Management → 6. Risk Engine → 7. Live Trading。

**不提前开发**：Web 控制台、多交易所接入、云部署、付费数据系统、高频交易基础设施。

## 影响

- 第一个行情/实盘 Adapter 以 Binance 为基准，契约测试保证其他交易所可后插；
- 数据采集、特征计算、回测配置默认 5m/15m/1h；
- Portfolio Manager 进入核心领域模型（StrategyAllocation、PortfolioRisk，见 docs/02 §3.5）；
- CLI 是控制平面的核心交付物。

## 备选方案

- 直接接多个交易所：否决（MVP 阶段复杂度过高）；
- 购买付费历史数据：否决（先验证闭环，不追求数据覆盖极限）；
- 单策略运行模式：否决（用户确认采用多策略组合）。

## 日期

2026-08-02
