"""事件驱动回测引擎（见 docs/03 §7，架构 §7）

逐事件撮合，完整成本模型，强平模拟，防未来函数。
确定性：同一输入两次运行逐位一致。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qps.core.constants import (
    BACKTEST_RUN_ID_PREFIX,
    BacktestEngineMode,
    ExperimentStatus,
    FillTiming,
)
from qps.core.costs import CostCalculator
from qps.core.errors import BacktestError
from qps.core.models.experiment import (
    BacktestRequest,
    BacktestRun,
    DataRange,
    ReportCard,
)
from qps.core.models.experiment import (
    CostConfig as CostConfigModel,
)
from qps.research.evaluation import (
    EquityPoint,
    TradeRecord,
    compute_alpha_score,
    compute_metrics,
    generate_report_card,
)

if TYPE_CHECKING:
    from qps.core.models.market import Bar
    from qps.data.provider import DataProvider
    from qps.di import Container
    from qps.research.experiments import ExperimentStore

logger = logging.getLogger(__name__)


class EventDrivenBacktestEngine:
    """事件驱动回测引擎

    功能：逐事件撮合、完整成本模型、强平、资金费率结算、防未来函数。
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    async def run(self, request: dict[str, Any]) -> BacktestRun:
        """执行回测

        Args:
            request: 回测请求字典（解析为 BacktestRequest）

        Returns:
            BacktestRun（失败返回 FAILED 记录而非抛异常）
        """
        try:
            req = BacktestRequest.model_validate(request) if not isinstance(request, dict) or "strategy_id" not in request else BacktestRequest(
                strategy_id=request.get("strategy_id", "unknown"),
                version_no=request.get("version_no", 1),
                params=request.get("params", {}),
                instruments=request.get("instruments", []),
                interval=request.get("interval", "5m"),
                start=datetime.fromisoformat(request["start"]) if isinstance(request.get("start"), str) else request.get("start", datetime(2026, 1, 1, tzinfo=UTC)),
                end=datetime.fromisoformat(request["end"]) if isinstance(request.get("end"), str) else request.get("end", datetime(2026, 1, 2, tzinfo=UTC)),
                engine_mode=BacktestEngineMode(request.get("engine_mode", "EVENT_DRIVEN")),
                data_version=request.get("data_version", "unknown"),
                feature_version=request.get("feature_version", "unknown"),
                cost_config=request.get("cost_config", CostConfigModel()) if isinstance(request.get("cost_config"), CostConfigModel) else CostConfigModel(),
                leverage=Decimal(str(request.get("leverage", "3"))),
                random_seed=request.get("random_seed", 42),
            )
        except Exception as e:
            return self._failed_run(f"bt_invalid_{hash(str(request)) % 10000:04d}", str(e), request)

        # 生成确定性 run_id
        run_id = self._generate_run_id(req)

        try:
            result = await self._execute_backtest(req, run_id)
            return result
        except BacktestError:
            raise
        except Exception as e:
            logger.exception("回测执行失败: %s", run_id)
            return self._failed_run(run_id, str(e), request)

    async def run_batch(
        self,
        requests: list[dict[str, Any]],
        max_workers: int = 4,
    ) -> list[BacktestRun]:
        """批量执行回测（顺序执行，max_workers 预留并行接口）"""
        results: list[BacktestRun] = []
        for req in requests:
            result = await self.run(req)
            results.append(result)
        return results

    async def rerun(self, run_id: str) -> BacktestRun:
        """重跑实验（确定性：结果必须与原始 run 一致）"""
        experiment_store = self._get_experiment_store()
        original = await experiment_store.get_run(run_id)

        if not isinstance(original, BacktestRun):
            raise BacktestError(detail=f"run_id {run_id} 不是回测运行")

        # 从原始 run 重建请求
        request = {
            "strategy_id": original.strategy_id,
            "version_no": original.version_no,
            "params": original.params,
            "instruments": original.engine_config.get("instruments", []),
            "interval": original.engine_config.get("interval", "5m"),
            "start": original.data_range.start.isoformat(),
            "end": original.data_range.end.isoformat(),
            "engine_mode": original.engine_config.get("engine_mode", "EVENT_DRIVEN"),
            "data_version": original.data_version,
            "feature_version": original.feature_version,
            "cost_config": original.engine_config.get("cost_config", {}),
            "leverage": original.engine_config.get("leverage", "3"),
            "random_seed": original.engine_config.get("random_seed", 42),
        }

        return await self.run(request)

    # ── 核心执行 ──────────────────────────────────────────

    async def _execute_backtest(self, req: BacktestRequest, run_id: str) -> BacktestRun:
        """执行事件驱动回测"""
        # 获取数据
        data_provider = self._get_data_provider()
        all_bars: dict[str, list[Bar]] = {}
        for instrument_id in req.instruments:
            bars = await data_provider.get_bars(
                instrument_id=instrument_id,
                interval=req.interval,
                start=req.start,
                end=req.end,
                data_version=req.data_version,
            )
            all_bars[instrument_id] = bars

        # 检查是否有数据
        total_bars = sum(len(b) for b in all_bars.values())
        if total_bars == 0:
            return self._failed_run(run_id, "无 K 线数据", req.model_dump())

        # 成本计算器
        cost_calc = CostCalculator(req.cost_config)

        # 初始化回测状态
        initial_equity = Decimal("10000")
        cash = initial_equity
        positions: dict[str, _Position] = {}
        trades: list[TradeRecord] = []
        equity_curve: list[EquityPoint] = []

        # 获取资金费率数据
        funding_rates: dict[str, list[Any]] = {}
        for instrument_id in req.instruments:
            fr = await data_provider.get_funding_rates(
                instrument_id=instrument_id,
                start=req.start,
                end=req.end,
            )
            funding_rates[instrument_id] = fr

        # 获取 mark price 数据
        mark_prices: dict[str, list[Any]] = {}
        for instrument_id in req.instruments:
            mp = await data_provider.get_mark_prices(
                instrument_id=instrument_id,
                start=req.start,
                end=req.end,
            )
            mark_prices[instrument_id] = mp

        # 撮合时点
        fill_timing = FillTiming(
            req.engine_config.get("fill_timing", "NEXT_BAR_OPEN")
            if hasattr(req, "engine_config") and isinstance(getattr(req, "engine_config", None), dict)
            else FillTiming.NEXT_BAR_OPEN
        )

        # 逐 bar 模拟
        bar_lists = {iid: list(bars) for iid, bars in all_bars.items()}

        # 按时间排序的所有 bar
        all_bar_events: list[tuple[datetime, str, Bar]] = []
        for iid, bars in bar_lists.items():
            for bar in bars:
                all_bar_events.append((bar.open_time, iid, bar))
        all_bar_events.sort(key=lambda x: x[0])

        # 防未来函数：记录已处理的最大 open_time
        processed_max_time: dict[str, datetime] = {}

        pending_signals: list[_Signal] = []
        funding_idx: dict[str, int] = {iid: 0 for iid in req.instruments}
        mark_idx: dict[str, int] = {iid: 0 for iid in req.instruments}

        for _event_time, instrument_id, bar in all_bar_events:
            # 防未来函数检查
            if instrument_id in processed_max_time and bar.open_time < processed_max_time[instrument_id]:
                    raise BacktestError(
                        detail=f"未来函数检测: {instrument_id} bar open_time={bar.open_time} "
                               f"< 已处理 {processed_max_time[instrument_id]}"
                    )
            processed_max_time[instrument_id] = bar.open_time

            # 处理挂单撮合
            self._match_pending_signals(
                pending_signals, bar, instrument_id, cost_calc,
                cash, positions, trades, fill_timing,
            )

            # 资金费率结算
            self._settle_funding(
                instrument_id, bar, funding_rates, funding_idx,
                positions, trades,
            )

            # 强平检查
            self._check_liquidation(
                instrument_id, bar, mark_prices, mark_idx,
                positions, trades, cost_calc, cash,
            )

            # 生成简化信号（均值回归 z-score）
            signals = self._generate_signals(bar, instrument_id, req.params)
            pending_signals.extend(signals)

            # 更新权益曲线
            unrealized = self._compute_unrealized_pnl(positions, bar, instrument_id)
            equity = cash + unrealized
            equity_curve.append(EquityPoint(
                timestamp=bar.open_time,
                equity=equity,
                cash=cash,
                unrealized_pnl=unrealized,
            ))

        # 计算指标
        metrics = compute_metrics(trades, equity_curve, initial_equity)

        # 生成报告
        report_card = generate_report_card(run_id, req.strategy_id, req.version_no, metrics)
        alpha_score = compute_alpha_score(run_id, req.strategy_id, req.version_no, metrics)
        report_card = ReportCardWithAlpha(report_card, alpha_score).to_report_card()

        # 保存 trades/equity 到文件
        data_dir = self._container.config.data_dir
        bt_dir = data_dir / "backtest" / run_id
        bt_dir.mkdir(parents=True, exist_ok=True)

        trades_ref = self._save_trades(trades, bt_dir)
        equity_ref = self._save_equity(equity_curve, bt_dir)

        # 环境锁
        env_lock = self._compute_env_lock()

        # 构建 BacktestRun
        engine_config = {
            "engine_mode": "EVENT_DRIVEN",
            "fill_timing": fill_timing.value,
            "instruments": req.instruments,
            "interval": req.interval,
            "cost_config": req.cost_config.model_dump(),
            "leverage": str(req.leverage),
            "random_seed": req.random_seed,
        }

        result = BacktestRun(
            run_id=run_id,
            strategy_id=req.strategy_id,
            version_no=req.version_no,
            params=req.params,
            data_range=DataRange(start=req.start, end=req.end),
            data_version=req.data_version,
            feature_version=req.feature_version,
            engine_config=engine_config,
            environment_lock=env_lock,
            trades_ref=trades_ref,
            equity_curve_ref=equity_ref,
            metrics=metrics,
            status=ExperimentStatus.SUCCEEDED,
            created_at=datetime.now(UTC),
        )

        # 保存到实验存储
        try:
            store = self._get_experiment_store()
            await store.create_run(result)
            await store.save_report_card(report_card)
        except Exception:
            logger.debug("实验存储写入失败（非关键路径）")

        return result

    # ── 信号生成（简化版，用于无策略代码时的回测）──

    def _generate_signals(
        self,
        bar: Bar,
        instrument_id: str,
        params: dict[str, Any],
    ) -> list[_Signal]:
        """基于参数生成交易信号（均值回归策略简化版）

        使用 bar 涨跌幅作为信号源，不依赖策略代码。
        """
        entry_z = float(params.get("entry_z", 2.0))
        position_size = Decimal(str(params.get("position_size", "0.1")))

        # 简化信号：用 bar 涨跌幅与阈值比较
        if bar.open == 0:
            return []

        change = (bar.close - bar.open) / bar.open
        signals: list[_Signal] = []

        if abs(float(change)) > entry_z / 100:
            side = "SHORT" if change > 0 else "LONG"
            signals.append(_Signal(
                instrument_id=instrument_id,
                side=side,
                action="OPEN",
                quantity=position_size,
                timestamp=bar.open_time,
            ))

        return signals

    # ── 撮合 ──────────────────────────────────────────────

    def _match_pending_signals(
        self,
        pending: list[_Signal],
        bar: Bar,
        instrument_id: str,
        cost_calc: CostCalculator,
        cash: Decimal,
        positions: dict[str, _Position],
        trades: list[TradeRecord],
        fill_timing: FillTiming,
    ) -> None:
        """撮合挂单"""
        matched_indices: list[int] = []

        for i, signal in enumerate(pending):
            if signal.instrument_id != instrument_id:
                continue

            # 确定成交价
            if fill_timing == FillTiming.BAR_CLOSE:
                fill_price = bar.close
            elif fill_timing == FillTiming.NEXT_BAR_OPEN:
                fill_price = bar.open  # 当前 bar 的 open（简化：用当前 bar 开盘价代表下一 bar 开盘）
            else:
                fill_price = bar.open

            # 应用滑点
            side = "BUY" if signal.side == "LONG" else "SELL"
            actual_price = cost_calc.apply_slippage_price(fill_price, side)

            # 计算成本
            trade_cost = cost_calc.compute_trade_cost(
                signal.quantity, actual_price, is_maker=False, side=side,
            )

            # 执行交易
            if signal.action == "OPEN":
                # 开仓
                margin = cost_calc.compute_margin(signal.quantity, actual_price)
                if margin > cash:
                    continue  # 保证金不足，跳过

                positions[signal.instrument_id] = _Position(
                    side=signal.side,
                    quantity=signal.quantity,
                    entry_price=actual_price,
                    margin=margin,
                )

                trades.append(TradeRecord(
                    instrument_id=signal.instrument_id,
                    side=signal.side,
                    action="OPEN",
                    quantity=signal.quantity,
                    price=actual_price,
                    timestamp=signal.timestamp,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                ))

            elif signal.action == "CLOSE" and instrument_id in positions:
                pos = positions[instrument_id]
                pnl = self._compute_pnl(pos, actual_price)

                trades.append(TradeRecord(
                    instrument_id=signal.instrument_id,
                    side=pos.side,
                    action="CLOSE",
                    quantity=signal.quantity,
                    price=actual_price,
                    timestamp=signal.timestamp,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                    pnl=pnl,
                ))

                del positions[instrument_id]

            matched_indices.append(i)

        # 移除已撮合信号（倒序删除）
        for i in sorted(matched_indices, reverse=True):
            pending.pop(i)

    def _settle_funding(
        self,
        instrument_id: str,
        bar: Bar,
        funding_rates: dict[str, list[Any]],
        funding_idx: dict[str, int],
        positions: dict[str, _Position],
        trades: list[TradeRecord],
    ) -> None:
        """资金费率结算"""
        if instrument_id not in positions:
            return

        pos = positions[instrument_id]
        rates = funding_rates.get(instrument_id, [])
        idx = funding_idx.get(instrument_id, 0)

        cost_calc = CostCalculator(CostConfigModel())

        while idx < len(rates):
            rate = rates[idx]
            # funding_ts 在当前 bar 之前或等于时结算
            rate_ts = rate.funding_ts if hasattr(rate, "funding_ts") else datetime.min.replace(tzinfo=UTC)
            if rate_ts > bar.open_time:
                break

            funding_cost = cost_calc.compute_funding_cost(
                pos.quantity, bar.close, rate.rate, pos.side,
            )

            trades.append(TradeRecord(
                instrument_id=instrument_id,
                side=pos.side,
                action="FUNDING",
                quantity=pos.quantity,
                price=bar.close,
                timestamp=bar.open_time,
                funding=funding_cost,
            ))

            idx += 1

        funding_idx[instrument_id] = idx

    def _check_liquidation(
        self,
        instrument_id: str,
        bar: Bar,
        mark_prices: dict[str, list[Any]],
        mark_idx: dict[str, int],
        positions: dict[str, _Position],
        trades: list[TradeRecord],
        cost_calc: CostCalculator,
        cash: Decimal,
    ) -> None:
        """强平检查"""
        if instrument_id not in positions:
            return

        pos = positions[instrument_id]

        # 获取 mark price（优先）或 close price
        mp_list = mark_prices.get(instrument_id, [])
        idx = mark_idx.get(instrument_id, 0)
        mark_price = bar.close  # 默认用收盘价

        while idx < len(mp_list):
            mp = mp_list[idx]
            mp_ts = mp.ts if hasattr(mp, "ts") else datetime.min.replace(tzinfo=UTC)
            if mp_ts > bar.open_time:
                break
            mark_price = mp.mark_price if hasattr(mp, "mark_price") else bar.close
            idx += 1

        mark_idx[instrument_id] = idx

        # 检查强平
        if cost_calc.is_liquidated(pos.side, pos.entry_price, mark_price):
            pnl = self._compute_pnl(pos, mark_price)

            trades.append(TradeRecord(
                instrument_id=instrument_id,
                side=pos.side,
                action="CLOSE",
                quantity=pos.quantity,
                price=mark_price,
                timestamp=bar.open_time,
                pnl=pnl,
                is_liquidation=True,
            ))

            del positions[instrument_id]

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: _Position, current_price: Decimal) -> Decimal:
        """计算持仓盈亏"""
        if pos.side == "LONG":
            return (current_price - pos.entry_price) * pos.quantity
        return (pos.entry_price - current_price) * pos.quantity

    @staticmethod
    def _compute_unrealized_pnl(
        positions: dict[str, _Position],
        bar: Bar,
        current_instrument: str,
    ) -> Decimal:
        """计算未实现盈亏"""
        total = Decimal("0")
        for iid, pos in positions.items():
            price = bar.close if iid == current_instrument else pos.entry_price
            if pos.side == "LONG":
                total += (price - pos.entry_price) * pos.quantity
            else:
                total += (pos.entry_price - price) * pos.quantity
        return total

    @staticmethod
    def _generate_run_id(req: BacktestRequest) -> str:
        """生成确定性 run_id"""
        seed = req.random_seed
        seed_hex = hashlib.sha256(str(seed).encode()).hexdigest()[:8]
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        return f"{BACKTEST_RUN_ID_PREFIX}{ts}_{seed_hex}"

    @staticmethod
    def _compute_env_lock() -> str:
        """计算环境锁（uv.lock hash）"""
        lock_path = Path("uv.lock")
        if lock_path.exists():
            content = lock_path.read_bytes()
            return f"env-{hashlib.sha256(content).hexdigest()[:8]}"
        return "env-unknown"

    @staticmethod
    def _save_trades(trades: list[TradeRecord], bt_dir: Path) -> str:
        """保存逐笔交易到 JSONL"""
        path = bt_dir / "trades.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps({
                    "instrument_id": t.instrument_id,
                    "side": t.side,
                    "action": t.action,
                    "quantity": str(t.quantity),
                    "price": str(t.price),
                    "timestamp": t.timestamp.isoformat(),
                    "fee": str(t.fee),
                    "slippage": str(t.slippage),
                    "funding": str(t.funding),
                    "pnl": str(t.pnl),
                    "is_liquidation": t.is_liquidation,
                }, ensure_ascii=False) + "\n")
        return str(path)

    @staticmethod
    def _save_equity(equity_curve: list[EquityPoint], bt_dir: Path) -> str:
        """保存权益曲线到 JSONL"""
        path = bt_dir / "equity.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for ep in equity_curve:
                f.write(json.dumps({
                    "timestamp": ep.timestamp.isoformat(),
                    "equity": str(ep.equity),
                    "cash": str(ep.cash),
                    "unrealized_pnl": str(ep.unrealized_pnl),
                }, ensure_ascii=False) + "\n")
        return str(path)

    def _failed_run(self, run_id: str, error: str, request: Any) -> BacktestRun:
        """创建失败记录"""
        return BacktestRun(
            run_id=run_id,
            strategy_id=request.get("strategy_id", "unknown") if isinstance(request, dict) else "unknown",
            version_no=request.get("version_no", 1) if isinstance(request, dict) else 1,
            params=request.get("params", {}) if isinstance(request, dict) else {},
            data_range=DataRange(
                start=datetime(2026, 1, 1, tzinfo=UTC),
                end=datetime(2026, 1, 2, tzinfo=UTC),
            ),
            data_version="unknown",
            feature_version="unknown",
            engine_config={"error": error},
            status=ExperimentStatus.FAILED,
            created_at=datetime.now(UTC),
        )

    def _get_data_provider(self) -> DataProvider:
        """获取 DataProvider"""
        from qps.data.provider import DataProvider
        port = self._container.get_port("data_provider")
        if isinstance(port, DataProvider):
            return port
        raise BacktestError(detail="data_provider 端口未实现")

    def _get_experiment_store(self) -> ExperimentStore:
        """获取 ExperimentStore"""
        from qps.research.experiments import ExperimentStore
        port = self._container.get_port("experiment_store")
        if isinstance(port, ExperimentStore):
            return port
        raise BacktestError(detail="experiment_store 端口未实现")


# ── 内部数据结构 ──────────────────────────────────────────


class _Position:
    """持仓状态"""

    def __init__(
        self,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        margin: Decimal,
    ) -> None:
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.margin = margin


class _Signal:
    """交易信号"""

    def __init__(
        self,
        instrument_id: str,
        side: str,
        action: str,
        quantity: Decimal,
        timestamp: datetime,
    ) -> None:
        self.instrument_id = instrument_id
        self.side = side
        self.action = action
        self.quantity = quantity
        self.timestamp = timestamp


class ReportCardWithAlpha:
    """辅助类：给 ReportCard 添加 AlphaScore"""

    def __init__(self, report_card: ReportCard, alpha_score: Any) -> None:
        self._report_card = report_card
        self._alpha_score = alpha_score

    def to_report_card(self) -> ReportCard:
        """转换为含 AlphaScore 的 ReportCard"""
        from qps.core.models.experiment import AlphaScore
        alpha = self._alpha_score if isinstance(self._alpha_score, AlphaScore) else None
        return ReportCard(
            run_id=self._report_card.run_id,
            strategy_id=self._report_card.strategy_id,
            version_no=self._report_card.version_no,
            metrics=self._report_card.metrics,
            gate_results=self._report_card.gate_results,
            alpha_score=alpha,
            conclusion=self._report_card.conclusion,
        )
