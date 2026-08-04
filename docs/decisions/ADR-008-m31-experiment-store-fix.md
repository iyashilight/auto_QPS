# ADR-008: M3.2 实验存储修复与契约对齐

**状态**: 已确认  
**日期**: 2026-08-05  
**关联**: ADR-007（M3 模型与契约补全）

## 决策

1. **experiments 表增加 start_ts/end_ts 列**（方案 A）：持久化 BacktestRun.data_range，保证 get_run/rerun 正确重建数据范围。不选方案 B（存入 engine_config）是因为 data_range 是一等字段，独立列更清晰。

2. **OptimizationRequest 补 start/end 字段**：walk-forward 滚动窗口基于请求数据范围（非硬编码），与 docs/03 §8 对齐。

3. **优化器 evaluations 存储**：evaluations 通过新增 `experiment_events` 表增量落盘（insert-only），get_run 重建 OptimizationRun 时从 experiment_events 恢复 evaluations 和 best_params。

4. **insert-only 兼容**：experiments 表的触发器改为仅拦截内容列变更，放行 status 列更新（R4 语义：实验记录只增不改，但 status 是状态机迁移的合法通道）。audit_log 表仍无条件禁止 UPDATE。应用层校验：只允许 status 迁移，禁止修改内容列。

5. **run_id 唯一性**：格式 `bt_<timestamp>_<contenthash8>_<uuid4>`（回测）和 `opt_<timestamp>_<contenthash8>_<uuid4>`（优化），uuid4 保证同秒同请求两次运行 run_id 不同。rerun 产生新 run_id，结果与原 run 逐位一致（random_seed 和数据保证确定性）。

6. **策略执行路径**：`_load_strategy` 改为 async，在引擎 async 调用链内直接 `await registry.get_version(...)`，禁止 `get_event_loop().run_until_complete()`。成功路径：registry 返回版本 + 策略文件存在 → signal_source="strategy"；加载失败回退 builtin。

7. **OOS 切分**：单次回测按 oos_ratio（默认 0.2）自动切分 IS/OOS，保证 oos_metrics 非空。

8. **cost_config 统一解析**：两个引擎统一走 BacktestRequest.model_validate(request)，删除手写默认值兜底。

9. **写库失败不静默**：引擎/优化器写库失败直接抛错（不吞掉 except Exception），R4/R5 红线保证数据可追溯。

10. **AlphaScore evaluated_at 注入**：compute_alpha_score 新增 evaluated_at 参数，测试中注入固定 clock 保证确定性。

## 新增表

### experiment_events

用于增量记录优化器 evaluations 和状态变更，insert-only：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| event_id | TEXT PRIMARY KEY | 事件唯一 ID |
| run_id | TEXT NOT NULL | 关联 experiments.run_id |
| event_type | TEXT NOT NULL | EVALUATION / STATUS_CHANGE / BEST_UPDATE |
| event_data | TEXT NOT NULL | JSON 事件数据 |
| created_at | TEXT NOT NULL | 创建时间 |
