from qps.trading.advanced import AllocationPlanner


def test_deterministic_netting_is_order_independent() -> None:
    planner = AllocationPlanner()
    intents = [{"intent_id": "a", "instrument_id": "BTC", "side": "BUY", "quantity": "2"}, {"intent_id": "b", "instrument_id": "BTC", "side": "SELL", "quantity": "1"}]
    first = planner.construct(intents, account_id="a")["net_targets"]
    second = planner.construct(list(reversed(intents)), account_id="a")["net_targets"]
    assert first == second == {"BTC": "1"}

