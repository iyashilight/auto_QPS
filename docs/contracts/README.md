# Contract Index

这里是当前有效契约的入口，不复制字段内容。字段和签名仍以对应文档及代码中的可执行约束为准。

| Contract | Canonical reference | Verification |
|---|---|---|
| 工程、依赖、时间、金额、密钥 | `docs/01-项目结构与工程规范.md` | ruff、mypy、配置与安全测试 |
| 领域模型、Schema、状态机 | `docs/02-领域模型与数据Schema.md` | 模型测试、SQLite schema contract、迁移测试 |
| 端口、适配器、研究/交易服务 | `docs/03-接口契约.md` | contract tests、调用图和 CLI 冒烟 |
| Strategy SDK 与 Intent | `docs/04-策略SDK规范.md` | future-data、隔离、确定性 replay |
| 事件信封、幂等和 lineage | `docs/05-事件定义.md` | 序列化、重复事件、重启恢复 |
| 配置和 CLI 语义 | `docs/06-配置与CLI规范.md` | 退出码、UTC、无副作用失败测试 |
| M3 正式研究、预算与盲测 | `docs/contracts/m3-v05-research-credibility.md` | PIT、预算、盲测隔离、append-only evidence 矩阵 |
| M4 Paper 真实稳定性证据 | `docs/contracts/m4-v05-paper-stability-evidence.md` | 签名 observation、日对账、真实日历窗口、fail-closed 导入 |
| 阶段目标和退出证据 | 当前 `prompts/M*.md` | task brief、evidence、阶段门禁 |

## Authority Rules

1. v0.5 主架构和 ADR-010/011 定义边界与决策。
2. 本目录列出的契约定义稳定字段、签名和语义。
3. 代码是契约的执行实现，测试是可复现证据，不是新的规范来源。
4. `AI_CONTEXT.md`、`docs/map.md`、`HANDOVER.md` 只做导航和状态摘要；发现冲突时回到上述权威来源。
5. 修改稳定契约、事件、迁移或安全边界时，先更新契约和 ADR，再改实现；普通实现细节不写 ADR。
