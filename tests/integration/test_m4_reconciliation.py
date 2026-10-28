from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import ReconciliationService, TradingLifecycle


def test_reconciliation_difference_is_explicit_and_freezes() -> None:
    with TemporaryDirectory() as directory:
        lifecycle = TradingLifecycle("paper")
        result = ReconciliationService(JournalDB(Path(directory) / "journal.db"), lifecycle).run(
            "paper", local={"balance": "1"}, remote={"balance": "2"})
        assert result["status"] == "DIFF"
        assert lifecycle.mode.value == "RISK_REDUCE_ONLY"

