# ADR-016：M2 v0.5 证据 Runner 完整性与冻结快照

## 状态

实施中；随 M2 v0.5 `CODE_COMPLETE` 交付，等待独立架构审查者核验。本文不宣布
M2 通过，也不改变 Runtime、Release、PIT、State 或 OS 隔离契约。

## 背景

M2 的 v5 矩阵冻结了产品 Runtime 控制点，却遗漏了 D5 实际执行攻击的
`m2_linux_capability_runner.py`、`capability-positive-linux-runner` 及其 fixture
调用范围。这样修改攻击 fixture 不会使 D5 control hash 失效，固定 Review snapshot
也无法证明审查了实际攻击路径。另一个治理缺口是 `review_preflight.py run` 将所有
非零退出统一记为 `failed`，把能力不足时 runner 明确返回的 `status=blocked`、退出码
77 错记为攻击失败。

## 决策

1. 保留 ADR-015 的执行不变量和 D1～D8 证明要求；本 ADR 只修订证据冻结边界、结构化
   runner 状态解析和 Review snapshot 完整性。
2. M2 任务升级到 `M2-v05-runtime-release-state-v6`，取代 v5；v5 的矩阵和证据文件
   保留为历史事实，不能移植为 v6 通过证据。
3. v6 task scope、D5 四行 controls 和 Review Compiler snapshot 必须包含 Runtime
   实现、D5 capability runner、wrapper 以及 runner 实际调用的 persisted fixture。
   `authoritative_control` 仍是 `process_runtime.py`，runner 只是证据控制面，不是
   产品隔离控制点。
4. `run_evidence` 对结构化输出执行保守分类：`status=blocked` 且退出码 77 记录
   `blocked`；攻击越权或断言失败记录 `failed`；仅攻击实际通过且符合期望退出码才记录
   `passed`。能力不足不能被解释成正向隔离证明。
5. v6 证据从空结果开始。所有受 task/matrix/ADR/runner hash 或依赖传播影响的行必须
   重新取证；外部 capability-positive 和 persisted fixture 证明仍由
   `evidence_validator` 在匹配环境登记，代码开发者不得伪造通过状态。

## 继承关系与范围

- 继承：ADR-010、ADR-011、ADR-015；ADR-015 继续是 Runtime/Release/State 的稳定契约
  和 D5 OS 隔离要求的权威设计。
- 修订：仅修订 v5 证据控制 hash、源码快照覆盖范围和 blocked/failed 的记录语义。
- 不在范围：不放宽 namespace、mount/chroot、secret、资源、进程组清理要求；不改变
  Release、PIT、typed lineage、State、CLI、Broker 或真实交易语义。

## 重新取证规则

v6 必须用 `review_preflight.py run` 记录本地证明，并使用独立持久目录和真实 SQLite/
文件状态运行 D3/D6/D7 fixture。D5 必须在真实具备 network namespace、mount namespace、
受限文件根和 mount/chroot 能力的 Linux 环境执行四个固定入口；rootless namespace 可创建
但 mount/chroot 失败的环境只能记录 `blocked`。所有输出只保留脱敏后的平台、kernel、
Python、UID、CapEff/CapBnd、namespace 探针、命令、攻击结果和退出码。

只有 v6 所有 proof 通过、无 stale/blocked/failed，且 Review Compiler 生成固定 snapshot
后，才能交给另行指定的架构审查者；代码开发者仍不得宣布 `ACCEPTED` 或推进 M3。

## 日期

2026-08-08
