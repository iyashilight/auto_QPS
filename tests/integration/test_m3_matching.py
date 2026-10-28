from datetime import UTC, datetime, timedelta
from decimal import Decimal

from qps.core.costs import CostCalculator
from qps.core.models.experiment import CostConfig
from qps.core.models.market import Bar
from qps.research.backtest.event_driven import EventDrivenBacktestEngine


def make_bar() -> Bar:
    ts = datetime(2026, 1, 1, tzinfo=UTC)
    return Bar(instrument_id="btc", open_time=ts, interval="5m", open=Decimal("100"), high=Decimal("110"), low=Decimal("90"), close=Decimal("105"), volume=Decimal("100"), quote_volume=Decimal("10000"), data_version="v1", event_time=ts, available_at=ts, ingested_at=ts, close_time=ts + timedelta(minutes=5), is_closed=True, decision_time=ts + timedelta(minutes=5))


def test_market_limit_partial_delay_and_expiry_are_observable() -> None:
    engine = EventDrivenBacktestEngine.__new__(EventDrivenBacktestEngine)
    cost = CostCalculator(CostConfig(slippage_bps=0))
    bar = make_bar()
    partial = engine.match(order={"order_type": "LIMIT", "side": "BUY", "price": Decimal("100"), "quantity": Decimal("5"), "available_liquidity": Decimal("2"), "requested_at": bar.open_time}, bar=bar, available_cash=Decimal("1000"), cost_calc=cost)
    assert partial["status"] == "PARTIALLY_FILLED"
    delayed = engine.match(order={"order_type": "MARKET", "side": "BUY", "quantity": Decimal("1"), "requested_at": bar.open_time + timedelta(minutes=1)}, bar=bar, available_cash=Decimal("1000"), cost_calc=cost)
    assert delayed["status"] == "PENDING"
    expired = engine.match(order={"order_type": "LIMIT", "side": "BUY", "price": Decimal("100"), "quantity": Decimal("1"), "expires_at": bar.open_time}, bar=bar, available_cash=Decimal("1000"), cost_calc=cost)
    assert expired["status"] == "EXPIRED"
