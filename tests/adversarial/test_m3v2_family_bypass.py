from tests.m3v2_helpers import candidate

from qps.research.candidate_family import CandidateFamilyResolver


def test_name_parameter_feature_signal_and_exposure_near_duplicates_share_family(tmp_path) -> None:
    resolver = CandidateFamilyResolver()
    first = candidate("first")
    family = resolver.resolve(first)
    renamed = first.model_copy(update={"candidate_id": "renamed", "candidate_fingerprint": "new"})
    tweaked = first.model_copy(update={"candidate_id": "tweaked", "candidate_fingerprint": "new2", "parameter_structure_hash": "params-structure-v2"})
    assert resolver.resolve(renamed) == family
    assert resolver.resolve(tweaked) == family

