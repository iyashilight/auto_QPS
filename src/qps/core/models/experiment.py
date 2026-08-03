"""实验与评估模型（见 docs/02 第 3.6 节）"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field, field_validator

from qps.core.constants import ExperimentStatus, OptimizationAlgorithm
from qps.core.models.validators import validate_utc_datetime


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

class DataRange(BaseModel):
    """数据时间范围"""

    start: datetime = Field(description="开始时间（UTC）")
    end: datetime = Field(description="结束时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("start", "end")
    @classmethod
    def _validate_datetime_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

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
    status: ExperimentStatus = Field(default=ExperimentStatus.PENDING, description="运行状态")
    created_at: datetime = Field(description="记录时间（UTC）")

    model_config = {"frozen": True}

    @field_validator("created_at")
    @classmethod
    def _validate_created_at_timezone(cls, v: datetime) -> datetime:
        return validate_utc_datetime(v)

class GateResult(BaseModel):
    """门禁结果"""

    gate_name: str = Field(description="门禁名称")
    passed: bool = Field(description="是否通过")
    detail: str | None = Field(default=None, description="详情")

    model_config = {"frozen": True}

class ReportCard(BaseModel):
    """报告卡（run_id + strategy_id + version_no）"""

    run_id: str = Field(description="实验运行 ID")
    strategy_id: str = Field(description="策略 ID")
    version_no: int = Field(ge=1, description="策略版本号")
    metrics: Metrics | None = Field(default=None, description="指标")
    gate_results: list[GateResult] = Field(default_factory=list, description="门禁结果")
    conclusion: str | None = Field(default=None, description="结论")

    model_config = {"frozen": True}
