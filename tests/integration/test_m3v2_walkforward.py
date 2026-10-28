from datetime import UTC, datetime, timedelta

from qps.research.evaluation.splitter import WalkForwardValidator


def test_two_rolling_windows_are_persistable(tmp_path) -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    records = [{"event_time": start + timedelta(days=index), "label_end": start + timedelta(days=index, hours=1)} for index in range(60)]
    windows = []
    for index in range(2):
        base = start + timedelta(days=index * 20)
        windows.append({
            "train_start": base, "train_end": base + timedelta(days=5),
            "validation_start": base + timedelta(days=6), "validation_end": base + timedelta(days=10),
            "oos_start": base + timedelta(days=11), "oos_end": base + timedelta(days=15),
            "metrics": {"sharpe": "1.0"},
        })
    result = WalkForwardValidator().run(records, windows=windows, trial_id="wf")
    assert len(result) == 2 and result[0].oos_end < result[1].oos_start

