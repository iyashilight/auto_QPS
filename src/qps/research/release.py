"""Immutable StrategyRelease builder and MetaDB-backed store."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from qps.core.errors import DataError
from qps.core.models.foundation import ApprovalRecord, StrategyRelease
from qps.infra.storage.meta_db import MetaDB

if TYPE_CHECKING:
    from qps.core.models.strategy import StrategyVersion


class ReleaseBuilder:
    """Build a self-contained release from a registered StrategyVersion."""

    def __init__(self, store: ReleaseStore, *, manifests: Any = None,
                 feature_registry: Any = None, evidence_store: Any = None) -> None:
        self.store = store
        self.manifests = manifests
        self.feature_registry = feature_registry
        self.evidence_store = evidence_store

    async def build(self, version: StrategyVersion, *, parameter_set: dict[str, Any],
                    feature_set_ids: tuple[str, ...], dataset_manifest_ids: tuple[str, ...],
                    cost_model_version: str, environment_lock: dict[str, Any],
                    requested_capabilities: tuple[str, ...], allowed_instruments: tuple[str, ...],
                    allowed_timeframes: tuple[str, ...], evidence_report_id: str,
                    created_by: str) -> StrategyRelease:
        if not version.code_content:
            raise DataError(detail="StrategyVersion 缺少可归档 code_content")
        if not feature_set_ids or not dataset_manifest_ids:
            raise DataError(detail="Release 必须引用 feature set 和 formal PIT dataset manifest")
        from qps.research.yaml_parser import validate_params
        try:
            validate_params(parameter_set, version.params_schema)
        except Exception as exc:
            raise DataError(detail=f"Release 参数不符合 StrategyVersion schema: {exc}") from exc
        dependency_lineage = await self._verify_dependencies(
            feature_set_ids, dataset_manifest_ids, allowed_instruments, allowed_timeframes)
        parameter_hash = _hash_json(parameter_set)
        environment_hash = _hash_json(environment_lock)
        manifest = {
            "strategy_id": version.strategy_id, "strategy_version": version.version_no,
            "code_content_hash": hashlib.sha256(version.code_content.encode()).hexdigest(),
            "parameter_set_hash": parameter_hash, "feature_set_ids": list(feature_set_ids),
            "dataset_manifest_ids": list(dataset_manifest_ids), "cost_model_version": cost_model_version,
            "environment_lock_hash": environment_hash, "evidence_report_id": evidence_report_id,
            "requested_capabilities": list(requested_capabilities), "allowed_instruments": list(allowed_instruments),
            "allowed_timeframes": list(allowed_timeframes), "generator_version": "m2-v05-2",
            "dependency_lineage": dependency_lineage or [],
        }
        manifest_hash = _hash_json(manifest)
        release_id = f"rel-{manifest_hash[:32]}"
        if self.evidence_store is not None:
            if not self.evidence_store.is_report_selected(
                release_id=release_id, report_id=evidence_report_id,
            ):
                raise DataError(detail="Release evidence 必须先由人工选择 append-only EvidenceReport")
            report = self.evidence_store.get_report(evidence_report_id)
            if not report.formal or report.lineage is None:
                raise DataError(detail="Release evidence 必须是 lineage 完整的 formal report")
            artifact_id = getattr(report, "formal_artifact_id", None)
            if artifact_id is not None:
                artifact = self.evidence_store.get_formal_artifact_by_id(artifact_id)
                if artifact is None or report.lineage != self.evidence_store.lineage_from_artifact(artifact):
                    raise DataError(detail="Release evidence 与 formal artifact lineage 不一致")
        release = StrategyRelease(release_id=release_id, strategy_id=version.strategy_id,
            strategy_version=version.version_no, code_content_hash=str(manifest["code_content_hash"]),
            parameter_set_hash=parameter_hash, feature_set_ids=feature_set_ids,
            dataset_manifest_ids=dataset_manifest_ids, cost_model_version=cost_model_version,
            environment_lock_hash=environment_hash, evidence_report_id=evidence_report_id,
            requested_capabilities=requested_capabilities, allowed_instruments=allowed_instruments,
            allowed_timeframes=allowed_timeframes, manifest_hash=manifest_hash,
            created_at=datetime.now(UTC), created_by=created_by)
        archive = self.store.archive_root / release_id
        if archive.exists():
            return await self.store.get_release(release_id)
        temporary = self.store.archive_root / f".{release_id}.tmp"
        try:
            temporary.mkdir(parents=True)
            (temporary / "code.py").write_text(version.code_content, encoding="utf-8")
            (temporary / "parameters.json").write_text(_canonical(parameter_set), encoding="utf-8")
            (temporary / "environment.json").write_text(_canonical(environment_lock), encoding="utf-8")
            for path in temporary.iterdir():
                path.chmod(0o444)
            files = [_file_record(temporary / name) for name in ("code.py", "parameters.json", "environment.json")]
            manifest["archive_files"] = files
            manifest_hash = _hash_json(manifest)
            release = release.model_copy(update={"manifest_hash": manifest_hash})
            (temporary / "release-manifest.json").write_text(
                _canonical(manifest | {"release_id": release_id, "manifest_hash": manifest_hash}), encoding="utf-8")
            (temporary / "release-manifest.json").chmod(0o444)
            for path in temporary.iterdir():
                with path.open("rb") as handle:
                    os.fsync(handle.fileno())
            temporary.rename(archive)
            directory_fd = os.open(self.store.archive_root, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            await self.store.save_release(release)
        except Exception:
            if temporary.exists():
                shutil.rmtree(temporary)
            if archive.exists() and not await self.store.has_release(release_id):
                shutil.rmtree(archive)
            raise
        return release

    async def _verify_dependencies(self, feature_ids: tuple[str, ...], dataset_ids: tuple[str, ...],
                                   instruments: tuple[str, ...], timeframes: tuple[str, ...]) -> list[dict[str, Any]]:
        if self.manifests is None or self.feature_registry is None:
            raise DataError(detail="ReleaseBuilder 缺少 formal ManifestStore/FeatureRegistry")
        lineage: list[dict[str, Any]] = []
        dataset_ids_seen: set[str] = set()
        for manifest_id in dataset_ids:
            manifest = self.manifests.get(manifest_id)
            self.manifests.verify_pit_manifest(manifest, expected_dataset_types={manifest.dataset_type},
                instrument_id=instruments[0] if len(instruments) == 1 else None,
                interval=timeframes[0] if len(timeframes) == 1 else None, require_formal=True)
            dataset_ids_seen.add(manifest_id)
            lineage.append({"dataset_manifest_id": manifest_id})
        feature_input_ids: set[str] = set()
        for feature_id in feature_ids:
            materialization = await self.feature_registry.get_materialization(feature_id)
            feature_input_ids.update(materialization.input_manifest_ids)
            lineage.append({"feature_set_id": feature_id,
                             "input_manifest_ids": list(materialization.input_manifest_ids),
                             "output_manifest_id": materialization.output_manifest_id})
            for manifest_id in (*materialization.input_manifest_ids, materialization.output_manifest_id):
                manifest = self.manifests.get(manifest_id)
                self.manifests.verify_pit_manifest(manifest, expected_dataset_types={manifest.dataset_type},
                    instrument_id=instruments[0] if len(instruments) == 1 else None,
                    interval=timeframes[0] if len(timeframes) == 1 else None, require_formal=True)
        if feature_input_ids != dataset_ids_seen:
            raise DataError(detail="Release dataset manifest 集合与 Feature 输入 lineage 不一致")
        return lineage


class ReleaseStore:
    """ReleaseStorePort implementation with immutable archive verification."""

    def __init__(self, db_path: Path | str, archive_root: Path | str | None = None) -> None:
        self.db = MetaDB(Path(db_path))
        self.db.initialize()
        self.archive_root = Path(archive_root or Path(db_path).parent / "releases").resolve()
        self.archive_root.mkdir(parents=True, exist_ok=True)
        self.reconcile_archives()

    async def save_release(self, release: StrategyRelease) -> None:
        self.verify_archive(release)
        values = _release_values(release)
        with self.db.transaction() as conn:
            row = conn.execute("SELECT * FROM strategy_releases WHERE release_id=?", (release.release_id,)).fetchone()
            if row is not None:
                if tuple(row) != tuple(values):
                    raise DataError(detail=f"Release 内容冲突: {release.release_id}")
                return
            conn.execute("INSERT INTO strategy_releases (release_id,strategy_id,strategy_version,code_content_hash,parameter_set_hash,feature_set_ids,dataset_manifest_ids,cost_model_version,environment_lock_hash,evidence_report_id,requested_capabilities,allowed_instruments,allowed_timeframes,manifest_hash,status,created_at,created_by,approved_at,approved_by) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)

    async def has_release(self, release_id: str) -> bool:
        with self.db.connect() as conn:
            return conn.execute("SELECT 1 FROM strategy_releases WHERE release_id=?", (release_id,)).fetchone() is not None

    def reconcile_archives(self) -> list[str]:
        """Remove temp/orphan archives before any Release can be used."""
        with self.db.connect() as conn:
            known = {str(row[0]) for row in conn.execute("SELECT release_id FROM strategy_releases")}
        removed: list[str] = []
        for path in self.archive_root.iterdir():
            if path.name.startswith(".") or path.name not in known:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                elif path.is_file() or path.is_symlink():
                    path.unlink()
                removed.append(path.name)
        return removed

    async def get_release(self, release_id: str) -> StrategyRelease:
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM strategy_releases WHERE release_id=?", (release_id,)).fetchone()
        if row is None:
            raise DataError(detail=f"Release 不存在: {release_id}")
        release = _row_to_release(row)
        self.verify_archive(release)
        return release

    async def save_approval(self, approval: ApprovalRecord) -> None:
        await self.get_release(approval.release_id)
        values = (approval.approval_id, approval.release_id, approval.status.value, approval.approved_by,
                  approval.reason, approval.created_at.isoformat())
        with self.db.transaction() as conn:
            row = conn.execute("SELECT approval_id,release_id,status,approved_by,reason,created_at FROM approval_records WHERE approval_id=?", (approval.approval_id,)).fetchone()
            if row is not None and tuple(row) != values:
                raise DataError(detail=f"审批记录冲突: {approval.approval_id}")
            if row is None:
                conn.execute("INSERT INTO approval_records (approval_id,release_id,status,approved_by,reason,created_at) VALUES (?,?,?,?,?,?)", values)
                conn.execute(
                    "INSERT INTO audit_log (log_id,actor,action,target_type,target_id,before,after,ts,reason) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f"approval-audit-{approval.approval_id}", approval.approved_by, "RELEASE_APPROVAL",
                     "STRATEGY_RELEASE", approval.release_id, None, _canonical({"status": approval.status.value,
                     "reason": approval.reason}), approval.created_at.isoformat(), "append-only approval"),
                )

    def verify_archive(self, release: StrategyRelease) -> None:
        archive = self.archive_root / release.release_id
        if not archive.is_dir() or not (archive / "code.py").is_file() or not (archive / "release-manifest.json").is_file():
            raise DataError(detail=f"Release archive 缺失: {release.release_id}")
        paths = list(archive.rglob("*"))
        if any(path.is_symlink() for path in paths):
            raise DataError(detail="Release archive 禁止 symlink")
        if any(path.is_dir() for path in paths):
            raise DataError(detail="Release archive 不得包含额外目录")
        manifest = json.loads((archive / "release-manifest.json").read_text(encoding="utf-8"))
        expected_files = {"code.py", "parameters.json", "environment.json"}
        records = manifest.get("archive_files")
        if not isinstance(records, list) or {record.get("path") for record in records} != expected_files:
            raise DataError(detail="Release archive file set 不匹配")
        actual_files = {path.name for path in paths if path.is_file() and path.name != "release-manifest.json"}
        if actual_files != expected_files:
            raise DataError(detail="Release archive 存在未登记文件")
        for record in records:
            path = archive / str(record["path"])
            actual = _file_record(path)
            if actual != record:
                raise DataError(detail=f"Release archive 文件 hash/size/mode 不匹配: {path.name}")
        code = (archive / "code.py").read_text(encoding="utf-8")
        parameters = json.loads((archive / "parameters.json").read_text(encoding="utf-8"))
        environment = json.loads((archive / "environment.json").read_text(encoding="utf-8"))
        if _hash_json(parameters) != release.parameter_set_hash or _hash_json(environment) != release.environment_lock_hash:
            raise DataError(detail="Release parameters/environment hash 不匹配")
        if hashlib.sha256(code.encode()).hexdigest() != release.code_content_hash:
            raise DataError(detail="Release code hash 不匹配")
        for field, expected in (("release_id", release.release_id), ("strategy_id", release.strategy_id),
                                ("strategy_version", release.strategy_version),
                                ("code_content_hash", release.code_content_hash),
                                ("parameter_set_hash", release.parameter_set_hash),
                                ("environment_lock_hash", release.environment_lock_hash),
                                ("feature_set_ids", list(release.feature_set_ids)),
                                ("dataset_manifest_ids", list(release.dataset_manifest_ids)),
                                ("allowed_instruments", list(release.allowed_instruments)),
                                ("allowed_timeframes", list(release.allowed_timeframes))):
            if manifest.get(field) != expected:
                raise DataError(detail=f"Release manifest 字段不匹配: {field}")
        actual = dict(manifest)
        actual.pop("release_id", None)
        actual.pop("manifest_hash", None)
        if _hash_json(actual) != release.manifest_hash or manifest.get("manifest_hash") != release.manifest_hash:
            raise DataError(detail="Release manifest hash 不匹配")


def _file_record(path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {"path": path.name, "sha256": hashlib.sha256(data).hexdigest(),
            "size": len(data), "mode": stat.S_IMODE(path.stat().st_mode)}


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_json(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _release_values(release: StrategyRelease) -> tuple[object, ...]:
    return (release.release_id, release.strategy_id, str(release.strategy_version), release.code_content_hash,
        release.parameter_set_hash, _canonical(list(release.feature_set_ids)), _canonical(list(release.dataset_manifest_ids)),
        release.cost_model_version, release.environment_lock_hash, release.evidence_report_id,
        _canonical(list(release.requested_capabilities)), _canonical(list(release.allowed_instruments)),
        _canonical(list(release.allowed_timeframes)), release.manifest_hash, release.status.value,
        release.created_at.isoformat(), release.created_by, release.approved_at.isoformat() if release.approved_at else None,
        release.approved_by)


def _row_to_release(row: Any) -> StrategyRelease:
    return StrategyRelease(release_id=row["release_id"], strategy_id=row["strategy_id"], strategy_version=row["strategy_version"],
        code_content_hash=row["code_content_hash"], parameter_set_hash=row["parameter_set_hash"],
        feature_set_ids=tuple(json.loads(row["feature_set_ids"])), dataset_manifest_ids=tuple(json.loads(row["dataset_manifest_ids"])),
        cost_model_version=row["cost_model_version"], environment_lock_hash=row["environment_lock_hash"],
        evidence_report_id=row["evidence_report_id"], requested_capabilities=tuple(json.loads(row["requested_capabilities"])),
        allowed_instruments=tuple(json.loads(row["allowed_instruments"])), allowed_timeframes=tuple(json.loads(row["allowed_timeframes"])),
        manifest_hash=row["manifest_hash"], status=row["status"], created_at=row["created_at"], created_by=row["created_by"],
        approved_at=row["approved_at"], approved_by=row["approved_by"])


class ReleaseEvidenceBoundary:
    """把人工选择和 Release 引用分成独立、可审计的边界。"""

    def __init__(self, evidence_store: Any) -> None:
        self.evidence_store = evidence_store

    def select_for_release(self, *, release_id: str, report_id: str, actor: str,
                           reason: str, selected_at: datetime) -> None:
        self.evidence_store.select_report(
            selection_id=f"selection:{release_id}:{report_id}", release_id=release_id,
            report_id=report_id, actor=actor, reason=reason, selected_at=selected_at,
        )

    def can_reference(self, *, release_id: str, report_id: str) -> bool:
        return cast("bool", self.evidence_store.is_report_selected(release_id=release_id, report_id=report_id))
