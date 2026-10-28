"""Market Data Gateway facade."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from qps.core.models.adapter import AdapterStatus
    from qps.core.models.market import Bar, FundingRate
    from qps.core.ports.market_adapter import MarketAdapterPort


class MarketDataGateway:
    """Constructor-injected facade over the MarketAdapterPort."""

    def __init__(self, adapter: MarketAdapterPort) -> None:
        self.adapter = adapter

    async def connect(self) -> None:
        await self.adapter.connect()

    async def disconnect(self) -> None:
        await self.adapter.disconnect()

    async def subscribe_bars(
        self,
        instrument_id: str,
        interval: str,
        callback: Callable[[Bar], None],
    ) -> str:
        return await self.adapter.subscribe_bars(instrument_id, interval, callback)

    async def subscribe_funding(
        self,
        instrument_id: str,
        callback: Callable[[FundingRate], None],
    ) -> str:
        return await self.adapter.subscribe_funding(instrument_id, callback)

    async def unsubscribe(self, subscription_id: str) -> None:
        await self.adapter.unsubscribe(subscription_id)

    async def fetch_historical_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        return await self.adapter.fetch_historical_bars(instrument_id, interval, start, end)

    def status(self) -> AdapterStatus:
        return self.adapter.status()


__all__ = ["MarketDataGateway"]
