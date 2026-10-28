from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import RiskReservationManager, TradingFrozenError


def test_reservation_and_command_are_created_as_one_durable_pair() -> None:
    with TemporaryDirectory() as directory:
        manager = RiskReservationManager(JournalDB(Path(directory) / "journal.db"))
        with ThreadPoolExecutor(max_workers=4) as pool:
            def reserve(i: int) -> dict[str, object] | None:
                try:
                    return manager.reserve_and_command(
                        account_id="paper", deployment_id="dep", intent_id=f"i-{i}",
                        instrument_id="BTC", side="BUY", quantity=Decimal("1"), limit_price=Decimal("1"),
                        max_reserved_notional=Decimal("2"))
                except TradingFrozenError:
                    return None

            commands = list(pool.map(reserve, range(4)))
        accepted = [item for item in commands if item is not None]
        assert len({item["reservation_id"] for item in accepted}) == 2
