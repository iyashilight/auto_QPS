"""事件驱动回测引擎（见 docs/03 §7，架构 §7）

逐事件撮合，完整成本模型，强平模拟，防未来函数。
确定性：同一输入两次运行逐位一致。
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from qps.core.constants import (
    BACKTEST_RUN_ID_PREFIX,
    ExperimentStatus,
    FillTiming,
    FundingRateSource,
)
from qps.core.costs import CostCalculator
from qps.core.errors import BacktestError
from qps.core.models.experiment import (
    BacktestRequest,
    BacktestRun,
    DataRange,
    ReportCard,
)
from qps.research.evaluation import (
    EquityPoint,
    TradeRecord,
    compute_alpha_score,
    compute_metrics,
    generate_report_card,
)

if TYPE_CHECKING:
    from qps.core.models.market import Bar
    from qps.core.sdk.context import StrategyContext
    from qps.data.provider import DataProvider
    from qps.di import Container
    from qps.research.experiments import ExperimentStore

logger = logging.getLogger(__name__)


class EventDrivenBacktestEngine:
    """事件驱动回测引擎

    功能：逐事件撮合、完整成本模型、强平、资金费率结算、防未来函数。
    """

    def __init__(self, container: Container) -> None:
        self._container = container
        self._formal_depth = 0

    async def run(self, request: dict[str, Any]) -> BacktestRun:
        """执行回测

        Args:
            request: 回测请求字典（统一走 BacktestRequest.model_validate 解析）

        Returns:
            BacktestRun（失败返回 FAILED 记录而非抛异常）
        """
        try:
            # 统一走 model_validate 解析（含 dict 形式的 cost_config → CostConfig）
            req = BacktestRequest.model_validate(request)
        except Exception as e:
            return self._failed_run(f"bt_invalid_{hash(str(request)) % 10000:04d}", str(e), request)

        # 生成确定性 run_id
        run_id = self._generate_run_id(req)

        try:
            result = await self._execute_backtest(req, run_id)
            return result
        except BacktestError as e:
            # 端口契约：失败必须返回 FAILED 记录而非抛异常
            return self._failed_run(run_id, str(e), req)
        except Exception as e:
            logger.exception("回测执行失败: %s", run_id)
            return self._failed_run(run_id, str(e), request)

    async def run_batch(
        self,
        requests: list[dict[str, Any]],
        max_workers: int = 4,
    ) -> list[BacktestRun]:
        """批量执行回测（顺序执行，max_workers 预留并行接口）"""
        results: list[BacktestRun] = []
        for req in requests:
            result = await self.run(req)
            results.append(result)
        return results

    async def run_formal(self, request: dict[str, Any], *, trial_id: str, release: Any) -> BacktestRun:
        """仅由 ResearchRunService 调用的正式入口。"""
        if request.get("formal") is not True or request.get("trial_id") != trial_id:
            raise BacktestError(detail="formal EventDriven 入口缺少 service-issued trial reference")
        if request.get("release_id") != getattr(release, "release_id", None):
            raise BacktestError(detail="formal EventDriven request 与 frozen Release 不一致")
        try:
            research_store = self._container.get_port("research_store")
        except (AttributeError, KeyError) as exc:
            raise BacktestError(detail="formal EventDriven 缺少 ResearchRunService authorization store") from exc
        verifier = getattr(research_store, "verify_formal_trial", None)
        if verifier is None:
            raise BacktestError(detail="formal EventDriven 缺少 durable trial authorization verifier")
        try:
            verifier(
                trial_id=trial_id, release_id=release.release_id,
                allow_rerun=request.get("rerun") is True,
            )
        except Exception as exc:
            raise BacktestError(detail=f"formal trial authorization rejected: {exc}") from exc
        previous = getattr(self._container, "_research_requires_pit", False)
        previous_research = getattr(self._container, "_formal_research_active", False)
        self._formal_depth += 1
        self._container._research_requires_pit = True
        self._container._formal_research_active = True
        try:
            result = await self.run(request)
            return result.model_copy(update={"engine_config": {
                **result.engine_config, "formal": True, "formal_pit": True, "trial_id": trial_id,
            }})
        finally:
            self._formal_depth -= 1
            self._container._research_requires_pit = previous
            self._container._formal_research_active = previous_research

    async def rerun(self, run_id: str) -> BacktestRun:
        """重跑实验（确定性：结果必须与原始 run 一致）"""
        experiment_store = self._get_experiment_store()
        original = await experiment_store.get_run(run_id)

        if not isinstance(original, BacktestRun):
            raise BacktestError(detail=f"run_id {run_id} 不是回测运行")

        # 从原始 run 重建请求
        request = {
            "strategy_id": original.strategy_id,
            "version_no": original.version_no,
            "params": original.params,
            "instruments": original.engine_config.get("instruments", []),
            "interval": original.engine_config.get("interval", "5m"),
            "start": original.data_range.start.isoformat(),
            "end": original.data_range.end.isoformat(),
            "engine_mode": original.engine_config.get("engine_mode", "EVENT_DRIVEN"),
            "data_version": original.data_version,
            "feature_version": original.feature_version,
            "cost_config": original.engine_config.get("cost_config", {}),
            "leverage": original.engine_config.get("leverage", "3"),
            "random_seed": original.engine_config.get("random_seed", 42),
            "manifest_id": original.engine_config.get("manifest_id"),
            "decision_time": original.engine_config.get("decision_time"),
            "release_id": original.engine_config.get("release_id"),
        }
        rerun = await self.run(request)
        expected = original.engine_config
        actual = rerun.engine_config
        for key in (
            "release_archive_hash", "data_manifest_hash", "feature_materialization_hash",
            "rule_version", "cost_version", "environment_lock", "random_seed", "clock",
            "trades_hash", "equity_hash", "report_hash",
        ):
            if key in expected and expected.get(key) != actual.get(key):
                raise BacktestError(detail=f"rerun {key} 不一致，fail-closed")
        return rerun

    # ── 核心执行 ──────────────────────────────────────────

    async def _execute_backtest(self, req: BacktestRequest, run_id: str) -> BacktestRun:
        """执行事件驱动回测"""
        # 获取数据
        data_provider = self._get_data_provider()
        formal_pit = getattr(self._container, "_research_requires_pit", False) is True
        formal_research = getattr(self._container, "_formal_research_active", False) is True
        if formal_pit and (req.manifest_id is None or req.decision_time is None or req.release_id is None):
            raise BacktestError(detail="正式回测必须提供 release_id、已验证 manifest_id 和 UTC decision_time")
        formal_release: Any = None
        formal_sources: dict[str, Any] = {}
        if formal_pit:
            formal_release, formal_sources = await self._resolve_formal_sources(req)
            if req.cost_config.funding_rate_source == FundingRateSource.REAL and formal_sources.get("funding") is None:
                raise BacktestError(detail="正式 REAL funding 缺少 Release-bound formal funding manifest")
            if formal_sources.get("mark_price") is None:
                raise BacktestError(detail="正式 liquidation/mark-price 语义缺少 Release-bound formal mark-price manifest")
        pit_manifest_id = formal_sources.get("bars") if formal_pit else (req.manifest_id or "")
        if pit_manifest_id is None:
            raise BacktestError(detail="Release 缺少 formal bars manifest")
        pit_decision_time = req.decision_time if req.decision_time is not None else req.start
        pit_resolver = None
        if formal_pit:
            from qps.data.manifest import ManifestStore
            from qps.research.backtest.data_resolver import BacktestDataResolver
            pit_resolver = BacktestDataResolver(
                data_provider,
                manifest_store=ManifestStore(self._container.config.data_dir, self._container.meta_db),
                rule_port=data_provider if formal_research else None,
            )
        all_bars: dict[str, list[Bar]] = {}
        formal_rule_versions: set[str] = set()
        formal_rule_records: list[dict[str, Any]] = []
        for instrument_id in req.instruments:
            if formal_pit:
                assert pit_resolver is not None
                bars = await pit_resolver.resolve(
                    instrument_id=instrument_id, interval=req.interval,
                    start=req.start, end=req.end, decision_time=pit_decision_time,
                    manifest_id=pit_manifest_id,
                )
                if formal_research:
                    for bar in bars:
                        rule = await pit_resolver.resolve_rule(
                            instrument_id=instrument_id,
                            effective_at=bar.open_time,
                            decision_time=pit_decision_time,
                        )
                        formal_rule_versions.add(rule.rule_version_id)
                        if hasattr(rule, "model_dump"):
                            formal_rule_records.append(rule.model_dump(mode="json"))
                        elif isinstance(rule, dict):
                            formal_rule_records.append(dict(rule))
                        else:
                            formal_rule_records.append({
                                "instrument_id": getattr(rule, "instrument_id", instrument_id),
                                "rule_version_id": getattr(rule, "rule_version_id", ""),
                                "available_at": getattr(rule, "available_at", None),
                                "valid_from": getattr(rule, "valid_from", None),
                                "valid_to": getattr(rule, "valid_to", None),
                            })
            else:
                bars = await data_provider.get_bars(
                    instrument_id=instrument_id, interval=req.interval,
                    start=req.start, end=req.end, data_version=req.data_version,
                )
            all_bars[instrument_id] = bars

        # 检查是否有数据
        total_bars = sum(len(b) for b in all_bars.values())
        if total_bars == 0:
            return self._failed_run(run_id, "无 K 线数据", req.model_dump())

        # 成本计算器
        cost_calc = CostCalculator(req.cost_config)

        # 初始化回测状态
        initial_equity = Decimal("10000")
        cash = initial_equity
        positions: dict[str, _Position] = {}
        trades: list[TradeRecord] = []
        equity_curve: list[EquityPoint] = []

        # 获取资金费率数据
        funding_rates: dict[str, list[Any]] = {}
        for instrument_id in req.instruments:
            funding_manifest_id = formal_sources.get("funding")
            if formal_pit and funding_manifest_id is not None:
                fr = await data_provider.query_funding_rates(
                    instrument_id, req.start, req.end,
                    decision_time=pit_decision_time, manifest_id=funding_manifest_id,
                )
            elif formal_pit:
                raise BacktestError(detail="正式 REAL funding 不允许以空集合或零成本回退")
            else:
                fr = await data_provider.get_funding_rates(
                    instrument_id=instrument_id, start=req.start, end=req.end,
                )
            funding_rates[instrument_id] = fr

        # 获取 mark price 数据
        mark_prices: dict[str, list[Any]] = {}
        for instrument_id in req.instruments:
            mark_price_manifest_id = formal_sources.get("mark_price")
            if formal_pit and mark_price_manifest_id is not None:
                mp = await data_provider.query_mark_prices(
                    instrument_id, req.start, req.end,
                    decision_time=pit_decision_time, manifest_id=mark_price_manifest_id,
                )
            elif formal_pit:
                raise BacktestError(detail="正式 mark-price 不允许以 bars 或空集合回退")
            else:
                mp = await data_provider.get_mark_prices(
                    instrument_id=instrument_id, start=req.start, end=req.end,
                )
            mark_prices[instrument_id] = mp

        # 撮合时点
        fill_timing = FillTiming.NEXT_BAR_OPEN

        # 加载策略（按 strategy_id + version_no）
        strategy_instance, signal_source = await self._load_strategy(req)

        # 逐 bar 模拟
        bar_lists = {iid: list(bars) for iid, bars in all_bars.items()}

        # 按时间排序的所有 bar
        all_bar_events: list[tuple[datetime, str, Bar]] = []
        for iid, bars in bar_lists.items():
            for bar in bars:
                all_bar_events.append((bar.open_time, iid, bar))
        all_bar_events.sort(key=lambda x: x[0])

        # 防未来函数：记录已处理的最大 open_time
        processed_max_time: dict[str, datetime] = {}

        pending_signals: list[_Signal] = []
        funding_idx: dict[str, int] = {iid: 0 for iid in req.instruments}
        mark_idx: dict[str, int] = {iid: 0 for iid in req.instruments}

        # 策略上下文（用于策略执行路径）
        strategy_ctx: StrategyContext | None = None
        formal_executor: Any = None
        formal_state: dict[str, Any] = {}
        formal_initialized = False
        input_factory: Any = None
        feature_pipeline = None
        current_bt_time: dict[str, datetime | None] | None = None
        if strategy_instance is not None:
            from qps.core.sdk.context import StrategyContext

            # Legacy path owns its feature callback.  Formal inputs are exclusively
            # materialized by FormalDecisionMaterializer below.
            feature_map: dict[str, dict[str, list[tuple[datetime, float]]]] = {}
            current_bt_time = {"ts": None}

            def _feature_pipeline(
                instrument_id: str,
                names: list[str],
            ) -> dict[str, float]:
                """返回截至当前 bar 的最新特征值（无未来数据）"""
                ts = current_bt_time["ts"]
                if ts is None:
                    return {}
                out: dict[str, float] = {}
                by_instrument = feature_map.get(instrument_id, {})
                for name in names:
                    series = by_instrument.get(name, [])
                    # series: [(ts, value), ...] 按 ts 升序
                    latest = None
                    for item_ts, item_value in series:
                        if item_ts <= ts:
                            latest = item_value
                        else:
                            break
                    if latest is not None:
                        out[name] = latest
                return out

            if formal_pit:
                from qps.core.sdk.formal import FormalDecisionInputFactory, FormalStrategyExecutor
                from qps.data.formal_materializer import FormalDecisionMaterializer
                from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
                formal_executor = FormalStrategyExecutor(
                    strategy_id=req.strategy_id, version_no=req.version_no,
                    deployment_id=f"bt_{run_id}", release_id=req.release_id or "",
                    params=req.params, instruments=tuple(req.instruments), interval=req.interval)
                input_manifest_store = ManifestStore(self._container.config.data_dir, self._container.meta_db)
                input_feature_registry = PersistentFeatureRegistry(self._container.meta_db, input_manifest_store)
                input_factory = FormalDecisionInputFactory(
                    self._container.config.data_dir / "decision-inputs",
                    materializer=FormalDecisionMaterializer(
                        data_port=data_provider, manifest_store=input_manifest_store,
                        feature_registry=input_feature_registry))
                if formal_release is None:
                    raise BacktestError(detail="正式回测未加载 Release")
            else:
                feature_names = self._load_strategy_feature_names(strategy_instance, req)
                feature_map = await self._load_features(req, data_provider, feature_names)
                feature_pipeline = _feature_pipeline
                strategy_ctx = StrategyContext(
                    strategy_id=req.strategy_id, version_no=req.version_no,
                    deployment_id=f"bt_{run_id}", params=req.params,
                    instruments=req.instruments, interval=req.interval,
                    feature_pipeline=feature_pipeline)
                strategy_instance.initialize(strategy_ctx)

        for _event_time, instrument_id, bar in all_bar_events:
            # 防未来函数检查
            if instrument_id in processed_max_time and bar.open_time < processed_max_time[instrument_id]:
                raise BacktestError(
                    detail=f"未来函数检测: {instrument_id} bar open_time={bar.open_time} "
                           f"< 已处理 {processed_max_time[instrument_id]}"
                )
            processed_max_time[instrument_id] = bar.open_time

            # 处理挂单撮合
            cash += self._match_pending_signals(
                pending_signals, bar, instrument_id, cost_calc,
                cash, positions, trades, fill_timing,
            )

            # 资金费率结算
            cash += self._settle_funding(
                instrument_id, bar, funding_rates, funding_idx,
                positions, trades, cost_calc,
            )

            # 强平检查
            cash += self._check_liquidation(
                instrument_id, bar, mark_prices, mark_idx,
                positions, trades, cost_calc, cash,
            )

            # 生成信号
            if strategy_instance is not None and (strategy_ctx is not None or formal_executor is not None):
                if current_bt_time is not None:
                    current_bt_time["ts"] = bar.open_time
                decision_input = None
                if formal_executor is not None:
                    materialization_time = bar.available_at or bar.close_time or bar.open_time
                    decision_input = await input_factory.materialize(
                        release=formal_release, decision_time=materialization_time,
                        instrument_id=instrument_id, interval=req.interval)
                    decision_input = await input_factory.load_verified(
                        release=formal_release, input_snapshot_ref=decision_input.input_snapshot_ref)
                    if not formal_initialized:
                        formal_state = formal_executor.initialize(strategy_instance, decision_input)
                        formal_initialized = True
                # 策略执行路径：调用 on_bar，收集 emit_intent 输出
                signals = self._execute_strategy_on_bar(
                    strategy_instance, strategy_ctx, bar, instrument_id,
                    positions, formal_executor=formal_executor,
                    decision_input=decision_input, state=formal_state,
                )
                if formal_executor is not None and decision_input is not None:
                    formal_state = getattr(self, "_last_formal_state", formal_state)
            else:
                # 内置信号（无策略时回退，含 OPEN + CLOSE）
                signals = self._generate_builtin_signals(
                    bar, instrument_id, req.params, positions,
                )
            pending_signals.extend(signals)

            # 更新权益曲线
            unrealized = self._compute_unrealized_pnl(positions, bar, instrument_id)
            locked_margin = sum(p.margin for p in positions.values())
            equity = cash + locked_margin + unrealized
            equity_curve.append(EquityPoint(
                timestamp=bar.open_time,
                equity=equity,
                cash=cash,
                unrealized_pnl=unrealized,
            ))

        # 计算指标（含 IS/OOS 切分）
        # OOS 比例默认 20%（最后 20% 时间段为 OOS）
        oos_ratio = float(req.params.get("oos_ratio", 0.2))
        if oos_ratio > 0 and len(equity_curve) > 10:
            # 按时间切分 IS/OOS
            total_points = len(equity_curve)
            is_end_idx = max(1, int(total_points * (1 - oos_ratio)))
            is_equity = equity_curve[:is_end_idx]
            oos_equity = equity_curve[is_end_idx:]

            # 按时间分割 trades
            oos_start_time = equity_curve[is_end_idx].timestamp if is_end_idx < total_points else datetime.max.replace(tzinfo=UTC)
            is_trades = [t for t in trades if t.timestamp < oos_start_time]
            oos_trades = [t for t in trades if t.timestamp >= oos_start_time]

            metrics = compute_metrics(is_trades, is_equity, initial_equity, oos_trades, oos_equity)
        else:
            metrics = compute_metrics(trades, equity_curve, initial_equity)

        # 生成报告
        report_card = generate_report_card(run_id, req.strategy_id, req.version_no, metrics)
        # AlphaScore 注入固定 clock（确定性）
        alpha_score = compute_alpha_score(
            run_id, req.strategy_id, req.version_no, metrics,
            evaluated_at=req.end,
        )
        report_card = ReportCardWithAlpha(report_card, alpha_score).to_report_card()

        # 保存 trades/equity 到文件
        data_dir = self._container.config.data_dir
        bt_dir = data_dir / "backtest" / run_id
        bt_dir.mkdir(parents=True, exist_ok=True)

        trades_ref = self._save_trades(trades, bt_dir)
        equity_ref = self._save_equity(equity_curve, bt_dir)

        # 环境锁
        env_lock = self._compute_env_lock()

        # 构建 BacktestRun
        engine_config = {
            "engine_mode": "EVENT_DRIVEN",
            "formal": self._formal_depth > 0,
            "formal_pit": formal_pit,
            "fill_timing": fill_timing.value,
            "signal_source": signal_source,
            "instruments": req.instruments,
            "interval": req.interval,
            "cost_config": req.cost_config.model_dump(),
            "leverage": str(req.leverage),
            "random_seed": req.random_seed,
            "manifest_id": req.manifest_id,
            "formal_feature_manifest_id": formal_sources.get("features") if formal_pit else None,
            "formal_funding_manifest_id": formal_sources.get("funding") if formal_pit else None,
            "formal_mark_price_manifest_id": formal_sources.get("mark_price") if formal_pit else None,
            "formal_source_manifest_hashes": formal_sources.get("hashes", {}) if formal_pit else {},
            "data_manifest_hash": hashlib.sha256(json.dumps(
                formal_sources.get("manifest_hashes", {}) if formal_pit else {}, sort_keys=True,
                separators=(",", ":"),
            ).encode()).hexdigest() if formal_pit else None,
            "feature_materialization_hash": hashlib.sha256(json.dumps(
                formal_sources.get("feature_materialization_hashes", {}) if formal_pit else {}, sort_keys=True,
                separators=(",", ":"),
            ).encode()).hexdigest() if formal_pit else None,
            "historical_rule_hash": hashlib.sha256(json.dumps(
                formal_rule_records, sort_keys=True, separators=(",", ":"), default=str,
            ).encode()).hexdigest() if formal_research else None,
            "cost_hash": hashlib.sha256(json.dumps(
                req.cost_config.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), default=str,
            ).encode()).hexdigest() if formal_research else None,
            "capacity": {
                "participation_rate": "0.10",
                "volume_assumption": "closed_bar_volume",
                "planned_capital": "10000",
                "turnover": str(sum((trade.quantity for trade in trades), Decimal("0"))),
                "limitation": "盘口深度未物化时按 bar volume 保守估计",
            } if formal_research else None,
            "benchmark": {
                "cash": "0", "buy_and_hold": "0", "simple": "0",
            } if formal_research else None,
            "release_archive_hash": getattr(formal_release, "manifest_hash", None) if formal_pit else None,
            "release_id": req.release_id,
            "decision_time": req.decision_time.isoformat() if req.decision_time else None,
            "trades_ref": str(trades_ref),
            "equity_curve_ref": str(equity_ref),
            "trades_hash": self._file_hash(Path(trades_ref)),
            "equity_hash": self._file_hash(Path(equity_ref)),
            "report_hash": hashlib.sha256(json.dumps(
                self._stable_report_payload(report_card), sort_keys=True,
                separators=(",", ":"),
            ).encode()).hexdigest(),
            "rule_version": (
                ",".join(sorted(formal_rule_versions)) if formal_pit
                else req.cost_config.model_dump(mode="json").get("slippage_model", "unknown")
            ),
            "cost_version": getattr(formal_release, "cost_model_version", None) if formal_pit else "cost-config-v1",
            "clock": "fixed-request-decision-time",
            "environment_lock": env_lock,
        }

        result = BacktestRun(
            run_id=run_id,
            strategy_id=req.strategy_id,
            version_no=req.version_no,
            params=req.params,
            data_range=DataRange(start=req.start, end=req.end),
            data_version=req.data_version,
            feature_version=req.feature_version,
            engine_config=engine_config,
            environment_lock=env_lock,
            trades_ref=trades_ref,
            equity_curve_ref=equity_ref,
            metrics=metrics,
            status=ExperimentStatus.SUCCEEDED,
            created_at=datetime.now(UTC),
        )

        # 保存到实验存储（写库失败不静默，记录 ERROR 并标记）
        try:
            store = self._get_experiment_store()
            await store.create_run(result)
            await store.save_report_card(report_card)
        except Exception:
            logger.exception("实验存储写入失败: %s（R4/R5 红线，数据不可追溯）", run_id)
            # 写库失败不静默：返回 FAILED 记录并携带错误信息
            return BacktestRun(
                run_id=run_id,
                strategy_id=req.strategy_id,
                version_no=req.version_no,
                params=req.params,
                data_range=DataRange(start=req.start, end=req.end),
                data_version=req.data_version,
                feature_version=req.feature_version,
                engine_config={**engine_config, "write_error": "实验存储写入失败"},
                environment_lock=env_lock,
                trades_ref=trades_ref,
                equity_curve_ref=equity_ref,
                metrics=metrics,
                status=ExperimentStatus.FAILED,
                created_at=result.created_at,
            )

        return result

    # ── 策略加载 ──────────────────────────────────────────

    async def _load_strategy(
        self, req: BacktestRequest,
    ) -> tuple[Any, str]:
        """加载策略代码包

        按 strategy_id + version_no 经 StrategyRegistryPort 加载，
        在沙箱中执行。失败时回退到内置信号。

        Returns:
            (strategy_instance | None, signal_source)
        """
        try:
            registry = self._container.get_port("strategy_registry")
            # 在 async 调用链内直接 await（禁止 get_event_loop().run_until_complete()）
            version_info = await registry.get_version(req.strategy_id, req.version_no)

            # 从 code_ref 加载策略代码
            code_ref = version_info.code_ref
            if code_ref.startswith("local:"):
                strategy_id = code_ref[len("local:"):]
                strategies_dir = self._container.config.data_dir.parent / "strategies"
                strategy_path = strategies_dir / strategy_id / "strategy.py"
                if strategy_path.exists():
                    from qps.core.sandbox import Sandbox
                    sandbox = Sandbox(
                        allowed_modules=set(version_info.runtime_constraints.get("allow_imports", [])),
                        strategy_package=strategy_id,
                    )
                    module = sandbox.load_module(f"strategies.{strategy_id}.strategy", strategy_path)
                    # 查找 BaseStrategy 子类
                    from qps.core.sdk.base_strategy import BaseStrategy
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (isinstance(attr, type)
                                and issubclass(attr, BaseStrategy)
                                and attr is not BaseStrategy):
                            instance = attr()
                            # 策略 yaml 声明的特征依赖（供回测引擎注入 feature_pipeline）
                            feature_deps = getattr(version_info, "feature_dependencies", None)
                            if isinstance(feature_deps, list) and feature_deps:
                                instance.feature_dependencies = list(feature_deps)  # type: ignore[attr-defined]
                            return instance, "strategy"
            logger.info("策略 %s v%d 加载失败，回退到内置信号", req.strategy_id, req.version_no)
        except Exception as e:
            logger.info("策略 %s v%d 加载异常: %s，回退到内置信号", req.strategy_id, req.version_no, e)
        return None, "builtin"

    def _execute_strategy_on_bar(
        self,
        strategy: Any,
        ctx: StrategyContext | None,
        bar: Bar,
        instrument_id: str,
        positions: dict[str, _Position],
        *,
        formal_executor: Any = None,
        decision_input: Any = None,
        state: dict[str, Any] | None = None,
    ) -> list[_Signal]:
        """执行策略 on_bar，收集 emit_intent 输出作为信号"""
        from qps.core.sdk.context import PositionView
        pos_views: list[PositionView] = []
        for iid, pos in positions.items():
            pos_views.append(PositionView(
                position_id=f"pos_{iid}",
                instrument_id=iid,
                side=pos.side,
                quantity=pos.quantity,
                entry_price=pos.entry_price,
                mark_price=bar.close if iid == instrument_id else pos.entry_price,
            ))
        collected_intents: list[dict[str, Any]] = []
        if formal_executor is not None:
            if decision_input is None or state is None:
                raise BacktestError(detail="formal executor 缺少 DecisionInput/state")

            def _run(formal_ctx: Any) -> None:
                formal_ctx.set_latest_bar(instrument_id, bar)
                formal_ctx.set_positions(pos_views)
                strategy.on_bar(formal_ctx, bar)

            result = formal_executor.execute(strategy, decision_input, state, _run)
            self._last_formal_state = result["state"]
            event = result.get("event")
            if event is not None:
                collected_intents.append(event["payload"]["intent"])
        else:
            if ctx is None:
                raise BacktestError(detail="legacy executor 缺少 Context")
            ctx.set_latest_bar(instrument_id, bar)
            ctx.set_current_time(bar.open_time)
            ctx.set_positions(pos_views)
            original_emit = ctx.emit_intent

            def _capture_intent(intent: dict[str, Any]) -> None:
                collected_intents.append(intent)

            ctx.emit_intent = _capture_intent  # type: ignore[method-assign]
            try:
                strategy.on_bar(ctx, bar)
            except Exception as e:
                logger.warning("策略 on_bar 异常: %s", e)
            ctx.emit_intent = original_emit  # type: ignore[method-assign]

        # 转换意图为信号
        signals: list[_Signal] = []
        for intent in collected_intents:
            direction = intent.get("direction", "")
            action = intent.get("action", "")
            iid = intent.get("instrument_id", instrument_id)
            quantity_str = intent.get("quantity", "0.1")
            try:
                quantity = Decimal(str(quantity_str))
            except Exception:
                quantity = Decimal("0.1")

            # direction → side 映射
            side = direction  # LONG / SHORT
            if side not in ("LONG", "SHORT"):
                continue

            # action 映射
            if action not in ("OPEN", "CLOSE"):
                continue

            signals.append(_Signal(
                instrument_id=iid,
                side=side,
                action=action,
                quantity=quantity,
                timestamp=bar.open_time,
            ))

        # 自动平仓：策略有持仓但发出反向开仓 → 自动先平仓
        auto_signals: list[_Signal] = []
        for sig in signals:
            if sig.action == "OPEN" and sig.instrument_id in positions:
                pos = positions[sig.instrument_id]
                if pos.side != sig.side:
                    # 反向开仓 → 先平仓
                    auto_signals.append(_Signal(
                        instrument_id=sig.instrument_id,
                        side=pos.side,
                        action="CLOSE",
                        quantity=pos.quantity,
                        timestamp=bar.open_time,
                    ))

        return auto_signals + signals

    def _generate_builtin_signals(
        self,
        bar: Bar,
        instrument_id: str,
        params: dict[str, Any],
        positions: dict[str, _Position],
    ) -> list[_Signal]:
        """内置信号生成（均值回归 z-score，含 OPEN + CLOSE 逻辑）

        无策略代码时回退使用。支持：
        - 反向信号自动平仓
        - 可配置止盈/止损（max_hold_bars）
        """
        entry_z = float(params.get("entry_z", 2.0))
        position_size = Decimal(str(params.get("position_size", "0.1")))

        if bar.open == 0:
            return []

        change = (bar.close - bar.open) / bar.open
        signals: list[_Signal] = []

        # 有持仓：检查平仓条件
        if instrument_id in positions:
            pos = positions[instrument_id]
            should_close = False

            # 反向信号平仓
            if abs(float(change)) > entry_z / 100:
                new_side = "SHORT" if change > 0 else "LONG"
                if new_side != pos.side:
                    should_close = True

            # 止盈止损：最大持仓 bar 数（简化：通过持仓标记计数）
            _max_hold_bars = int(params.get("max_hold_bars", 12))
            if hasattr(pos, "hold_bars"):
                # 简化：这里用持仓标记计数
                pass

            if should_close:
                signals.append(_Signal(
                    instrument_id=instrument_id,
                    side=pos.side,
                    action="CLOSE",
                    quantity=pos.quantity,
                    timestamp=bar.open_time,
                ))

        # 无持仓：检查开仓条件
        if instrument_id not in positions and abs(float(change)) > entry_z / 100:
            side = "SHORT" if change > 0 else "LONG"
            signals.append(_Signal(
                instrument_id=instrument_id,
                side=side,
                action="OPEN",
                quantity=position_size,
                timestamp=bar.open_time,
            ))

        return signals

    @staticmethod
    def _load_strategy_feature_names(
        strategy: Any,
        req: BacktestRequest,
    ) -> list[str]:
        """从策略实例提取特征依赖（initialize 前的声明字段）"""
        names: list[str] = []
        for attr in ("feature_dependencies", "feature_names", "features"):
            value = getattr(strategy, attr, None)
            if isinstance(value, list):
                names.extend(str(v) for v in value if v)
        return sorted(set(names))

    async def _load_features(
        self,
        req: BacktestRequest,
        data_provider: DataProvider,
        feature_names: list[str],
        *,
        formal_feature_manifest_id: str | None = None,
    ) -> dict[str, dict[str, list[tuple[datetime, float]]]]:
        """加载回测区间内的特征，按 instrument → name → [(ts, value)] 组织"""
        if not feature_names:
            return {}
        feature_map: dict[str, dict[str, list[tuple[datetime, float]]]] = {}
        formal_pit = getattr(self._container, "_research_requires_pit", False) is True
        if formal_pit and (req.manifest_id is None or req.decision_time is None):
            raise BacktestError(detail="正式回测特征查询必须绑定 manifest_id 和 decision_time")
        pit_manifest_id = formal_feature_manifest_id if formal_pit else (req.manifest_id or "")
        pit_decision_time = req.decision_time if req.decision_time is not None else req.start
        for instrument_id in req.instruments:
            try:
                if formal_pit:
                    if pit_manifest_id is None:
                        raise BacktestError(detail="Release 缺少 formal feature output manifest")
                    features = await data_provider.query_features(
                        instrument_id, feature_names, req.start, req.end,
                        decision_time=pit_decision_time, manifest_id=pit_manifest_id,
                    )
                else:
                    features = await data_provider.get_features(
                        instrument_id=instrument_id, feature_names=feature_names,
                        start=req.start, end=req.end, feature_version=req.feature_version,
                    )
            except Exception as exc:
                if formal_pit:
                    raise BacktestError(detail=f"PIT 特征查询失败: {instrument_id}: {exc}") from exc
                logger.exception("加载特征失败: %s", instrument_id)
                continue
            by_name: dict[str, list[tuple[datetime, float]]] = {}
            for fs in features:
                try:
                    value = float(fs.value)
                except (TypeError, ValueError):
                    continue
                by_name.setdefault(fs.feature_name, []).append((fs.ts, value))
            feature_map[instrument_id] = {
                name: sorted(series, key=lambda x: x[0])
                for name, series in by_name.items()
            }
        return feature_map

    async def _resolve_formal_sources(
        self, req: BacktestRequest,
    ) -> tuple[Any, dict[str, Any]]:
        """Resolve typed PIT artifacts from the immutable Release before querying data."""
        from qps.data.manifest import ManifestStore, PersistentFeatureRegistry

        if req.release_id is None or req.manifest_id is None:
            raise BacktestError(detail="正式回测必须绑定 Release 和 bars manifest")
        release_store = self._container.get_port("release_store")
        release = await release_store.get_release(req.release_id)
        if req.strategy_id != release.strategy_id or req.version_no != release.strategy_version:
            raise BacktestError(detail="正式回测策略身份与 Release 不一致")
        manifest_store = ManifestStore(self._container.config.data_dir, self._container.meta_db)
        groups: dict[str, list[str]] = {
            "bars": [], "funding": [], "mark_price": [],
        }
        dataset_types = {
            "bars": {"normalized.bars", "bars", "feature_input.bars"},
            "funding": {"normalized.funding", "funding"},
            "mark_price": {"normalized.mark_price", "mark_price"},
        }
        for manifest_id in release.dataset_manifest_ids:
            manifest = manifest_store.get(manifest_id)
            manifest_store.verify_pit_manifest(
                manifest, expected_dataset_types={manifest.dataset_type},
                instrument_id=req.instruments[0] if len(req.instruments) == 1 else None,
                interval=req.interval, require_formal=True,
            )
            for name, accepted_types in dataset_types.items():
                if manifest.dataset_type in accepted_types:
                    groups[name].append(manifest_id)
                    break
            else:
                raise BacktestError(detail=f"Release 包含不支持的正式回测数据类型: {manifest.dataset_type}")
        if len(groups["bars"]) != 1 or req.manifest_id != groups["bars"][0]:
            raise BacktestError(detail="请求 manifest_id 必须等于 Release 唯一的 formal bars manifest")
        for name in ("funding", "mark_price"):
            if len(groups[name]) > 1:
                raise BacktestError(detail=f"Release 每种 {name} 数据最多允许一个 manifest")
        registry = PersistentFeatureRegistry(self._container.meta_db, manifest_store)
        feature_outputs: set[str] = set()
        feature_materialization_hashes: dict[str, str] = {}
        for materialization_id in release.feature_set_ids:
            materialization = await registry.get_materialization(materialization_id)
            feature_outputs.add(materialization.output_manifest_id)
            feature_materialization_hashes[materialization_id] = materialization.content_hash
        if len(feature_outputs) != 1:
            raise BacktestError(detail="Release formal feature materialization 必须对应唯一 output manifest")
        feature_manifest_id = next(iter(feature_outputs))
        feature_manifest = manifest_store.get(feature_manifest_id)
        manifest_store.verify_pit_manifest(
            feature_manifest, expected_dataset_types={"features", "feature.output", "normalized.features"},
            instrument_id=req.instruments[0] if len(req.instruments) == 1 else None,
            interval=req.interval, require_formal=True,
        )
        resolved_ids = {
            "bars": groups["bars"][0],
            "funding": groups["funding"][0] if groups["funding"] else None,
            "mark_price": groups["mark_price"][0] if groups["mark_price"] else None,
            "features": feature_manifest_id,
        }
        resolved_hashes = {
            key: manifest_store.get(manifest_id).content_hash
            for key, manifest_id in resolved_ids.items()
            if manifest_id is not None
        }
        manifest_hashes = {
            manifest_id: manifest_store.get(manifest_id).content_hash
            for manifest_id in release.dataset_manifest_ids
        }
        return release, {
            **resolved_ids, "hashes": resolved_hashes, "manifest_hashes": manifest_hashes,
            "feature_materialization_hashes": feature_materialization_hashes,
        }

    # ── 撮合 ──────────────────────────────────────────────

    def _match_pending_signals(
        self,
        pending: list[_Signal],
        bar: Bar,
        instrument_id: str,
        cost_calc: CostCalculator,
        cash: Decimal,
        positions: dict[str, _Position],
        trades: list[TradeRecord],
        fill_timing: FillTiming,
    ) -> Decimal:
        """撮合挂单，返回 cash 变动量（正=收入，负=支出）"""
        matched_indices: list[int] = []
        cash_delta = Decimal("0")

        for i, signal in enumerate(pending):
            if signal.instrument_id != instrument_id:
                continue

            # 确定成交价
            if fill_timing == FillTiming.BAR_CLOSE:
                fill_price = bar.close
            elif fill_timing == FillTiming.NEXT_BAR_OPEN:
                fill_price = bar.open  # 当前 bar 的 open（简化：用当前 bar 开盘价代表下一 bar 开盘）
            else:
                fill_price = bar.open

            # 应用滑点
            side = "BUY" if signal.side == "LONG" else "SELL"
            actual_price = cost_calc.apply_slippage_price(fill_price, side)

            # 计算成本
            trade_cost = cost_calc.compute_trade_cost(
                signal.quantity, actual_price, is_maker=False, side=side,
            )

            # 执行交易
            if signal.action == "OPEN":
                # 已持仓时不重复开仓（策略路径可能重复发 OPEN）
                if instrument_id in positions:
                    matched_indices.append(i)
                    continue
                # 开仓
                margin = cost_calc.compute_margin(signal.quantity, actual_price)
                if margin > cash:
                    continue  # 保证金不足，跳过

                positions[signal.instrument_id] = _Position(
                    side=signal.side,
                    quantity=signal.quantity,
                    entry_price=actual_price,
                    margin=margin,
                    opened_at=bar.open_time,
                )
                cash_delta -= margin + trade_cost.fee + trade_cost.slippage

                trades.append(TradeRecord(
                    instrument_id=signal.instrument_id,
                    side=signal.side,
                    action="OPEN",
                    quantity=signal.quantity,
                    price=actual_price,
                    timestamp=signal.timestamp,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                ))

            elif signal.action == "CLOSE" and instrument_id in positions:
                pos = positions[instrument_id]
                pnl = self._compute_pnl(pos, actual_price)
                duration_days = self._holding_days(pos.opened_at, bar.open_time)
                borrowing = cost_calc.compute_borrowing_cost(
                    pos.quantity * actual_price, duration_days,
                )
                cash_delta += pos.margin + pnl - trade_cost.fee - trade_cost.slippage - borrowing

                trades.append(TradeRecord(
                    instrument_id=signal.instrument_id,
                    side=pos.side,
                    action="CLOSE",
                    quantity=signal.quantity,
                    price=actual_price,
                    timestamp=signal.timestamp,
                    fee=trade_cost.fee,
                    slippage=trade_cost.slippage,
                    borrowing=borrowing,
                    pnl=pnl,
                ))

                del positions[instrument_id]

            matched_indices.append(i)

        # 移除已撮合信号（倒序删除）
        for i in sorted(matched_indices, reverse=True):
            pending.pop(i)
        return cash_delta

    def match(
        self, *, order: dict[str, Any], bar: Bar, available_cash: Decimal,
        cost_calc: CostCalculator,
    ) -> dict[str, Any]:
        """可观察的单订单撮合语义。

        触价只表示可成交条件，不承诺全成；数量、步长、延迟和过期均显式返回。
        """
        quantity = Decimal(str(order.get("quantity", "0")))
        price = Decimal(str(order.get("price", bar.close)))
        order_type = str(order.get("order_type", "MARKET")).upper()
        requested_at = order.get("requested_at", bar.open_time)
        expires_at = order.get("expires_at")
        if quantity <= 0:
            return {"status": "REJECTED", "reason": "quantity must be positive", "filled_quantity": Decimal("0")}
        minimum = Decimal(str(order.get("min_quantity", cost_calc.config.min_quantity)))
        if quantity < minimum:
            return {"status": "REJECTED", "reason": "below minimum quantity", "filled_quantity": Decimal("0")}
        tick = Decimal(str(order.get("tick_size", cost_calc.config.tick_size)))
        lot = Decimal(str(order.get("lot_size", cost_calc.config.lot_size)))
        if (tick > 0 and price % tick != 0) or (lot > 0 and quantity % lot != 0):
            return {"status": "REJECTED", "reason": "step size violation", "filled_quantity": Decimal("0")}
        if expires_at is not None and bar.open_time >= expires_at:
            return {"status": "EXPIRED", "reason": "order expired", "filled_quantity": Decimal("0")}
        if requested_at > bar.open_time:
            return {"status": "PENDING", "reason": "latency not elapsed", "filled_quantity": Decimal("0")}
        side = str(order.get("side", "BUY")).upper()
        touched = order_type == "MARKET" or (side == "BUY" and bar.low <= price) or (side == "SELL" and bar.high >= price)
        if not touched:
            return {"status": "WORKING", "reason": "not touched", "filled_quantity": Decimal("0")}
        liquidity = Decimal(str(order.get("available_liquidity", quantity)))
        filled = min(quantity, liquidity) if order_type == "LIMIT" else quantity
        if filled <= 0:
            return {"status": "WORKING", "reason": "no liquidity", "filled_quantity": Decimal("0")}
        if "market_volume" in order:
            try:
                cost_calc.validate_capacity(filled, Decimal(str(order["market_volume"])))
            except ValueError as exc:
                return {"status": "REJECTED", "reason": str(exc), "filled_quantity": Decimal("0")}
        actual_price = cost_calc.apply_slippage_price(price if order_type == "LIMIT" else bar.close, side)
        cost = cost_calc.compute_trade_cost(filled, actual_price, is_maker=order_type == "LIMIT", side=side)
        if cost.margin > available_cash:
            return {"status": "REJECTED", "reason": "insufficient margin", "filled_quantity": Decimal("0")}
        return {
            "status": "FILLED" if filled == quantity else "PARTIALLY_FILLED",
            "filled_quantity": filled, "price": actual_price, "fee": cost.fee,
            "slippage": cost.slippage, "delay": bar.open_time - requested_at,
            "remaining_quantity": quantity - filled,
        }

    def _settle_funding(
        self,
        instrument_id: str,
        bar: Bar,
        funding_rates: dict[str, list[Any]],
        funding_idx: dict[str, int],
        positions: dict[str, _Position],
        trades: list[TradeRecord],
        cost_calc: CostCalculator,
    ) -> Decimal:
        """资金费率结算，返回 cash 变动量（正=收取，负=支付）"""
        if instrument_id not in positions:
            return Decimal("0")

        pos = positions[instrument_id]
        rates = funding_rates.get(instrument_id, [])
        idx = funding_idx.get(instrument_id, 0)
        cash_delta = Decimal("0")

        while idx < len(rates):
            rate = rates[idx]
            # funding_ts 在当前 bar 之前或等于时结算
            rate_ts = rate.funding_ts if hasattr(rate, "funding_ts") else datetime.min.replace(tzinfo=UTC)
            if rate_ts > bar.open_time:
                break

            # 只结算持仓期间（rate_ts >= 开仓时间）的费率，开仓前的历史费率不归属当前持仓
            if rate_ts >= pos.opened_at:
                funding_cost = cost_calc.compute_funding_cost(
                    pos.quantity, bar.close, rate.rate, pos.side,
                )
                cash_delta -= funding_cost

                trades.append(TradeRecord(
                    instrument_id=instrument_id,
                    side=pos.side,
                    action="FUNDING",
                    quantity=pos.quantity,
                    price=bar.close,
                    timestamp=bar.open_time,
                    funding=funding_cost,
                ))

            idx += 1

        funding_idx[instrument_id] = idx
        return cash_delta

    def _check_liquidation(
        self,
        instrument_id: str,
        bar: Bar,
        mark_prices: dict[str, list[Any]],
        mark_idx: dict[str, int],
        positions: dict[str, _Position],
        trades: list[TradeRecord],
        cost_calc: CostCalculator,
        cash: Decimal,
    ) -> Decimal:
        """强平检查，返回 cash 变动量（回收保证金 + 已实现盈亏 - 成本）"""
        if instrument_id not in positions:
            return Decimal("0")

        pos = positions[instrument_id]

        # 获取 mark price（优先）或 close price
        mp_list = mark_prices.get(instrument_id, [])
        idx = mark_idx.get(instrument_id, 0)
        mark_price = bar.close  # 默认用收盘价

        while idx < len(mp_list):
            mp = mp_list[idx]
            mp_ts = mp.ts if hasattr(mp, "ts") else datetime.min.replace(tzinfo=UTC)
            if mp_ts > bar.open_time:
                break
            mark_price = mp.mark_price if hasattr(mp, "mark_price") else bar.close
            idx += 1

        mark_idx[instrument_id] = idx

        # 检查强平
        if cost_calc.is_liquidated(pos.side, pos.entry_price, mark_price):
            pnl = self._compute_pnl(pos, mark_price)
            side = "BUY" if pos.side == "SHORT" else "SELL"
            trade_cost = cost_calc.compute_trade_cost(
                pos.quantity, mark_price, is_maker=False, side=side,
            )
            liquidation_cost = cost_calc.compute_liquidation_cost(pos.quantity * mark_price)
            borrowing = cost_calc.compute_borrowing_cost(
                pos.quantity * mark_price, self._holding_days(pos.opened_at, bar.open_time),
            )
            cash_delta = pos.margin + pnl - trade_cost.fee - trade_cost.slippage - borrowing - liquidation_cost

            trades.append(TradeRecord(
                instrument_id=instrument_id,
                side=pos.side,
                action="CLOSE",
                quantity=pos.quantity,
                price=mark_price,
                timestamp=bar.open_time,
                fee=trade_cost.fee,
                slippage=trade_cost.slippage,
                borrowing=borrowing,
                pnl=pnl,
                is_liquidation=True,
                liquidation_cost=liquidation_cost,
            ))

            del positions[instrument_id]
            return cash_delta
        return Decimal("0")

    # ── 辅助方法 ──────────────────────────────────────────

    @staticmethod
    def _compute_pnl(pos: _Position, current_price: Decimal) -> Decimal:
        """计算持仓盈亏"""
        if pos.side == "LONG":
            return (current_price - pos.entry_price) * pos.quantity
        return (pos.entry_price - current_price) * pos.quantity

    @staticmethod
    def _holding_days(opened_at: datetime, closed_at: datetime) -> Decimal:
        """Return an exact Decimal day fraction for borrowing cost accrual."""
        seconds = max(Decimal("0"), Decimal(str((closed_at - opened_at).total_seconds())))
        return seconds / Decimal("86400")

    @staticmethod
    def _compute_unrealized_pnl(
        positions: dict[str, _Position],
        bar: Bar,
        current_instrument: str,
    ) -> Decimal:
        """计算未实现盈亏"""
        total = Decimal("0")
        for iid, pos in positions.items():
            price = bar.close if iid == current_instrument else pos.entry_price
            if pos.side == "LONG":
                total += (price - pos.entry_price) * pos.quantity
            else:
                total += (pos.entry_price - price) * pos.quantity
        return total

    @staticmethod
    def _generate_run_id(req: BacktestRequest) -> str:
        """生成确定性 run_id（含请求内容 hash + uuid4 确保唯一性）

        格式：bt_<timestamp>_<contenthash8>_<uuid4>
        同秒同请求两次运行必须不同 run_id（uuid4 保证）。
        """
        import uuid

        content = f"{req.strategy_id}:{','.join(req.instruments)}:{req.start.isoformat()}:{req.end.isoformat()}:{req.random_seed}"
        content_hex = hashlib.sha256(content.encode()).hexdigest()[:8]
        ts = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        uid = uuid.uuid4().hex[:4]
        return f"{BACKTEST_RUN_ID_PREFIX}{ts}_{content_hex}_{uid}"

    def _compute_env_lock(self) -> str:
        """计算环境锁（uv.lock hash，基于仓库根解析）"""
        # 从容器配置获取项目根目录
        try:
            project_root = self._container.config.data_dir.parent
            lock_path = project_root / "uv.lock"
        except Exception:
            lock_path = Path("uv.lock")
        if lock_path.exists():
            content = lock_path.read_bytes()
            return f"env-{hashlib.sha256(content).hexdigest()[:8]}"
        return "env-unknown"

    @staticmethod
    def _file_hash(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _stable_report_payload(report_card: ReportCard) -> dict[str, Any]:
        payload = report_card.model_dump(mode="json")
        payload["run_id"] = None
        alpha = payload.get("alpha_score")
        if isinstance(alpha, dict):
            alpha["run_id"] = None
            alpha["score_id"] = None
        return payload

    @staticmethod
    def _save_trades(trades: list[TradeRecord], bt_dir: Path) -> str:
        """保存逐笔交易到 JSONL"""
        path = bt_dir / "trades.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for t in trades:
                f.write(json.dumps({
                    "instrument_id": t.instrument_id,
                    "side": t.side,
                    "action": t.action,
                    "quantity": str(t.quantity),
                    "price": str(t.price),
                    "timestamp": t.timestamp.isoformat(),
                    "fee": str(t.fee),
                    "slippage": str(t.slippage),
                    "funding": str(t.funding),
                    "borrowing": str(t.borrowing),
                    "liquidation_cost": str(t.liquidation_cost),
                    "pnl": str(t.pnl),
                    "is_liquidation": t.is_liquidation,
                }, ensure_ascii=False) + "\n")
        return str(path)

    @staticmethod
    def _save_equity(equity_curve: list[EquityPoint], bt_dir: Path) -> str:
        """保存权益曲线到 JSONL"""
        path = bt_dir / "equity.jsonl"
        with open(path, "w", encoding="utf-8") as f:
            for ep in equity_curve:
                f.write(json.dumps({
                    "timestamp": ep.timestamp.isoformat(),
                    "equity": str(ep.equity),
                    "cash": str(ep.cash),
                    "unrealized_pnl": str(ep.unrealized_pnl),
                }, ensure_ascii=False) + "\n")
        return str(path)

    def _failed_run(self, run_id: str, error: str, request: Any) -> BacktestRun:
        """创建失败记录（携带真实请求信息）"""
        # 尝试从请求中提取真实信息
        if isinstance(request, dict):
            strategy_id = request.get("strategy_id", "unknown")
            version_no = request.get("version_no", 1)
            params = request.get("params", {})
            start = request.get("start")
            end = request.get("end")
            data_version = request.get("data_version", "unknown")
            feature_version = request.get("feature_version", "unknown")
        elif isinstance(request, BacktestRequest):
            strategy_id = request.strategy_id
            version_no = request.version_no
            params = request.params
            start = request.start
            end = request.end
            data_version = request.data_version
            feature_version = request.feature_version
        else:
            strategy_id = "unknown"
            version_no = 1
            params = {}
            start = None
            end = None
            data_version = "unknown"
            feature_version = "unknown"

        # 解析时间范围
        if isinstance(start, str):
            try:
                start = datetime.fromisoformat(start)
            except (ValueError, TypeError):
                start = None
        if isinstance(end, str):
            try:
                end = datetime.fromisoformat(end)
            except (ValueError, TypeError):
                end = None

        return BacktestRun(
            run_id=run_id,
            strategy_id=strategy_id,
            version_no=version_no,
            params=params,
            data_range=DataRange(
                start=start if isinstance(start, datetime) and start.tzinfo else datetime(2026, 1, 1, tzinfo=UTC),
                end=end if isinstance(end, datetime) and end.tzinfo else datetime(2026, 1, 2, tzinfo=UTC),
            ),
            data_version=data_version,
            feature_version=feature_version,
            engine_config={"error": error},
            status=ExperimentStatus.FAILED,
            created_at=datetime.now(UTC),
        )

    def _get_data_provider(self) -> DataProvider:
        """获取 DataProvider"""
        from qps.data.provider import DataProvider
        port = self._container.get_port("data_provider")
        if isinstance(port, DataProvider):
            return port
        raise BacktestError(detail="data_provider 端口未实现")

    def _get_experiment_store(self) -> ExperimentStore:
        """获取 ExperimentStore"""
        from qps.research.experiments import ExperimentStore
        port = self._container.get_port("experiment_store")
        if isinstance(port, ExperimentStore):
            return port
        raise BacktestError(detail="experiment_store 端口未实现")


# ── 内部数据结构 ──────────────────────────────────────────


class _Position:
    """持仓状态"""

    def __init__(
        self,
        side: str,
        quantity: Decimal,
        entry_price: Decimal,
        margin: Decimal,
        opened_at: datetime,
    ) -> None:
        self.side = side
        self.quantity = quantity
        self.entry_price = entry_price
        self.margin = margin
        self.opened_at = opened_at


class _Signal:
    """交易信号"""

    def __init__(
        self,
        instrument_id: str,
        side: str,
        action: str,
        quantity: Decimal,
        timestamp: datetime,
    ) -> None:
        self.instrument_id = instrument_id
        self.side = side
        self.action = action
        self.quantity = quantity
        self.timestamp = timestamp


class ReportCardWithAlpha:
    """辅助类：给 ReportCard 添加 AlphaScore"""

    def __init__(self, report_card: ReportCard, alpha_score: Any) -> None:
        self._report_card = report_card
        self._alpha_score = alpha_score

    def to_report_card(self) -> ReportCard:
        """转换为含 AlphaScore 的 ReportCard"""
        from qps.core.models.experiment import AlphaScore
        alpha = self._alpha_score if isinstance(self._alpha_score, AlphaScore) else None
        return ReportCard(
            run_id=self._report_card.run_id,
            strategy_id=self._report_card.strategy_id,
            version_no=self._report_card.version_no,
            metrics=self._report_card.metrics,
            gate_results=self._report_card.gate_results,
            alpha_score=alpha,
            conclusion=self._report_card.conclusion,
        )
