from qps.trading.kernel import MarketChaosRunner


def test_chaos_runner_is_replayable_and_records_invariants() -> None:
    result = MarketChaosRunner().run({"scenario_id": "jump", "expected_invariants": ["NO_DUPLICATE"]}, seed=7)
    assert result["scenario_id"] == "jump"
    assert result["invariants"] == ["NO_DUPLICATE"]
    assert result["passed"] is True

