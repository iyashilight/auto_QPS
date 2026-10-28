from pathlib import Path
from tempfile import TemporaryDirectory

from qps.trading.advanced import StrategyLedger


def test_strategy_ledger_is_append_only_and_idempotent() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "portfolio.db"
        ledger = StrategyLedger(database)
        first = ledger.append({"strategy_id": "s", "idempotency_key": "k", "amount": "1"})
        assert ledger.append({"strategy_id": "s", "idempotency_key": "k", "amount": "2"}) == first
        assert StrategyLedger(database).entries("s") == [first]
