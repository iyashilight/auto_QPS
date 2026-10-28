import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import OutboxDispatcher, PaperBroker, RiskReservationManager


def test_broker_is_not_called_before_dispatch_of_committed_outbox() -> None:
    with TemporaryDirectory() as directory:
        journal = JournalDB(Path(directory) / "journal.db")
        command = RiskReservationManager(journal).reserve_and_command(
            account_id="paper", deployment_id="dep", intent_id="i", instrument_id="BTC",
            side="BUY", quantity=Decimal("1"), limit_price=Decimal("10"))
        broker = PaperBroker()
        assert broker.place_calls == 0
        asyncio.run(OutboxDispatcher(journal, broker).dispatch())
        assert broker.place_calls == 1
        assert command["client_order_id"] in broker.orders

