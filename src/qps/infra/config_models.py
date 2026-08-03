"""Pydantic 配置模型（typed config）

对 configs/default.yaml 的每个顶层节定义 Pydantic 模型，
加载后执行类型校验，缺失必填项报 ConfigError。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

from qps.core.errors import ConfigError

# ── 项目配置 ──────────────────────────────────────────────


class ProjectConfig(BaseModel):
    """项目配置"""

    name: str = Field(default="autoQps", description="项目名称")
    data_dir: str = Field(default="./data", description="数据目录")
    timezone: str = Field(default="Asia/Shanghai", description="时区")


# ── 交易所配置 ────────────────────────────────────────────


class BinanceRateLimitConfig(BaseModel):
    """Binance 限频配置"""

    rest_per_sec: int = Field(default=10, description="REST 每秒请求数")
    ws_per_sec: int = Field(default=5, description="WebSocket 每秒请求数")


class BinanceAdapterConfig(BaseModel):
    """Binance 适配器配置"""

    base_url: str = Field(default="https://api.binance.com", description="REST 基础 URL")
    ws_base_url: str = Field(
        default="wss://stream.binance.com:9443", description="WebSocket 基础 URL"
    )
    testnet: bool = Field(default=False, description="是否使用测试网")
    rate_limit: BinanceRateLimitConfig = Field(
        default_factory=BinanceRateLimitConfig, description="限频配置"
    )


class ExchangeConfig(BaseModel):
    """交易所配置"""

    primary: str = Field(default="binance", description="主交易所")
    adapters: dict[str, Any] = Field(
        default_factory=lambda: {"binance": BinanceAdapterConfig().model_dump()},
        description="适配器配置",
    )


# ── 行情数据配置 ──────────────────────────────────────────


class MarketDataConfig(BaseModel):
    """行情数据配置"""

    default_interval: str = Field(default="5m", description="默认 K 线间隔")
    intervals: list[str] = Field(
        default=["1m", "5m", "15m", "1h"], description="支持的 K 线间隔"
    )
    collect_1m: bool = Field(default=False, description="是否采集 1m 数据")
    subscriptions: list[str] = Field(
        default=["BTC-USDT-PERP", "ETH-USDT-PERP"], description="订阅标的"
    )


# ── 回测配置 ──────────────────────────────────────────────


class BacktestCostConfig(BaseModel):
    """回测成本配置"""

    maker_fee: Decimal = Field(default=Decimal("0.0002"), description="Maker 费率")
    taker_fee: Decimal = Field(default=Decimal("0.0004"), description="Taker 费率")
    slippage_model: Literal["FIXED", "DEPTH"] = Field(
        default="FIXED", description="滑点模型"
    )
    slippage_bps: int = Field(default=3, description="滑点基点")
    funding_rate_source: Literal["REAL", "FIXED"] = Field(
        default="REAL", description="资金费率来源"
    )
    leverage: Decimal = Field(default=Decimal("3"), description="杠杆")
    margin_mode: str = Field(default="ISOLATED", description="保证金模式")
    liquidation_margin_rate: Decimal = Field(
        default=Decimal("0.005"), description="强平保证金率"
    )


class BacktestConfig(BaseModel):
    """回测配置"""

    default_mode: Literal["VECTORIZED", "EVENT_DRIVEN"] = Field(
        default="EVENT_DRIVEN", description="默认回测模式"
    )
    default_interval: str = Field(default="5m", description="默认 K 线间隔")
    costs: BacktestCostConfig = Field(
        default_factory=BacktestCostConfig, description="成本配置"
    )
    parallel_workers: int = Field(default=4, description="并行工作数")
    random_seed: int = Field(default=42, description="随机种子")


# ── 优化器配置 ────────────────────────────────────────────


class WalkforwardConfig(BaseModel):
    """Walk-forward 配置"""

    train_len: int = Field(default=60, description="训练窗口长度（天）")
    val_len: int = Field(default=20, description="验证窗口长度（天）")


class OptimizerConfig(BaseModel):
    """优化器配置"""

    algorithm: Literal["GRID", "RANDOM", "BAYESIAN", "GENETIC"] = Field(
        default="GRID", description="搜索算法"
    )
    budget: int = Field(default=1000, description="搜索预算")
    parallel_workers: int = Field(default=4, description="并行工作数")
    walkforward: WalkforwardConfig = Field(
        default_factory=WalkforwardConfig, description="Walk-forward 配置"
    )


# ── 风控配置 ──────────────────────────────────────────────


class RiskAccountConfig(BaseModel):
    """账户级风控配置"""

    max_total_notional: Decimal = Field(
        default=Decimal("10000"), description="最大总名义金额"
    )
    max_daily_loss: Decimal = Field(default=Decimal("500"), description="最大日亏损")
    max_total_drawdown: Decimal = Field(
        default=Decimal("0.15"), description="最大总回撤"
    )
    max_combined_leverage: Decimal = Field(
        default=Decimal("3"), description="最大组合杠杆"
    )


class RiskStrategyDefaultsConfig(BaseModel):
    """策略级风控默认配置"""

    max_position_notional: Decimal = Field(
        default=Decimal("2000"), description="最大持仓名义金额"
    )
    max_daily_loss: Decimal = Field(default=Decimal("200"), description="最大日亏损")
    signal_freq_per_min: int = Field(default=10, description="每分钟最大信号频率")
    cooldown_minutes: int = Field(default=30, description="冷却时间（分钟）")


class RiskOrderConfig(BaseModel):
    """订单级风控配置"""

    max_single_notional: Decimal = Field(
        default=Decimal("2000"), description="最大单笔名义金额"
    )
    max_price_deviation_pct: Decimal = Field(
        default=Decimal("0.02"), description="最大价格偏离百分比"
    )


class RiskMarketConfig(BaseModel):
    """市场级风控配置"""

    max_latency_ms: int = Field(default=500, description="最大延迟（毫秒）")
    freeze_on_ws_disconnect: bool = Field(
        default=True, description="WebSocket 断线时冻结"
    )


class RiskConfig(BaseModel):
    """风控配置"""

    account: RiskAccountConfig = Field(
        default_factory=RiskAccountConfig, description="账户级风控"
    )
    strategy_defaults: RiskStrategyDefaultsConfig = Field(
        default_factory=RiskStrategyDefaultsConfig, description="策略级风控默认"
    )
    order: RiskOrderConfig = Field(
        default_factory=RiskOrderConfig, description="订单级风控"
    )
    market: RiskMarketConfig = Field(
        default_factory=RiskMarketConfig, description="市场级风控"
    )


# ── 组合配置 ──────────────────────────────────────────────


class PortfolioConfig(BaseModel):
    """组合配置"""

    mode: str = Field(default="multi_strategy", description="组合模式")
    allocation_currency: str = Field(default="USDT", description="分配币种")
    strategy_weights: dict[str, Decimal] = Field(
        default_factory=dict, description="策略权重分配"
    )


# ── Paper Broker 配置 ─────────────────────────────────────


class PaperConfig(BaseModel):
    """模拟盘配置"""

    fill_latency_ms: int = Field(default=10, description="成交延迟（毫秒）")
    partial_fill: bool = Field(default=True, description="是否允许部分成交")


# ── 调度器配置 ────────────────────────────────────────────


class JobSpecConfig(BaseModel):
    """调度任务配置"""

    name: str = Field(description="任务名称")
    type: str = Field(description="任务类型")
    cron: str = Field(description="Cron 表达式")


class SchedulerConfig(BaseModel):
    """调度器配置"""

    jobs: list[JobSpecConfig] = Field(default_factory=list, description="任务列表")


# ── 日志配置 ──────────────────────────────────────────────


class LoggingConfig(BaseModel):
    """日志配置"""

    level: str = Field(default="INFO", description="日志级别")
    json_lines: bool = Field(default=True, description="是否使用 JSON Lines 格式")
    dir: str = Field(default="./data/logs", description="日志目录")


# ── 告警配置 ──────────────────────────────────────────────


class AlertsConfig(BaseModel):
    """告警配置"""

    channel: Literal["log", "desktop", "telegram"] = Field(
        default="log", description="告警通道"
    )


# ── 顶层配置 ──────────────────────────────────────────────


class AppConfig(BaseModel):
    """应用配置（全部配置的顶层容器）"""

    project: ProjectConfig = Field(default_factory=ProjectConfig, description="项目配置")
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig, description="交易所配置")
    market_data: MarketDataConfig = Field(
        default_factory=MarketDataConfig, description="行情数据配置"
    )
    backtest: BacktestConfig = Field(default_factory=BacktestConfig, description="回测配置")
    optimizer: OptimizerConfig = Field(
        default_factory=OptimizerConfig, description="优化器配置"
    )
    risk: RiskConfig = Field(default_factory=RiskConfig, description="风控配置")
    portfolio: PortfolioConfig = Field(
        default_factory=PortfolioConfig, description="组合配置"
    )
    paper: PaperConfig = Field(default_factory=PaperConfig, description="模拟盘配置")
    scheduler: SchedulerConfig = Field(
        default_factory=SchedulerConfig, description="调度器配置"
    )
    logging: LoggingConfig = Field(default_factory=LoggingConfig, description="日志配置")
    alerts: AlertsConfig = Field(default_factory=AlertsConfig, description="告警配置")


def validate_config(raw: dict[str, Any]) -> AppConfig:
    """校验原始配置字典并返回类型化 AppConfig

    Args:
        raw: 原始配置字典（来自 Config.raw_data）

    Returns:
        类型化 AppConfig

    Raises:
        ConfigError: 配置校验失败时
    """
    try:
        return AppConfig.model_validate(raw)
    except Exception as e:
        raise ConfigError(detail=f"配置校验失败: {e}", cause=e) from e
