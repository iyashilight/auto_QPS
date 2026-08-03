"""T4 配置模型测试：Pydantic 配置校验、缺失必填项、环境变量覆盖"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from qps.core.errors import ConfigError
from qps.infra.config import Config
from qps.infra.config_models import (
    AppConfig,
    BacktestConfig,
    BacktestCostConfig,
    JobSpecConfig,
    validate_config,
)

if TYPE_CHECKING:
    from pathlib import Path

# ── AppConfig 默认值 ──────────────────────────────────────


class TestAppConfigDefaults:
    """AppConfig 默认值与 configs/default.yaml 一致"""

    def test_default_project(self) -> None:
        cfg = AppConfig()
        assert cfg.project.name == "autoQps"
        assert cfg.project.data_dir == "./data"

    def test_default_backtest(self) -> None:
        cfg = AppConfig()
        assert cfg.backtest.default_mode == "EVENT_DRIVEN"
        assert cfg.backtest.costs.maker_fee == Decimal("0.0002")
        assert cfg.backtest.costs.taker_fee == Decimal("0.0004")
        assert cfg.backtest.random_seed == 42

    def test_default_risk(self) -> None:
        cfg = AppConfig()
        assert cfg.risk.account.max_total_notional == Decimal("10000")
        assert cfg.risk.order.max_single_notional == Decimal("2000")
        assert cfg.risk.market.freeze_on_ws_disconnect is True

    def test_default_paper(self) -> None:
        cfg = AppConfig()
        assert cfg.paper.fill_latency_ms == 10
        assert cfg.paper.partial_fill is True


# ── 校验与约束 ────────────────────────────────────────────


class TestConfigValidation:
    """配置校验与约束"""

    def test_backtest_mode_must_be_valid(self) -> None:
        """回测模式必须是合法值"""
        with pytest.raises(ValidationError):
            BacktestConfig(default_mode="invalid_mode")

    def test_slippage_model_must_be_valid(self) -> None:
        """滑点模型必须是合法值"""
        with pytest.raises(ValidationError):
            BacktestCostConfig(slippage_model="random")

    def test_funding_rate_source_must_be_valid(self) -> None:
        """资金费率来源必须是合法值"""
        with pytest.raises(ValidationError):
            BacktestCostConfig(funding_rate_source="invalid")

    def test_alerts_channel_must_be_valid(self) -> None:
        """告警通道必须是合法值"""
        with pytest.raises(ValidationError):
            AppConfig.model_validate({"alerts": {"channel": "slack"}})

    def test_job_spec_requires_fields(self) -> None:
        """调度任务配置必填项"""
        with pytest.raises(ValidationError):
            JobSpecConfig(name="test")  # 缺少 type 和 cron


# ── validate_config ───────────────────────────────────────


class TestValidateConfig:
    """validate_config 函数测试"""

    def test_valid_full_config(self) -> None:
        """完整配置校验通过"""
        raw = {
            "project": {"name": "autoQps", "data_dir": "./data"},
            "backtest": {"default_mode": "EVENT_DRIVEN", "random_seed": 42},
        }
        cfg = validate_config(raw)
        assert cfg.project.name == "autoQps"

    def test_empty_config_uses_defaults(self) -> None:
        """空配置使用默认值"""
        cfg = validate_config({})
        assert cfg.project.name == "autoQps"
        assert cfg.risk.account.max_total_notional == Decimal("10000")

    def test_invalid_config_raises_config_error(self) -> None:
        """非法配置抛 ConfigError"""
        with pytest.raises(ConfigError, match="配置校验失败"):
            validate_config({"backtest": {"default_mode": "invalid"}})


# ── 与 Config 类集成 ──────────────────────────────────────


class TestConfigIntegration:
    """Pydantic 配置模型与 Config 类集成测试"""

    def test_config_to_app_config(self, config_dir: Path) -> None:
        """从 Config.raw_data 构建 AppConfig"""

        import yaml

        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_dir / "default.yaml", "w", encoding="utf-8") as f:
            yaml.dump(
                {
                    "project": {"name": "autoQps", "data_dir": "./data"},
                    "backtest": {"random_seed": 42},
                },
                f,
            )

        cfg = Config(config_dir=config_dir, env_vars={})
        app_cfg = validate_config(cfg.raw_data)
        assert app_cfg.project.name == "autoQps"
        assert app_cfg.backtest.random_seed == 42

    def test_env_override_reflected_in_app_config(self, config_dir: Path) -> None:
        """环境变量覆盖反映在 AppConfig 中"""

        import yaml

        config_dir.mkdir(parents=True, exist_ok=True)
        with open(config_dir / "default.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"logging": {"level": "INFO"}}, f)

        cfg = Config(
            config_dir=config_dir,
            env_vars={"QPS_LOG_LEVEL": "DEBUG"},
        )
        app_cfg = validate_config(cfg.raw_data)
        assert app_cfg.logging.level == "DEBUG"


# 需要导入 Path
