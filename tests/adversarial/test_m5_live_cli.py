from decimal import Decimal

from qps.trading.advanced import LiveApprovalGate


def test_high_risk_action_has_auditable_approval_record() -> None:
    record = LiveApprovalGate(hard_cap=Decimal("10")).authorize(
        deployment_id="d", notional=Decimal("1"), approved_by="owner", reason="test")
    assert record["approval_id"]
    assert record["approved_by"] == "owner"

