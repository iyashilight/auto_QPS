#!/usr/bin/env python3
"""Run M2's capability-positive Linux isolation probes through the real Runtime."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import platform
import secrets
import socket
import subprocess
from datetime import UTC, datetime
from pathlib import Path

COMMANDS = {
    "m2-isolation-network",
    "m2-isolation-filesystem-secret",
    "m2-isolation-resource-limits",
    "m2-isolation-process-group-cleanup",
}


def _root() -> Path:
    value = os.environ.get("QPS_M2_FIXTURE_DIR")
    return Path(value).resolve() if value else Path("/tmp/autoqps-m2-v05-isolation").resolve()


def _environment(command: str) -> dict[str, object]:
    capabilities: dict[str, str] = {}
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith(("CapEff:", "CapBnd:", "Seccomp:")):
                key, value = line.split(":", 1)
                capabilities[key] = value.strip()
    except OSError:
        pass
    return {
        "command": command,
        "platform": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "uid": os.getuid(),
        "capabilities": capabilities,
        "cwd": str(Path.cwd()),
    }


def _namespace_probe() -> tuple[bool, list[dict[str, object]]]:
    probes: list[dict[str, object]] = []
    for flag in ("-n", "-m"):
        result = subprocess.run(
            ["unshare", flag, "true"], capture_output=True, text=True, check=False,
        )
        probes.append({
            "flag": flag, "exit_code": result.returncode,
            "stderr": result.stderr.strip()[:500],
        })
    return all(item["exit_code"] == 0 for item in probes), probes


async def _attack_release(ids: dict[str, str], root: Path, name: str, code: str):
    """Create a disposable Release whose behavior is the attack under test."""
    from qps.core.models.strategy import StrategyVersion
    from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
    from qps.infra.storage.meta_db import MetaDB
    from qps.research.registry import StrategyRegistry
    from qps.research.release import ReleaseBuilder, ReleaseStore

    data_dir = Path(ids["data_dir"])
    db_path = data_dir / "meta.db"
    manifests = ManifestStore(data_dir, MetaDB(db_path))
    registry = PersistentFeatureRegistry(MetaDB(db_path), manifests)
    original = await StrategyRegistry(db_path).get_version("m2_fixture", 1)
    strategy_id = f"m2_isolation_{name}"
    version = StrategyVersion(
        strategy_id=strategy_id, version_no=1, code_ref=f"fixture:{name}", code_content=code,
        params_schema=original.params_schema, data_dependencies=original.data_dependencies,
        feature_dependencies=original.feature_dependencies, runtime_constraints={},
        asset_class_support=original.asset_class_support, content_hash=hashlib.sha256(code.encode()).hexdigest(),
        source=original.source, created_at=datetime.now(UTC),
    )
    release = await ReleaseBuilder(
        ReleaseStore(db_path), manifests=manifests, feature_registry=registry,
    ).build(
        version, parameter_set={}, feature_set_ids=(ids["feature_materialization_id"],),
        dataset_manifest_ids=(ids["bars_manifest_id"], ids["funding_manifest_id"], ids["mark_price_manifest_id"]),
        cost_model_version="m2-isolation", environment_lock={"fixture": "isolation", "name": name},
        requested_capabilities=(), allowed_instruments=("binance-btc-usdt-perp",), allowed_timeframes=("5m",),
        evidence_report_id=f"m2-isolation-{name}", created_by="capability-positive-linux-runner",
    )
    return release, ReleaseStore(db_path)


def _deployment(deployment_id: str, release_id: str):
    from qps.core.constants import BrokerMode
    from qps.core.models.strategy import Deployment

    return Deployment(
        deployment_id=deployment_id, strategy_id="m2_isolation", version_no=1,
        account_id="m2-evidence", mode=BrokerMode.PAPER, release_id=release_id,
        created_at=datetime.now(UTC),
    )


async def _runtime(release_store, root: Path, name: str, **kwargs):
    from qps.core.sdk.formal import FormalDecisionInputFactory
    from qps.data.formal_materializer import FormalDecisionMaterializer
    from qps.data.manifest import ManifestStore, PersistentFeatureRegistry
    from qps.data.provider import DataProvider
    from qps.trading.runtime.process_runtime import IsolatedStrategyRuntime
    from qps.trading.runtime.state import StateStore

    manifest_store = ManifestStore(release_store.db.db_path.parent, release_store.db)
    feature_registry = PersistentFeatureRegistry(release_store.db, manifest_store)
    return IsolatedStrategyRuntime(
        release_store, timeout_seconds=2.0, state_store=StateStore(root / f"state-{name}.db"),
        input_factory=FormalDecisionInputFactory(
            root / f"decision-inputs-{name}", materializer=FormalDecisionMaterializer(
                data_port=DataProvider(release_store.db.db_path.parent, release_store.db, allow_legacy=False),
                manifest_store=manifest_store, feature_registry=feature_registry)), **kwargs,
    )


async def _execute_trusted_input(runtime, deployment, release) -> dict[str, object]:
    """Materialize the persisted trust-root artifact before exercising the child."""
    decision_time = datetime(2026, 1, 1, 11, 0, tzinfo=UTC)
    decision_input = await runtime.input_factory.materialize(
        release=release, decision_time=decision_time,
        instrument_id=release.allowed_instruments[0], interval=release.allowed_timeframes[0])
    return await runtime.execute(
        deployment.deployment_id, decision_time=decision_time,
        input_snapshot_ref=decision_input.input_snapshot_ref,
    )


async def _network(ids: dict[str, str], root: Path) -> dict[str, object]:
    from qps.core.errors import BacktestError

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    code = f'''import socket
class Strategy:
    def initialize(self, ctx):
        sock = socket.socket()
        try:
            sock.settimeout(0.2)
            sock.connect(("127.0.0.1", {port}))
            raise RuntimeError("ISOLATION_BREACH_NETWORK")
        except OSError:
            pass
        finally:
            sock.close()
    def on_bar(self, ctx, bar):
        pass
'''
    release, store = await _attack_release(ids, root, "network", code)
    runtime = await _runtime(store, root, "network")
    deployment = _deployment("m2-network", release.release_id)
    try:
        await runtime.start(deployment)
        await _execute_trusted_input(runtime, deployment, release)
        listener.settimeout(0.5)
        try:
            connection, _ = listener.accept()
        except TimeoutError:
            connection = None
        if connection is not None:
            connection.close()
            raise AssertionError("child successfully connected to parent listener")
        return {"assertion": "child socket connection failed in new network namespace", "passed": True}
    except BacktestError as exc:
        if "ISOLATION_BREACH_NETWORK" in str(exc):
            raise AssertionError("network namespace did not isolate child socket") from exc
        raise
    finally:
        await runtime.stop(deployment.deployment_id)
        listener.close()


async def _filesystem(ids: dict[str, str], root: Path) -> dict[str, object]:
    secret = secrets.token_urlsafe(24)
    sentinel = root / "outside-workspace-sentinel"
    sentinel.write_text(secret, encoding="utf-8")
    os.environ["QPS_M2_SECRET"] = secret
    escaped = str(sentinel)
    code = f'''import os
class Strategy:
    def initialize(self, ctx):
        for path in ({escaped!r}, "/etc/shadow"):
            try:
                with open(path, encoding="utf-8") as handle:
                    handle.read(1)
                raise RuntimeError("ISOLATION_BREACH_FILESYSTEM")
            except FileNotFoundError:
                pass
        if os.environ.get("QPS_M2_SECRET"):
            raise RuntimeError("ISOLATION_BREACH_SECRET_ENV")
    def on_bar(self, ctx, bar):
        pass
'''
    release, store = await _attack_release(ids, root, "filesystem", code)
    runtime = await _runtime(store, root, "filesystem")
    deployment = _deployment("m2-filesystem", release.release_id)
    try:
        await runtime.start(deployment)
        await _execute_trusted_input(runtime, deployment, release)
        return {"assertions": ["archive-outside sentinel unreadable", "/etc/shadow unreadable",
                                "QPS_M2_SECRET absent from child environment", "no secret in IPC"], "passed": True}
    except Exception as exc:
        if "ISOLATION_BREACH" in str(exc):
            raise AssertionError("child escaped filesystem root or received secret") from exc
        raise
    finally:
        await runtime.stop(deployment.deployment_id)
        os.environ.pop("QPS_M2_SECRET", None)
        sentinel.unlink(missing_ok=True)


async def _resource_case(ids: dict[str, str], root: Path, name: str, code: str) -> dict[str, object]:
    release, store = await _attack_release(ids, root, name, code)
    runtime = await _runtime(store, root, name)
    deployment = _deployment(f"m2-resource-{name}", release.release_id)
    try:
        try:
            await runtime.start(deployment)
            await _execute_trusted_input(runtime, deployment, release)
        except Exception as exc:
            return {"resource": name, "child_rejected_or_terminated": True,
                    "error_type": type(exc).__name__}
        raise AssertionError(f"child exceeded {name} limit without observable failure")
    finally:
        await runtime.stop(deployment.deployment_id)


async def _resources(ids: dict[str, str], root: Path) -> dict[str, object]:
    cases = {
        "cpu": "class Strategy:\n    def initialize(self, ctx):\n        while True: pass\n    def on_bar(self, ctx, bar): pass\n",
        "address-space": "class Strategy:\n    def initialize(self, ctx):\n        self.value = bytearray(512 * 1024 * 1024)\n    def on_bar(self, ctx, bar): pass\n",
        "process-count": "class Strategy:\n    def initialize(self, ctx):\n        import os\n        children = []\n        for _ in range(128):\n            pid = os.fork()\n            if pid == 0:\n                os._exit(0)\n            children.append(pid)\n    def on_bar(self, ctx, bar): pass\n",
    }
    results = [await _resource_case(ids, root, name, code) for name, code in cases.items()]
    return {"assertions": results, "passed": all(item["child_rejected_or_terminated"] for item in results)}


def _process_group_members(group_id: int) -> list[int]:
    members: list[int] = []
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            content = (entry / "stat").read_text(encoding="utf-8")
            _, after_name = content.split(") ", 1)
            fields = after_name.split()
            if int(fields[2]) == group_id:
                members.append(int(entry.name))
        except (OSError, ValueError, IndexError):
            continue
    return members


async def _process_cleanup(ids: dict[str, str], root: Path) -> dict[str, object]:
    code = '''import subprocess
class Strategy:
    def initialize(self, ctx):
        subprocess.Popen(["sleep", "60"])
    def on_bar(self, ctx, bar):
        pass
'''
    stable_code = "class Strategy:\n    def initialize(self, ctx): pass\n    def on_bar(self, ctx, bar): pass\n"
    first_release, first_store = await _attack_release(ids, root, "cleanup", code)
    second_release, second_store = await _attack_release(ids, root, "stable", stable_code)
    runtime = await _runtime(first_store, root, "cleanup")
    second_runtime = await _runtime(second_store, root, "stable")
    first = _deployment("m2-cleanup", first_release.release_id)
    second = _deployment("m2-stable", second_release.release_id)
    try:
        await runtime.start(first)
        await _execute_trusted_input(runtime, first, first_release)
        await second_runtime.start(second)
        await _execute_trusted_input(second_runtime, second, second_release)
        group_id = os.getpgid(runtime._states[first.deployment_id].process.pid)  # type: ignore[attr-defined]
        if not _process_group_members(group_id):
            raise AssertionError("descendant process was not created")
        await runtime.stop(first.deployment_id)
        remaining = _process_group_members(group_id)
        stable_status = await second_runtime.status(second.deployment_id)
        if remaining or stable_status.get("status") != "RUNNING":
            raise AssertionError(f"process group cleanup failed: remaining={remaining}, stable={stable_status}")
        return {"process_group": group_id, "remaining_descendants": remaining,
                "independent_deployment": stable_status, "passed": True}
    finally:
        await runtime.stop(first.deployment_id)
        await second_runtime.stop(second.deployment_id)


async def _run(command: str, root: Path) -> dict[str, object]:
    from m2_external_fixture_runner import _build_fixture

    ids = await _build_fixture(root)
    if command == "m2-isolation-network":
        return await _network(ids, root)
    if command == "m2-isolation-filesystem-secret":
        return await _filesystem(ids, root)
    if command == "m2-isolation-resource-limits":
        return await _resources(ids, root)
    return await _process_cleanup(ids, root)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=sorted(COMMANDS))
    args = parser.parse_args()
    environment = _environment(args.command)
    capable, probes = _namespace_probe()
    environment["namespace_probes"] = probes
    if not capable:
        print(json.dumps({"status": "blocked", "reason": "required Linux network/mount namespace capability unavailable",
                          "environment": environment}, sort_keys=True))
        return 77
    try:
        result = asyncio.run(_run(args.command, _root()))
        print(json.dumps({"status": "passed" if result.get("passed") else "failed",
                          "environment": environment, "result": result}, sort_keys=True))
        return 0 if result.get("passed") else 1
    except Exception as exc:
        print(json.dumps({"status": "failed", "environment": environment,
                          "error_type": type(exc).__name__, "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
