"""Fail-closed, archive-only strategy subprocess runtime."""

from __future__ import annotations

import asyncio
import ctypes
import json
import os
import resource
import shutil
import signal
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from qps.core.errors import BacktestError, DataError
from qps.core.sdk.formal import (
    FormalDecisionInputFactory,
    build_validated_intent,
    content_hash,
)
from qps.trading.runtime.state import CommittedState, StateStore

if TYPE_CHECKING:
    from qps.core.models.strategy import Deployment
    from qps.research.release import ReleaseStore


_WORKER = r'''
import contextlib, io, json, pathlib, sys

archive = pathlib.Path(sys.argv[1]).resolve()
release_id = sys.argv[2]
deployment_id = sys.argv[3]
sys.path.insert(0, sys.argv[4])
from qps.core.sdk.formal import DecisionInput, FormalStrategyExecutor
code = (archive / "code.py").read_text(encoding="utf-8")
params = json.loads((archive / "parameters.json").read_text(encoding="utf-8"))
namespace = {"__name__": "__qps_release__", "__file__": str(archive / "code.py")}
with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
    exec(compile(code, str(archive / "code.py"), "exec"), namespace)
strategy_type = namespace.get("Strategy")
if strategy_type is None:
    raise RuntimeError("Release archive 没有 Strategy 类")
strategy = strategy_type()
state = {}

executor = FormalStrategyExecutor(strategy_id=namespace.get("strategy_id", ""),
    version_no=0, deployment_id=deployment_id, release_id=release_id, params=params,
    instruments=(), interval="")

def make_input(message):
    return DecisionInput(input_snapshot_ref=message["input_snapshot_ref"],
        decision_time=__import__("datetime").datetime.fromisoformat(message["decision_time"]),
        available_at=__import__("datetime").datetime.fromisoformat(message["available_at"]),
        instrument_id=message["instruments"][0], interval=message["interval"],
        payload=message.get("input_snapshot", {}), source_manifest_ids=tuple(message.get("source_manifest_ids", ())),
        feature_materialization_ids=tuple(message.get("feature_materialization_ids", ())),
        source_manifest_hashes=tuple(tuple(item) for item in message.get("source_manifest_hashes", ())),
        feature_materialization_hashes=tuple(tuple(item) for item in message.get("feature_materialization_hashes", ())),
        source_row_hash=message["source_row_hash"],
        materializer_schema_version=message["materializer_schema_version"],
        decision_request=message["decision_request"])

def make_bar(value):
    from qps.core.models import Bar
    return Bar.model_validate(value)

initialized = False
for raw_line in sys.stdin:
    message = json.loads(raw_line)
    if message.get("protocol_version") != 1 or message.get("release_id") != release_id or message.get("deployment_id") != deployment_id:
        print(json.dumps({"protocol_version": 1, "type": "error", "error_type": "PROTOCOL_REJECTED"}), flush=True)
        continue
    if message.get("type") == "stop":
        break
    if message.get("type") == "ready":
        try:
            executor = FormalStrategyExecutor(strategy_id=message["strategy_id"],
                version_no=message["version_no"], deployment_id=deployment_id, release_id=release_id,
                params=params, instruments=tuple(message["instruments"]), interval=message["interval"])
            # Initialization is deliberately deferred until execute carries a persisted,
            # Release-bound DecisionInput artifact.  The ready handshake is not evidence.
            initialized = False
            print(json.dumps({"protocol_version": 1, "type": "ready"}), flush=True)
        except Exception as exc:
            print(json.dumps({"protocol_version": 1, "type": "error", "error_type": "STRATEGY_FAILED", "error": str(exc)}), flush=True)
        continue
    if message.get("type") != "execute":
        print(json.dumps({"protocol_version": 1, "type": "error", "error_type": "UNKNOWN_MESSAGE"}), flush=True)
        continue
    incoming = message.get("state")
    if incoming is not None:
        state.clear()
        state.update(incoming)
    try:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            if not initialized:
                executor.initialize(strategy, make_input(message))
                initialized = True
            payload = message["input_snapshot"]
            callback = getattr(strategy, "on_snapshot", None)
            if callback is None:
                callback = getattr(strategy, "on_bar", None)
                if callback is None:
                    raise RuntimeError("Strategy 没有 on_snapshot 或 on_bar")
                run = lambda ctx: callback(ctx, make_bar(payload["bar"] if "bar" in payload else payload))
            else:
                run = lambda ctx: callback(ctx, payload)
            result = executor.execute(strategy, make_input(message), state, run)
        print(json.dumps({"protocol_version": 1, "type": "result", **result}), flush=True)
    except Exception as exc:
        print(json.dumps({"protocol_version": 1, "type": "error", "error_type": "STRATEGY_FAILED",
                          "error": str(exc)}), flush=True)
'''


@dataclass
class _ProcessState:
    process: asyncio.subprocess.Process
    started_at: str
    errors: int = 0
    last_error: str | None = None
    status: str = "RUNNING"
    sandbox_root: str | None = None


class LinuxIsolationBackend:
    """Linux capability boundary used by production Runtime."""

    _PR_CAPBSET_DROP = 24
    _PR_SET_NO_NEW_PRIVS = 38
    _UNPRIVILEGED_ID_BASE = 100_000

    def available(self) -> bool:
        return self._unshare_available("-n") and self._unshare_available("-m")

    @staticmethod
    def _unshare_available(namespace: str) -> bool:
        result = subprocess.run(["unshare", namespace, "true"], capture_output=True, check=False)
        return result.returncode == 0

    def prepare_root(self, archive: Path, root: Path) -> Path:
        sandbox = Path(tempfile.mkdtemp(prefix="qps-runtime-", dir=root))
        sandbox.chmod(0o755)
        for source in archive.iterdir():
            if source.is_symlink() or source.is_dir():
                shutil.rmtree(sandbox, ignore_errors=True)
                raise DataError(detail="Release archive 不能包含 symlink/目录")
            target = sandbox / source.name
            shutil.copyfile(source, target)
            target.chmod(0o444)
        return sandbox

    @staticmethod
    def preexec(limits: Any, sandbox: Path, sdk_root: Path, python_root: Path) -> None:
        os.setsid()
        limits()
        cap_last = int(Path("/proc/sys/kernel/cap_last_cap").read_text(encoding="ascii").strip())
        os.unshare(os.CLONE_NEWNET | os.CLONE_NEWNS)
        subprocess.run(["mount", "--make-rprivate", "/"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        bind_sources = (Path("/usr"), Path("/lib"), Path("/lib64"), python_root, sdk_root)
        for source in bind_sources:
            if not source.exists():
                continue
            target = sandbox / source.relative_to("/")
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run(["mount", "--rbind", str(source), str(target)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mount", "--make-rslave", str(target)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run(["mount", "-o", "remount,bind,ro", str(target)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        os.chroot(sandbox)
        os.chdir("/")
        LinuxIsolationBackend._drop_privileges(cap_last)

    @staticmethod
    def _drop_privileges(cap_last: int) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        for capability in range(cap_last + 1):
            if libc.prctl(LinuxIsolationBackend._PR_CAPBSET_DROP, capability, 0, 0, 0) != 0:
                error = ctypes.get_errno()
                raise OSError(error, os.strerror(error))
        if libc.prctl(LinuxIsolationBackend._PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        isolated_id = LinuxIsolationBackend._UNPRIVILEGED_ID_BASE + os.getpid()
        os.setgroups([])
        os.setgid(isolated_id)
        os.setuid(isolated_id)

    @staticmethod
    def cleanup(sandbox: str | None) -> None:
        if sandbox:
            shutil.rmtree(sandbox, ignore_errors=True)


class IsolatedStrategyRuntime:
    """One process per deployment; no trading capabilities cross IPC."""

    def __init__(self, release_store: ReleaseStore, *, timeout_seconds: float = 5.0,
                 max_message_bytes: int = 1_048_576, require_os_isolation: bool = True,
                 cpu_seconds: int = 2, memory_bytes: int = 256 * 1024 * 1024,
                 state_store: StateStore | None = None,
                 input_factory: FormalDecisionInputFactory | None = None) -> None:
        if state_store is None:
            raise ValueError("正式 Runtime 必须注入 StateStore")
        self.release_store = release_store
        self.timeout_seconds = timeout_seconds
        self.max_message_bytes = max_message_bytes
        self.require_os_isolation = require_os_isolation
        self.cpu_seconds = cpu_seconds
        self.memory_bytes = memory_bytes
        self.state_store = state_store
        if input_factory is None:
            raise ValueError("正式 Runtime 必须注入带 ManifestStore/FeatureRegistry 信任根的 InputFactory")
        self.input_factory = input_factory
        self.isolation = LinuxIsolationBackend()
        self._states: dict[str, _ProcessState] = {}
        self._deployments: dict[str, Deployment] = {}

    async def start(self, deployment: Deployment) -> None:
        if not deployment.release_id:
            raise BacktestError(detail="Runtime 必须从 release_id 启动")
        if self.require_os_isolation and not self.isolation.available():
            raise BacktestError(detail="隔离能力不可用: Linux network/mount namespace 创建失败")
        release = await self.release_store.get_release(deployment.release_id)
        archive = (self.release_store.archive_root / release.release_id).resolve()
        if archive.parent != self.release_store.archive_root or archive.is_symlink() or not archive.is_dir():
            raise DataError(detail="非法 Release archive 路径")
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONUNBUFFERED": "1", "QPS_RUNTIME": "1"}
        sandbox_root: Path | None = None
        archive_arg = archive
        preexec = self._limits
        process_cwd = archive
        if self.require_os_isolation:
            sandbox_root = self.isolation.prepare_root(archive, self.release_store.archive_root)
            archive_arg = Path("/")
            process_cwd = sandbox_root
            sdk_root = Path(__file__).resolve().parents[3]
            python_root = Path(sys.prefix).resolve()
            preexec = lambda: self.isolation.preexec(self._limits, sandbox_root, sdk_root, python_root)  # noqa: E731
        else:
            sdk_root = Path(__file__).resolve().parents[3]
        process = await asyncio.create_subprocess_exec(sys.executable, "-I", "-c", _WORKER,
            str(archive_arg), release.release_id, deployment.deployment_id, str(sdk_root),
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env, cwd=str(process_cwd),
            preexec_fn=preexec)
        self._states[deployment.deployment_id] = _ProcessState(process, datetime.now(UTC).isoformat(),
            sandbox_root=str(sandbox_root) if sandbox_root else None)
        self._deployments[deployment.deployment_id] = deployment
        try:
            ready = await self._request(deployment.deployment_id, {
                "protocol_version": 1, "type": "ready", "release_id": release.release_id,
                "deployment_id": deployment.deployment_id, "strategy_id": release.strategy_id,
                "version_no": release.strategy_version, "decision_time": datetime.now(UTC).isoformat(),
                "interval": release.allowed_timeframes[0], "instruments": list(release.allowed_instruments),
            })
            if ready.get("type") != "ready":
                raise BacktestError(
                    detail=str(ready.get("error", ready.get("error_type", "策略启动握手失败")))
                )
        except Exception:
            await self.stop(deployment.deployment_id)
            raise

    async def execute(self, deployment_id: str, *, decision_time: datetime,
                      input_snapshot_ref: str | None = None,
                      input_snapshot: dict[str, Any] | None = None,
                      source_manifest_ids: tuple[str, ...] | None = None,
                      feature_materialization_ids: tuple[str, ...] | None = None,
                      state: dict[str, Any] | None = None) -> dict[str, Any]:
        deployment = self._deployments.get(deployment_id)
        if deployment is None or not deployment.release_id:
            raise BacktestError(detail=f"deployment 不存在: {deployment_id}")
        if decision_time.tzinfo is None or decision_time.utcoffset() != timedelta_zero():
            raise BacktestError(detail="decision_time 必须是 UTC")
        if input_snapshot is not None:
            raise BacktestError(detail="正式 Runtime 不接受调用方 input_snapshot payload")
        if input_snapshot_ref is None:
            raise BacktestError(detail="正式 Runtime 必须使用已持久化 DecisionInput artifact ref")
        if source_manifest_ids is not None or feature_materialization_ids is not None:
            raise BacktestError(detail="正式 Runtime 不接受调用方 source/feature ID 作为信任证明")
        release = await self.release_store.get_release(deployment.release_id)
        try:
            decision_input = await self.input_factory.load_verified(
                release=release, input_snapshot_ref=input_snapshot_ref)
        except Exception as exc:
            raise BacktestError(detail=f"DecisionInput artifact 校验失败: {exc}") from exc
        if decision_input.decision_time != decision_time:
            raise BacktestError(detail="DecisionInput decision_time 与 Runtime 请求不一致")
        committed = self.state_store.replay(deployment_id, input_snapshot_ref)
        if committed is not None:
            if committed.release_id != release.release_id or committed.input_hash != decision_input.payload_hash:
                raise BacktestError(detail="已提交 state 与 DecisionInput lineage 不一致")
            return {"protocol_version": 1, "type": "result", "intent": committed.intent,
                    "event_id": committed.event_id,
                    "state": committed.payload, "state_hash": committed.state_hash,
                    "input_snapshot_ref": input_snapshot_ref, "input_hash": committed.input_hash,
                    "lineage_hash": decision_input.lineage_hash, "audit_hash": committed.intent_hash,
                    "replayed": True}
        if state is None:
            latest = self.state_store.latest(deployment_id)
            state = latest.payload if latest else None
        message = {"protocol_version": 1, "type": "execute", "release_id": release.release_id,
            "deployment_id": deployment_id, "strategy_id": release.strategy_id,
            "version_no": release.strategy_version, "decision_time": decision_time.isoformat(),
            "interval": release.allowed_timeframes[0], "instruments": list(release.allowed_instruments),
            "available_at": decision_input.available_at.isoformat(),
            "input_snapshot": cast("Any", decision_input.payload).to_plain(), "input_snapshot_ref": input_snapshot_ref,
            "input_hash": decision_input.payload_hash, "lineage_hash": decision_input.lineage_hash,
            "source_manifest_ids": list(decision_input.source_manifest_ids),
            "source_manifest_hashes": [list(item) for item in decision_input.source_manifest_hashes],
            "feature_materialization_ids": list(decision_input.feature_materialization_ids),
            "feature_materialization_hashes": [list(item) for item in decision_input.feature_materialization_hashes],
            "source_row_hash": decision_input.source_row_hash,
            "materializer_schema_version": decision_input.materializer_schema_version,
            "decision_request": cast("Any", decision_input.decision_request).to_plain(),
            "state": state}
        result = await self._request(deployment_id, message)
        if result.get("type") == "error":
            raise BacktestError(detail=str(result.get("error", result.get("error_type"))))
        result = self._validate_result(result, release=release, deployment=deployment,
            decision_time=decision_time, input_snapshot_ref=input_snapshot_ref,
            input_hash=decision_input.payload_hash, lineage_hash=decision_input.lineage_hash)
        previous = self.state_store.latest(deployment_id)
        intent_hash = content_hash(result.get("intent"))
        self.state_store.commit(CommittedState(deployment_id, release.release_id, input_snapshot_ref,
            (previous.state_version + 1) if previous else 1, str(result["state_hash"]), intent_hash,
            cast("dict[str, Any]", result.get("state", {})), input_hash=decision_input.payload_hash,
            previous_state_hash=previous.state_hash if previous else "", event_id=str(result.get("event_id") or ""),
            intent=cast("dict[str, Any] | None", result.get("intent")), decision_time=decision_time.isoformat()))
        return result

    @staticmethod
    def _validate_result(result: dict[str, Any], *, release: Any, deployment: Any,
                         decision_time: datetime, input_snapshot_ref: str,
                         input_hash: str, lineage_hash: str) -> dict[str, Any]:
        if result.get("input_snapshot_ref") != input_snapshot_ref or result.get("input_hash") != input_hash \
                or result.get("lineage_hash") != lineage_hash:
            raise BacktestError(detail="Strategy result DecisionInput lineage 不匹配")
        event = result.get("event")
        if event is None:
            intent = None
            event_id = None
        else:
            try:
                payload = event["payload"]
                raw_intent = payload["intent"]
                raw = {key: raw_intent[key] for key in (
                    "direction", "action", "quantity", "notional", "order_type", "limit_price",
                    "instrument_id", "intent_priority", "reason_tags", "raw_payload") if key in raw_intent}
                validated = build_validated_intent(raw=raw, strategy_id=release.strategy_id,
                    version_no=release.strategy_version, deployment_id=deployment.deployment_id,
                    release_id=release.release_id, interval=release.allowed_timeframes[0],
                    instruments=tuple(release.allowed_instruments), decision_time=decision_time,
                    input_snapshot_ref=input_snapshot_ref)
                intent = validated["intent"]
                occurred = event.get("occurred_at")
                recorded = event.get("recorded_at")
                if event.get("event_type") != "INTENT_CREATED" or event.get("schema_version") != 1 \
                        or event.get("source") != f"strategy_runtime.{release.strategy_id}" \
                        or occurred is None or recorded is None \
                        or datetime.fromisoformat(str(occurred).replace("Z", "+00:00")) != decision_time \
                        or datetime.fromisoformat(str(recorded).replace("Z", "+00:00")) != decision_time:
                    raise ValueError("child Intent event metadata 被篡改")
                if raw_intent != intent or event.get("event_id") != validated["event_id"]:
                    raise ValueError("child Intent 与 parent canonical Intent 不一致")
                event_id = validated["event_id"]
            except Exception as exc:
                raise BacktestError(detail=f"Intent 校验失败: {exc}") from exc
        state = result.get("state", {})
        state_hash = content_hash(state)
        if result.get("state_hash") not in {None, state_hash}:
            raise BacktestError(detail="state_hash 不匹配")
        canonical_event = None if event is None else validated["event"]
        return {"protocol_version": 1, "type": "result", "event": canonical_event,
                "intent": intent, "event_id": event_id, "state": state,
                "state_hash": state_hash, "input_snapshot_ref": input_snapshot_ref,
                "input_hash": input_hash, "lineage_hash": lineage_hash,
                "audit_hash": content_hash({"event_id": event_id, "state_hash": state_hash,
                                              "input_snapshot_ref": input_snapshot_ref})}

    async def stop(self, deployment_id: str) -> None:
        state = self._states.pop(deployment_id, None)
        self._deployments.pop(deployment_id, None)
        if state is None:
            return
        if state.process.stdin is not None:
            state.process.stdin.close()
        if state.process.returncode is None:
            try:
                os.killpg(state.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                state.process.terminate()
            try:
                await asyncio.wait_for(state.process.wait(), self.timeout_seconds)
            except TimeoutError:
                try:
                    os.killpg(state.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    state.process.kill()
                await state.process.wait()
        self.isolation.cleanup(state.sandbox_root)

    async def restart(self, deployment_id: str) -> None:
        deployment = self._deployments.get(deployment_id)
        if deployment is None:
            raise BacktestError(detail=f"deployment 不存在: {deployment_id}")
        await self.stop(deployment_id)
        await self.start(deployment)

    async def status(self, deployment_id: str) -> dict[str, Any]:
        state = self._states.get(deployment_id)
        if state is None:
            return {"status": "STOPPED", "deployment_id": deployment_id}
        if state.process.returncode is not None and state.status == "RUNNING":
            state.status = "FAILED"
        return {"status": state.status, "deployment_id": deployment_id,
                "started_at": state.started_at, "error_count": state.errors, "last_error": state.last_error}

    async def _request(self, deployment_id: str, message: dict[str, Any]) -> dict[str, Any]:
        state = self._states.get(deployment_id)
        if state is None or state.status != "RUNNING" or state.process.returncode is not None:
            raise BacktestError(detail=f"策略进程不可用: {deployment_id}")
        encoded = json.dumps(message, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(encoded.encode()) > self.max_message_bytes:
            state.status = "FAILED"
            raise BacktestError(detail="IPC 消息超过大小上限")
        try:
            assert state.process.stdin is not None and state.process.stdout is not None
            state.process.stdin.write((encoded + "\n").encode())
            await state.process.stdin.drain()
            line = await asyncio.wait_for(state.process.stdout.readline(), self.timeout_seconds)
            if not line or len(line) > self.max_message_bytes:
                raise BacktestError(detail="策略进程返回坏 IPC")
            result = cast("dict[str, Any]", json.loads(line.decode()))
            if result.get("protocol_version") != 1 or result.get("type") not in {"ready", "result", "error"}:
                raise BacktestError(detail="策略进程协议污染")
            allowed = {
                "ready": {"protocol_version", "type"},
                "result": {"protocol_version", "type", "event", "state", "input_snapshot_ref",
                            "input_hash", "lineage_hash"},
                "error": {"protocol_version", "type", "error_type", "error"},
            }[str(result["type"])]
            if set(result) - allowed:
                raise BacktestError(detail="策略进程返回未知 IPC 字段")
            return result
        except Exception as exc:
            state.errors += 1
            state.last_error = str(exc)
            state.status = "FAILED"
            if state.process.returncode is None:
                try:
                    os.killpg(state.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    state.process.kill()
                await state.process.wait()
            raise BacktestError(detail=f"策略进程故障: {exc}") from exc

    def _limits(self) -> None:
        resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_seconds, self.cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (self.memory_bytes, self.memory_bytes))
        resource.setrlimit(resource.RLIMIT_NPROC, (16, 16))

    def _isolated_limits(self) -> None:
        self._limits()
        os.unshare(os.CLONE_NEWNET)


def timedelta_zero() -> Any:
    from datetime import timedelta
    return timedelta(0)
