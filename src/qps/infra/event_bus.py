"""事件总线：同步直连 + 异步队列两种投递（见 docs/05 第 4 节）

投递模式：
- 同步直连（direct）：交易关键路径，同一协程内按序完成，消费失败向上抛
- 异步队列（queue）：非关键路径（监控、落盘、审计），队列隔离，消费失败重试或丢弃并告警

规则：
- 所有事件按 event_id 幂等消费
- 事件必须可序列化（JSON）
- 关键路径事件必须用同步直连
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from qps.core.errors import QpsError

if TYPE_CHECKING:
    from collections.abc import Callable

    from qps.core.events import EventEnvelope, EventType

logger = logging.getLogger(__name__)


class EventBusError(QpsError):
    """事件总线错误"""

    _default_error_code = "EVENT_BUS_ERROR"


class _Subscription:
    """订阅记录"""

    __slots__ = ("callback", "event_type", "mode", "subscription_id")

    def __init__(
        self,
        subscription_id: str,
        event_type: EventType | None,
        callback: Callable[[EventEnvelope], None],
        mode: str,
    ) -> None:
        self.subscription_id = subscription_id
        self.event_type = event_type  # None 表示订阅所有类型
        self.callback = callback
        self.mode = mode  # "direct" 或 "queue"


class EventBus:
    """事件总线

    支持两种投递模式：
    - 同步直连（direct）：交易关键路径，回调在发布者协程内同步执行
    - 异步队列（queue）：非关键路径，回调在独立任务中执行

    幂等保证：按 event_id 去重，同一 event_id 只消费一次。
    """

    # 已消费事件 ID 集合容量上限，超过时自动清理
    _CONSUMED_IDS_MAX_SIZE: int = 10_000

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []
        self._consumed_event_ids: set[str] = set()
        self._sub_counter: int = 0
        self._queue: asyncio.Queue[EventEnvelope] = asyncio.Queue()
        self._queue_task: asyncio.Task[None] | None = None

    # ── 订阅 ──────────────────────────────────────────────

    def subscribe(
        self,
        callback: Callable[[EventEnvelope], None],
        event_type: EventType | None = None,
        mode: str = "direct",
    ) -> str:
        """订阅事件

        Args:
            callback: 事件回调函数
            event_type: 订阅的事件类型，None 表示订阅所有类型
            mode: 投递模式，"direct"（同步直连）或 "queue"（异步队列）

        Returns:
            订阅 ID

        Raises:
            EventBusError: mode 不合法时
        """
        if mode not in ("direct", "queue"):
            raise EventBusError(detail=f"不合法的投递模式: {mode}，必须为 direct 或 queue")

        self._sub_counter += 1
        sub_id = f"sub_{self._sub_counter}"
        sub = _Subscription(sub_id, event_type, callback, mode)
        self._subscriptions.append(sub)
        return sub_id

    def subscribe_queue(
        self,
        callback: Callable[[EventEnvelope], Any],
        event_type: EventType | None = None,
    ) -> str:
        """订阅异步队列事件（便捷方法）"""
        return self.subscribe(callback, event_type, mode="queue")

    def unsubscribe(self, subscription_id: str) -> None:
        """取消订阅（幂等）"""
        # 原地过滤，避免 O(n) 重建整个列表
        i = 0
        while i < len(self._subscriptions):
            if self._subscriptions[i].subscription_id == subscription_id:
                del self._subscriptions[i]
            else:
                i += 1

    # ── 发布 ──────────────────────────────────────────────

    def publish(self, event: EventEnvelope) -> None:
        """发布事件（同步直连模式）

        同步执行所有 direct 模式的订阅回调。
        如果 event_id 已消费过，跳过（幂等）。
        回调抛异常时向上传播。

        Args:
            event: 事件信封
        """
        # 幂等检查
        if event.event_id in self._consumed_event_ids:
            return
        self._consumed_event_ids.add(event.event_id)

        # 容量上限自动清理：超过阈值时清空（事件 ID 含时间戳前缀，旧 ID 重复概率极低）
        if len(self._consumed_event_ids) > self._CONSUMED_IDS_MAX_SIZE:
            self._consumed_event_ids.clear()

        # 同步直连投递
        for sub in self._subscriptions:
            if sub.mode != "direct":
                continue
            if sub.event_type is not None and sub.event_type != event.event_type:
                continue
            sub.callback(event)

        # 异步队列投递：检查是否有 queue 模式订阅者匹配
        has_queue_sub = any(
            sub.mode == "queue"
            and (sub.event_type is None or sub.event_type == event.event_type)
            for sub in self._subscriptions
        )
        if has_queue_sub:
            try:
                self._queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("事件队列已满，丢弃事件: %s", event.event_id)

    async def publish_async(self, event: EventEnvelope) -> None:
        """发布事件（异步模式，同时处理队列消费）

        同步执行 direct 回调后，启动队列消费任务。
        """
        self.publish(event)
        # 启动队列消费
        if not self._queue_task or self._queue_task.done():
            self._queue_task = asyncio.create_task(self._consume_queue())

    async def _consume_queue(self) -> None:
        """消费异步队列中的事件"""
        while True:
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=0.1)
            except TimeoutError:
                break

            # 动态查找当前匹配的 queue 模式订阅者
            for sub in self._subscriptions:
                if sub.mode != "queue":
                    continue
                if sub.event_type is not None and sub.event_type != event.event_type:
                    continue
                try:
                    result = sub.callback(event)
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    logger.exception(
                        "异步队列消费失败: event_id=%s, consumer=%s",
                        event.event_id,
                        getattr(sub.callback, "__name__", "unknown"),
                    )
            self._queue.task_done()

    # ── 工具方法 ──────────────────────────────────────────

    def is_consumed(self, event_id: str) -> bool:
        """检查事件是否已被消费（幂等检查）"""
        return event_id in self._consumed_event_ids

    def clear_consumed(self) -> None:
        """清除已消费记录（测试用）"""
        self._consumed_event_ids.clear()

    def reset(self) -> None:
        """重置事件总线（测试用）"""
        self._subscriptions.clear()
        self._consumed_event_ids.clear()
        self._sub_counter = 0
        if self._queue_task and not self._queue_task.done():
            self._queue_task.cancel()
        self._queue = asyncio.Queue()
