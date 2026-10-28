"""正式 EventDriven 的 PIT/标的/周期/历史规则解析。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime

from qps.core.errors import BacktestError, DataError


class BacktestDataResolver:
    def __init__(self, data_port: Any, *, manifest_store: Any = None, rule_port: Any = None) -> None:
        self.data_port = data_port
        self.manifest_store = manifest_store
        self.rule_port = rule_port

    async def resolve(self, *, instrument_id: str, interval: str, start: datetime,
                      end: datetime, decision_time: datetime, manifest_id: str) -> list[Any]:
        if decision_time.tzinfo is None or decision_time.utcoffset() is None:
            raise DataError(detail="decision_time 必须是 UTC aware datetime")
        if self.manifest_store is not None:
            manifest = self.manifest_store.get(manifest_id)
            self.manifest_store.verify_pit_manifest(
                manifest, expected_dataset_types={manifest.dataset_type},
                instrument_id=instrument_id, interval=interval, require_formal=True,
            )
        rows = await self.data_port.query(
            instrument_id, start, end, decision_time=decision_time, manifest_id=manifest_id,
        )
        result: list[Any] = []
        for row in rows:
            self.validate_row(row, instrument_id=instrument_id, interval=interval, decision_time=decision_time)
            result.append(row)
        return sorted(result, key=lambda item: item.open_time)

    @staticmethod
    def validate_row(row: Any, *, instrument_id: str, interval: str, decision_time: datetime) -> None:
        actual_instrument = getattr(row, "instrument_id", None)
        actual_interval = getattr(row, "interval", None)
        if actual_instrument != instrument_id:
            raise DataError(detail=f"PIT 标的污染: {actual_instrument} != {instrument_id}")
        if actual_interval != interval:
            raise DataError(detail=f"PIT 周期污染: {actual_interval} != {interval}")
        for name in ("event_time", "available_at", "close_time"):
            value = getattr(row, name, None)
            if value is None or value.tzinfo is None or value.utcoffset() is None:
                raise DataError(detail=f"正式 PIT 行缺少 UTC {name}")
            if value > decision_time:
                raise DataError(detail=f"PIT 未来数据不可见: {name} > decision_time")
        if getattr(row, "is_closed", None) is not True:
            raise DataError(detail="正式 bars 必须是 closed")
        available = row.available_at
        event_time = row.event_time
        close_time = row.close_time
        if not (available <= decision_time and event_time <= decision_time and close_time <= decision_time):
            raise DataError(detail="PIT 可见性约束失败")

    async def resolve_rule(self, *, instrument_id: str, effective_at: datetime,
                           decision_time: datetime) -> Any:
        if (
            effective_at.tzinfo is None or effective_at.utcoffset() is None
            or decision_time.tzinfo is None or decision_time.utcoffset() is None
        ):
            raise DataError(detail="instrument rule 查询必须使用 UTC aware datetime")
        if self.rule_port is None:
            raise BacktestError(detail="正式回测缺少历史 instrument rule resolver")
        resolver = (
            getattr(self.rule_port, "resolve", None)
            or getattr(self.rule_port, "resolve_at", None)
            or getattr(self.rule_port, "get_instrument_rule", None)
        )
        if resolver is None:
            raise BacktestError(detail="rule port 缺少 resolve")
        rule = resolver(instrument_id, effective_at=effective_at, known_at=decision_time)
        if hasattr(rule, "__await__"):
            rule = await rule
        if rule is None or rule.instrument_id != instrument_id or rule.available_at > decision_time:
            raise DataError(detail="未来或跨标的 instrument rule 被拒绝")
        if rule.valid_from > effective_at or (rule.valid_to is not None and effective_at >= rule.valid_to):
            raise DataError(detail="instrument rule 不在历史有效窗口")
        return rule


__all__ = ["BacktestDataResolver"]
