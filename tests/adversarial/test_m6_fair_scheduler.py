from qps.trading.advanced import PortfolioRuntimeScheduler


def test_scheduler_deduplicates_and_orders_fairly() -> None:
    assert PortfolioRuntimeScheduler().schedule(["b", "a", "b"]) == ["a", "b"]
