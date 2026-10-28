import pytest

from qps.research.challenge import BlindDatasetVault


def test_blind_vault_rejects_all_public_identities() -> None:
    vault = BlindDatasetVault({"raw_rows": ["secret"], "answer": "secret"})
    for actor in ("cli", "optimizer", "researcher", "future-ai"):
        with pytest.raises(PermissionError):
            vault.public_read(actor)

