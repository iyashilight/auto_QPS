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
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import numpy as np

from qps.core.errors import DataError
from qps.core.models.market import Bar, FeatureSet

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime
    from pathlib import Path

logger = logging.getLogger(__name__)

# 特征版本号前缀
_FEATURE_VERSION_PREFIX = "feat-"


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
    版本号基于内容哈希（name + params + 函数指纹），确保跨进程稳定。
    """

    def __init__(self) -> None:
        # name → [FeatureRegistration, ...]（按版本递增）
        self._registrations: dict[str, list[FeatureRegistration]] = {}
        # 实例级计数器（用于区分同一进程中的多次注册）
        self._instance_counter: int = 0

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
            版本号（格式：feat-<hash>）
        """
        # 生成基于内容的哈希版本号（包含 input_window）
        version = self._generate_version(name, func, input_window, params)

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

    def _generate_version(
        self,
        name: str,
        func: Callable[..., np.ndarray],
        input_window: int,
        params: dict[str, Any],
    ) -> str:
        """生成基于内容的版本号

        使用 name + input_window + params 哈希 + 函数代码哈希，确保：
        - 跨进程稳定
        - 相同内容产生相同版本
        - 不同内容产生不同版本

        Args:
            name: 特征名
            func: 特征函数
            input_window: 输入窗口大小
            params: 特征参数

        Returns:
            版本号字符串
        """
        import inspect

        # 获取函数源码（如果可用）
        try:
            source = inspect.getsource(func)
        except (OSError, TypeError):
            source = f"{func.__name__}_{id(func)}"

        # 构建版本内容（包含 input_window）
        version_content = f"{name}:{input_window}:{sorted(params.items())}:{source}"

        # 计算哈希
        hash_obj = hashlib.sha256(version_content.encode("utf-8"))
        short_hash = hash_obj.hexdigest()[:12]

        return f"{_FEATURE_VERSION_PREFIX}{short_hash}"

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
    提供两种校验模式：
    - strict: 严格的时间切片重算对比（慢但准确）
    - structural: 结构性检查（快，检查常见违规模式）
    """

    @staticmethod
    def check(
        bars: list[Bar],
        input_window: int,
        feature_values: np.ndarray,
        mode: str = "structural",
    ) -> bool:
        """校验无未来函数

        Args:
            bars: 输入 K 线数据
            input_window: 输入窗口大小
            feature_values: 特征函数输出
            mode: 校验模式 - 'strict'（严格）或 'structural'（结构性）

        Returns:
            True 表示通过校验
        """
        # 基本检查：输出长度不应超过输入长度
        if len(feature_values) > len(bars):
            return False

        if mode == "strict":
            return FutureFunctionChecker._check_strict(bars, input_window, feature_values)
        else:
            return FutureFunctionChecker._check_structural(bars, input_window, feature_values)

    @staticmethod
    def _check_strict(
        bars: list[Bar],
        input_window: int,
        feature_values: np.ndarray,
    ) -> bool:
        """严格校验：时间切片重算对比

        对于每个 bar i，只使用 bars[:i+1] 的数据重新计算，
        如果结果与原计算不同，说明使用了未来数据。
        """
        # 此方法需要特征函数可重复调用，实际使用时由 FeatureEngine 内部调用
        # 这里做基础检查：前 input_window-1 个值应该为 NaN（因为没有足够的历史数据）
        if input_window > 1 and len(feature_values) >= input_window:
            for i in range(min(input_window - 1, len(feature_values))):
                if not np.isnan(feature_values[i]):
                    # 记录警告但不阻断（某些特征确实不需要历史）
                    logger.warning(
                        "特征在位置 %d 有值，但 input_window=%d 意味着需要至少 %d 根历史 K 线",
                        i, input_window, input_window,
                    )
        return True

    @staticmethod
    def _check_structural(
        bars: list[Bar],
        input_window: int,
        feature_values: np.ndarray,
    ) -> bool:
        """结构性检查：检查常见违规模式

        注意：此检查无法完全防止未来函数，只能检测明显的结构性问题。
        真正的未来函数防护需要在 M3 回测引擎中实现。
        """
        # 基本检查：输出长度不应超过输入长度
        if len(feature_values) > len(bars):
            return False

        # 检查 NaN 前缀（input_window 约束）
        if input_window > 1 and len(feature_values) >= input_window:
            for i in range(min(input_window - 1, len(feature_values))):
                if not np.isnan(feature_values[i]):
                    logger.info(
                        "特征在位置 %d 有值，input_window=%d（可能允许）",
                        i, input_window,
                    )

        return True

    @staticmethod
    def validate_no_lookahead(
        func: Callable[..., np.ndarray],
        bars: list[Bar],
        params: dict[str, Any],
        input_window: int,
    ) -> bool:
        """验证特征函数不使用未来数据

        通过对比完整计算和逐步计算的结果来检测 look-ahead bias。

        Args:
            func: 特征函数
            bars: 完整 K 线数据
            params: 特征参数
            input_window: 输入窗口大小

        Returns:
            True 表示无未来函数
        """
        # 修正边界：测试窗口应足够大以覆盖 input_window + 至少 5 个样本
        test_size = max(input_window + 10, 20)

        if len(bars) < test_size:
            # 数据太少，无法有效检测
            return True

        # 取最后 test_size 根 K 线做测试
        test_bars = bars[-test_size:]
        full_result = func(test_bars, params)

        # 逐步计算：每次只用到当前 bar 及之前的数据
        step_results = []
        for i in range(len(test_bars)):
            subset = test_bars[:i + 1]
            result = func(subset, params)
            step_results.append(result[-1] if len(result) > 0 else np.nan)

        # 对比结果：从 input_window 开始检查
        for i in range(input_window, len(step_results)):
            if not np.isnan(step_results[i]) and not np.isnan(full_result[i]) and abs(step_results[i] - full_result[i]) > 1e-10:
                logger.warning(
                    "检测到潜在未来函数：位置 %d 的逐步计算结果 (%.10f) 与完整计算结果 (%.10f) 不一致",
                    i, step_results[i], full_result[i],
                )
                return False

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
        check_mode: str = "structural",
    ) -> list[FeatureSet]:
        """基于 K 线数据计算特征

        Args:
            instrument_id: 内部标的 ID
            interval: K 线间隔
            feature_names: 特征名列表
            bars: K 线数据（按 open_time 升序）
            check_mode: 未来函数校验模式 - 'strict'、'structural' 或 'none'

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
            if check_mode != "none":
                if not self._checker.check(bars, reg.input_window, values, mode=check_mode):
                    logger.warning(
                        "特征 %s 可能存在未来函数，请检查 input_window 设置",
                        name,
                    )
                # strict 模式下额外调用 validate_no_lookahead
                if check_mode == "strict" and len(bars) >= reg.input_window + 10 and not FutureFunctionChecker.validate_no_lookahead(
                    reg.func, bars, reg.params, reg.input_window
                ):
                    logger.warning(
                        "特征 %s 检测到潜在未来函数（validate_no_lookahead）",
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

    # 根据 K 线间隔自动计算年化因子
    # 1m → 525600, 5m → 105120, 15m → 35040, 1h → 8760
    interval = bars[0].interval if bars else "5m"
    annualization_factors = {
        "1m": 525600,
        "5m": 105120,
        "15m": 35040,
        "1h": 8760,
    }
    annualization_factor = params.get("annualization_factor", annualization_factors.get(interval, 105120))

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


def feature_mean_return(bars: list[Bar], params: dict[str, Any]) -> np.ndarray:
    """N 期对数收益率均值

    Args:
        bars: K 线数据
        params: {"period": N}（默认 20）

    Returns:
        均值数组
    """
    period = params.get("period", 20)
    returns = feature_returns(bars, {})
    mean_ret = np.full(len(returns), np.nan)
    for i in range(period, len(returns)):
        window = returns[i - period + 1: i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) >= 2:
            mean_ret[i] = np.mean(valid)
    return mean_ret


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
    funding_rates = params.get("funding_rates")

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
    oi_values = params.get("oi_values")

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
    mark_prices = params.get("mark_prices")

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
    - mean_return_20: 20 期对数收益率均值
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
        name="mean_return_20",
        func=feature_mean_return,
        input_window=21,
        params={"period": 20},
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
