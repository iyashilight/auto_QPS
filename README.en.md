# autoQps — Personal Mid-Frequency Crypto Trading System

> **English** | [中文](README.md)

A personal, single-machine, single-user mid-frequency quantitative trading system for crypto (spot + perpetual futures).

Goal: a closed loop of **data → research → backtest → paper trading → live trading**.

## Documentation

- Architecture design: [加密货币中频AI量化系统架构设计.md](加密货币中频AI量化系统架构设计.md) *(in Chinese)*
- Implementation spec: [docs/README.md](docs/README.md) *(in Chinese)*
- Engineering conventions: [docs/01-项目结构与工程规范.md](docs/01-项目结构与工程规范.md) *(in Chinese)*

## Quick start

```bash
# 1. Install dependencies (requires uv)
uv sync

# 2. Inspect the command tree
qps --help

# 3. Run tests
uv run pytest

# 4. Static checks
uv run ruff check src/ tests/
uv run mypy src/qps/
```

## Environment variables

| Variable | Purpose |
| --- | --- |
| `QPS_MODE` | Run mode: `paper` / `production` |
| `QPS_DATA_DIR` | Override the data directory |
| `QPS_BINANCE_API_KEY` | Binance API key |
| `QPS_BINANCE_API_SECRET` | Binance API secret |
| `QPS_LOG_LEVEL` | Override the log level |

Secrets are read from environment variables only — never hard-coded into the repository.

## Highlights

- Event-driven backtesting engine with point-in-time data handling
- Strategy SDK and release/runtime separation
- Exchange adapters (Binance spot + perpetual futures) behind a common contract
- Research governance: ADRs, milestone contracts and evidence runs are all versioned under `docs/`

## License

Released under [GPL-3.0](LICENSE).
