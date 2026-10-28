from qps.core.models.foundation import CandidateDescriptor
from qps.infra.storage.research_db import ResearchStore
from qps.research.candidate_family import CandidateFamilyResolver


def test_rename_and_near_duplicate_do_not_reset_family(tmp_path) -> None:
    resolver = CandidateFamilyResolver(ResearchStore(tmp_path / "family.db"))
    first = CandidateDescriptor(candidate_id="old-name", candidate_fingerprint="fp-old", strategy_family_id="declared-a", code_structure_hash="code", parameter_structure_hash="params", feature_lineage=("f1", "f2"), signal_sequence_hash="signal", exposure_fingerprint="exposure", exposure_refs=("btc",))
    renamed = first.model_copy(update={"candidate_id": "new-name", "candidate_fingerprint": "fp-new", "strategy_family_id": "declared-b"})
    nearby = first.model_copy(update={"candidate_id": "near", "candidate_fingerprint": "fp-near", "parameter_structure_hash": "params-near", "strategy_family_id": "declared-c"})
    family = resolver.resolve(first)
    assert resolver.resolve(renamed) == family
    assert resolver.resolve(nearby) == family
