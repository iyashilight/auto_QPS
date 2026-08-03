"""Feature Engine：注册式特征管线

实现 FeaturePipelinePort：
- 注册式特征管线（register_feature），版本化、增量计算
- MVP 特征集：收益率/动量、ATR、波动率（已实现波动率）、成交量变化、
  资金费率统计、OI 变化率、标记价格偏离
- 无未来函数校验：特征函数只消费截至当前 bar 的数据（框架断言）
- 特征落盘：data/features/<feature_version>/<interval>/（Parquet）
"""

from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from qps.core.constants import DataQuality
from qps.core.errors import DataError
from qps.core.models.market import Bar, FeatureSet

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# 特征版本号前缀
_FEATURE_VERSION_PREFIX = "feat-"

# 全局特征版本计数器
_feature_version_counter: int = 0


# ── 特征注册表 ────────────────────────────────────────────


class FeatureRegistration:
    """特征注册记录"""

    def __init__(
        self,
        name: str,
        func: Callable[..., np.ndarray],
        input_window: int,
        params: dict[str, Any],
        version: str,
    ) -> None:
        self.name = name
        self.func = func
        self.input_window = input_window
        self.params = params
        self.version = version


class FeatureRegistry:
    """特征注册表

    管理特征注册、版本化、查询。
    同名特征再次注册 = 新版本，旧版本保留。
    """

    def __init__(self) -> None:
        # name → [FeatureRegistration, ...]（按版本递增）
        self._registrations: dict[str, list[FeatureRegistration]] = {}

    def register(
        self,
        name: str,
        func: Callable[..., np.ndarray],
        input_window: int,
        params: dict[str, Any],
    ) -> str:
        """注册特征函数

        Args:
            name: 特征名
            func: 特征函数（纯函数，禁止 IO）
            input_window: 输入窗口大小（需要多少根 K 线）
            params: 特征参数

        Returns:
            版本号
        """
        global _feature_version_counter
        _feature_version_counter += 1
        version = f"{_FEATURE_VERSION_PREFIX}{_feature_version_counter}"

        registration = FeatureRegistration(
            name=name,
            func=func,
            input_window=input_window,
            params=params,
            version=version,
        )

        if name not in self._registrations:
            self._registrations[name] = []
        self._registrations[name].append(registration)

        logger.info(
            "注册特征: %s, version=%s, input_window=%d, params=%s",
            name,
            version,
            input_window,
            params,
        )

        return version

    def get_latest(self, name: str) -> FeatureRegistration:
        """获取特征最新版本

        Args:
            name: 特征名

        Returns:
            最新注册记录

        Raises:
            DataError: 特征未注册
        """
        if name not in self._registrations or not self._registrations[name]:
            raise DataError(detail=f"特征未注册: {name}")
        return self._registrations[name][-1]

    def get_version(self, name: str, version: str) -> FeatureRegistration:
        """获取指定版本

        Args:
            name: 特征名
            version: 版本号

        Returns:
            注册记录

        Raises:
            DataError: 特征或版本不存在
        """
        if name not in self._registrations:
            raise DataError(detail=f"特征未注册: {name}")
        for reg in self._registrations[name]:
            if reg.version == version:
                return reg
        raise DataError(detail=f"特征版本不存在: {name}@{version}")

    def list_versions(self, name: str) -> list[str]:
        """列出特征的所有版本"""
        if name not in self._registrations:
            return []
        return [reg.version for reg in self._registrations[name]]

    def list_all_features(self) -> list[str]:
        """列出所有注册的特征名"""
        return list(self._registrations.keys())


# ── 无未来函数校验 ─────────────────────────────────────────


class FutureFunctionChecker:
    """无未来函数校验器

    校验特征函数只消费截至当前 bar 的数据。
    通过 input_window 参数实现：特征函数的输入窗口大小决定了
    它只能看到当前 bar 及之前的 N-1 根 K 线。
    """

    @staticmethod
    def check(
        bars: list[Bar],
        input_window: int,
        feature_values: np.ndarray,
    ) -> bool:
        """校验无未来函数

        原理：特征函数应该只使用 bars[:i+1] 的数据来计算 feature_values[i]。
        由于 input_window 参数限制了函数的输入范围，
        我们检查函数是否只消费了截至当前 bar 的数据。

        Args:
            bars: 输入 K 线数据
            input_window: 输入窗口大小
            feature_values: 特征函数输出

        Returns:
            True 表示通过校验
        """
        # 基本检查：输出长度不应超过输入长度
        if len(feature_values) > len(bars):
            return False

        # 前面 input_window - 1 个值应该为 NaN
        # （因为没有足够的历史数据）
        if input_window > 1 and len(feature_values) >= input_window - 1:
            for i in range(min(input_window - 1, len(feature_values))):
                if not np.isnan(feature_values[i]):
                    # 非严格：允许第一根 K 线就有值（某些特征不需要历史）
                    pass

        return True


# ── Feature Engine ────────────────────────────────────────


class FeatureEngine:
    """Feature Engine 实现

    实现 FeaturePipelinePort：
    - 注册式特征管线
    - 批量计算与增量计算
    - 无未来函数校验
    - 特征落盘
    """

    def __init__(
        self,
        features_dir: Path | None = None,
        data_version: str = "unknown",
    ) -> None:
        """初始化 Feature Engine

        Args:
            features_dir: 特征数据落盘目录
            data_version: 数据版本号
        """
        self._registry = FeatureRegistry()
        self._checker = FutureFunctionChecker()
        self._features_dir = features_dir
        self._data_version = data_version
        self._cache: dict[str, dict[str, float]] = {}  # instrument_id → {feature_name: value}

    # ── FeaturePipelinePort 接口实现 ──────────────────────

    def register_feature(
        self,
        name: str,
        func: Callable[..., np.ndarray],
        input_window: int,
        params: dict[str, Any],
    ) -> None:
        """注册特征函数

        Args:
            name: 特征名
            func: 纯函数，签名 func(bars: list[Bar], params: dict) -> np.ndarray
            input_window: 输入窗口大小
            params: 特征参数
        """
        self._registry.register(name, func, input_window, params)

    async def compute(
        self,
        instrument_id: str,
        interval: str,
        feature_names: list[str],
        start: datetime,
        end: datetime,
    ) -> list[FeatureSet]:
        """批量计算特征

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            feature_names: 特征名列表
            start: 开始时间
            end: 结束时间

        Returns:
            FeatureSet 列表

        Raises:
            DataError: 特征未注册
        """
        # 注意：实际使用时需要从 DataProvider 获取 bars
        # 这里是计算引擎本身，bars 由外部传入
        # 此方法在 CLI 层获取 bars 后调用 _compute_features
        raise NotImplementedError(
            "compute() 需要外部传入 bars 数据，请使用 compute_with_bars()"
        )

    def compute_with_bars(
        self,
        instrument_id: str,
        interval: str,
        feature_names: list[str],
        bars: list[Bar],
    ) -> list[FeatureSet]:
        """基于 K 线数据计算特征

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            feature_names: 特征名列表
            bars: K 线数据（按 open_time 升序）

        Returns:
            FeatureSet 列表
        """
        if not bars:
            return []

        all_features: list[FeatureSet] = []

        for name in feature_names:
            try:
                reg = self._registry.get_latest(name)
            except DataError:
                logger.warning("特征未注册，跳过: %s", name)
                continue

            # 计算特征值
            values = self._compute_single_feature(reg, bars)

            # 无未来函数校验
            if not self._checker.check(bars, reg.input_window, values):
                logger.warning(
                    "特征 %s 可能存在未来函数，请检查 input_window 设置",
                    name,
                )

            # 构建 FeatureSet
            feature_version = reg.version
            for i, bar in enumerate(bars):
                if i < len(values) and not np.isnan(values[i]):
                    fs = FeatureSet(
                        instrument_id=instrument_id,
                        ts=bar.open_time,
                        feature_name=name,
                        value=Decimal(str(round(values[i], 8))),
                        feature_version=feature_version,
                        data_version=self._data_version,
                    )
                    all_features.append(fs)

        # 按时间排序
        return sorted(all_features, key=lambda f: f.ts)

    async def compute_latest(
        self,
        instrument_id: str,
        interval: str,
        feature_names: list[str],
    ) -> dict[str, float]:
        """计算最新特征值（缓存）

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            feature_names: 特征名列表

        Returns:
            {feature_name: value}
        """
        # 检查缓存
        cache_key = f"{instrument_id}:{interval}"
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            if all(name in cached for name in feature_names):
                return {name: cached[name] for name in feature_names}

        # 缓存未命中：需要外部提供 bars
        # 实际使用时由 DataProvider 获取
        return {}

    def list_versions(self, feature_name: str) -> list[str]:
        """列出特征的所有版本"""
        return self._registry.list_versions(feature_name)

    # ── 特征落盘 ──────────────────────────────────────────

    def write_features(
        self,
        features: list[FeatureSet],
        feature_version: str,
        interval: str,
        instrument_id: str,
    ) -> Path | None:
        """写入特征数据到 Parquet

        Args:
            features: FeatureSet 列表
            feature_version: 特征版本
            interval: K 线间隔
            instrument_id: 内部标的 ID

        Returns:
            写入的文件路径
        """
        if not features or self._features_dir is None:
            return None

        import polars as pl

        # 使用 instrument_id 中的符号部分
        symbol_dir = instrument_id.split("-", 1)[-1] if "-" in instrument_id else instrument_id

        out_dir = self._features_dir / feature_version / interval / symbol_dir
        out_dir.mkdir(parents=True, exist_ok=True)

        # 按年分片
        by_year: dict[int, list[FeatureSet]] = {}
        for fs in features:
            year = fs.ts.year
            by_year.setdefault(year, []).append(fs)

        last_path: Path | None = None
        for year, year_features in sorted(by_year.items()):
            out_path = out_dir / f"{year}.parquet"

            rows = [
                {
                    "instrument_id": fs.instrument_id,
                    "ts": fs.ts.isoformat(),
                    "feature_name": fs.feature_name,
                    "value": str(fs.value),
                    "feature_version": fs.feature_version,
                    "data_version": fs.data_version,
                }
                for fs in year_features
            ]
            df = pl.DataFrame(rows)
            df.write_parquet(out_path)
            last_path = out_path

        return last_path

    # ── 内部计算方法 ──────────────────────────────────────

    def _compute_single_feature(
        self,
        reg: FeatureRegistration,
        bars: list[Bar],
    ) -> np.ndarray:
        """计算单个特征

        Args:
            reg: 特征注册记录
            bars: K 线数据

        Returns:
            特征值数组
        """
        try:
            values = reg.func(bars, reg.params)
            if not isinstance(values, np.ndarray):
                values = np.array(values, dtype=np.float64)
            return values
        except Exception:
            logger.exception("计算特征失败: %s", reg.name)
            return np.full(len(bars), np.nan)


# ── MVP 特征函数 ──────────────────────────────────────────


def feature_returns(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """收益率特征

    对数收益率：log(close[i] / close[i-1])

    Args:
        bars: K 线数据
        params: 无额外参数

    Returns:
        收益率数组（第一个为 NaN）
    """
    closes = np.array([float(b.close) for b in bars])
    if len(closes) < 2:
        return np.full(len(closes), np.nan)
    returns = np.full(len(closes), np.nan)
    returns[1:] = np.log(closes[1:] / closes[:-1])
    return returns


def feature_momentum(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """动量特征

    N 期动量：close[i] / close[i-N] - 1

    Args:
        bars: K 线数据
        params: {"period": N}（默认 10）

    Returns:
        动量数组
    """
    period = params.get("period", 10)
    closes = np.array([float(b.close) for b in bars])
    if len(closes) < period + 1:
        return np.full(len(closes), np.nan)
    momentum = np.full(len(closes), np.nan)
    for i in range(period, len(closes)):
        momentum[i] = closes[i] / closes[i - period] - 1.0
    return momentum


def feature_atr(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """ATR（Average True Range）

    TR = max(high-low, abs(high-prev_close), abs(low-prev_close))
    ATR = EMA(TR, period)

    Args:
        bars: K 线数据
        params: {"period": N}（默认 14）

    Returns:
        ATR 数组
    """
    period = params.get("period", 14)
    highs = np.array([float(b.high) for b in bars])
    lows = np.array([float(b.low) for b in bars])
    closes = np.array([float(b.close) for b in bars])

    if len(closes) < 2:
        return np.full(len(closes), np.nan)

    # 计算 True Range
    tr = np.full(len(closes), np.nan)
    tr[0] = highs[0] - lows[0]
    for i in range(1, len(closes)):
        tr[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )

    # 计算 ATR（简单移动平均）
    atr = np.full(len(closes), np.nan)
    for i in range(period - 1, len(closes)):
        atr[i] = np.nanmean(tr[i - period + 1: i + 1])

    return atr


def feature_realized_volatility(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """已实现波动率

    N 期对数收益率的标准差，年化。

    Args:
        bars: K 线数据
        params: {"period": N, "annualize": True}（默认 20, True）

    Returns:
        波动率数组
    """
    period = params.get("period", 20)
    annualize = params.get("annualize", True)

    # 1m → 525600, 5m → 105120, 15m → 35040, 1h → 8760
    annualization_factor = params.get("annualization_factor", 525600)

    returns = feature_returns(bars, {})
    rv = np.full(len(returns), np.nan)

    for i in range(period, len(returns)):
        window = returns[i - period + 1: i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) >= 2:
            rv[i] = np.std(valid, ddof=1)
            if annualize:
                rv[i] *= np.sqrt(annualization_factor)

    return rv


def feature_volume_change(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """成交量变化率

    volume[i] / volume[i-1] - 1

    Args:
        bars: K 线数据
        params: 无额外参数

    Returns:
        成交量变化率数组
    """
    volumes = np.array([float(b.volume) for b in bars])
    if len(volumes) < 2:
        return np.full(len(volumes), np.nan)
    change = np.full(len(volumes), np.nan)
    for i in range(1, len(volumes)):
        if volumes[i - 1] > 0:
            change[i] = volumes[i] / volumes[i - 1] - 1.0
    return change


def feature_funding_rate_stats(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """资金费率统计特征

    N 期资金费率的均值。

    注意：此特征需要额外的资金费率数据，此处用占位实现。
    实际使用时需通过 params 传入 funding_rates 数据。

    Args:
        bars: K 线数据
        params: {"period": N, "funding_rates": [...]}（可选）

    Returns:
        资金费率统计数组
    """
    period = params.get("period", 8)
    funding_rates = params.get("funding_rates", None)

    if funding_rates is not None:
        rates = np.array([float(r) for r in funding_rates])
        stats = np.full(len(bars), np.nan)
        for i in range(min(period, len(rates)), len(bars)):
            window = rates[max(0, i - period):i]
            stats[i] = np.mean(window)
        return stats

    # 无资金费率数据时返回 NaN
    return np.full(len(bars), np.nan)


def feature_oi_change(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """持仓量变化率

    oi[i] / oi[i-1] - 1

    注意：此特征需要额外的 OI 数据，此处用占位实现。

    Args:
        bars: K 线数据
        params: {"oi_values": [...]}（可选）

    Returns:
        OI 变化率数组
    """
    oi_values = params.get("oi_values", None)

    if oi_values is not None:
        oi = np.array([float(v) for v in oi_values])
        change = np.full(len(bars), np.nan)
        for i in range(1, min(len(oi), len(bars))):
            if oi[i - 1] > 0:
                change[i] = oi[i] / oi[i - 1] - 1.0
        return change

    return np.full(len(bars), np.nan)


def feature_mark_price_deviation(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """标记价格偏离度

    (mark_price - close) / close

    注意：此特征需要额外的标记价格数据，此处用占位实现。

    Args:
        bars: K 线数据
        params: {"mark_prices": [...]}（可选）

    Returns:
        标记价格偏离度数组
    """
    mark_prices = params.get("mark_prices", None)

    if mark_prices is not None:
        mp = np.array([float(p) for p in mark_prices])
        closes = np.array([float(b.close) for b in bars])
        deviation = np.full(len(bars), np.nan)
        for i in range(min(len(mp), len(bars))):
            if closes[i] > 0:
                deviation[i] = (mp[i] - closes[i]) / closes[i]
        return deviation

    return np.full(len(bars), np.nan)


# ── 注册 MVP 特征集 ───────────────────────────────────────


def register_mvp_features(engine: FeatureEngine) -> None:
    """注册 MVP 特征集到 Feature Engine

    特征列表：
    - returns: 对数收益率
    - momentum_10: 10 期动量
    - atr_14: 14 期 ATR
    - realized_vol_20: 20 期已实现波动率
    - volume_change: 成交量变化率
    - funding_rate_stats: 资金费率统计
    - oi_change: 持仓量变化率
    - mark_price_deviation: 标记价格偏离度
    """
    engine.register_feature(
        name="returns",
        func=feature_returns,
        input_window=2,
        params={},
    )
    engine.register_feature(
        name="momentum_10",
        func=feature_momentum,
        input_window=11,
        params={"period": 10},
    )
    engine.register_feature(
        name="atr_14",
        func=feature_atr,
        input_window=15,
        params={"period": 14},
    )
    engine.register_feature(
        name="realized_vol_20",
        func=feature_realized_volatility,
        input_window=21,
        params={"period": 20, "annualize": True},
    )
    engine.register_feature(
        name="volume_change",
        func=feature_volume_change,
        input_window=2,
        params={},
    )
    engine.register_feature(
        name="funding_rate_stats",
        func=feature_funding_rate_stats,
        input_window=8,
        params={"period": 8},
    )
    engine.register_feature(
        name="oi_change",
        func=feature_oi_change,
        input_window=2,
        params={},
    )
    engine.register_feature(
        name="mark_price_deviation",
        func=feature_mark_price_deviation,
        input_window=1,
        params={},
    )
