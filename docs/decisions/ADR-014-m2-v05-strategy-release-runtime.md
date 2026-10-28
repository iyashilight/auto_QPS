# ADR-014：M2 v0.5 策略 Release 与 Runtime 边界

## 状态

已纳入实现，代码已交付，待 M2 v0.5 架构复审。

## 决策

- `StrategyRegistry` 只使用 `MetaDB` canonical strategies/versions 表；版本保存确定性 `code_content` artifact，重复内容 hash 幂等，历史记录由 insert-only 约束保护。
- `ReleaseBuilder` 将登记版本、参数、Feature/PIT manifest、成本、环境和能力清单 canonical 化，生成独立只读 archive、manifest hash 和 `release_id`。
- `ReleaseStore.get_release()` 在返回前校验 archive 存在、无 symlink、代码 hash 和 manifest hash；审批记录独立追加，不能绕过 Release 存在性校验。
- `IsolatedStrategyRuntime` 每个 deployment 使用独立子进程和版本化 JSON IPC。子进程只接收 Release archive 路径和输入快照，不接收 Broker、账户或父进程密钥；异常/超时只标记本 deployment 失败。
- Runtime 默认要求 Linux network namespace (`unshare -n`) 和 `RLIMIT_CPU/RLIMIT_AS/RLIMIT_NPROC`；namespace 探测失败即 fail-closed，不降级启动无网络限制的 child。`require_os_isolation=False` 仅用于离线协议/策略执行测试，不能用于生产。
- `StrategyContext` 增加可选的 `release_id`、固定 `decision_time/clock` 和稳定 state snapshot；旧 SDK 构造保持兼容，但显式 Release 绑定后启用标的白名单。
- `ReleaseBuilder` 在归档和 MetaDB 写入前验证参数 schema、formal PIT manifest hash/quality/标的/周期，以及 FeatureMaterialization 的输入输出 lineage；依赖验证失败不留下 archive 或成功 marker。
- `StateStore` 以 SQLite append-only snapshot journal 提供输入引用幂等、state version 单调和跨 Release 拒绝，Runtime 重复输入返回已提交结果。

## 安全边界

AST Sandbox 仍是注册前开发质量检查，不替代 OS 隔离。Runtime 环境使用显式 allowlist，archive 路径必须位于 ReleaseStore 根目录；M2 不实现 Broker、账户、风险写入、自动审批或 M3 Research Budget。

## 运行前置条件

生产运行需要 Linux 且当前用户具备创建 network namespace 的能力；容器/主机拒绝该能力时 Runtime 明确拒绝启动。该机制不替代代码静态检查、archive hash 校验和 Release 资格验证。

## 影响

新增 `src/qps/research/release.py`、`src/qps/trading/runtime/process_runtime.py` 和 `StrategyVersion.code_content` 持久化字段；旧工作目录不再是正式 Runtime 输入。完整 M2 矩阵仍需架构审查者独立反向验证。
