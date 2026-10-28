from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qps.core.costs import CostCalculator
from qps.core.models.experiment import CostConfig
from qps.core.models.market import Bar
from qps.research.backtest.event_driven import EventDrivenBacktestEngine


def test_limit_touch_does_not_imply_full_fill() -> None:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    bar = Bar(instrument_id="btc", open_time=ts, interval="5m", open=Decimal("100"), high=Decimal("105"), low=Decimal("95"), close=Decimal("100"), volume=Decimal("1"), quote_volume=Decimal("100"), data_version="v1", event_time=ts, available_at=ts, ingested_at=ts, close_time=ts + timedelta(minutes=5), is_closed=True, decision_time=ts + timedelta(minutes=5))
    result = EventDrivenBacktestEngine.__new__(EventDrivenBacktestEngine).match(order={"order_type": "LIMIT", "side": "BUY", "price": Decimal("100"), "quantity": Decimal("10"), "available_liquidity": Decimal("0.5"), "requested_at": ts}, bar=bar, available_cash=Decimal("1000"), cost_calc=CostCalculator(CostConfig(slippage_bps=0)))
    assert result["status"] == "PARTIALLY_FILLED"
    assert result["filled_quantity"] == Decimal("0.5")
