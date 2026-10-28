from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import AccountLedger


def test_account_and_strategy_ledger_record_fill_once() -> None:
    with TemporaryDirectory() as directory:
        ledger = AccountLedger(JournalDB(Path(directory) / "journal.db"))
        fill = {"fill_id": "f", "trade_id": "trade-1", "account_id": "a", "instrument_id": "BTC", "side": "BUY", "quantity": Decimal("1"), "price": Decimal("100"), "fee": Decimal("1"), "fee_currency": "USDT"}
        first = ledger.post_fill(fill, strategy_id="s")
        second = ledger.post_fill(fill, strategy_id="s")
        assert first == second
        assert len(ledger.entries("a", strategy_id="s")) == 3
