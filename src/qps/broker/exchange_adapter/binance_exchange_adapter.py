"""Binance Exchange Adapter 实现（见 docs/03 第 4 节）

实现 ExchangeAdapterPort：
- REST 签名（HMAC-SHA256）、时间戳、限频令牌桶、超时与重试（指数退避）
- WebSocket 连接、心跳、断线重连（自动恢复订阅）
- 服务器时间校准（时钟偏移）
- 异常包装：BrokerAuthError / BrokerRateLimitError / BrokerNetworkError / BrokerBusinessError
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlencode

import httpx

from qps.core.errors import (
    BrokerAuthError,
    BrokerBusinessError,
    BrokerNetworkError,
    BrokerRateLimitError,
)
from qps.core.models.adapter import AdapterStatus

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


# ── 限频令牌桶 ──────────────────────────────────────────────


class TokenBucket:
    """令牌桶限频器

    用于 REST API 请求限频。按固定速率补充令牌，
    请求消耗令牌，令牌不足时等待或抛 BrokerRateLimitError。
    """

    def __init__(
        self,
        rate: float,
        capacity: int | None = None,
    ) -> None:
        """初始化令牌桶

        Args:
            rate: 每秒补充令牌数
            capacity: 桶容量（默认 = rate，允许短时突发）
        """
        self._rate = rate
        self._capacity = capacity if capacity is not None else max(1, int(rate))
        self._tokens: float = float(self._capacity)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """补充令牌（基于时间差）"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._capacity,
            self._tokens + elapsed * self._rate,
        )
        self._last_refill = now

    async def acquire(self, timeout: float = 10.0) -> None:
        """获取一个令牌

        Args:
            timeout: 最大等待时间（秒），超时抛 BrokerRateLimitError

        Raises:
            BrokerRateLimitError: 等待超时
        """
        deadline = time.monotonic() + timeout
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrokerRateLimitError(
                    detail=f"限频等待超时（{timeout}s），令牌桶耗尽"
                )
            # 等待令牌补充
            await asyncio.sleep(min(0.1, remaining))

    @property
    def available_tokens(self) -> float:
        """当前可用令牌数（只读）"""
        self._refill()
        return self._tokens


# ── 重试策略 ────────────────────────────────────────────────


class RetryPolicy:
    """指数退避重试策略"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor

    def delay_for_attempt(self, attempt: int) -> float:
        """计算第 N 次重试的等待时间

        Args:
            attempt: 重试次数（从 0 开始）

        Returns:
            等待秒数
        """
        delay = self.base_delay * (self.backoff_factor ** attempt)
        return min(delay, self.max_delay)


# ── Binance Exchange Adapter ───────────────────────────────


class BinanceExchangeAdapter:
    """Binance Exchange Adapter 实现

    实现 ExchangeAdapterPort：
    - REST 签名（HMAC-SHA256）
    - 限频令牌桶
    - 超时与重试（指数退避）
    - WebSocket 连接、心跳、断线重连
    - 服务器时间校准
    """

    # Binance API 端点
    _REST_PATHS: ClassVar[dict[str, str]] = {
        "klines": "/api/v3/klines",
        "fundingRate": "/fapi/v1/fundingRate",
        "openInterest": "/fapi/v1/openInterest",
        "markPrice": "/fapi/v1/premiumIndex",
        "exchangeInfo": "/api/v3/exchangeInfo",
        "fapiExchangeInfo": "/fapi/v1/exchangeInfo",
        "time": "/api/v3/time",
        "fapiTime": "/fapi/v1/time",
        "account": "/api/v3/account",
        "fapiAccount": "/fapi/v2/account",
        "order": "/api/v3/order",
        "fapiOrder": "/fapi/v1/order",
        "allOrders": "/api/v3/allOrders",
        "fapiAllOrders": "/fapi/v1/allOrders",
    }

    # WebSocket 频道映射
    _WS_CHANNELS: ClassVar[dict[str, str]] = {
        "kline": "{symbol}@kline_{interval}",
        "markPrice": "{symbol}@markPrice@1s",
        "forceOrder": "{symbol}@forceOrder",
        "fundingRate": "{symbol}@fundingRate",
        "openInterest": "{symbol}@openInterest",
    }

    def __init__(
        self,
        base_url: str = "https://api.binance.com",
        ws_base_url: str = "wss://stream.binance.com:9443/ws",
        testnet: bool = False,
        rate_limit_per_sec: float = 10.0,
        ws_rate_limit_per_sec: float = 5.0,
        request_timeout: float = 10.0,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        """初始化 Binance Exchange Adapter

        Args:
            base_url: REST API 基础 URL
            ws_base_url: WebSocket 基础 URL
            testnet: 是否使用测试网
            rate_limit_per_sec: REST 每秒请求数限制
            ws_rate_limit_per_sec: WebSocket 每秒请求数限制
            request_timeout: 请求超时（秒）
            retry_policy: 重试策略（默认指数退避 3 次）
        """
        # 测试网 URL 覆盖
        if testnet:
            base_url = "https://testnet.binance.vision"
            ws_base_url = "wss://testnet.binance.vision/ws"

        self._base_url = base_url.rstrip("/")
        self._ws_base_url = ws_base_url.rstrip("/")
        self._testnet = testnet
        self._request_timeout = request_timeout
        self._retry_policy = retry_policy or RetryPolicy()

        # 限频器
        self._rest_limiter = TokenBucket(rate=rate_limit_per_sec)
        self._ws_limiter = TokenBucket(rate=ws_rate_limit_per_sec)

        # 认证信息
        self._api_key: str | None = None
        self._api_secret: str | None = None

        # 连接状态
        self._connected: bool = False
        self._reconnect_count: int = 0
        self._last_heartbeat: datetime | None = None
        self._server_time_offset: float = 0.0  # 本地 - 服务器 偏移（秒）

        # HTTP 客户端
        self._client: httpx.AsyncClient | None = None

        # WebSocket 相关
        self._ws: Any = None  # websockets.WebSocketClientProtocol
        self._ws_task: asyncio.Task[None] | None = None
        self._ws_subscriptions: dict[str, dict[str, Any]] = {}  # sub_id -> {channel, payload, callback}
        self._ws_sub_counter: int = 0
        self._ws_reconnect_delay: float = 1.0
        self._ws_max_reconnect_delay: float = 60.0

    # ── ExchangeAdapterPort 接口实现 ────────────────────────

    async def connect(self, credentials: dict[str, str] | None = None) -> None:
        """连接交易所

        Args:
            credentials: {"api_key": ..., "api_secret": ...}

        Raises:
            BrokerAuthError: 认证失败
            BrokerNetworkError: 网络错误
        """
        if self._connected:
            return

        # 提取认证信息
        if credentials:
            self._api_key = credentials.get("api_key")
            self._api_secret = credentials.get("api_secret")

        # 创建 HTTP 客户端
        headers: dict[str, str] = {}
        if self._api_key:
            headers["X-MBX-APIKEY"] = self._api_key

        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(self._request_timeout),
            headers=headers,
        )

        try:
            # 校准服务器时间
            await self._calibrate_server_time()
            self._connected = True
            logger.info(
                "Binance Exchange Adapter 已连接: %s (testnet=%s)",
                self._base_url,
                self._testnet,
            )
        except httpx.TimeoutException as e:
            raise BrokerNetworkError(
                detail=f"连接超时: {self._base_url}",
                cause=e,
            ) from e
        except httpx.ConnectError as e:
            raise BrokerNetworkError(
                detail=f"连接失败: {self._base_url}",
                cause=e,
            ) from e

    async def disconnect(self) -> None:
        """断开连接（幂等）"""
        if not self._connected:
            return

        # 关闭 WebSocket
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._ws_task
        self._ws = None
        self._ws_subscriptions.clear()

        # 关闭 HTTP 客户端
        if self._client:
            await self._client.aclose()
            self._client = None

        self._connected = False
        logger.info("Binance Exchange Adapter 已断开")

    async def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        signed: bool = False,
    ) -> dict[str, Any]:
        """发送 REST 请求

        Args:
            method: HTTP 方法（GET/POST/PUT/DELETE）
            path: API 路径（如 /api/v3/klines）
            params: 请求参数
            signed: 是否需要签名

        Returns:
            交易所原始响应（dict）

        Raises:
            BrokerNetworkError: 网络错误/超时
            BrokerRateLimitError: 限频
            BrokerBusinessError: 业务错误
            BrokerAuthError: 认证错误
        """
        if not self._connected:
            raise BrokerNetworkError(detail="未连接，请先调用 connect()")

        params = dict(params) if params else {}

        # 签名
        if signed:
            if not self._api_key or not self._api_secret:
                raise BrokerAuthError(detail="签名请求需要 API Key 和 Secret")
            params = self._sign_params(params)

        # 限频
        await self._rest_limiter.acquire()

        # 重试循环
        last_error: Exception | None = None
        for attempt in range(self._retry_policy.max_retries + 1):
            try:
                response = await self._send_request(method, path, params)
                return response
            except BrokerRateLimitError as e:
                # 限频：等待 retry-after 后重试
                retry_after = float(e.detail.split("retry_after=")[-1]) if "retry_after=" in e.detail else 1.0
                if attempt < self._retry_policy.max_retries:
                    delay = retry_after + self._retry_policy.delay_for_attempt(attempt)
                    logger.warning("限频重试 (%d/%d): 等待 %.1fs", attempt + 1, self._retry_policy.max_retries, delay)
                    await asyncio.sleep(delay)
                    last_error = e
                    continue
                raise
            except BrokerNetworkError as e:
                # 网络错误：指数退避重试
                if attempt < self._retry_policy.max_retries:
                    delay = self._retry_policy.delay_for_attempt(attempt)
                    logger.warning("网络错误重试 (%d/%d): 等待 %.1fs", attempt + 1, self._retry_policy.max_retries, delay)
                    await asyncio.sleep(delay)
                    last_error = e
                    continue
                raise

        # 不应到达此处，但保险起见
        if last_error is not None:
            raise last_error
        raise BrokerNetworkError(detail="请求失败")

    async def subscribe(
        self,
        channel: str,
        payload: dict[str, Any],
        callback: Callable[[dict[str, Any]], None],
    ) -> str:
        """订阅 WebSocket 频道

        Args:
            channel: 频道名（如 kline/markPrice/fundingRate）
            payload: 订阅参数（如 {"symbol": "btcusdt", "interval": "5m"}）
            callback: 消息回调

        Returns:
            订阅 ID
        """
        self._ws_sub_counter += 1
        sub_id = f"ws_sub_{self._ws_sub_counter}"

        self._ws_subscriptions[sub_id] = {
            "channel": channel,
            "payload": payload,
            "callback": callback,
        }

        # 如果 WebSocket 已连接，立即发送订阅
        if self._ws is not None:
            await self._ws_send_subscribe(channel, payload)

        # 如果 WebSocket 未连接，启动连接
        if self._ws is None and self._ws_task is None:
            self._ws_task = asyncio.create_task(self._ws_loop())

        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅（幂等）"""
        if subscription_id in self._ws_subscriptions:
            del self._ws_subscriptions[subscription_id]

    async def get_server_time(self) -> datetime:
        """获取交易所服务器时间

        Returns:
            服务器时间（UTC）
        """
        if not self._connected or not self._client:
            raise BrokerNetworkError(detail="未连接")

        try:
            resp = await self._client.get("/api/v3/time")
            resp.raise_for_status()
            data = resp.json()
            return datetime.fromtimestamp(data["serverTime"] / 1000, tz=UTC)
        except httpx.HTTPError as e:
            raise BrokerNetworkError(detail="获取服务器时间失败", cause=e) from e

    def rate_limiter(self) -> dict[str, Any]:
        """获取限频器状态

        Returns:
            限频器信息（剩余配额、重置时间等）
        """
        return {
            "rest_available": self._rest_limiter.available_tokens,
            "ws_available": self._ws_limiter.available_tokens,
            "rest_rate": self._rest_limiter._rate,
            "ws_rate": self._ws_limiter._rate,
        }

    def status(self) -> AdapterStatus:
        """查询适配器连接状态

        Returns:
            AdapterStatus 快照
        """
        return AdapterStatus(
            connected=self._connected,
            last_heartbeat=self._last_heartbeat,
            reconnect_count=self._reconnect_count,
        )

    # ── 内部方法 ────────────────────────────────────────────

    def _sign_params(self, params: dict[str, Any]) -> dict[str, Any]:
        """对请求参数进行 HMAC-SHA256 签名

        Args:
            params: 原始参数

        Returns:
            添加了签名和时间戳的参数
        """
        assert self._api_secret is not None

        # 添加时间戳（校准偏移）
        server_ts = int((time.time() + self._server_time_offset) * 1000)
        params["timestamp"] = server_ts

        # 排序参数并编码
        query_string = urlencode(sorted(params.items()))

        # HMAC-SHA256 签名
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        params["signature"] = signature
        return params

    async def _calibrate_server_time(self) -> None:
        """校准本地时钟与服务器时钟的偏移

        Raises:
            BrokerNetworkError: 无法获取服务器时间
        """
        if not self._client:
            return

        try:
            local_before = time.time()
            resp = await self._client.get("/api/v3/time")
            resp.raise_for_status()
            local_after = time.time()

            data = resp.json()
            server_ts = data["serverTime"] / 1000.0
            local_ts = (local_before + local_after) / 2.0

            self._server_time_offset = server_ts - local_ts
            logger.debug(
                "服务器时间校准: 偏移=%.3fms",
                self._server_time_offset * 1000,
            )
        except httpx.TimeoutException as e:
            raise BrokerNetworkError(
                detail="服务器时间校准超时",
                cause=e,
            ) from e
        except httpx.ConnectError as e:
            raise BrokerNetworkError(
                detail="服务器时间校准连接失败",
                cause=e,
            ) from e
        except httpx.HTTPStatusError as e:
            logger.warning("服务器时间校准 HTTP 错误: %s，将使用本地时间", e)
            self._server_time_offset = 0.0
        except Exception as e:
            logger.warning("服务器时间校准失败: %s，将使用本地时间", e)
            self._server_time_offset = 0.0

    async def _send_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """发送单次 HTTP 请求并处理响应

        Args:
            method: HTTP 方法
            path: API 路径
            params: 请求参数

        Returns:
            响应 JSON

        Raises:
            BrokerNetworkError: 网络错误
            BrokerRateLimitError: 限频
            BrokerBusinessError: 业务错误
            BrokerAuthError: 认证错误
        """
        assert self._client is not None

        try:
            if method.upper() == "GET":
                response = await self._client.get(path, params=params)
            elif method.upper() == "POST":
                response = await self._client.post(path, params=params)
            elif method.upper() == "PUT":
                response = await self._client.put(path, params=params)
            elif method.upper() == "DELETE":
                response = await self._client.delete(path, params=params)
            else:
                raise BrokerBusinessError(detail=f"不支持的 HTTP 方法: {method}")
        except httpx.TimeoutException as e:
            raise BrokerNetworkError(detail=f"请求超时: {method} {path}", cause=e) from e
        except httpx.ConnectError as e:
            raise BrokerNetworkError(detail=f"连接失败: {method} {path}", cause=e) from e
        except httpx.HTTPError as e:
            raise BrokerNetworkError(detail=f"HTTP 错误: {e}", cause=e) from e

        # 处理响应状态码
        if response.status_code == 429:
            # 限频
            retry_after = response.headers.get("Retry-After", "1")
            raise BrokerRateLimitError(
                detail=f"Binance 限频: status=429, retry_after={retry_after}"
            )
        if response.status_code == 418:
            # IP 被禁
            raise BrokerRateLimitError(
                detail="Binance IP 被禁 (418)，请检查限频规则"
            )
        if response.status_code in (401, 403):
            raise BrokerAuthError(
                detail=f"Binance 认证失败: status={response.status_code}"
            )

        if response.status_code >= 400:
            # 尝试解析 Binance 错误
            try:
                error_data = response.json()
                code = error_data.get("code", -1)
                msg = error_data.get("msg", "未知错误")
                raise BrokerBusinessError(
                    detail=f"Binance 错误: code={code}, msg={msg}",
                    error_code=f"BINANCE_{code}",
                )
            except (ValueError, KeyError) as e:
                raise BrokerBusinessError(
                    detail=f"Binance HTTP 错误: status={response.status_code}, body={response.text[:200]}"
                ) from e

        result: dict[str, Any] = response.json()
        return result

    async def _ws_loop(self) -> None:
        """WebSocket 主循环：连接 → 订阅 → 接收 → 心跳 → 断线重连"""
        import websockets

        while True:
            try:
                async with websockets.connect(self._ws_base_url) as ws:
                    self._ws = ws
                    self._reconnect_count += 1 if self._ws_subscriptions else 0
                    self._ws_reconnect_delay = 1.0  # 重置重连延迟
                    logger.info("WebSocket 已连接: %s", self._ws_base_url)

                    # 重新订阅所有频道
                    await self._ws_resubscribe_all()

                    # 接收消息循环
                    async for message in ws:
                        try:
                            import json

                            data = json.loads(message)
                            self._last_heartbeat = datetime.now(tz=UTC)
                            await self._ws_dispatch(data)
                        except Exception:
                            logger.exception("WebSocket 消息处理失败")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("WebSocket 断线: %s", e)
                self._ws = None

                # 指数退避重连
                delay = min(
                    self._ws_reconnect_delay,
                    self._ws_max_reconnect_delay,
                )
                self._ws_reconnect_delay *= 2
                logger.info("WebSocket 重连等待: %.1fs", delay)
                await asyncio.sleep(delay)

    async def _ws_resubscribe_all(self) -> None:
        """重新订阅所有已注册的频道"""
        for sub_id, sub_info in self._ws_subscriptions.items():
            try:
                await self._ws_send_subscribe(
                    sub_info["channel"],
                    sub_info["payload"],
                )
            except Exception:
                logger.exception("重新订阅失败: sub_id=%s", sub_id)

    async def _ws_send_subscribe(
        self,
        channel: str,
        payload: dict[str, Any],
    ) -> None:
        """发送 WebSocket 订阅请求"""
        if self._ws is None:
            return

        import json

        # 构建 Binance 订阅流名
        stream_name = self._build_ws_stream_name(channel, payload)

        subscribe_msg = {
            "method": "SUBSCRIBE",
            "params": [stream_name],
            "id": self._ws_sub_counter,
        }

        await self._ws_limiter.acquire()
        await self._ws.send(json.dumps(subscribe_msg))

    def _build_ws_stream_name(
        self,
        channel: str,
        payload: dict[str, Any],
    ) -> str:
        """构建 Binance WebSocket 流名

        Args:
            channel: 频道名
            payload: 订阅参数

        Returns:
            流名，如 btcusdt@kline_5m
        """
        template = self._WS_CHANNELS.get(channel, channel)
        symbol = payload.get("symbol", "").lower()
        interval = payload.get("interval", "")

        stream = template.format(symbol=symbol, interval=interval)
        return stream

    async def _ws_dispatch(self, data: dict[str, Any]) -> None:
        """分发 WebSocket 消息到匹配的订阅回调

        Args:
            data: 解析后的 WebSocket 消息
        """
        # Binance 合并流格式: {"stream": "btcusdt@kline_5m", "data": {...}}
        # 单流格式: {"e": "kline", ...}
        stream_name = data.get("stream", "")
        event_data = data.get("data", data)  # 合并流取 data，单流取整个

        for sub_id, sub_info in self._ws_subscriptions.items():
            channel = sub_info["channel"]
            payload = sub_info["payload"]
            callback = sub_info["callback"]

            # 检查消息是否匹配此订阅
            expected_stream = self._build_ws_stream_name(channel, payload)
            if stream_name and stream_name == expected_stream:
                try:
                    callback(event_data)
                except Exception:
                    logger.exception(
                        "WebSocket 回调异常: sub_id=%s, channel=%s",
                        sub_id,
                        channel,
                    )
            elif not stream_name:
                # 单流模式：按事件类型匹配
                event_type = event_data.get("e", "")
                channel_event_map = {
                    "kline": "kline",
                    "markPrice": "markPrice",
                    "forceOrder": "forceOrder",
                    "fundingRate": "fundingRate",
                    "openInterest": "openInterest",
                }
                if channel_event_map.get(event_type) == channel:
                    try:
                        callback(event_data)
                    except Exception:
                        logger.exception(
                            "WebSocket 回调异常: sub_id=%s, channel=%s",
                            sub_id,
                            channel,
                        )
