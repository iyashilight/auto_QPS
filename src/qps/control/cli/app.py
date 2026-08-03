"""CLI 命令入口（见 06 文档第 4 节）

命令树：qps → data/research/experiment/strategy/trade/risk/task/broker
M1 阶段实现 data 子命令（collect/verify/status）。
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import typer

if TYPE_CHECKING:
    from pathlib import Path

app = typer.Typer(
    name="qps",
    help="autoQps — 个人加密货币中频量化交易系统",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# 注册子命令组
data_app = typer.Typer(name="data", help="市场数据管理", no_args_is_help=True)
research_app = typer.Typer(name="research", help="策略研究", no_args_is_help=True)
experiment_app = typer.Typer(name="experiment", help="实验管理", no_args_is_help=True)
strategy_app = typer.Typer(name="strategy", help="策略管理", no_args_is_help=True)
trade_app = typer.Typer(name="trade", help="交易管理", no_args_is_help=True)
risk_app = typer.Typer(name="risk", help="风控管理", no_args_is_help=True)
task_app = typer.Typer(name="task", help="任务管理", no_args_is_help=True)
broker_app = typer.Typer(name="broker", help="Broker 状态", no_args_is_help=True)

app.add_typer(data_app)
app.add_typer(research_app)
app.add_typer(experiment_app)
app.add_typer(strategy_app)
app.add_typer(trade_app)
app.add_typer(risk_app)
app.add_typer(task_app)
app.add_typer(broker_app)


def _not_implemented(name: str) -> None:
    """统一未实现提示"""
    typer.echo(f"⚠️  命令 '{name}' 尚未实现，将在后续里程碑中完成。", err=True)
    raise typer.Exit(code=1)


def _get_data_dir() -> Path:
    """获取数据目录"""
    from qps.infra.config import Config

    config = Config()
    return config.data_dir


def _make_instrument_id(instrument: str) -> str:
    """将用户输入的标的符号转为内部 ID

    BTC-USDT-PERP → binance-btc-usdt-perp
    """
    if "-" in instrument and not instrument.startswith("binance-"):
        return f"binance-{instrument.lower()}"
    return instrument.lower()


# ── data 子命令 ──────────────────────────────────────────


@data_app.command("collect")
def data_collect(
    instrument: str = typer.Argument(help="标的符号，如 BTC-USDT-PERP"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    start: str | None = typer.Option(None, "--start", help="开始日期 (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, "--end", help="结束日期 (YYYY-MM-DD)"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
    with_funding: bool = typer.Option(False, "--with-funding", help="同时采集资金费率"),
    with_oi: bool = typer.Option(False, "--with-oi", help="同时采集持仓量"),
    mode: str = typer.Option("merge", "--mode", "-m", help="写入模式: merge(合并去重,默认)/overwrite(覆盖)/skip(跳过已有)"),
) -> None:
    """历史数据采集（异步任务，进度可查）"""
    from qps.broker.exchange_adapter.binance_exchange_adapter import BinanceExchangeAdapter
    from qps.data.adapters.binance_market_adapter import BinanceMarketAdapter
    from qps.data.pipeline.normalizer import Normalizer, RawWriter

    # 解析日期
    if start is None:
        typer.echo("❌ 必须指定 --start 日期", err=True)
        raise typer.Exit(code=2)
    if end is None:
        typer.echo("❌ 必须指定 --end 日期", err=True)
        raise typer.Exit(code=2)

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        typer.echo(f"❌ 日期格式错误: {e}", err=True)
        raise typer.Exit(code=2) from e

    # 验证日期范围
    if start_dt >= end_dt:
        typer.echo("❌ start 必须早于 end", err=True)
        raise typer.Exit(code=2)

    instrument_id = _make_instrument_id(instrument)

    # 验证 K 线间隔
    valid_intervals = ("1m", "5m", "15m", "1h")
    if interval not in valid_intervals:
        typer.echo(f"❌ 不支持的 K 线间隔: {interval}，支持: {valid_intervals}", err=True)
        raise typer.Exit(code=2)

    async def _collect() -> None:
        # 创建适配器
        exchange_adapter = BinanceExchangeAdapter()
        market_adapter = BinanceMarketAdapter(exchange_adapter=exchange_adapter)

        try:
            await market_adapter.connect()

            # 采集历史 K 线
            typer.echo(f"📊 采集 {instrument} {interval} K 线 [{start}, {end})...")
            bars = await market_adapter.fetch_historical_bars(
                instrument_id, interval, start_dt, end_dt
            )

            if not bars:
                typer.echo("⚠️  未采集到数据", err=True)
                return

            # Raw 落盘
            data_dir = _get_data_dir()
            raw_writer = RawWriter(raw_dir=data_dir / "raw", source="binance")
            symbol = instrument.lower().replace("-", "")
            raw_files = raw_writer.write_bars(symbol, bars, mode=mode)

            # Normalized
            normalizer = Normalizer(normalized_dir=data_dir / "normalized")
            normalized, report = normalizer.normalize_bars(bars, instrument_id, interval)
            norm_path = normalizer.write_normalized_bars(normalized, instrument_id, interval, mode=mode)

            # 采集资金费率
            funding_count = 0
            if with_funding:
                typer.echo(f"💰 采集 {instrument} 资金费率...")
                funding_rates = await market_adapter.fetch_funding_rates(instrument_id, start_dt, end_dt)
                if funding_rates:
                    raw_files_funding = raw_writer.write_funding_rates(symbol, funding_rates, mode=mode)
                    funding_count = len(funding_rates)
                    typer.echo(f"   资金费率 Raw: {len(raw_files_funding)} 个文件")

                    # Normalized
                    normalizer = Normalizer(normalized_dir=data_dir / "normalized")
                    normalized_funding, _ = normalizer.normalize_funding_rates(funding_rates, instrument_id)
                    norm_path_funding = normalizer.write_normalized_funding_rates(normalized_funding, instrument_id, mode=mode)
                    typer.echo(f"   资金费率 Normalized: {norm_path_funding}")

            # 采集持仓量
            oi_count = 0
            oi_actual_start: datetime | None = None
            oi_actual_end: datetime | None = None
            if with_oi:
                typer.echo(f"📈 采集 {instrument} 历史持仓量...")
                oi_history = await market_adapter.fetch_historical_open_interest(instrument_id, start_dt, end_dt)
                if oi_history:
                    raw_files_oi = raw_writer.write_open_interest(symbol, oi_history, mode=mode)
                    oi_count = len(oi_history)
                    typer.echo(f"   持仓量 Raw: {len(raw_files_oi)} 个文件")

                    # Normalized
                    normalizer = Normalizer(normalized_dir=data_dir / "normalized")
                    normalized_oi, _ = normalizer.normalize_open_interest(oi_history, instrument_id)
                    norm_path_oi = normalizer.write_normalized_open_interest(normalized_oi, instrument_id, mode=mode)
                    typer.echo(f"   持仓量 Normalized: {norm_path_oi}")

                    # 记录实际覆盖范围
                    if oi_history:
                        sorted_oi = sorted(oi_history, key=lambda o: o.ts)
                        oi_actual_start = sorted_oi[0].ts
                        oi_actual_end = sorted_oi[-1].ts

            # 输出结果
            if json_output:
                result = {
                    "instrument": instrument,
                    "instrument_id": instrument_id,
                    "interval": interval,
                    "start": start,
                    "end": end,
                    "bars_collected": len(bars),
                    "bars_normalized": len(normalized),
                    "quality": report.overall_quality.value,
                    "quality_report": report.to_dict(),
                    "raw_files": [str(f) for f in raw_files],
                    "normalized_file": str(norm_path) if norm_path else None,
                    "funding_rates_collected": funding_count,
                    "oi_collected": oi_count,
                }
                if with_funding and funding_count > 0:
                    result["funding_normalized"] = str(norm_path_funding) if norm_path_funding else None
                if with_oi and oi_count > 0:
                    result["oi_normalized"] = str(norm_path_oi) if norm_path_oi else None
                    result["oi_actual_start"] = oi_actual_start.isoformat() if oi_actual_start else None
                    result["oi_actual_end"] = oi_actual_end.isoformat() if oi_actual_end else None
                typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                typer.echo(f"✅ 采集完成: {len(bars)} 根 K 线")
                typer.echo(f"   Raw 文件: {len(raw_files)} 个")
                typer.echo(f"   Normalized: {norm_path}")
                typer.echo(f"   数据质量: {report.overall_quality.value}")
                if report.gaps:
                    typer.echo(f"   缺口: {len(report.gaps)} 个")
                if report.suspect_bars > 0:
                    typer.echo(f"   可疑数据: {report.suspect_bars} 根")
                if funding_count > 0:
                    typer.echo(f"   资金费率: {funding_count} 条 (Raw + Normalized)")
                if oi_count > 0:
                    typer.echo(f"   持仓量: {oi_count} 条 (Raw + Normalized)")
                    if oi_actual_start and oi_actual_end:
                        typer.echo(f"   持仓量覆盖范围: {oi_actual_start.date()} ~ {oi_actual_end.date()}")

        except Exception as e:
            typer.echo(f"❌ 采集失败: {e}", err=True)
            raise typer.Exit(code=1) from e
        finally:
            await market_adapter.disconnect()

    asyncio.run(_collect())


@data_app.command("verify")
def data_verify(
    instrument: str = typer.Argument(help="标的符号"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    start: str | None = typer.Option(None, "--start", help="开始日期"),
    end: str | None = typer.Option(None, "--end", help="结束日期"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
    with_funding: bool = typer.Option(False, "--with-funding", help="同时检查资金费率"),
    with_oi: bool = typer.Option(False, "--with-oi", help="同时检查持仓量"),
    oi_period: str = typer.Option("5m", "--oi-period", help="持仓量数据间隔（默认 5m）"),
) -> None:
    """数据质量检查"""
    from qps.data.pipeline.normalizer import QualityChecker

    instrument_id = _make_instrument_id(instrument)
    data_dir = _get_data_dir()

    # 验证 K 线间隔
    valid_intervals = ("1m", "5m", "15m", "1h")
    if interval not in valid_intervals:
        typer.echo(f"❌ 不支持的 K 线间隔: {interval}，支持: {valid_intervals}", err=True)
        raise typer.Exit(code=2)

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC) if start else datetime(2020, 1, 1, tzinfo=UTC)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) if end else datetime.now(tz=UTC)
    except ValueError as e:
        typer.echo(f"❌ 日期格式错误: {e}", err=True)
        raise typer.Exit(code=2) from e

    checker = QualityChecker(normalized_dir=data_dir / "normalized")
    report = checker.check_bars(instrument_id, interval, start_dt, end_dt)

    # 可选：检查资金费率和持仓量
    funding_report: dict[str, Any] | None = None
    oi_report: dict[str, Any] | None = None

    if with_funding:
        funding_report = checker.check_funding_rates(instrument_id, start_dt, end_dt)

    if with_oi:
        oi_report = checker.check_open_interest(instrument_id, start_dt, end_dt, period=oi_period)

    if json_output:
        result = report.to_dict()
        if funding_report:
            result["funding"] = funding_report
        if oi_report:
            result["oi"] = oi_report
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo(f"📊 数据质量报告: {instrument} ({interval})")
        typer.echo(f"   总 K 线数: {report.total_bars}")
        typer.echo(f"   OK: {report.ok_bars}")
        typer.echo(f"   SUSPECT: {report.suspect_bars}")
        typer.echo(f"   缺口: {len(report.gaps)} 个")
        typer.echo(f"   乱序: {report.out_of_order} 条")
        typer.echo(f"   价格跳变: {len(report.price_jumps)} 处")
        typer.echo(f"   整体质量: {report.overall_quality.value}")

        if funding_report:
            typer.echo("\n💰 资金费率质量:")
            typer.echo(f"   预期数量: {funding_report['total_expected']}")
            typer.echo(f"   实际数量: {funding_report['total_actual']}")
            typer.echo(f"   缺失率: {funding_report['missing_rate']:.2%}")
            typer.echo(f"   可疑记录: {funding_report['suspect_count']}")

        if oi_report:
            typer.echo("\n📈 持仓量质量:")
            typer.echo(f"   预期数量: {oi_report['total_expected']}")
            typer.echo(f"   实际数量: {oi_report['total_actual']}")
            typer.echo(f"   缺失率: {oi_report['missing_rate']:.2%}")
            typer.echo(f"   可疑记录: {oi_report['suspect_count']}")


@data_app.command("compute")
def data_compute(
    instrument: str = typer.Argument(help="标的符号，如 BTC-USDT-PERP"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    start: str | None = typer.Option(None, "--start", help="开始日期 (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, "--end", help="结束日期 (YYYY-MM-DD)"),
    features: str = typer.Option("returns,momentum_10,atr_14,realized_vol_20,volume_change", "--features", "-f", help="特征列表（逗号分隔）"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
    check_mode: str = typer.Option("structural", "--check-mode", help="未来函数校验模式: structural/strict/none"),
) -> None:
    """计算特征数据"""
    from qps.broker.exchange_adapter.binance_exchange_adapter import BinanceExchangeAdapter
    from qps.data.adapters.binance_market_adapter import BinanceMarketAdapter
    from qps.data.features.engine import FeatureEngine, register_mvp_features
    from qps.data.pipeline.normalizer import Normalizer

    # 解析日期
    if start is None:
        typer.echo("❌ 必须指定 --start 日期", err=True)
        raise typer.Exit(code=2)
    if end is None:
        typer.echo("❌ 必须指定 --end 日期", err=True)
        raise typer.Exit(code=2)

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    except ValueError as e:
        typer.echo(f"❌ 日期格式错误: {e}", err=True)
        raise typer.Exit(code=2) from e

    if start_dt >= end_dt:
        typer.echo("❌ start 必须早于 end", err=True)
        raise typer.Exit(code=2)

    instrument_id = _make_instrument_id(instrument)

    # 验证 K 线间隔
    valid_intervals = ("1m", "5m", "15m", "1h")
    if interval not in valid_intervals:
        typer.echo(f"❌ 不支持的 K 线间隔: {interval}，支持: {valid_intervals}", err=True)
        raise typer.Exit(code=2)

    # 解析特征列表
    feature_names = [f.strip() for f in features.split(",")]

    async def _compute() -> None:
        # 创建适配器
        exchange_adapter = BinanceExchangeAdapter()
        market_adapter = BinanceMarketAdapter(exchange_adapter=exchange_adapter)

        try:
            await market_adapter.connect()

            # 采集历史 K 线
            typer.echo(f"📊 采集 {instrument} {interval} K 线 [{start}, {end})...")
            bars = await market_adapter.fetch_historical_bars(
                instrument_id, interval, start_dt, end_dt
            )

            if not bars:
                typer.echo("⚠️  未采集到数据", err=True)
                return

            # 规范化
            data_dir = _get_data_dir()
            normalizer = Normalizer(normalized_dir=data_dir / "normalized")
            normalized, report = normalizer.normalize_bars(bars, instrument_id, interval)

            # 计算特征
            typer.echo(f"🔬 计算特征: {', '.join(feature_names)}...")
            engine = FeatureEngine(
                features_dir=data_dir / "features",
                data_version="binance-default",
            )
            register_mvp_features(engine)

            features_result = engine.compute_with_bars(
                instrument_id, interval, feature_names, normalized, check_mode=check_mode
            )

            # 落盘特征
            if features_result:
                feature_version = engine.list_versions(feature_names[0])[-1] if feature_names else "unknown"
                feature_path = engine.write_features(features_result, feature_version, interval, instrument_id)

                if json_output:
                    result = {
                        "instrument": instrument,
                        "instrument_id": instrument_id,
                        "interval": interval,
                        "start": start,
                        "end": end,
                        "bars_used": len(normalized),
                        "features_computed": len(features_result),
                        "feature_version": feature_version,
                        "feature_file": str(feature_path) if feature_path else None,
                        "features": [
                            {
                                "ts": f.ts.isoformat(),
                                "name": f.feature_name,
                                "value": float(f.value),
                                "version": f.feature_version,
                            }
                            for f in features_result[:100]  # 限制输出数量
                        ],
                    }
                    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
                else:
                    typer.echo(f"✅ 特征计算完成: {len(features_result)} 个特征值")
                    typer.echo(f"   特征版本: {feature_version}")
                    typer.echo(f"   特征文件: {feature_path}")
                    typer.echo(f"   数据质量: {report.overall_quality.value}")
            else:
                typer.echo("⚠️  无特征计算结果")

        except Exception as e:
            typer.echo(f"❌ 特征计算失败: {e}", err=True)
            raise typer.Exit(code=1) from e
        finally:
            await market_adapter.disconnect()

    asyncio.run(_compute())


@data_app.command("status")
def data_status(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """数据覆盖状态"""
    data_dir = _get_data_dir()

    # 扫描 Normalized 数据目录
    normalized_dir = data_dir / "normalized"
    coverage: list[dict[str, Any]] = []

    if normalized_dir.exists():
        for interval_dir in sorted(normalized_dir.iterdir()):
            if not interval_dir.is_dir():
                continue
            interval = interval_dir.name
            for symbol_dir in sorted(interval_dir.iterdir()):
                if not symbol_dir.is_dir():
                    continue
                symbol = symbol_dir.name
                parquet_files = list(symbol_dir.glob("*.parquet"))
                years = sorted([f.stem for f in parquet_files])
                if years:
                    coverage.append({
                        "instrument": symbol,
                        "interval": interval,
                        "years": years,
                        "file_count": len(parquet_files),
                    })

    # 扫描 Raw 数据目录
    raw_dir = data_dir / "raw"
    raw_sources: list[str] = []
    if raw_dir.exists():
        raw_sources = sorted([d.name for d in raw_dir.iterdir() if d.is_dir()])

    # 扫描特征数据目录
    features_dir = data_dir / "features"
    feature_versions: list[str] = []
    if features_dir.exists():
        feature_versions = sorted([d.name for d in features_dir.iterdir() if d.is_dir()])

    if json_output:
        result = {
            "data_dir": str(data_dir),
            "normalized_coverage": coverage,
            "raw_sources": raw_sources,
            "feature_versions": feature_versions,
        }
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo("📊 数据覆盖状态")
        typer.echo(f"   数据目录: {data_dir}")
        typer.echo(f"   Raw 数据源: {', '.join(raw_sources) if raw_sources else '无'}")
        typer.echo(f"   特征版本: {', '.join(feature_versions) if feature_versions else '无'}")
        if coverage:
            typer.echo("   Normalized 数据:")
            for item in coverage:
                typer.echo(
                    f"     {item['instrument']} ({item['interval']}): "
                    f"{item['years'][0]}-{item['years'][-1]}, "
                    f"{item['file_count']} 个文件"
                )
        else:
            typer.echo("   Normalized 数据: 无")


# ── research 子命令 ──────────────────────────────────────


@research_app.command("backtest")
def research_backtest(
    strategy_id: str = typer.Argument(help="策略 ID"),
    version: int | None = typer.Option(None, "--version", "-v", help="策略版本"),
    params: str | None = typer.Option(None, "--params", help="参数 JSON"),
    mode: str = typer.Option("event_driven", "--mode", "-m", help="回测模式"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    range_str: str | None = typer.Option(None, "--range", help="时间范围 START:END"),
) -> None:
    """策略回测"""
    _not_implemented("research backtest")


@research_app.command("optimize")
def research_optimize(
    strategy_id: str = typer.Argument(help="策略 ID"),
    algorithm: str = typer.Option("grid", "--algorithm", "-a", help="搜索算法"),
    budget: int = typer.Option(1000, "--budget", "-b", help="搜索预算"),
    objective: str | None = typer.Option(None, "--objective", help="目标函数 JSON"),
) -> None:
    """参数优化"""
    _not_implemented("research optimize")


@research_app.command("list-runs")
def research_list_runs(
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="按策略过滤"),
    type_filter: str | None = typer.Option(None, "--type", "-t", help="按类型过滤"),
) -> None:
    """列出回测/优化运行记录"""
    _not_implemented("research list-runs")


# ── experiment 子命令 ────────────────────────────────────


@experiment_app.command("show")
def experiment_show(
    run_id: str = typer.Argument(help="实验运行 ID"),
) -> None:
    """查看实验详情"""
    _not_implemented("experiment show")


@experiment_app.command("compare")
def experiment_compare(
    run_ids: list[str] = typer.Argument(help="实验运行 ID 列表"),
) -> None:
    """对比多个实验"""
    _not_implemented("experiment compare")


@experiment_app.command("rerun")
def experiment_rerun(
    run_id: str = typer.Argument(help="实验运行 ID"),
) -> None:
    """重跑实验"""
    _not_implemented("experiment rerun")


# ── strategy 子命令 ──────────────────────────────────────


@strategy_app.command("create")
def strategy_create(
    strategy_id: str = typer.Argument(help="策略 ID"),
    template: str = typer.Option("trend", "--template", "-t", help="策略模板 (trend/mean_reversion/grid)"),
    name: str | None = typer.Option(None, "--name", help="显示名（可选）"),
    instruments: str | None = typer.Option(None, "--instruments", help="标的列表，逗号分隔（如 BTC-USDT-PERP,ETH-USDT-PERP）"),
) -> None:
    """创建策略（脚手架生成 + 入库）"""
    from qps.infra.config import Config
    from qps.research.registry import StrategyCodePack, StrategyRegistry
    from qps.research.scaffold import StrategyScaffold

    # 验证模板
    valid_templates = ("trend", "mean_reversion", "grid")
    if template not in valid_templates:
        typer.echo(f"❌ 无效模板: {template}，支持: {valid_templates}", err=True)
        raise typer.Exit(code=2)

    # 解析标的列表
    instrument_list = None
    if instruments:
        instrument_list = [inst.strip() for inst in instruments.split(",")]

    # 获取目录（仓库根 strategies/）
    config = Config()
    # 使用 data_dir 的父目录作为项目根目录
    strategies_dir = config.data_dir.parent / "strategies"

    # 生成脚手架
    scaffold = StrategyScaffold(strategies_dir)
    try:
        strategy_dir = scaffold.create(
            strategy_id=strategy_id,
            template=template,
            name=name,
            instruments=instrument_list,
        )
        typer.echo(f"✅ 策略创建成功: {strategy_id}")
        typer.echo(f"   目录: {strategy_dir}")
        typer.echo(f"   模板: {template}")

        # 读取生成文件并入库
        code_content = (strategy_dir / "strategy.py").read_text(encoding="utf-8")
        yaml_content = (strategy_dir / "strategy.yaml").read_text(encoding="utf-8")

        import yaml as yaml_lib
        yaml_data = yaml_lib.safe_load(yaml_content)

        code_pack = StrategyCodePack(
            strategy_id=strategy_id,
            code_ref=f"local:{strategy_id}",
            code_content=code_content,
            params_schema=yaml_data.get("params_schema", {}),
            data_dependencies=[],
            feature_dependencies=yaml_data.get("features", []),
            runtime_constraints=yaml_data.get("runtime_constraints", {}),
        )

        db_path = config.data_dir / "meta.db"
        registry = StrategyRegistry(db_path)
        version_info = asyncio.run(registry.save_version(code_pack))

        typer.echo(f"   版本号: {version_info['version_no']}")
        typer.echo(f"   内容哈希: {version_info['content_hash']}")
        typer.echo("")
        typer.echo("下一步：")
        typer.echo(f"   1. 编辑 {strategy_dir}/strategy.py 实现策略逻辑")
        typer.echo(f"   2. 运行测试: pytest {strategy_dir}/tests/test_smoke.py")
    except ValueError as e:
        typer.echo(f"❌ 创建失败: {e}", err=True)
        raise typer.Exit(code=1) from e


@strategy_app.command("list")
def strategy_list(
    status: str | None = typer.Option(None, "--status", "-s", help="按状态过滤"),
) -> None:
    """列出策略"""
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry

    config = Config()
    db_path = config.data_dir / "meta.db"

    if not db_path.exists():
        typer.echo("⚠️  元数据库不存在，请先运行数据采集", err=True)
        return

    registry = StrategyRegistry(db_path)

    try:
        strategies = asyncio.run(registry.list_strategies(status))

        if not strategies:
            typer.echo("📋 无策略记录")
            return

        typer.echo("📋 策略列表:")
        typer.echo("")
        for s in strategies:
            typer.echo(f"  • {s['strategy_id']}")
            typer.echo(f"    名称: {s['name']}")
            typer.echo(f"    风格: {s['style']}")
            typer.echo(f"    状态: {s['lifecycle_status']}")
            typer.echo(f"    来源: {s['source']}")
            typer.echo("")

    except Exception as e:
        typer.echo(f"❌ 查询失败: {e}", err=True)
        raise typer.Exit(code=1) from e


@strategy_app.command("status")
def strategy_status(
    strategy_id: str = typer.Argument(help="策略 ID"),
) -> None:
    """查看策略状态"""
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry

    config = Config()
    db_path = config.data_dir / "meta.db"

    if not db_path.exists():
        typer.echo("⚠️  元数据库不存在", err=True)
        return

    registry = StrategyRegistry(db_path)

    try:
        versions = asyncio.run(registry.list_versions(strategy_id))

        if not versions:
            typer.echo(f"📋 策略 {strategy_id} 无版本记录")
            return

        typer.echo(f"📋 策略 {strategy_id} 版本历史:")
        typer.echo("")
        for v in versions:
            typer.echo(f"  • 版本 {v['version_no']}")
            typer.echo(f"    代码引用: {v['code_ref']}")
            typer.echo(f"    内容哈希: {v['content_hash']}")
            typer.echo(f"    创建时间: {v['created_at']}")
            typer.echo("")

    except Exception as e:
        typer.echo(f"❌ 查询失败: {e}", err=True)
        raise typer.Exit(code=1) from e


@strategy_app.command("promote")
def strategy_promote(
    strategy_id: str = typer.Argument(help="策略 ID"),
    to_status: str = typer.Option(..., "--to", help="目标状态"),
    evidence: str | None = typer.Option(None, "--evidence", help="证据 ID 列表（逗号分隔）"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """策略状态提升（需门禁证据）

    注意：完整生命周期管理由 M4 LifecycleManagerPort 实现，当前为占位提示。
    """
    from qps.infra.config import Config

    config = Config()
    db_path = config.data_dir / "meta.db"

    if not db_path.exists():
        typer.echo("⚠️  元数据库不存在", err=True)
        return

    # 验证目标状态
    valid_statuses = (
        "DRAFT", "BACKTESTING", "VALIDATED", "PAPER_TRADING",
        "PRODUCTION", "PAUSED", "DISABLED", "DEPRECATED", "ARCHIVED"
    )
    if to_status not in valid_statuses:
        typer.echo(f"❌ 无效的目标状态: {to_status}", err=True)
        typer.echo(f"   支持的状态: {', '.join(valid_statuses)}", err=True)
        raise typer.Exit(code=2)

    # 解析证据列表
    evidence_refs = []
    if evidence:
        evidence_refs = [e.strip() for e in evidence.split(",")]

    # 确认提示
    if not confirm:
        typer.echo(f"⚠️  即将将策略 {strategy_id} 状态改为 {to_status}")
        if evidence_refs:
            typer.echo(f"   证据: {', '.join(evidence_refs)}")
        typer.echo("")
        typer.echo("请使用 --confirm 参数确认操作", err=True)
        raise typer.Exit(code=1)

    # 诚实的未实现提示
    typer.echo(f"ℹ️  策略 {strategy_id} 状态提升到 {to_status}")
    typer.echo("   ⚠️  完整生命周期管理由 M4 LifecycleManagerPort 实现（待开发）")
    typer.echo("   当前版本仅做占位提示，未实际执行状态迁移")


# ── trade 子命令 ─────────────────────────────────────────


@trade_app.command("status")
def trade_status() -> None:
    """交易系统状态"""
    _not_implemented("trade status")


@trade_app.command("positions")
def trade_positions(
    account: str | None = typer.Option(None, "--account", "-a", help="账户过滤"),
) -> None:
    """查看持仓"""
    _not_implemented("trade positions")


@trade_app.command("orders")
def trade_orders(
    status: str | None = typer.Option(None, "--status", "-s", help="订单状态过滤"),
    deployment: str | None = typer.Option(None, "--deployment", "-d", help="部署 ID 过滤"),
) -> None:
    """查看订单"""
    _not_implemented("trade orders")


@trade_app.command("reconcile")
def trade_reconcile(
    account: str | None = typer.Option(None, "--account", "-a", help="账户"),
) -> None:
    """对账"""
    _not_implemented("trade reconcile")


@trade_app.command("deploy")
def trade_deploy(
    strategy_id: str = typer.Argument(help="策略 ID"),
    mode: str = typer.Option("paper", "--mode", "-m", help="部署模式"),
    params: str | None = typer.Option(None, "--params", help="参数 JSON"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """部署策略"""
    _not_implemented("trade deploy")


# ── risk 子命令 ──────────────────────────────────────────


@risk_app.command("status")
def risk_status() -> None:
    """风控状态"""
    _not_implemented("risk status")


@risk_app.command("freeze")
def risk_freeze(
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="策略 ID"),
    all_strategies: bool = typer.Option(False, "--all", help="冻结所有策略"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """冻结策略/全局"""
    _not_implemented("risk freeze")


@risk_app.command("unfreeze")
def risk_unfreeze(
    target: str = typer.Argument(help="策略 ID 或 'all'"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """解冻策略/全局"""
    _not_implemented("risk unfreeze")


# ── task 子命令 ──────────────────────────────────────────


@task_app.command("list")
def task_list() -> None:
    """列出任务"""
    _not_implemented("task list")


@task_app.command("cancel")
def task_cancel(
    task_id: str = typer.Argument(help="任务 ID"),
) -> None:
    """取消任务"""
    _not_implemented("task cancel")


# ── broker 子命令 ────────────────────────────────────────


@broker_app.command("status")
def broker_status() -> None:
    """Broker 连接状态"""
    _not_implemented("broker status")


# ── 版本信息 ─────────────────────────────────────────────


def _version_callback(value: bool) -> None:
    if value:
        from qps import __version__

        typer.echo(f"autoQps v{__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", "-V", help="显示版本号", callback=_version_callback, is_eager=True
    ),
) -> None:
    """autoQps — 个人加密货币中频量化交易系统"""
