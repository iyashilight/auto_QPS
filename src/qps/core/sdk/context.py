"""策略上下文（见 docs/04 第 4 节）

提供策略运行时所需的全部能力：参数、行情、特征、持仓、资金、状态、意图输出等。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, MutableMapping
from datetime import datetime
from decimal import Decimal
from typing import Any

from qps.core.models.account import Position
from qps.core.models.market import Bar


class MarketRegimeView:
    """市场状态视图（只读软信号）

    由 Coordinator/规则内核周期更新并注入，策略读取它做软响应。
    强制约束（禁用新仓、降杠杆）由风控/组合层执行，策略无权绕过。
    """

    def __init__(
        self,
        trend: str = "UNKNOWN",
        volatility: str = "UNKNOWN",
        volume: str = "UNKNOWN",
        risk_mode: str = "NORMAL",
    ) -> None:
        self.trend = trend
        self.volatility = volatility
        self.volume = volume
        self.risk_mode = risk_mode

    def to_dict(self) -> dict[str, str]:
        return {
            "trend": self.trend,
            "volatility": self.volatility,
            "volume": self.volume,
            "risk_mode": self.risk_mode,
        }


class PositionView:
    """持仓视图（只读）

    提供当前部署相关持仓信息。
    """

    def __init__(
        self,
        position_id: str,
        instrument_id: str,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        mark_price: Decimal | None = None,
        unrealized_pnl: Decimal | None = None,
    ) -> None:
        self.position_id = position_id
        self.instrument_id = instrument_id
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.mark_price = mark_price
        self.unrealized_pnl = unrealized_pnl

    @classmethod
    def from_model(cls, pos: Position) -> PositionView:
        """从 Position 模型创建视图"""
        return cls(
            position_id=pos.position_id,
            instrument_id=pos.instrument_id,
            side=pos.side.value,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            mark_price=pos.mark_price,
            unrealized_pnl=pos.unrealized_pnl,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "instrument_id": self.instrument_id,
            "side": self.side,
            "quantity": str(self.quantity),
            "entry_price": str(self.entry_price),
            "mark_price": str(self.mark_price) if self.mark_price else None,
            "unrealized_pnl": str(self.unrealized_pnl) if self.unrealized_pnl else None,
        }


class StrategyContext:
    """策略上下文

    提供策略运行时所需的全部能力。所有提供的行情/特征/持仓都是只读视图。

    能力表：
    - params: 当前部署的参数快照（不可变）
    - latest_bar: 最新已处理 K 线
    - feature: 读取 Feature Pipeline 最新值
    - regime: 当前市场状态与风险模式
    - history: 最近 n 根 K 线
    - positions: 当前部署相关持仓
    - cash: 可用资金
    - state: 跨回调状态（框架持久化）
    - emit_intent: 唯一输出通道
    - log: 策略日志
    """

    def __init__(
        self,
        strategy_id: str,
        version_no: int,
        deployment_id: str,
        params: dict[str, Any],
        instruments: list[str],
        interval: str,
        feature_pipeline: Callable[..., dict[str, float]] | None = None,
        event_bus: Callable[[dict[str, Any]], None] | None = None,
        default_valid_until_seconds: int = 600,
    ) -> None:
        """初始化策略上下文

        Args:
            strategy_id: 策略 ID
            version_no: 版本号
            deployment_id: 部署实例 ID
            params: 参数快照（不可变）
            instruments: 标的列表
            interval: K 线间隔
            feature_pipeline: 特征管道回调（可选）
            event_bus: 事件总线回调（可选）
            default_valid_until_seconds: 默认有效期（秒）
        """
        self._strategy_id = strategy_id
        self._version_no = version_no
        self._deployment_id = deployment_id
        self._params = params
        self._instruments = instruments
        self._interval = interval
        self._feature_pipeline = feature_pipeline
        self._event_bus = event_bus
        self._default_valid_until_seconds = default_valid_until_seconds

        # 内部状态
        self._latest_bars: dict[str, Bar] = {}
        self._history: dict[str, list[Bar]] = {}
        self._positions: list[PositionView] = []
        self._cash: Decimal = Decimal("0")
        self._state: dict[str, Any] = {}
        self._regime = MarketRegimeView()
        self._log_buffer: list[dict[str, Any]] = []

        # 时间管理
        self._current_time: datetime | None = None

    # ── 参数访问 ──────────────────────────────────────────

    @property
    def params(self) -> dict[str, Any]:
        """当前部署的参数快照（不可变）"""
        return self._params

    @property
    def strategy_id(self) -> str:
        """策略 ID"""
        return self._strategy_id

    @property
    def version_no(self) -> int:
        """版本号"""
        return self._version_no

    @property
    def deployment_id(self) -> str:
        """部署实例 ID"""
        return self._deployment_id

    @property
    def instruments(self) -> list[str]:
        """标的列表"""
        return self._instruments

    @property
    def interval(self) -> str:
        """K 线间隔"""
        return self._interval

    # ── 行情访问 ──────────────────────────────────────────

    def set_latest_bar(self, instrument_id: str, bar: Bar) -> None:
        """设置最新 K 线（由框架调用）"""
        self._latest_bars[instrument_id] = bar
        # 更新历史
        if instrument_id not in self._history:
            self._history[instrument_id] = []
        self._history[instrument_id].append(bar)

    def latest_bar(self, instrument_id: str) -> Bar | None:
        """获取最新已处理 K 线"""
        return self._latest_bars.get(instrument_id)

    def history(self, instrument_id: str, n: int) -> list[Bar]:
        """获取最近 n 根 K 线

        Args:
            instrument_id: 标的 ID
            n: 数量

        Returns:
            最近 n 根 K 线（按时间升序）
        """
        bars = self._history.get(instrument_id, [])
        return bars[-n:] if n > 0 else bars

    # ── 特征访问 ──────────────────────────────────────────

    def feature(self, instrument_id: str, name: str) -> float | None:
        """读取 Feature Pipeline 最新值

        Args:
            instrument_id: 标的 ID
            name: 特征名

        Returns:
            特征值，缓存未命中返回 None

        说明：
        - 缓存未命中（返回空）时按"特征暂不可用"处理
        - 策略应跳过本轮信号，不得用空值/零值继续计算
        """
        if self._feature_pipeline is None:
            return None

        try:
            result = self._feature_pipeline(instrument_id, [name])
            if result and name in result:
                return result[name]
            return None
        except Exception:
            return None

    def features(self, instrument_id: str, names: list[str]) -> dict[str, float]:
        """批量读取特征值

        Args:
            instrument_id: 标的 ID
            names: 特征名列表

        Returns:
            {feature_name: value}，缺失的特征不包含在结果中
        """
        if self._feature_pipeline is None:
            return {}

        try:
            result = self._feature_pipeline(instrument_id, names)
            return {k: v for k, v in result.items() if k in names}
        except Exception:
            return {}

    # ── 市场状态 ──────────────────────────────────────────

    @property
    def regime(self) -> MarketRegimeView:
        """当前市场状态与风险模式（只读软信号）"""
        return self._regime

    def set_regime(self, regime: MarketRegimeView) -> None:
        """设置市场状态（由框架调用）"""
        self._regime = regime

    # ── 持仓与资金 ────────────────────────────────────────

    def set_positions(self, positions: list[PositionView]) -> None:
        """设置持仓视图（由框架调用）"""
        self._positions = positions

    @property
    def positions(self) -> list[PositionView]:
        """当前部署相关持仓（只读）"""
        return self._positions

    def set_cash(self, cash: Decimal) -> None:
        """设置可用资金（由框架调用）"""
        self._cash = cash

    @property
    def cash(self) -> Decimal:
        """可用资金（只读）"""
        return self._cash

    # ── 状态管理 ──────────────────────────────────────────

    @property
    def state(self) -> MutableMapping[str, Any]:
        """跨回调状态（框架持久化，仅本部署可见）"""
        return self._state

    def save_state(self) -> dict[str, Any]:
        """保存当前状态（用于持久化）"""
        return dict(self._state)

    def restore_state(self, state: dict[str, Any]) -> None:
        """恢复状态（用于重启恢复）"""
        self._state.clear()
        self._state.update(state)

    # ── 意图输出 ──────────────────────────────────────────

    def emit_intent(self, intent: dict[str, Any]) -> None:
        """输出交易意图（唯一输出通道）

        Args:
            intent: 意图字典，至少包含 direction, action, order_type

        说明：
        - 框架补全 intent_id, strategy_id, version_no, deployment_id, valid_until
        - 执行字段白名单校验
        - 发出 INTENT_CREATED 事件
        """
        from qps.core.sdk.intent_builder import IntentBuilder

        builder = IntentBuilder(
            strategy_id=self._strategy_id,
            version_no=self._version_no,
            deployment_id=self._deployment_id,
            default_valid_until_seconds=self._default_valid_until_seconds,
            event_bus=self._event_bus,
        )
        builder.build_and_emit(intent)

    # ── 日志 ──────────────────────────────────────────────

    def log(self, level: str, message: str, **extra: Any) -> None:
        """策略日志

        Args:
            level: 日志级别（DEBUG, INFO, WARNING, ERROR）
            message: 消息内容
            **extra: 额外字段
        """
        entry = {
            "level": level,
            "message": message,
            "strategy_id": self._strategy_id,
            "deployment_id": self._deployment_id,
            "timestamp": datetime.utcnow().isoformat(),
            **extra,
        }
        self._log_buffer.append(entry)

        # 同时输出到标准日志
        logger = logging.getLogger(f"qps.strategy.{self._strategy_id}")
        log_method = getattr(logger, level.lower(), logger.info)
        log_method(message, extra=extra)

    def get_logs(self) -> list[dict[str, Any]]:
        """获取日志缓冲区"""
        return list(self._log_buffer)

    # ── 时间管理 ──────────────────────────────────────────

    def set_current_time(self, time: datetime) -> None:
        """设置当前时间（由框架调用，回测时注入）"""
        self._current_time = time

    @property
    def current_time(self) -> datetime | None:
        """当前时间（回测时由框架注入，实盘时为系统时间）"""
        if self._current_time is not None:
            return self._current_time
        return datetime.utcnow()
