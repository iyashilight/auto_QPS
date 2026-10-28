# Project Map

> 模块导航，不定义新的业务规则。跨模块调用必须通过 `src/qps/core/ports/`；规则以架构、契约和 ADR 为准。

## Architecture

| Area | Location | Responsibility | Must not do |
|---|---|---|---|
| Domain core | `src/qps/core/` | 值对象、状态机、事件、端口 | 依赖基础设施或业务实现 |
| Infrastructure | `src/qps/infra/` | SQLite、journal/outbox、配置、时钟、日志 | 持有策略业务决策 |
| Market data | `src/qps/data/` | 采集、规范化、PIT 查询、特征和 manifest | 返回未来数据；绕过 manifest |
| Broker | `src/qps/broker/` | Paper/交易所适配和能力映射 | 保存策略状态；修改风险决定 |
| Research | `src/qps/research/` | 回测、优化、实验、验证、Release | 读取 Live 密钥；调用 Broker |
| Trading | `src/qps/trading/` | runtime、allocation、risk、execution、ledger、reconcile | 绕过持久化订单流程 |
| Control | `src/qps/control/` | CLI、审批、编排和运维入口 | 直接实现领域规则 |

## Runtime Boundaries

```text
Research Platform -> immutable StrategyRelease -> Live Trading Kernel
StrategyRuntime -> Intent -> Allocation -> RiskReservation
  -> DurableOrderCommand -> Broker -> Fill -> Ledger -> Reconciliation
```

研究进程和交易进程可以同仓库、同主机，但不共享可变运行状态和交易密钥。

## Tests

| Type | Location | Purpose |
|---|---|---|
| Unit | `tests/unit/` | 纯函数、值对象、状态机和局部业务逻辑 |
| Contract | `tests/contract/` | 端口、Broker、序列化和 CLI 边界 |
| Integration | `tests/integration/` | 数据、订单、账本、对账的真实模块组合 |
| Replay | `tests/replay/` | 确定性回放、PIT 和 Release 可复现性 |
| Fault | `tests/fault/` | 崩溃、超时、重复消息、恢复和降险 |

缺少某个测试目录不代表该类别已验收；阶段 brief 必须明确哪些类别适用。

## Documentation

- 当前架构：`加密货币中频AI量化系统架构设计-v0.5.md`
- 阶段门禁和 Review 收敛：`docs/governance/stage-gates.md`
- 实现契约：`docs/01-项目结构与工程规范.md` 至 `docs/06-配置与CLI规范.md`，索引见 `docs/contracts/README.md`
- 当前阶段 Prompt：`prompts/00-project-context.md` 和对应 `prompts/M*.md`
- 历史 v0.4 文档：根目录旧架构和 `docs/07-实施指南与验收标准.md`，不得作为 v0.5 门禁依据
