"""pytest 全局配置与 fixture

提供：固定种子、固定时钟（可注入）、临时数据目录
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Generator
    from pathlib import Path


@pytest.fixture
def fixed_seed() -> int:
    """固定随机种子，保证测试可复现"""
    return 42


@pytest.fixture
def fixed_now() -> datetime:
    """固定当前时间（UTC）"""
    return datetime(2026, 1, 15, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def mock_clock(fixed_now: datetime) -> Generator[None, None, None]:
    """注入固定时钟

    注意：当前 logging 模块使用 datetime.fromtimestamp(record.created) 生成时间戳，
    不受 datetime.now mock 影响。如需控制日志时间戳，应通过 LogRecord.created 注入。
    此 fixture 保留供未来需要 mock datetime.now 的模块使用。
    """
    with patch("qps.infra.logging.datetime") as mock_dt:
        mock_dt.now.return_value = fixed_now
        mock_dt.fromtimestamp.return_value = fixed_now
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        yield


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> Path:
    """临时数据目录（测试结束后自动清理）"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "raw").mkdir()
    (data_dir / "normalized").mkdir()
    (data_dir / "features").mkdir()
    (data_dir / "event_logs").mkdir()
    (data_dir / "backups").mkdir()
    return data_dir


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """临时配置目录"""
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir()
    return cfg_dir


@pytest.fixture
def clean_env() -> Generator[None, None, None]:
    """清理 QPS_* 环境变量，避免测试间干扰

    保存测试前的 QPS_* 变量，测试后恢复原始值并移除测试中新创建的 QPS_* 变量。
    """
    # 记录测试前存在的 QPS_* 变量及其值
    original_keys = {k for k in os.environ if k.startswith("QPS_")}
    saved = {k: os.environ.pop(k) for k in list(original_keys)}
    try:
        yield
    finally:
        # 恢复原始变量
        os.environ.update(saved)
        # 移除测试期间新创建的 QPS_* 变量
        current_keys = {k for k in os.environ if k.startswith("QPS_")}
        for k in current_keys - original_keys:
            os.environ.pop(k, None)
