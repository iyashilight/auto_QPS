# ADR-009: 未来函数验收口径——排序语义

**状态**: 已确认  
**日期**: 2026-08-05  
**关联**: ADR-008（M3.2 实验存储修复）、docs/07 §6（M3 验收标准）

## 背景

M3.2 复审查发现：引擎在处理前全局排序 bars，乱序输入被**静默排序**而非抛 `BacktestError`。`test_out_of_order_bar_detected` 名不副实（断言的是有序数据 SUCCEEDED），`test_internal_lookahead_check` 是空断言。docs/07 §6「未来函数拦截测试：用未来数据的策略/特征被拒绝」无有效测试，无 ADR 记录排序即无未来函数的语义决策。

## 决策

**选择方案 B（排序语义）**：正式接受「按 open_time 排序后逐 bar 处理，策略在 t 时刻仅可见 ≤t 数据 = 无未来函数」。

### 理由

1. **回测引擎的职责是确定性模拟**：引擎按时间排序后逐 bar 处理，保证策略在 t 时刻只看到 ≤t 的数据，这本身就是无未来函数的保证。乱序数据属于数据质量问题，应由 data verify（M1）负责检测和拒绝，而非回测引擎。
2. **静默排序是合理行为**：多标的 bars 合并后必须按时间排序才能正确模拟，这是事件驱动引擎的基本设计。拒绝乱序输入反而会破坏多标的场景。
3. **SDK 层保证**：策略通过 `StrategyContext` 访问数据，SDK 层保证 `ctx` 可见的历史数据最后时间戳 ≤ 当前 `bar.open_time`，这是无未来函数的核心保证。
4. **与 M1 一致**：M1 的 `validate_no_lookahead` 已在数据管线层面检测未来函数，回测引擎不需要重复此职责。

### 验收口径修订

docs/07 §6「未来函数拦截测试」改写为：
- 引擎按 open_time 升序逐 bar 处理
- 策略输入严格限于截至当前 bar（SDK 层保证）
- 乱序输入由 data verify 负责（M1 已实现）

### 测试要求

1. `test_out_of_order_bar_detected` → 重命名为 `test_out_of_order_bars_sorted_and_processed`：乱序输入与有序输入结果一致（验证排序语义）
2. 删除 `test_internal_lookahead_check` 空断言
3. 新增 `test_strategy_no_future_data_access`：真实策略 `on_bar` 中断言 `ctx` 可见的历史数据最后时间戳 ≤ 当前 `bar.open_time`

## 不选方案 A（拒绝乱序）的理由

- 方案 A 要求引擎在排序前检查单调性，但多标的 bars 合并后必然需要排序，拒绝乱序与多标的设计矛盾
- 乱序检测是数据质量职责，不应由回测引擎承担
- 增加引擎复杂度且无实际收益（排序后结果等价）
