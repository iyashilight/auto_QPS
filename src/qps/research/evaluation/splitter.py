"""Purged/embargoed dataset split，不以简单时间排序代替标签窗口隔离。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from collections.abc import Iterable

from qps.core.errors import DataError
from qps.core.models.foundation import WalkForwardWindow


@dataclass(frozen=True)
class SplitResult:
    development: tuple[Any, ...]
    validation: tuple[Any, ...]
    purged: tuple[Any, ...]
    embargoed: tuple[Any, ...]


class ValidationSplitter:
    def split(self, records: Iterable[Any], *, validation_start: datetime,
              validation_end: datetime, embargo: timedelta = timedelta(0),
              label_horizon: timedelta = timedelta(0)) -> SplitResult:
        if validation_end <= validation_start:
            raise DataError(detail="validation 区间必须递增")
        development: list[Any] = []
        validation: list[Any] = []
        purged: list[Any] = []
        embargoed: list[Any] = []
        embargo_end = validation_end + embargo
        for record in records:
            start = self._time(record, "event_time", "timestamp", "ts")
            label_end = self._time(record, "label_end", "close_time", "end", default=start + label_horizon)
            if validation_start <= start < validation_end:
                validation.append(record)
            elif start < validation_start:
                if label_end > validation_start:
                    purged.append(record)
                else:
                    development.append(record)
            elif validation_end <= start < embargo_end:
                embargoed.append(record)
            else:
                development.append(record)
        return SplitResult(tuple(development), tuple(validation), tuple(purged), tuple(embargoed))

    @staticmethod
    def _time(record: Any, *names: str, default: datetime | None = None) -> datetime:
        for name in names:
            value = record.get(name) if isinstance(record, dict) else getattr(record, name, None)
            if value is not None:
                if not isinstance(value, datetime) or value.tzinfo is None:
                    raise DataError(detail=f"split 时间必须是 aware datetime: {name}")
                return value
        if default is not None:
            return default
        raise DataError(detail="记录缺少 event/label 时间")


class WalkForwardValidator:
    """至少两个、严格 UTC 推进且不可关闭 purge/embargo 的验证器。"""

    def __init__(self, store: Any | None = None) -> None:
        self.store = store

    def run(
        self, records: list[Any], *, windows: list[dict[str, Any]], trial_id: str = "walk-forward",
        parameters: dict[str, Any] | None = None, purge: timedelta = timedelta(0),
        embargo: timedelta = timedelta(0), disable_purge: bool = False,
    ) -> tuple[WalkForwardWindow, ...]:
        if disable_purge or purge < timedelta(0) or embargo < timedelta(0):
            raise DataError(detail="walk-forward 不允许关闭或逆向 purge/embargo")
        if len(windows) < 2:
            raise DataError(detail="walk-forward 至少需要两个滚动窗口")
        result: list[WalkForwardWindow] = []
        previous_oos_end: datetime | None = None
        for index, spec in enumerate(windows):
            times = tuple(spec.get(key) for key in (
                "train_start", "train_end", "validation_start", "validation_end", "oos_start", "oos_end",
            ))
            if any(not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None for value in times):
                raise DataError(detail="walk-forward 窗口必须使用 UTC aware datetime")
            typed_times = cast("tuple[datetime, datetime, datetime, datetime, datetime, datetime]", times)
            train_start, train_end, validation_start, validation_end, oos_start, oos_end = typed_times
            if not (train_start < train_end < validation_start < validation_end < oos_start < oos_end):
                raise DataError(detail="walk-forward 窗口时间顺序非法")
            if previous_oos_end is not None and oos_start <= previous_oos_end:
                raise DataError(detail="walk-forward OOS 窗口必须严格向前推进")
            previous_oos_end = oos_end
            # Apply the actual split guard even when a caller supplies pre-cut records.
            available_records = [item for item in records if self._event_time(item) < oos_start]
            split = ValidationSplitter().split(
                available_records, validation_start=validation_start, validation_end=validation_end,
                embargo=embargo, label_horizon=purge,
            )
            if any(self._event_time(item) >= oos_start for item in split.development + split.validation):
                raise DataError(detail="walk-forward 训练/验证数据包含 OOS 未来行")
            input_payload = {
                "trial_id": trial_id, "window": [value.isoformat() for value in typed_times],
                "records": [self._plain(item) for item in records],
            }
            input_hash = hashlib.sha256(self._canonical(input_payload).encode()).hexdigest()
            parameter_hash = hashlib.sha256(self._canonical(parameters or {}).encode()).hexdigest()
            window = WalkForwardWindow(
                window_id=f"{trial_id}:wf:{index}", train_start=train_start, train_end=train_end,
                validation_start=validation_start, validation_end=validation_end,
                oos_start=oos_start, oos_end=oos_end,
                purge=str(purge), embargo=str(embargo), input_hash=input_hash,
                parameter_hash=parameter_hash, metrics=spec.get("metrics", {}),
                failure_reason=spec.get("failure_reason"),
                status=str(spec.get("status", "SUCCEEDED")),
            )
            result.append(window)
            if self.store is not None:
                payload = self._canonical(window)
                self.store.append_artifact(
                    artifact_id=window.window_id, trial_id=trial_id,
                    artifact_type="walk-forward-window",
                    content_hash=hashlib.sha256(payload.encode()).hexdigest(),
                    payload=window, created_at=window.oos_end,
                )
        return tuple(result)

    @staticmethod
    def _event_time(value: Any) -> datetime:
        return ValidationSplitter._time(value, "event_time", "timestamp", "ts")

    @staticmethod
    def _plain(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if isinstance(value, dict):
            return {str(k): WalkForwardValidator._plain(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [WalkForwardValidator._plain(v) for v in value]
        return value

    @classmethod
    def _canonical(cls, value: Any) -> str:
        return json.dumps(cls._plain(value), sort_keys=True, separators=(",", ":"), default=str)


__all__ = ["SplitResult", "ValidationSplitter", "WalkForwardValidator"]
