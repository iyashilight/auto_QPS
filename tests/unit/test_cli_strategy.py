"""CLI strategy 端到端测试（见 M2.2 T7）

测试 qps strategy create/list/status 命令的完整链路：
- create: 三模板之一创建策略
- list: 列出策略可见
- status: 版本历史正常
- 同内容二次 create 幂等返回同版本号
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from qps.control.cli.app import app

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """创建临时数据目录并设置 QPS_DATA_DIR 环境变量"""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    os.environ["QPS_DATA_DIR"] = str(data_dir)
    yield data_dir
    os.environ.pop("QPS_DATA_DIR", None)


runner = CliRunner()


class TestCLIStrategyCreate:
    """strategy create 端到端测试"""

    def test_create_trend_strategy(self, data_dir: Path) -> None:
        """创建趋势策略成功"""
        result = runner.invoke(app, ["strategy", "create", "test_trend_cli", "--template", "trend"])
        assert result.exit_code == 0, f"输出: {result.output}"
        assert "策略创建成功" in result.output
        assert "test_trend_cli" in result.output
        assert "版本号: 1" in result.output
        assert "内容哈希:" in result.output

    def test_create_mean_reversion_strategy(self, data_dir: Path) -> None:
        """创建均值回归策略成功"""
        result = runner.invoke(app, ["strategy", "create", "test_mr_cli", "--template", "mean_reversion"])
        assert result.exit_code == 0, f"输出: {result.output}"
        assert "策略创建成功" in result.output
        assert "版本号: 1" in result.output

    def test_create_invalid_template_fails(self, data_dir: Path) -> None:
        """无效模板失败"""
        result = runner.invoke(app, ["strategy", "create", "test_bad", "--template", "invalid"])
        assert result.exit_code == 2
        assert "无效模板" in result.output


class TestCLIStrategyList:
    """strategy list 端到端测试"""

    def test_list_after_create(self, data_dir: Path) -> None:
        """创建后 list 可见"""
        # 先创建
        runner.invoke(app, ["strategy", "create", "test_list_cli", "--template", "trend"])
        # 再列出
        result = runner.invoke(app, ["strategy", "list"])
        assert result.exit_code == 0, f"输出: {result.output}"
        assert "test_list_cli" in result.output

    def test_list_empty(self, data_dir: Path) -> None:
        """空数据库 list 返回无策略"""
        result = runner.invoke(app, ["strategy", "list"])
        assert result.exit_code == 0
        assert "无策略记录" in result.output


class TestCLIStrategyStatus:
    """strategy status 端到端测试"""

    def test_status_after_create(self, data_dir: Path) -> None:
        """创建后 status 版本历史正常"""
        # 先创建
        runner.invoke(app, ["strategy", "create", "test_status_cli", "--template", "trend"])
        # 查看状态
        result = runner.invoke(app, ["strategy", "status", "test_status_cli"])
        assert result.exit_code == 0, f"输出: {result.output}"
        assert "版本 1" in result.output
        assert "代码引用:" in result.output
        assert "内容哈希:" in result.output

    def test_status_nonexistent_strategy(self, data_dir: Path) -> None:
        """不存在的策略返回无版本"""
        result = runner.invoke(app, ["strategy", "status", "nonexistent"])
        assert result.exit_code == 0
        assert "无版本记录" in result.output


class TestCLIStrategyIdempotent:
    """同内容二次 create 幂等测试"""

    def test_create_idempotent_same_version(self, data_dir: Path) -> None:
        """同内容二次 create 返回相同版本号"""
        # 第一次创建
        result1 = runner.invoke(app, ["strategy", "create", "test_idem_cli", "--template", "trend"])
        assert result1.exit_code == 0
        assert "版本号: 1" in result1.output

        # 第二次创建（同内容，应幂等返回版本 1）
        result2 = runner.invoke(app, ["strategy", "create", "test_idem_cli", "--template", "trend"])
        assert result2.exit_code == 0
        assert "版本号: 1" in result2.output

        # 确认只有一个版本
        result_status = runner.invoke(app, ["strategy", "status", "test_idem_cli"])
        assert result_status.exit_code == 0
        # 只出现一次"版本 1"
        assert result_status.output.count("版本 1") == 1


class TestCLIM2LifecycleFailures:
    """M2 lifecycle commands preserve non-zero/no-side-effect semantics."""

    def test_check_package_and_missing_release_fail_closed(self, data_dir: Path) -> None:
        created = runner.invoke(app, ["strategy", "create", "m2_cli", "--template", "trend"])
        assert created.exit_code == 0
        checked = runner.invoke(app, ["strategy", "check", "m2_cli"])
        assert checked.exit_code == 0
        assert '"static_check": "passed"' in checked.output
        packaged = runner.invoke(app, ["strategy", "package", "m2_cli", "--confirm"])
        assert packaged.exit_code == 0
        assert '"status": "READY"' in packaged.output

        before = sorted((data_dir / "releases").glob("*") if (data_dir / "releases").exists() else [])
        shown = runner.invoke(app, ["strategy", "show", "rel-missing"])
        approved = runner.invoke(app, ["strategy", "approve", "rel-missing", "--status", "APPROVED",
                                      "--reason", "test", "--actor", "tester", "--confirm"])
        assert shown.exit_code == 1
        assert approved.exit_code == 1
        after = sorted((data_dir / "releases").glob("*") if (data_dir / "releases").exists() else [])
        assert before == after

    def test_promote_requires_persisted_evidence_and_has_no_side_effect(self, data_dir: Path) -> None:
        created = runner.invoke(app, ["strategy", "create", "m2_promote_cli", "--template", "trend"])
        assert created.exit_code == 0
        promoted = runner.invoke(app, [
            "strategy", "promote", "m2_promote_cli", "--to", "BACKTESTING",
            "--evidence", "report-1", "--confirm",
        ])
        assert promoted.exit_code == 1
        assert "证据不存在或未持久化" in promoted.stderr
        listed = runner.invoke(app, ["strategy", "list"])
        assert "状态: DRAFT" in listed.stdout

    def test_promote_updates_state_and_audit_after_evidence_is_durable(self, data_dir: Path) -> None:
        created = runner.invoke(app, ["strategy", "create", "m2_promote_success", "--template", "trend"])
        assert created.exit_code == 0
        from qps.infra.storage.meta_db import MetaDB

        meta = MetaDB(data_dir / "meta.db")
        meta.initialize()
        with meta.transaction() as conn:
            conn.execute(
                "INSERT INTO evidence_reports(report_id,evidence_quality,expected_edge,risk_estimate,experiment_refs,created_at) "
                "VALUES (?,?,?,?,?,?)",
                ("report-promote-1", "{}", "{}", "{}", "[]", "2026-01-01T00:00:00+00:00"),
            )
        promoted = runner.invoke(app, [
            "strategy", "promote", "m2_promote_success", "--to", "BACKTESTING",
            "--evidence", "report-promote-1", "--confirm",
        ])
        assert promoted.exit_code == 0, promoted.output
        assert '"status": "TRANSITIONED"' in promoted.stdout
        listed = runner.invoke(app, ["strategy", "list"])
        assert "状态: BACKTESTING" in listed.stdout
        with meta.connect() as conn:
            assert conn.execute(
                "SELECT action FROM audit_log WHERE target_id=?", ("m2_promote_success",),
            ).fetchone()[0] == "LIFECYCLE_TRANSITION"
