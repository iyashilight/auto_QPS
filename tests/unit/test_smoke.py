"""冒烟测试：验证项目脚手架可运行"""

from __future__ import annotations


def test_qps_package_importable() -> None:
    """qps 包可正常导入"""
    import qps

    assert hasattr(qps, "__version__")
    assert qps.__version__ == "0.1.0"


def test_core_modules_importable() -> None:
    """核心模块可正常导入"""
    from qps.core import constants, errors, events

    assert hasattr(constants, "AssetClass")
    assert hasattr(events, "EventType")
    assert hasattr(errors, "QpsError")


def test_infra_modules_importable() -> None:
    """基础设施模块可正常导入"""
    from qps.infra import config
    from qps.infra import logging as qps_logging

    assert hasattr(config, "Config")
    assert hasattr(qps_logging, "setup_logging")


def test_constants_enums() -> None:
    """常量枚举值正确"""
    from qps.core.constants import AssetClass, OrderStatus, StrategyLifecycleStatus

    assert AssetClass.SPOT == "SPOT"
    assert AssetClass.PERPETUAL == "PERPETUAL"
    assert OrderStatus.FILLED == "FILLED"
    assert StrategyLifecycleStatus.DRAFT == "DRAFT"


def test_event_types_defined() -> None:
    """事件类型常量已定义"""
    from qps.core.events import EventType

    assert EventType.BAR_UPDATED == "BAR_UPDATED"
    assert EventType.INTENT_CREATED == "INTENT_CREATED"
    assert EventType.RISK_EVENT == "RISK_EVENT"
    assert EventType.ORDER_UPDATED == "ORDER_UPDATED"


def test_error_hierarchy() -> None:
    """异常层级正确"""
    from qps.core.errors import (
        BrokerAuthError,
        BrokerBusinessError,
        BrokerError,
        BrokerNetworkError,
        BrokerRateLimitError,
        ConfigError,
        DataError,
        QpsError,
    )

    assert issubclass(ConfigError, QpsError)
    assert issubclass(DataError, QpsError)
    assert issubclass(BrokerError, QpsError)
    assert issubclass(BrokerAuthError, BrokerError)
    assert issubclass(BrokerRateLimitError, BrokerError)
    assert issubclass(BrokerNetworkError, BrokerError)
    assert issubclass(BrokerBusinessError, BrokerError)

    # 测试异常信息：str() 包含正确的 error_code
    err = ConfigError(detail="test error")
    assert err.error_code == "CONFIG_ERROR"
    assert "[CONFIG_ERROR]" in str(err)
    assert "test error" in str(err)

    # 测试子类 error_code 一致性
    auth_err = BrokerAuthError(detail="auth failed")
    assert auth_err.error_code == "BROKER_AUTH_ERROR"
    assert "[BROKER_AUTH_ERROR]" in str(auth_err)
