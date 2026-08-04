"""T7 依赖容器测试：容器构建、测试容器、端口替换"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import yaml

from qps.di import Container, _NotImplementedPort, build_container, build_test_container
from qps.infra.config import Config

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def test_config(config_dir: Path) -> Config:
    """测试配置"""
    config_dir.mkdir(parents=True, exist_ok=True)
    with open(config_dir / "default.yaml", "w", encoding="utf-8") as f:
        yaml.dump({"project": {"name": "autoQps_test"}}, f)
    return Config(config_dir=config_dir, env_vars={})


# ── 容器构建 ──────────────────────────────────────────────


class TestBuildContainer:
    """build_container 测试"""

    def test_build_container_returns_container(self, test_config: Config) -> None:
        """容器可构建"""
        container = build_container(test_config)
        assert isinstance(container, Container)

    def test_container_has_config(self, test_config: Config) -> None:
        """容器持有配置"""
        container = build_container(test_config)
        assert container.config is test_config

    def test_container_has_app_config(self, test_config: Config) -> None:
        """容器持有类型化配置"""
        container = build_container(test_config)
        assert container.app_config is not None
        assert container.app_config.project.name == "autoQps_test"

    def test_container_has_event_bus(self, test_config: Config) -> None:
        """容器持有事件总线"""
        container = build_container(test_config)
        assert container.event_bus is not None

    def test_container_all_ports_registered(self, test_config: Config) -> None:
        """所有端口已注册（占位实现）"""
        container = build_container(test_config)
        expected_ports = [
            "data_provider",
            "market_adapter",
            "exchange_adapter",
            "broker_adapter",
            "feature_pipeline",
            "backtest_engine",
            "optimizer",
            "strategy_registry",
            "lifecycle_manager",
            "signal_engine",
            "portfolio_manager",
            "risk_engine",
            "execution_engine",
            "strategy_runtime",
            "experiment_store",
            "monitoring",
            "scheduler",
            "agent_gateway",
        ]
        for name in expected_ports:
            assert container.has_port(name), f"端口 {name} 应已注册"

    def test_port_not_implemented_raises(self, test_config: Config) -> None:
        """占位端口调用抛 NotImplementedError"""
        container = build_container(test_config)
        port = container.get_port("backtest_engine")
        with pytest.raises(NotImplementedError, match="尚未实现"):
            port.run({})

    def test_get_missing_port_raises(self, test_config: Config) -> None:
        """获取未注册端口抛 KeyError"""
        container = build_container(test_config)
        with pytest.raises(KeyError, match="未注册"):
            container.get_port("nonexistent_port")

    def test_list_ports(self, test_config: Config) -> None:
        """列出所有已注册端口"""
        container = build_container(test_config)
        ports = container.list_ports()
        assert len(ports) == 18
        assert "data_provider" in ports


# ── 测试容器 ──────────────────────────────────────────────


class TestBuildTestContainer:
    """build_test_container 测试"""

    def test_build_test_container_without_config(self) -> None:
        """无配置构建测试容器"""
        container = build_test_container()
        assert isinstance(container, Container)

    def test_build_test_container_with_config(self, test_config: Config) -> None:
        """带配置构建测试容器"""
        container = build_test_container(config=test_config)
        assert container.config is test_config

    def test_test_container_with_port_overrides(self, test_config: Config) -> None:
        """测试容器可替换指定实现"""

        class MockDataProvider:
            async def get_bars(self, *args: object, **kwargs: object) -> list[str]:
                return ["mock_bar"]

        container = build_test_container(
            config=test_config,
            port_overrides={"data_provider": MockDataProvider()},
        )
        port = container.get_port("data_provider")
        assert isinstance(port, MockDataProvider)

    def test_replace_port_returns_old(self, test_config: Config) -> None:
        """替换端口返回旧实现"""
        container = build_container(test_config)
        old = container.replace_port("backtest_engine", _NotImplementedPort())
        assert isinstance(old, _NotImplementedPort)

    def test_register_custom_port(self, test_config: Config) -> None:
        """注册自定义端口"""

        class CustomImpl:
            pass

        container = build_container(test_config)
        container.register_port("custom_port", CustomImpl())
        assert container.has_port("custom_port")
        assert isinstance(container.get_port("custom_port"), CustomImpl)
