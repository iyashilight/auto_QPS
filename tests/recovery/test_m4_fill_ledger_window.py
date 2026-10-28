from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import AccountLedger


def test_fill_ledger_replay_is_idempotent_after_restart() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "journal.db"
        fill = {
            "fill_id": "f",
            "trade_id": "fill:restart",
            "account_id": "a",
            "instrument_id": "BTC",
            "side": "BUY",
            "quantity": Decimal("1"),
            "price": Decimal("10"),
        }
        first = AccountLedger(JournalDB(database)).post_fill(fill, strategy_id="s")
        second = AccountLedger(JournalDB(database)).post_fill(fill, strategy_id="s")
        assert first == second
        assert len(AccountLedger(JournalDB(database)).entries("a", strategy_id="s")) == 2
