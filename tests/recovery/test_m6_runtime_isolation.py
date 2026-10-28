from qps.trading.advanced import PortfolioRuntimeSupervisor


def test_strategy_crash_freezes_only_that_strategy() -> None:
    supervisor = PortfolioRuntimeSupervisor()
    result = supervisor.poll("broken", healthy=False)
    assert result["frozen"] is True
    assert result["other_strategies_affected"] is False

