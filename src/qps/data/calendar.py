"""交易所日历与维护窗口

实现：
- 加密货币 7×24 + 交易所维护窗口表（配置可维护）
- 数据空洞策略：标记 SUSPECT 或跳过（可配置）
- 实现 DataProviderPort.calendar() 依赖
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta

from qps.core.models.instrument import CalendarBreak, ExchangeCalendar

logger = logging.getLogger(__name__)


# ── 默认维护窗口 ──────────────────────────────────────────

# Binance 已知的维护窗口（可配置覆盖）
_DEFAULT_MAINTENANCE_WINDOWS: dict[str, list[dict[str, str]]] = {
    "binance": [
        # 示例：Binance 定期维护窗口（实际值应从配置加载）
        # {"start": "2026-01-15T02:00:00Z", "end": "2026-01-15T04:00:00Z", "reason": "scheduled_maintenance"},
    ],
}


class ExchangeCalendarManager:
    """交易所日历管理器

    加密货币 7×24 交易，但需维护窗口表。
    维护窗口可从配置文件加载，支持动态更新。
    """

    def __init__(
        self,
        maintenance_windows: dict[str, list[dict[str, str]]] | None = None,
    ) -> None:
        """初始化日历管理器

        Args:
            maintenance_windows: 维护窗口配置，格式：
                {"binance": [{"start": "ISO8601", "end": "ISO8601", "reason": "..."}]}
        """
        # 深拷贝默认配置，避免测试间共享可变状态
        import copy

        self._maintenance_windows = copy.deepcopy(
            maintenance_windows if maintenance_windows is not None else _DEFAULT_MAINTENANCE_WINDOWS
        )
        # 缓存：exchange + date → ExchangeCalendar
        self._cache: dict[str, ExchangeCalendar] = {}

    def get_calendar(
        self,
        exchange: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> list[ExchangeCalendar]:
        """获取交易日历

        加密货币 7×24 交易，所以每个日期都是交易日。
        session_open = 00:00:00 UTC, session_close = 23:59:59 UTC

        Args:
            exchange: 交易所 ID
            start_date: 开始日期（默认今天 - 30 天）
            end_date: 结束日期（默认今天）

        Returns:
            交易日历列表，按日期升序
        """
        if start_date is None:
            start_date = date.today() - timedelta(days=30)
        if end_date is None:
            end_date = date.today()

        calendars: list[ExchangeCalendar] = []
        current = start_date
        while current <= end_date:
            cal = self._get_or_create_calendar(exchange, current)
            calendars.append(cal)
            current += timedelta(days=1)

        return calendars

    def get_calendar_for_date(self, exchange: str, d: date) -> ExchangeCalendar:
        """获取指定日期的日历

        Args:
            exchange: 交易所 ID
            d: 日期

        Returns:
            ExchangeCalendar
        """
        return self._get_or_create_calendar(exchange, d)

    def is_in_maintenance(
        self,
        exchange: str,
        ts: datetime,
    ) -> bool:
        """检查指定时间是否在维护窗口内

        Args:
            exchange: 交易所 ID
            ts: 检查时间（UTC）

        Returns:
            True 表示在维护窗口内
        """
        cal = self._get_or_create_calendar(exchange, ts.date())
        return any(brk.start <= ts < brk.end for brk in cal.breaks)

    def is_trading_time(
        self,
        exchange: str,
        ts: datetime,
    ) -> bool:
        """检查指定时间是否为交易时间

        加密货币 7×24，所以只有维护窗口内才不是交易时间。

        Args:
            exchange: 交易所 ID
            ts: 检查时间（UTC）

        Returns:
            True 表示可交易
        """
        return not self.is_in_maintenance(exchange, ts)

    def add_maintenance_window(
        self,
        exchange: str,
        start: datetime,
        end: datetime,
        reason: str | None = None,
    ) -> None:
        """添加维护窗口

        Args:
            exchange: 交易所 ID
            start: 开始时间（UTC）
            end: 结束时间（UTC）
            reason: 原因
        """
        # 清除受影响日期的缓存
        current_date = start.date()
        while current_date <= end.date():
            cache_key = f"{exchange}:{current_date.isoformat()}"
            self._cache.pop(cache_key, None)
            current_date += timedelta(days=1)

        # 添加到配置
        if exchange not in self._maintenance_windows:
            self._maintenance_windows[exchange] = []

        self._maintenance_windows[exchange].append({
            "start": start.isoformat(),
            "end": end.isoformat(),
            "reason": reason or "maintenance",
        })

        logger.info(
            "添加维护窗口: %s [%s, %s), reason=%s",
            exchange,
            start.isoformat(),
            end.isoformat(),
            reason,
        )

    def _get_or_create_calendar(self, exchange: str, d: date) -> ExchangeCalendar:
        """获取或创建指定日期的日历

        Args:
            exchange: 交易所 ID
            d: 日期

        Returns:
            ExchangeCalendar
        """
        cache_key = f"{exchange}:{d.isoformat()}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 加密货币 7×24：session_open = 00:00:00, session_close = 23:59:59
        session_open = datetime.combine(d, time.min, tzinfo=UTC)
        session_close = datetime.combine(
            d, time(23, 59, 59), tzinfo=UTC
        )

        # 查找该日期的维护窗口
        breaks = self._find_breaks(exchange, d)

        cal = ExchangeCalendar(
            exchange=exchange,
            date=d,
            session_open=session_open,
            session_close=session_close,
            breaks=breaks,
        )

        self._cache[cache_key] = cal
        return cal

    def _find_breaks(self, exchange: str, d: date) -> list[CalendarBreak]:
        """查找指定日期的维护窗口

        Args:
            exchange: 交易所 ID
            d: 日期

        Returns:
            CalendarBreak 列表
        """
        windows = self._maintenance_windows.get(exchange, [])
        breaks: list[CalendarBreak] = []

        for window in windows:
            # 确保解析后的 datetime 是 tz-aware（UTC）
            start_raw = datetime.fromisoformat(window["start"])
            end_raw = datetime.fromisoformat(window["end"])

            # 如果解析结果是 naive，添加 UTC 时区
            if start_raw.tzinfo is None:
                start_raw = start_raw.replace(tzinfo=UTC)
            if end_raw.tzinfo is None:
                end_raw = end_raw.replace(tzinfo=UTC)

            start = start_raw
            end = end_raw

            # 检查是否与该日期重叠
            day_start = datetime.combine(d, time.min, tzinfo=UTC)
            day_end = datetime.combine(d, time(23, 59, 59), tzinfo=UTC)

            if start <= day_end and end >= day_start:
                # 截断到该日期范围内
                break_start = max(start, day_start)
                break_end = min(end, day_end)

                breaks.append(CalendarBreak(
                    start=break_start,
                    end=break_end,
                    reason=window.get("reason"),
                ))

        return sorted(breaks, key=lambda b: b.start)


# ── 数据空洞策略 ──────────────────────────────────────────


class DataGapStrategy:
    """数据空洞处理策略

    当检测到数据空洞时，根据策略决定：
    - SUSPECT: 标记为可疑数据（默认）
    - SKIP: 跳过空洞，不填充
    - FILL_ZERO: 用零值填充（用于某些场景）
    """

    def __init__(self, strategy: str = "SUSPECT") -> None:
        """初始化空洞策略

        Args:
            strategy: 策略名称，SUSPECT / SKIP / FILL_ZERO
        """
        if strategy not in ("SUSPECT", "SKIP", "FILL_ZERO"):
            raise ValueError(f"不支持的数据空洞策略: {strategy}，支持: SUSPECT / SKIP / FILL_ZERO")
        self._strategy = strategy

    @property
    def strategy(self) -> str:
        return self._strategy

    def should_mark_suspect(self) -> bool:
        """是否标记为 SUSPECT"""
        return self._strategy == "SUSPECT"

    def should_skip(self) -> bool:
        """是否跳过"""
        return self._strategy == "SKIP"

    def should_fill_zero(self) -> bool:
        """是否零值填充"""
        return self._strategy == "FILL_ZERO"
