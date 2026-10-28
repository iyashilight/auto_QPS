from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qps.trading.kernel import CapitalPolicyService, TradingFrozenError


def test_policy_expiry_and_whitelist_block_new_risk() -> None:
    service = CapitalPolicyService({
        "eligible_deployments": ("dep",),
        "instrument_limits": {"BTC": "1"},
        "valid_from": datetime.now(UTC) - timedelta(minutes=1),
        "expires_at": datetime.now(UTC) + timedelta(minutes=1),
        "capital_envelope_by_strategy": {"s": "100"},
    })
    service.check(deployment_id="dep", instrument_id="BTC", notional=Decimal("10"))
    with pytest.raises(TradingFrozenError):
        service.check(deployment_id="other", instrument_id="BTC", notional=Decimal("1"))

