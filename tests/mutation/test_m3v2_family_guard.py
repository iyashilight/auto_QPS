from tests.m3v2_helpers import candidate

from qps.research.candidate_family import CandidateFamilyResolver


def test_family_fingerprint_uses_multiple_dimensions() -> None:
    resolver = CandidateFamilyResolver()
    base = candidate("base")
    family = resolver.resolve(base)
    altered = base.model_copy(update={"strategy_family_id": None, "candidate_fingerprint": "new-fingerprint", "code_structure_hash": "other", "signal_sequence_hash": "other", "exposure_fingerprint": "other", "exposure_refs": ("eth",)})
    assert resolver.resolve(altered) != family
