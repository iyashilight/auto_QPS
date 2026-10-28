"""Public Broker adapter wrapper for a recorded or sandbox exchange client."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from qps.core.models.adapter import AdapterStatus
from qps.trading.advanced import LiveSafetyError


class LiveBrokerAdapter:
    def __init__(self, exchange: Any, *, capabilities: dict[str, Any] | None = None) -> None:
        self.exchange = exchange
        self.capabilities = capabilities or {}
        self._connected = False
        self._reconnect_count = 0
        self._last_heartbeat: datetime | None = None
        self._orders: dict[str, dict[str, Any]] = {}
        self._execution_callbacks: list[Any] = []
        self._account_callbacks: list[Any] = []

    async def connect(self) -> None:
        was_connected = self._connected
        await self.exchange.connect()
        self._connected = True
        self._last_heartbeat = datetime.now(UTC)
        if was_connected:
            self._reconnect_count += 1

    async def disconnect(self) -> None:
        await self.exchange.disconnect()
        self._connected = False

    def _require_connected(self) -> None:
        if not self._connected:
            raise LiveSafetyError("Live broker 未连接")

    async def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self._require_connected()
        client_order_id = str(order["client_order_id"])
        if client_order_id in self._orders:
            return dict(self._orders[client_order_id])
        result = dict(await self.exchange.place_order(order))
        result.setdefault("client_order_id", client_order_id)
        self._orders[client_order_id] = dict(result)
        return result

    async def cancel_order(self, client_order_id: str) -> dict[str, Any]:
        self._require_connected()
        result = dict(await self.exchange.cancel_order(client_order_id))
        self._orders[client_order_id] = dict(result)
        return result

    async def get_order(self, client_order_id: str) -> dict[str, Any]:
        self._require_connected()
        if client_order_id in self._orders:
            return dict(self._orders[client_order_id])
        result = dict(await self.exchange.get_order(client_order_id))
        self._orders[client_order_id] = dict(result)
        return result

    async def get_account(self) -> Any:
        self._require_connected()
        return await self.exchange.get_account()

    async def get_positions(self) -> list[Any]:
        self._require_connected()
        return list(await self.exchange.get_positions())

    def on_execution_report(self, callback: Any) -> None:
        self._execution_callbacks.append(callback)
        register = getattr(self.exchange, "on_execution_report", None)
        if callable(register):
            register(callback)

    def on_account_update(self, callback: Any) -> None:
        self._account_callbacks.append(callback)
        register = getattr(self.exchange, "on_account_update", None)
        if callable(register):
            register(callback)

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            connected=self._connected,
            last_heartbeat=self._last_heartbeat,
            reconnect_count=self._reconnect_count,
        )
