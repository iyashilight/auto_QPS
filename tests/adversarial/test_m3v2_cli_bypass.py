import pytest

from qps.core.errors import BacktestError
from qps.research.optimizer.engine import Optimizer


@pytest.mark.asyncio
async def test_ordinary_optimizer_and_legacy_paths_cannot_become_formal() -> None:
    with pytest.raises(BacktestError):
        await Optimizer(object()).search({"formal": True, "trial_id": "forged"})

