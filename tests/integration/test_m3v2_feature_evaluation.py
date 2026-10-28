from datetime import UTC, datetime

from qps.core.constants import DatasetSplit
from qps.core.models.foundation import FeatureEvaluation
from qps.infra.storage.research_db import ResearchStore


def test_feature_evaluation_keeps_all_scope_dimensions(tmp_path) -> None:
    store = ResearchStore(tmp_path / "research.db")
    first = FeatureEvaluation(evaluation_id="f1", feature_id="feature", feature_version=1, strategy_family_id="family-a", experiment_id="trial-a", dataset_split=DatasetSplit.VALIDATION, evidence_metrics={"sharpe": "1"}, evaluated_at=datetime.now(UTC))
    second = first.model_copy(update={"evaluation_id": "f2", "strategy_family_id": "family-b", "experiment_id": "trial-b", "dataset_split": DatasetSplit.BLIND_CHALLENGE})
    store.append_feature_evaluation(first)
    store.append_feature_evaluation(second)
    assert first.strategy_family_id != second.strategy_family_id
