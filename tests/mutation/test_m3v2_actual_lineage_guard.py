import pytest
from tests.m3v2_helpers import candidate, registration, release

from qps.core.errors import DataError
from qps.research.research_run_service import ResearchRunService


def test_registration_cannot_supply_missing_actual_lineage() -> None:
    incomplete = registration("missing").model_copy(update={"historical_rule_hash": None})
    with pytest.raises(DataError):
        ResearchRunService._require_registration(incomplete, candidate("missing"), release().release_id)

