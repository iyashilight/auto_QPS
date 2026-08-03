"""CLI 命令入口（见 06 文档第 4 节）

命令树：qps → data/research/experiment/strategy/trade/risk/task/broker
本阶段所有子命令返回「未实现」提示。
"""

from __future__ import annotations

import typer

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


# ── data 子命令 ──────────────────────────────────────────


@data_app.command("collect")
def data_collect(
    instrument: str = typer.Argument(help="标的符号，如 BTC-USDT-PERP"),
    interval: str = typer.Option("5m", "--interval", "-i", help="K 线间隔"),
    start: str | None = typer.Option(None, "--start", help="开始日期"),
    end: str | None = typer.Option(None, "--end", help="结束日期"),
) -> None:
    """历史数据采集"""
    _not_implemented("data collect")


@data_app.command("verify")
def data_verify(
    instrument: str = typer.Argument(help="标的符号"),
    interval: str | None = typer.Option(None, "--interval", "-i", help="K 线间隔"),
) -> None:
    """数据质量检查"""
    _not_implemented("data verify")


@data_app.command("status")
def data_status() -> None:
    """数据覆盖状态"""
    _not_implemented("data status")


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
    template: str = typer.Option("trend", "--template", "-t", help="策略模板"),
) -> None:
    """创建策略（脚手架生成）"""
    _not_implemented("strategy create")


@strategy_app.command("list")
def strategy_list(
    status: str | None = typer.Option(None, "--status", "-s", help="按状态过滤"),
) -> None:
    """列出策略"""
    _not_implemented("strategy list")


@strategy_app.command("status")
def strategy_status(
    strategy_id: str = typer.Argument(help="策略 ID"),
) -> None:
    """查看策略状态"""
    _not_implemented("strategy status")


@strategy_app.command("promote")
def strategy_promote(
    strategy_id: str = typer.Argument(help="策略 ID"),
    to_status: str = typer.Option(..., "--to", help="目标状态"),
    evidence: str | None = typer.Option(None, "--evidence", help="证据 ID 列表"),
    confirm: bool = typer.Option(False, "--confirm", help="确认操作"),
) -> None:
    """策略状态提升（需门禁证据）"""
    _not_implemented("strategy promote")


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
