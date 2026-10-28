from datetime import UTC, datetime

import pytest

from qps.core.constants import DatasetSplit
from qps.core.errors import DataError
from qps.core.models.foundation import FeatureEvaluation
from qps.infra.storage.research_db import ResearchStore
from qps.research.evaluation.feature import FeatureEvaluationStore


def evaluation(eid: str, split: DatasetSplit) -> FeatureEvaluation:
    return FeatureEvaluation(evaluation_id=eid, feature_id="f", feature_version=1, strategy_family_id="family-a", experiment_id="exp-a", dataset_split=split, evidence_metrics={"sharpe": "1"}, evaluated_at=datetime(2026, 1, 1, tzinfo=UTC))


def test_scope_dimensions_are_not_globalized_and_blind_is_not_written(tmp_path) -> None:
    store = FeatureEvaluationStore(ResearchStore(tmp_path / "feature.db"))
    store.append(evaluation("e1", DatasetSplit.DEVELOPMENT))
    with pytest.raises(DataError):
        store.append(evaluation("e2", DatasetSplit.BLIND_CHALLENGE))
    assert len(store.list_scoped(feature_id="f", feature_version=1, strategy_family_id="family-a", experiment_id="exp-a", dataset_split=DatasetSplit.DEVELOPMENT)) == 1
