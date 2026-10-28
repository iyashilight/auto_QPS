import asyncio
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import OutboxDispatcher, PaperBroker, RiskReservationManager


def test_dispatch_retry_reuses_same_client_order_id() -> None:
    with TemporaryDirectory() as directory:
        journal = JournalDB(Path(directory) / "journal.db")
        command = RiskReservationManager(journal).reserve_and_command(
            account_id="paper", deployment_id="dep", intent_id="i", instrument_id="BTC",
            side="BUY", quantity=Decimal("1"), limit_price=Decimal("10"))
        broker = PaperBroker()
        asyncio.run(OutboxDispatcher(journal, broker).dispatch())
        assert broker.orders[command["client_order_id"]].client_order_id == command["client_order_id"]
        assert broker.place_calls == 1

