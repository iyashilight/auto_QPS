"""指标计算与报告（见 docs/02 §3.6）

实现 Metrics 全字段、ReportCard 生成、Alpha Score 信号级评分。
金额一律 Decimal，禁止 float 累计盈亏（R8）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from qps.core.constants import AlphaTrend, WeightAdjustment
from qps.core.models.experiment import (
    AlphaScore,
    GateResult,
    Metrics,
    ReportCard,
)

# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class TradeRecord:
    """交易记录"""

    instrument_id: str
    side: str  # LONG / SHORT
    action: str  # OPEN / CLOSE
    quantity: Decimal
    price: Decimal
    timestamp: datetime
    fee: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    funding: Decimal = Decimal("0")
    pnl: Decimal = Decimal("0")  # 平仓盈亏
    is_liquidation: bool = False


@dataclass
class EquityPoint:
    """权益曲线点"""

    timestamp: datetime
    equity: Decimal  # 总权益 = 现金 + 未实现盈亏
    cash: Decimal
    unrealized_pnl: Decimal


# ── 指标计算 ──────────────────────────────────────────────

_RISK_FREE_RATE = Decimal("0")


def compute_metrics(
    trades: list[TradeRecord],
    equity_curve: list[EquityPoint],
    initial_equity: Decimal,
    oos_trades: list[TradeRecord] | None = None,
    oos_equity_curve: list[EquityPoint] | None = None,
) -> Metrics:
    """从交易记录和权益曲线计算 Metrics 全字段

    Args:
        trades: 交易记录列表
        equity_curve: 权益曲线点列表
        initial_equity: 初始权益
        oos_trades: 样本外交易记录
        oos_equity_curve: 样本外权益曲线

    Returns:
        Metrics 实例
    """
    if not equity_curve or initial_equity <= 0:
        return _empty_metrics()

    final_equity = equity_curve[-1].equity
    total_return = (final_equity - initial_equity) / initial_equity

    days = _trading_days(equity_curve)
    annualized_return = _annualized_return(total_return, days)
    max_dd = _max_drawdown(equity_curve)

    close_trades = [t for t in trades if t.action == "CLOSE"]
    trade_count = len(close_trades)
    winning = [t for t in close_trades if t.pnl > 0]
    losing = [t for t in close_trades if t.pnl < 0]
    win_rate = Decimal(str(len(winning) / trade_count)) if trade_count > 0 else Decimal("0")

    daily_returns = _compute_daily_returns(equity_curve)
    volatility = _annualized_volatility(daily_returns)
    sharpe = _sharpe_ratio(daily_returns, _RISK_FREE_RATE)

    gross_profit: Decimal = sum(t.pnl for t in winning)  # type: ignore[assignment]
    gross_loss: Decimal = abs(sum(t.pnl for t in losing))  # type: ignore[assignment]
    profit_factor: Decimal = gross_profit / gross_loss if gross_loss > 0 else (
        Decimal("999") if gross_profit > 0 else Decimal("0")
    )

    calmar = annualized_return / max_dd if max_dd > 0 else Decimal("0")
    sortino = _sortino_ratio(daily_returns, _RISK_FREE_RATE)
    avg_hold = _avg_holding_time(trades)
    turnover = _compute_turnover(trades, initial_equity)
    max_consec = _max_consecutive_losses(close_trades)

    funding_pnl: Decimal = sum(t.funding for t in trades)  # type: ignore[assignment]
    liquidation_count: int = sum(1 for t in trades if t.is_liquidation)

    cost_breakdown: dict[str, Any] = {
        "fees": str(sum(t.fee for t in trades)),
        "slippage": str(sum(t.slippage for t in trades)),
        "funding": str(sum(t.funding for t in trades)),
    }

    oos_metrics: dict[str, Any] = {}
    if oos_trades is not None and oos_equity_curve is not None and len(oos_equity_curve) > 1:
        oos_metrics = _compute_oos_metrics(oos_trades, oos_equity_curve, initial_equity)

    return Metrics(
        total_return=total_return,
        annualized_return=annualized_return,
        max_drawdown=max_dd,
        sharpe=sharpe,
        win_rate=win_rate,
        volatility=volatility,
        trade_count=trade_count,
        profit_factor=profit_factor,
        calmar=calmar,
        sortino=sortino,
        avg_holding_time=avg_hold,
        turnover=turnover,
        max_consecutive_losses=max_consec,
        funding_pnl=funding_pnl,
        liquidation_count=liquidation_count,
        cost_breakdown=cost_breakdown,
        oos_metrics=oos_metrics,
    )


def generate_report_card(
    run_id: str,
    strategy_id: str,
    version_no: int,
    metrics: Metrics,
) -> ReportCard:
    """生成 ReportCard（含 gate_results 骨架）"""
    gate_results: list[GateResult] = []

    # draft→bt 门禁（IS 指标）
    bt_passed = (
        metrics.sharpe > Decimal("0")
        and metrics.max_drawdown < Decimal("0.3")
        and metrics.trade_count >= 10
    )
    gate_results.append(GateResult(
        gate_name="draft→bt",
        passed=bt_passed,
        detail=f"sharpe={metrics.sharpe:.2f}, max_dd={metrics.max_drawdown:.4f}, trades={metrics.trade_count}",
    ))

    # bt→validated 门禁（OOS 指标）
    oos = metrics.oos_metrics
    if oos:
        oos_sharpe = Decimal(str(oos.get("sharpe", "0")))
        oos_max_dd = Decimal(str(oos.get("max_drawdown", "1")))
        validated_passed = oos_sharpe > Decimal("0") and oos_max_dd < Decimal("0.3")
        gate_results.append(GateResult(
            gate_name="bt→validated",
            passed=validated_passed,
            detail=f"oos_sharpe={oos_sharpe:.2f}, oos_max_dd={oos_max_dd:.4f}",
        ))
    else:
        gate_results.append(GateResult(
            gate_name="bt→validated",
            passed=False,
            detail="无 OOS 数据",
        ))

    if all(g.passed for g in gate_results):
        conclusion = "PASS"
    elif gate_results[0].passed:
        conclusion = "IS_PASS_OOS_FAIL"
    else:
        conclusion = "FAIL"

    return ReportCard(
        run_id=run_id,
        strategy_id=strategy_id,
        version_no=version_no,
        metrics=metrics,
        gate_results=gate_results,
        conclusion=conclusion,
    )


def compute_alpha_score(
    run_id: str,
    strategy_id: str,
    version_no: int,
    metrics: Metrics,
) -> AlphaScore:
    """计算 Alpha Score 六维度（信号级，ADR-006 并入 M3）

    数据来源限定回测输出，无 LLM/向量/外部依赖。
    """
    # opportunity: 交易频率
    daily_freq = Decimal(str(metrics.trade_count)) / Decimal("252") if metrics.trade_count > 0 else Decimal("0")
    opportunity = min(Decimal("1"), daily_freq / Decimal("2"))

    # confidence: 基于胜率
    confidence = max(Decimal("0"), min(Decimal("1"), metrics.win_rate * Decimal("2")))

    # stability: 基于最大回撤
    stability = max(Decimal("0"), min(Decimal("1"), Decimal("1") - metrics.max_drawdown * Decimal("2")))

    # market_fit: M3 默认中性值（Regime detection 是 M4）
    market_fit = Decimal("0.5")

    # decay_rate: 基于收益衰减
    decay_rate = max(Decimal("0"), min(Decimal("1"), Decimal("1") - abs(metrics.annualized_return)))

    # risk_cost: 基于风险指标
    risk_cost = max(Decimal("0"), min(Decimal("1"), metrics.max_drawdown + metrics.volatility / Decimal("2")))

    return AlphaScore(
        score_id=f"alpha_{run_id}",
        strategy_id=strategy_id,
        version_no=version_no,
        evaluated_at=datetime.now(UTC),
        run_id=run_id,
        opportunity=opportunity,
        confidence=confidence,
        stability=stability,
        market_fit=market_fit,
        decay_rate=decay_rate,
        risk_cost=risk_cost,
        composite_score=None,  # M3 默认 null
        trend=AlphaTrend.STABLE,
        recommended_weight_adjustment=WeightAdjustment.MAINTAIN,
    )


# ── 内部计算方法 ──────────────────────────────────────────


def _empty_metrics() -> Metrics:
    """空指标"""
    return Metrics(
        total_return=Decimal("0"),
        annualized_return=Decimal("0"),
        max_drawdown=Decimal("0"),
        sharpe=Decimal("0"),
        win_rate=Decimal("0"),
        volatility=Decimal("0"),
        trade_count=0,
        profit_factor=Decimal("0"),
    )


def _trading_days(equity_curve: list[EquityPoint]) -> Decimal:
    """计算交易天数"""
    if len(equity_curve) < 2:
        return Decimal("1")
    delta = (equity_curve[-1].timestamp - equity_curve[0].timestamp).total_seconds()
    return Decimal(str(max(delta / 86400, 1)))


def _annualized_return(total_return: Decimal, days: Decimal) -> Decimal:
    """计算年化收益"""
    if days <= 0 or total_return <= Decimal("-1"):
        return Decimal("0")
    try:
        base = float(1 + total_return)
        exp = float(Decimal("365.25") / days)
        if base <= 0:
            return Decimal("-1")
        result = base ** exp - 1
        return Decimal(str(round(result, 8)))
    except (ValueError, OverflowError):
        return Decimal("0")


def _max_drawdown(equity_curve: list[EquityPoint]) -> Decimal:
    """计算最大回撤"""
    if not equity_curve:
        return Decimal("0")
    peak = equity_curve[0].equity
    max_dd = Decimal("0")
    for point in equity_curve:
        if point.equity > peak:
            peak = point.equity
        if peak > 0:
            dd = (peak - point.equity) / peak
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _compute_daily_returns(equity_curve: list[EquityPoint]) -> list[Decimal]:
    """计算日收益率序列"""
    if len(equity_curve) < 2:
        return []
    daily_equity: dict[str, Decimal] = {}
    for point in equity_curve:
        day_key = point.timestamp.strftime("%Y-%m-%d")
        daily_equity[day_key] = point.equity
    sorted_days = sorted(daily_equity.keys())
    returns: list[Decimal] = []
    for i in range(1, len(sorted_days)):
        prev = daily_equity[sorted_days[i - 1]]
        curr = daily_equity[sorted_days[i]]
        if prev > 0:
            returns.append((curr - prev) / prev)
    return returns


def _annualized_volatility(daily_returns: list[Decimal]) -> Decimal:
    """计算年化波动率"""
    if len(daily_returns) < 2:
        return Decimal("0")
    floats = [float(r) for r in daily_returns]
    mean = sum(floats) / len(floats)
    variance = sum((r - mean) ** 2 for r in floats) / (len(floats) - 1)
    daily_std = math.sqrt(variance) if variance > 0 else 0
    return Decimal(str(round(daily_std * math.sqrt(252), 8)))


def _sharpe_ratio(daily_returns: list[Decimal], risk_free: Decimal) -> Decimal:
    """计算夏普比率"""
    if len(daily_returns) < 2:
        return Decimal("0")
    floats = [float(r) for r in daily_returns]
    mean = sum(floats) / len(floats)
    rf = float(risk_free) / 252
    variance = sum((r - mean) ** 2 for r in floats) / (len(floats) - 1)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return Decimal("0")
    return Decimal(str(round((mean - rf) / std * math.sqrt(252), 8)))


def _sortino_ratio(daily_returns: list[Decimal], risk_free: Decimal) -> Decimal:
    """计算 Sortino 比率"""
    if len(daily_returns) < 2:
        return Decimal("0")
    floats = [float(r) for r in daily_returns]
    mean = sum(floats) / len(floats)
    rf = float(risk_free) / 252
    downside = [min(0, r - rf) ** 2 for r in floats]
    downside_var = sum(downside) / len(downside) if downside else 0
    downside_std = math.sqrt(downside_var) if downside_var > 0 else 0
    if downside_std == 0:
        return Decimal("0")
    return Decimal(str(round((mean - rf) / downside_std * math.sqrt(252), 8)))


def _avg_holding_time(trades: list[TradeRecord]) -> Decimal | None:
    """计算平均持仓时长（秒）"""
    open_map: dict[str, datetime] = {}
    holding_times: list[float] = []
    for t in trades:
        key = t.instrument_id
        if t.action == "OPEN":
            open_map[key] = t.timestamp
        elif t.action == "CLOSE" and key in open_map:
            delta = (t.timestamp - open_map[key]).total_seconds()
            if delta > 0:
                holding_times.append(delta)
            del open_map[key]
    if not holding_times:
        return None
    return Decimal(str(round(sum(holding_times) / len(holding_times), 2)))


def _compute_turnover(trades: list[TradeRecord], initial_equity: Decimal) -> Decimal | None:
    """计算换手率"""
    if initial_equity <= 0:
        return None
    total_notional = sum(t.quantity * t.price for t in trades)
    return total_notional / initial_equity


def _max_consecutive_losses(close_trades: list[TradeRecord]) -> int | None:
    """计算最大连续亏损次数"""
    if not close_trades:
        return None
    max_streak = 0
    current_streak = 0
    for t in close_trades:
        if t.pnl < 0:
            current_streak += 1
            max_streak = max(max_streak, current_streak)
        else:
            current_streak = 0
    return max_streak


def _compute_oos_metrics(
    oos_trades: list[TradeRecord],
    oos_equity_curve: list[EquityPoint],
    initial_equity: Decimal,
) -> dict[str, Any]:
    """计算样本外指标"""
    oos_m = compute_metrics(oos_trades, oos_equity_curve, initial_equity)
    return {
        "total_return": str(oos_m.total_return),
        "annualized_return": str(oos_m.annualized_return),
        "max_drawdown": str(oos_m.max_drawdown),
        "sharpe": str(oos_m.sharpe),
        "win_rate": str(oos_m.win_rate),
        "volatility": str(oos_m.volatility),
        "trade_count": str(oos_m.trade_count),
        "profit_factor": str(oos_m.profit_factor),
        "funding_pnl": str(oos_m.funding_pnl) if oos_m.funding_pnl is not None else None,
        "liquidation_count": str(oos_m.liquidation_count) if oos_m.liquidation_count is not None else None,
    }
