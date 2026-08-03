"""策略元数据模型（见 docs/04 第 2 节）

定义 strategy.yaml 的字段和参数 schema。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from qps.core.constants import AssetClass, StrategyStyle


class StrategyMetadata(BaseModel):
    """策略元数据（strategy.yaml 内容）"""

    strategy_id: str = Field(description="策略 ID，如 trend_breakout")
    name: str = Field(description="显示名（中文允许）")
    style: StrategyStyle = Field(description="策略风格")
    instruments: list[str] = Field(default_factory=list, description="默认标的目标")
    interval: str = Field(default="5m", description="默认运行频率")
    asset_class_support: list[AssetClass] = Field(
        default=[AssetClass.PERPETUAL],
        description="支持的资产类别",
    )
    params_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema")
    features: list[str] = Field(default_factory=list, description="依赖的公共特征名列表")
    private_features: list[dict[str, Any]] = Field(
        default_factory=list,
        description="私有特征声明 [{name, input_window, params}]",
    )
    runtime_constraints: dict[str, Any] = Field(
        default_factory=dict,
        description="静态检查覆盖项",
    )
    version_note: str | None = Field(default=None, description="版本说明")

    model_config = {"frozen": True}

    @field_validator("interval")
    @classmethod
    def _validate_interval(cls, v: str) -> str:
        """验证 K 线间隔格式"""
        valid_intervals = ("1m", "5m", "15m", "1h", "1d")
        if v not in valid_intervals:
            raise ValueError(f"无效的 interval: {v}，支持: {valid_intervals}")
        return v

    @model_validator(mode="after")
    def _validate_params_schema(self) -> StrategyMetadata:
        """验证 params_schema 基本结构"""
        if self.params_schema:
            schema = self.params_schema
            if schema.get("type") != "object":
                raise ValueError("params_schema 必须包含 type: object")
            if "properties" not in schema:
                raise ValueError("params_schema 必须包含 properties 字段")
        return self

    def to_yaml_dict(self) -> dict[str, Any]:
        """转换为 YAML 字典（用于生成模板）"""
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "style": self.style.value,
            "instruments": self.instruments,
            "interval": self.interval,
            "asset_class_support": [ac.value for ac in self.asset_class_support],
            "params_schema": self.params_schema,
            "features": self.features,
            "runtime_constraints": self.runtime_constraints or {
                "allow_network": False,
                "allow_file_io": False,
                "allow_threads": False,
                "allow_subprocess": False,
                "allow_imports": ["numpy", "polars", "qps.core.models", "qps.core.sdk"],
            },
        }


class ParamSpec(BaseModel):
    """参数规格（params_schema 中单个属性的定义）"""

    name: str = Field(description="参数名")
    type: str = Field(description="类型：integer, number, string, boolean")
    required: bool = Field(default=True, description="是否必填")
    default: Any = Field(default=None, description="默认值")
    minimum: float | int | None = Field(default=None, description="最小值")
    maximum: float | int | None = Field(default=None, description="最大值")
    step: float | int | None = Field(default=None, description="步长（网格搜索用）")
    description: str = Field(default="", description="参数说明")

    def to_json_schema(self) -> dict[str, Any]:
        """转换为 JSON Schema 属性定义"""
        schema: dict[str, Any] = {"type": self.type}
        if self.minimum is not None:
            schema["minimum"] = self.minimum
        if self.maximum is not None:
            schema["maximum"] = self.maximum
        if self.step is not None:
            schema["step"] = self.step
        if self.description:
            schema["description"] = self.description
        return schema
