import pytest

from qps.trading.advanced import DeploymentCapabilityService, LiveSafetyError


def test_venue_private_capability_cannot_bypass_public_contract() -> None:
    service = DeploymentCapabilityService()
    with pytest.raises(LiveSafetyError):
        service.configure({"withdraw": True}, {"trade": True})

