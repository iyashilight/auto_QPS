"""Paper implementation of the public broker adapter contract.

The adapter deliberately exposes only deterministic Paper behaviour.  It is
safe to construct in production DI, but it never reads credentials or makes
network calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from qps.core.constants import AccountMode, AccountStatus, AccountType
from qps.core.models.account import Account, Position
from qps.core.models.adapter import AdapterStatus
from qps.trading.kernel import PaperBroker

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping


class PaperBrokerAdapter(PaperBroker):
    """Deterministic broker adapter with account/status callbacks."""

    def __init__(self, *, account_id: str = "paper", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.account_id = account_id
        self._connected = False
        self._last_heartbeat: datetime | None = None
        self._execution_callbacks: list[Callable[[dict[str, Any]], None]] = []
        self._account_callbacks: list[Callable[[Account], None]] = []

    async def connect(self) -> None:
        self._connected = True
        self._last_heartbeat = datetime.now(UTC)

    async def disconnect(self) -> None:
        self._connected = False

    def status(self) -> AdapterStatus:
        return AdapterStatus(
            connected=self._connected,
            last_heartbeat=self._last_heartbeat,
        )

    async def get_account(self) -> Account:
        return Account(
            account_id=self.account_id,
            mode=AccountMode.PAPER,
            account_type=AccountType.PERPETUAL,
            status=AccountStatus.ACTIVE,
        )

    async def get_positions(self) -> list[Position]:
        # Paper fills are delivered through the explicit fill() fault-injection
        # API.  Position projection is owned by the Journal/Ledger path.
        return []

    def on_execution_report(self, callback: Callable[[dict[str, Any]], None]) -> None:
        self._execution_callbacks.append(callback)

    def on_account_update(self, callback: Callable[[Account], None]) -> None:
        self._account_callbacks.append(callback)

    async def place_order(self, order: Mapping[str, Any]) -> Mapping[str, Any]:
        result = await super().place_order(order)
        for callback in tuple(self._execution_callbacks):
            callback(dict(result))
        return result


__all__ = ["PaperBrokerAdapter"]
