"""配置加载单元测试：验证三级覆盖顺序

default.yaml → <mode>.yaml → 环境变量 QPS_*
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from qps.infra.config import (
    Config,
    _deep_merge,
    _env_vars_to_config,
    _resolve_placeholders,
)

# ── 辅助函数 ──────────────────────────────────────────────


def _write_yaml(path: Path, data: dict) -> None:
    """写入 YAML 文件"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True)


# ── 深度合并测试 ──────────────────────────────────────────


class TestDeepMerge:
    """深度合并逻辑测试"""

    def test_simple_override(self) -> None:
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_override(self) -> None:
        base = {"outer": {"a": 1, "b": 2}}
        override = {"outer": {"b": 3, "c": 4}}
        result = _deep_merge(base, override)
        assert result == {"outer": {"a": 1, "b": 3, "c": 4}}

    def test_deep_nested(self) -> None:
        base = {"l1": {"l2": {"l3": "old"}}}
        override = {"l1": {"l2": {"l3": "new", "extra": 1}}}
        result = _deep_merge(base, override)
        assert result == {"l1": {"l2": {"l3": "new", "extra": 1}}}

    def test_override_dict_with_scalar(self) -> None:
        base = {"key": {"nested": 1}}
        override = {"key": "scalar"}
        result = _deep_merge(base, override)
        assert result == {"key": "scalar"}

    def test_merge_independence(self) -> None:
        """合并结果与原始输入完全独立，修改结果不影响原始数据"""
        base = {"risk": {"max_loss": 500}}
        override: dict[str, object] = {}
        result = _deep_merge(base, override)
        result["risk"]["max_loss"] = 0
        # 原始 base 不受影响（深拷贝保证独立性）
        assert base["risk"]["max_loss"] == 500


# ── 占位符解析测试 ────────────────────────────────────────


class TestResolvePlaceholders:
    """占位符解析测试"""

    def test_simple_placeholder(self) -> None:
        result = _resolve_placeholders("${MY_VAR}", {"MY_VAR": "hello"})
        assert result == "hello"

    def test_missing_placeholder_preserved(self) -> None:
        result = _resolve_placeholders("${MISSING_VAR}", {})
        assert result == "${MISSING_VAR}"

    def test_nested_dict_placeholder(self) -> None:
        data = {"key": "${API_KEY}", "other": "plain"}
        result = _resolve_placeholders(data, {"API_KEY": "secret123"})
        assert result == {"key": "secret123", "other": "plain"}

    def test_list_placeholder(self) -> None:
        data = ["${VAR1}", "static", "${VAR2}"]
        result = _resolve_placeholders(data, {"VAR1": "a", "VAR2": "b"})
        assert result == ["a", "static", "b"]

    def test_mixed_string_placeholder(self) -> None:
        result = _resolve_placeholders("prefix_${VAR}_suffix", {"VAR": "mid"})
        assert result == "prefix_mid_suffix"


# ── 环境变量转换测试 ──────────────────────────────────────


class TestEnvVarsToConfig:
    """环境变量到配置字典转换测试"""

    def test_qps_mode(self) -> None:
        result = _env_vars_to_config({"QPS_MODE": "paper"})
        assert result.get("_mode") == "paper"

    def test_qps_data_dir(self) -> None:
        result = _env_vars_to_config({"QPS_DATA_DIR": "/tmp/data"})
        assert result["project"]["data_dir"] == "/tmp/data"

    def test_qps_log_level(self) -> None:
        result = _env_vars_to_config({"QPS_LOG_LEVEL": "DEBUG"})
        assert result["logging"]["level"] == "DEBUG"

    def test_qps_binance_api_key(self) -> None:
        # 通用双下划线规则：QPS_EXCHANGE__ADAPTERS__BINANCE__API_KEY
        result = _env_vars_to_config({"QPS_EXCHANGE__ADAPTERS__BINANCE__API_KEY": "my_key"})
        assert result["exchange"]["adapters"]["binance"]["api_key"] == "my_key"

    def test_boolean_conversion(self) -> None:
        # 布尔转换仅对已知布尔路径生效
        result = _env_vars_to_config({"QPS_EXCHANGE__ADAPTERS__BINANCE__TESTNET": "true"})
        assert result["exchange"]["adapters"]["binance"]["testnet"] is True

        result = _env_vars_to_config({"QPS_EXCHANGE__ADAPTERS__BINANCE__TESTNET": "false"})
        assert result["exchange"]["adapters"]["binance"]["testnet"] is False

    def test_numeric_string_not_converted_to_bool(self) -> None:
        # '1'/'0' 不再被转为布尔值，避免破坏数字型环境变量
        result = _env_vars_to_config({"QPS_SOME__COUNT": "1"})
        assert result["some"]["count"] == "1"  # 保持字符串，不转为 True

        result = _env_vars_to_config({"QPS_SOME__COUNT": "0"})
        assert result["some"]["count"] == "0"  # 保持字符串，不转为 False

    def test_non_qps_vars_ignored(self) -> None:
        result = _env_vars_to_config({"OTHER_VAR": "value", "QPS_MODE": "paper"})
        assert "OTHER_VAR" not in str(result)
        assert result.get("_mode") == "paper"


# ── 三级覆盖集成测试 ──────────────────────────────────────


class TestConfigThreeLevelOverride:
    """三级覆盖顺序测试：default → mode → env"""

    def test_default_only(self, config_dir: Path) -> None:
        """仅加载 default.yaml"""
        _write_yaml(
            config_dir / "default.yaml",
            {"project": {"name": "autoQps", "data_dir": "./data"}},
        )
        cfg = Config(config_dir=config_dir, env_vars={})
        assert cfg.get("project.name") == "autoQps"
        assert cfg.get("project.data_dir") == "./data"

    def test_mode_overrides_default(self, config_dir: Path) -> None:
        """mode.yaml 覆盖 default.yaml"""
        _write_yaml(
            config_dir / "default.yaml",
            {"logging": {"level": "INFO", "json_lines": True}},
        )
        _write_yaml(
            config_dir / "paper.yaml",
            {"logging": {"level": "DEBUG"}},
        )
        cfg = Config(config_dir=config_dir, mode="paper", env_vars={})
        # paper 覆盖了 level，但 json_lines 保留 default
        assert cfg.get("logging.level") == "DEBUG"
        assert cfg.get("logging.json_lines") is True

    def test_env_overrides_all(self, config_dir: Path) -> None:
        """环境变量覆盖 default + mode"""
        _write_yaml(
            config_dir / "default.yaml",
            {"logging": {"level": "INFO"}},
        )
        _write_yaml(
            config_dir / "paper.yaml",
            {"logging": {"level": "DEBUG"}},
        )
        cfg = Config(
            config_dir=config_dir,
            mode="paper",
            env_vars={"QPS_LOG_LEVEL": "WARNING"},
        )
        assert cfg.get("logging.level") == "WARNING"

    def test_nested_merge_preserves_unoverridden(self, config_dir: Path) -> None:
        """嵌套合并保留未被覆盖的字段"""
        _write_yaml(
            config_dir / "default.yaml",
            {
                "risk": {
                    "account": {
                        "max_total_notional": 10000,
                        "max_daily_loss": 500,
                    }
                }
            },
        )
        _write_yaml(
            config_dir / "production.yaml",
            {
                "risk": {
                    "account": {
                        "max_daily_loss": 200,
                    }
                }
            },
        )
        cfg = Config(config_dir=config_dir, mode="production", env_vars={})
        assert cfg.get("risk.account.max_daily_loss") == 200
        assert cfg.get("risk.account.max_total_notional") == 10000

    def test_placeholder_resolved_from_env(self, config_dir: Path) -> None:
        """占位符从环境变量解析"""
        _write_yaml(
            config_dir / "default.yaml",
            {"exchange": {"adapters": {"binance": {"api_key": "${QPS_BINANCE_API_KEY}"}}}},
        )
        cfg = Config(
            config_dir=config_dir,
            env_vars={"QPS_BINANCE_API_KEY": "real_key_123"},
        )
        assert cfg.get("exchange.adapters.binance.api_key") == "real_key_123"

    def test_placeholder_unresolved_when_env_missing(self, config_dir: Path) -> None:
        """环境变量缺失时保留占位符"""
        _write_yaml(
            config_dir / "default.yaml",
            {"exchange": {"adapters": {"binance": {"api_key": "${QPS_BINANCE_API_KEY}"}}}},
        )
        cfg = Config(config_dir=config_dir, env_vars={})
        assert cfg.get("exchange.adapters.binance.api_key") == "${QPS_BINANCE_API_KEY}"

    def test_data_dir_property(self, config_dir: Path) -> None:
        """data_dir 属性返回 Path"""
        _write_yaml(
            config_dir / "default.yaml",
            {"project": {"data_dir": "./data"}},
        )
        cfg = Config(config_dir=config_dir, env_vars={})
        assert isinstance(cfg.data_dir, Path)
        assert str(cfg.data_dir) == "data"

    def test_mode_property(self, config_dir: Path) -> None:
        """mode 属性正确返回"""
        _write_yaml(config_dir / "default.yaml", {})
        cfg = Config(config_dir=config_dir, mode="paper", env_vars={})
        assert cfg.mode == "paper"

    def test_mode_from_env(self, config_dir: Path) -> None:
        """从环境变量获取 mode"""
        _write_yaml(config_dir / "default.yaml", {})
        cfg = Config(config_dir=config_dir, env_vars={"QPS_MODE": "production"})
        assert cfg.mode == "production"

    def test_get_with_default(self, config_dir: Path) -> None:
        """get 方法路径不存在时返回默认值"""
        _write_yaml(config_dir / "default.yaml", {"project": {"name": "autoQps"}})
        cfg = Config(config_dir=config_dir, env_vars={})
        assert cfg.get("nonexistent.path", "fallback") == "fallback"

    def test_config_error_on_invalid_yaml(self, config_dir: Path) -> None:
        """YAML 解析失败抛出 ConfigError"""
        from qps.core.errors import ConfigError

        bad_file = config_dir / "default.yaml"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("invalid: yaml: content: [", encoding="utf-8")

        with pytest.raises(ConfigError, match="YAML 解析失败"):
            Config(config_dir=config_dir, env_vars={})

    def test_config_error_on_missing_default_yaml(self, config_dir: Path) -> None:
        """default.yaml 缺失时抛出 ConfigError"""
        from qps.core.errors import ConfigError

        # config_dir 下没有任何 yaml 文件
        with pytest.raises(ConfigError, match="必需的配置文件不存在"):
            Config(config_dir=config_dir, env_vars={})
