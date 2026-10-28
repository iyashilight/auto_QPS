# 实现规格文档（Implementation Specs）

> 本文档集是给**代码开发者**的实现依据，回答三个问题：
> 1. 项目结构长什么样、按什么规范写；
> 2. 每个模块的接口契约是什么（签名、语义、异常）；
> 3. 写完后怎么算「完成」（验收标准）。
>
> 架构层面的「为什么」见根目录《加密货币中频AI量化系统架构设计-v0.5.md》。v0.4 两份架构保留为历史背景；本文档当前包含部分 v0.4 已实现契约，切换 v0.5 时按 Prompt 差距审计并通过 ADR/迁移同步。

## 快速入口

- 会话短上下文：根目录 [`AI_CONTEXT.md`](../AI_CONTEXT.md)
- 模块和依赖地图：[`map.md`](map.md)
- 当前有效契约索引：[`contracts/README.md`](contracts/README.md)
- 阶段门禁和 Review 收敛：[`governance/stage-gates.md`](governance/stage-gates.md)
- 当前任务：[`../tasks/`](../tasks/)
- 当前状态和遗留问题：根目录 [`HANDOVER.md`](../HANDOVER.md)

本目录的字段、签名和事件定义是实现契约；不重复定义架构决策。v0.5 与历史文档冲突时，以 v0.5 主架构、契约和 ADR-010/011 为准。

## 1. 文档定位与边界

- 本套文档包含：**目录结构、命名规范、领域模型字段级定义、数据 Schema（含参考 DDL）、接口签名级契约、事件定义、策略 SDK 规范、配置文件与 CLI 规范、分阶段实施指南**；
- 本套文档**不包含**业务实现逻辑（回测撮合算法、风控规则逻辑、策略本身等由开发者实现）；
- 文档中的 Python 签名、JSON 示例、SQL DDL 均为**契约与参考形态**，实现时允许按框架调整，但对外可见的接口签名与数据字段不允许擅自变更（变更需走文档修订）。

## 2. 阅读顺序（按实施顺序）

| 顺序 | 文档 | 用途 |
| --- | --- | --- |
| 1 | [01-项目结构与工程规范](01-项目结构与工程规范.md) | 先搭骨架：目录、命名、依赖、测试、Git 规范 |
| 2 | [02-领域模型与数据Schema](02-领域模型与数据Schema.md) | 定义所有模型与表，M0 的落地依据 |
| 3 | [03-接口契约](03-接口契约.md) | 所有模块端口的签名与语义，实现依据 |
| 4 | [04-策略SDK规范](04-策略SDK规范.md) | 策略插件怎么写、长什么样 |
| 5 | [05-事件定义](05-事件定义.md) | 事件信封、事件类型、payload 示例 |
| 6 | [06-配置与CLI规范](06-配置与CLI规范.md) | 配置文件、环境变量、CLI 命令集 |
| 7 | [07-实施指南与验收标准](07-实施指南与验收标准.md) | 历史 v0.4 实施清单；v0.5 阶段以 `prompts/M*.md` 为准 |

## 3. 全局约定速查（所有模块必须遵守）

| 约定 | 规则 |
| --- | --- |
| 语言 | Python 3.12+，类型注解必须，标识符英文，docstring/注释中文 |
| 包名 | 系统代码统一放 `src/qps/`，禁止散落根目录脚本 |
| 时间 | 统一 UTC；`ts` 一律为带时区时间；事件同时保留 `exchange_ts` 与 `received_ts` 语义 |
| 金额与价格 | 涉及资金/价格的字段用 `Decimal`（禁止 float 累计盈亏）；数据表内用 NUMERIC/字符串存储 |
| 不可变性 | 策略版本、数据批次、实验记录、事件落盘只增不改；修改即新版本 |
| 幂等 | 所有可能重复的调用（下单、任务执行、事件消费）必须有幂等键 |
| 依赖方向 | 业务模块 → 端口（`core/ports`）→ 实现；禁止反向依赖，禁止循环导入 |
| 密钥 | API Key/Secret 只从环境变量读取，禁止进配置、日志、实验记录 |
| 测试 | 每个端口实现必须有对应测试；Broker/Adapter 必须有契约测试；回测必须有确定性测试 |
| 错误 | 业务异常类型化（见 01 章 7 节），禁止裸抛字符串 |

## 4. 与架构文档的对应关系

| 架构文档章节 | 实现规格文档 |
| --- | --- |
| v0.5 §4～§7 发布、PIT、研究、SDK | 02（模型）+ 03（端口）+ 04（SDK） |
| v0.5 §8 回测/Paper 一致性 | 02（BacktestRun/Metrics）+ 03（Backtest/Optimizer） |
| v0.5 §9～§15 交易内核、订单、账本、对账 | 02（领域/DDL）+ 03（端口）+ 05（事件） |
| v0.5 §16～§17 MarketState 与 AI | 02（工件）+ 03（研究端口）+ 05（事件） |
| v0.5 §18～§22 存储、运维、安全、测试 | 01 + 02 + 06 |
| v0.5 §23 里程碑 | `prompts/M*.md`；docs/07 在 v0.5 契约落地时同步修订 |

## 5. 文档修订规则

- 接口签名、字段名、事件类型属于**稳定契约**，修改必须更新本文档并注明变更版本；
- 每个文档头部标注最后更新日期；后续与架构文档 v0.5 同步演进。

## 6. 里程碑提示词与角色说明

所有 Codex 会话都使用根目录 `prompts/` 下的里程碑提示词。代码开发和架构审查是两个独立角色，当前角色由用户在任务中指定：

```
prompts/
├── 00-project-context.md    # 全局上下文（每次会话必读）
├── M-1-engineering.md       # 工程规范与地基
├── M0-foundation.md         # PIT/Feature/Budget/Health/Policy/Risk/订单账本契约
├── M1-market-data.md        # Point-in-Time 数据与 Feature Registry
├── M2-strategy-framework.md # 策略 SDK 与不可变 Release
├── M3-backtest.md           # 可信研究、统一 Research Budget 与盲测治理
├── M4-portfolio-risk.md     # 持久化内核 + 单策略 Paper + Health 遥测 + Chaos v1
├── M5-paper-trading.md      # 单策略 Live + Broker 扩展 + Health 自动降险
├── M6-live-trading.md       # Portfolio Construction + Market Risk + 多策略账本
└── M7-ai-agent.md           # 使用既有 Registry/Budget 的 AI 离线研究
```

- 根目录 `AGENTS.md` 是所有 Codex 会话的共同上下文入口；角色说明见 `docs/roles/`；
- `CLAUDE.md` 仅作为历史兼容说明保留；
- 决策记录：`docs/decisions/`（ADR 格式，见该目录 README）；
- 每个里程碑完成后，当前角色按 `00-project-context.md` 第 9 节模板汇报并更新 `HANDOVER.md`；下一阶段角色由用户重新指定。

---

*最后更新：2026-08-05（v0.5 规划基线；字段级 specs 待各阶段 ADR/迁移同步）*
