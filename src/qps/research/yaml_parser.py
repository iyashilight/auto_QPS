"""strategy.yaml 解析与校验（见 docs/04 第 2、7 节）

负责解析 strategy.yaml 文件，校验字段和 params_schema。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from pathlib import Path

from qps.core.errors import ConfigError
from qps.research.metadata import StrategyMetadata


class SchemaValidationError(Exception):
    """参数 schema 校验错误"""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("参数 schema 校验失败:\n" + "\n".join(f"  - {e}" for e in errors))


class YamlParser:
    """YAML 解析器"""

    @staticmethod
    def parse(file_path: Path) -> dict[str, Any]:
        """解析 YAML 文件

        Args:
            file_path: YAML 文件路径

        Returns:
            解析后的字典

        Raises:
            ConfigError: 解析失败时抛出
        """
        try:
            content = file_path.read_text(encoding="utf-8")
            data = yaml.safe_load(content)
            if not isinstance(data, dict):
                raise ConfigError(detail=f"YAML 内容不是字典: {file_path}")
            return data
        except yaml.YAMLError as e:
            raise ConfigError(detail=f"YAML 解析失败: {file_path}", cause=e) from e
        except OSError as e:
            raise ConfigError(detail=f"读取文件失败: {file_path}", cause=e) from e

    @staticmethod
    def validate_metadata(data: dict[str, Any]) -> StrategyMetadata:
        """校验元数据

        Args:
            data: 解析后的字典

        Returns:
            StrategyMetadata 实例

        Raises:
            ConfigError: 校验失败时抛出
        """
        try:
            return StrategyMetadata(**data)
        except Exception as e:
            raise ConfigError(detail=f"元数据校验失败: {e}") from e


class ParamSchemaValidator:
    """参数 schema 校验器（JSON Schema draft-07 子集）"""

    def __init__(self, params: dict[str, Any], schema: dict[str, Any]) -> None:
        """初始化校验器

        Args:
            params: 实际参数值
            schema: JSON Schema
        """
        self.params = params
        self.schema = schema

    def validate(self) -> list[str]:
        """校验参数

        Returns:
            错误列表，空表示通过
        """
        errors: list[str] = []

        # 检查 schema 结构
        if self.schema.get("type") != "object":
            errors.append("schema.type 必须是 object")
            return errors

        properties = self.schema.get("properties", {})
        required = set(self.schema.get("required", []))

        # 检查必填参数
        for param_name in required:
            if param_name not in self.params:
                errors.append(f"缺少必填参数: {param_name}")

        # 检查每个参数
        for param_name, param_value in self.params.items():
            if param_name not in properties:
                errors.append(f"未知参数: {param_name}")
                continue

            prop_schema = properties[param_name]
            error = self._validate_property(param_name, param_value, prop_schema)
            if error:
                errors.append(error)

        return errors

    def _validate_property(self, name: str, value: Any, schema: dict[str, Any]) -> str | None:
        """校验单个属性"""
        # 类型检查
        expected_type = schema.get("type")
        if expected_type:
            type_error = self._check_type(name, value, expected_type)
            if type_error:
                return type_error

        # 范围检查
        if isinstance(value, (int, float)):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")

            if minimum is not None and value < minimum:
                return f"参数 {name} 值 {value} 小于最小值 {minimum}"

            if maximum is not None and value > maximum:
                return f"参数 {name} 值 {value} 大于最大值 {maximum}"

        return None

    def _check_type(self, name: str, value: Any, expected_type: str) -> str | None:
        """检查类型"""
        type_map: dict[str, type | tuple[type, ...]] = {
            "integer": int,
            "number": (int, float),
            "string": str,
            "boolean": bool,
        }

        expected = type_map.get(expected_type)
        if expected is None:
            return None

        if not isinstance(value, expected):
            return f"参数 {name} 类型错误: 期望 {expected_type}, 实际 {type(value).__name__}"

        return None


def load_and_validate_yaml(file_path: Path) -> tuple[StrategyMetadata, dict[str, Any]]:
    """加载并校验 YAML 文件

    Args:
        file_path: YAML 文件路径

    Returns:
        (StrategyMetadata, 原始数据字典)

    Raises:
        ConfigError: 加载或校验失败时抛出
    """
    data = YamlParser.parse(file_path)
    metadata = YamlParser.validate_metadata(data)
    return metadata, data


def validate_params(params: dict[str, Any], schema: dict[str, Any]) -> None:
    """校验参数是否符合 schema

    Args:
        params: 参数字典
        schema: JSON Schema

    Raises:
        SchemaValidationError: 校验失败时抛出
    """
    validator = ParamSchemaValidator(params, schema)
    errors = validator.validate()
    if errors:
        raise SchemaValidationError(errors)


def validate_schema_structure(schema: dict[str, Any]) -> None:
    """校验 params_schema 结构本身是否合法（不验证具体参数值）

    Args:
        schema: JSON Schema 字典

    Raises:
        SchemaValidationError: schema 结构非法时抛出
    """
    errors: list[str] = []

    # 检查顶层 type
    if schema.get("type") != "object":
        errors.append("schema.type 必须是 object")

    # 检查 properties 存在且为字典
    properties = schema.get("properties")
    if properties is not None and not isinstance(properties, dict):
        errors.append("schema.properties 必须是字典")

    # 检查每个 property 的 type 是否合法
    valid_types = {"integer", "number", "string", "boolean", "array", "object"}
    if isinstance(properties, dict):
        for prop_name, prop_schema in properties.items():
            if isinstance(prop_schema, dict):
                prop_type = prop_schema.get("type")
                if prop_type and prop_type not in valid_types:
                    errors.append(f"属性 {prop_name}.type 必须是有效类型之一: {valid_types}")

    if errors:
        raise SchemaValidationError(errors)
