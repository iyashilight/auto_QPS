"""实验与评估模型（见 docs/02 第 3.6 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import (
    AlphaTrend,
    BacktestEngineMode,
    ExperimentStatus,
    FundingRateSource,
    MarginMode,
    OptimizationAlgorithm,
    SlippageModel,
    WeightAdjustment,
)
from qps.core.models.validators import validate_utc_datetime

# ── 成本配置 ──────────────────────────────────────────────


class CostConfig(BaseModel):
    """回测成本配置（docs/03 §7，回测/Paper/实盘三方复用）"""

    maker_fee: Decimal = Field(default=Decimal("0.0002"), description="Maker 费率")
    taker_fee: Decimal = Field(default=Decimal("0.0004"), description="Taker 费率")
    slippage_model: SlippageModel = Field(default=SlippageModel.FIXED, description="滑点模型")
    slippage_bps: int = Field(default=3, description="滑点基点")
    funding_rate_source: FundingRateSource = Field(
        default=FundingRateSource.REAL, description="资金费率来源"
    )
    leverage: Decimal = Field(default=Decimal("3"), description="杠杆")
    margin_mode: MarginMode = Field(default=MarginMode.ISOLATED, description="保证金模式")
    liquidation_margin_rate: Decimal = Field(
        default=Decimal("0.005"), description="强平保证金率"
    )
    use_depth_snapshots: bool = Field(
        default=False, description="是否使用订单簿深度快照（DEPTH 滑点模型需要）"
    )

    model_config = {"frozen": True}


# ── 回测请求 ──────────────────────────────────────────────


class BacktestRequest(BaseModel):
    """回测请求（全字段显式，docs/03 §7）"""

    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    params: dict[str, Any] = Field(default_factory=dict, description="策略参数")
    instruments: list[str] = Field(description="标的列表")
    interval: str = Field(description="K 线间隔")
    start: datetime = Field(description="回测开始时间（UTC）")
    end: datetime = Field(description="回测结束时间（UTC）")
    engine_mode: BacktestEngineMode = Field(
        default=BacktestEngineMode.EVENT_DRIVEN, description="引擎模式"
    )
    data_version: str = Field(description="数据版本")
    feature_version: str = Field(description="特征版本")
    cost_config: CostConfig = Field(default_factory=CostConfig, description="成本配置")
    leverage: Decimal = Field(default=Decimal("3"), description="杠杆")
    margin_mode: MarginMode = Field(default=MarginMode.ISOLATED, description="保证金模式")
    random_seed: int = Field(default=42, description="随机种子")

    model_config = {"frozen": True}

    @field_validator("start", "end")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


# ── 优化请求 ──────────────────────────────────────────────


class WalkforwardConfigRequest(BaseModel):
    """Walk-forward 配置（docs/03 §8）"""

    train_len: int = Field(default=60, description="训练窗口长度（天）")
    val_len: int = Field(default=20, description="验证窗口长度（天）")
    oos_len: int = Field(default=20, description="样本外窗口长度（天）")
    min_windows: int = Field(default=3, description="最少滚动窗口数")

    model_config = {"frozen": True}


class OptimizationRequest(BaseModel):
    """优化请求（docs/03 §8）"""

    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    search_space: dict[str, Any] = Field(description="搜索空间")
    objective: dict[str, Any] = Field(description="目标函数 {name, weights}")
    algorithm: OptimizationAlgorithm = Field(description="搜索算法")
    budget: int = Field(default=1000, description="搜索预算（评估次数）")
    walkforward_config: WalkforwardConfigRequest = Field(
        default_factory=WalkforwardConfigRequest, description="Walk-forward 配置"
    )
    parallel_workers: int = Field(default=4, description="并行工作数")
    random_seed: int = Field(default=42, description="随机种子")
    data_version: str = Field(description="数据版本")
    feature_version: str = Field(description="特征版本")
    instruments: list[str] = Field(default_factory=list, description="标的列表")
    interval: str = Field(default="5m", description="K 线间隔")
    start: datetime | None = Field(default=None, description="回测开始时间（UTC）")
    end: datetime | None = Field(default=None, description="回测结束时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("start", "end")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        return validate_utc_datetime(v)


# ── 指标 ──────────────────────────────────────────────────


class Metrics(BaseModel):
    """指标全集（报告与门禁依据）"""

    total_return: Decimal = Field(description="总收益")
    annualized_return: Decimal = Field(description="年化收益")
    max_drawdown: Decimal = Field(description="最大回撤")
    sharpe: Decimal = Field(description="夏普比率")
    win_rate: Decimal = Field(description="胜率")
    volatility: Decimal = Field(description="波动率")
    trade_count: int = Field(description="交易次数")
    profit_factor: Decimal = Field(description="盈亏比")
    calmar: Decimal = Field(default=Decimal("0"), description="Calmar 比率")
    sortino: Decimal = Field(default=Decimal("0"), description="Sortino 比率")
    avg_holding_time: Decimal | None = Field(default=None, description="平均持仓时长")
    turnover: Decimal | None = Field(default=None, description="换手率")
    max_consecutive_losses: int | None = Field(default=None, description="最大连续亏损次数")
    funding_pnl: Decimal | None = Field(default=None, description="资金费率累计盈亏（永续必填）")
    liquidation_count: int | None = Field(default=None, description="强平次数（永续必填）")
    cost_breakdown: dict[str, Any] = Field(
        default_factory=dict, description="手续费/滑点/资金费率分列"
    )
    oos_metrics: dict[str, Any] = Field(
        default_factory=dict, description="样本外指标（门禁依据）"
    )

    model_config = {"frozen": True}


# ── 数据范围 ──────────────────────────────────────────────


class DataRange(BaseModel):
    """数据时间范围"""

    start: datetime = Field(description="开始时间（UTC）")
    end: datetime = Field(description="结束时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("start", "end")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


# ── 运行记录 ──────────────────────────────────────────────


class BacktestRun(BaseModel):
    """回测运行记录"""

    run_id: str = Field(description="唯一 ID，如 bt_<timestamp>_<uuid8>")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    params: dict[str, Any] = Field(default_factory=dict, description="参数")
    data_range: DataRange = Field(description="时间范围")
    data_version: str = Field(description="数据版本")
    feature_version: str = Field(description="特征版本")
    engine_config: dict[str, Any] = Field(default_factory=dict, description="引擎配置快照")
    environment_lock: str | None = Field(default=None, description="环境锁文件 hash")
    trades_ref: str | None = Field(default=None, description="逐笔交易文件路径")
    equity_curve_ref: str | None = Field(default=None, description="权益曲线文件路径")
    metrics: Metrics | None = Field(default=None, description="指标全集")
    status: ExperimentStatus = Field(default=ExperimentStatus.PENDING, description="运行状态")
    created_at: datetime = Field(description="记录时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


class OptimizationRun(BaseModel):
    """优化运行记录"""

    run_id: str = Field(description="唯一 ID")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    search_space: dict[str, Any] = Field(default_factory=dict, description="搜索空间")
    objective: dict[str, Any] = Field(default_factory=dict, description="目标函数")
    algorithm: OptimizationAlgorithm = Field(description="搜索算法")
    evaluations: list[dict[str, Any]] = Field(default_factory=list, description="评估记录")
    best_params: dict[str, Any] | None = Field(default=None, description="最优参数")
    best_metrics: Metrics | None = Field(default=None, description="最优指标")
    walkforward_report: dict[str, Any] | None = Field(default=None, description="Walk-forward 报告")
    data_version: str = Field(default="unknown", description="数据版本")
    feature_version: str = Field(default="unknown", description="特征版本")
    status: ExperimentStatus = Field(default=ExperimentStatus.PENDING, description="运行状态")
    created_at: datetime = Field(description="记录时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


# ── 门禁与报告 ────────────────────────────────────────────


class GateResult(BaseModel):
    """门禁结果"""

    gate_name: str = Field(description="门禁名称")
    passed: bool = Field(description="是否通过")
    detail: str | None = Field(default=None, description="详情")

    model_config = {"frozen": True}


class AlphaScore(BaseModel):
    """Alpha Score 信号级评分（docs/02 §3.6，ADR-006 并入 M3）"""

    score_id: str = Field(description="唯一 ID")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    evaluated_at: datetime = Field(description="评估时间（UTC）")
    run_id: str = Field(description="来源回测/优化运行 ID（R5 可追溯）")
    opportunity: Decimal = Field(ge=0, le=1, description="机会频率 0~1")
    confidence: Decimal = Field(ge=0, le=1, description="置信度 0~1")
    stability: Decimal = Field(ge=0, le=1, description="稳定性 0~1")
    market_fit: Decimal = Field(ge=0, le=1, description="市场适配度 0~1")
    decay_rate: Decimal = Field(ge=0, le=1, description="衰减率 0~1（0=无衰减）")
    risk_cost: Decimal = Field(ge=0, le=1, description="风险成本 0~1（0=低成本）")
    composite_score: Decimal | None = Field(
        default=None, ge=0, le=1, description="综合评分（公式可配置，默认 null）"
    )
    trend: AlphaTrend = Field(default=AlphaTrend.STABLE, description="趋势")
    recommended_weight_adjustment: WeightAdjustment = Field(
        default=WeightAdjustment.MAINTAIN, description="推荐权重调整"
    )

    model_config = {"frozen": True}

    @field_validator("evaluated_at")
    @classmethod
    def _validate_evaluated_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


class ReportCard(BaseModel):
    """报告卡（run_id + strategy_id + version_no）"""

    run_id: str = Field(description="实验运行 ID")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    metrics: Metrics | None = Field(default=None, description="指标")
    gate_results: list[GateResult] = Field(default_factory=list, description="门禁结果")
    alpha_score: AlphaScore | None = Field(default=None, description="Alpha Score 评分")
    conclusion: str | None = Field(default=None, description="结论")

    model_config = {"frozen": True}


# ── 实验查询结果 ──────────────────────────────────────────


class RunSummary(BaseModel):
    """运行摘要（list_runs 返回）"""

    run_id: str = Field(description="运行 ID")
    run_type: str = Field(description="BACKTEST / OPTIMIZATION")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(description="策略版本号")
    status: ExperimentStatus = Field(description="运行状态")
    created_at: datetime = Field(description="创建时间（UTC）")
    total_return: Decimal | None = Field(default=None, description="总收益")
    sharpe: Decimal | None = Field(default=None, description="夏普比率")
    max_drawdown: Decimal | None = Field(default=None, description="最大回撤")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)


class ComparisonReport(BaseModel):
    """比较报告（compare 返回，docs/03 §12）"""

    run_ids: list[str] = Field(description="比较的运行 ID 列表")
    metric_names: list[str] = Field(description="指标名列表")
    rows: list[dict[str, Any]] = Field(description="每行一个 run，含 OOS 列")

    model_config = {"frozen": True}


# ── 帮助函数 ──────────────────────────────────────────────


def build_backtest_request(
    strategy_id: str,
    version_no: int,
    params: dict[str, Any],
    instruments: list[str],
    interval: str,
    start: datetime,
    end: datetime,
    engine_mode: BacktestEngineMode = BacktestEngineMode.EVENT_DRIVEN,
    data_version: str = "unknown",
    feature_version: str = "unknown",
    cost_config: CostConfig | None = None,
    leverage: Decimal = Decimal("3"),
    margin_mode: MarginMode = MarginMode.ISOLATED,
    random_seed: int = 42,
) -> BacktestRequest:
    """构建回测请求（docs/03 §7，组装 dict 与端口签名衔接）

    Args:
        strategy_id: 策略 ID
        version_no: 策略版本号
        params: 策略参数
        instruments: 标的列表
        interval: K 线间隔
        start: 回测开始时间
        end: 回测结束时间
        engine_mode: 引擎模式
        data_version: 数据版本
        feature_version: 特征版本
        cost_config: 成本配置（默认使用 CostConfig()）
        leverage: 杠杆
        margin_mode: 保证金模式
        random_seed: 随机种子

    Returns:
        BacktestRequest 实例
    """
    return BacktestRequest(
        strategy_id=strategy_id,
        version_no=version_no,
        params=params,
        instruments=instruments,
        interval=interval,
        start=start,
        end=end,
        engine_mode=engine_mode,
        data_version=data_version,
        feature_version=feature_version,
        cost_config=cost_config or CostConfig(),
        leverage=leverage,
        margin_mode=margin_mode,
        random_seed=random_seed,
    )
