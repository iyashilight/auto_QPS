import pytest

from qps.research.challenge import BlindChallengeRunner, BlindDatasetVault


def test_blind_raw_domain_rejects_developer_identity(tmp_path) -> None:
    runner = BlindChallengeRunner(
        vault=BlindDatasetVault({"answer": "secret", "interval_key": "2026-01"}),
        budget_port=object(),
        research_store=object(),
    )

    with pytest.raises(PermissionError):
        runner.read_raw(actor="developer")
    assert runner.raw_read_count == 0
