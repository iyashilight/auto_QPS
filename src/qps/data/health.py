"""Data freshness state and auditable stale/recovery events."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from qps.core.events import EventEnvelope, EventType

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.infra.storage.meta_db import MetaDB


class DataFreshnessMonitor:
    """Reject new decisions while a data stream is stale."""

    def __init__(
        self,
        max_age: timedelta,
        publish: Callable[[EventEnvelope], Any] | None = None,
        meta_db: MetaDB | None = None,
    ) -> None:
        self._max_age = max_age
        self._publish = publish
        self._meta_db = meta_db
        self._stale: dict[str, bool] = meta_db.latest_data_freshness() if meta_db else {}

    def check(
        self,
        stream_id: str,
        latest_available_at: datetime,
        *,
        now: datetime | None = None,
    ) -> EventEnvelope | None:
        observed_at = now or datetime.now(UTC)
        if (
            observed_at.tzinfo is None or latest_available_at.tzinfo is None
            or observed_at.utcoffset() != timedelta(0)
            or latest_available_at.utcoffset() != timedelta(0)
        ):
            raise ValueError("DATA freshness 时间必须是 UTC")
        stale = observed_at - latest_available_at > self._max_age
        was_stale = self._stale.get(stream_id, False)
        self._stale[stream_id] = stale
        if stale == was_stale:
            return None
        event_type = EventType.DATA_STALE if stale else EventType.DATA_RECOVERED
        event_id = "evt_" + hashlib.sha256(
            f"{stream_id}:{event_type.value}:{observed_at.isoformat()}".encode()
        ).hexdigest()[:24]
        event = EventEnvelope(
            event_id=event_id,
            event_type=event_type,
            occurred_at=observed_at,
            recorded_at=observed_at,
            source="data.health",
            correlation_id=stream_id,
            payload={
                "stream_id": stream_id,
                "latest_available_at": latest_available_at.isoformat(),
                "observed_at": observed_at.isoformat(),
                "max_age_seconds": self._max_age.total_seconds(),
            },
        )
        if self._publish is not None:
            self._publish(event)
        if self._meta_db is not None:
            self._meta_db.record_data_freshness(
                event.event_id, stream_id, stale, dict(event.payload), observed_at.isoformat()
            )
        return event

    def allows_decision(self, stream_id: str) -> bool:
        return not self._stale.get(stream_id, False)
