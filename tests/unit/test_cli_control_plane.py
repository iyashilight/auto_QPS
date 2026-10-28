from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import TYPE_CHECKING

from typer.testing import CliRunner

from qps.control.cli.app import app
from qps.infra.storage.meta_db import MetaDB
from qps.infra.storage.trading_journal import JournalDB
from qps.trading.kernel import AccountLedger

if TYPE_CHECKING:
    from pathlib import Path


def _invoke(data_dir: Path, *args: str):
    os.environ["QPS_DATA_DIR"] = str(data_dir)
    try:
        return CliRunner().invoke(app, list(args))
    finally:
        os.environ.pop("QPS_DATA_DIR", None)


def test_task_list_and_cancel_are_durable_and_audited(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    meta = MetaDB(data_dir / "meta.db")
    meta.initialize()
    with meta.transaction() as conn:
        conn.execute("INSERT INTO tasks(task_id,task_type,params,status) VALUES (?,?,?,?)", ("t-1", "collect", "{}", "RUNNING"))

    listed = _invoke(data_dir, "task", "list", "--json")
    assert listed.exit_code == 0
    assert json.loads(listed.stdout)["tasks"][0]["task_id"] == "t-1"

    canceled = _invoke(data_dir, "task", "cancel", "t-1", "--confirm")
    assert canceled.exit_code == 0
    assert json.loads(canceled.stdout)["status"] == "CANCELED"
    with meta.connect() as conn:
        assert conn.execute("SELECT status FROM tasks WHERE task_id='t-1'").fetchone()[0] == "CANCELED"
        assert conn.execute("SELECT action FROM audit_log WHERE target_id='t-1'").fetchone()[0] == "TASK_CANCEL"


def test_broker_status_is_explicitly_paper_and_fail_closed(tmp_path: Path) -> None:
    result = _invoke(tmp_path / "data", "broker", "status", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "PAPER"
    assert payload["connected"] is False
    assert payload["new_risk_allowed"] is False


def test_positions_reads_persisted_account_ledger(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    ledger = AccountLedger(JournalDB(data_dir / "trading.db"))
    ledger.append(account_id="paper", asset="BTC", amount=Decimal("2"), entry_type="POSITION_DELTA", source_type="TEST", source_id="s1")
    result = _invoke(data_dir, "trade", "positions", "--account", "paper")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["positions"] == [{"account": "paper", "asset": "BTC", "amount": "2"}]


def test_reconcile_requires_external_snapshot(tmp_path: Path) -> None:
    blocked = _invoke(tmp_path / "data", "trade", "reconcile", "--account", "paper")
    assert blocked.exit_code == 77
    assert json.loads(blocked.stdout)["status"] == "EVIDENCE_BLOCKED"

    passed = _invoke(tmp_path / "data", "trade", "reconcile", "--account", "paper", "--remote-json", '{"orders": 0}')
    assert passed.exit_code == 0
    assert json.loads(passed.stdout)["status"] == "MATCHED"
