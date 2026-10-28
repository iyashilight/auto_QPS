from datetime import UTC, datetime

import pytest

from qps.core.constants import DatasetSplit
from qps.core.errors import DataError
from qps.core.models.foundation import FeatureEvaluation
from qps.infra.storage.research_db import ResearchStore


def test_feature_scope_duplicate_and_blind_registry_write_are_rejected(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")
    value = FeatureEvaluation(evaluation_id="same", feature_id="feature", feature_version=1, strategy_family_id="family", experiment_id="trial", dataset_split=DatasetSplit.BLIND_CHALLENGE, evidence_metrics={"score": "1"}, evaluated_at=datetime.now(UTC))
    store.append_feature_evaluation(value)
    with pytest.raises(DataError):
        store.append_feature_evaluation(value.model_copy(update={"evaluation_id": "different", "evidence_metrics": {"score": "2"}}))
