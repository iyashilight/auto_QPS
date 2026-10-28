"""Signal Engine public entry point."""

from qps.trading.port_adapters import SignalEngineAdapter

SignalEngine = SignalEngineAdapter

__all__ = ["SignalEngine", "SignalEngineAdapter"]
