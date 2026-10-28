import pytest

from qps.core.errors import BacktestError
from qps.research.optimizer.engine import Optimizer


@pytest.mark.asyncio
async def test_legacy_optimizer_cannot_claim_formal_or_budgeted_run() -> None:
    with pytest.raises(BacktestError):
        await Optimizer(object()).search({"formal": True})

