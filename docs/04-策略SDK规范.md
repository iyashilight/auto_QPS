# 04 策略 SDK 规范

> 适用：策略开发者 + 系统开发者。本文件定义「策略插件」的目录结构、元数据、类契约与意图输出规范。
> 原则：策略在回测与实盘运行**同一套代码**；策略不接触任何交易能力；所有策略结构统一（可机器生成）。
> 最后更新：2026-08-02

## 1. 策略包结构（标准形态）

每个策略一个目录，位于 `strategies/<strategy_id>/`：

```
strategies/trend_breakout/
├── strategy.yaml        # 元数据 + 参数 schema + 依赖声明（必填）
├── __init__.py          # 导出 Strategy 类（必填）
├── strategy.py          # 策略实现（开发者编写）
├── features.py          # 可选：策略私有特征函数（注册到 Feature Pipeline）
└── tests/
    ├── test_smoke.py    # 冒烟回测用例（必填）
    └── test_params.py   # 参数 schema 校验用例
```

约束：

- `strategy_id` 与目录名一致（小写 snake_case）；
- `__init__.py` 必须导出且只导出 `Strategy` 类；
- 禁止策略目录内出现：配置文件外的硬编码路径、网络地址、密钥、数据文件；
- 策略代码禁止 import 系统内部模块（`qps.trading.*`、`qps.broker.*`），只允许 import `qps.core.models` 与 SDK 暴露的类型。

## 2. strategy.yaml 规范

字段：

| 字段 | 必填 | 说明/示例 |
| --- | --- | --- |
| strategy_id | 是 | `trend_breakout` |
| name | 是 | 显示名（中文允许） |
| style | 是 | `TREND` / `MEAN_REVERSION` / `ARBITRAGE` / `GRID` / `AI` / `OTHER` |
| version_note | 否 | 版本说明 |
| instruments | 是 | 默认标的目标，如 `["BTC-USDT-PERP"]` |
| interval | 是 | 默认运行频率 `5m` |
| asset_class_support | 是 | `["SPOT"]` / `["PERPETUAL"]` / `["SPOT","PERPETUAL"]` |
| params_schema | 是 | JSON Schema（见 4 节） |
| features | 否 | 依赖的公共特征名列表（Feature Store 提供） |
| private_features | 否 | 私有特征声明 `[{name, input_window, params}]` |
| runtime_constraints | 否 | 静态检查覆盖项（默认全开，见 6 节） |

示例（完整形态，开发者可照此写）：

```yaml
strategy_id: trend_breakout
name: 通道突破趋势策略
style: TREND
instruments: ["BTC-USDT-PERP"]
interval: "5m"
asset_class_support: ["PERPETUAL"]

params_schema:
  type: object
  required: [lookback, atr_mult, position_size]
  properties:
    lookback:
      type: integer
      minimum: 20
      maximum: 200
      default: 50
    atr_mult:
      type: number
      minimum: 1.0
      maximum: 5.0
      default: 2.0
    position_size:
      type: number
      minimum: 0.01
      maximum: 0.5
      default: 0.1

features: ["atr_14", "ema_50"]

runtime_constraints:
  allow_network: false
  allow_file_io: false
  allow_threads: false
  allow_subprocess: false
  allow_imports: ["numpy", "polars", "qps.core.models"]
```

## 3. 策略类契约

策略类必须继承 SDK 提供的 `BaseStrategy`（位置 `qps.core.sdk.BaseStrategy`），实现以下回调：

```python
from qps.core.sdk import BaseStrategy, StrategyContext
from qps.core.models import Bar, Tick, FundingRate, OpenInterest, TradingIntent

class TrendBreakout(BaseStrategy):
    def initialize(self, ctx: StrategyContext) -> None:
        """初始化：读取 ctx.params 与静态数据，禁止 IO。"""
        ...

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """每个 K 线回调（回测与实盘同语义：回测=bar 收盘后，实盘=最新聚合 bar）。"""
        ...

    def on_tick(self, ctx: StrategyContext, tick: Tick) -> None:
        """可选：逐笔回调（默认不实现）。"""
        ...

    def on_funding(self, ctx: StrategyContext, funding: FundingRate) -> None:
        """可选：资金费率回调（永续策略建议实现）。"""
        ...

    def on_oi(self, ctx: StrategyContext, oi: OpenInterest) -> None:
        """可选：持仓量回调。"""
        ...
```

行为约束：

1. **纯计算**：回调内禁止网络、文件、进程、线程、`time.sleep`；
2. **无副作用的持久化**：跨回调状态必须通过 `ctx.state`（由框架按策略实例持久化）；禁止模块级/类级全局状态（回测并行会出错）；
3. **确定性**：相同输入 + 相同参数必须产生相同意图序列；禁止依赖真实时钟（回测时间由框架注入）；
4. **输出唯一通道**：通过 `ctx.emit_intent(...)` 输出意图；禁止 return 信号、禁止直接构造 Order；
5. **只读市场数据**：`ctx` 提供的行情/特征/持仓都是只读视图，禁止修改；
6. **参数只读**：`ctx.params` 不可变，策略运行期禁止修改参数。

## 4. StrategyContext 能力表

| 能力 | 签名（形态） | 说明 |
| --- | --- | --- |
| 参数 | `ctx.params -> dict` | 当前部署的参数快照（不可变） |
| 行情 | `ctx.latest_bar(instrument_id) -> Bar | None` | 最新已处理 K 线 |
| 特征 | `ctx.feature(instrument_id, name) -> float | None` | 读取 Feature Pipeline 最新值 |
| 历史 | `ctx.history(instrument_id, n) -> list[Bar]` | 最近 n 根 K 线（由框架提供，非策略自存） |
| 持仓视图 | `ctx.positions -> list[PositionView]` | 当前部署相关持仓（只读） |
| 资金视图 | `ctx.cash -> Decimal` | 可用资金（只读） |
| 自身状态 | `ctx.state -> MutableMapping` | 跨回调状态（框架持久化，仅本部署可见） |
| 输出意图 | `ctx.emit_intent(intent: TradingIntent) -> None` | 唯一输出通道（框架校验后进入 Signal Engine） |
| 日志 | `ctx.log(level, message, **extra)` | 策略日志（统一格式、脱敏） |

实现要点（系统开发者）：

- `emit_intent` 内部：补全 `intent_id / strategy_id / deployment_id / valid_until`（若策略未填），执行字段白名单校验，随后发事件 `INTENT_CREATED`；
- `ctx.state` 序列化为 JSON 持久化（内存态 + 落库），重启后恢复；
- `ctx.history` 必须与 `on_bar` 收到的 bar 同源（同一 data_version/feature_version）。

## 5. TradingIntent 输出规范

策略作者只需填写核心字段，框架补全上下文：

```json
{
  "direction": "long",
  "action": "open",
  "quantity": 0.05,
  "order_type": "market",
  "reason_tags": ["channel_breakout", "trend"]
}
```

框架补全后完整形态（开发者实现 `emit_intent` 时输出）：

```json
{
  "intent_id": "6f1c2a1e-9b3d-4f7e-8a2c-1d5e9b7c0a11",
  "strategy_id": "trend_breakout",
  "version_no": 3,
  "deployment_id": "dep_20260802_001",
  "instrument_id": "BTC-USDT-PERP",
  "direction": "long",
  "action": "open",
  "quantity": 0.05,
  "notional": null,
  "order_type": "market",
  "limit_price": null,
  "valid_until": "2026-08-02T12:05:00Z",
  "intent_priority": "NORMAL",
  "reason_tags": ["channel_breakout", "trend"],
  "raw_payload": {}
}
```

校验规则（Signal Engine 强制）：

- `direction` + `action` 合法组合：`LONG/SHORT` 与 `OPEN/INCREASE/REDUCE/CLOSE/FLIP` 全组合允许；`FLAT` 仅与 `CLOSE` 组合；
- `quantity` 与 `notional` 二选一，同时为空或同时非空 → 拒绝；
- `quantity > 0` 且不超 `ctx` 可用视图的量级（超限由 Portfolio Manager 处理，不在此拒绝）；
- `order_type=LIMIT` 时 `limit_price` 必填且 > 0；
- `valid_until` 默认 = 当前时间 + 策略配置的默认有效期（如 10 分钟），过期意图作废；
- 未知字段 → 拒绝（白名单模式）；`reason_tags` 限 `[a-z0-9_]+`。

## 6. 静态检查规则（运行时强制）

策略版本入库与启动时执行：

| 检查项 | 规则 |
| --- | --- |
| import 白名单 | 只允许 `numpy`、`polars`、`qps.core.models`、`qps.core.sdk` 及标准库安全子集（`math`, `statistics`, `datetime`, `typing`, `dataclasses`） |
| 网络 | 禁止 `socket`、`http`、`websocket`、`requests`、`httpx`、`aiohttp` 相关 import |
| 文件 | 禁止 `open()`、`pathlib` 写操作、`os` 写操作 |
| 进程/线程 | 禁止 `threading`、`multiprocessing`、`subprocess`、`os.fork` |
| 执行 | 禁止 `eval`、`exec`、`compile`、`__import__` 动态调用 |
| 交易句柄 | 禁止 import 任何 `qps.trading.*`、`qps.broker.*`（源码 AST 检查） |
| 睡眠/时钟 | 禁止 `time.sleep`；`time.time` 使用告警（应使用 ctx 注入时间） |

实现位置：`qps/research/registry.py` 或独立 `qps/core/sandbox.py`（AST 静态分析 + import 钩子）。启动时检查失败 = 启动失败并返回原因，不允许带病运行。

## 7. 参数 schema 规范

- 参数通过 `params_schema`（JSON Schema draft-07 子集）声明：类型、范围、默认值、必填；
- 回测/优化器直接消费 schema 生成搜索空间（grid 需 `enum` 或 `minimum/maximum/step` 扩展字段）；
- 参数变更 = 新版本策略（`version_no` 递增）；禁止热改参数覆盖 schema；
- 数值参数建议加 `step`（扩展关键字）供网格搜索使用，示例：

```json
{"lookback": {"type": "integer", "minimum": 20, "maximum": 200, "default": 50, "step": 10}}
```

## 8. 回测与实盘一致性要求（SDK 层面）

| 一致性项 | 要求 |
| --- | --- |
| 代码同一份 | 回测与实盘加载同一 StrategyVersion，禁止回测专用分支 |
| 数据同管线 | 特征必须来自 Feature Pipeline（回测版本化数据 + 实盘实时计算同实现） |
| 时间语义 | `on_bar` 在回测=bar 收盘后、实盘=聚合完成时；框架保证「截至 t」数据 |
| 意图出口相同 | 回测中 `emit_intent` 同样经过 Signal Engine 校验（用回测版校验器） |
| 成本模型同源 | 回测与 Paper Broker 共用同一 `CostConfig` 与保证金/强平实现 |

## 9. SDK 完成验收（系统开发者）

1. `strategies/demo_mean_reversion/` 示例策略（含 yaml + 实现 + 冒烟测试）可跑通「入库 → 回测 → 出报告」；
2. 静态检查能拦截：网络 import、文件写、`time.sleep`、交易模块 import 四类违规（测试覆盖）；
3. `emit_intent` 白名单校验单测覆盖所有拒绝分支；
4. 回测与实盘共用代码的示例在 Paper Broker 上连续运行 24h 无状态丢失。

---

*本文件为策略 SDK 基线；字段与回调新增必须同步更新 03 文档对应端口。*
