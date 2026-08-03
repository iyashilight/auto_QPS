"""静态检查沙箱（见 docs/04 第 6 节）

AST 静态分析 + import 钩子，检查策略代码的安全性。
"""

from __future__ import annotations

import ast
import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SandboxViolation:
    """违规记录"""

    rule_id: str
    message: str
    line: int | None = None
    column: int | None = None
    severity: str = "ERROR"  # ERROR / WARNING


@dataclass
class SandboxResult:
    """检查结果"""

    passed: bool = True
    violations: list[SandboxViolation] = field(default_factory=list)

    def add_violation(self, violation: SandboxViolation) -> None:
        """添加违规记录"""
        self.passed = False
        self.violations.append(violation)

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "violations": [
                {
                    "rule_id": v.rule_id,
                    "message": v.message,
                    "line": v.line,
                    "column": v.column,
                    "severity": v.severity,
                }
                for v in self.violations
            ],
        }


# ── 禁止的模块 ──────────────────────────────────────────────

FORBIDDEN_MODULES = {
    # 网络
    "socket",
    "http",
    "websocket",
    "requests",
    "httpx",
    "aiohttp",
    "urllib",
    # 文件写
    "pathlib",  # 写操作
    # 进程/线程
    "threading",
    "multiprocessing",
    "subprocess",
    # 执行
    "builtins.eval",
    "builtins.exec",
    "builtins.compile",
    # 交易模块
    "qps.trading",
    "qps.broker",
    "qps.control",
}

# 允许的模块
ALLOWED_MODULES = {
    "numpy",
    "polars",
    "math",
    "statistics",
    "datetime",
    "typing",
    "dataclasses",
    "decimal",
    "collections",
    "itertools",
    "functools",
    "copy",
    "re",
    "json",
    "logging",
    "abc",
    "enum",
    "uuid",
    "hashlib",
    # qps 核心模块
    "qps.core.models",
    "qps.core.sdk",
    "qps.core.constants",
    "qps.core.errors",
}

# 禁止的函数调用
FORBIDDEN_FUNCTIONS = {
    "time.sleep",
    "os.fork",
    "os.system",
}


class ImportChecker(ast.NodeVisitor):
    """Import 检查器"""

    def __init__(self, result: SandboxResult) -> None:
        self.result = result

    def visit_Import(self, node: ast.Import) -> None:
        """检查 import 语句"""
        for alias in node.names:
            module_name = alias.name.split(".")[0]
            if module_name in FORBIDDEN_MODULES:
                self.result.add_violation(SandboxViolation(
                    rule_id="FORBIDDEN_IMPORT",
                    message=f"禁止导入模块: {alias.name}",
                    line=node.lineno,
                    column=node.col_offset,
                ))
            elif module_name not in ALLOWED_MODULES and not alias.name.startswith("qps.core"):
                # 非白名单且非 qps.core 子模块
                self.result.add_violation(SandboxViolation(
                    rule_id="UNAUTHORIZED_IMPORT",
                    message=f"未授权导入模块: {alias.name}",
                    line=node.lineno,
                    column=node.col_offset,
                    severity="WARNING",
                ))
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """检查 from ... import 语句"""
        if node.module:
            module_name = node.module.split(".")[0]
            if module_name in FORBIDDEN_MODULES:
                self.result.add_violation(SandboxViolation(
                    rule_id="FORBIDDEN_IMPORT",
                    message=f"禁止导入模块: {node.module}",
                    line=node.lineno,
                    column=node.col_offset,
                ))
            elif module_name not in ALLOWED_MODULES and not node.module.startswith("qps.core"):
                self.result.add_violation(SandboxViolation(
                    rule_id="UNAUTHORIZED_IMPORT",
                    message=f"未授权导入模块: {node.module}",
                    line=node.lineno,
                    column=node.col_offset,
                    severity="WARNING",
                ))
        self.generic_visit(node)


class CallChecker(ast.NodeVisitor):
    """函数调用检查器"""

    def __init__(self, result: SandboxResult) -> None:
        self.result = result

    def visit_Call(self, node: ast.Call) -> None:
        """检查函数调用"""
        # 获取函数名
        if isinstance(node.func, ast.Attribute):
            # obj.method() 形式
            if isinstance(node.func.value, ast.Name):
                func_name = f"{node.func.value.id}.{node.func.attr}"
                if func_name in FORBIDDEN_FUNCTIONS:
                    self.result.add_violation(SandboxViolation(
                        rule_id="FORBIDDEN_FUNCTION",
                        message=f"禁止调用函数: {func_name}",
                        line=node.lineno,
                        column=node.col_offset,
                    ))
        elif isinstance(node.func, ast.Name):
            # func() 形式
            if node.func.id in ("eval", "exec", "compile"):
                self.result.add_violation(SandboxViolation(
                    rule_id="FORBIDDEN_FUNCTION",
                    message=f"禁止调用函数: {node.func.id}",
                    line=node.lineno,
                    column=node.col_offset,
                ))
        self.generic_visit(node)


class NameChecker(ast.NodeVisitor):
    """名称检查器（检测内置函数使用）"""

    def __init__(self, result: SandboxResult) -> None:
        self.result = result

    def visit_Name(self, node: ast.Name) -> None:
        """检查变量名（检测 eval/exec/compile 等内置函数引用）"""
        if node.id in ("eval", "exec", "compile"):
            self.result.add_violation(SandboxViolation(
                rule_id="FORBIDDEN_NAME",
                message=f"禁止使用内置函数: {node.id}",
                line=node.lineno,
                column=node.col_offset,
            ))
        self.generic_visit(node)


class FileAccessChecker(ast.NodeVisitor):
    """文件访问检查器"""

    def __init__(self, result: SandboxResult) -> None:
        self.result = result

    def visit_Call(self, node: ast.Call) -> None:
        """检查 open() 调用"""
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            self.result.add_violation(SandboxViolation(
                rule_id="FILE_ACCESS",
                message="禁止使用 open() 进行文件操作",
                line=node.lineno,
                column=node.col_offset,
            ))
        self.generic_visit(node)


def check_code(source: str) -> SandboxResult:
    """检查源代码

    Args:
        source: Python 源代码字符串

    Returns:
        检查结果
    """
    result = SandboxResult()

    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        result.add_violation(SandboxViolation(
            rule_id="SYNTAX_ERROR",
            message=f"语法错误: {e}",
            line=e.lineno,
        ))
        return result

    # 运行所有检查器
    ImportChecker(result).visit(tree)
    CallChecker(result).visit(tree)
    NameChecker(result).visit(tree)
    FileAccessChecker(result).visit(tree)

    return result


def check_file(file_path: Path) -> SandboxResult:
    """检查文件

    Args:
        file_path: Python 文件路径

    Returns:
        检查结果
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except OSError as e:
        result = SandboxResult()
        result.add_violation(SandboxViolation(
            rule_id="FILE_READ_ERROR",
            message=f"读取文件失败: {e}",
        ))
        return result

    return check_code(source)


def check_directory(dir_path: Path) -> SandboxResult:
    """检查目录下所有 Python 文件

    Args:
        dir_path: 目录路径

    Returns:
        检查结果（汇总所有文件）
    """
    all_results: list[SandboxResult] = []

    for py_file in sorted(dir_path.glob("*.py")):
        if py_file.name.startswith("test_"):
            # 测试文件跳过检查
            continue
        result = check_file(py_file)
        all_results.append(result)

    # 合并结果
    merged = SandboxResult()
    for r in all_results:
        merged.violations.extend(r.violations)
        if not r.passed:
            merged.passed = False

    return merged


class Sandbox:
    """沙箱类

    用于在受限环境中加载和执行策略代码。
    """

    def __init__(self, allowed_modules: set[str] | None = None) -> None:
        """初始化沙箱

        Args:
            allowed_modules: 额外允许的模块集合
        """
        self._allowed_modules = ALLOWED_MODULES.copy()
        if allowed_modules:
            self._allowed_modules.update(allowed_modules)

    def check(self, source: str) -> SandboxResult:
        """检查源代码

        Args:
            source: Python 源代码字符串

        Returns:
            检查结果
        """
        return check_code(source)

    def check_file(self, file_path: Path) -> SandboxResult:
        """检查文件

        Args:
            file_path: Python 文件路径

        Returns:
            检查结果
        """
        return check_file(file_path)

    def check_directory(self, dir_path: Path) -> SandboxResult:
        """检查目录

        Args:
            dir_path: 目录路径

        Returns:
            检查结果
        """
        return check_directory(dir_path)

    def load_module(self, name: str, path: Path) -> Any:
        """加载模块（受限制）

        Args:
            name: 模块名
            path: 模块文件路径

        Returns:
            加载的模块

        Raises:
            SandboxViolation: 检查失败时抛出
        """
        # 先检查文件
        result = self.check_file(path)
        if not result.passed:
            raise ValueError(f"代码检查失败: {result.to_dict()}")

        # 加载模块
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"无法加载模块: {name}")

        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)

        return module