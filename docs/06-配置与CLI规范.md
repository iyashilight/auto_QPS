# 06 配置与 CLI 规范

> 适用：系统开发者。定义配置文件结构、环境变量与 CLI 命令集。
> 最后更新：2026-08-02

## 1. 配置分层

加载顺序（后者覆盖前者）：

```
configs/default.yaml  →  configs/<mode>.yaml（paper/production）  →  环境变量（QPS_*）  →  运行时参数
```

## 2. 默认配置文件结构（configs/default.yaml）

```yaml
project:
  name: autoQps
  data_dir: ./data
  timezone: Asia/Shanghai

exchange:
  primary: binance
  adapters:
    binance:
      base_url: https://api.binance.com
      ws_base_url: wss://stream.binance.com:9443
      testnet: false
      rate_limit:
        rest_per_sec: 10
        ws_per_sec: 5

market_data:
  default_interval: 5m
  intervals: [1m, 5m, 15m, 1h]
  collect_1m: false            # 1m 按需采集
  subscriptions: [BTC-USDT-PERP, ETH-USDT-PERP]

backtest:
  default_mode: event_driven
  default_interval: 5m
  costs:
    maker_fee: 0.0002
    taker_fee: 0.0004
    slippage_model: fixed
    slippage_bps: 3
    funding_rate_source: real
    leverage: 3
    margin_mode: ISOLATED
    liquidation_margin_rate: 0.005
    use_depth_snapshots: false
  parallel_workers: 4
  random_seed: 42

optimizer:
  algorithm: grid
  budget: 1000
  parallel_workers: 4
  walkforward:
    train_len: 60
    val_len: 20

risk:
  account:
    max_total_notional: 10000
    max_daily_loss: 500
    max_total_drawdown: 0.15
    max_combined_leverage: 3
  mode:                        # 风险模式状态机（架构 §8.5）
    hysteresis:
      observe_minutes: 120     # 事件撤回后的观察窗口
      require_volatility_drop: true   # 需波动率回落才逐级恢复
      recover_stepwise: true   # DEFENSIVE → CAUTION → NORMAL
    news_events:               # 事件 → 模式/动作 静态映射表（确定性入口）
      - type: EXCHANGE_HACK
        min_severity: 7
        min_confidence: 0.7
        mode: DEFENSIVE
        actions: [disable_new_positions, leverage_limit:1]
      - type: EXCHANGE_STATUS
        min_severity: 6
        min_confidence: 0.8
        mode: CAUTION
        actions: [disable_new_positions]
      - type: REGULATORY_RISK
        min_severity: 6
        min_confidence: 0.6
        mode: CAUTION
        actions: [disable_new_positions]
      - type: MAINTENANCE
        min_severity: 4
        min_confidence: 0.8
        mode: NORMAL
        actions: []
  strategy_defaults:
    max_position_notional: 2000
    max_daily_loss: 200
    signal_freq_per_min: 10
    cooldown_minutes: 30
  order:
    max_single_notional: 2000
    max_price_deviation_pct: 0.02
  market:
    max_latency_ms: 500
    freeze_on_ws_disconnect: true

news:                          # 规则版新闻管线（M5/M6，无 LLM）
  enabled: false               # M5 开启（Paper 模拟），M6 接真实源
  sources:
    - name: binance_announcement
      kind: L1_OFFICIAL
      poll_seconds: 30
    - name: coindesk_rss
      kind: L2_RSS
      poll_seconds: 90
    - name: cointelegraph_rss
      kind: L2_RSS
      poll_seconds: 90
  confidence_priors:           # 分层置信度先验（架构 §2.7.4）
    L1_OFFICIAL: 0.9
    L2_RSS: 0.6
    L3_SOCIAL: 0.3
  cross_source_bonus: 0.1      # 多源一致加成（封顶 1.0）
  latency_target_ms: 60000     # 事件 → 风险模式切换延迟目标 < 1min

portfolio:
  mode: multi_strategy
  allocation_currency: USDT
  # 示例：等权分配（可后续改人工指定）
  strategy_weights:
    trend_breakout: 0.5
    grid_btc: 0.5

paper:
  fill_latency_ms: 10
  partial_fill: true

scheduler:
  jobs:
    - name: daily_backfill
      type: data.collect
      cron: "0 */6 * * *"
    - name: daily_reconcile
      type: trade.reconcile
      cron: "0 0 * * *"

logging:
  level: INFO
  json_lines: true
  dir: ./data/logs

alerts:
  channel: log           # log / desktop / telegram（预留）
```

## 3. 环境变量（密钥与覆盖）

| 变量 | 用途 |
| --- | --- |
| `QPS_MODE` | `paper` / `production`（选择覆盖配置） |
| `QPS_DATA_DIR` | 数据目录覆盖 |
| `QPS_BINANCE_API_KEY` | Binance API Key |
| `QPS_BINANCE_API_SECRET` | Binance API Secret |
| `QPS_BINANCE_TESTNET` | `true/false`（实盘适配器联调用） |
| `QPS_LOG_LEVEL` | 日志级别覆盖 |

规则：密钥只走环境变量（或系统密钥环）；配置文件中只出现占位符（如 `${QPS_BINANCE_API_KEY}`），禁止明文密钥入库。

## 4. CLI 命令集（MVP）

命令树（`qps` 为入口）：

```
qps
├── data
│   ├── collect <instrument> [--interval 5m] [--start] [--end]   # 历史数据采集
│   ├── verify <instrument> [--interval]                         # 数据质量检查
│   └── status                                                   # 数据覆盖状态
├── research
│   ├── backtest <strategy_id> [--version] [--params JSON] [--mode vectorized|event_driven] [--interval] [--range START:END]
│   ├── optimize <strategy_id> [--algorithm grid|random|bayesian] [--budget N] [--objective JSON]
│   └── list-runs [--strategy] [--type]
├── experiment
│   ├── show <run_id>
│   ├── compare <run_id1> <run_id2> [...]
│   └── rerun <run_id>
├── strategy
│   ├── create <strategy_id> --template trend|mean_reversion|grid
│   ├── list [--status]
│   ├── status <strategy_id>
│   └── promote <strategy_id> --to VALIDATED|PAPER_TRADING|PRODUCTION --evidence <ids>
├── trade
│   ├── status
│   ├── positions [--account]
│   ├── orders [--status] [--deployment]
│   ├── reconcile [--account]
│   └── deploy <strategy_id> --mode paper|production --params JSON
├── risk
│   ├── status
│   ├── mode [--account]                                   # 查看风险模式与切换历史
│   ├── freeze [--strategy <id>|--all]
│   └── unfreeze <strategy_id|all> --confirm
├── news
│   ├── inject <json> --account paper_001 [--confirm]      # 仅 Paper：注入合成新闻事件（测试链路）
│   ├── status                                             # 新闻源连接与最近事件
│   └── events [--active]                                  # 查看活动事件（CANDIDATE/CONFIRMED 未过期）
├── task
│   ├── list
│   └── cancel <task_id>
└── broker
    └── status
```

命令输出约定：

- 默认表格输出（rich/plain）；`--json` 输出机器可读 JSON（供脚本/AI 消费）；
- 退出码：`0` 成功；`1` 业务失败（含原因输出）；`2` 参数错误；`3` 风控/权限拒绝；
- 所有修改类命令（promote/freeze/unfreeze/deploy）必须二次确认（`--confirm`）并写 AuditLog；
- `qps news inject` 仅允许在 Paper 账户执行（实盘账户拒绝，退出码 3）；`risk mode` 为只读命令，HALT 恢复只能走 `unfreeze --all`（人工）；
- 未登录/密钥缺失 → 明确报错并提示设置环境变量，禁止静默降级。

## 5. CLI 示例（验收形态）

```bash
# 采集 BTC 永续 5m 历史数据
qps data collect BTC-USDT-PERP --interval 5m --start 2026-01-01 --end 2026-07-01

M1 数据与特征命令：

`qps data manifest build <dataset_type> --partitions <path[,path...]>` 默认生成 candidate/hash-only manifest；`manifest status` 会显示 `manifest_kind=candidate`、`hash_verified` 与 `verified=false`。只有带 `--formal-pit --instrument-id <id> --quality-report <ref>` 的命令会在持久化前执行 PIT schema、时间、质量报告和 lineage 验证，并以 `manifest_kind=formal_pit` 报告可用性。`qps data manifest show <manifest_id>`、`qps data manifest verify <manifest_id>`；`qps data feature register/list/show/deprecate/materialize`。命令只能通过 Manifest/Feature Registry 端口操作，输出 manifest ID、内容 hash、版本和 lineage，不以“最新文件”或文件名替代版本选择。

`qps data compute` 的 `--decision-time`，以及 `qps data feature materialize` 的 `--start`、`--end`、`--decision-time` 必须使用带 `Z` 或 `+00:00` 的 UTC ISO 8601；naive、非 UTC offset 和 `start >= end` 均以非零退出，且不得写入 manifest、registry 或 materialization。

# 事件驱动回测（PIT 参数必填）
qps research backtest trend_breakout --version 3 --mode event_driven \
  --manifest-id m_20260805_bars --decision-time 2026-08-05T12:00:00Z \
  --interval 5m --range 2026-01-01T00:00:00Z:2026-07-01T00:00:00Z --json

# 正式质量验证必须引用 manifest；省略 --manifest-id 会以非零退出
qps data verify BTC-USDT-PERP --manifest-id m_20260805_bars --json

# verify 同时校验 manifest 的 PIT schema、标的/周期覆盖和质量报告；只验证 hash 不构成研究证据

# 输出（示意）
# {"run_id":"bt_20260802_1a2b","status":"SUCCEEDED",
#  "metrics":{"total_return":0.18,"max_drawdown":0.07,"sharpe":1.9,
#             "win_rate":0.55,"volatility":0.42,"trade_count":320,
#             "funding_pnl":-12.3,"liquidation_count":0}}

# 查看风控状态
qps risk status --json
# {"account":{"frozen":false,"day_pnl":-80.5,"daily_loss_limit":500},
#  "rules_enabled":12,"breaches_today":0}
```

## 6. 实现建议

- 使用 typer 实现，命令即函数（参数自动校验、帮助文档自动生成）；
- 所有命令在 `src/qps/control/cli/` 下按域拆分（`data_cmds.py`、`research_cmds.py` 等）；
- CLI 只做「参数解析 + 调端口」，业务逻辑全部在服务模块，禁止 CLI 层写业务逻辑；
- 长任务（回测/优化/采集）异步提交并立即返回 task_id，进度用 `qps task list` 查询。

---

*本文件为配置与 CLI 基线；新增命令必须同步更新本文档。*
### M2 策略发布 CLI

`strategy check` 读取已登记 durable artifact；`strategy package` 和 `strategy release` 为修改类命令，必须 `--confirm`。Release 必须显式提供参数、环境锁、evidence、formal dataset manifest 和 feature materialization ID，依赖验证失败返回非零且不得写成功 archive。`strategy approve` 只追加审批与 AuditLog，不自动修改 Release status；`strategy show` 在输出前重新校验 archive/hash。
# M2 v0.5 Release/CLI Boundary

Release archive 必须包含逐文件 hash/size/mode 的 `release-manifest.json`。`ReleaseStore.get_release()` 每次读取重新验证代码、参数、环境、文件集合、manifest 字段和依赖 lineage；任何篡改均失败。生产 ReleaseBuilder 不提供依赖资格绕过开关；CLI e2e 必须验证成功/失败退出码、审批审计和失败无副作用。
