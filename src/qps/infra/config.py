"""分层配置加载（见 06 文档第 1~3 节）

加载顺序：configs/default.yaml → configs/<mode>.yaml → 环境变量 QPS_*
支持 ${VAR} 占位符解析，敏感字段只走环境变量。
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

from qps.core.errors import ConfigError

# 环境变量前缀
_ENV_PREFIX = "QPS_"

# 占位符模式：${VAR_NAME}
_PLACEHOLDER_PATTERN = re.compile(r"\$\{(\w+)\}")

# 环境变量到配置路径的映射（统一使用双下划线分隔嵌套）
# 特殊映射仅用于与 YAML 路径不一致的变量
_ENV_MAPPING: dict[str, list[str]] = {
    "QPS_MODE": ["_mode"],
    "QPS_DATA_DIR": ["project", "data_dir"],
    "QPS_LOG_LEVEL": ["logging", "level"],
}

# 已知布尔类型字段的配置路径（仅对这些字段做布尔转换）
_BOOLEAN_PATHS: set[str] = {
    "exchange.adapters.binance.testnet",
    "market_data.collect_1m",
    "risk.market.freeze_on_ws_disconnect",
    "paper.partial_fill",
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """深度合并两个字典，override 中的值覆盖 base 中的同名键

    使用 copy.deepcopy 保证返回值与输入完全独立，不会因共享引用而导致意外修改。
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolve_placeholders(value: Any, env_vars: dict[str, str]) -> Any:
    """递归解析 ${VAR} 占位符

    如果环境变量不存在，保留原始占位符字符串。
    """
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return env_vars.get(var_name, match.group(0))

        return _PLACEHOLDER_PATTERN.sub(_replace, value)

    if isinstance(value, dict):
        return {k: _resolve_placeholders(v, env_vars) for k, v in value.items()}

    if isinstance(value, list):
        return [_resolve_placeholders(item, env_vars) for item in value]

    return value


def _load_yaml_file(path: Path, *, required: bool = False) -> dict[str, Any]:
    """加载 YAML 文件

    Args:
        path: YAML 文件路径
        required: 是否必须存在。若为 True 且文件不存在则抛出 ConfigError
    """
    if not path.exists():
        if required:
            raise ConfigError(detail=f"必需的配置文件不存在: {path}")
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
            return data if isinstance(data, dict) else {}
    except yaml.YAMLError as e:
        raise ConfigError(detail=f"YAML 解析失败: {path}", cause=e) from e
    except OSError as e:
        raise ConfigError(detail=f"配置文件读取失败: {path}", cause=e) from e


def _parse_boolean(value: str) -> bool | None:
    """解析布尔值字符串，仅对明确的布尔字符串做转换

    注意：不再将 '1'/'0' 转为布尔值，避免破坏数字型环境变量。
    """
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    return None


def _env_vars_to_config(env_vars: dict[str, str]) -> dict[str, Any]:
    """将 QPS_* 环境变量转换为嵌套配置字典

    规则：
    - 特殊映射（_ENV_MAPPING）处理与 YAML 路径不一致的变量
    - 通用规则：QPS_SECTION__SUBSECTION__KEY → section.subsection.key（双下划线分隔嵌套）
    - 仅对 _BOOLEAN_PATHS 中的字段做布尔转换，其他字段保留原始字符串
    """
    config: dict[str, Any] = {}

    for env_key, value in env_vars.items():
        if not env_key.startswith(_ENV_PREFIX):
            continue

        # 确定路径
        if env_key in _ENV_MAPPING:
            path_parts = _ENV_MAPPING[env_key]
        else:
            # 通用规则：QPS_SECTION__KEY → section.key（双下划线分隔嵌套）
            stripped = env_key[len(_ENV_PREFIX):]
            path_parts = stripped.lower().split("__")

        # 值转换：仅对已知布尔字段做转换
        parsed_value: Any = value
        dot_path = ".".join(path_parts)
        if dot_path in _BOOLEAN_PATHS:
            bool_val = _parse_boolean(value)
            if bool_val is not None:
                parsed_value = bool_val

        # 构建嵌套字典（防止标量/嵌套路径冲突）
        current = config
        for part in path_parts[:-1]:
            if part not in current:
                current[part] = {}
            elif not isinstance(current[part], dict):
                # 已有标量值与嵌套路径冲突，用新 dict 覆盖
                current[part] = {}
            current = current[part]
        current[path_parts[-1]] = parsed_value

    return config


class Config:
    """分层配置管理器

    加载顺序：default.yaml → <mode>.yaml → 环境变量 QPS_*
    """

    def __init__(
        self,
        config_dir: Path | str | None = None,
        mode: str | None = None,
        env_vars: dict[str, str] | None = None,
    ) -> None:
        """初始化配置

        Args:
            config_dir: 配置文件目录，默认为项目根目录下的 configs/
            mode: 运行模式（paper/production），也可通过 QPS_MODE 环境变量指定
            env_vars: 环境变量字典（测试注入用），默认读取 os.environ
        """
        self._env_vars = env_vars if env_vars is not None else dict(os.environ)
        self._config_dir = Path(config_dir) if config_dir else self._find_config_dir()

        # 计算环境变量配置（只计算一次，后续复用）
        self._env_config = _env_vars_to_config(self._env_vars)

        # 确定运行模式
        self._mode = mode if mode is not None else self._env_config.get("_mode", "")

        # 三级加载
        self._data = self._load_all()

    def _find_config_dir(self) -> Path:
        """查找配置目录"""
        # 从当前目录向上查找
        current = Path.cwd()
        for _ in range(10):
            candidate = current / "configs"
            if candidate.is_dir():
                return candidate
            parent = current.parent
            if parent == current:
                break
            current = parent
        return Path.cwd() / "configs"

    def _load_all(self) -> dict[str, Any]:
        """执行三级加载与合并"""
        # 第一级：default.yaml（必须存在）
        default_data = _load_yaml_file(self._config_dir / "default.yaml", required=True)

        # 第二级：mode.yaml
        mode_data: dict[str, Any] = {}
        if self._mode:
            mode_data = _load_yaml_file(self._config_dir / f"{self._mode}.yaml")

        # 合并
        merged = _deep_merge(default_data, mode_data)
        env_config_clean = {k: v for k, v in self._env_config.items() if k != "_mode"}
        merged = _deep_merge(merged, env_config_clean)

        # 解析占位符
        merged = _resolve_placeholders(merged, self._env_vars)

        return dict(merged)

    def get(self, key_path: str, default: Any = None) -> Any:
        """获取配置值，支持点号分隔路径

        Args:
            key_path: 配置路径，如 "project.data_dir"
            default: 默认值

        Returns:
            配置值，路径不存在时返回 default
        """
        parts = key_path.split(".")
        current: Any = self._data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return default
        return current

    @property
    def mode(self) -> str:
        """当前运行模式"""
        return self._mode

    @property
    def data_dir(self) -> Path:
        """数据目录"""
        data_dir = self.get("project.data_dir", "./data")
        return Path(data_dir)

    @property
    def raw_data(self) -> dict[str, Any]:
        """原始配置数据（调试用）"""
        return copy.deepcopy(self._data)

    def reload(self) -> None:
        """重新加载配置

        如果构造时注入了 env_vars，reload 会重新使用注入的值而非 os.environ，
        保证测试隔离性。调用方如需读取最新环境变量，应重新构造 Config 实例。
        """
        self._env_config = _env_vars_to_config(self._env_vars)
        self._mode = self._env_config.get("_mode", self._mode)
        self._data = self._load_all()
