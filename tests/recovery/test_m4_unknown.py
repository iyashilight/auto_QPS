import asyncio

from qps.trading.kernel import OrderRecoveryService, PaperBroker


def test_unknown_recovery_queries_original_client_order_id() -> None:
    broker = PaperBroker()
    result = asyncio.run(OrderRecoveryService(broker).recover_unknown("missing"))
    assert result["client_order_id"] == "missing"
    assert result["status"] == "UNKNOWN"

