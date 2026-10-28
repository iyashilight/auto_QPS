from qps.trading.advanced import FillAllocator


def test_fill_uses_precommitted_ratios() -> None:
    result = FillAllocator().allocate(fill={"quantity": "10"}, plan={"persisted": True, "allocations": [{"strategy_id": "a", "ratio": "0.6"}, {"strategy_id": "b", "ratio": "0.4"}]})
    assert [item["quantity"] for item in result] == [6, 4]
