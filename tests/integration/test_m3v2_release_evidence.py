import json
import os
import subprocess
from pathlib import Path


def test_persisted_release_builder_requires_manual_evidence_selection(tmp_path: Path) -> None:
    env = {**os.environ, "QPS_M3V2_FIXTURE_DIR": str(tmp_path)}
    command = Path(__file__).parents[2] / "scripts" / "external-fixture-runner"
    result = subprocess.run(
        [str(command), "m3v2-release-evidence-build"],
        cwd=Path(__file__).parents[2], env=env, check=False,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["status"] == "passed"
    assert payload["candidate_only"] is True
    assert payload["evidence_report_id"].startswith("evidence-")
