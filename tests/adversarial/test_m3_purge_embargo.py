from datetime import UTC, datetime, timedelta

from qps.research.evaluation.splitter import ValidationSplitter


def test_label_overlap_is_purged_and_embargoed() -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    records = [
        {"event_time": ts, "label_end": ts + timedelta(hours=2)},
        {"event_time": ts + timedelta(hours=2), "label_end": ts + timedelta(hours=2, minutes=1)},
        {"event_time": ts + timedelta(hours=3, minutes=30), "label_end": ts + timedelta(hours=3, minutes=31)},
    ]
    result = ValidationSplitter().split(records, validation_start=ts + timedelta(hours=1), validation_end=ts + timedelta(hours=3), embargo=timedelta(hours=1))
    assert result.purged == (records[0],)
    assert result.validation == (records[1],)
    assert result.embargoed == (records[2],)
