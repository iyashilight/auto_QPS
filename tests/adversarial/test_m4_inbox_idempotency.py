import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import InboxProcessor, OutboxDispatcher, PaperBroker, RiskReservationManager


def test_duplicate_execution_report_is_idempotent() -> None:
    with TemporaryDirectory() as directory:
        journal = JournalDB(Path(directory) / "journal.db")
        command = RiskReservationManager(journal).reserve_and_command(
            account_id="paper", deployment_id="dep", intent_id="i", instrument_id="BTC",
            side="BUY", quantity=Decimal("1"), limit_price=Decimal("10"))
        broker = PaperBroker()
        asyncio.run(OutboxDispatcher(journal, broker).dispatch())
        report = broker.fill(command["client_order_id"], price=Decimal("10"), quantity=Decimal("1"), account_id="paper")
        processor = InboxProcessor(journal)
        first = processor.apply(report)
        second = processor.apply(report)
        assert first["status"] == "FILLED"
        assert second["status"] == "DUPLICATE"

