from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from types import ModuleType


def _load_preflight() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts" / "review_preflight.py"
    spec = importlib.util.spec_from_file_location("review_preflight", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()


def _write_yaml(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (tmp_path / "control.py").write_text("ENFORCED = True\n", encoding="utf-8")
    (tmp_path / "downstream.py").write_text("FORMAL_PATH = True\n", encoding="utf-8")
    (tmp_path / "architecture.md").write_text("# Design\n", encoding="utf-8")
    task_dir = tmp_path / "tasks" / "sample"
    _write_yaml(
        task_dir / "task.yaml",
        {
            "schema_version": 1,
            "task_id": "sample",
            "risk_level": "critical",
            "matrix_id": "sample-v1",
            "matrix_file": "tasks/sample/matrix.yaml",
            "evidence_results_file": "tasks/sample/evidence-results.yaml",
            "architecture_refs": ["architecture.md"],
            "scope_paths": ["control.py", "downstream.py"],
            "declared_state": "CODE_COMPLETE",
            "transition_history": [
                {"from": None, "to": "DRAFT", "actor_role": "project_owner"},
                {
                    "from": "DRAFT",
                    "to": "DESIGN_APPROVED",
                    "actor_role": "architecture_reviewer",
                },
                {
                    "from": "DESIGN_APPROVED",
                    "to": "IMPLEMENTING",
                    "actor_role": "code_developer",
                },
                {
                    "from": "IMPLEMENTING",
                    "to": "CODE_COMPLETE",
                    "actor_role": "code_developer",
                },
            ],
        },
    )
    _write_yaml(
        task_dir / "matrix.yaml",
        {
            "schema_version": 1,
            "matrix_id": "sample-v1",
            "rows": [
                {
                    "id": "S-1",
                    "invariant": "The control cannot be bypassed",
                    "risk": "critical",
                    "entrypoint": "control.entrypoint",
                    "authoritative_control": "control.py",
                    "controls": ["control.py"],
                    "depends_on": [],
                    "required_proofs": ["adversarial"],
                    "proofs": {
                        "adversarial": {
                            "command": ["python", "-c", "assert True"],
                            "expected": "exit code 0",
                            "environment": "local-test",
                        }
                    },
                },
                {
                    "id": "S-2",
                    "invariant": "Downstream uses the control",
                    "risk": "critical",
                    "entrypoint": "control.downstream",
                    "authoritative_control": "downstream.py",
                    "controls": ["downstream.py"],
                    "depends_on": ["S-1"],
                    "required_proofs": ["integration"],
                    "proofs": {
                        "integration": {
                            "command": ["python", "-c", "assert True"],
                            "expected": "exit code 0",
                            "environment": "local-test",
                        }
                    },
                },
            ],
        },
    )
    return task_dir


def _record_all(task_dir: Path) -> None:
    preflight.record_evidence(
        task_dir,
        "S-1",
        "adversarial",
        status="passed",
        actual="attack rejected",
        actor_role="evidence_validator",
        environment_id="local-test",
    )
    preflight.record_evidence(
        task_dir,
        "S-2",
        "integration",
        status="passed",
        actual="formal path used",
        actor_role="evidence_validator",
        environment_id="local-test",
    )


def test_compile_creates_review_bundle_only_when_all_evidence_passes(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    _record_all(task_dir)

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "PREFLIGHT_PASSED"
    assert result.bundle_path is not None and result.bundle_path.is_file()
    manifest = yaml.safe_load(result.bundle_path.read_text(encoding="utf-8"))
    assert manifest["task_ref"] == "tasks/sample/task.yaml"
    assert len(manifest["snapshot"]["task_hash"]) == 64


def test_control_change_invalidates_evidence_and_downstream_dependency(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    _record_all(task_dir)
    (tmp_path / "control.py").write_text("ENFORCED = False\n", encoding="utf-8")

    result = preflight.compile_review(task_dir)
    report = yaml.safe_load(result.report_path.read_text(encoding="utf-8"))

    assert result.effective_state == "EVIDENCE_BLOCKED"
    assert report["rows"]["S-1"]["proofs"]["adversarial"]["status"] == "stale"
    assert "blocked dependencies: S-1" in report["rows"]["S-2"]["reasons"]
    assert result.bundle_path is None


def test_blocked_external_evidence_stops_review_before_reviewer(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    preflight.record_evidence(
        task_dir,
        "S-1",
        "adversarial",
        status="blocked",
        actual="required host capability unavailable",
        actor_role="code_developer",
        environment_id="local-test",
    )

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "EVIDENCE_BLOCKED"
    assert result.bundle_path is None


def test_developer_cannot_record_passing_evidence(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)

    try:
        preflight.record_evidence(
            task_dir,
            "S-1",
            "adversarial",
            status="passed",
            actual="claimed pass",
            actor_role="code_developer",
            environment_id="local-test",
        )
    except preflight.PreflightError as exc:
        assert "evidence_validator" in str(exc)
    else:
        raise AssertionError("developer must not promote evidence to passed")


def test_invalid_state_transition_is_rejected(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    task = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    task["transition_history"][1]["actor_role"] = "code_developer"
    _write_yaml(task_dir / "task.yaml", task)

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "PREFLIGHT_INVALID"
    assert any("cannot perform" in error for error in result.errors)


def test_architecture_change_requires_new_matrix_id(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    _record_all(task_dir)
    (tmp_path / "architecture.md").write_text("# Changed design\n", encoding="utf-8")

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "PREFLIGHT_INVALID"
    assert any("architecture changed" in error for error in result.errors)


def test_task_definition_change_requires_new_matrix_id(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    _record_all(task_dir)
    task_path = task_dir / "task.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["risk_level"] = "standard"
    _write_yaml(task_path, task)

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "PREFLIGHT_INVALID"
    assert any("task definition changed" in error for error in result.errors)


def test_evidence_environment_must_match_matrix(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)

    try:
        preflight.record_evidence(
            task_dir,
            "S-1",
            "adversarial",
            status="blocked",
            actual="wrong host",
            actor_role="code_developer",
            environment_id="different-host",
        )
    except preflight.PreflightError as exc:
        assert "does not match required" in str(exc)
    else:
        raise AssertionError("evidence from the wrong environment must be rejected")


def test_run_treats_expected_nonzero_exit_as_passing_evidence(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    matrix_path = task_dir / "matrix.yaml"
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    proof = matrix["rows"][0]["proofs"]["adversarial"]
    proof["command"] = [sys.executable, "-c", "raise SystemExit(3)"]
    proof["expected_exit_code"] = 3
    _write_yaml(matrix_path, matrix)

    exit_code = preflight.run_evidence(task_dir, "S-1", "adversarial")
    results = yaml.safe_load(
        (task_dir / "evidence-results.yaml").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert results["results"]["S-1"]["adversarial"]["status"] == "passed"
    assert results["results"]["S-1"]["adversarial"]["exit_code"] == 3


def test_run_records_structured_capability_block_as_blocked(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    matrix_path = task_dir / "matrix.yaml"
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    proof = matrix["rows"][0]["proofs"]["adversarial"]
    proof["command"] = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'status': 'blocked', 'environment': {'CapEff': '0'}})); raise SystemExit(77)",
    ]
    _write_yaml(matrix_path, matrix)

    exit_code = preflight.run_evidence(task_dir, "S-1", "adversarial")
    results = yaml.safe_load(
        (task_dir / "evidence-results.yaml").read_text(encoding="utf-8")
    )

    assert exit_code == 77
    record = results["results"]["S-1"]["adversarial"]
    assert record["status"] == "blocked"
    assert record["actor_role"] == "code_developer"
    assert record["exit_code"] == 77


def test_run_keeps_structured_attack_failure_as_failed(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    matrix_path = task_dir / "matrix.yaml"
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    proof = matrix["rows"][0]["proofs"]["adversarial"]
    proof["command"] = [
        sys.executable,
        "-c",
        "import json; print(json.dumps({'status': 'failed', 'error_type': 'AssertionError'})); raise SystemExit(1)",
    ]
    _write_yaml(matrix_path, matrix)

    exit_code = preflight.run_evidence(task_dir, "S-1", "adversarial")
    results = yaml.safe_load(
        (task_dir / "evidence-results.yaml").read_text(encoding="utf-8")
    )

    assert exit_code == 1
    assert results["results"]["S-1"]["adversarial"]["status"] == "failed"


def test_draft_allows_planned_controls_without_evaluating_evidence(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    task_path = task_dir / "task.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["declared_state"] = "DRAFT"
    task["transition_history"] = [
        {"from": None, "to": "DRAFT", "actor_role": "project_owner"}
    ]
    task["scope_paths"] = ["planned.py"]
    _write_yaml(task_path, task)
    matrix_path = task_dir / "matrix.yaml"
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    matrix["rows"][0]["control_status"] = "planned"
    matrix["rows"][0]["controls"] = ["planned.py"]
    matrix["rows"][0]["authoritative_control"] = "planned.py"
    _write_yaml(matrix_path, matrix)

    result = preflight.compile_review(task_dir)
    report = yaml.safe_load(result.report_path.read_text(encoding="utf-8"))

    assert result.effective_state == "DRAFT"
    assert report["rows"]["S-1"]["status"] == "not_evaluated"
    assert "snapshot" not in report


def test_code_complete_rejects_planned_controls(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    matrix_path = task_dir / "matrix.yaml"
    matrix = yaml.safe_load(matrix_path.read_text(encoding="utf-8"))
    matrix["rows"][0]["control_status"] = "planned"
    matrix["rows"][0]["controls"] = ["planned.py"]
    matrix["rows"][0]["authoritative_control"] = "planned.py"
    _write_yaml(matrix_path, matrix)

    result = preflight.compile_review(task_dir)

    assert result.effective_state == "PREFLIGHT_INVALID"
    assert any("cannot keep planned controls" in error for error in result.errors)


def test_phase_catalog_accepts_historical_and_preflight_sources(tmp_path: Path) -> None:
    _workspace(tmp_path)
    catalog_path = tmp_path / "tasks" / "phase-registry.yaml"
    _write_yaml(
        catalog_path,
        {
            "schema_version": 1,
            "phases": [
                {
                    "id": "M0",
                    "status": "HISTORICAL_ACCEPTED",
                    "status_source": "historical_review",
                    "depends_on": [],
                    "evidence_refs": ["architecture.md"],
                    "change_policy": "new_task_and_matrix",
                },
                {
                    "id": "M1",
                    "status": "EVIDENCE_BLOCKED",
                    "status_source": "preflight",
                    "depends_on": ["M0"],
                    "task_dir": "tasks/sample",
                },
            ],
        },
    )

    assert preflight.validate_phase_catalog(catalog_path) == []


def test_phase_catalog_rejects_status_drift(tmp_path: Path) -> None:
    _workspace(tmp_path)
    catalog_path = tmp_path / "tasks" / "phase-registry.yaml"
    _write_yaml(
        catalog_path,
        {
            "schema_version": 1,
            "phases": [
                {
                    "id": "M0",
                    "status": "PREFLIGHT_PASSED",
                    "status_source": "preflight",
                    "depends_on": [],
                    "task_dir": "tasks/sample",
                }
            ],
        },
    )

    errors = preflight.validate_phase_catalog(catalog_path)

    assert any("does not match preflight" in error for error in errors)


def test_phase_catalog_rejects_missing_expected_phase(tmp_path: Path) -> None:
    _workspace(tmp_path)
    catalog_path = tmp_path / "tasks" / "phase-registry.yaml"
    _write_yaml(
        catalog_path,
        {
            "schema_version": 1,
            "expected_phase_ids": ["M-1", "M0"],
            "phases": [
                {
                    "id": "M-1",
                    "status": "HISTORICAL_ACCEPTED",
                    "status_source": "historical_review",
                    "depends_on": [],
                    "evidence_refs": ["architecture.md"],
                    "change_policy": "new_task_and_matrix",
                }
            ],
        },
    )

    errors = preflight.validate_phase_catalog(catalog_path)

    assert any("do not match expected order" in error for error in errors)


def test_compile_cli_distinguishes_valid_draft_from_invalid_input(tmp_path: Path) -> None:
    task_dir = _workspace(tmp_path)
    task_path = task_dir / "task.yaml"
    task = yaml.safe_load(task_path.read_text(encoding="utf-8"))
    task["declared_state"] = "DRAFT"
    task["transition_history"] = [
        {"from": None, "to": "DRAFT", "actor_role": "project_owner"}
    ]
    _write_yaml(task_path, task)

    assert preflight.main(["compile", str(task_dir)]) == 4
