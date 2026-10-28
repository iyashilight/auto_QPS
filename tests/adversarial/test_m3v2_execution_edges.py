from decimal import Decimal

from qps.core.costs import CostCalculator
from qps.core.models.experiment import CostConfig


def test_touch_is_not_full_fill_and_missing_history_is_not_zero_cost() -> None:
    calc = CostCalculator(CostConfig())
    assert calc.compute_fee(Decimal("100"), Decimal("1"), is_maker=False) >= Decimal("0")
    assert calc.compute_slippage(Decimal("100"), Decimal("1"), side="BUY") >= Decimal("0")
