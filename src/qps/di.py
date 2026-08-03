"""依赖组装（模块化单体容器）

提供 build_container(config) 与 build_test_container()。
所有模块通过构造器注入端口，禁止模块内部自行 new 实现。
本阶段端口实现可为占位（抛 NotImplementedError 或返回未实现状态），
但容器结构必须可扩展。
"""

from __future__ import annotations

from typing import Any

from qps.infra.config import Config
from qps.infra.config_models import AppConfig, validate_config
from qps.infra.event_bus import EventBus
from qps.infra.storage.meta_db import MetaDB


class Container:
    """依赖注入容器

    持有全部端口实现实例和基础设施组件。
    通过属性访问各端口，支持运行时替换（测试用）。
    """

    def __init__(self, config: Config, app_config: AppConfig) -> None:
        self._config = config
        self._app_config = app_config
        self._event_bus = EventBus()
        self._meta_db: MetaDB | None = None

        # 端口实现占位：本阶段不实现业务逻辑
        self._ports: dict[str, Any] = {}

    @property
    def config(self) -> Config:
        """原始配置"""
        return self._config

    @property
    def app_config(self) -> AppConfig:
        """类型化配置"""
        return self._app_config

    @property
    def event_bus(self) -> EventBus:
        """事件总线"""
        return self._event_bus

    @property
    def meta_db(self) -> MetaDB:
        """元数据库"""
        if self._meta_db is None:
            self._meta_db = MetaDB(self._config.data_dir / "meta.db")
        return self._meta_db

    def get_port(self, port_name: str) -> Any:
        """获取端口实现

        Args:
            port_name: 端口名称（如 "data_provider"）

        Returns:
            端口实现实例

        Raises:
            KeyError: 端口未注册
        """
        if port_name not in self._ports:
            raise KeyError(
                f"端口 '{port_name}' 未注册。"
                f"已注册的端口: {list(self._ports.keys())}"
            )
        return self._ports[port_name]

    def register_port(self, port_name: str, implementation: Any) -> None:
        """注册端口实现

        Args:
            port_name: 端口名称
            implementation: 端口实现实例
        """
        self._ports[port_name] = implementation

    def has_port(self, port_name: str) -> bool:
        """检查端口是否已注册"""
        return port_name in self._ports

    def list_ports(self) -> list[str]:
        """列出所有已注册的端口"""
        return list(self._ports.keys())

    def replace_port(self, port_name: str, implementation: Any) -> Any:
        """替换端口实现（测试用，返回旧实现）

        Args:
            port_name: 端口名称
            implementation: 新实现

        Returns:
            旧实现（可能为 None）
        """
        old = self._ports.get(port_name)
        self._ports[port_name] = implementation
        return old


class _NotImplementedPort:
    """端口占位实现：所有方法抛 NotImplementedError"""

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        raise NotImplementedError(
            f"端口方法 '{name}' 尚未实现，将在后续里程碑中完成。"
        )


def build_container(config: Config) -> Container:
    """构建生产容器

    Args:
        config: 已加载的 Config 实例

    Returns:
        Container 实例（端口为占位实现）
    """
    app_config = validate_config(config.raw_data)
    container = Container(config, app_config)

    # 注册占位端口实现
    _port_names = [
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
    for name in _port_names:
        container.register_port(name, _NotImplementedPort())

    return container


def build_test_container(
    config: Config | None = None,
    port_overrides: dict[str, Any] | None = None,
) -> Container:
    """构建测试容器

    Args:
        config: 可选配置（默认为最小配置）
        port_overrides: 可选端口覆盖（用于测试替换）

    Returns:
        Container 实例（端口可被替换）
    """
    if config is None:
        # 最小测试配置
        import tempfile
        from pathlib import Path

        tmp_dir = Path(tempfile.mkdtemp())
        cfg_dir = tmp_dir / "configs"
        cfg_dir.mkdir()
        import yaml

        with open(cfg_dir / "default.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"project": {"name": "autoQps_test"}}, f)

        config = Config(config_dir=cfg_dir, env_vars={})

    container = build_container(config)

    # 应用端口覆盖
    if port_overrides:
        for name, impl in port_overrides.items():
            container.register_port(name, impl)

    return container
