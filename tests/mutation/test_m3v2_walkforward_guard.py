from datetime import UTC, datetime, timedelta

import pytest

from qps.core.errors import DataError
from qps.research.evaluation.splitter import WalkForwardValidator


def test_purge_embargo_cannot_be_disabled_or_single_window_used() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    spec = {"train_start": start, "train_end": start + timedelta(days=1), "validation_start": start + timedelta(days=2), "validation_end": start + timedelta(days=3), "oos_start": start + timedelta(days=4), "oos_end": start + timedelta(days=5), "metrics": {}}
    with pytest.raises(DataError):
        WalkForwardValidator().run([], windows=[spec, {**spec, "train_start": start + timedelta(days=10), "train_end": start + timedelta(days=11), "validation_start": start + timedelta(days=12), "validation_end": start + timedelta(days=13), "oos_start": start + timedelta(days=14), "oos_end": start + timedelta(days=15)}], disable_purge=True)

