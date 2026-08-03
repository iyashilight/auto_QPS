"""沙箱静态检查测试（见 docs/04 第 6 节）

测试五类违规拦截、__future__ 放行、allow_imports 生效、__import__ 拦截。
"""

from __future__ import annotations

from pathlib import Path

from qps.core.sandbox import (
    Sandbox,
    check_code,
)


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
