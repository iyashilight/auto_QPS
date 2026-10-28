#!/usr/bin/env python3
"""Compile task evidence into a fixed review package.

This tool deliberately validates evidence structure and freshness, not domain
correctness. A reviewer still has to inspect the fixed source snapshot and
independently reproduce critical probes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

SCHEMA_VERSION = 1
ALLOWED_PROOF_TYPES = {
    "static",
    "unit",
    "integration",
    "adversarial",
    "recovery",
    "mutation",
}
ALLOWED_RESULT_STATES = {"passed", "failed", "blocked"}
ALLOWED_RISKS = {"low", "standard", "critical"}
ALLOWED_CONTROL_STATES = {"implemented", "planned"}
ALLOWED_PHASE_STATES = {
    "HISTORICAL_ACCEPTED",
    "DRAFT",
    "DESIGN_APPROVED",
    "IMPLEMENTING",
    "CODE_COMPLETE",
    "EVIDENCE_BLOCKED",
    "PREFLIGHT_PASSED",
    "ACCEPTED",
}
DEVELOPER_STATES = {"DRAFT", "DESIGN_APPROVED", "IMPLEMENTING", "CODE_COMPLETE"}
TRANSITION_ACTORS: dict[tuple[str | None, str], set[str]] = {
    (None, "DRAFT"): {"project_owner", "architecture_reviewer"},
    ("DRAFT", "DESIGN_APPROVED"): {"architecture_reviewer"},
    ("DESIGN_APPROVED", "IMPLEMENTING"): {"code_developer"},
    ("IMPLEMENTING", "CODE_COMPLETE"): {"code_developer"},
    ("REVIEW_FAILED", "IMPLEMENTING"): {"code_developer"},
    ("REVIEW_FAILED_DESIGN", "DRAFT"): {"architecture_reviewer"},
}


class PreflightError(ValueError):
    """Raised when governance inputs are malformed."""


@dataclass(frozen=True)
class CompileResult:
    effective_state: str
    report_path: Path
    bundle_path: Path | None
    errors: tuple[str, ...]


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise PreflightError(f"missing YAML file: {path}")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise PreflightError(f"YAML root must be a mapping: {path}")
    return value


def _dump_yaml(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _workspace_root(task_dir: Path) -> Path:
    for candidate in (task_dir, *task_dir.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    raise PreflightError(f"cannot locate workspace root from {task_dir}")


def _resolve(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PreflightError(f"path escapes workspace: {relative}") from exc
    return candidate


def _expand_files(root: Path, paths: list[str]) -> list[Path]:
    files: set[Path] = set()
    for relative in paths:
        path = _resolve(root, relative)
        if path.is_file():
            files.add(path)
        elif path.is_dir():
            files.update(
                item
                for item in path.rglob("*")
                if item.is_file() and "review-bundle" not in item.parts
            )
        else:
            raise PreflightError(f"snapshot path does not exist: {relative}")
    return sorted(files)


def _hash_files(root: Path, paths: list[str]) -> tuple[str, dict[str, str]]:
    digest = hashlib.sha256()
    entries: dict[str, str] = {}
    for path in _expand_files(root, paths):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        item_hash = hashlib.sha256(content).hexdigest()
        entries[relative] = item_hash
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item_hash.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), entries


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _architecture_hash(root: Path, task: dict[str, Any]) -> str:
    refs = _as_string_list(task.get("architecture_refs"), "architecture_refs")
    digest, _ = _hash_files(root, refs)
    return digest


def _task_hash(task_dir: Path) -> str:
    return _hash_bytes((task_dir / "task.yaml").read_bytes())


def _as_string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise PreflightError(f"{field} must be a non-empty string list")
    return value


def _validate_transitions(task: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    history = task.get("transition_history")
    if not isinstance(history, list) or not history:
        return ["transition_history must contain at least the DRAFT transition"]
    previous: str | None = None
    for index, transition in enumerate(history):
        if not isinstance(transition, dict):
            errors.append(f"transition_history[{index}] must be a mapping")
            continue
        source = transition.get("from")
        target = transition.get("to")
        actor = transition.get("actor_role")
        if source != previous:
            errors.append(
                f"transition_history[{index}] starts at {source!r}, expected {previous!r}"
            )
        allowed = TRANSITION_ACTORS.get((source, target), set())
        if actor not in allowed:
            errors.append(
                f"transition_history[{index}] actor {actor!r} cannot perform {source}->{target}"
            )
        previous = target if isinstance(target, str) else previous
    declared_state = task.get("declared_state")
    if declared_state != previous:
        errors.append(
            f"declared_state {declared_state!r} does not match transition history {previous!r}"
        )
    if declared_state not in DEVELOPER_STATES:
        errors.append(
            "task.yaml may only declare states through CODE_COMPLETE; preflight/review states are derived"
        )
    return errors


def _validate_matrix(
    root: Path, task: dict[str, Any], matrix: dict[str, Any]
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    errors: list[str] = []
    if matrix.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"unsupported matrix schema_version: {matrix.get('schema_version')!r}")
    if matrix.get("matrix_id") != task.get("matrix_id"):
        errors.append("task matrix_id does not match matrix.yaml")
    rows_value = matrix.get("rows")
    if not isinstance(rows_value, list) or not rows_value:
        return {}, [*errors, "matrix rows must be a non-empty list"]

    rows: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(rows_value):
        if not isinstance(row, dict):
            errors.append(f"rows[{index}] must be a mapping")
            continue
        row_id = row.get("id")
        if not isinstance(row_id, str) or not row_id:
            errors.append(f"rows[{index}] has no id")
            continue
        if row_id in rows:
            errors.append(f"duplicate matrix row id: {row_id}")
            continue
        rows[row_id] = row
        for field in ("invariant", "entrypoint", "authoritative_control"):
            if not isinstance(row.get(field), str) or not row[field]:
                errors.append(f"{row_id}.{field} must be a non-empty string")
        if row.get("risk") not in ALLOWED_RISKS:
            errors.append(f"{row_id}.risk must be one of {sorted(ALLOWED_RISKS)}")
        control_status = row.get("control_status", "implemented")
        if control_status not in ALLOWED_CONTROL_STATES:
            errors.append(
                f"{row_id}.control_status must be one of {sorted(ALLOWED_CONTROL_STATES)}"
            )
        if control_status == "planned" and task.get("declared_state") == "CODE_COMPLETE":
            errors.append(f"{row_id} cannot keep planned controls at CODE_COMPLETE")
        try:
            controls = _as_string_list(row.get("controls"), f"{row_id}.controls")
        except PreflightError as exc:
            errors.append(str(exc))
            controls = []
        if row.get("authoritative_control") not in controls:
            errors.append(f"{row_id}.authoritative_control must be listed in controls")
        for control in controls:
            try:
                path = _resolve(root, control)
                if control_status == "implemented" and not path.is_file():
                    errors.append(f"{row_id} control does not exist: {control}")
            except PreflightError as exc:
                errors.append(str(exc))
        dependencies = row.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            errors.append(f"{row_id}.depends_on must be a string list")
        required = row.get("required_proofs")
        if not isinstance(required, list) or not required:
            errors.append(f"{row_id}.required_proofs must not be empty")
            required = []
        elif len(required) != len(set(required)):
            errors.append(f"{row_id}.required_proofs contains duplicates")
        unknown = set(required) - ALLOWED_PROOF_TYPES
        if unknown:
            errors.append(f"{row_id} has unknown proof types: {sorted(unknown)}")
        if row.get("risk") == "critical" and not (
            set(required) & {"integration", "adversarial", "recovery", "mutation"}
        ):
            errors.append(f"{row_id} critical proof cannot be static/unit only")
        proofs = row.get("proofs")
        if not isinstance(proofs, dict):
            errors.append(f"{row_id}.proofs must be a mapping")
            continue
        for proof_type in required:
            proof = proofs.get(proof_type)
            if not isinstance(proof, dict):
                errors.append(f"{row_id}.proofs.{proof_type} is missing")
                continue
            command = proof.get("command")
            if not isinstance(command, list) or not all(
                isinstance(part, str) and part for part in command
            ):
                errors.append(f"{row_id}.{proof_type}.command must be an argv list")
            if not isinstance(proof.get("expected"), str) or not proof["expected"]:
                errors.append(f"{row_id}.{proof_type}.expected is required")
            if not isinstance(proof.get("environment"), str) or not proof["environment"]:
                errors.append(f"{row_id}.{proof_type}.environment is required")

    for row_id, row in rows.items():
        for dependency in row.get("depends_on", []):
            if dependency not in rows:
                errors.append(f"{row_id} depends on unknown row {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(row_id: str) -> None:
        if row_id in visiting:
            errors.append(f"matrix dependency cycle includes {row_id}")
            return
        if row_id in visited:
            return
        visiting.add(row_id)
        for dependency in rows[row_id].get("depends_on", []):
            if dependency in rows:
                visit(dependency)
        visiting.remove(row_id)
        visited.add(row_id)

    for row_id in rows:
        visit(row_id)
    return rows, errors


def _row_control_hash(root: Path, row: dict[str, Any]) -> str:
    digest, _ = _hash_files(root, row["controls"])
    return digest


def _environment() -> dict[str, str]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "hostname": platform.node(),
    }


def record_evidence(
    task_dir: Path,
    row_id: str,
    proof_type: str,
    *,
    status: str,
    actual: str,
    actor_role: str,
    environment_id: str,
    exit_code: int | None = None,
) -> Path:
    task_dir = task_dir.resolve()
    root = _workspace_root(task_dir)
    task = _load_yaml(task_dir / "task.yaml")
    matrix_path = _resolve(root, str(task["matrix_file"]))
    matrix = _load_yaml(matrix_path)
    rows, errors = _validate_matrix(root, task, matrix)
    if errors:
        raise PreflightError("; ".join(errors))
    if row_id not in rows:
        raise PreflightError(f"unknown matrix row: {row_id}")
    row = rows[row_id]
    if row.get("control_status", "implemented") != "implemented":
        raise PreflightError(f"{row_id} controls are still planned")
    if proof_type not in row.get("required_proofs", []):
        raise PreflightError(f"{row_id} does not require proof type {proof_type}")
    if status not in ALLOWED_RESULT_STATES:
        raise PreflightError(f"invalid evidence status: {status}")
    if status in {"passed", "failed"} and actor_role != "evidence_validator":
        raise PreflightError("passed/failed evidence may only be recorded by evidence_validator")
    if status == "blocked" and actor_role not in {
        "code_developer",
        "evidence_validator",
    }:
        raise PreflightError("blocked evidence requires code_developer or evidence_validator")
    proof = row["proofs"][proof_type]
    if environment_id != proof["environment"]:
        raise PreflightError(
            f"evidence environment {environment_id!r} does not match required "
            f"{proof['environment']!r}"
        )

    results_path = _resolve(root, str(task["evidence_results_file"]))
    current_matrix_hash = _hash_bytes(matrix_path.read_bytes())
    current_architecture_hash = _architecture_hash(root, task)
    current_task_hash = _task_hash(task_dir)
    if results_path.exists():
        results = _load_yaml(results_path)
        recorded_matrix_hash = results.get("matrix_hash")
        recorded_architecture_hash = results.get("architecture_hash")
        recorded_task_hash = results.get("task_hash")
        if recorded_matrix_hash not in {None, current_matrix_hash}:
            raise PreflightError("matrix changed without a new matrix_id/evidence file")
        if recorded_architecture_hash not in {None, current_architecture_hash}:
            raise PreflightError("architecture changed without a new matrix_id/evidence file")
        if recorded_task_hash not in {None, current_task_hash}:
            raise PreflightError("task definition changed without a new matrix_id/evidence file")
    else:
        results = {
            "schema_version": SCHEMA_VERSION,
            "matrix_id": task["matrix_id"],
            "matrix_hash": current_matrix_hash,
            "architecture_hash": current_architecture_hash,
            "task_hash": current_task_hash,
            "results": {},
        }
    results.setdefault("matrix_hash", current_matrix_hash)
    results.setdefault("architecture_hash", current_architecture_hash)
    results.setdefault("task_hash", current_task_hash)
    all_results = results.setdefault("results", {})
    row_results = all_results.setdefault(row_id, {})
    record: dict[str, Any] = {
        "status": status,
        "actual": actual,
        "actor_role": actor_role,
        "environment_id": environment_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "control_hash": _row_control_hash(root, row),
        "command": proof["command"],
    }
    if exit_code is not None:
        record["exit_code"] = exit_code
    row_results[proof_type] = record
    _dump_yaml(results_path, results)
    return results_path


def _sanitize_capability_output(
    output: str, *, command: list[str], status: str, exit_code: int
) -> str:
    """Keep only non-sensitive capability metadata from a structured runner result."""
    candidate: Any = None
    for line in reversed(output.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            candidate = parsed
            break
    if not isinstance(candidate, dict) or not isinstance(candidate.get("environment"), dict):
        return output

    environment = candidate["environment"]
    safe_environment: dict[str, Any] = {
        key: environment[key]
        for key in ("platform", "kernel", "python", "machine", "uid")
        if key in environment
    }
    capabilities = environment.get("capabilities")
    if isinstance(capabilities, dict):
        safe_environment["capabilities"] = {
            key: capabilities[key]
            for key in ("CapEff", "CapBnd", "Seccomp")
            if key in capabilities
        }
    probes = environment.get("namespace_probes")
    if isinstance(probes, list):
        safe_environment["namespace_probes"] = [
            {
                key: probe[key]
                for key in ("flag", "exit_code", "stderr")
                if isinstance(probe, dict) and key in probe
            }
            for probe in probes
            if isinstance(probe, dict)
        ]
    safe: dict[str, Any] = {
        "status": status,
        "command": command,
        "exit_code": exit_code,
        "environment": safe_environment,
    }
    if "reason" in candidate:
        safe["reason"] = candidate["reason"]
    if "result" in candidate:
        result = candidate["result"]
        if isinstance(result, dict):
            safe["result_keys"] = sorted(str(key) for key in result)
            if "passed" in result:
                safe["result_passed"] = result["passed"]
        else:
            safe["result_type"] = type(result).__name__
    if "error_type" in candidate:
        safe["error_type"] = candidate["error_type"]
    return json.dumps(safe, ensure_ascii=False, sort_keys=True)


def run_evidence(task_dir: Path, row_id: str, proof_type: str) -> int:
    task_dir = task_dir.resolve()
    root = _workspace_root(task_dir)
    task = _load_yaml(task_dir / "task.yaml")
    matrix = _load_yaml(_resolve(root, str(task["matrix_file"])))
    rows, errors = _validate_matrix(root, task, matrix)
    if errors:
        raise PreflightError("; ".join(errors))
    row = rows.get(row_id)
    if row is None or proof_type not in row.get("required_proofs", []):
        raise PreflightError(f"unknown proof: {row_id}/{proof_type}")
    if row.get("control_status", "implemented") != "implemented":
        raise PreflightError(f"{row_id} controls are still planned")
    proof = row["proofs"][proof_type]
    environment = os.environ.copy()
    environment["PATH"] = str(root / "scripts") + os.pathsep + environment.get("PATH", "")
    completed = subprocess.run(
        proof["command"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    output = (completed.stdout + completed.stderr)[-4000:]
    expected_exit_code = int(proof.get("expected_exit_code", 0))
    structured_status: str | None = None
    for line in reversed(output.splitlines()):
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.get("status") in ALLOWED_RESULT_STATES:
            structured_status = str(candidate["status"])
            break

    if structured_status == "blocked" and completed.returncode == 77:
        status = "blocked"
    elif structured_status in {"failed", "blocked"}:
        status = "failed"
    elif structured_status == "passed" and completed.returncode == expected_exit_code:
        status = "passed"
    else:
        status = "passed" if completed.returncode == expected_exit_code else "failed"
    matched_expectation = status == "passed"
    actor_role = "code_developer" if status == "blocked" else "evidence_validator"
    actual = _sanitize_capability_output(
        output,
        command=[str(part) for part in proof["command"]],
        status=status,
        exit_code=completed.returncode,
    )
    record_evidence(
        task_dir,
        row_id,
        proof_type,
        status=status,
        actual=actual or f"exit code {completed.returncode}",
        actor_role=actor_role,
        environment_id=proof["environment"],
        exit_code=completed.returncode,
    )
    return 0 if matched_expectation else completed.returncode or 1


def compile_review(task_dir: Path, *, write: bool = True) -> CompileResult:
    task_dir = task_dir.resolve()
    root = _workspace_root(task_dir)
    report_path = task_dir / "preflight-report.json"
    bundle_path = task_dir / "review-bundle" / "manifest.json"
    errors: list[str] = []
    try:
        task = _load_yaml(task_dir / "task.yaml")
        if task.get("schema_version") != SCHEMA_VERSION:
            errors.append(f"unsupported task schema_version: {task.get('schema_version')!r}")
        if task.get("risk_level") not in ALLOWED_RISKS:
            errors.append("task risk_level is invalid")
        errors.extend(_validate_transitions(task))
        matrix_path = _resolve(root, str(task["matrix_file"]))
        results_path = _resolve(root, str(task["evidence_results_file"]))
        matrix = _load_yaml(matrix_path)
        rows, matrix_errors = _validate_matrix(root, task, matrix)
        errors.extend(matrix_errors)
        results = _load_yaml(results_path) if results_path.exists() else {"results": {}}
        if results.get("matrix_id") not in {None, task.get("matrix_id")}:
            errors.append("evidence results matrix_id does not match task")
        if results.get("matrix_hash") not in {None, _hash_bytes(matrix_path.read_bytes())}:
            errors.append("matrix changed without a new matrix_id; all evidence is stale")
        if results.get("architecture_hash") not in {None, _architecture_hash(root, task)}:
            errors.append("architecture changed without a new matrix_id; all evidence is stale")
        if results.get("task_hash") not in {None, _task_hash(task_dir)}:
            errors.append("task definition changed without a new matrix_id; all evidence is stale")
    except (KeyError, PreflightError, OSError, yaml.YAMLError) as exc:
        errors.append(str(exc))
        task = {}
        matrix = {}
        rows = {}
        results = {"results": {}}
        matrix_path = task_dir / "matrix.yaml"
        results_path = task_dir / "evidence-results.yaml"

    row_reports: dict[str, dict[str, Any]] = {}
    declared_state = task.get("declared_state") if task else None
    if not errors and declared_state != "CODE_COMPLETE":
        for row_id, row in rows.items():
            row_reports[row_id] = {
                "status": "not_evaluated",
                "reasons": ["task is not CODE_COMPLETE"],
                "proofs": {
                    proof_type: {
                        "status": "not_evaluated",
                        "environment": row["proofs"][proof_type]["environment"],
                    }
                    for proof_type in row["required_proofs"]
                },
                "control_hash": None,
                "control_status": row.get("control_status", "implemented"),
                "depends_on": row.get("depends_on", []),
            }
    elif not errors:
        recorded_results = results.get("results", {})
        if not isinstance(recorded_results, dict):
            errors.append("evidence results must be a mapping")
            recorded_results = {}
        provisional: dict[str, str] = {}
        for row_id, row in rows.items():
            reasons: list[str] = []
            current_hash = _row_control_hash(root, row)
            row_results = recorded_results.get(row_id, {})
            if not isinstance(row_results, dict):
                row_results = {}
            proof_report: dict[str, Any] = {}
            for proof_type in row["required_proofs"]:
                definition = row["proofs"][proof_type]
                record = row_results.get(proof_type)
                proof_state = "missing"
                if isinstance(record, dict):
                    recorded_state = record.get("status")
                    if record.get("command") != definition.get("command"):
                        proof_state = "stale"
                        reasons.append(f"{proof_type}: proof command changed")
                    elif record.get("control_hash") != current_hash:
                        proof_state = "stale"
                        reasons.append(f"{proof_type}: controlled source changed")
                    elif recorded_state in ALLOWED_RESULT_STATES:
                        proof_state = str(recorded_state)
                        if proof_state != "passed":
                            reasons.append(f"{proof_type}: {proof_state}")
                    else:
                        reasons.append(f"{proof_type}: invalid recorded state")
                else:
                    reasons.append(f"{proof_type}: missing")
                proof_report[proof_type] = {
                    "status": proof_state,
                    "environment": definition["environment"],
                }
            status = "passed" if not reasons else "blocked"
            provisional[row_id] = status
            row_reports[row_id] = {
                "status": status,
                "reasons": reasons,
                "proofs": proof_report,
                "control_hash": current_hash,
                "control_status": row.get("control_status", "implemented"),
                "depends_on": row.get("depends_on", []),
            }

        changed = True
        while changed:
            changed = False
            for row_id, row in rows.items():
                blocked_dependencies = [
                    dependency
                    for dependency in row.get("depends_on", [])
                    if provisional.get(dependency) != "passed"
                ]
                if blocked_dependencies and provisional[row_id] == "passed":
                    provisional[row_id] = "blocked"
                    row_reports[row_id]["status"] = "blocked"
                    row_reports[row_id]["reasons"].append(
                        "blocked dependencies: " + ", ".join(blocked_dependencies)
                    )
                    changed = True

    all_rows_passed = bool(row_reports) and all(
        item["status"] == "passed" for item in row_reports.values()
    )
    if errors:
        effective_state = "PREFLIGHT_INVALID"
    elif declared_state != "CODE_COMPLETE":
        effective_state = str(declared_state or "DRAFT")
    elif all_rows_passed:
        effective_state = "PREFLIGHT_PASSED"
    else:
        effective_state = "EVIDENCE_BLOCKED"

    generated_at = datetime.now(UTC).isoformat()
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task.get("task_id") if task else None,
        "matrix_id": task.get("matrix_id") if task else None,
        "declared_state": declared_state,
        "effective_state": effective_state,
        "generated_at": generated_at,
        "errors": errors,
        "rows": row_reports,
    }

    review_manifest: dict[str, Any] | None = None
    if not errors and task and declared_state == "CODE_COMPLETE":
        try:
            scope_paths = _as_string_list(task.get("scope_paths"), "scope_paths")
            control_paths = [
                control for row in rows.values() for control in row.get("controls", [])
            ]
            code_hash, code_entries = _hash_files(root, sorted(set(scope_paths + control_paths)))
            architecture_refs = _as_string_list(
                task.get("architecture_refs"), "architecture_refs"
            )
            architecture_hash, architecture_entries = _hash_files(root, architecture_refs)
            matrix_hash = _hash_bytes(matrix_path.read_bytes())
            evidence_hash = (
                _hash_bytes(results_path.read_bytes()) if results_path.is_file() else None
            )
            environment = _environment()
            snapshot = {
                "task_hash": _task_hash(task_dir),
                "code_snapshot_hash": code_hash,
                "code_files": code_entries,
                "architecture_hash": architecture_hash,
                "architecture_files": architecture_entries,
                "matrix_hash": matrix_hash,
                "evidence_hash": evidence_hash,
                "environment": environment,
                "environment_hash": _hash_bytes(
                    json.dumps(environment, sort_keys=True).encode("utf-8")
                ),
            }
            report["snapshot"] = snapshot
            if effective_state == "PREFLIGHT_PASSED":
                review_manifest = {
                    "schema_version": SCHEMA_VERSION,
                    "task_id": task["task_id"],
                    "matrix_id": task["matrix_id"],
                    "effective_state": effective_state,
                    "created_at": generated_at,
                    "snapshot": snapshot,
                    "matrix_ref": matrix_path.relative_to(root).as_posix(),
                    "task_ref": (task_dir / "task.yaml").relative_to(root).as_posix(),
                    "evidence_ref": results_path.relative_to(root).as_posix(),
                    "review_instructions": (
                        "Inspect the fixed source hashes and independently rerun critical probes; "
                        "the bundle is an index, not a substitute for source review."
                    ),
                }
        except (PreflightError, OSError) as exc:
            errors.append(str(exc))
            report["errors"] = errors
            report["effective_state"] = "PREFLIGHT_INVALID"
            effective_state = "PREFLIGHT_INVALID"
            review_manifest = None

    if write:
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if review_manifest is not None:
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            bundle_path.write_text(
                json.dumps(review_manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        elif bundle_path.exists():
            bundle_path.unlink()

    return CompileResult(
        effective_state=effective_state,
        report_path=report_path,
        bundle_path=bundle_path if review_manifest is not None else None,
        errors=tuple(errors),
    )


def validate_phase_catalog(catalog_path: Path) -> list[str]:
    """Validate the M0-M7 registry against historical evidence or task preflight."""
    catalog_path = catalog_path.resolve()
    root = _workspace_root(catalog_path.parent)
    catalog = _load_yaml(catalog_path)
    errors: list[str] = []
    if catalog.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"unsupported phase catalog schema_version: {catalog.get('schema_version')!r}"
        )
    phases_value = catalog.get("phases")
    if not isinstance(phases_value, list) or not phases_value:
        return [*errors, "phase catalog phases must be a non-empty list"]

    phases: dict[str, dict[str, Any]] = {}
    for index, phase in enumerate(phases_value):
        if not isinstance(phase, dict):
            errors.append(f"phases[{index}] must be a mapping")
            continue
        phase_id = phase.get("id")
        if not isinstance(phase_id, str) or not phase_id:
            errors.append(f"phases[{index}] has no id")
            continue
        if phase_id in phases:
            errors.append(f"duplicate phase id: {phase_id}")
            continue
        phases[phase_id] = phase
        status = phase.get("status")
        if status not in ALLOWED_PHASE_STATES:
            errors.append(f"{phase_id}.status must be one of {sorted(ALLOWED_PHASE_STATES)}")
        dependencies = phase.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item for item in dependencies
        ):
            errors.append(f"{phase_id}.depends_on must be a string list")
        source = phase.get("status_source")
        if source == "historical_review":
            if status != "HISTORICAL_ACCEPTED":
                errors.append(
                    f"{phase_id} historical_review requires HISTORICAL_ACCEPTED status"
                )
            try:
                evidence_refs = _as_string_list(
                    phase.get("evidence_refs"), f"{phase_id}.evidence_refs"
                )
                for reference in evidence_refs:
                    if not _resolve(root, reference).is_file():
                        errors.append(f"{phase_id} evidence ref does not exist: {reference}")
            except PreflightError as exc:
                errors.append(str(exc))
            if phase.get("change_policy") != "new_task_and_matrix":
                errors.append(f"{phase_id} historical baseline requires new_task_and_matrix policy")
        elif source == "preflight":
            task_dir_value = phase.get("task_dir")
            if not isinstance(task_dir_value, str) or not task_dir_value:
                errors.append(f"{phase_id}.task_dir is required for preflight source")
                continue
            try:
                task_dir = _resolve(root, task_dir_value)
                result = compile_review(task_dir, write=False)
                if result.effective_state != status:
                    errors.append(
                        f"{phase_id} catalog status {status!r} does not match "
                        f"preflight {result.effective_state!r}"
                    )
                errors.extend(f"{phase_id}: {error}" for error in result.errors)
            except (PreflightError, OSError, yaml.YAMLError) as exc:
                errors.append(f"{phase_id}: {exc}")
        else:
            errors.append(f"{phase_id}.status_source must be historical_review or preflight")

    expected_phase_ids = catalog.get("expected_phase_ids")
    if expected_phase_ids is not None:
        try:
            expected = _as_string_list(expected_phase_ids, "expected_phase_ids")
            actual = list(phases)
            if actual != expected:
                errors.append(
                    f"phase catalog ids {actual!r} do not match expected order {expected!r}"
                )
        except PreflightError as exc:
            errors.append(str(exc))

    for phase_id, phase in phases.items():
        for dependency in phase.get("depends_on", []):
            if dependency not in phases:
                errors.append(f"{phase_id} depends on unknown phase {dependency}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(phase_id: str) -> None:
        if phase_id in visiting:
            errors.append(f"phase dependency cycle includes {phase_id}")
            return
        if phase_id in visited:
            return
        visiting.add(phase_id)
        for dependency in phases[phase_id].get("depends_on", []):
            if dependency in phases:
                visit(dependency)
        visiting.remove(phase_id)
        visited.add(phase_id)

    for phase_id in phases:
        visit(phase_id)
    return errors


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    compile_parser = subparsers.add_parser("compile", help="validate and compile review evidence")
    compile_parser.add_argument("task_dir", type=Path)

    record_parser = subparsers.add_parser("record", help="record manual/external evidence")
    record_parser.add_argument("task_dir", type=Path)
    record_parser.add_argument("row_id")
    record_parser.add_argument("proof_type", choices=sorted(ALLOWED_PROOF_TYPES))
    record_parser.add_argument("--status", required=True, choices=sorted(ALLOWED_RESULT_STATES))
    record_parser.add_argument("--actual", required=True)
    record_parser.add_argument("--actor-role", required=True)
    record_parser.add_argument("--environment-id", required=True)

    run_parser = subparsers.add_parser("run", help="execute and record one proof command")
    run_parser.add_argument("task_dir", type=Path)
    run_parser.add_argument("row_id")
    run_parser.add_argument("proof_type", choices=sorted(ALLOWED_PROOF_TYPES))

    catalog_parser = subparsers.add_parser(
        "catalog", help="validate the all-stage governance registry"
    )
    catalog_parser.add_argument("catalog_file", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "compile":
            result = compile_review(args.task_dir)
            print(f"{result.effective_state}: {result.report_path}")
            if result.errors:
                for error in result.errors:
                    print(f"ERROR: {error}", file=sys.stderr)
            if result.effective_state == "PREFLIGHT_PASSED":
                return 0
            if result.effective_state == "EVIDENCE_BLOCKED":
                return 3
            if result.effective_state == "PREFLIGHT_INVALID":
                return 2
            return 4
        if args.command == "record":
            path = record_evidence(
                args.task_dir,
                args.row_id,
                args.proof_type,
                status=args.status,
                actual=args.actual,
                actor_role=args.actor_role,
                environment_id=args.environment_id,
            )
            print(path)
            return 0
        if args.command == "run":
            return run_evidence(args.task_dir, args.row_id, args.proof_type)
        if args.command == "catalog":
            errors = validate_phase_catalog(args.catalog_file)
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 2
            print(f"VALID: {args.catalog_file}")
            return 0
    except (PreflightError, OSError, yaml.YAMLError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
