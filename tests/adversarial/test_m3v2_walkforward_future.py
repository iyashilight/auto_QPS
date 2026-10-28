from datetime import UTC, datetime, timedelta

import pytest

from qps.core.errors import DataError
from qps.research.evaluation.splitter import WalkForwardValidator


def test_future_or_reordered_window_is_rejected() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    spec = {"train_start": start, "train_end": start + timedelta(days=5), "validation_start": start + timedelta(days=6), "validation_end": start + timedelta(days=10), "oos_start": start + timedelta(days=11), "oos_end": start + timedelta(days=15), "metrics": {}}
    with pytest.raises(DataError):
        WalkForwardValidator().run([], windows=[spec], trial_id="one")

