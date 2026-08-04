"""StrategyContext 上下文测试（见 docs/04 第 4 节）

测试 feature 缓存未命中、params/positions 只读、state save/restore、emit_intent 经 event_bus 捕获。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from qps.core.models.account import Position
from qps.core.models.market import Bar
from qps.core.sdk.context import (
    MarketRegimeView,
    PositionView,
    StrategyContext,
)


class TestFeatureCacheMiss:
    """特征缓存未命中测试"""

    def test_feature_pipeline_returns_empty(self) -> None:
        """Feature Pipeline 返回空字典 → None"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
            feature_pipeline=lambda inst, names: {},
        )
        result = ctx.feature("BTC-USDT-PERP", "returns")
        assert result is None

    def test_feature_pipeline_raises_exception(self) -> None:
        """Feature Pipeline 抛异常 → None"""
        def failing_pipeline(inst: str, names: list[str]) -> dict[str, float]:
            raise RuntimeError("Pipeline error")

        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
            feature_pipeline=failing_pipeline,
        )
        result = ctx.feature("BTC-USDT-PERP", "returns")
        assert result is None

    def test_feature_pipeline_none(self) -> None:
        """无 Feature Pipeline → None"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )
        result = ctx.feature("BTC-USDT-PERP", "returns")
        assert result is None

    def test_features_batch_missing(self) -> None:
        """批量读取特征缺失"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
            feature_pipeline=lambda inst, names: {"returns": 0.01},
        )
        result = ctx.features("BTC-USDT-PERP", ["returns", "realized_vol_20"])
        # 只返回存在的特征
        assert "returns" in result
        assert "realized_vol_20" not in result


class TestParamsReadonly:
    """参数只读测试"""

    def test_params_immutable(self) -> None:
        """params 不可变：对返回对象赋值/修改 → TypeError"""
        params = {"lookback": 50}
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params=params,
            instruments=[],
            interval="5m",
        )
        # MappingProxyType 不允许赋值
        with pytest.raises(TypeError):
            ctx.params["lookback"] = 100  # type: ignore[index]

    def test_params_not_modifiable(self) -> None:
        """无法通过 ctx.params 修改原始参数"""
        params = {"lookback": 50}
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params=params,
            instruments=[],
            interval="5m",
        )
        # 尝试修改 params 视图应抛 TypeError
        with pytest.raises(TypeError):
            ctx.params["new_key"] = "value"  # type: ignore[index]
        # 原始数据不受影响
        assert params == {"lookback": 50}

    def test_params_get_still_works(self) -> None:
        """ctx.params.get(...) 仍可用（MappingProxyType 兼容）"""
        params = {"lookback": 50, "entry_z": 2.0}
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params=params,
            instruments=[],
            interval="5m",
        )
        assert ctx.params.get("lookback") == 50
        assert ctx.params.get("missing", "default") == "default"
        assert "entry_z" in ctx.params


class TestPositionsReadonly:
    """持仓视图只读测试"""

    def test_positions_view_creation(self) -> None:
        """持仓视图创建"""
        pos = PositionView(
            position_id="pos_1",
            instrument_id="BTC-USDT-PERP",
            side="LONG",
            quantity=Decimal("0.1"),
            entry_price=Decimal("67000"),
            mark_price=Decimal("68000"),
            unrealized_pnl=Decimal("100"),
        )
        assert pos.quantity == Decimal("0.1")
        assert pos.to_dict()["quantity"] == "0.1"

    def test_positions_from_model(self) -> None:
        """从 Position 模型创建视图"""
        from qps.core.constants import Direction

        pos = Position(
            position_id="pos_1",
            account_id="acc_1",
            instrument_id="BTC-USDT-PERP",
            side=Direction.LONG,
            quantity=Decimal("0.1"),
            entry_price=Decimal("67000"),
            updated_ts=datetime.now(UTC),
        )
        view = PositionView.from_model(pos)
        assert view.side == "LONG"
        assert view.quantity == Decimal("0.1")

    def test_positions_returns_tuple(self) -> None:
        """ctx.positions 返回 tuple，修改不影响内部状态"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )
        pos = PositionView(
            position_id="pos_1",
            instrument_id="BTC-USDT-PERP",
            side="LONG",
            quantity=Decimal("0.1"),
            entry_price=Decimal("67000"),
        )
        ctx.set_positions([pos])

        # 返回的是 tuple
        assert isinstance(ctx.positions, tuple)
        assert len(ctx.positions) == 1
        assert ctx.positions[0].position_id == "pos_1"

    def test_positions_modification_does_not_affect_internal(self) -> None:
        """修改返回的 positions 不影响内部状态"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )
        pos = PositionView(
            position_id="pos_1",
            instrument_id="BTC-USDT-PERP",
            side="LONG",
            quantity=Decimal("0.1"),
            entry_price=Decimal("67000"),
        )
        ctx.set_positions([pos])

        # 获取 positions 后再设置新持仓
        positions_before = ctx.positions
        pos2 = PositionView(
            position_id="pos_2",
            instrument_id="ETH-USDT-PERP",
            side="SHORT",
            quantity=Decimal("1.0"),
            entry_price=Decimal("3500"),
        )
        ctx.set_positions([pos, pos2])

        # 之前获取的 positions 不受影响（tuple 不可变）
        assert len(positions_before) == 1
        # 新获取的 positions 反映最新状态
        assert len(ctx.positions) == 2


class TestStateSaveRestore:
    """状态保存恢复测试"""

    def test_state_save_and_restore(self) -> None:
        """状态保存和恢复"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )

        # 设置状态
        ctx.state["position_side"] = "LONG"
        ctx.state["entry_price"] = Decimal("67000")

        # 保存
        saved = ctx.save_state()
        assert saved["position_side"] == "LONG"

        # 恢复
        ctx.restore_state({"position_side": "SHORT", "entry_price": Decimal("68000")})
        assert ctx.state["position_side"] == "SHORT"
        assert ctx.state["entry_price"] == Decimal("68000")

    def test_state_clear_on_restore(self) -> None:
        """恢复时清空旧状态"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )

        ctx.state["old_key"] = "old_value"
        ctx.restore_state({"new_key": "new_value"})

        assert "old_key" not in ctx.state
        assert ctx.state["new_key"] == "new_value"


class TestEmitIntentViaEventBus:
    """emit_intent 经 event_bus 捕获测试"""

    def test_emit_intent_captured(self) -> None:
        """emit_intent 被 event_bus 捕获"""
        events_captured: list[dict[str, Any]] = []

        def event_bus(data: dict[str, Any]) -> None:
            events_captured.append(data)

        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
            event_bus=event_bus,
        )

        ctx.emit_intent({
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        })

        assert len(events_captured) == 1
        assert "event" in events_captured[0]
        assert "intent" in events_captured[0]

    def test_emit_intent_without_event_bus(self) -> None:
        """无 event_bus 也能正常发射"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )

        # 不应抛出异常
        ctx.emit_intent({
            "direction": "LONG",
            "action": "OPEN",
            "order_type": "MARKET",
            "quantity": 0.1,
        })


class TestMarketRegimeView:
    """市场状态视图测试"""

    def test_default_values(self) -> None:
        """默认值"""
        regime = MarketRegimeView()
        assert regime.trend == "UNKNOWN"
        assert regime.risk_mode == "NORMAL"

    def test_custom_values(self) -> None:
        """自定义值"""
        regime = MarketRegimeView(
            trend="UPTREND",
            volatility="HIGH",
            risk_mode="HALT",
        )
        assert regime.trend == "UPTREND"
        assert regime.volatility == "HIGH"
        assert regime.risk_mode == "HALT"

    def test_to_dict(self) -> None:
        """转换为字典"""
        regime = MarketRegimeView(trend="UPTREND")
        d = regime.to_dict()
        assert d["trend"] == "UPTREND"
        assert d["risk_mode"] == "NORMAL"


class TestLatestBarAndHistory:
    """最新 K 线与历史测试"""

    def test_set_and_get_latest_bar(self) -> None:
        """设置和获取最新 K 线"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )

        bar = Bar(
            instrument_id="BTC-USDT-PERP",
            open_time=datetime(2026, 1, 1, tzinfo=UTC),
            interval="5m",
            open=Decimal("66000"),
            high=Decimal("67000"),
            low=Decimal("66000"),
            close=Decimal("67000"),
            volume=Decimal("1000"),
            quote_volume=Decimal("100000"),
            data_version="test",
        )

        ctx.set_latest_bar("BTC-USDT-PERP", bar)
        latest = ctx.latest_bar("BTC-USDT-PERP")
        assert latest is not None
        assert latest.close == Decimal("67000")

    def test_history(self) -> None:
        """历史 K 线"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )

        bars = []
        for i in range(10):
            bar = Bar(
                instrument_id="BTC-USDT-PERP",
                open_time=datetime(2026, 1, 1, tzinfo=UTC),
                interval="5m",
                open=Decimal("66000"),
                high=Decimal("67000"),
                low=Decimal("66000"),
                close=Decimal(str(67000 + i)),
                volume=Decimal("1000"),
                quote_volume=Decimal("100000"),
                data_version="test",
            )
            bars.append(bar)
            ctx.set_latest_bar("BTC-USDT-PERP", bar)

        history = ctx.history("BTC-USDT-PERP", 5)
        assert len(history) == 5
        assert history[-1].close == Decimal("67009")

    def test_latest_bar_none_when_not_set(self) -> None:
        """未设置时返回 None"""
        ctx = StrategyContext(
            strategy_id="test",
            version_no=1,
            deployment_id="dep_test",
            params={},
            instruments=[],
            interval="5m",
        )
        assert ctx.latest_bar("BTC-USDT-PERP") is None
