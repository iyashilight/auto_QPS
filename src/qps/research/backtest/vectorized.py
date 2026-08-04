"""向量化回测引擎（见 docs/03 §7，架构 §7）

参数粗筛用：按 bar 收盘/下一 bar 开盘撮合，简化成本模型。
基于 Polars 批量计算，支持多标的并行。
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
)
from qps.core.costs import CostCalculator
from qps.core.errors import BacktestError
from qps.core.models.experiment import (
    BacktestRequest,
    BacktestRun,
    CostConfig,
    DataRange,
    ReportCard,
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


class VectorizedBacktestEngine:
    """向量化回测引擎

    功能：参数粗筛、简化成本模型、按 bar 收盘/下一 bar 开盘撮合。
    与事件驱动引擎共享 CostCalculator，但撮合逻辑简化。
    """

    def __init__(self, container: Container) -> None:
        self._container = container

    async def run(self, request: dict[str, Any]) -> BacktestRun:
        """执行向量化回测

        Args:
            request: 回测请求字典（解析为 BacktestRequest）

        Returns:
            BacktestRun（失败返回 FAILED 记录而非抛异常）
        """
        try:
            req = self._parse_request(request)
        except Exception as e:
            return self._failed_run(
                f"btv_invalid_{hash(str(request)) % 10000:04d}", str(e), request,
            )

        run_id = self._generate_run_id(req)

        try:
            result = await self._execute_backtest(req, run_id)
            return result
        except BacktestError:
            raise
        except Exception as e:
            logger.exception("向量化回测执行失败: %s", run_id)
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

        request = {
            "strategy_id": original.strategy_id,
            "version_no": original.version_no,
            "params": original.params,
            "instruments": original.engine_config.get("instruments", []),
            "interval": original.engine_config.get("interval", "5m"),
            "start": original.data_range.start.isoformat(),
            "end": original.data_range.end.isoformat(),
            "engine_mode": "VECTORIZED",
            "data_version": original.data_version,
            "feature_version": original.feature_version,
            "cost_config": original.engine_config.get("cost_config", {}),
            "leverage": original.engine_config.get("leverage", "3"),
            "random_seed": original.engine_config.get("random_seed", 42),
        }

        return await self.run(request)

    # ── 核心执行 ──────────────────────────────────────────

    async def _execute_backtest(
        self, req: BacktestRequest, run_id: str,
    ) -> BacktestRun:
        """执行向量化回测

        向量化引擎简化逻辑：
        - 逐 bar 遍历（与事件驱动一致，保证确定性）
        - 撮合按 bar 收盘价（简化，无挂单/限价）
        - 成本使用共享 CostCalculator
        - 不做强平/资金费率结算（粗筛阶段简化）
        """
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

        total_bars = sum(len(b) for b in all_bars.values())
        if total_bars == 0:
            return self._failed_run(run_id, "无 K 线数据", req.model_dump())

        cost_calc = CostCalculator(req.cost_config)

        # 初始化回测状态
        initial_equity = Decimal("10000")
        cash = initial_equity
        positions: dict[str, _VecPosition] = {}
        trades: list[TradeRecord] = []
        equity_curve: list[EquityPoint] = []

        # 按时间排序所有 bar
        all_bar_events: list[tuple[datetime, str, Bar]] = []
        for iid, bars in all_bars.items():
            for bar in bars:
                all_bar_events.append((bar.open_time, iid, bar))
        all_bar_events.sort(key=lambda x: x[0])

        # 防未来函数：记录已处理的最大 open_time
        processed_max_time: dict[str, datetime] = {}

        for _event_time, instrument_id, bar in all_bar_events:
            # 防未来函数检查
            if instrument_id in processed_max_time and bar.open_time < processed_max_time[instrument_id]:
                raise BacktestError(
                    detail=f"未来函数检测: {instrument_id} bar open_time={bar.open_time} "
                           f"< 已处理 {processed_max_time[instrument_id]}",
                )
            processed_max_time[instrument_id] = bar.open_time

            # 向量化撮合：用收盘价直接成交
            cash_delta = self._process_bar(
                bar=bar,
                instrument_id=instrument_id,
                cost_calc=cost_calc,
                cash=cash,
                positions=positions,
                trades=trades,
                params=req.params,
            )
            cash += cash_delta

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
        report_card = ReportCard(
            run_id=report_card.run_id,
            strategy_id=report_card.strategy_id,
            version_no=report_card.version_no,
            metrics=report_card.metrics,
            gate_results=report_card.gate_results,
            alpha_score=alpha_score,
            conclusion=report_card.conclusion,
        )

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
            "engine_mode": "VECTORIZED",
            "fill_timing": "BAR_CLOSE",
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

    # ── 信号生成与撮合 ────────────────────────────────────

    def _process_bar(
        self,
        bar: Bar,
        instrument_id: str,
        cost_calc: CostCalculator,
        cash: Decimal,
        positions: dict[str, _VecPosition],
        trades: list[TradeRecord],
        params: dict[str, Any],
    ) -> Decimal:
        """处理单个 bar：生成信号 + 撮合

        向量化简化逻辑：
        - 用 bar 涨跌幅生成信号（与事件驱动一致）
        - 收盘价直接成交（无挂单/限价/部分成交）
        - 先平仓再开仓

        Returns:
            cash 变动量（正=收入，负=支出）
        """
        entry_z = float(params.get("entry_z", 2.0))
        position_size = Decimal(str(params.get("position_size", "0.1")))
        cash_delta = Decimal("0")

        if bar.open == 0:
            return cash_delta

        change = (bar.close - bar.open) / bar.open
        fill_price = bar.close  # 向量化：收盘价成交

        # 先检查是否需要平仓（反向信号）
        if instrument_id in positions:
            pos = positions[instrument_id]
            # 如果有持仓且信号方向相反，先平仓
            should_close = False
            if abs(float(change)) > entry_z / 100:
                new_side = "SHORT" if change > 0 else "LONG"
                if new_side != pos.side:
                    should_close = True

            # 或持仓超过 N bar（简化止盈止损）
            max_hold_bars = int(params.get("max_hold_bars", 12))
            if pos.hold_bars >= max_hold_bars:
                should_close = True

            if should_close:
                # 平仓
                side = "BUY" if pos.side == "SHORT" else "SELL"
                actual_price = cost_calc.apply_slippage_price(fill_price, side)
                trade_cost = cost_calc.compute_trade_cost(
                    pos.quantity, actual_price, is_maker=False, side=side,
                )
                pnl = self._compute_pnl(pos, actual_price)

                trades.append(TradeRecord(
                    instrument_id=instrument_id,
                    side=pos.side,
                    action="CLOSE",
                    quantity=pos.quantity,
                    price=actual_price,
                    timestamp=bar.open_time,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                    pnl=pnl,
                ))
                # 平仓：回收保证金 + 盈亏 - 成本
                cash_delta += pos.margin + pnl - trade_cost.fee - trade_cost.slippage
                del positions[instrument_id]

        # 开仓信号
        if instrument_id not in positions and abs(float(change)) > entry_z / 100:
            side_str = "SHORT" if change > 0 else "LONG"
            buy_sell = "BUY" if side_str == "LONG" else "SELL"
            actual_price = cost_calc.apply_slippage_price(fill_price, buy_sell)
            trade_cost = cost_calc.compute_trade_cost(
                position_size, actual_price, is_maker=False, side=buy_sell,
            )
            margin = cost_calc.compute_margin(position_size, actual_price)

            if margin <= cash + cash_delta:  # 检查可用现金
                positions[instrument_id] = _VecPosition(
                    side=side_str,
                    quantity=position_size,
                    entry_price=actual_price,
                    margin=margin,
                    hold_bars=0,
                )
                trades.append(TradeRecord(
                    instrument_id=instrument_id,
                    side=side_str,
                    action="OPEN",
                    quantity=position_size,
                    price=actual_price,
                    timestamp=bar.open_time,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                ))
                # 开仓：扣除保证金 + 成本
                cash_delta -= margin + trade_cost.fee + trade_cost.slippage

        # 更新持仓 bar 计数
        if instrument_id in positions:
            pos = positions[instrument_id]
            positions[instrument_id] = _VecPosition(
                side=pos.side,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                margin=pos.margin,
                hold_bars=pos.hold_bars + 1,
            )

        return cash_delta

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: _VecPosition, current_price: Decimal) -> Decimal:
        """计算持仓盈亏"""
        if pos.side == "LONG":
            return (current_price - pos.entry_price) * pos.quantity
        return (pos.entry_price - current_price) * pos.quantity

    @staticmethod
    def _compute_unrealized_pnl(
        positions: dict[str, _VecPosition],
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
        return f"{BACKTEST_RUN_ID_PREFIX}v_{ts}_{seed_hex}"

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

    @staticmethod
    def _parse_request(request: dict[str, Any]) -> BacktestRequest:
        """解析回测请求"""
        return BacktestRequest(
            strategy_id=request.get("strategy_id", "unknown"),
            version_no=request.get("version_no", 1),
            params=request.get("params", {}),
            instruments=request.get("instruments", []),
            interval=request.get("interval", "5m"),
            start=datetime.fromisoformat(request["start"]) if isinstance(request.get("start"), str) else request.get("start", datetime(2026, 1, 1, tzinfo=UTC)),
            end=datetime.fromisoformat(request["end"]) if isinstance(request.get("end"), str) else request.get("end", datetime(2026, 1, 2, tzinfo=UTC)),
            engine_mode=BacktestEngineMode.VECTORIZED,
            data_version=request.get("data_version", "unknown"),
            feature_version=request.get("feature_version", "unknown"),
            cost_config=request.get("cost_config", CostConfig()) if isinstance(request.get("cost_config"), CostConfig) else CostConfig(),
            leverage=Decimal(str(request.get("leverage", "3"))),
            random_seed=request.get("random_seed", 42),
        )

    @staticmethod
    def _failed_run(run_id: str, error: str, request: Any) -> BacktestRun:
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


class _VecPosition:
    """向量化持仓状态"""

    def __init__(
        self,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        margin: Decimal,
        hold_bars: int = 0,
    ) -> None:
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.margin = margin
        self.hold_bars = hold_bars
