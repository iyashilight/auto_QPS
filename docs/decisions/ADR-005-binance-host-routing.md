# ADR-005: Binance 现货/永续主机路由

## 背景

Binance API 对现货（Spot）和永续合约（Futures）使用不同的主机端点：

| 市场 | REST 主机 | WS 主机 |
|------|-----------|---------|
| 现货 | `https://api.binance.com` | `wss://stream.binance.com:9443/ws` |
| 永续 | `https://fapi.binance.com` | `wss://fstream.binance.com/ws` |

当前 `BinanceExchangeAdapter` 只有单一 base_url（默认现货主机），所有 `/fapi/v1/*` 与 `/futures/data/*` 请求都发到现货主机，导致实盘采集 PERP 数据失败。

## 决策

在 `ExchangeAdapterPort` 增加 `market` 参数（`Literal["spot", "futures"] = "spot"`），按市场选择主机：

1. **REST 路由**：`request(method, path, params, signed, market)` 根据 `path` 前缀或显式 `market` 参数选择主机
2. **WS 路由**：`subscribe(channel, payload, callback, market)` 根据 `market` 选择 WS 主机
3. **Market Adapter 调用**：`BinanceMarketAdapter` 封装 `_market_of(instrument_id)` helper，PERP 标的走 futures 主机

## 实现要点

### ExchangeAdapter

```python
class BinanceExchangeAdapter:
    def __init__(
        self,
        base_url: str = "https://api.binance.com",           # 现货 REST
        futures_base_url: str = "https://fapi.binance.com",   # 永续 REST
        ws_base_url: str = "wss://stream.binance.com:9443/ws",  # 现货 WS
        futures_ws_base_url: str = "wss://fstream.binance.com/ws",  # 永续 WS
        ...
    ):
        ...

    async def request(self, method, path, params=None, signed=False, market="spot"):
        # 选择主机
        if market == "futures" or path.startswith("/fapi/") or path.startswith("/futures/"):
            client = self._futures_client  # 使用永续客户端
        else:
            client = self._spot_client      # 使用现货客户端
        ...

    async def subscribe(self, channel, payload, callback, market="spot"):
        # 选择 WS 主机
        if market == "futures":
            ws_url = self._futures_ws_base_url
        else:
            ws_url = self._ws_base_url
        ...
```

### MarketAdapter

```python
class BinanceMarketAdapter:
    def _market_of(self, instrument_id: str) -> str:
        """判断标的属于哪个市场"""
        if "-PERP" in instrument_id.upper():
            return "futures"
        return "spot"

    async def fetch_historical_bars(self, instrument_id, interval, start, end):
        market = self._market_of(instrument_id)
        klines_path = "/fapi/v1/klines" if market == "futures" else "/api/v3/klines"
        raw_data = await self._exchange_adapter.request("GET", klines_path, params, market=market)
        ...
```

## 配置兼容

- `configs/default.yaml` 新增键：`futures_base_url`、`futures_ws_base_url`
- 不删改既有键，保持向后兼容
- 环境变量沿用：`QPS_BINANCE_API_KEY`、`QPS_BINANCE_API_SECRET`

## 测试策略

- 契约测试 mock 层断言「PERP 请求打到 futures 主机、spot 请求打到现货主机」
- REST 检查路径前缀/主机
- WS 检查连接 URL
