"""CLI 命令入口（见 06 文档第 4 节）

命令树：qps → data/research/experiment/strategy/trade/risk/task/broker
M1 阶段实现 data 子命令（collect/verify/status）。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
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
manifest_app = typer.Typer(name="manifest", help="不可变数据 manifest", no_args_is_help=True)
feature_app = typer.Typer(name="feature", help="持久化 Feature Registry", no_args_is_help=True)
research_app = typer.Typer(name="research", help="策略研究", no_args_is_help=True)
research_report_app = typer.Typer(name="report", help="正式 EvidenceReport", no_args_is_help=True)
experiment_app = typer.Typer(name="experiment", help="实验管理", no_args_is_help=True)
strategy_app = typer.Typer(name="strategy", help="策略管理", no_args_is_help=True)
trade_app = typer.Typer(name="trade", help="交易管理", no_args_is_help=True)
risk_app = typer.Typer(name="risk", help="风控管理", no_args_is_help=True)
task_app = typer.Typer(name="task", help="任务管理", no_args_is_help=True)
broker_app = typer.Typer(name="broker", help="Broker 状态", no_args_is_help=True)

app.add_typer(data_app)
data_app.add_typer(manifest_app)
data_app.add_typer(feature_app)
app.add_typer(research_app)
research_app.add_typer(research_report_app)
app.add_typer(experiment_app)
app.add_typer(strategy_app)
app.add_typer(trade_app)
app.add_typer(risk_app)
app.add_typer(task_app)
app.add_typer(broker_app)


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


def _parse_utc_iso(value: str, field: str) -> datetime:
    """Parse one explicit UTC ISO-8601 value without local-time coercion."""
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} 必须是 UTC ISO 8601 时间") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{field} 必须是带 +00:00 或 Z 的 UTC 时间")
    return parsed.astimezone(UTC)


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
            from qps.core.models.foundation import DatasetManifest, SourceReference
            from qps.data.manifest import ManifestStore
            from qps.infra.storage.meta_db import MetaDB

            manifest: DatasetManifest | None = None
            pit_ready = bool(normalized) and all(
                value is not None
                for bar in normalized
                for value in (bar.event_time, bar.available_at, bar.ingested_at, bar.close_time, bar.is_closed)
            )
            if pit_ready:
                meta_db = MetaDB(data_dir / "meta.db")
                meta_db.initialize()
                manifest_store = ManifestStore(data_dir, meta_db)
                candidate = manifest_store.build(
                    "normalized.bars", [str(norm_path)], [SourceReference(name="binance")],
                    schema_version="v0.5-pit-formal", persist=False,
                )
                quality_ref = manifest_store.write_quality_report(
                    candidate, report.to_dict(),
                )
                manifest = manifest_store.build_formal_pit(
                    "normalized.bars", [str(norm_path)], [SourceReference(name="binance")],
                    instrument_id=instrument_id, interval=interval, quality_report_ref=quality_ref,
                )
            else:
                typer.echo("⚠️  Binance 数据缺少源端 available_at，未创建正式 PIT manifest", err=True)

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
                    "manifest_id": manifest.manifest_id if manifest else None,
                    "manifest_content_hash": manifest.content_hash if manifest else None,
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
                if manifest:
                    typer.echo(f"   Manifest: {manifest.manifest_id}")
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
    manifest_id: str | None = typer.Option(None, "--manifest-id", help="按不可变 manifest 验证"),
) -> None:
    """数据质量检查"""
    if manifest_id is not None:
        from qps.core.errors import DataError

        _data_dir, manifests, _registry = _m1_services()
        try:
            start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC) if start else None
            end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC) if end else None
            if start_dt is not None and end_dt is not None and start_dt >= end_dt:
                raise DataError(detail="start 必须早于 end")
            manifest = manifests.verify_pit_manifest(
                manifest_id,
                expected_dataset_types={"normalized.bars", "bars", "feature_input.bars"},
                instrument_id=_make_instrument_id(instrument), interval=interval,
                start=start_dt, end=end_dt, require_time_coverage=True, require_formal=True,
            )
        except Exception as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        payload = {
            "manifest_id": manifest.manifest_id,
            "dataset_type": manifest.dataset_type,
            "content_hash": manifest.content_hash,
            "partition_refs": list(manifest.partition_refs),
            "instrument_id": _make_instrument_id(instrument),
            "interval": interval,
            "quality_report_ref": manifest.quality_report_ref,
            "verified": True,
        }
        typer.echo(json.dumps(payload, ensure_ascii=False) if json_output else f"verified {manifest.manifest_id}")
        return

    typer.echo("❌ 正式数据验证必须提供 --manifest-id；未提供时禁止目录扫描", err=True)
    raise typer.Exit(code=2)


@data_app.command("compute")
def data_compute(
    instrument: str = typer.Argument(help="标的符号，如 BTC-USDT-PERP"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    start: str | None = typer.Option(None, "--start", help="开始日期 (YYYY-MM-DD)"),
    end: str | None = typer.Option(None, "--end", help="结束日期 (YYYY-MM-DD)"),
    features: str = typer.Option("returns,momentum_10,atr_14,realized_vol_20,volume_change", "--features", "-f", help="特征列表（逗号分隔）"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
    check_mode: str = typer.Option("structural", "--check-mode", help="未来函数校验模式: structural/strict/none"),
    manifest_id: str | None = typer.Option(None, "--manifest-id", help="已验证的 PIT bars manifest"),
    decision_time: str | None = typer.Option(None, "--decision-time", help="UTC PIT 决策时点"),
) -> None:
    """从已验证 PIT manifest 计算并物化特征。"""
    from qps.data.features.engine import FeatureEngine, register_mvp_features

    # 解析日期
    if start is None:
        typer.echo("❌ 必须指定 --start 日期", err=True)
        raise typer.Exit(code=2)
    if end is None:
        typer.echo("❌ 必须指定 --end 日期", err=True)
        raise typer.Exit(code=2)
    if manifest_id is None or decision_time is None:
        typer.echo("❌ 正式特征计算必须指定 --manifest-id 和 --decision-time", err=True)
        raise typer.Exit(code=2)

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
        end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
        decision_dt = _parse_utc_iso(decision_time, "--decision-time")
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
        try:
            data_dir = _get_data_dir()
            data_dir, manifests, registry = _m1_services()
            from qps.data.provider import DataProvider

            provider = DataProvider(data_dir, allow_legacy=False)
            engine = FeatureEngine(
                features_dir=data_dir / "features",
                data_version=manifest_id,
                data_port=provider, registry_port=registry, manifest_store=manifests,
                allow_legacy_compute=False,
            )
            register_mvp_features(engine)
            materialization = await engine.materialize(
                instrument_id, interval, feature_names, start_dt, end_dt,
                decision_time=decision_dt, input_manifest_id=manifest_id,
            )
            result = {"materialization_id": materialization.materialization_id,
                      "output_manifest_id": materialization.output_manifest_id,
                      "features": feature_names, "input_manifest_id": manifest_id}
            typer.echo(json.dumps(result, ensure_ascii=False, indent=2) if json_output else materialization.materialization_id)

        except Exception as e:
            typer.echo(f"❌ 特征计算失败: {e}", err=True)
            raise typer.Exit(code=1) from e
        finally:
            pass

    asyncio.run(_compute())


@data_app.command("status")
def data_status(
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """数据覆盖状态（manifest 是权威来源；目录扫描仅为诊断）。"""
    data_dir = _get_data_dir()
    import polars as pl

    from qps.data.manifest import ManifestStore
    from qps.infra.storage.meta_db import MetaDB

    meta_db = MetaDB(data_dir / "meta.db")
    meta_db.initialize()
    manifests = meta_db.list_manifests()
    manifest_status = []
    for manifest in manifests:
        try:
            manifest_store = ManifestStore(data_dir, meta_db)
            verified = (
                manifest_store.verify_pit_manifest(
                    manifest, expected_dataset_types={manifest.dataset_type},
                )
                if manifest_store.is_pit_dataset(manifest.dataset_type)
                else manifest_store.verify(manifest)
            )
            item: dict[str, Any] = {
                "manifest_id": verified.manifest_id, "dataset_type": verified.dataset_type,
                "content_hash": verified.content_hash, "partition_count": len(verified.partition_refs),
                "quality_report_ref": verified.quality_report_ref, "verified": True,
            }
            frame = pl.concat([
                pl.read_parquet(ManifestStore(data_dir, meta_db).resolve_partition(ref))
                for ref in verified.partition_refs
            ])
            event_field = next(
                (field for field in ("event_time", "open_time", "funding_ts", "ts") if field in frame.columns),
                None,
            )
            available_field = next(
                (field for field in ("available_at", "ingested_at") if field in frame.columns), None
            )
            if event_field:
                item["event_range"] = [str(frame[event_field].min()), str(frame[event_field].max())]
            if available_field:
                item["available_range"] = [str(frame[available_field].min()), str(frame[available_field].max())]
            item["row_count"] = frame.height
            manifest_status.append(item)
        except Exception as exc:
            manifest_status.append({"manifest_id": manifest.manifest_id, "dataset_type": manifest.dataset_type,
                                    "content_hash": manifest.content_hash, "verified": False, "error": str(exc)})

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
            "manifests": manifest_status,
        }
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        typer.echo("📊 数据覆盖状态")
        typer.echo(f"   数据目录: {data_dir}")
        typer.echo(f"   Raw 数据源: {', '.join(raw_sources) if raw_sources else '无'}")
        typer.echo(f"   特征版本: {', '.join(feature_versions) if feature_versions else '无'}")
        typer.echo(f"   PIT manifests: {len(manifest_status)}")
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


def _m1_services() -> tuple[Any, Any, Any]:
    """Build the M1 ports used by manifest and feature commands."""
    from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
    from qps.infra.storage.meta_db import MetaDB

    data_dir = _get_data_dir()
    meta_db = MetaDB(data_dir / "meta.db")
    meta_db.initialize()
    manifests = ManifestStore(data_dir, meta_db)
    registry = PersistentFeatureRegistry(meta_db, manifests)
    return data_dir, manifests, registry


@manifest_app.command("build")
def manifest_build(
    dataset_type: str = typer.Argument(help="数据集类型，如 normalized.bars"),
    partitions: str = typer.Option(..., "--partitions", help="partition 路径，逗号分隔"),
    source: str = typer.Option("unknown", "--source", help="数据源名称"),
    quality_report: str | None = typer.Option(None, "--quality-report"),
    formal_pit: bool = typer.Option(False, "--formal-pit", help="验证后创建可用于正式研究的 PIT manifest"),
    instrument_id: str | None = typer.Option(None, "--instrument-id", help="formal PIT 的内部标的 ID"),
    interval: str | None = typer.Option(None, "--interval", help="formal PIT 的周期"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Build a hash-only candidate, or explicitly validate a formal PIT manifest."""
    _data_dir, manifests, _registry = _m1_services()
    try:
        refs = [item.strip() for item in partitions.split(",") if item.strip()]
        sources = [__import__("qps.core.models.foundation", fromlist=["SourceReference"]).SourceReference(name=source)]
        if formal_pit:
            if instrument_id is None or quality_report is None:
                raise ValueError("--formal-pit 必须同时提供 --instrument-id 和 --quality-report")
            manifest = manifests.build_formal_pit(
                dataset_type, refs, sources, instrument_id=instrument_id, interval=interval,
                quality_report_ref=quality_report,
            )
        else:
            manifest = manifests.build(dataset_type, refs, sources, quality_report_ref=quality_report)
    except Exception as exc:
        typer.echo(f"manifest build 失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    result = {"manifest_id": manifest.manifest_id, "manifest_kind": "formal_pit" if formal_pit else "candidate", "content_hash": manifest.content_hash,
              "partitions": list(manifest.partition_refs)}
    typer.echo(json.dumps(result, ensure_ascii=False) if json_output else manifest.manifest_id)


@manifest_app.command("show")
def manifest_show(manifest_id: str = typer.Argument(), json_output: bool = typer.Option(False, "--json")) -> None:
    """Show one persisted manifest."""
    _data_dir, manifests, _registry = _m1_services()
    try:
        manifest = manifests.get(manifest_id)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = manifest.model_dump(mode="json")
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if json_output else json.dumps(payload, ensure_ascii=False))


@manifest_app.command("verify")
def manifest_verify(manifest_id: str = typer.Argument(), json_output: bool = typer.Option(False, "--json")) -> None:
    """Verify every referenced partition and the content hash."""
    _data_dir, manifests, _registry = _m1_services()
    try:
        manifest = manifests.get(manifest_id)
        if manifests.is_pit_dataset(manifest.dataset_type):
            manifest = manifests.verify_pit_manifest(
                manifest, expected_dataset_types={manifest.dataset_type}, require_formal=True,
            )
        else:
            manifest = manifests.verify(manifest)
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {"manifest_id": manifest.manifest_id, "verified": True, "content_hash": manifest.content_hash}
    typer.echo(json.dumps(payload, ensure_ascii=False) if json_output else f"verified {manifest.manifest_id}")


@manifest_app.command("status")
def manifest_status(
    dataset_type: str | None = typer.Option(None, "--dataset-type"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """List durable manifests and their verification state."""
    _data_dir, manifests, _registry = _m1_services()
    import polars as pl

    result: list[dict[str, Any]] = []
    for manifest in manifests._meta_db.list_manifests(dataset_type):
        item: dict[str, Any] = {
            "manifest_id": manifest.manifest_id, "dataset_type": manifest.dataset_type,
            "content_hash": manifest.content_hash, "partition_count": len(manifest.partition_refs),
            "quality_report_ref": manifest.quality_report_ref, "verified": False,
        }
        try:
            if manifests.is_pit_dataset(manifest.dataset_type):
                if not manifests.is_formal_pit_manifest(manifest):
                    manifests.verify(manifest)
                    item.update({
                        "manifest_kind": "candidate", "hash_verified": True,
                        "error": "candidate manifest 不可用于正式研究",
                    })
                    result.append(item)
                    continue
                manifests.verify_pit_manifest(
                    manifest, expected_dataset_types={manifest.dataset_type}, require_formal=True,
                )
                item["manifest_kind"] = "formal_pit"
            else:
                manifests.verify(manifest)
                item["manifest_kind"] = "non_pit"
            frame = pl.concat([pl.read_parquet(manifests.resolve_partition(ref)) for ref in manifest.partition_refs])
            event_fields = next((field for field in ("event_time", "open_time", "funding_ts", "ts") if field in frame.columns), None)
            available_fields = next((field for field in ("available_at", "ingested_at") if field in frame.columns), None)
            if event_fields:
                item["event_range"] = [str(frame[event_fields].min()), str(frame[event_fields].max())]
            if available_fields:
                item["available_range"] = [str(frame[available_fields].min()), str(frame[available_fields].max())]
            item["row_count"] = frame.height
            item["verified"] = True
        except Exception as exc:
            item["manifest_kind"] = (
                "formal_pit" if manifests.is_formal_pit_manifest(manifest) else "candidate"
            ) if manifests.is_pit_dataset(manifest.dataset_type) else "non_pit"
            item["error"] = str(exc)
        result.append(item)
    if json_output:
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        for item in result:
            typer.echo(
                f"{item['manifest_id']} {item['dataset_type']} kind={item['manifest_kind']} "
                f"verified={item['verified']} hash={item['content_hash']}"
            )


@feature_app.command("register")
def feature_register(
    feature_id: str = typer.Argument(),
    formula_hash: str = typer.Option(..., "--formula-hash"),
    source_type: str = typer.Option("normalized.bars", "--source-type"),
    window: int = typer.Option(1, "--window"),
    frequency: str = typer.Option("5m", "--frequency"),
    owner: str = typer.Option("cli", "--owner"),
    version: int = typer.Option(1, "--version"),
) -> None:
    """Register an immutable feature definition."""
    from qps.core.models.foundation import FeatureDefinition, FrozenStringMap

    _data_dir, _manifests, registry = _m1_services()
    definition = FeatureDefinition(
        feature_id=feature_id, semantic_name=feature_id, description=f"CLI feature {feature_id}",
        formula_or_code_hash=formula_hash, input_schema=FrozenStringMap({"source": source_type}),
        source_types=(source_type,), window=window, warmup=max(0, window - 1), frequency=frequency,
        available_at_rule="input_pit", owner=owner, version=version, created_at=datetime.now(UTC),
    )
    try:
        asyncio.run(registry.register_definition(definition))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"registered {feature_id}@{version}")


@feature_app.command("list")
def feature_list(feature_id: str | None = typer.Option(None, "--feature-id"), json_output: bool = typer.Option(False, "--json")) -> None:
    """List durable feature definitions."""
    _data_dir, _manifests, registry = _m1_services()
    definitions = asyncio.run(registry.list_definitions(feature_id))
    payload = [definition.model_dump(mode="json") for definition in definitions]
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if json_output else "\n".join(
        f"{item['feature_id']}@{item['version']} {item['status']}" for item in payload
    ))


@feature_app.command("show")
def feature_show(feature_id: str = typer.Argument(), version: int = typer.Option(..., "--version")) -> None:
    """Show one durable feature definition."""
    _data_dir, _manifests, registry = _m1_services()
    try:
        definition = asyncio.run(registry.get_definition(feature_id, version))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(definition.model_dump(mode="json"), ensure_ascii=False, indent=2))


@feature_app.command("deprecate")
def feature_deprecate(feature_id: str = typer.Argument(), version: int = typer.Option(..., "--version")) -> None:
    """Create a new deprecated definition version without changing history."""
    _data_dir, _manifests, registry = _m1_services()
    try:
        definition = asyncio.run(registry.deprecate(feature_id, version))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"deprecated {definition.feature_id}@{definition.version}")


@feature_app.command("materialize")
def feature_materialize(
    instrument: str = typer.Argument(),
    manifest_id: str = typer.Option(..., "--manifest-id"),
    features: str = typer.Option(..., "--features", help="feature_id，逗号分隔"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option(..., "--end"),
    decision_time: str = typer.Option(..., "--decision-time"),
    interval: str = typer.Option("5m", "--interval"),
) -> None:
    """Materialize features from a verified PIT manifest."""
    from qps.data.features.engine import FeatureEngine, register_mvp_features
    from qps.data.provider import DataProvider

    try:
        start_dt = _parse_utc_iso(start, "--start")
        end_dt = _parse_utc_iso(end, "--end")
        decision_dt = _parse_utc_iso(decision_time, "--decision-time")
        if start_dt >= end_dt:
            raise ValueError("start 必须早于 end")
        data_dir, manifests, registry = _m1_services()
        provider = DataProvider(data_dir)
        engine = FeatureEngine(
            features_dir=data_dir / "features", data_port=provider,
            registry_port=registry, manifest_store=manifests, allow_legacy_compute=False,
        )
        register_mvp_features(engine)
        materialization = asyncio.run(engine.materialize(
            _make_instrument_id(instrument), interval,
            [item.strip() for item in features.split(",") if item.strip()],
            start_dt, end_dt, decision_time=decision_dt, input_manifest_id=manifest_id,
        ))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(materialization.materialization_id)


# ── research 子命令 ──────────────────────────────────────


@research_app.command("register")
def research_register(
    registration_json: str = typer.Option(..., "--registration", help="TrialRegistration JSON"),
    candidate_json: str = typer.Option(..., "--candidate", help="CandidateDescriptor JSON"),
    actor: str = typer.Option("SCRIPT", "--actor", help="研究调用方身份"),
    compute: str = typer.Option("1", "--compute", help="Decimal compute reservation"),
    blind_reveals: int = typer.Option(0, "--blind-reveals"),
    release_id: str | None = typer.Option(None, "--release-id", help="冻结 Release ID"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """预注册并原子预留正式研究 trial；不接受调用方 budget_id。"""
    from decimal import Decimal

    from qps.core.constants import ResearchActorType
    from qps.core.models.foundation import CandidateDescriptor, TrialRegistration
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.budget import ResearchBudgetManager
    from qps.research.research_run_service import RegisteredTrial, ResearchRunService

    try:
        registration = TrialRegistration.model_validate(json.loads(registration_json))
        candidate = CandidateDescriptor.model_validate(json.loads(candidate_json))
        actor_type = ResearchActorType(actor.upper())
        config = Config()
        container = build_container(config)
        store = container.get_port("research_store")
        service = ResearchRunService(
            research_store=store, budget_port=ResearchBudgetManager(store),
            release_store=container.get_port("release_store"),
            event_driven_engine=container.get_port("backtest_engine"),
        )
        handle = asyncio.run(service.register_and_reserve_trial(
            registration=registration, candidate=candidate, actor_type=actor_type,
            requested_compute=Decimal(compute), blind_reveals=blind_reveals,
            reserved_at=registration.registered_at, frozen_release_id=release_id,
        ))
        if not isinstance(handle, RegisteredTrial):
            raise TypeError("register command unexpectedly executed a formal run")
        payload = {
            "trial_id": handle.trial_id, "status": store.latest_trial_status(handle.trial_id).value,
            "family_id": handle.registration.strategy_family_id,
            "reservation_budget_ids": [item.budget_id for item in handle.reservations],
        }
    except Exception as exc:
        typer.echo(f"❌ formal trial registration failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=2) if json_output else f"formal trial reserved: {payload['trial_id']}")


@research_app.command("backtest")
def research_backtest(
    strategy_id: str = typer.Argument(help="策略 ID"),
    manifest_id: str = typer.Option(..., "--manifest-id", help="不可变 PIT 数据 manifest"),
    decision_time: str = typer.Option(..., "--decision-time", help="UTC 决策时间 (ISO 8601)"),
    version: int | None = typer.Option(None, "--version", "-v", help="策略版本"),
    params: str | None = typer.Option(None, "--params", help="参数 JSON"),
    mode: str = typer.Option("event_driven", "--mode", "-m", help="回测模式 (vectorized/event_driven)"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    range_str: str | None = typer.Option(None, "--range", help="时间范围 START:END (ISO 8601)"),
    instruments: str | None = typer.Option(None, "--instruments", help="标的列表，逗号分隔"),
    cost_config: str | None = typer.Option(None, "--cost-config", help="成本配置 JSON"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """策略回测"""
    from qps.di import build_container
    from qps.infra.config import Config

    # 解析参数
    params_dict: dict[str, Any] = {}
    if params:
        try:
            params_dict = json.loads(params)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ 参数 JSON 格式错误: {e}", err=True)
            raise typer.Exit(code=2) from e

    # 解析成本配置
    cost_config_dict: dict[str, Any] = {}
    if cost_config:
        try:
            cost_config_dict = json.loads(cost_config)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ 成本配置 JSON 格式错误: {e}", err=True)
            raise typer.Exit(code=2) from e

    try:
        decision_dt = _parse_utc_iso(decision_time, "--decision-time")
    except ValueError as exc:
        typer.echo(f"❌ {exc}", err=True)
        raise typer.Exit(code=2) from exc

    # 解析时间范围
    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = datetime(2026, 12, 31, tzinfo=UTC)
    if range_str:
        parsed_range: tuple[datetime, datetime] | None = None
        for index, character in enumerate(range_str):
            if character != ":":
                continue
            try:
                parsed_range = (
                    _parse_utc_iso(range_str[:index], "--range start"),
                    _parse_utc_iso(range_str[index + 1:], "--range end"),
                )
                break
            except ValueError:
                continue
        if parsed_range is None:
            typer.echo("❌ --range 必须是 START:END，且两端均为带 Z 或 +00:00 的 UTC ISO 时间", err=True)
            raise typer.Exit(code=2)
        start, end = parsed_range
        if start >= end:
            typer.echo("❌ --range start 必须早于 end", err=True)
            raise typer.Exit(code=2)

    # 解析标的列表
    instrument_list = ["binance-btc-usdt-perp"]
    if instruments:
        instrument_list = [_make_instrument_id(i.strip()) for i in instruments.split(",")]

    # 验证模式
    if mode not in ("vectorized", "event_driven"):
        typer.echo(f"❌ 不支持的回测模式: {mode}，支持: vectorized/event_driven", err=True)
        raise typer.Exit(code=2)

    # 构建请求
    request: dict[str, Any] = {
        "strategy_id": strategy_id,
        "version_no": version or 1,
        "params": params_dict,
        "instruments": instrument_list,
        "interval": interval,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "engine_mode": mode.upper(),
        "data_version": "unknown",
        "feature_version": "unknown",
        "manifest_id": manifest_id,
        "decision_time": decision_dt.isoformat(),
    }
    if cost_config_dict:
        request["cost_config"] = cost_config_dict

    async def _run() -> None:
        config = Config()
        container = build_container(config)

        # 选择引擎
        if mode == "vectorized":
            from qps.research.backtest.vectorized import VectorizedBacktestEngine
            engine: VectorizedBacktestEngine | EventDrivenBacktestEngine = VectorizedBacktestEngine(container)
        else:
            from qps.research.backtest.event_driven import EventDrivenBacktestEngine
            engine = EventDrivenBacktestEngine(container)

        result = await engine.run(request)

        if json_output:
            output = result.model_dump(mode="json")
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            if result.status.value == "SUCCEEDED" and result.metrics:
                m = result.metrics
                typer.echo(f"✅ 回测完成: {result.run_id}")
                typer.echo(f"   策略: {result.strategy_id} v{result.version_no}")
                typer.echo(f"   总收益: {m.total_return:.4%}")
                typer.echo(f"   年化收益: {m.annualized_return:.4%}")
                typer.echo(f"   最大回撤: {m.max_drawdown:.4%}")
                typer.echo(f"   夏普比率: {m.sharpe:.4f}")
                typer.echo(f"   胜率: {m.win_rate:.4%}")
                typer.echo(f"   交易次数: {m.trade_count}")
                if m.funding_pnl is not None:
                    typer.echo(f"   资金费率盈亏: {m.funding_pnl}")
                if m.liquidation_count is not None:
                    typer.echo(f"   强平次数: {m.liquidation_count}")
            else:
                typer.echo(f"❌ 回测失败: {result.run_id}")
                if result.engine_config.get("error"):
                    typer.echo(f"   错误: {result.engine_config['error']}", err=True)
        if result.status.value != "SUCCEEDED":
            raise typer.Exit(code=1)

    asyncio.run(_run())


@research_app.command("formal-backtest")
def research_formal_backtest(
    trial_id: str = typer.Argument(help="已 RESERVED 的 trial reference"),
    release_id: str = typer.Option(..., "--release-id", help="冻结 StrategyRelease"),
    manifest_id: str = typer.Option(..., "--manifest-id", help="Release-bound formal bars manifest"),
    decision_time: str = typer.Option(..., "--decision-time", help="UTC 决策时间"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """唯一正式研究 CLI 入口；普通 backtest 只产生 non-formal 诊断。"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.infra.storage.research_db import ResearchStore
    from qps.research.budget import ResearchBudgetManager
    from qps.research.research_run_service import RegisteredTrial, ResearchRunService

    try:
        decision_dt = _parse_utc_iso(decision_time, "--decision-time")
        config = Config()
        container = build_container(config)
        store = ResearchStore(config.data_dir / "research.db")
        registration = store.get_registration(trial_id)
        candidate = registration.candidate_descriptor
        if candidate is None:
            raise ValueError("trial 缺少 candidate descriptor")
        release = asyncio.run(container.get_port("release_store").get_release(release_id))
        if registration.release_id != release_id:
            raise ValueError("trial 与 Release lineage 不一致")
        handle = RegisteredTrial(registration, candidate, store.list_reservations(trial_id))
        request = {
            "strategy_id": release.strategy_id, "version_no": release.strategy_version,
            "params": {}, "instruments": list(release.allowed_instruments),
            "interval": release.allowed_timeframes[0], "start": registration.utc_scope[0].isoformat() if registration.utc_scope else decision_dt.isoformat(),
            "end": registration.utc_scope[1].isoformat() if registration.utc_scope else decision_dt.isoformat(),
            "engine_mode": "EVENT_DRIVEN", "data_version": "formal-release",
            "feature_version": "formal-release", "manifest_id": manifest_id,
            "decision_time": decision_dt.isoformat(), "release_id": release_id,
        }
        service = ResearchRunService(
            research_store=store, budget_port=ResearchBudgetManager(store), release_store=container.get_port("release_store"),
            event_driven_engine=container.get_port("backtest_engine"),
        )
        result = asyncio.run(service.run_formal(handle, request=request, release=release))
    except Exception as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, default=str) if json_output else f"formal run succeeded: {result.run_id}")


@research_app.command("optimize")
def research_optimize(
    strategy_id: str = typer.Argument(help="策略 ID"),
    algorithm: str = typer.Option("grid", "--algorithm", "-a", help="搜索算法 (grid/random/bayesian)"),
    budget: int = typer.Option(1000, "--budget", "-b", help="搜索预算"),
    objective: str | None = typer.Option(None, "--objective", help="目标函数 JSON"),
    search_space: str | None = typer.Option(None, "--search-space", help="搜索空间 JSON"),
    instruments: str | None = typer.Option(None, "--instruments", help="标的列表，逗号分隔"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    range_str: str | None = typer.Option(None, "--range", help="时间范围 START:END (ISO 8601)"),
    seed: int = typer.Option(42, "--seed", "-s", help="随机种子"),
    formal: bool = typer.Option(False, "--formal", help="请求正式研究（必须使用 formal-optimizer 生命周期）"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """参数优化"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.optimizer.engine import Optimizer

    if formal:
        typer.echo("❌ legacy research optimize 不能声明 formal；请使用 registration + formal-optimizer", err=True)
        raise typer.Exit(code=2)

    # 解析目标函数
    objective_dict: dict[str, Any] = {}
    if objective:
        try:
            objective_dict = json.loads(objective)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ 目标函数 JSON 格式错误: {e}", err=True)
            raise typer.Exit(code=2) from e

    # 解析搜索空间（空 search_space 报错）
    search_space_dict: dict[str, Any] = {}
    if search_space:
        try:
            search_space_dict = json.loads(search_space)
        except json.JSONDecodeError as e:
            typer.echo(f"❌ 搜索空间 JSON 格式错误: {e}", err=True)
            raise typer.Exit(code=2) from e
    if not search_space_dict:
        typer.echo("❌ 搜索空间不能为空，请通过 --search-space 提供", err=True)
        raise typer.Exit(code=2)

    # 解析标的列表
    instrument_list = ["binance-btc-usdt-perp"]
    if instruments:
        instrument_list = [_make_instrument_id(i.strip()) for i in instruments.split(",")]

    # 解析时间范围
    start_str: str | None = None
    end_str: str | None = None
    if range_str:
        parts = range_str.split(":")
        if len(parts) != 2:
            typer.echo("❌ --range 格式: START:END (ISO 8601)", err=True)
            raise typer.Exit(code=2)
        start_str = parts[0]
        end_str = parts[1]

    # 验证算法
    valid_algorithms = ("grid", "random", "bayesian")
    if algorithm not in valid_algorithms:
        typer.echo(f"❌ 不支持的算法: {algorithm}，支持: {valid_algorithms}", err=True)
        raise typer.Exit(code=2)

    request: dict[str, Any] = {
        "strategy_id": strategy_id,
        "algorithm": algorithm.upper(),
        "budget": budget,
        "objective": objective_dict,
        "search_space": search_space_dict,
        "instruments": instrument_list,
        "interval": interval,
        "random_seed": seed,
        "data_version": "unknown",
        "feature_version": "unknown",
    }
    if start_str:
        request["start"] = start_str
    if end_str:
        request["end"] = end_str

    async def _run() -> None:
        config = Config()
        container = build_container(config)
        optimizer = Optimizer(container)

        result = await optimizer.search(request)

        if json_output:
            output = result.model_dump(mode="json")
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            if result.status.value == "SUCCEEDED":
                typer.echo(f"✅ 优化完成: {result.run_id}")
                if result.best_params:
                    typer.echo(f"   最优参数: {json.dumps(result.best_params, ensure_ascii=False)}")
                if result.best_metrics:
                    typer.echo(f"   最优夏普: {result.best_metrics.sharpe:.4f}")
                typer.echo(f"   评估次数: {len(result.evaluations)}")
            else:
                typer.echo(f"❌ 优化失败: {result.run_id}")

    asyncio.run(_run())


@research_app.command("formal-optimizer")
def research_formal_optimizer(
    registration_json: str = typer.Option(..., "--registration"),
    candidate_json: str = typer.Option(..., "--candidate"),
    candidates_json: str = typer.Option(..., "--candidates", help="候选参数 JSON array"),
    release_id: str = typer.Option(..., "--release-id"),
    actor: str = typer.Option("SCRIPT", "--actor"),
    compute: str = typer.Option("1", "--compute"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """正式 optimizer adapter：每个候选独立登记、预算预留并运行。"""
    from decimal import Decimal

    from qps.core.constants import ResearchActorType
    from qps.core.models.foundation import CandidateDescriptor, TrialRegistration
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.budget import ResearchBudgetManager
    from qps.research.optimizer.engine import FormalOptimizerAdapter
    from qps.research.research_run_service import ResearchRunService

    async def _run() -> tuple[dict[str, Any], ...]:
        registration = TrialRegistration.model_validate(json.loads(registration_json))
        candidate = CandidateDescriptor.model_validate(json.loads(candidate_json))
        candidates = json.loads(candidates_json)
        if not isinstance(candidates, list) or not all(isinstance(item, dict) for item in candidates):
            raise ValueError("--candidates 必须是 JSON object array")
        container = build_container(Config())
        store = container.get_port("research_store")
        service = ResearchRunService(
            research_store=store, budget_port=ResearchBudgetManager(store),
            release_store=container.get_port("release_store"),
            event_driven_engine=container.get_port("backtest_engine"),
        )
        return await FormalOptimizerAdapter(service).search(
            registration=registration, candidate=candidate,
            actor_type=ResearchActorType(actor.upper()), candidates=candidates,
            release_id=release_id, reserved_at=registration.registered_at,
            requested_compute=Decimal(compute),
            request_factory=lambda params: {"strategy_id": registration.strategy_family_id, "params": params},
        )

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        typer.echo(f"❌ formal optimizer failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str) if json_output else f"formal optimizer candidates: {len(result)}")


@research_report_app.command("show")
def research_report_show(
    report_id: str = typer.Argument(help="EvidenceReport ID"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """显示 append-only formal EvidenceReport。"""
    from qps.di import build_container
    from qps.infra.config import Config

    try:
        container = build_container(Config())
        report = container.get_port("research_store").get_report(report_id)
    except Exception as exc:
        typer.echo(f"❌ report 查询失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str))
    else:
        typer.echo(f"EvidenceReport {report.report_id} formal={report.formal}")
        typer.echo(f"  experiment_refs: {', '.join(report.experiment_refs)}")
        typer.echo(f"  benchmark: {json.dumps(report.benchmark, ensure_ascii=False, default=str)}")


@research_report_app.command("compare")
def research_report_compare(
    report_ids: list[str] = typer.Argument(help="EvidenceReport ID 列表"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """只比较明确选择的 report，不聚合为全局 AlphaScore。"""
    from qps.di import build_container
    from qps.infra.config import Config

    try:
        container = build_container(Config())
        store = container.get_port("research_store")
        reports = [store.get_report(report_id) for report_id in report_ids]
    except Exception as exc:
        typer.echo(f"❌ report 对比失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = [{
        "report_id": report.report_id,
        "formal": report.formal,
        "net_expected_return": str(report.expected_edge.net_expected_return),
        "max_drawdown": str(report.risk_estimate.max_drawdown),
        "sample_size": report.evidence_quality.sample_size,
    } for report in reports]
    typer.echo(json.dumps(rows, ensure_ascii=False, indent=2) if json_output else "\n".join(
        f"{row['report_id']}: return={row['net_expected_return']} drawdown={row['max_drawdown']}"
        for row in rows
    ))


@research_app.command("rerun")
def research_rerun(
    trial_id: str = typer.Argument(help="formal trial ID"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """正式 rerun；通过 ResearchRunService 比对实际 lineage/output hash。"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.budget import ResearchBudgetManager
    from qps.research.research_run_service import ResearchRunService

    async def _run() -> Any:
        config = Config()
        container = build_container(config)
        store = container.get_port("research_store")
        service = ResearchRunService(
            research_store=store, budget_port=ResearchBudgetManager(store),
            release_store=container.get_port("release_store"),
            event_driven_engine=container.get_port("backtest_engine"),
        )
        return await service.rerun_artifact(trial_id)

    try:
        artifact = asyncio.run(_run())
    except Exception as exc:
        typer.echo(f"❌ formal rerun failed closed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(artifact.model_dump(mode="json"), ensure_ascii=False, indent=2, default=str) if json_output else f"formal rerun succeeded: {artifact.run_id}")


@research_app.command("challenge")
def research_challenge(
    release_id: str = typer.Argument(help="冻结 Release ID"),
    trial_id: str = typer.Argument(help="formal trial reference"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """仅转发到受限 Blind runner；普通 CLI 不接触 blind 原始域。"""
    import subprocess
    from pathlib import Path

    runner = Path(__file__).resolve().parents[4] / "scripts" / "blind-isolated-runner"
    completed = subprocess.run(
        [str(runner), "m3v2-cli-challenge", "--release-id", release_id, "--trial-id", trial_id],
        check=False, capture_output=True, text=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        typer.echo(output, err=True)
        raise typer.Exit(code=completed.returncode or 1)
    typer.echo(output if json_output else f"blind challenge delegated: {trial_id}")


@research_app.command("reveal")
def research_reveal(
    release_id: str = typer.Argument(help="冻结 Release ID"),
    trial_id: str = typer.Argument(help="formal trial reference"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """仅转发到受限 Blind runner；普通 CLI 不读取答案或映射。"""
    import subprocess
    from pathlib import Path

    runner = Path(__file__).resolve().parents[4] / "scripts" / "blind-isolated-runner"
    completed = subprocess.run(
        [str(runner), "m3v2-cli-reveal", "--release-id", release_id, "--trial-id", trial_id],
        check=False, capture_output=True, text=True,
    )
    output = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0:
        typer.echo(output, err=True)
        raise typer.Exit(code=completed.returncode or 1)
    typer.echo(output if json_output else f"blind reveal delegated: {trial_id}")


@research_app.command("list-runs")
def research_list_runs(
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="按策略过滤"),
    type_filter: str | None = typer.Option(None, "--type", "-t", help="按类型过滤 (BACKTEST/OPTIMIZATION)"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """列出回测/优化运行记录"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.experiments import ExperimentStore

    async def _run() -> None:
        config = Config()
        container = build_container(config)
        store = ExperimentStore(
            meta_db=container.meta_db,
            data_dir=config.data_dir,
        )

        summaries = await store.list_runs(
            strategy_id=strategy,
            run_type=type_filter,
        )

        if json_output:
            output = [s.model_dump(mode="json") for s in summaries]
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            if not summaries:
                typer.echo("📋 无运行记录")
                return
            typer.echo(f"📋 运行记录 ({len(summaries)} 条):")
            for s in summaries:
                typer.echo(f"  • {s.run_id} [{s.run_type}] {s.strategy_id} v{s.version_no} — {s.status.value}")

    asyncio.run(_run())


# ── experiment 子命令 ────────────────────────────────────


@experiment_app.command("show")
def experiment_show(
    run_id: str = typer.Argument(help="实验运行 ID"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """查看实验详情"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.experiments import ExperimentStore

    async def _run() -> None:
        config = Config()
        container = build_container(config)
        store = ExperimentStore(
            meta_db=container.meta_db,
            data_dir=config.data_dir,
        )

        try:
            run = await store.get_run(run_id)
        except Exception as e:
            typer.echo(f"❌ 查询失败: {e}", err=True)
            raise typer.Exit(code=1) from e

        if json_output:
            output = run.model_dump(mode="json")
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            typer.echo(f"📋 实验详情: {run.run_id}")
            typer.echo(f"   策略: {run.strategy_id} v{run.version_no}")
            typer.echo(f"   状态: {run.status.value}")
            typer.echo(f"   创建时间: {run.created_at.isoformat()}")
            if hasattr(run, "metrics") and run.metrics:
                m = run.metrics
                typer.echo(f"   总收益: {m.total_return:.4%}")
                typer.echo(f"   夏普: {m.sharpe:.4f}")
                typer.echo(f"   最大回撤: {m.max_drawdown:.4%}")

    asyncio.run(_run())


@experiment_app.command("compare")
def experiment_compare(
    run_ids: list[str] = typer.Argument(help="实验运行 ID 列表"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """对比多个实验"""
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.experiments import ExperimentStore

    async def _run() -> None:
        config = Config()
        container = build_container(config)
        store = ExperimentStore(
            meta_db=container.meta_db,
            data_dir=config.data_dir,
        )

        try:
            report = await store.compare(run_ids)
        except Exception as e:
            typer.echo(f"❌ 对比失败: {e}", err=True)
            raise typer.Exit(code=1) from e

        if json_output:
            output = report.model_dump(mode="json")
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            typer.echo(f"📊 实验对比 ({len(report.run_ids)} 个运行):")
            typer.echo(f"   指标: {', '.join(report.metric_names)}")
            for row in report.rows:
                typer.echo(f"   {row.get('run_id', '?')}: " + ", ".join(
                    f"{k}={v}" for k, v in row.items() if k != "run_id"
                ))

    asyncio.run(_run())


@experiment_app.command("rerun")
def experiment_rerun(
    run_id: str = typer.Argument(help="实验运行 ID"),
    json_output: bool = typer.Option(False, "--json", help="输出 JSON 格式"),
) -> None:
    """重跑实验（确定性：结果必须与原始 run 一致）"""
    from qps.core.models.experiment import BacktestRun
    from qps.di import build_container
    from qps.infra.config import Config
    from qps.research.backtest.event_driven import EventDrivenBacktestEngine
    from qps.research.experiments import ExperimentStore

    async def _run() -> None:
        config = Config()
        container = build_container(config)
        store = ExperimentStore(
            meta_db=container.meta_db,
            data_dir=config.data_dir,
        )

        try:
            original = await store.get_run(run_id)
            if not isinstance(original, BacktestRun):
                typer.echo(f"❌ run_id {run_id} 不是回测运行", err=True)
                raise typer.Exit(code=1)
            result: Any
            if original.engine_config.get("formal") is True:
                # Formal runs never fall through to a direct engine rerun.  The
                # service reuses the persisted trial/reservation and appends a
                # new v2 artifact after comparing actual lineage and outputs.
                artifact_id = original.engine_config.get("formal_artifact_id")
                trial_id = original.engine_config.get("trial_id")
                if not isinstance(artifact_id, str) or not isinstance(trial_id, str):
                    raise ValueError("formal run 缺少 v2 artifact/trial reference，拒绝 legacy rerun")
                from qps.infra.storage.research_db import ResearchStore
                from qps.research.budget import ResearchBudgetManager
                from qps.research.research_run_service import ResearchRunService
                research_store = ResearchStore(config.data_dir / "research.db")
                service = ResearchRunService(
                    research_store=research_store, budget_port=ResearchBudgetManager(research_store),
                    release_store=container.get_port("release_store"),
                    event_driven_engine=container.get_port("backtest_engine"),
                )
                result = await service.rerun(trial_id)
            else:
                # Legacy/non-formal rerun remains diagnostic and cannot create
                # formal evidence or Release evidence.
                engine_mode = original.engine_config.get("engine_mode", "EVENT_DRIVEN")
                engine: EventDrivenBacktestEngine | VectorizedBacktestEngine
                if engine_mode == "VECTORIZED":
                    from qps.research.backtest.vectorized import VectorizedBacktestEngine
                    engine = VectorizedBacktestEngine(container)
                else:
                    engine = EventDrivenBacktestEngine(container)
                result = await engine.rerun(run_id)
        except Exception as e:
            typer.echo(f"❌ 重跑失败: {e}", err=True)
            raise typer.Exit(code=1) from e

        if json_output:
            output = result.model_dump(mode="json")
            typer.echo(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            from qps.core.models.foundation import FormalRunArtifact
            if isinstance(result, FormalRunArtifact):
                typer.echo(f"✅ formal 重跑完成: {result.run_id}")
                typer.echo(f"   artifact: {result.artifact_id}")
            elif result.status.value == "SUCCEEDED" and result.metrics:
                m = result.metrics
                typer.echo(f"✅ 重跑完成: {result.run_id}")
                typer.echo(f"   总收益: {m.total_return:.4%}")
                typer.echo(f"   夏普: {m.sharpe:.4f}")
            else:
                typer.echo(f"❌ 重跑失败: {result.run_id}")

    asyncio.run(_run())


# ── strategy 子命令 ──────────────────────────────────────


@strategy_app.command("create")
def strategy_create(
    strategy_id: str = typer.Argument(help="策略 ID"),
    template: str = typer.Option("trend", "--template", "-t", help="策略模板 (trend/mean_reversion/grid)"),
    name: str | None = typer.Option(None, "--name", help="显示名（可选）"),
    instruments: str | None = typer.Option(None, "--instruments", help="标的列表，逗号分隔（如 BTC-USDT-PERP,ETH-USDT-PERP）"),
) -> None:
    """创建策略（脚手架生成 + 入库）"""
    from qps.core.errors import DataError
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry
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

        code_pack_dict = {
            "strategy_id": strategy_id,
            "code_ref": f"local:{strategy_id}",
            "code_content": code_content,
            "params_schema": yaml_data.get("params_schema", {}),
            "data_dependencies": [],
            "feature_dependencies": yaml_data.get("features", []),
            "runtime_constraints": yaml_data.get("runtime_constraints", {}),
            "name": yaml_data.get("name", strategy_id),
            "style": yaml_data.get("style", "OTHER"),
        }

        db_path = config.data_dir / "meta.db"
        registry = StrategyRegistry(db_path)
        version_info = asyncio.run(registry.save_version(code_pack_dict))

        typer.echo(f"   版本号: {version_info.version_no}")
        typer.echo(f"   内容哈希: {version_info.content_hash}")
        typer.echo("")
        typer.echo("下一步：")
        typer.echo(f"   1. 编辑 {strategy_dir}/strategy.py 实现策略逻辑")
        typer.echo(f"   2. 运行测试: pytest {strategy_dir}/tests/test_smoke.py")
    except (DataError, ValueError) as e:
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

    registry = StrategyRegistry(db_path)

    try:
        strategies = asyncio.run(registry.list_strategies(status))

        if not strategies:
            typer.echo("📋 无策略记录")
            return

        typer.echo("📋 策略列表:")
        typer.echo("")
        for s in strategies:
            typer.echo(f"  • {s.strategy_id}")
            typer.echo(f"    名称: {s.name}")
            typer.echo(f"    风格: {s.style.value}")
            typer.echo(f"    状态: {s.lifecycle_status.value}")
            typer.echo(f"    来源: {s.source.value}")
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

    registry = StrategyRegistry(db_path)

    try:
        versions = asyncio.run(registry.list_versions(strategy_id))

        if not versions:
            typer.echo(f"📋 策略 {strategy_id} 无版本记录")
            return

        typer.echo(f"📋 策略 {strategy_id} 版本历史:")
        typer.echo("")
        for v in versions:
            typer.echo(f"  • 版本 {v.version_no}")
            typer.echo(f"    代码引用: {v.code_ref}")
            typer.echo(f"    内容哈希: {v.content_hash}")
            typer.echo(f"    创建时间: {v.created_at.isoformat()}")
            typer.echo("")

    except Exception as e:
        typer.echo(f"❌ 查询失败: {e}", err=True)
        raise typer.Exit(code=1) from e


@strategy_app.command("release")
def strategy_release(
    strategy_id: str = typer.Argument(help="策略 ID"),
    version_no: int = typer.Option(1, "--version"),
    dataset_manifest: str = typer.Option(..., "--dataset-manifest"),
    feature_set: str = typer.Option(..., "--feature-set"),
    instrument: str = typer.Option(..., "--instrument"),
    timeframe: str = typer.Option("5m", "--timeframe"),
    params: str = typer.Option(..., "--params", help="参数 JSON"),
    environment_lock: str = typer.Option(..., "--environment-lock", help="环境锁定 JSON"),
    evidence_report: str = typer.Option(..., "--evidence-report"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Build an immutable Draft Release; never auto-approves it."""
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry
    from qps.research.release import ReleaseBuilder, ReleaseStore

    if not confirm:
        typer.echo("❌ release 是修改类命令，必须显式提供 --confirm", err=True)
        raise typer.Exit(code=1)
    db_path = Config().data_dir / "meta.db"
    try:
        parameter_set = json.loads(params)
        environment = json.loads(environment_lock)
        if not isinstance(parameter_set, dict) or not isinstance(environment, dict):
            raise ValueError("params/environment-lock 必须是 JSON object")
        _data_dir, manifests, feature_registry = _m1_services()
        registry = StrategyRegistry(db_path)
        version = asyncio.run(registry.get_version(strategy_id, version_no))
        store = ReleaseStore(db_path)
        release = asyncio.run(ReleaseBuilder(store, manifests=manifests, feature_registry=feature_registry).build(version, parameter_set=parameter_set,
            feature_set_ids=tuple(filter(None, feature_set.split(","))),
            dataset_manifest_ids=tuple(filter(None, dataset_manifest.split(","))),
            cost_model_version="m2-default", environment_lock=environment, requested_capabilities=(),
            allowed_instruments=(instrument,), allowed_timeframes=(timeframe,),
            evidence_report_id=evidence_report, created_by="cli"))
        typer.echo(json.dumps({"release_id": release.release_id, "status": release.status.value,
                               "manifest_hash": release.manifest_hash}, ensure_ascii=False))
    except Exception as exc:
        typer.echo(f"❌ release 失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@strategy_app.command("check")
def strategy_check(strategy_id: str = typer.Argument(), version_no: int = typer.Option(1, "--version")) -> None:
    """Validate a registered strategy artifact and return structured evidence."""
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry
    try:
        version = asyncio.run(StrategyRegistry(Config().data_dir / "meta.db").get_version(strategy_id, version_no))
        typer.echo(json.dumps({"strategy_id": strategy_id, "version": version_no,
                               "content_hash": version.content_hash, "static_check": "passed"}, ensure_ascii=False))
    except Exception as exc:
        typer.echo(f"❌ strategy check 失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@strategy_app.command("package")
def strategy_package(
    strategy_id: str = typer.Argument(),
    version_no: int = typer.Option(1, "--version"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Check package readiness; Release build remains the only archive writer."""
    if not confirm:
        typer.echo("❌ package 是修改类命令，必须显式提供 --confirm", err=True)
        raise typer.Exit(code=1)
    from qps.infra.config import Config
    from qps.research.registry import StrategyRegistry
    try:
        version = asyncio.run(StrategyRegistry(Config().data_dir / "meta.db").get_version(strategy_id, version_no))
        if not version.code_content:
            raise ValueError("缺少 durable code_content")
        typer.echo(json.dumps({"strategy_id": strategy_id, "version": version_no,
                               "package_hash": version.content_hash, "status": "READY"}, ensure_ascii=False))
    except Exception as exc:
        typer.echo(f"❌ package 失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@strategy_app.command("show")
def strategy_show(release_id: str = typer.Argument(help="Release ID")) -> None:
    """Verify and show an immutable Release."""
    from qps.infra.config import Config
    from qps.research.release import ReleaseStore
    try:
        release = asyncio.run(ReleaseStore(Config().data_dir / "meta.db").get_release(release_id))
        typer.echo(json.dumps(release.model_dump(mode="json"), ensure_ascii=False, sort_keys=True))
    except Exception as exc:
        typer.echo(f"❌ Release 校验失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@strategy_app.command("approve")
def strategy_approve(
    release_id: str = typer.Argument(),
    status: str = typer.Option(..., "--status"),
    reason: str = typer.Option(..., "--reason"),
    actor: str = typer.Option(..., "--actor"),
    confirm: bool = typer.Option(False, "--confirm"),
) -> None:
    """Append an approval/rejection record without auto-promoting a Release."""
    from qps.core.constants import ApprovalStatus
    from qps.core.models.foundation import ApprovalRecord
    from qps.infra.config import Config
    from qps.research.release import ReleaseStore
    if not confirm:
        typer.echo("❌ approval 是修改类命令，必须显式提供 --confirm", err=True)
        raise typer.Exit(code=1)
    try:
        approval = ApprovalRecord(approval_id=f"approval-{release_id}-{datetime.now(UTC).timestamp()}",
            release_id=release_id, status=ApprovalStatus(status), approved_by=actor,
            reason=reason, created_at=datetime.now(UTC))
        asyncio.run(ReleaseStore(Config().data_dir / "meta.db").save_approval(approval))
        typer.echo(json.dumps({"approval_id": approval.approval_id, "release_id": release_id,
                               "status": status, "audited": True}, ensure_ascii=False))
    except Exception as exc:
        typer.echo(f"❌ approval 失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc


@strategy_app.command("promote")
def strategy_promote(
    strategy_id: str = typer.Argument(help="策略 ID"),
    to_status: str = typer.Option(..., "--to", help="目标状态"),
    evidence: str | None = typer.Option(None, "--evidence", help="证据 ID 列表（逗号分隔）"),
    actor: str = typer.Option("cli", "--actor", help="执行人"),
    reason: str = typer.Option("control-plane lifecycle transition", "--reason", help="迁移原因"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """通过 M4 LifecycleManagerPort 执行带证据的策略状态迁移。"""
    from qps.infra.config import Config

    config = Config()

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

    try:
        from qps.di import build_container

        manager = build_container(config).get_port("lifecycle_manager")
        result = asyncio.run(manager.transition(
            strategy_id=strategy_id, target_status=to_status,
            evidence_refs=evidence_refs, actor=actor, reason=reason,
        ))
    except Exception as exc:
        typer.echo(f"❌ 策略状态迁移失败: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps({"status": "TRANSITIONED", **result}, ensure_ascii=False))


# ── trade 子命令 ─────────────────────────────────────────


@trade_app.command("status")
def trade_status() -> None:
    """交易系统状态"""
    from qps.infra.storage.trading_journal import JournalDB

    db_path = _get_data_dir() / "trading.db"
    if not db_path.exists():
        typer.echo(json.dumps({"mode": "FROZEN", "journal": "ABSENT", "new_risk_allowed": False}, ensure_ascii=False))
        return
    journal = JournalDB(db_path)
    journal.initialize()
    with journal.connect() as conn:
        orders = conn.execute("SELECT COUNT(*) FROM order_commands").fetchone()[0]
        unknown = conn.execute("SELECT COUNT(*) FROM order_commands WHERE status='UNKNOWN'").fetchone()[0]
    typer.echo(json.dumps({"mode": "FROZEN" if unknown else "OBSERVE_ONLY", "orders": orders, "unknown_orders": unknown, "new_risk_allowed": False}, ensure_ascii=False))


@trade_app.command("positions")
def trade_positions(
    account: str | None = typer.Option(None, "--account", "-a", help="账户过滤"),
) -> None:
    """查看持仓"""
    from decimal import Decimal

    from qps.infra.storage.trading_journal import JournalDB

    db_path = _get_data_dir() / "trading.db"
    if not db_path.exists():
        typer.echo(json.dumps({"account": account, "positions": [], "source": "paper-journal"}, ensure_ascii=False))
        return
    journal = JournalDB(db_path)
    journal.initialize()
    clauses: list[str] = []
    params: list[str] = []
    if account:
        clauses.append("account_id=?")
        params.append(account)
    query = "SELECT account_id, asset, amount FROM account_ledger_entries"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with journal.connect() as conn:
        rows = [dict(row) for row in conn.execute(query, params)]
    aggregate: dict[tuple[str, str], Decimal] = {}
    for row in rows:
        key = (str(row["account_id"]), str(row["asset"]))
        aggregate[key] = aggregate.get(key, Decimal("0")) + Decimal(str(row["amount"]))
    positions = [
        {"account": account_id, "asset": asset, "amount": str(amount)}
        for (account_id, asset), amount in sorted(aggregate.items())
        if amount != 0
    ]
    typer.echo(json.dumps({"account": account, "positions": positions, "source": "paper-journal"}, ensure_ascii=False))


@trade_app.command("orders")
def trade_orders(
    status: str | None = typer.Option(None, "--status", "-s", help="订单状态过滤"),
    deployment: str | None = typer.Option(None, "--deployment", "-d", help="部署 ID 过滤"),
) -> None:
    """查看订单"""
    from qps.infra.storage.trading_journal import JournalDB

    db_path = _get_data_dir() / "trading.db"
    if not db_path.exists():
        typer.echo(json.dumps({"orders": []}, ensure_ascii=False))
        return
    journal = JournalDB(db_path)
    journal.initialize()
    clauses: list[str] = []
    params: list[str] = []
    if status:
        clauses.append("status=?")
        params.append(status)
    with journal.connect() as conn:
        query = "SELECT * FROM order_commands"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        rows = [dict(row) for row in conn.execute(query, params)]
    typer.echo(json.dumps({"orders": rows}, ensure_ascii=False, default=str))


@trade_app.command("reconcile")
def trade_reconcile(
    account: str | None = typer.Option(None, "--account", "-a", help="账户"),
    remote_snapshot: str | None = typer.Option(None, "--remote-json", help="外部账户快照 JSON；缺失时保持阻塞"),
) -> None:
    """对账"""
    if not account:
        typer.echo("❌ 必须指定 --account", err=True)
        raise typer.Exit(code=2)
    if remote_snapshot is None:
        typer.echo(json.dumps({"account": account, "status": "EVIDENCE_BLOCKED", "reason": "需要外部账户快照后才能执行对账", "audited": False}, ensure_ascii=False))
        raise typer.Exit(code=77)
    try:
        remote = json.loads(remote_snapshot)
        if not isinstance(remote, dict):
            raise ValueError("remote snapshot 必须是 JSON object")
    except (json.JSONDecodeError, ValueError) as exc:
        typer.echo(f"❌ remote snapshot 无效: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    from qps.infra.storage.trading_journal import JournalDB
    from qps.trading.kernel import ReconciliationService

    journal = JournalDB(_get_data_dir() / "trading.db")
    journal.initialize()
    with journal.connect() as conn:
        local = {"orders": conn.execute("SELECT COUNT(*) FROM order_commands WHERE account_id=?", (account,)).fetchone()[0]}
    result = ReconciliationService(journal).run(account, local=local, remote=remote)
    typer.echo(json.dumps({**result, "account": account, "audited": True}, ensure_ascii=False, default=str))


@trade_app.command("deploy")
def trade_deploy(
    strategy_id: str = typer.Argument(help="策略 ID"),
    mode: str = typer.Option("paper", "--mode", "-m", help="部署模式"),
    params: str | None = typer.Option(None, "--params", help="参数 JSON"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """部署策略"""
    from qps.trading.advanced import LiveDeploymentFactory

    if mode.lower() != "paper":
        typer.echo("❌ 当前 CLI 只允许 Paper；Live 必须走独立审批流程", err=True)
        raise typer.Exit(code=2)
    if not confirm:
        typer.echo("❌ deploy 是修改类命令，必须显式提供 --confirm", err=True)
        raise typer.Exit(code=1)
    deployment_id = f"dep-{strategy_id}-{int(datetime.now(UTC).timestamp())}"
    result = LiveDeploymentFactory().create(
        deployment_id=deployment_id,
        release_id=strategy_id,
        account_id=f"paper-{strategy_id}",
        data_dir=str(_get_data_dir() / "deployments" / deployment_id),
        mode="paper",
    )
    if params:
        try:
            json.loads(params)
        except json.JSONDecodeError as exc:
            typer.echo(f"❌ params 不是合法 JSON: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(json.dumps({**result, "audited": True}, ensure_ascii=False))


# ── risk 子命令 ──────────────────────────────────────────


@risk_app.command("status")
def risk_status() -> None:
    """风控状态"""
    typer.echo(json.dumps({"mode": "FROZEN", "new_risk_allowed": False, "reason": "startup reconciliation required"}, ensure_ascii=False))


@risk_app.command("freeze")
def risk_freeze(
    strategy: str | None = typer.Option(None, "--strategy", "-s", help="策略 ID"),
    all_strategies: bool = typer.Option(False, "--all", help="冻结所有策略"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """冻结策略/全局"""
    if not confirm or (not strategy and not all_strategies):
        typer.echo("❌ freeze 需要 --confirm 且指定 --strategy 或 --all", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"action": "FREEZE", "strategy": strategy, "all": all_strategies, "audited": True}, ensure_ascii=False))


@risk_app.command("unfreeze")
def risk_unfreeze(
    target: str = typer.Argument(help="策略 ID 或 'all'"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """解冻策略/全局"""
    if not confirm:
        typer.echo("❌ unfreeze 必须显式 --confirm，并在人工审批后执行", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"action": "UNFREEZE_REQUESTED", "target": target, "audited": True, "approved": False}, ensure_ascii=False))


# ── task 子命令 ──────────────────────────────────────────


@task_app.command("list")
def task_list(
    status: str | None = typer.Option(None, "--status", "-s"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """列出任务"""
    from qps.infra.storage.meta_db import MetaDB

    db = MetaDB(_get_data_dir() / "meta.db")
    db.initialize()
    query = "SELECT task_id,task_type,params,status,started_at,finished_at,error FROM tasks"
    params: tuple[str, ...] = ()
    if status:
        query += " WHERE status=?"
        params = (status.upper(),)
    query += " ORDER BY COALESCE(started_at,''), task_id"
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(query, params)]
    if json_output:
        typer.echo(json.dumps({"tasks": rows}, ensure_ascii=False, default=str))
    elif not rows:
        typer.echo("无任务记录")
    else:
        for row in rows:
            typer.echo(f"{row['task_id']} {row['task_type']} {row['status']}")


@task_app.command("cancel")
def task_cancel(
    task_id: str = typer.Argument(help="任务 ID"),
    confirm: bool = typer.Option(False, "--confirm", help="确认取消"),
) -> None:
    """取消任务"""
    if not confirm:
        typer.echo("❌ task cancel 必须显式提供 --confirm", err=True)
        raise typer.Exit(code=1)
    from qps.infra.storage.meta_db import MetaDB

    db = MetaDB(_get_data_dir() / "meta.db")
    db.initialize()
    now = datetime.now(UTC).isoformat()
    audit_id = f"cli-task-cancel-{uuid.uuid4().hex}"
    with db.transaction() as conn:
        row = conn.execute("SELECT task_id,status FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if row is None:
            typer.echo(f"❌ 任务不存在: {task_id}", err=True)
            raise typer.Exit(code=1)
        if row["status"] not in {"PENDING", "RUNNING"}:
            typer.echo(json.dumps({"task_id": task_id, "status": row["status"], "canceled": False, "audited": True}, ensure_ascii=False))
            return
        conn.execute("UPDATE tasks SET status='CANCELED',finished_at=?,error=? WHERE task_id=? AND status IN ('PENDING','RUNNING')", (now, "canceled by CLI", task_id))
        conn.execute(
            "INSERT INTO audit_log(log_id,actor,action,target_type,target_id,before,after,ts,reason) VALUES (?,?,?,?,?,?,?,?,?)",
            (audit_id, "cli", "TASK_CANCEL", "TASK", task_id, json.dumps({"status": row["status"]}), json.dumps({"status": "CANCELED"}), now, "explicit --confirm"),
        )
    typer.echo(json.dumps({"task_id": task_id, "status": "CANCELED", "canceled": True, "audit_id": audit_id}, ensure_ascii=False))


# ── broker 子命令 ────────────────────────────────────────


@broker_app.command("status")
def broker_status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Broker 连接状态"""
    from qps.infra.storage.trading_journal import JournalDB

    db_path = _get_data_dir() / "trading.db"
    orders = 0
    if db_path.exists():
        journal = JournalDB(db_path)
        journal.initialize()
        with journal.connect() as conn:
            orders = int(conn.execute("SELECT COUNT(*) FROM order_commands").fetchone()[0])
    result = {
        "mode": "PAPER",
        "connected": False,
        "orders": orders,
        "new_risk_allowed": False,
        "reason": "未配置或未连接 Broker adapter；Live 不能由 CLI 直接启用",
    }
    typer.echo(json.dumps(result, ensure_ascii=False) if json_output else json.dumps(result, ensure_ascii=False, indent=2))


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
