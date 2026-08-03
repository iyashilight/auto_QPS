"""业务异常类型定义（见 01 文档第 7 节）"""

from __future__ import annotations


class QpsError(Exception):
    """所有业务异常基类

    子类可通过类属性 _default_error_code 指定默认错误代码，
    避免在 __init__ 中重复设置。error_code 也可在构造时显式覆盖。
    """

    _default_error_code: str = "QPS_ERROR"

    def __init__(
        self,
        detail: str,
        cause: Exception | None = None,
        *,
        error_code: str | None = None,
    ) -> None:
        self.error_code = error_code if error_code is not None else self._default_error_code
        self.detail = detail
        self.cause = cause
        super().__init__(f"[{self.error_code}] {detail}")


class ConfigError(QpsError):
    """配置加载/校验失败"""

    _default_error_code = "CONFIG_ERROR"


class DataError(QpsError):
    """数据缺失/质量不合格/版本不匹配"""

    _default_error_code = "DATA_ERROR"


class BacktestError(QpsError):
    """回测配置/数据/引擎错误"""

    _default_error_code = "BACKTEST_ERROR"


class BrokerError(QpsError):
    """Broker 层错误基类"""

    _default_error_code = "BROKER_ERROR"


class BrokerAuthError(BrokerError):
    """认证失败（密钥过期等）"""

    _default_error_code = "BROKER_AUTH_ERROR"


class BrokerRateLimitError(BrokerError):
    """限频触发"""

    _default_error_code = "BROKER_RATE_LIMIT_ERROR"


class BrokerNetworkError(BrokerError):
    """网络中断/超时"""

    _default_error_code = "BROKER_NETWORK_ERROR"


class BrokerBusinessError(BrokerError):
    """交易所业务拒绝（拒单等），含原始原因"""

    _default_error_code = "BROKER_BUSINESS_ERROR"
