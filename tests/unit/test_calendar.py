"""T4 交易日历与维护窗口单元测试

测试：
- ExchangeCalendarManager: 7×24 日历、维护窗口、缓存
- DataGapStrategy: 策略选择
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from qps.data.calendar import DataGapStrategy, ExchangeCalendarManager

# ── ExchangeCalendarManager ───────────────────────────────


class TestExchangeCalendarManager:
    def _fresh_mgr(self, windows: dict[str, list[dict[str, str]]] | None = None) -> ExchangeCalendarManager:
        """每个测试用独立的实例，避免状态泄漏"""
        return ExchangeCalendarManager(maintenance_windows=windows)

    def test_get_calendar_default_range(self) -> None:
        """默认日期范围（今天-30天 到 今天）"""
        mgr = self._fresh_mgr()
        calendars = mgr.get_calendar("binance")
        assert len(calendars) == 31  # 30 天 + 今天

    def test_get_calendar_custom_range(self) -> None:
        """自定义日期范围"""
        mgr = self._fresh_mgr()
        start = date(2026, 1, 1)
        end = date(2026, 1, 7)
        calendars = mgr.get_calendar("binance", start, end)
        assert len(calendars) == 7
        assert calendars[0].date == start
        assert calendars[-1].date == end

    def test_7x24_session(self) -> None:
        """7×24 交易：session_open = 00:00, session_close = 23:59:59"""
        mgr = self._fresh_mgr()
        cal = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert cal.session_open.hour == 0
        assert cal.session_close.hour == 23
        assert cal.session_close.minute == 59

    def test_no_maintenance_windows_by_default(self) -> None:
        """默认无维护窗口"""
        mgr = self._fresh_mgr()
        cal = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal.breaks) == 0

    def test_add_maintenance_window(self) -> None:
        """添加维护窗口"""
        mgr = self._fresh_mgr()
        mgr.add_maintenance_window(
            "binance",
            datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC),
            reason="scheduled",
        )

        cal = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal.breaks) == 1
        assert cal.breaks[0].reason == "scheduled"

    def test_is_in_maintenance(self) -> None:
        """维护窗口检测"""
        mgr = self._fresh_mgr()
        mgr.add_maintenance_window(
            "binance",
            datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC),
        )

        # 维护时间内
        assert mgr.is_in_maintenance("binance", datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)) is True
        # 维护时间外
        assert mgr.is_in_maintenance("binance", datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)) is False

    def test_is_trading_time(self) -> None:
        """交易时间检测"""
        mgr = self._fresh_mgr()
        mgr.add_maintenance_window(
            "binance",
            datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC),
        )

        # 维护时间内不是交易时间
        assert mgr.is_trading_time("binance", datetime(2026, 1, 1, 3, 0, 0, tzinfo=UTC)) is False
        # 维护时间外是交易时间
        assert mgr.is_trading_time("binance", datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)) is True

    def test_maintenance_window_crossing_midnight(self) -> None:
        """跨午夜维护窗口"""
        windows = {
            "binance": [
                {
                    "start": "2026-01-01T22:00:00+00:00",
                    "end": "2026-01-02T02:00:00+00:00",
                    "reason": "maintenance",
                },
            ],
        }
        mgr = self._fresh_mgr(windows)

        # 1月1日有维护窗口（22:00-23:59:59）
        cal_1 = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal_1.breaks) == 1

        # 1月2日也有维护窗口（00:00-02:00）
        cal_2 = mgr.get_calendar_for_date("binance", date(2026, 1, 2))
        assert len(cal_2.breaks) == 1

    def test_cache_invalidation_on_add(self) -> None:
        """添加维护窗口后缓存失效"""
        mgr = self._fresh_mgr()

        # 先获取一次
        cal_before = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal_before.breaks) == 0

        # 添加维护窗口
        mgr.add_maintenance_window(
            "binance",
            datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 4, 0, 0, tzinfo=UTC),
        )

        # 再次获取应反映更新
        cal_after = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal_after.breaks) == 1

    def test_custom_maintenance_windows(self) -> None:
        """自定义维护窗口配置"""
        windows = {
            "binance": [
                {
                    "start": "2026-01-01T02:00:00+00:00",
                    "end": "2026-01-01T04:00:00+00:00",
                    "reason": "scheduled",
                },
            ],
        }
        mgr = self._fresh_mgr(windows)
        cal = mgr.get_calendar_for_date("binance", date(2026, 1, 1))
        assert len(cal.breaks) == 1


# ── DataGapStrategy ───────────────────────────────────────


class TestDataGapStrategy:
    def test_default_strategy(self) -> None:
        strategy = DataGapStrategy()
        assert strategy.strategy == "SUSPECT"
        assert strategy.should_mark_suspect() is True

    def test_skip_strategy(self) -> None:
        strategy = DataGapStrategy("SKIP")
        assert strategy.should_skip() is True

    def test_fill_zero_strategy(self) -> None:
        strategy = DataGapStrategy("FILL_ZERO")
        assert strategy.should_fill_zero() is True

    def test_invalid_strategy_raises(self) -> None:
        with pytest.raises(ValueError, match="不支持的数据空洞策略"):
            DataGapStrategy("INVALID")
