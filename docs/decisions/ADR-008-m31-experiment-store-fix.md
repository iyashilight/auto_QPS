# ADR-008: M3.1 实验存储修复与契约对齐

**状态**: 已确认  
**日期**: 2026-08-05  
**关联**: ADR-007（M3 模型与契约补全）

## 决策

1. **experiments 表增加 start_ts/end_ts 列**（方案 A）：持久化 BacktestRun.data_range，保证 get_run/rerun 正确重建数据范围。不选方案 B（存入 engine_config）是因为 data_range 是一等字段，独立列更清晰。

2. **OptimizationRequest 补 start/end 字段**：walk-forward 滚动窗口基于请求数据范围（非硬编码），与 docs/03 §8 对齐。

3. **优化器 evaluations 存储**：evaluations 完整写入 experiments.params（JSON），get_run 重建 OptimizationRun 时从 params 恢复全部字段（search_space/objective/best_params/evaluations/walkforward_report）。

4. **insert-only 兼容**：优化器状态更新仅 UPDATE status 列（R4 语义：实验记录只增不改，但 status 是状态机迁移的合法通道，触发器放行或文档化）。

5. **run_id 唯一性**：包含请求内容 hash（strategy_id + instruments + start + end + seed），避免同秒同 seed 冲突。

6. **策略执行路径**：事件驱动引擎按 strategy_id + version_no 加载策略代码（经 StrategyRegistryPort + Sandbox），在沙箱中执行 on_bar(ctx, bar)，收集 emit_intent 输出作为信号。加载失败回退到内置信号（含 OPEN + CLOSE）。向量化引擎标注 signal_source=builtin。

7. **OOS 切分**：单次回测按 oos_ratio（默认 0.2）自动切分 IS/OOS，保证 oos_metrics 非空。

8. **cost_config 统一解析**：两个引擎统一走 BacktestRequest.model_validate(request)，删除手写默认值兜底。

9. **写库失败不静默**：引擎/优化器写库失败记录 ERROR 日志，返回 FAILED 记录并携带错误信息（R4/R5 红线）。

10. **AlphaScore evaluated_at 注入**：compute_alpha_score 新增 evaluated_at 参数，测试中注入固定 clock 保证确定性。
