from pathlib import Path
from tempfile import TemporaryDirectory

from qps.agents.task_runner import AgentTaskRunner


def test_agent_output_has_immutable_artifact_reference() -> None:
    with TemporaryDirectory() as directory:
        runner = AgentTaskRunner(Path(directory) / "agent.db")
        task = runner.run(
            "answer",
            {"question": "q", "model_version": "offline-v1", "tool_versions": {"retriever": "1"}},
            handler=lambda _: {"record_ids": ["r2", "r1"], "citations": [{"record_id": "r1", "score": "0.9"}]},
        )
        assert task.task_id
        artifact = next(iter(runner.artifacts.values()))
        assert artifact.content["lineage"]["record_ids"] == ["r1", "r2"]
        assert artifact.content["lineage"]["model_version"] == "offline-v1"
