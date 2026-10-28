from qps.trading.kernel import TradingLifecycle


def test_restart_stays_observe_only_until_manual_enable() -> None:
    lifecycle = TradingLifecycle("paper")
    assert lifecycle.start(lambda: True).value == "OBSERVE_ONLY"
    assert lifecycle.allows() is False
    lifecycle.enable(approved_by="owner", reason="paper restart")
    assert lifecycle.allows() is True

