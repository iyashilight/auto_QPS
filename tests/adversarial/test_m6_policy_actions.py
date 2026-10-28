import pytest

from qps.trading.kernel import CapitalPolicyService, TradingFrozenError


def test_policy_does_not_allow_automatic_expansion() -> None:
    service = CapitalPolicyService({"capital_envelope_by_strategy": {"s": "10"}})
    with pytest.raises(TradingFrozenError):
        service.check(deployment_id="d", instrument_id="BTC", notional=11)

