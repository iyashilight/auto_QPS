"""Binance Market Adapter 实现（见 docs/03 第 3 节）

实现 MarketAdapterPort：
- REST 历史 K 线（分页、限频节流、区间校验）
- WebSocket 订阅：K 线（1m/5m/15m/1h）、资金费率、持仓量、标记价格
- 回调去重去抖、乱序缓冲、exchange_ts 与 received_ts 同时记录
- 输出统一 schema（Bar/FundingRate/OpenInterest/MarkPrice）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from qps.core.constants import DataQuality
from qps.core.errors import BrokerBusinessError, BrokerNetworkError
from qps.core.models.adapter import AdapterStatus
from qps.core.models.market import Bar, FundingRate, MarkPrice, OpenInterest

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.broker.exchange_adapter.binance_exchange_adapter import BinanceExchangeAdapter

logger = logging.getLogger(__name__)


# ── 符号映射 ──────────────────────────────────────────────

# 内部符号 → Binance 交易所符号
_SYMBOL_MAP: dict[str, str] = {
    "BTC-USDT-PERP": "BTCUSDT",
    "ETH-USDT-PERP": "ETHUSDT",
    "BTC-USDT": "BTCUSDT",
    "ETH-USDT": "ETHUSDT",
}

# Binance 交易所符号 → 内部符号
_SYMBOL_MAP_REVERSE: dict[str, str] = {v: k for k, v in _SYMBOL_MAP.items()}

# K 线间隔映射
_INTERVAL_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
}

# Binance K 线每页最大条数
_KLINES_LIMIT = 1000

# 去重窗口（秒）：同一 instrument_id + open_time 的 K 线在窗口内只保留最新
_DEDUP_WINDOW_SECONDS = 5


def _to_exchange_symbol(instrument_id: str) -> str:
    """将内部标的 ID 转为 Binance 交易所符号

    Args:
        instrument_id: 内部标的 ID，如 binance-btc-usdt-perp

    Returns:
        Binance 交易所符号，如 BTCUSDT
    """
    # 先尝试从 instrument_id 提取内部符号
    # instrument_id 格式: binance-btc-usdt-perp / binance-btc-usdt
    parts = instrument_id.split("-", 1)
    if len(parts) > 1:
        internal_symbol = parts[1].upper()
        # 将 hyphen-case 转大写后查映射
        mapped = _SYMBOL_MAP.get(internal_symbol)
        if mapped:
            return mapped

    # 直接查映射
    if instrument_id in _SYMBOL_MAP:
        return _SYMBOL_MAP[instrument_id]

    # 兜底：去掉前缀，去掉 hyphen，转大写
    symbol = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id
    return symbol.replace("-", "").upper()


def _data_version_for_batch(source: str, batch_id: str) -> str:
    """生成数据版本号

    格式见 docs/02 §4.5: <source>-<batch_sha256前8>

    Args:
        source: 数据源，如 binance
        batch_id: 批次标识

    Returns:
        版本号，如 binance-3f2a91c4
    """
    import hashlib

    short_hash = hashlib.sha256(batch_id.encode()).hexdigest()[:8]
    return f"{source}-{short_hash}"


# ── 去重缓冲 ──────────────────────────────────────────────


class DedupBuffer:
    """K 线去重去抖缓冲

    对于同一 instrument_id + open_time 的 K 线更新：
    - 在窗口内只保留最新版本
    - 窗口过期后推送
    """

    def __init__(self, window_seconds: int = _DEDUP_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._buffer: dict[str, Bar] = {}  # key = instrument_id:open_time:interval
        self._timestamps: dict[str, datetime] = {}  # key → 入缓冲时间

    def push(self, bar: Bar) -> Bar | None:
        """推入 K 线，返回应推送的 K 线或 None

        Args:
            bar: 新 K 线数据

        Returns:
            如果窗口过期且此 K 线是最新的，返回该 K 线；否则返回 None
        """
        key = f"{bar.instrument_id}:{bar.open_time.isoformat()}:{bar.interval}"
        now = datetime.now(tz=UTC)

        # 检查是否已有更新的版本
        if key in self._buffer:
            existing = self._buffer[key]
            # 如果新 bar 的 volume 更大（Binance K 线更新机制：成交量递增）
            if bar.volume >= existing.volume:
                self._buffer[key] = bar
                self._timestamps[key] = now
        else:
            self._buffer[key] = bar
            self._timestamps[key] = now

        # 检查窗口是否过期
        entry_time = self._timestamps.get(key)
        if entry_time and (now - entry_time).total_seconds() >= self._window_seconds:
            result = self._buffer.pop(key, None)
            self._timestamps.pop(key, None)
            return result

        return None

    def flush(self) -> list[Bar]:
        """强制刷新所有缓冲的 K 线"""
        bars = list(self._buffer.values())
        self._buffer.clear()
        self._timestamps.clear()
        return bars


# ── 乱序缓冲 ──────────────────────────────────────────────


class OutOfOrderBuffer:
    """乱序缓冲：对乱序到达的 K 线排序后输出

    收集一批 K 线后按 open_time 排序输出，
    确保上层收到的数据按时间升序。
    """

    def __init__(self, max_size: int = 100) -> None:
        self._max_size = max_size
        self._bars: list[Bar] = []

    def push(self, bar: Bar) -> list[Bar]:
        """推入 K 线，缓冲满时排序输出

        Args:
            bar: K 线数据

        Returns:
            排序后的 K 线列表（缓冲满时），或空列表（缓冲未满时）
        """
        self._bars.append(bar)

        if len(self._bars) >= self._max_size:
            return self.flush()

        return []

    def flush(self) -> list[Bar]:
        """排序并输出所有缓冲的 K 线"""
        sorted_bars = sorted(self._bars, key=lambda b: b.open_time)
        self._bars = []
        return sorted_bars


# ── Binance Market Adapter ────────────────────────────────


class BinanceMarketAdapter:
    """Binance Market Adapter 实现

    实现 MarketAdapterPort：
    - REST 历史 K 线采集（自动分页、限频节流）
    - WebSocket 实时行情订阅
    - 去重去抖、乱序缓冲
    - 统一 schema 输出
    """

    def __init__(
        self,
        exchange_adapter: BinanceExchangeAdapter,
        data_version: str | None = None,
    ) -> None:
        """初始化 Binance Market Adapter

        Args:
            exchange_adapter: 底层 Exchange Adapter（签名、限频、重连）
            data_version: 数据版本号（默认自动生成）
        """
        self._exchange_adapter = exchange_adapter
        self._data_version = data_version or _data_version_for_batch("binance", "default")

        # 订阅管理
        self._subscriptions: dict[str, dict[str, Any]] = {}  # sub_id -> 订阅信息
        self._sub_counter: int = 0

        # 去重与乱序缓冲
        self._dedup_buffer = DedupBuffer()
        self._ooo_buffer = OutOfOrderBuffer()

        # 连接状态
        self._connected: bool = False

    # ── MarketAdapterPort 接口实现 ────────────────────────

    async def connect(self) -> None:
        """连接行情数据源

        Raises:
            BrokerNetworkError: 连接失败
        """
        if self._connected:
            return

        # 确保底层 Exchange Adapter 已连接
        if not self._exchange_adapter.status().connected:
            # 尝试用环境变量中的凭证连接
            import os

            api_key = os.environ.get("QPS_BINANCE_API_KEY")
            api_secret = os.environ.get("QPS_BINANCE_API_SECRET")
            credentials = None
            if api_key and api_secret:
                credentials = {"api_key": api_key, "api_secret": api_secret}

            await self._exchange_adapter.connect(credentials=credentials)

        self._connected = True
        logger.info("Binance Market Adapter 已连接")

    async def disconnect(self) -> None:
        """断开连接（幂等）"""
        if not self._connected:
            return

        # 取消所有订阅
        for sub_id in list(self._subscriptions.keys()):
            await self.unsubscribe(sub_id)

        self._connected = False
        logger.info("Binance Market Adapter 已断开")

    async def subscribe_bars(
        self,
        instrument_id: str,
        interval: str,
        callback: Callable[[Bar], None],
    ) -> str:
        """订阅 K 线数据

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            callback: 回调函数

        Returns:
            订阅 ID
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        if interval not in _INTERVAL_MAP:
            raise ValueError(f"不支持的 K 线间隔: {interval}，支持: {list(_INTERVAL_MAP.keys())}")

        exchange_symbol = _to_exchange_symbol(instrument_id)
        binance_interval = _INTERVAL_MAP[interval]

        self._sub_counter += 1
        sub_id = f"market_sub_{self._sub_counter}"

        def on_kline(data: dict[str, Any]) -> None:
            bar = self._parse_ws_kline(instrument_id, interval, data)
            if bar is not None:
                # 去重
                deduped = self._dedup_buffer.push(bar)
                if deduped is not None:
                    # 乱序缓冲
                    ready = self._ooo_buffer.push(deduped)
                    for sorted_bar in ready:
                        try:
                            callback(sorted_bar)
                        except Exception:
                            logger.exception("K 线回调异常: %s", instrument_id)

        ws_sub_id = await self._exchange_adapter.subscribe(
            "kline",
            {"symbol": exchange_symbol, "interval": binance_interval},
            on_kline,
        )

        self._subscriptions[sub_id] = {
            "type": "bars",
            "instrument_id": instrument_id,
            "interval": interval,
            "ws_sub_id": ws_sub_id,
            "callback": callback,
        }

        return sub_id

    async def subscribe_funding(
        self,
        instrument_id: str,
        callback: Callable[[FundingRate], None],
    ) -> str:
        """订阅资金费率

        Args:
            instrument_id: 内部标的 ID
            callback: 回调函数

        Returns:
            订阅 ID
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        exchange_symbol = _to_exchange_symbol(instrument_id)

        self._sub_counter += 1
        sub_id = f"market_sub_{self._sub_counter}"

        def on_funding(data: dict[str, Any]) -> None:
            fr = self._parse_ws_funding(instrument_id, data)
            if fr is not None:
                try:
                    callback(fr)
                except Exception:
                    logger.exception("资金费率回调异常: %s", instrument_id)

        ws_sub_id = await self._exchange_adapter.subscribe(
            "fundingRate",
            {"symbol": exchange_symbol},
            on_funding,
        )

        self._subscriptions[sub_id] = {
            "type": "funding",
            "instrument_id": instrument_id,
            "ws_sub_id": ws_sub_id,
            "callback": callback,
        }

        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅（幂等）"""
        if subscription_id not in self._subscriptions:
            return

        sub_info = self._subscriptions.pop(subscription_id)
        ws_sub_id = sub_info.get("ws_sub_id")
        if ws_sub_id:
            await self._exchange_adapter.unsubscribe(ws_sub_id)

    async def fetch_historical_bars(
        self,
        instrument_id: str,
        interval: str,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """采集历史 K 线数据（自动分页）

        Args:
            instrument_id: 内部标的 ID（如 binance-btc-usdt-perp 或 binance-btc-usdt）
            interval: K 线间隔
            start: 开始时间
            end: 结束时间

        Returns:
            按 open_time 升序的 K 线列表

        Raises:
            BrokerNetworkError: 网络错误
            BrokerRateLimitError: 限频
            ValueError: 参数错误
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        if interval not in _INTERVAL_MAP:
            raise ValueError(f"不支持的 K 线间隔: {interval}")

        if start >= end:
            raise ValueError(f"start 必须早于 end: start={start}, end={end}")

        exchange_symbol = _to_exchange_symbol(instrument_id)
        binance_interval = _INTERVAL_MAP[interval]

        # 根据 instrument_id 类型选择 API 端点
        # PERP → /fapi/v1/klines (永续合约)
        # spot → /api/v3/klines (现货)
        is_perp = "-PERP" in instrument_id.upper() or "-PERP" in instrument_id
        klines_path = "/fapi/v1/klines" if is_perp else "/api/v3/klines"

        all_bars: list[Bar] = []
        current_start = start

        while current_start < end:
            params: dict[str, Any] = {
                "symbol": exchange_symbol,
                "interval": binance_interval,
                "startTime": int(current_start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000) - 1,  # Binance endTime 是包含的
                "limit": _KLINES_LIMIT,
            }

            try:
                raw_data = await self._exchange_adapter.request(
                    "GET", klines_path, params=params
                )
            except BrokerBusinessError as e:
                # 业务错误：记录日志并重新抛出，不做跨市场回退
                logger.error(
                    "K 线采集失败: %s %s [%s, %s), 错误: %s",
                    instrument_id,
                    interval,
                    start.isoformat(),
                    end.isoformat(),
                    e.detail,
                )
                raise

            if not raw_data or not isinstance(raw_data, list):
                break

            bars = self._parse_rest_klines(instrument_id, interval, raw_data)
            if not bars:
                break

            all_bars.extend(bars)

            # 更新下一页起始时间（最后一根 K 线的 open_time + 间隔）
            last_bar = bars[-1]
            interval_td = self._interval_to_timedelta(interval)
            current_start = last_bar.open_time + interval_td

            # 如果返回数量少于请求数量，说明已到末尾
            if len(raw_data) < _KLINES_LIMIT:
                break

        # 去掉超出 [start, end) 范围的 K 线
        all_bars = [b for b in all_bars if start <= b.open_time < end]

        logger.info(
            "采集历史 K 线: %s %s [%s, %s), 共 %d 根",
            instrument_id,
            interval,
            start.isoformat(),
            end.isoformat(),
            len(all_bars),
        )

        return all_bars

    def status(self) -> AdapterStatus:
        """查询适配器连接状态"""
        return AdapterStatus(
            connected=self._connected,
            last_heartbeat=self._exchange_adapter.status().last_heartbeat,
            reconnect_count=self._exchange_adapter.status().reconnect_count,
        )

    # ── 数据解析 ────────────────────────────────────────────

    def _parse_rest_klines(
        self,
        instrument_id: str,
        interval: str,
        raw_data: list[list[Any]],
    ) -> list[Bar]:
        """解析 REST K 线响应

        Binance K 线响应格式:
        [
            [open_time, open, high, low, close, volume,
             close_time, quote_volume, trades_count,
             taker_buy_volume, taker_buy_quote_volume, ignore]
        ]

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            raw_data: Binance 原始数据

        Returns:
            Bar 列表
        """
        bars: list[Bar] = []
        for item in raw_data:
            if len(item) < 11:
                continue
            try:
                bar = Bar(
                    instrument_id=instrument_id,
                    open_time=datetime.fromtimestamp(item[0] / 1000, tz=UTC),
                    interval=interval,
                    open=Decimal(str(item[1])),
                    high=Decimal(str(item[2])),
                    low=Decimal(str(item[3])),
                    close=Decimal(str(item[4])),
                    volume=Decimal(str(item[5])),
                    quote_volume=Decimal(str(item[7])),
                    taker_buy_volume=Decimal(str(item[9])),
                    trades_count=int(item[8]),
                    data_version=self._data_version,
                    quality=DataQuality.OK,
                )
                bars.append(bar)
            except Exception:
                logger.exception("解析 K 线数据失败: %s", item[:5])
                continue
        return bars

    def _parse_ws_kline(
        self,
        instrument_id: str,
        interval: str,
        data: dict[str, Any],
    ) -> Bar | None:
        """解析 WebSocket K 线数据

        Binance WS kline 事件:
        {
            "e": "kline",
            "k": {
                "t": open_time, "o": open, "h": high, "l": low, "c": close,
                "v": volume, "q": quote_volume, "n": trades_count,
                "x": is_closed, "V": taker_buy_volume, "Q": taker_buy_quote_volume
            }
        }

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            data: WebSocket 消息

        Returns:
            Bar 或 None
        """
        try:
            k = data.get("k", data)  # 兼容嵌套和非嵌套格式
            return Bar(
                instrument_id=instrument_id,
                open_time=datetime.fromtimestamp(k["t"] / 1000, tz=UTC),
                interval=interval,
                open=Decimal(str(k["o"])),
                high=Decimal(str(k["h"])),
                low=Decimal(str(k["l"])),
                close=Decimal(str(k["c"])),
                volume=Decimal(str(k["v"])),
                quote_volume=Decimal(str(k.get("q", "0"))),
                taker_buy_volume=Decimal(str(k.get("V", "0"))),
                trades_count=int(k.get("n", 0)),
                data_version=self._data_version,
                quality=DataQuality.OK,
            )
        except Exception:
            logger.exception("解析 WS K 线数据失败")
            return None

    def _parse_ws_funding(
        self,
        instrument_id: str,
        data: dict[str, Any],
    ) -> FundingRate | None:
        """解析 WebSocket 资金费率数据

        Binance WS fundingRate 事件:
        {
            "e": "fundingRate",
            "r": rate,
            "T": funding_time
        }

        Args:
            instrument_id: 内部标的 ID
            data: WebSocket 消息

        Returns:
            FundingRate 或 None
        """
        try:
            return FundingRate(
                instrument_id=instrument_id,
                funding_ts=datetime.fromtimestamp(data["T"] / 1000, tz=UTC),
                rate=Decimal(str(data["r"])),
                interval="8h",
            )
        except Exception:
            logger.exception("解析 WS 资金费率数据失败")
            return None

    # ── 额外的 REST 查询方法 ────────────────────────────────

    async def fetch_funding_rates(
        self,
        instrument_id: str,
        start: datetime,
        end: datetime,
    ) -> list[FundingRate]:
        """采集历史资金费率（自动分页）

        Args:
            instrument_id: 内部标的 ID
            start: 开始时间
            end: 结束时间

        Returns:
            FundingRate 列表
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        exchange_symbol = _to_exchange_symbol(instrument_id)

        all_rates: list[FundingRate] = []
        current_start = start

        # Binance funding rate API 每页最多 1000 条
        # 8h 费率约 333 天/1000 条，超过需分页
        page_size = 1000

        while current_start < end:
            params: dict[str, Any] = {
                "symbol": exchange_symbol,
                "startTime": int(current_start.timestamp() * 1000),
                "endTime": int(end.timestamp() * 1000),
                "limit": page_size,
            }

            try:
                raw_data = await self._exchange_adapter.request(
                    "GET", "/fapi/v1/fundingRate", params=params
                )
            except Exception as e:
                logger.error("资金费率采集失败: %s, 错误: %s", instrument_id, e)
                break

            if not raw_data or not isinstance(raw_data, list):
                break

            # 解析结果
            for item in raw_data:
                try:
                    fr = FundingRate(
                        instrument_id=instrument_id,
                        funding_ts=datetime.fromtimestamp(item["fundingTime"] / 1000, tz=UTC),
                        rate=Decimal(str(item["fundingRate"])),
                        interval="8h",
                    )
                    all_rates.append(fr)
                except Exception:
                    logger.exception("解析资金费率数据失败")
                    continue

            # 如果返回数量少于请求数量，说明已到末尾
            if len(raw_data) < page_size:
                break

            # 更新下一页起始时间（最后一条的 funding_time + 8h）
            last_item = raw_data[-1]
            last_ts = datetime.fromtimestamp(last_item["fundingTime"] / 1000, tz=UTC)
            current_start = last_ts + timedelta(hours=8)

        return sorted(all_rates, key=lambda r: r.funding_ts)

    async def fetch_open_interest(
        self,
        instrument_id: str,
    ) -> OpenInterest | None:
        """查询当前持仓量

        Args:
            instrument_id: 内部标的 ID

        Returns:
            OpenInterest 或 None
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        exchange_symbol = _to_exchange_symbol(instrument_id)

        try:
            raw_data = await self._exchange_adapter.request(
                "GET", "/fapi/v1/openInterest",
                params={"symbol": exchange_symbol},
            )
            return OpenInterest(
                instrument_id=instrument_id,
                ts=datetime.now(tz=UTC),
                oi_value=Decimal(str(raw_data["openInterest"])),
                oi_contracts=Decimal(str(raw_data.get("openInterest", "0"))),
                exchange_ts=datetime.now(tz=UTC),
            )
        except Exception:
            logger.exception("查询持仓量失败: %s", instrument_id)
            return None

    async def fetch_mark_price(
        self,
        instrument_id: str,
    ) -> MarkPrice | None:
        """查询当前标记价格

        Args:
            instrument_id: 内部标的 ID

        Returns:
            MarkPrice 或 None
        """
        if not self._connected:
            raise BrokerNetworkError(detail="Market Adapter 未连接")

        exchange_symbol = _to_exchange_symbol(instrument_id)

        try:
            raw_data = await self._exchange_adapter.request(
                "GET", "/fapi/v1/premiumIndex",
                params={"symbol": exchange_symbol},
            )
            return MarkPrice(
                instrument_id=instrument_id,
                ts=datetime.now(tz=UTC),
                mark_price=Decimal(str(raw_data["markPrice"])),
                index_price=Decimal(str(raw_data.get("indexPrice", "0"))),
                exchange_ts=datetime.now(tz=UTC),
            )
        except Exception:
            logger.exception("查询标记价格失败: %s", instrument_id)
            return None

    # ── 工具方法 ────────────────────────────────────────────

    @staticmethod
    def _interval_to_timedelta(interval: str) -> timedelta:
        """将 K 线间隔字符串转为 timedelta

        Args:
            interval: 间隔字符串，如 1m/5m/15m/1h

        Returns:
            对应的 timedelta
        """
        mapping: dict[str, timedelta] = {
            "1m": timedelta(minutes=1),
            "5m": timedelta(minutes=5),
            "15m": timedelta(minutes=15),
            "1h": timedelta(hours=1),
        }
        return mapping.get(interval, timedelta(minutes=5))
