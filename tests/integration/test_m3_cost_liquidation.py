from decimal import Decimal

from qps.core.costs import CostCalculator
from qps.core.models.experiment import CostConfig


def test_cost_types_and_liquidation_are_conservative_and_separate() -> None:
    calc = CostCalculator(CostConfig(taker_fee=Decimal("0.001"), slippage_bps=10, borrowing_rate=Decimal("0.01"), liquidation_fee_rate=Decimal("0.02")))
    trade = calc.compute_trade_cost(Decimal("2"), Decimal("100"))
    funding = calc.compute_funding_cost(Decimal("2"), Decimal("100"), Decimal("0.001"), "LONG")
    borrowing = calc.compute_borrowing_cost(Decimal("200"), Decimal("0.5"))
    liquidation = calc.compute_liquidation_cost(Decimal("200"))
    assert trade.fee > 0 and trade.slippage > 0
    assert funding == Decimal("0.2")
    assert borrowing == Decimal("1.00")
    assert liquidation == Decimal("4.00")
    assert trade.fee + trade.slippage + liquidation > trade.fee
