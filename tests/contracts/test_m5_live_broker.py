import asyncio

import pytest

from qps.broker.exchange_adapter.live_adapter import LiveBrokerAdapter
from qps.trading.advanced import LiveSafetyError


class FixtureExchange:
    def __init__(self) -> None:
        self.execution_callback = None
        self.account_callback = None

    async def connect(self) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    async def place_order(self, order: dict[str, object]) -> dict[str, object]:
        return {"client_order_id": order["client_order_id"], "status": "WORKING"}

    async def cancel_order(self, client_order_id: str) -> dict[str, object]:
        return {"client_order_id": client_order_id, "status": "CANCELED"}

    async def get_order(self, client_order_id: str) -> dict[str, object]:
        return {"client_order_id": client_order_id, "status": "UNKNOWN"}

    async def get_account(self) -> dict[str, object]:
        return {"account_id": "fixture"}

    async def get_positions(self) -> list[object]:
        return []

    def on_execution_report(self, callback: object) -> None:
        self.execution_callback = callback

    def on_account_update(self, callback: object) -> None:
        self.account_callback = callback


def test_live_adapter_delegates_public_minimum_contract() -> None:
    exchange = FixtureExchange()
    adapter = LiveBrokerAdapter(exchange)
    asyncio.run(adapter.connect())
    result = asyncio.run(adapter.place_order({"client_order_id": "same-id"}))
    assert result["client_order_id"] == "same-id"
    assert asyncio.run(adapter.place_order({"client_order_id": "same-id"})) == result
    assert asyncio.run(adapter.get_order("same-id"))["status"] == "WORKING"
    assert asyncio.run(adapter.get_order("unknown-id"))["status"] == "UNKNOWN"
    assert asyncio.run(adapter.get_account())["account_id"] == "fixture"
    assert asyncio.run(adapter.get_positions()) == []
    def execution_callback(_: object) -> None:
        pass

    def account_callback(_: object) -> None:
        pass
    adapter.on_execution_report(execution_callback)
    adapter.on_account_update(account_callback)
    assert exchange.execution_callback is execution_callback
    assert exchange.account_callback is account_callback
    assert adapter.status().connected is True


def test_live_adapter_fails_closed_when_disconnected() -> None:
    adapter = LiveBrokerAdapter(FixtureExchange())
    with pytest.raises(LiveSafetyError):
        asyncio.run(adapter.place_order({"client_order_id": "not-connected"}))
