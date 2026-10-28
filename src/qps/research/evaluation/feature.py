"""按 feature/family/experiment/split 隔离的 FeatureEvaluation。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qps.core.constants import DatasetSplit
from qps.core.errors import DataError
from qps.core.models.foundation import FeatureEvaluation

if TYPE_CHECKING:
    from qps.infra.storage.research_db import ResearchStore


class FeatureEvaluationStore:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def append(self, evaluation: FeatureEvaluation) -> None:
        if evaluation.dataset_split == DatasetSplit.BLIND_CHALLENGE:
            raise DataError(detail="Blind FeatureEvaluation 必须留在受限结果域，不能回写普通 Registry")
        self.store.append_feature_evaluation(evaluation)

    async def record_evaluation(self, evaluation: FeatureEvaluation) -> None:
        self.append(evaluation)

    def record_blind_result(self, evaluation: FeatureEvaluation) -> None:
        # Deliberately no-op to the ordinary store; caller must use isolated runner output.
        if evaluation.dataset_split != DatasetSplit.BLIND_CHALLENGE:
            raise DataError(detail="只有 BLIND_CHALLENGE 可以写入 isolated blind result")

    def list_scoped(self, *, feature_id: str, feature_version: int,
                    strategy_family_id: str, experiment_id: str,
                    dataset_split: DatasetSplit) -> list[FeatureEvaluation]:
        with self.store.connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM research_feature_evaluations WHERE feature_id=? AND feature_version=? AND strategy_family_id=? AND experiment_id=? AND dataset_split=?",
                (feature_id, feature_version, strategy_family_id, experiment_id, dataset_split.value),
            ).fetchall()
        return [FeatureEvaluation.model_validate(__import__("json").loads(row["payload"])) for row in rows]


__all__ = ["FeatureEvaluationStore"]
