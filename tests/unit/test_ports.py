"""T6 端口定义测试：所有端口可导入、签名与 docs/03 一致"""

from __future__ import annotations

import inspect

import pytest

from qps.core.ports import (
    AgentGatewayPort,
    BacktestEnginePort,
    BrokerAdapterPort,
    DataProviderPort,
    ExchangeAdapterPort,
    ExecutionEnginePort,
    ExperimentStorePort,
    FeaturePipelinePort,
    LifecycleManagerPort,
    MarketAdapterPort,
    MonitoringPort,
    OptimizerPort,
    PortfolioManagerPort,
    RiskEnginePort,
    SchedulerPort,
    SignalEnginePort,
    StrategyRegistryPort,
    StrategyRuntimePort,
)

# ── 所有端口可导入 ──────────────────────────────────────────


class TestPortsImportable:
    """所有端口可正常导入"""

    @pytest.mark.parametrize(
        "port_class",
        [
            DataProviderPort,
            MarketAdapterPort,
            ExchangeAdapterPort,
            BrokerAdapterPort,
            FeaturePipelinePort,
            BacktestEnginePort,
            OptimizerPort,
            StrategyRegistryPort,
            LifecycleManagerPort,
            SignalEnginePort,
            PortfolioManagerPort,
            RiskEnginePort,
            ExecutionEnginePort,
            StrategyRuntimePort,
            ExperimentStorePort,
            MonitoringPort,
            SchedulerPort,
            AgentGatewayPort,
        ],
        ids=lambda cls: cls.__name__,
    )
    def test_port_is_protocol(self, port_class: type) -> None:
        """所有端口都是 Protocol"""
        # runtime_checkable 的 Protocol 可用 isinstance 检查
        assert hasattr(port_class, "__protocol_attrs__") or hasattr(
            port_class, "_is_protocol"
        )

    @pytest.mark.parametrize(
        "port_class",
        [
            DataProviderPort,
            MarketAdapterPort,
            ExchangeAdapterPort,
            BrokerAdapterPort,
            FeaturePipelinePort,
            BacktestEnginePort,
            OptimizerPort,
            StrategyRegistryPort,
            LifecycleManagerPort,
            SignalEnginePort,
            PortfolioManagerPort,
            RiskEnginePort,
            ExecutionEnginePort,
            StrategyRuntimePort,
            ExperimentStorePort,
            MonitoringPort,
            SchedulerPort,
            AgentGatewayPort,
        ],
        ids=lambda cls: cls.__name__,
    )
    def test_port_has_methods(self, port_class: type) -> None:
        """每个端口至少有一个方法"""
        methods = [
            name
            for name, _ in inspect.getmembers(port_class, predicate=inspect.isfunction)
            if not name.startswith("_")
        ]
        assert len(methods) > 0, f"{port_class.__name__} 应至少有一个方法"


# ── 关键端口签名校验 ──────────────────────────────────────────


class TestDataProviderPortSignature:
    """DataProviderPort 签名与 docs/03 第 2 节一致"""

    def test_has_get_bars(self) -> None:
        assert hasattr(DataProviderPort, "get_bars")

    def test_has_get_ticks(self) -> None:
        assert hasattr(DataProviderPort, "get_ticks")

    def test_has_get_funding_rates(self) -> None:
        assert hasattr(DataProviderPort, "get_funding_rates")

    def test_has_get_open_interest(self) -> None:
        assert hasattr(DataProviderPort, "get_open_interest")

    def test_has_get_mark_prices(self) -> None:
        assert hasattr(DataProviderPort, "get_mark_prices")

    def test_has_get_features(self) -> None:
        assert hasattr(DataProviderPort, "get_features")

    def test_has_get_instrument(self) -> None:
        assert hasattr(DataProviderPort, "get_instrument")


class TestBrokerAdapterPortSignature:
    """BrokerAdapterPort 签名与 docs/03 第 5 节一致"""

    def test_has_place_order(self) -> None:
        assert hasattr(BrokerAdapterPort, "place_order")

    def test_has_cancel_order(self) -> None:
        assert hasattr(BrokerAdapterPort, "cancel_order")

    def test_has_get_order(self) -> None:
        assert hasattr(BrokerAdapterPort, "get_order")

    def test_has_get_account(self) -> None:
        assert hasattr(BrokerAdapterPort, "get_account")

    def test_has_get_positions(self) -> None:
        assert hasattr(BrokerAdapterPort, "get_positions")

    def test_has_on_execution_report(self) -> None:
        assert hasattr(BrokerAdapterPort, "on_execution_report")

    def test_has_on_account_update(self) -> None:
        assert hasattr(BrokerAdapterPort, "on_account_update")


class TestTradingPortsSignature:
    """交易链路端口签名与 docs/03 第 10 节一致"""

    def test_signal_has_ingest_intent(self) -> None:
        assert hasattr(SignalEnginePort, "ingest_intent")

    def test_portfolio_has_on_intent(self) -> None:
        assert hasattr(PortfolioManagerPort, "on_intent")

    def test_portfolio_has_get_allocation(self) -> None:
        assert hasattr(PortfolioManagerPort, "get_allocation")

    def test_portfolio_has_get_portfolio_risk(self) -> None:
        assert hasattr(PortfolioManagerPort, "get_portfolio_risk")

    def test_risk_has_pre_trade_check(self) -> None:
        assert hasattr(RiskEnginePort, "pre_trade_check")

    def test_risk_has_register_rule(self) -> None:
        assert hasattr(RiskEnginePort, "register_rule")

    def test_execution_has_submit_order(self) -> None:
        assert hasattr(ExecutionEnginePort, "submit_order")

    def test_execution_has_cancel(self) -> None:
        assert hasattr(ExecutionEnginePort, "cancel")

    def test_execution_has_reconcile(self) -> None:
        assert hasattr(ExecutionEnginePort, "reconcile")


class TestRegistryPortSignature:
    """策略注册表与生命周期端口签名与 docs/03 第 9 节一致"""

    def test_registry_has_save_version(self) -> None:
        assert hasattr(StrategyRegistryPort, "save_version")

    def test_registry_has_get_version(self) -> None:
        assert hasattr(StrategyRegistryPort, "get_version")

    def test_registry_has_list_versions(self) -> None:
        assert hasattr(StrategyRegistryPort, "list_versions")

    def test_registry_has_list_strategies(self) -> None:
        assert hasattr(StrategyRegistryPort, "list_strategies")

    def test_lifecycle_has_transition(self) -> None:
        assert hasattr(LifecycleManagerPort, "transition")


class TestServicesPortsSignature:
    """实验存储/监控/调度/Agent 网关端口签名与 docs/03 第 12 节一致"""

    def test_experiment_store_has_create_run(self) -> None:
        assert hasattr(ExperimentStorePort, "create_run")

    def test_experiment_store_has_get_run(self) -> None:
        assert hasattr(ExperimentStorePort, "get_run")

    def test_experiment_store_has_compare(self) -> None:
        assert hasattr(ExperimentStorePort, "compare")

    def test_monitoring_has_record(self) -> None:
        assert hasattr(MonitoringPort, "record")

    def test_monitoring_has_snapshot(self) -> None:
        assert hasattr(MonitoringPort, "snapshot")

    def test_monitoring_has_alert(self) -> None:
        assert hasattr(MonitoringPort, "alert")

    def test_scheduler_has_schedule(self) -> None:
        assert hasattr(SchedulerPort, "schedule")

    def test_scheduler_has_cancel(self) -> None:
        assert hasattr(SchedulerPort, "cancel")

    def test_agent_gateway_has_submit_task(self) -> None:
        assert hasattr(AgentGatewayPort, "submit_task")

    def test_agent_gateway_has_read_artifact(self) -> None:
        assert hasattr(AgentGatewayPort, "read_artifact")

    def test_agent_gateway_has_write_artifact(self) -> None:
        assert hasattr(AgentGatewayPort, "write_artifact")


# ── 强类型返回签名校验 ──────────────────────────────────────────


class TestStrongTypedReturns:
    """验证 4 个端口方法返回强类型（非 dict[str, Any]）"""

    def _get_return_annotation_name(self, port_class: type, method_name: str) -> str:
        """获取端口方法返回类型的字符串表示"""
        method = getattr(port_class, method_name)
        sig = inspect.signature(method)
        return str(sig.return_annotation)

    def test_data_provider_calendar_returns_exchange_calendar_list(self) -> None:
        """DataProviderPort.calendar() -> list[ExchangeCalendar]"""
        ann = self._get_return_annotation_name(DataProviderPort, "calendar")
        assert "ExchangeCalendar" in ann, f"calendar() 返回类型应为 list[ExchangeCalendar]，实际为 {ann}"
        assert "dict" not in ann.lower(), f"calendar() 不应返回 dict，实际为 {ann}"

    def test_market_adapter_status_returns_adapter_status(self) -> None:
        """MarketAdapterPort.status() -> AdapterStatus"""
        ann = self._get_return_annotation_name(MarketAdapterPort, "status")
        assert "AdapterStatus" in ann, f"status() 返回类型应为 AdapterStatus，实际为 {ann}"
        assert "dict" not in ann.lower(), f"status() 不应返回 dict，实际为 {ann}"

    def test_exchange_adapter_status_returns_adapter_status(self) -> None:
        """ExchangeAdapterPort.status() -> AdapterStatus"""
        ann = self._get_return_annotation_name(ExchangeAdapterPort, "status")
        assert "AdapterStatus" in ann, f"status() 返回类型应为 AdapterStatus，实际为 {ann}"
        assert "dict" not in ann.lower(), f"status() 不应返回 dict，实际为 {ann}"

    def test_broker_adapter_status_returns_adapter_status(self) -> None:
        """BrokerAdapterPort.status() -> AdapterStatus"""
        ann = self._get_return_annotation_name(BrokerAdapterPort, "status")
        assert "AdapterStatus" in ann, f"status() 返回类型应为 AdapterStatus，实际为 {ann}"
        assert "dict" not in ann.lower(), f"status() 不应返回 dict，实际为 {ann}"
