# ADR-007: M3 模型与契约补全

## 背景

M3（回测引擎 + 优化 + 实验管理）需要新增多个模型和契约：
- `BacktestRequest`：回测请求全字段显式（docs/03 §7）
- `CostConfig`：成本配置独立模型，回测/Paper/实盘三方复用
- `AlphaScore`：信号级评分（docs/02 §3.6，ADR-006 并入）
- `OptimizationRequest`：优化请求强类型（docs/03 §8）
- `RunSummary`/`ComparisonReport`：实验查询结果（docs/03 §12）
- `FillTiming` 枚举：撮合时点配置

同时修复：
- YAML 小写枚举值与 Pydantic Literal 大写不匹配（`event_driven` vs `EVENT_DRIVEN`）
- `BacktestCostConfig` 缺少 `use_depth_snapshots` 字段
- `report_cards` DDL 缺少 `alpha_score` 列
- `OptimizationRun` 缺少 `data_version`/`feature_version` 字段

## 决策

1. **大小写规范化**：在 `validate_config` 中对 raw_data 做大写规范化（`_normalize_config_case`），YAML 保持人类可读的小写，Pydantic Literal 保持大写。此方案对用户透明，不破坏现有 YAML。

2. **CostConfig 与 BacktestCostConfig 并存**：`CostConfig`（experiment.py）是回测引擎使用的强类型模型，使用枚举值；`BacktestCostConfig`（config_models.py）是配置层模型，使用 Literal。两者字段对齐，`BacktestCostConfig` 新增 `use_depth_snapshots`。

3. **AlphaScore 模型**：六维度（opportunity/confidence/stability/market_fit/decay_rate/risk_cost），0~1 Decimal，`composite_score` 默认 null。`AlphaTrend` 和 `WeightAdjustment` 枚举放在 `constants.py`。

4. **RunSummary/ComparisonReport**：`list_runs` 返回 `RunSummary` 列表，`compare` 返回 `ComparisonReport`。端口签名保持 dict（向后兼容），但内部使用强类型模型。

5. **OptimizationRun.data_version/feature_version**：新增字段，默认 "unknown"（兼容旧数据），新写入必须显式提供（experiments 表 NOT NULL）。

6. **FillTiming 枚举**：新增 BAR_CLOSE/NEXT_BAR_OPEN/BAR_AFTER_NEXT，用于事件驱动引擎撮合时点配置。

## 变更文件

- `src/qps/core/models/experiment.py`：新增 CostConfig/BacktestRequest/AlphaScore/OptimizationRequest/RunSummary/ComparisonReport/WalkforwardConfigRequest/build_backtest_request
- `src/qps/core/constants.py`：新增 AlphaTrend/WeightAdjustment/FillTiming 枚举
- `src/qps/core/models/__init__.py`：导出更新
- `src/qps/infra/config_models.py`：BacktestCostConfig 新增 use_depth_snapshots，_normalize_config_case
- `src/qps/infra/storage/meta_db.py`：report_cards DDL 新增 alpha_score 列
- `docs/02-领域模型与数据Schema.md`：DDL alpha_score 列
- `docs/06-配置与CLI规范.md`：use_depth_snapshots 配置项
- `configs/default.yaml`：use_depth_snapshots: false

## 状态

已接受（2026-08-04，M3 T1 实现）。
