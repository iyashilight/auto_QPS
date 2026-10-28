"""共享成本模型（docs/03 §7，R9/R10）

回测、Paper（M5）、实盘三方复用同一实现，禁止回测内另写一套。
所有金额计算使用 Decimal，禁止 float 累计（R8）。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from qps.core.constants import SlippageModel
from qps.core.models.experiment import CostConfig


@dataclass(frozen=True)
class TradeCost:
    """单笔交易成本明细"""

    fee: Decimal = Decimal("0")  # 手续费
    slippage: Decimal = Decimal("0")  # 滑点成本
    funding: Decimal = Decimal("0")  # 资金费率成本
    borrowing: Decimal = Decimal("0")  # 借贷成本
    liquidation: Decimal = Decimal("0")  # 强平费用
    notional: Decimal = Decimal("0")  # 名义价值
    margin: Decimal = Decimal("0")  # 所需保证金

    @property
    def total(self) -> Decimal:
        """总成本"""
        return self.fee + self.slippage + self.funding + self.borrowing + self.liquidation


class CostCalculator:
    """成本计算器（回测/Paper/实盘共用）

    计算规则：
    - 手续费 = 名义价值 × 费率（maker/taker）
    - 滑点 = 名义价值 × slippage_bps / 10000（FIXED 模式）
    - 保证金 = 名义价值 / 杠杆（ISOLATED 模式）
    - 强平价计算基于维持保证金率
    """

    def __init__(self, config: CostConfig) -> None:
        """初始化

        Args:
            config: 成本配置
        """
        self._config = config

    @property
    def config(self) -> CostConfig:
        """成本配置"""
        return self._config

    def compute_fee(
        self,
        quantity: Decimal,
        price: Decimal,
        is_maker: bool = False,
    ) -> Decimal:
        """计算手续费

        Args:
            quantity: 数量
            price: 价格
            is_maker: 是否为 maker 单

        Returns:
            手续费金额（按名义价值计费）
        """
        notional = quantity * price
        fee_rate = self._config.maker_fee if is_maker else self._config.taker_fee
        return notional * fee_rate

    def compute_slippage(
        self,
        quantity: Decimal,
        price: Decimal,
        side: str = "BUY",
    ) -> Decimal:
        """计算滑点成本

        Args:
            quantity: 数量
            price: 价格
            side: 买/卖方向

        Returns:
            滑点金额（FIXED 模式：名义价值 × slippage_bps / 10000）
        """
        if self._config.slippage_model == SlippageModel.FIXED:
            notional = quantity * price
            return notional * Decimal(str(self._config.slippage_bps)) / Decimal("10000")
        # DEPTH 模式：MVP 用与 FIXED 相同的计算，接口预留
        notional = quantity * price
        return notional * Decimal(str(self._config.slippage_bps)) / Decimal("10000")

    def apply_slippage_price(
        self,
        price: Decimal,
        side: str = "BUY",
    ) -> Decimal:
        """对价格应用滑点，返回实际成交价

        Args:
            price: 原始价格
            side: BUY/SELL

        Returns:
            含滑点的成交价（买入价上浮，卖出价下浮）
        """
        slippage_pct = Decimal(str(self._config.slippage_bps)) / Decimal("10000")
        if side == "BUY":
            return price * (Decimal("1") + slippage_pct)
        return price * (Decimal("1") - slippage_pct)

    def compute_margin(
        self,
        quantity: Decimal,
        price: Decimal,
    ) -> Decimal:
        """计算所需保证金

        Args:
            quantity: 数量
            price: 价格

        Returns:
            保证金金额 = 名义价值 / 杠杆
        """
        notional = quantity * price
        leverage = self._config.leverage
        if leverage <= 0:
            return notional  # 无杠杆 = 全额保证金
        return notional / leverage

    def compute_liquidation_price(
        self,
        entry_price: Decimal,
        quantity: Decimal,
        side: str,
        margin_balance: Decimal | None = None,
    ) -> Decimal:
        """计算强平价格

        强平条件（逐仓）：
        - 多头：标记价格 ≤ 入场价 × (1 - 1/杠杆 + 维持保证金率)
        - 空头：标记价格 ≥ 入场价 × (1 + 1/杠杆 - 维持保证金率)

        Args:
            entry_price: 入场价格
            quantity: 持仓数量
            side: LONG/SHORT
            margin_balance: 当前保证金余额（如为 None，使用初始保证金）

        Returns:
            强平价格
        """
        leverage = self._config.leverage
        mmr = self._config.liquidation_margin_rate

        if leverage <= 0:
            return Decimal("0")

        if side == "LONG":
            # 多头强平价 = 入场价 × (1 - 1/杠杆 + 维持保证金率)
            return entry_price * (Decimal("1") - Decimal("1") / leverage + mmr)
        # 空头强平价 = 入场价 × (1 + 1/杠杆 - 维持保证金率)
        return entry_price * (Decimal("1") + Decimal("1") / leverage - mmr)

    def is_liquidated(
        self,
        side: str,
        entry_price: Decimal,
        mark_price: Decimal,
    ) -> bool:
        """判断是否触发强平

        Args:
            side: LONG/SHORT
            entry_price: 入场价
            mark_price: 标记价格

        Returns:
            True 表示触发强平
        """
        liq_price = self.compute_liquidation_price(entry_price, Decimal("1"), side)
        if side == "LONG":
            return mark_price <= liq_price
        return mark_price >= liq_price

    def compute_funding_cost(
        self,
        quantity: Decimal,
        price: Decimal,
        rate: Decimal,
        side: str,
    ) -> Decimal:
        """计算单次资金费率结算成本

        资金费率 = 名义价值 × 费率
        - 多头：正费率支付，负费率收取
        - 空头：正费率收取，负费率支付

        Args:
            quantity: 持仓数量
            price: 结算时价格
            rate: 资金费率
            side: LONG/SHORT

        Returns:
            资金费率成本（正数=支付，负数=收取）
        """
        notional = quantity * price
        funding = notional * rate
        # 多头：正费率支付，负费率收取
        # 空头：正费率收取，负费率支付
        if side == "LONG":
            return funding  # 正费率时为正（支付），负费率时为负（收取）
        return -funding  # 正费率时为负（收取），负费率时为正（支付）

    def compute_borrowing_cost(self, notional: Decimal, duration: Decimal) -> Decimal:
        """按名义价值和持仓时间（天）计借贷成本。"""
        return notional * self._config.borrowing_rate * duration

    def compute_liquidation_cost(self, notional: Decimal) -> Decimal:
        return notional * self._config.liquidation_fee_rate

    def validate_capacity(self, quantity: Decimal, market_volume: Decimal) -> None:
        if market_volume < 0 or quantity > market_volume * self._config.capacity_participation_rate:
            raise ValueError("订单超过容量参与上限")

    def compute_trade_cost(
        self,
        quantity: Decimal,
        price: Decimal,
        is_maker: bool = False,
        side: str = "BUY",
    ) -> TradeCost:
        """计算完整交易成本

        Args:
            quantity: 数量
            price: 价格
            is_maker: 是否 maker
            side: BUY/SELL

        Returns:
            TradeCost 包含费用明细
        """
        notional = quantity * price
        fee = self.compute_fee(quantity, price, is_maker)
        slippage = self.compute_slippage(quantity, price, side)
        margin = self.compute_margin(quantity, price)

        return TradeCost(
            fee=fee,
            slippage=slippage,
            notional=notional,
            margin=margin,
        )


def cost_config_from_backtest_config(bt_cost: Any) -> CostConfig:
    """从 BacktestCostConfig 创建 CostConfig

    Args:
        bt_cost: BacktestCostConfig 实例（config_models.py）

    Returns:
        CostConfig 实例
    """
    from qps.core.constants import FundingRateSource, MarginMode, SlippageModel

    def _to_upper(val: str) -> str:
        return val.upper() if isinstance(val, str) else val

    def _enum_or_val(enum_cls: type, val: str) -> Any:
        return enum_cls(_to_upper(val) if isinstance(val, str) else val.value)

    return CostConfig(
        maker_fee=bt_cost.maker_fee,
        taker_fee=bt_cost.taker_fee,
        slippage_model=_enum_or_val(SlippageModel, bt_cost.slippage_model),
        slippage_bps=bt_cost.slippage_bps,
        funding_rate_source=_enum_or_val(FundingRateSource, bt_cost.funding_rate_source),
        leverage=bt_cost.leverage,
        margin_mode=_enum_or_val(MarginMode, bt_cost.margin_mode),
        liquidation_margin_rate=bt_cost.liquidation_margin_rate,
        use_depth_snapshots=bt_cost.use_depth_snapshots,
    )
