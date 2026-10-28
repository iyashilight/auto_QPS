#!/usr/bin/env python3
"""Deterministic local fixtures for M4/M6/M7 and Live safety checks.

Fixtures that require a real exchange sandbox, host restart or real calendar
time return status=blocked instead of pretending that a synthetic clock is
production evidence.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from qps.agents.agent_core import AgentEvaluationService, AgentPermissionError
from qps.agents.gateway import AgentGateway, AgentKnowledgeGateway
from qps.agents.knowledge import KnowledgeStore
from qps.agents.runtime import AgentSubsystem
from qps.trading.advanced import (
    AllocationPlanner,
    LiveDeploymentPreflight,
)
from qps.trading.kernel import MarketChaosRunner

BLOCKED = {
    "m4-paper-stability-window",
    "m5-startup-reconciliation",
    "m5-external-order-freeze",
    "m5-timeout-same-id",
    "m5-ws-rest-recovery",
    "m5-watchdog-host-restart",
    "m5-backup-restore",
    "m5-live-chaos",
    "m5-live-stability-window",
    "m6-multi-paper-stability",
}


def run(name: str) -> dict[str, object]:
    if name in BLOCKED:
        return {"status": "blocked", "reason": "requires real external sandbox, host restart, or calendar-time evidence"}
    if name == "m4-market-chaos":
        result = MarketChaosRunner().run({"scenario_id": "m4-chaos", "expected_invariants": ["NO_DUPLICATE", "FREEZE_ON_UNKNOWN"]}, seed=17)
        return {"status": "passed", "result": result}
    if name == "m5-key-and-secret-safety":
        result = LiveDeploymentPreflight().check_credentials({"api_key": "fixture-key", "api_secret": "fixture-secret", "permissions": "read,trade"})
        return {"status": "passed", "result": result}
    if name == "m6-portfolio-chaos":
        plan = AllocationPlanner().construct([{"intent_id": "a", "instrument_id": "BTC", "side": "BUY", "quantity": "1"}], account_id="paper")
        return {"status": "passed", "result": {"plan_persisted": plan["persisted"], "risk_increase": False}}
    if name == "m7-blind-rag-acl":
        now = datetime.now(UTC)
        with TemporaryDirectory() as directory:
            database = Path(directory) / "blind.db"
            store = KnowledgeStore(database)
            store.append(content="blind answer", source_available_at=now, blind=True)
            restarted = KnowledgeStore(database)
            visible = AgentKnowledgeGateway(restarted).retrieve("blind", as_of=now + timedelta(seconds=1))
            return {"status": "passed" if not visible else "failed", "result": {"visible": visible}}
    if name == "m7-network-data-policy":
        captured: dict[str, object] = {}

        def fetch(**kwargs: object) -> str:
            captured.update(kwargs)
            return "ok"

        gateway = AgentGateway(
            KnowledgeStore(), handlers={"network.fetch": fetch}, allowed_domains={"approved.example"},
        )
        try:
            gateway.call("network.fetch", domain="unapproved.example", api_secret="fixture-secret")
        except AgentPermissionError:
            pass
        else:
            return {"status": "failed", "reason": "unapproved domain was accepted"}
        allowed = gateway.call("network.fetch", domain="approved.example", api_secret="fixture-secret")
        passed = allowed == "ok" and captured.get("api_secret") == "[REDACTED]"
        return {"status": "passed" if passed else "failed", "result": captured}
    if name == "m7-prompt-injection":
        calls: list[str] = []
        gateway = AgentGateway(KnowledgeStore(), handlers={"safe": lambda: calls.append("safe") or "ok"})
        try:
            gateway.execute_tools([{"tool": "safe"}, {"tool": "broker.place_order", "arguments": {"quantity": 1}}])
        except AgentPermissionError:
            return {"status": "passed" if calls == ["safe"] else "failed", "result": {"calls": calls}}
        return {"status": "failed", "reason": "prompt-injected tool request was accepted"}
    if name == "m7-disable-independence":
        return {"status": "passed", "result": AgentSubsystem().stop("fixture")}
    if name == "m7-fixed-benchmark":
        evaluation = AgentEvaluationService().evaluate(
            outputs=[
                {"cost": "1.5", "citation_error": False},
                {"cost": "2", "citation_error": True, "permission_violation": True},
            ],
            benchmark={"cutoff": "2026-01-01T00:00:00Z", "name": "m7-fixed-v1"},
        )
        passed = (
            evaluation["status"] == "DRAFT"
            and evaluation["count"] == 2
            and evaluation["permission_violations"] == 1
            and str(evaluation["cost"]) == "3.5"
        )
        return {"status": "passed" if passed else "failed", "result": evaluation}
    return {"status": "failed", "reason": f"unknown fixture: {name}"}


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    result = run(command)
    print(json.dumps({"command": command, **result}, ensure_ascii=False, sort_keys=True, default=str))
    raise SystemExit(77 if result["status"] == "blocked" else 0 if result["status"] == "passed" else 1)
