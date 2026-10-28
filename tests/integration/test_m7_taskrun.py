from pathlib import Path
from tempfile import TemporaryDirectory

from qps.agents.task_runner import AgentTaskRunner


def test_taskrun_and_artifact_are_replayable() -> None:
    with TemporaryDirectory() as directory:
        database = Path(directory) / "agent.db"
        task = AgentTaskRunner(database).run("research", {"x": 1})
        assert task.status.value == "SUCCEEDED"
        restarted = AgentTaskRunner(database)
        assert task.task_id in restarted.tasks
        assert restarted.artifacts
        assert restarted.artifacts[next(iter(restarted.artifacts))].content["lineage"]["input_hash"]
