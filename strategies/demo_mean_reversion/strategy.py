"""演示均值回归策略

策略逻辑：
- 计算过去 N 根 K 线的收益率均值和标准差
- 当价格偏离均值超过 entry_z 个标准差时入场
- 当价格回归到 exit_z 个标准差内时出场
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

        # 初始化状态
        self._position_side: str | None = None  # LONG / SHORT / None
        self._entry_price: Decimal | None = None

        ctx.log("INFO", "策略初始化完成",
                 lookback=self.lookback, entry_z=self.entry_z, exit_z=self.exit_z)

    def on_bar(self, ctx: StrategyContext, bar: Bar) -> None:
        """K 线回调（核心逻辑）

        Args:
            ctx: 策略上下文
            bar: K 线数据
        """
        instrument_id = bar.instrument_id

        # 更新历史数据（由框架调用）
        ctx.set_latest_bar(instrument_id, bar)

        # 获取历史数据
        history = ctx.history(instrument_id, self.lookback + 1)
        if len(history) < self.lookback + 1:
            # 数据不足，跳过
            return

        # 计算收益率序列
        returns = self._calculate_returns(history)
        if not returns:
            return

        # 计算均值和标准差
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        std_return = variance ** 0.5

        # 当前价格相对于均值的偏离
        current_close = bar.close
        last_close = history[-1].close
        current_return = float((current_close - last_close) / last_close) if last_close > 0 else 0

        # Z-Score
        z_score = (current_return - mean_return) / std_return if std_return > 0 else 0

        # 检查持仓状态
        has_position = self._position_side is not None

        # ── 交易逻辑 ──────────────────────────────────────

        if not has_position:
            # 无持仓：检查入场条件
            if abs(z_score) >= self.entry_z:
                direction = "LONG" if z_score < 0 else "SHORT"
                self._open_position(ctx, instrument_id, direction, current_close)
        else:
            # 有持仓：检查出场条件
            if self._should_close(z_score):
                self._close_position(ctx, instrument_id, current_close)

        # 更新状态
        ctx.state["last_z_score"] = z_score
        ctx.state["last_mean_return"] = mean_return
        ctx.state["last_std_return"] = std_return

    def _calculate_returns(self, bars: list[Bar]) -> list[float]:
        """计算收益率序列

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
        self._position_side = direction
        self._entry_price = price

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
        if self._position_side is None:
            return

        close_action = "CLOSE"
        ctx.emit_intent({
            "instrument_id": instrument_id,
            "direction": self._position_side,
            "action": close_action,
            "quantity": str(self.position_size),
            "order_type": "MARKET",
            "reason_tags": ["mean_reversion", "z_score_exit"],
        })

        pnl = self._calculate_pnl(price)
        ctx.log("INFO", "平仓信号",
                 side=self._position_side,
                 price=str(price),
                 pnl=str(pnl))

        # 重置状态
        self._position_side = None
        self._entry_price = None

    def _should_close(self, z_score: float) -> bool:
        """判断是否应该平仓

        Args:
            z_score: 当前 Z-Score

        Returns:
            True 表示应该平仓
        """
        if self._position_side == "LONG":
            # 多头持仓：Z-Score 回归到正值时平仓
            return z_score >= self.exit_z
        else:
            # 空头持仓：Z-Score 回归到负值时平仓
            return z_score <= -self.exit_z

    def _calculate_pnl(self, current_price: Decimal) -> Decimal:
        """计算盈亏

        Args:
            current_price: 当前价格

        Returns:
            盈亏金额
        """
        if self._entry_price is None or self._entry_price == 0:
            return Decimal("0")

        if self._position_side == "LONG":
            return (current_price - self._entry_price) * self.position_size
        else:
            return (self._entry_price - current_price) * self.position_size
