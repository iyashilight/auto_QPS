# ADR-013：M1 v0.5 PIT、不可变 Manifest 与持久化 Feature Registry

## 状态

已接受，Review 5 Fixes 代码已交付，待架构复审。

## 决策

1. 研究读取必须显式传入 `decision_time` 和 `manifest_id`。PIT 适配器先验证 MetaDB manifest 及其引用文件的内容 hash，再读取显式 partition；只返回 `available_at <= decision_time` 且 `is_closed=true` 的 Bar。每条记录必须带并匹配请求 `instrument_id`；旧无 PIT Parquet 不能被静默接受。
2. Raw、Normalized 和 Feature 的修订生成新 partition 文件；merge 只生成包含旧内容和新内容的新文件，旧文件字节保持不变。Manifest、FeatureDefinition 和 FeatureMaterialization 使用 MetaDB insert-only 表，内容相同的重试幂等，冲突失败。
3. FeatureEngine 的生产 `compute()` 只能从 `PointInTimeDataPort` 取输入。materialization 使用临时文件、最终 rename 和单个 MetaDB 事务写入输出 manifest、每个 feature definition/materialization；失败清理本次新增文件，M1 不实现 FeatureEvaluation。
4. funding/OI/mark 通过各自 PIT 查询端口读取 manifest 分区，必须具备 `instrument_id`、`available_at/ingested_at`，并拒绝非 UTC、malformed 或 `ingested_at < available_at` 的记录；旧格式由类型化 `DataError` 拒绝，不能保留正式路径上的未标记同名读取。Binance K 线没有源端可见时间时保持 PIT 未知，不以本地 ingestion time 伪造 `available_at`。
5. `ManifestStore.build()` 和默认 manifest CLI 只产生 candidate/hash-only manifest；`build_formal_pit()`/`--formal-pit` 在持久化前调用语义验证。`ManifestStore.verify()` 只执行字节 hash 完整性校验；正式入口统一执行 `verify_pit_manifest`，验证 dataset type、PIT schema、质量报告 JSON 与 manifest hash 绑定、instrument/interval 覆盖、Bar close 语义和 Feature source manifest lineage。五类 PIT 查询共享 UTC 与 `start < end` 请求校验。
6. 正式 Feature partition 的每行必须携带 UTC `decision_time`，并满足不早于该行 `ts` 与 `available_at`。Feature 查询同时执行 `available_at <= 请求 decision_time` 和 `行 decision_time <= 请求 decision_time`，返回行级值；未来、null、naive、非 UTC 或缺列均为语义错误而非可忽略记录。
7. `data compute` 与 `data feature materialize` 在接触数据服务前严格解析 UTC ISO 输入；后者的 `start/end/decision-time` 均不得使用本机时区推断。`research backtest --range` 的两端使用相同 UTC 契约，在容器和研究运行初始化前拒绝 naive、非 UTC 或逆序范围。
8. `manifest status` 是正式资格诊断而非 hash-only 成功报告：PIT candidate 只能显示 `hash_verified`，不能显示 `verified=true`；formal PIT 必须重新调用 `verify_pit_manifest` 后才显示 `verified=true`。
9. 数据新鲜度从 stale 到恢复分别发布 `DATA_STALE` 和 `DATA_RECOVERED`，状态追加到 audit_log 并在重启时重放，stale 状态拒绝新决策。

## 影响

`PointInTimeDataPort` 的返回值固定为带 PIT 字段且按 instrument 隔离的 Bar、funding、OI、mark 或 FeatureSet；CLI 的 verify/status 以 MetaDB manifest 列表为事实来源，`data verify` 没有 manifest 时直接失败，目录扫描只作诊断。正式回测 CLI 必须传递 `manifest_id` 和 UTC `decision_time`，失败结果返回非零退出码。旧 v0.4 `DataProvider.get_*`/`compute_with_bars` 保留兼容性，但不属于生产容器、正式回测或研究 CLI 路径。

## 验收重点

架构复审需反向验证 manifest 选择不能退回目录扫描、PIT 时间过滤不可绕过、旧 partition 不会被原地覆盖、multi-feature 失败不留孤儿输出且重试幂等、Registry 重启仍可恢复 lineage，以及 DATA_STALE 阻断和恢复事件可重放。
