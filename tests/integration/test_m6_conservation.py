from decimal import Decimal

from qps.trading.advanced import AllocationReconciler


def test_allocation_conservation_has_explicit_residual() -> None:
    result = AllocationReconciler().reconcile(account_quantity=Decimal("10"), allocated_quantities=[Decimal("6"), Decimal("4")])
    assert result["matched"] is True
    assert result["residual"] == 0

