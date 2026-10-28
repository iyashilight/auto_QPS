from qps.trading.advanced import LiveWatchdog


def test_runtime_failure_only_freezes_new_risk() -> None:
    watchdog = LiveWatchdog()
    result = watchdog.monitor()
    assert result["action"] == "FREEZE"

