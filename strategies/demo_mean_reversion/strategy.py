"""演示均值回归策略

策略逻辑：
- 从 Feature Pipeline 读取 returns / mean_return_20 / realized_vol_20 特征
- 计算 Z-Score = (当前收益率 - 均值) / 标准差
- 当 |Z-Score| >= entry_z 时入场
- 当 Z-Score 回归到 exit_z 内时出场
"""

from __future__ import annotations

from decimal import Decimal

from qps.core.models import Bar
from qps.core.sdk import BaseStrategy, StrategyContext


class Strategy(BaseStrategy):
    """均值回归策略"""

    def initialize(self, ctx: StrategyContext) -> None:
        """初始化策略

        Args:
            ctx: 策略上下文
        """
        # 从参数中读取配置
        self.lookback = int(ctx.params.get("lookback", 50))
        self.entry_z = float(ctx.params.get("entry_z", 2.0))
        self.exit_z = float(ctx.params.get("exit_z", 0.5))
        self.position_size = Decimal(str(ctx.params.get("position_size", 0.1)))

        # 初始化状态（存入 ctx.state 保证重启恢复）
        ctx.state["position_side"] = None  # LONG / SHORT / None
        ctx.state["entry_price"] = None

        ctx.log("INFO", "策略初始化完成",
                 lookback=self.lookback, entry_z=self.entry_z, exit_z=self.exit_z)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """K 线回调（核心逻辑）

        Args:
            ctx: 策略上下文
            bar: K 线数据
        """
        instrument_id = bar.instrument_id

        # ── 从 Feature Pipeline 读取特征 ──────────────────────────────────
        returns = ctx.feature(instrument_id, "returns")
        mean_return = ctx.feature(instrument_id, "mean_return_20")
        realized_vol = ctx.feature(instrument_id, "realized_vol_20")

        # 特征缓存未命中：跳过本轮信号
        if returns is None or mean_return is None or realized_vol is None:
            ctx.log("DEBUG", "特征缓存未命中，跳过本轮信号",
                     feature_missing=["returns" if returns is None else "",
                                     "mean_return_20" if mean_return is None else "",
                                     "realized_vol_20" if realized_vol is None else ""])
            return

        # ── 计算 Z-Score ──────────────────────────────────────────────────
        # 使用 Feature Pipeline 提供的完整统计量
        # returns: 当前对数收益率
        # mean_return_20: 过去 20 期对数收益率均值
        # realized_vol_20: 过去 20 期年化波动率（需去年化）
        # 5m K 线年化因子 = 105120，去年化 = vol / sqrt(105120)
        annualization_factor = 105120  # 5m 间隔
        std_return = realized_vol / (annualization_factor ** 0.5)
        z_score = (returns - mean_return) / std_return if std_return > 0 else 0

        # 检查持仓状态
        position_side = ctx.state.get("position_side")
        has_position = position_side is not None

        # ── 交易逻辑 ──────────────────────────────────────────────────────

        if not has_position:
            # 无持仓：检查入场条件
            if abs(z_score) >= self.entry_z:
                direction = "LONG" if z_score < 0 else "SHORT"
                self._open_position(ctx, instrument_id, direction, bar.close)
        else:
            # 有持仓：检查出场条件
            if self._should_close(z_score, position_side):
                self._close_position(ctx, instrument_id, bar.close)

        # 更新状态
        ctx.state["last_z_score"] = z_score
        ctx.state["last_return"] = returns
        ctx.state["last_mean_return"] = mean_return
        ctx.state["last_volatility"] = realized_vol

    def _calculate_returns(self, bars: list[Bar]) -> list[float]:
        """计算收益率序列（备用方法，不直接使用）

        Args:
            bars: K 线列表

        Returns:
            收益率列表
        """
        returns = []
        for i in range(1, len(bars)):
            prev_close = float(bars[i - 1].close)
            curr_close = float(bars[i].close)
            if prev_close > 0:
                ret = (curr_close - prev_close) / prev_close
                returns.append(ret)
        return returns

    def _open_position(
        self,
        ctx: StrategyContext,
        instrument_id: str,
        direction: str,
        price: Decimal,
    ) -> None:
        """开仓

        Args:
            ctx: 策略上下文
            instrument_id: 标的 ID
            direction: 方向（LONG/SHORT）
            price: 入场价格
        """
        ctx.state["position_side"] = direction
        ctx.state["entry_price"] = price

        ctx.emit_intent({
            "instrument_id": instrument_id,
            "direction": direction,
            "action": "OPEN",
            "quantity": str(self.position_size),
            "order_type": "MARKET",
            "reason_tags": ["mean_reversion", "z_score_entry"],
        })

        ctx.log("INFO", "开仓信号",
                 direction=direction,
                 price=str(price),
                 size=str(self.position_size))

    def _close_position(
        self,
        ctx: StrategyContext,
        instrument_id: str,
        price: Decimal,
    ) -> None:
        """平仓

        Args:
            ctx: 策略上下文
            instrument_id: 标的 ID
            price: 出场价格
        """
        position_side = ctx.state.get("position_side")
        if position_side is None:
            return

        ctx.emit_intent({
            "instrument_id": instrument_id,
            "direction": position_side,
            "action": "CLOSE",
            "quantity": str(self.position_size),
            "order_type": "MARKET",
            "reason_tags": ["mean_reversion", "z_score_exit"],
        })

        entry_price = ctx.state.get("entry_price")
        pnl = self._calculate_pnl(price, entry_price, position_side)
        ctx.log("INFO", "平仓信号",
                 side=position_side,
                 price=str(price),
                 pnl=str(pnl))

        # 重置状态
        ctx.state["position_side"] = None
        ctx.state["entry_price"] = None

    def _should_close(self, z_score: float, position_side: str) -> bool:
        """判断是否应该平仓

        Args:
            z_score: 当前 Z-Score
            position_side: 持仓方向

        Returns:
            True 表示应该平仓
        """
        if position_side == "LONG":
            # 多头持仓：Z-Score 回归到正值时平仓
            return z_score >= self.exit_z
        else:
            # 空头持仓：Z-Score 回归到负值时平仓
            return z_score <= -self.exit_z

    def _calculate_pnl(self, current_price: Decimal, entry_price: Decimal | None, position_side: str) -> Decimal:
        """计算盈亏

        Args:
            current_price: 当前价格
            entry_price: 入场价格
            position_side: 持仓方向

        Returns:
            盈亏金额
        """
        if entry_price is None or entry_price == 0:
            return Decimal("0")

        if position_side == "LONG":
            return (current_price - entry_price) * self.position_size
        else:
            return (entry_price - current_price) * self.position_size
