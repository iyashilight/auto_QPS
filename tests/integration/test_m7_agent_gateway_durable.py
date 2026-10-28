"""Production DI AgentGatewayPort durability and permission boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from qps.agents.agent_core import AgentPermissionError, DurableAgentGateway
from qps.core.models.artifact import Artifact

if TYPE_CHECKING:
    from pathlib import Path


def test_durable_agent_gateway_runs_allowlisted_role_and_restarts(tmp_path: Path) -> None:
    db_path = tmp_path / "agent.db"
    gateway = DurableAgentGateway(db_path)
    asyncio.run(gateway.register_handler("research", lambda params: {"echo": params["value"]}))
    task = asyncio.run(gateway.submit_task("research", {"value": "offline", "prompt_hash": "p1"}))
    assert task.status.value == "SUCCEEDED"
    artifact_id = next(iter(gateway.runner.artifacts))

    restarted = DurableAgentGateway(db_path)
    artifact = asyncio.run(restarted.read_artifact(artifact_id))
    assert artifact.content["echo"] == "offline"
    with pytest.raises(AgentPermissionError, match="未注册 handler"):
        asyncio.run(restarted.submit_task("broker.place_order", {"quantity": 1}))


def test_durable_agent_gateway_artifact_is_append_only(tmp_path: Path) -> None:
    gateway = DurableAgentGateway(tmp_path / "agent.db")
    artifact = Artifact(
        artifact_id="artifact-fixed", artifact_type="research", content={"status": "DRAFT"},
        agent_role="local", created_at=datetime.now(UTC),
    )
    assert asyncio.run(gateway.write_artifact(artifact)) == "artifact-fixed"
    with pytest.raises(AgentPermissionError, match="不可覆盖"):
        asyncio.run(gateway.write_artifact(artifact.model_copy(update={"content": {"status": "PUBLISHED"}})))
