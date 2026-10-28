"""Candidate family 归属：结构、特征、信号和暴露共同决定统计族。"""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import TYPE_CHECKING

from qps.core.errors import DataError
from qps.infra.storage.research_db import ResearchStore, canonical_json

if TYPE_CHECKING:
    from qps.core.models.foundation import CandidateDescriptor


class CandidateFamilyResolver:
    def __init__(self, store: ResearchStore | None = None, *, similarity_threshold: float = 0.75) -> None:
        self.store = store
        self.similarity_threshold = similarity_threshold
        self._families: dict[str, CandidateDescriptor] = dict(store.list_candidate_families()) if store else {}

    @staticmethod
    def _jaccard(left: tuple[str, ...], right: tuple[str, ...]) -> float:
        a, b = set(left), set(right)
        if not a and not b:
            return 1.0
        return len(a & b) / max(1, len(a | b))

    def _similarity(self, left: CandidateDescriptor, right: CandidateDescriptor) -> float:
        code = 1.0 if left.code_structure_hash == right.code_structure_hash else 0.0
        params = 1.0 if left.parameter_structure_hash == right.parameter_structure_hash else 0.0
        features = self._jaccard(left.feature_lineage, right.feature_lineage)
        signal = 1.0 if left.signal_sequence_hash == right.signal_sequence_hash else 0.0
        exposure = 1.0 if left.exposure_fingerprint == right.exposure_fingerprint else self._jaccard(left.exposure_refs, right.exposure_refs)
        return 0.30 * code + 0.20 * params + 0.20 * features + 0.20 * signal + 0.10 * exposure

    def resolve(self, candidate: CandidateDescriptor, *, resolved_at: datetime | None = None) -> str:
        if resolved_at is None:
            resolved_at = datetime.now(UTC)
        for family_id, known in sorted(self._families.items()):
            if candidate.candidate_fingerprint == known.candidate_fingerprint or self._similarity(candidate, known) >= self.similarity_threshold:
                return family_id
        basis = {
            "code": candidate.code_structure_hash,
            "params": candidate.parameter_structure_hash,
            "features": sorted(candidate.feature_lineage),
            "signal": candidate.signal_sequence_hash,
            "exposure": candidate.exposure_fingerprint,
        }
        family_id = candidate.strategy_family_id or f"family-{sha256(canonical_json(basis).encode()).hexdigest()[:20]}"
        self._families[family_id] = candidate
        if self.store:
            self.store.save_candidate_family(family_id, candidate, resolved_at)
        return family_id

    def resolve_or_reject_mismatch(self, candidate: CandidateDescriptor, declared_family: str) -> str:
        family_id = self.resolve(candidate)
        if declared_family and declared_family != family_id:
            # A caller cannot select a fresh family to evade the known family.
            known = self._families.get(family_id)
            if known is not None:
                raise DataError(detail=f"candidate family 声明与结构解析不一致: {declared_family} != {family_id}")
        return family_id


__all__ = ["CandidateFamilyResolver"]
