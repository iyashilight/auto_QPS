from qps.trading.advanced import LiveWatchdog, NativeProtection


def test_watchdog_and_native_protection_remain_visible_outside_trader() -> None:
    watchdog = LiveWatchdog()
    assert watchdog.monitor()["action"] == "FREEZE"
    protection = NativeProtection()
    protection.arm(approved_by="owner", reason="fixture")
    assert protection.enabled is True
