"""YAML 解析与参数 schema 校验测试（见 docs/04 第 2、7 节）

测试必填缺失、类型错误、超范围、未知参数、非法 schema 结构。
"""

from __future__ import annotations

import pytest

from qps.research.metadata import StrategyMetadata
from qps.research.yaml_parser import (
    ParamSchemaValidator,
    SchemaValidationError,
    validate_params,
)


class TestParamSchemaValidator:
    """参数 schema 校验器测试"""

    def test_valid_params(self) -> None:
        """合法参数通过"""
        schema = {
            "type": "object",
            "required": ["lookback"],
            "properties": {
                "lookback": {"type": "integer", "minimum": 20, "maximum": 200},
                "atr_mult": {"type": "number", "minimum": 1.0, "maximum": 5.0},
            },
        }
        validator = ParamSchemaValidator({"lookback": 50, "atr_mult": 2.0}, schema)
        errors = validator.validate()
        assert errors == []

    def test_missing_required_param(self) -> None:
        """缺少必填参数"""
        schema = {
            "type": "object",
            "required": ["lookback"],
            "properties": {
                "lookback": {"type": "integer"},
            },
        }
        validator = ParamSchemaValidator({}, schema)
        errors = validator.validate()
        assert len(errors) == 1
        assert "缺少必填参数: lookback" in errors[0]

    def test_unknown_param(self) -> None:
        """未知参数"""
        schema = {
            "type": "object",
            "required": [],
            "properties": {
                "lookback": {"type": "integer"},
            },
        }
        validator = ParamSchemaValidator({"lookback": 50, "unknown": 100}, schema)
        errors = validator.validate()
        assert len(errors) >= 1
        assert any("未知参数" in e for e in errors)

    def test_type_error(self) -> None:
        """类型错误"""
        schema = {
            "type": "object",
            "required": [],
            "properties": {
                "lookback": {"type": "integer"},
            },
        }
        validator = ParamSchemaValidator({"lookback": "not_an_int"}, schema)
        errors = validator.validate()
        assert len(errors) >= 1
        assert any("类型错误" in e for e in errors)

    def test_value_out_of_range(self) -> None:
        """值超出范围"""
        schema = {
            "type": "object",
            "required": [],
            "properties": {
                "lookback": {"type": "integer", "minimum": 20, "maximum": 200},
            },
        }
        # 小于最小值
        validator = ParamSchemaValidator({"lookback": 10}, schema)
        errors = validator.validate()
        assert len(errors) >= 1
        assert any("小于最小值" in e for e in errors)

        # 大于最大值
        validator = ParamSchemaValidator({"lookback": 300}, schema)
        errors = validator.validate()
        assert len(errors) >= 1
        assert any("大于最大值" in e for e in errors)

    def test_invalid_schema_structure(self) -> None:
        """非法 schema 结构"""
        schema = {"invalid": "structure"}
        validator = ParamSchemaValidator({}, schema)
        errors = validator.validate()
        assert len(errors) >= 1
        assert "schema.type 必须是 object" in errors[0]

    def test_string_type_validation(self) -> None:
        """字符串类型校验"""
        schema = {
            "type": "object",
            "required": [],
            "properties": {
                "name": {"type": "string"},
            },
        }
        validator = ParamSchemaValidator({"name": "test"}, schema)
        errors = validator.validate()
        assert errors == []

        validator = ParamSchemaValidator({"name": 123}, schema)
        errors = validator.validate()
        assert len(errors) >= 1

    def test_boolean_type_validation(self) -> None:
        """布尔类型校验"""
        schema = {
            "type": "object",
            "required": [],
            "properties": {
                "enabled": {"type": "boolean"},
            },
        }
        validator = ParamSchemaValidator({"enabled": True}, schema)
        errors = validator.validate()
        assert errors == []

        validator = ParamSchemaValidator({"enabled": "yes"}, schema)
        errors = validator.validate()
        assert len(errors) >= 1


class TestValidateParamsFunction:
    """validate_params 函数测试"""

    def test_valid_params_no_error(self) -> None:
        """合法参数不抛出异常"""
        schema = {
            "type": "object",
            "required": ["lookback"],
            "properties": {
                "lookback": {"type": "integer"},
            },
        }
        validate_params({"lookback": 50}, schema)  # 不应抛出

    def test_invalid_params_raises(self) -> None:
        """非法参数抛出 SchemaValidationError"""
        schema = {
            "type": "object",
            "required": ["lookback"],
            "properties": {
                "lookback": {"type": "integer"},
            },
        }
        with pytest.raises(SchemaValidationError):
            validate_params({}, schema)


class TestStrategyMetadata:
    """StrategyMetadata 模型测试"""

    def test_valid_metadata(self) -> None:
        """合法元数据"""
        metadata = StrategyMetadata(
            strategy_id="test_strategy",
            name="测试策略",
            style="TREND",
            instruments=["BTC-USDT-PERP"],
            interval="5m",
            params_schema={
                "type": "object",
                "properties": {},
            },
        )
        assert metadata.strategy_id == "test_strategy"
        assert metadata.style.value == "TREND"

    def test_invalid_interval_rejected(self) -> None:
        """无效 interval 拒绝"""
        with pytest.raises(ValueError, match="无效的 interval"):
            StrategyMetadata(
                strategy_id="test",
                name="测试",
                style="TREND",
                interval="invalid",
                params_schema={"type": "object", "properties": {}},
            )

    def test_invalid_params_schema_rejected(self) -> None:
        """无效 params_schema 拒绝"""
        with pytest.raises(ValueError, match="params_schema 必须包含 type: object"):
            StrategyMetadata(
                strategy_id="test",
                name="测试",
                style="TREND",
                params_schema={"invalid": "schema"},
            )

    def test_to_yaml_dict(self) -> None:
        """转换为 YAML 字典"""
        metadata = StrategyMetadata(
            strategy_id="test_strategy",
            name="测试策略",
            style="TREND",
            instruments=["BTC-USDT-PERP"],
            params_schema={
                "type": "object",
                "properties": {},
            },
        )
        yaml_dict = metadata.to_yaml_dict()
        assert yaml_dict["strategy_id"] == "test_strategy"
        assert yaml_dict["style"] == "TREND"
        assert "runtime_constraints" in yaml_dict
