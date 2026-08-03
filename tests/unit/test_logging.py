"""结构化日志单元测试"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from qps.infra.logging import (
    JsonFormatter,
    PlainFormatter,
    _mask_sensitive,
    get_logger,
    setup_logging,
)

if TYPE_CHECKING:
    from pathlib import Path


class TestMaskSensitive:
    """敏感字段脱敏测试"""

    def test_mask_api_key(self) -> None:
        data = {"api_key": "secret123", "name": "test"}
        result = _mask_sensitive(data)
        assert result["api_key"] == "***MASKED***"
        assert result["name"] == "test"

    def test_mask_nested(self) -> None:
        data = {"exchange": {"adapters": {"binance": {"api_secret": "s3cr3t"}}}}
        result = _mask_sensitive(data)
        assert result["exchange"]["adapters"]["binance"]["api_secret"] == "***MASKED***"

    def test_mask_list(self) -> None:
        data = [{"api_key": "k1"}, {"api_key": "k2"}]
        result = _mask_sensitive(data)
        assert result[0]["api_key"] == "***MASKED***"
        assert result[1]["api_key"] == "***MASKED***"

    def test_no_mask_normal_fields(self) -> None:
        data = {"symbol": "BTC-USDT-PERP", "price": 65000}
        result = _mask_sensitive(data)
        assert result == data

    def test_mask_token(self) -> None:
        data = {"token": "abc123"}
        result = _mask_sensitive(data)
        assert result["token"] == "***MASKED***"

    def test_mask_password(self) -> None:
        data = {"password": "pass123"}
        result = _mask_sensitive(data)
        assert result["password"] == "***MASKED***"

    def test_mask_compound_key(self) -> None:
        """子串匹配：复合键名如 binance_api_key 也被脱敏"""
        data = {"binance_api_key": "sk-xxx", "user_password_hash": "h"}
        result = _mask_sensitive(data)
        assert result["binance_api_key"] == "***MASKED***"
        assert result["user_password_hash"] == "***MASKED***"

    def test_mask_passphrase(self) -> None:
        """子串匹配：passphrase 被脱敏"""
        data = {"mnemonic_phrase": "word1 word2", "passphrase": "mypass"}
        result = _mask_sensitive(data)
        assert result["mnemonic_phrase"] == "***MASKED***"
        assert result["passphrase"] == "***MASKED***"


class TestJsonFormatter:
    """JSON 格式化器测试"""

    def test_basic_format(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="qps.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["level"] == "INFO"
        assert parsed["module"] == "qps.test"
        assert parsed["message"] == "test message"
        assert "timestamp" in parsed

    def test_structured_data(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="qps.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.structured_data = {"symbol": "BTC-USDT", "api_key": "secret"}  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["data"]["symbol"] == "BTC-USDT"
        assert parsed["data"]["api_key"] == "***MASKED***"

    def test_event_id(self) -> None:
        formatter = JsonFormatter()
        record = logging.LogRecord(
            name="qps.test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="test",
            args=(),
            exc_info=None,
        )
        record.event_id = "evt_123"  # type: ignore[attr-defined]
        output = formatter.format(record)
        parsed = json.loads(output)
        assert parsed["event_id"] == "evt_123"


class TestPlainFormatter:
    """纯文本格式化器测试"""

    def test_basic_format(self) -> None:
        formatter = PlainFormatter()
        record = logging.LogRecord(
            name="qps.test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="warning message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        assert "WARNING" in output
        assert "warning message" in output


class TestSetupLogging:
    """日志配置测试"""

    def test_setup_json_mode(self) -> None:
        setup_logging(level="DEBUG", json_lines=True)
        logger = logging.getLogger("qps")
        assert logger.level == logging.DEBUG
        # 清理
        logger.handlers.clear()

    def test_setup_plain_mode(self) -> None:
        setup_logging(level="INFO", json_lines=False)
        logger = logging.getLogger("qps")
        assert any(
            isinstance(h.formatter, PlainFormatter) for h in logger.handlers
        )
        logger.handlers.clear()

    def test_setup_with_file(self, tmp_path: Path) -> None:
        log_dir = tmp_path / "logs"
        setup_logging(level="INFO", json_lines=True, log_dir=log_dir)
        logger = logging.getLogger("qps")
        assert log_dir.exists()
        assert any(isinstance(h, logging.FileHandler) for h in logger.handlers)
        logger.handlers.clear()

    def test_get_logger(self) -> None:
        logger = get_logger("data.adapters")
        assert logger.name == "qps.data.adapters"

    def test_get_logger_auto_prefix(self) -> None:
        logger = get_logger("qps.test.module")
        assert logger.name == "qps.test.module"
