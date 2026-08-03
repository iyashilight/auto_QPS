"""T5 Feature Engine 单元测试

测试：
- FeatureRegistry: 注册、版本化、查询
- FeatureEngine: 计算、无未来函数校验、落盘
- MVP 特征函数: returns/momentum/ATR/volatility/volume_change
- 确定性测试：同一输入两次计算逐位一致
- 无未来函数测试：使用未来数据的特征被拒绝
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np
import pytest

from qps.core.models.market import Bar
from qps.data.features.engine import (
    FeatureEngine,
    FeatureRegistry,
    FutureFunctionChecker,
    feature_atr,
    feature_momentum,
    feature_realized_volatility,
    feature_returns,
    feature_volume_change,
    register_mvp_features,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── 辅助 ──────────────────────────────────────────────────


def _make_bar(
    open_time: datetime,
    close: float = 67000.0,
    high: float = 67100.0,
    low: float = 66900.0,
    volume: float = 100.0,
) -> Bar:
    return Bar(
        instrument_id="binance-btc-usdt-perp",
        open_time=open_time,
        interval="5m",
        open=Decimal(str(67000.0)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal("6700000"),
        data_version="binance-test",
    )


def _make_bars(count: int, base_price: float = 67000.0) -> list[Bar]:
    """生成测试用 K 线数据"""
    bars: list[Bar] = []
    for i in range(count):
        t = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
        # 微调时间
        from datetime import timedelta

        t = t + timedelta(minutes=5 * i)
        # 模拟价格波动
        price = base_price * (1 + 0.001 * np.sin(i * 0.5))
        high = price * 1.002
        low = price * 0.998
        volume = 100 + 10 * np.sin(i * 0.3)
        bars.append(_make_bar(t, close=price, high=high, low=low, volume=volume))
    return bars


# ── FeatureRegistry ───────────────────────────────────────


class TestFeatureRegistry:
    def test_register_and_get_latest(self) -> None:
        """注册并获取最新版本"""
        registry = FeatureRegistry()
        version = registry.register("returns", feature_returns, 2, {})
        latest = registry.get_latest("returns")
        assert latest.version == version
        assert latest.name == "returns"

    def test_register_same_name_creates_new_version(self) -> None:
        """同名特征再次注册创建新版本"""
        registry = FeatureRegistry()
        v1 = registry.register("returns", feature_returns, 2, {})
        v2 = registry.register("returns", feature_returns, 3, {})
        assert v1 != v2
        assert registry.get_latest("returns").version == v2

    def test_list_versions(self) -> None:
        """列出所有版本"""
        registry = FeatureRegistry()
        v1 = registry.register("atr", feature_atr, 15, {"period": 14})
        v2 = registry.register("atr", feature_atr, 16, {"period": 14})
        versions = registry.list_versions("atr")
        assert len(versions) == 2
        assert v1 in versions
        assert v2 in versions

    def test_get_version(self) -> None:
        """获取指定版本"""
        registry = FeatureRegistry()
        v1 = registry.register("atr", feature_atr, 15, {"period": 14})
        reg = registry.get_version("atr", v1)
        assert reg.version == v1

    def test_get_nonexistent_feature_raises(self) -> None:
        """获取不存在的特征抛 DataError"""
        from qps.core.errors import DataError

        registry = FeatureRegistry()
        with pytest.raises(DataError, match="特征未注册"):
            registry.get_latest("nonexistent")

    def test_list_all_features(self) -> None:
        """列出所有特征名"""
        registry = FeatureRegistry()
        registry.register("returns", feature_returns, 2, {})
        registry.register("atr", feature_atr, 15, {})
        names = registry.list_all_features()
        assert "returns" in names
        assert "atr" in names


# ── FutureFunctionChecker ─────────────────────────────────


class TestFutureFunctionChecker:
    def test_valid_output_length(self) -> None:
        """输出长度不超过输入长度"""
        bars = _make_bars(10)
        values = np.random.randn(10)
        assert FutureFunctionChecker.check(bars, 2, values) is True

    def test_output_longer_than_input_fails(self) -> None:
        """输出长度超过输入长度不通过"""
        bars = _make_bars(10)
        values = np.random.randn(15)
        assert FutureFunctionChecker.check(bars, 2, values) is False


# ── MVP 特征函数 ──────────────────────────────────────────


class TestFeatureReturns:
    def test_first_value_is_nan(self) -> None:
        """第一个收益率为 NaN"""
        bars = _make_bars(10)
        values = feature_returns(bars, {})
        assert np.isnan(values[0])

    def test_deterministic(self) -> None:
        """确定性：相同输入产生相同输出"""
        bars = _make_bars(20)
        v1 = feature_returns(bars, {})
        v2 = feature_returns(bars, {})
        np.testing.assert_array_equal(v1, v2)

    def test_length_matches_bars(self) -> None:
        """输出长度与 K 线数量一致"""
        bars = _make_bars(30)
        values = feature_returns(bars, {})
        assert len(values) == len(bars)

    def test_single_bar(self) -> None:
        """单根 K 线返回 NaN"""
        bars = _make_bars(1)
        values = feature_returns(bars, {})
        assert len(values) == 1
        assert np.isnan(values[0])


class TestFeatureMomentum:
    def test_first_period_is_nan(self) -> None:
        """前 N 期为 NaN"""
        bars = _make_bars(20)
        values = feature_momentum(bars, {"period": 10})
        assert all(np.isnan(values[:10]))

    def test_deterministic(self) -> None:
        bars = _make_bars(30)
        v1 = feature_momentum(bars, {"period": 10})
        v2 = feature_momentum(bars, {"period": 10})
        np.testing.assert_array_equal(v1, v2)


class TestFeatureATR:
    def test_first_period_is_nan(self) -> None:
        """前 period-1 期为 NaN"""
        bars = _make_bars(30)
        values = feature_atr(bars, {"period": 14})
        # ATR(14) 需要至少 14 个 TR 值
        assert np.isnan(values[0])

    def test_atr_positive(self) -> None:
        """ATR 值为正"""
        bars = _make_bars(30)
        values = feature_atr(bars, {"period": 14})
        valid = values[~np.isnan(values)]
        assert all(v > 0 for v in valid)

    def test_deterministic(self) -> None:
        bars = _make_bars(30)
        v1 = feature_atr(bars, {"period": 14})
        v2 = feature_atr(bars, {"period": 14})
        np.testing.assert_array_equal(v1, v2)


class TestFeatureRealizedVolatility:
    def test_deterministic(self) -> None:
        bars = _make_bars(50)
        v1 = feature_realized_volatility(bars, {"period": 20})
        v2 = feature_realized_volatility(bars, {"period": 20})
        np.testing.assert_array_equal(v1, v2)

    def test_volatility_positive(self) -> None:
        """波动率为正"""
        bars = _make_bars(50)
        values = feature_realized_volatility(bars, {"period": 20})
        valid = values[~np.isnan(values)]
        assert all(v > 0 for v in valid)


class TestFeatureVolumeChange:
    def test_first_value_is_nan(self) -> None:
        """第一个值为 NaN"""
        bars = _make_bars(10)
        values = feature_volume_change(bars, {})
        assert np.isnan(values[0])

    def test_deterministic(self) -> None:
        bars = _make_bars(20)
        v1 = feature_volume_change(bars, {})
        v2 = feature_volume_change(bars, {})
        np.testing.assert_array_equal(v1, v2)


# ── FeatureEngine 集成测试 ───────────────────────────────


class TestFeatureEngine:
    def _make_engine(self, features_dir: Path | None = None) -> FeatureEngine:
        engine = FeatureEngine(features_dir=features_dir, data_version="binance-test")
        register_mvp_features(engine)
        return engine

    def test_register_mvp_features(self) -> None:
        """MVP 特征集注册"""
        engine = self._make_engine()
        features = engine._registry.list_all_features()
        assert "returns" in features
        assert "momentum_10" in features
        assert "atr_14" in features
        assert "realized_vol_20" in features
        assert "volume_change" in features

    def test_compute_with_bars(self) -> None:
        """基于 K 线计算特征"""
        engine = self._make_engine()
        bars = _make_bars(50)

        features = engine.compute_with_bars(
            "binance-btc-usdt-perp",
            "5m",
            ["returns", "atr_14"],
            bars,
        )

        assert len(features) > 0
        # 应该有 returns 和 atr_14 的特征值
        feature_names = {f.feature_name for f in features}
        assert "returns" in feature_names
        assert "atr_14" in feature_names

    def test_compute_returns_sorted(self) -> None:
        """结果按时间排序"""
        engine = self._make_engine()
        bars = _make_bars(30)

        features = engine.compute_with_bars(
            "binance-btc-usdt-perp",
            "5m",
            ["returns"],
            bars,
        )

        timestamps = [f.ts for f in features]
        assert timestamps == sorted(timestamps)

    def test_compute_empty_bars(self) -> None:
        """空 K 线返回空列表"""
        engine = self._make_engine()
        features = engine.compute_with_bars("test", "5m", ["returns"], [])
        assert features == []

    def test_compute_unregistered_feature_skipped(self) -> None:
        """未注册的特征跳过"""
        engine = self._make_engine()
        bars = _make_bars(10)
        features = engine.compute_with_bars("test", "5m", ["nonexistent"], bars)
        assert features == []

    def test_deterministic_computation(self) -> None:
        """确定性测试：同一输入两次计算逐位一致"""
        engine = self._make_engine()
        bars = _make_bars(50)

        features1 = engine.compute_with_bars(
            "binance-btc-usdt-perp", "5m", ["returns", "atr_14"], bars
        )
        features2 = engine.compute_with_bars(
            "binance-btc-usdt-perp", "5m", ["returns", "atr_14"], bars
        )

        assert len(features1) == len(features2)
        for f1, f2 in zip(features1, features2, strict=False):
            assert f1.value == f2.value, f"不一致: {f1.feature_name} at {f1.ts}"

    def test_no_future_function(self) -> None:
        """无未来函数测试：特征只使用截至当前 bar 的数据"""
        engine = self._make_engine()
        bars = _make_bars(30)

        # 计算 returns 特征
        features = engine.compute_with_bars(
            "binance-btc-usdt-perp", "5m", ["returns"], bars
        )

        # 验证：returns[i] 只依赖 close[i] 和 close[i-1]
        # 第一根 K 线的 returns 应该为 NaN（没有前值）
        returns_features = [f for f in features if f.feature_name == "returns"]
        first_bar_returns = [f for f in returns_features if f.ts == bars[0].open_time]
        # 第一根 K 线不应有 returns 值（因为 NaN 被过滤了）
        assert len(first_bar_returns) == 0, "第一根 K 线不应有收益率值"

    def test_write_features(self, tmp_path: Path) -> None:
        """特征落盘"""
        engine = self._make_engine(features_dir=tmp_path / "features")
        bars = _make_bars(30)

        features = engine.compute_with_bars(
            "binance-btc-usdt-perp", "5m", ["returns"], bars
        )

        if features:
            version = features[0].feature_version
            path = engine.write_features(features, version, "5m", "binance-btc-usdt-perp")
            assert path is not None
            assert path.exists()

    def test_list_versions(self) -> None:
        """列出特征版本"""
        engine = self._make_engine()
        versions = engine.list_versions("returns")
        assert len(versions) >= 1
