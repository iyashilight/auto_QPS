from decimal import Decimal
from tempfile import TemporaryDirectory

import pytest

from qps.trading.advanced import LiveApprovalGate, LiveSafetyError


def test_live_requires_approval_and_hard_cap() -> None:
    with TemporaryDirectory() as directory:
        gate = LiveApprovalGate(hard_cap=Decimal("100"), audit_path=f"{directory}/approvals.jsonl")
        with pytest.raises(LiveSafetyError):
            gate.authorize(deployment_id="d", notional=Decimal("101"), approved_by="owner", reason="too large")
        gate.authorize(deployment_id="d", notional=Decimal("1"), approved_by="owner", reason="bounded")
        with open(f"{directory}/approvals.jsonl", encoding="utf-8") as audit_file:
            assert "approval_id" in audit_file.read()
