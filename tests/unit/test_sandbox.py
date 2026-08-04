"""沙箱静态检查测试（见 docs/04 第 6 节）

测试五类违规拦截、__future__ 放行、allow_imports 生效、__import__ 拦截。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from qps.core.sandbox import (
    Sandbox,
    check_code,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestForbiddenImports:
    """禁止导入模块测试"""

    def test_network_import_socket(self) -> None:
        """网络 import - socket"""
        result = check_code("import socket")
        assert not result.passed
        assert any(v.rule_id == "FORBIDDEN_IMPORT" for v in result.violations)

    def test_network_import_requests(self) -> None:
        """网络 import - requests"""
        result = check_code("import requests")
        assert not result.passed
        assert any(v.rule_id == "FORBIDDEN_IMPORT" for v in result.violations)

    def test_network_import_httpx(self) -> None:
        """网络 import - httpx"""
        result = check_code("import httpx")
        assert not result.passed

    def test_network_import_websocket(self) -> None:
        """网络 import - websocket"""
        result = check_code("import websocket")
        assert not result.passed

    def test_file_write_import_pathlib(self) -> None:
        """文件写 import - pathlib"""
        result = check_code("from pathlib import Path")
        assert not result.passed

    def test_threading_import(self) -> None:
        """线程 import - threading"""
        result = check_code("import threading")
        assert not result.passed

    def test_subprocess_import(self) -> None:
        """进程 import - subprocess"""
        result = check_code("import subprocess")
        assert not result.passed

    def test_trading_module_import(self) -> None:
        """交易模块 import - qps.trading"""
        result = check_code("from qps.trading import something")
        assert not result.passed

    def test_broker_module_import(self) -> None:
        """交易模块 import - qps.broker"""
        result = check_code("from qps.broker import something")
        assert not result.passed


class TestForbiddenFunctions:
    """禁止函数调用测试"""

    def test_time_sleep(self) -> None:
        """time.sleep 调用"""
        result = check_code("import time\ntime.sleep(1)")
        assert not result.passed
        assert any(v.rule_id == "FORBIDDEN_FUNCTION" for v in result.violations)

    def test_eval_function(self) -> None:
        """eval() 调用"""
        result = check_code("eval('1+1')")
        assert not result.passed

    def test_exec_function(self) -> None:
        """exec() 调用"""
        result = check_code("exec('x=1')")
        assert not result.passed

    def test_compile_function(self) -> None:
        """compile() 调用"""
        result = check_code("compile('1+1', '<string>', 'eval')")
        assert not result.passed


class TestFileAccess:
    """文件访问测试"""

    def test_open_call(self) -> None:
        """open() 调用"""
        result = check_code("f = open('test.txt')")
        assert not result.passed
        assert any(v.rule_id == "FILE_ACCESS" for v in result.violations)


class TestFutureImports:
    """__future__ import 放行测试"""

    def test_future_annotations(self) -> None:
        """from __future__ import annotations 必须放行"""
        source = "from __future__ import annotations\n\nclass Foo:\n    pass"
        result = check_code(source)
        assert result.passed

    def test_future_with_other_code(self) -> None:
        """__future__ + 合法代码"""
        source = """from __future__ import annotations

from qps.core.sdk import BaseStrategy

class Strategy(BaseStrategy):
    def initialize(self, ctx):
        pass
"""
        result = check_code(source)
        assert result.passed


class TestAllowedImports:
    """允许导入测试"""

    def test_numpy_import(self) -> None:
        """numpy import 允许"""
        result = check_code("import numpy as np")
        assert result.passed

    def test_polars_import(self) -> None:
        """polars import 允许"""
        result = check_code("import polars as pl")
        assert result.passed

    def test_math_import(self) -> None:
        """math import 允许"""
        result = check_code("import math")
        assert result.passed

    def test_datetime_import(self) -> None:
        """datetime import 允许"""
        result = check_code("from datetime import datetime")
        assert result.passed

    def test_qps_core_models_import(self) -> None:
        """qps.core.models import 允许"""
        result = check_code("from qps.core.models import Bar")
        assert result.passed

    def test_qps_core_sdk_import(self) -> None:
        """qps.core.sdk import 允许"""
        result = check_code("from qps.core.sdk import BaseStrategy")
        assert result.passed


class TestDynamicImport:
    """动态导入拦截测试"""

    def test_dunder_import_call(self) -> None:
        """__import__() 调用拦截"""
        result = check_code("mod = __import__('os')")
        assert not result.passed
        assert any(v.rule_id == "DYNAMIC_IMPORT" for v in result.violations)


class TestRuntimeConstraints:
    """runtime_constraints.allow_imports 生效测试"""

    def test_custom_allowed_modules(self) -> None:
        """自定义 allow_imports 生效"""
        sandbox = Sandbox(allowed_modules={"pandas", "scipy"})
        result = sandbox.check("import pandas as pd")
        assert result.passed

    def test_custom_allowed_modules_not_affect_forbidden(self) -> None:
        """自定义 allow_imports 不影响禁止模块"""
        sandbox = Sandbox(allowed_modules={"pandas"})
        result = sandbox.check("import socket")
        assert not result.passed


class TestSandboxClass:
    """Sandbox 类测试"""

    def test_check_file(self, tmp_path: Path) -> None:
        """check_file 方法测试"""
        file_path = tmp_path / "test.py"
        file_path.write_text("import socket\n")
        sandbox = Sandbox()
        result = sandbox.check_file(file_path)
        assert not result.passed

    def test_check_directory(self, tmp_path: Path) -> None:
        """check_directory 方法测试"""
        # 创建合法文件
        valid_file = tmp_path / "valid.py"
        valid_file.write_text("import math\n")

        # 创建非法文件
        invalid_file = tmp_path / "invalid.py"
        invalid_file.write_text("import socket\n")

        sandbox = Sandbox()
        result = sandbox.check_directory(tmp_path)
        assert not result.passed
        assert len(result.violations) >= 1


class TestStrategyPackageSelfImport:
    """策略包自导入放行测试"""

    def test_strategy_package_import_allowed(self) -> None:
        """策略包自身 import 放行"""
        source = "import demo_mean_reversion"
        result = check_code(source, strategy_package="demo_mean_reversion")
        assert result.passed

    def test_strategy_package_from_import_allowed(self) -> None:
        """from <strategy_package>.xxx import 放行"""
        source = "from demo_mean_reversion.strategy import Strategy"
        result = check_code(source, strategy_package="demo_mean_reversion")
        assert result.passed

    def test_strategy_package_init_import_allowed(self) -> None:
        """策略包 __init__.py 中的自导入放行"""
        source = """\"\"\"演示均值回归策略\"\"\"

from demo_mean_reversion.strategy import Strategy

__all__ = ["Strategy"]
"""
        result = check_code(source, strategy_package="demo_mean_reversion")
        assert result.passed

    def test_strategy_package_submodule_import_allowed(self) -> None:
        """策略包子模块 import 放行"""
        source = "from demo_mean_reversion.features import compute_returns"
        result = check_code(source, strategy_package="demo_mean_reversion")
        assert result.passed

    def test_non_strategy_package_import_still_blocked(self) -> None:
        """非策略包的未授权 import 仍然被拦截"""
        source = "import some_other_package"
        result = check_code(source, strategy_package="demo_mean_reversion")
        assert not result.passed
        assert any(v.rule_id == "UNAUTHORIZED_IMPORT" for v in result.violations)

    def test_strategy_package_does_not_override_forbidden(self) -> None:
        """策略包放行不影响禁止模块拦截"""
        source = "import socket"
        result = check_code(source, strategy_package="socket")
        # 即使 strategy_package 名与禁止模块同名，禁止模块仍优先拦截
        assert not result.passed
        assert any(v.rule_id == "FORBIDDEN_IMPORT" for v in result.violations)

    def test_sandbox_class_with_strategy_package(self) -> None:
        """Sandbox 类 strategy_package 参数生效"""
        sandbox = Sandbox(strategy_package="my_strategy")
        result = sandbox.check("from my_strategy.strategy import Strategy")
        assert result.passed

    def test_check_directory_with_strategy_package(self, tmp_path: Path) -> None:
        """check_directory 传入 strategy_package 整包通过"""
        # 创建策略包目录结构
        pkg_dir = tmp_path / "my_strategy"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text(
            "from my_strategy.strategy import Strategy\n__all__ = ['Strategy']\n"
        )
        (pkg_dir / "strategy.py").write_text(
            "from __future__ import annotations\nfrom qps.core.sdk import BaseStrategy\n\nclass Strategy(BaseStrategy):\n    pass\n"
        )

        sandbox = Sandbox(strategy_package="my_strategy")
        result = sandbox.check_directory(pkg_dir)
        assert result.passed
