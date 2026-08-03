"""结构化日志（JSON lines 输出）

统一字段：时间、模块、级别、事件 ID、策略版本
敏感字段脱敏，通过配置控制级别与输出目录。
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# 敏感字段关键词（子串匹配：键名包含这些词则脱敏）
_SENSITIVE_SUBSTRINGS: tuple[str, ...] = (
    "api_key",
    "api_secret",
    "secret",
    "password",
    "token",
    "credential",
    "private_key",
    "mnemonic",
    "passphrase",
)

# 脱敏替换值
_MASK = "***MASKED***"


def _is_sensitive_key(key: str) -> bool:
    """检查键名是否包含敏感子串"""
    key_lower = key.lower()
    return any(substr in key_lower for substr in _SENSITIVE_SUBSTRINGS)


def _mask_sensitive(data: Any) -> Any:
    """递归脱敏敏感字段（子串匹配）"""
    if isinstance(data, dict):
        return {
            k: _MASK if _is_sensitive_key(k) else _mask_sensitive(v)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [_mask_sensitive(item) for item in data]
    return data


def _record_timestamp(record: logging.LogRecord) -> str:
    """从 LogRecord 的 created 时间戳生成 UTC ISO 8601 字符串

    使用 record.created（事件产生时间）而非 datetime.now()（格式化时间），
    保证日志时间戳准确反映事件发生时刻。
    """
    dt = datetime.fromtimestamp(record.created, tz=UTC)
    return dt.isoformat()


class JsonFormatter(logging.Formatter):
    """JSON lines 格式化器"""

    def __init__(
        self,
        *,
        include_strategy_version: bool = True,
        include_event_id: bool = True,
    ) -> None:
        super().__init__()
        self._include_strategy_version = include_strategy_version
        self._include_event_id = include_event_id

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为 JSON lines"""
        log_entry: dict[str, Any] = {
            "timestamp": _record_timestamp(record),
            "level": record.levelname,
            "module": record.name,
            "message": record.getMessage(),
        }

        # 附加字段（通过 extra 传入）
        if self._include_event_id and hasattr(record, "event_id"):
            log_entry["event_id"] = record.event_id

        if self._include_strategy_version and hasattr(record, "strategy_version"):
            log_entry["strategy_version"] = record.strategy_version

        # 附加结构化数据
        if hasattr(record, "structured_data"):
            log_entry["data"] = _mask_sensitive(record.structured_data)

        # 异常信息
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """纯文本格式化器（开发模式）"""

    def format(self, record: logging.LogRecord) -> str:
        """格式化日志记录为纯文本"""
        timestamp = _record_timestamp(record)
        # 简化为人类可读格式
        ts_display = timestamp.replace("T", " ")[:23]
        base = f"{ts_display} UTC | {record.levelname:8s} | {record.name} | {record.getMessage()}"

        if hasattr(record, "structured_data"):
            data = _mask_sensitive(record.structured_data)
            base += f" | data={json.dumps(data, ensure_ascii=False, default=str)}"

        if record.exc_info and record.exc_info[1] is not None:
            base += "\n" + self.formatException(record.exc_info)

        return base


# 跟踪已设置过模块级别的 logger 名称，用于清理
_configured_module_loggers: list[str] = []


def setup_logging(
    *,
    level: str = "INFO",
    json_lines: bool = True,
    log_dir: Path | str | None = None,
    module_levels: dict[str, str] | None = None,
) -> None:
    """配置结构化日志

    Args:
        level: 全局日志级别
        json_lines: 是否使用 JSON lines 格式（False 则用纯文本）
        log_dir: 日志输出目录（None 则只输出到 stderr）
        module_levels: 模块级别覆盖，如 {"qps.data": "DEBUG"}
    """
    qps_logger = logging.getLogger("qps")
    qps_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 禁止向 root logger 传播，避免重复日志
    qps_logger.propagate = False

    # 关闭并清除已有 handler（防止文件描述符泄漏）
    for handler in qps_logger.handlers[:]:
        handler.close()
    qps_logger.handlers.clear()

    # stderr handler
    stderr_handler = logging.StreamHandler(sys.stderr)
    if json_lines:
        stderr_handler.setFormatter(JsonFormatter())
    else:
        stderr_handler.setFormatter(PlainFormatter())
    qps_logger.addHandler(stderr_handler)

    # 文件 handler（可选）
    if log_dir is not None:
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(
            log_path / "qps.log", encoding="utf-8"
        )
        file_handler.setFormatter(JsonFormatter())
        qps_logger.addHandler(file_handler)

    # 清理旧的模块级别覆盖
    for module_name in _configured_module_loggers:
        module_logger = logging.getLogger(f"qps.{module_name}")
        module_logger.setLevel(qps_logger.level)
    _configured_module_loggers.clear()

    # 模块级别覆盖
    if module_levels:
        for module_name, module_level in module_levels.items():
            module_logger = logging.getLogger(f"qps.{module_name}")
            module_logger.setLevel(getattr(logging, module_level.upper(), logging.INFO))
            _configured_module_loggers.append(module_name)


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器

    Args:
        name: 日志器名称（通常为模块名）

    Returns:
        配置好的 Logger 实例
    """
    if not name.startswith("qps."):
        name = f"qps.{name}"
    return logging.getLogger(name)
