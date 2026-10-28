from qps.trading.kernel import TradingLifecycle


def test_storage_failure_freezes_new_risk() -> None:
    lifecycle = TradingLifecycle("paper")
    lifecycle.start(lambda: True)
    lifecycle.enable(approved_by="owner", reason="test")
    lifecycle.freeze("storage unavailable")
    assert lifecycle.allows() is False

