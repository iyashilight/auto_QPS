"""Fail-closed, signed Paper stability evidence (ADR-019).

This module deliberately uses only the Python standard library.  The private
key is accepted by the host collector but is never written to the JournalDB.
All imported records are immutable and are verified before persistence.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

Q = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
D = (-121665 * pow(121666, Q - 2, Q)) % Q
SQRT_M1 = pow(2, (Q - 1) // 4, Q)
B = (15112221349535400772501151409588531511454012693041857206046113283949847762202,
     46316835694926478169428394003475163141307993866256225615783033603165251855960)


class PaperEvidenceError(ValueError):
    """Evidence is invalid, conflicting or cannot be safely imported."""


class PaperEvidenceIncompleteError(PaperEvidenceError):
    """The evidence is valid but the fixed real window is not complete."""


def _inv(x: int) -> int:
    return pow(x, Q - 2, Q)


def _add(p: tuple[int, int], q: tuple[int, int]) -> tuple[int, int]:
    x1, y1 = p
    x2, y2 = q
    z = _inv((1 + D * x1 * x2 * y1 * y2) % Q)
    return ((x1 * y2 + x2 * y1) * z % Q, (y1 * y2 + x1 * x2) * _inv((1 - D * x1 * x2 * y1 * y2) % Q) % Q)


def _mul(p: tuple[int, int], n: int) -> tuple[int, int]:
    result = (0, 1)
    while n:
        if n & 1:
            result = _add(result, p)
        p = _add(p, p)
        n >>= 1
    return result


def _encode(p: tuple[int, int]) -> bytes:
    x, y = p
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode(raw: bytes) -> tuple[int, int]:
    if len(raw) != 32:
        raise PaperEvidenceError("Ed25519 public/signature point length invalid")
    value = int.from_bytes(raw, "little")
    x_sign = value >> 255
    y = value & ((1 << 255) - 1)
    if y >= Q:
        raise PaperEvidenceError("Ed25519 point out of range")
    xx = (y * y - 1) * _inv(D * y * y + 1) % Q
    x = pow(xx, (Q + 3) // 8, Q)
    if (x * x - xx) % Q:
        x = x * SQRT_M1 % Q
    if (x * x - xx) % Q or (x & 1) != x_sign:
        x = Q - x
    if (x & 1) != x_sign:
        raise PaperEvidenceError("Ed25519 point is not on curve")
    return x, y


def _public(seed: bytes) -> bytes:
    if len(seed) != 32:
        raise PaperEvidenceError("Ed25519 seed must be 32 bytes")
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8)
    scalar |= 1 << 254
    return _encode(_mul(B, scalar))


def _sign(seed: bytes, message: bytes) -> bytes:
    digest = hashlib.sha512(seed).digest()
    scalar = int.from_bytes(digest[:32], "little") & ((1 << 254) - 8)
    scalar |= 1 << 254
    public = _encode(_mul(B, scalar))
    nonce = int.from_bytes(hashlib.sha512(digest[32:] + message).digest(), "little") % L
    r_point = _encode(_mul(B, nonce))
    challenge = int.from_bytes(hashlib.sha512(r_point + public + message).digest(), "little") % L
    return r_point + ((nonce + challenge * scalar) % L).to_bytes(32, "little")


def _verify(public: bytes, message: bytes, signature: bytes) -> bool:
    try:
        if len(signature) != 64:
            return False
        r = _decode(signature[:32])
        a = _decode(public)
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % L
        return _encode(_mul(B, s)) == _encode(_add(r, _mul(a, h)))
    except PaperEvidenceError:
        return False


def _canonical(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode()


def _hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _utc(value: str | datetime) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PaperEvidenceError("timestamps must be UTC aware")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class EvidenceLineage:
    deployment_id: str
    release_id: str
    policy_id: str
    account_id: str
    journal_id: str
    signer_fingerprint: str

    def as_dict(self) -> dict[str, str]:
        return self.__dict__.copy()


class PaperEvidenceStore:
    """Dedicated append-only storage for Paper evidence records."""

    def __init__(self, path: Path, journal_id: str | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.journal_id = journal_id or hashlib.sha256(str(self.path.resolve()).encode()).hexdigest()[:32]
        with self.connect() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS paper_evidence_records (
                    observation_id TEXT PRIMARY KEY, record_hash TEXT NOT NULL UNIQUE,
                    recorded_at TEXT NOT NULL, record_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_readiness_reports (
                    report_id TEXT PRIMARY KEY, report_hash TEXT NOT NULL UNIQUE,
                    report_json TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TRIGGER IF NOT EXISTS paper_evidence_no_update
                BEFORE UPDATE ON paper_evidence_records FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'paper evidence append-only'); END;
                CREATE TRIGGER IF NOT EXISTS paper_evidence_no_delete
                BEFORE DELETE ON paper_evidence_records FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'paper evidence append-only'); END;
                CREATE TRIGGER IF NOT EXISTS paper_readiness_no_update
                BEFORE UPDATE ON paper_readiness_reports FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'paper readiness append-only'); END;
                CREATE TRIGGER IF NOT EXISTS paper_readiness_no_delete
                BEFORE DELETE ON paper_readiness_reports FOR EACH ROW
                BEGIN SELECT RAISE(ABORT, 'paper readiness append-only'); END;
            """)

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def records(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [json.loads(row["record_json"]) for row in conn.execute("SELECT record_json FROM paper_evidence_records ORDER BY rowid")]

    def insert(self, record: Mapping[str, Any]) -> bool:
        encoded = _canonical(dict(record))
        record_hash = hashlib.sha256(encoded).hexdigest()
        with self.connect() as conn:
            existing = conn.execute("SELECT record_json FROM paper_evidence_records WHERE observation_id=?", (record["observation_id"],)).fetchone()
            if existing is not None:
                if existing[0] != encoded.decode():
                    raise PaperEvidenceError("conflicting replay for observation_id")
                return False
            conn.execute("INSERT INTO paper_evidence_records VALUES (?,?,?,?)", (record["observation_id"], record_hash, record["recorded_at"], encoded.decode()))
            conn.commit()
            return True

    def append_report(self, report: Mapping[str, Any]) -> None:
        encoded = _canonical(dict(report)).decode()
        report_hash = hashlib.sha256(encoded.encode()).hexdigest()
        with self.connect() as conn:
            conn.execute("INSERT OR IGNORE INTO paper_readiness_reports VALUES (?,?,?,?)", (report["report_id"], report_hash, encoded, report["created_at"]))
            row = conn.execute("SELECT report_json FROM paper_readiness_reports WHERE report_id=?", (report["report_id"],)).fetchone()
            if row is None or row[0] != encoded:
                raise PaperEvidenceError("conflicting readiness report")
            conn.commit()


class PaperEvidenceCollector:
    def __init__(self, store: PaperEvidenceStore, lineage: EvidenceLineage, signing_seed: bytes, *, boot_id: str, real_window: bool = False) -> None:
        self.store = store
        self.lineage = lineage
        self.seed = signing_seed
        self.boot_id = boot_id
        self.real_window = real_window
        if hashlib.sha256(_public(signing_seed)).hexdigest() != lineage.signer_fingerprint:
            raise PaperEvidenceError("signer fingerprint does not match key")

    @property
    def public_key(self) -> bytes:
        return _public(self.seed)

    def append(self, *, kind: str, recorded_at: datetime, monotonic_ns: int, payload: Mapping[str, Any], observation_id: str | None = None, boot_id: str | None = None) -> dict[str, Any]:
        when = _utc(recorded_at)
        prior = self.store.records()
        previous_hash = _hash(prior[-1]) if prior else "0" * 64
        body: dict[str, Any] = {"observation_id": observation_id or f"obs_{secrets.token_hex(12)}", "kind": kind, "lineage": self.lineage.as_dict(), "recorded_at": when.isoformat(), "monotonic_ns": int(monotonic_ns), "boot_id": boot_id or self.boot_id, "previous_hash": previous_hash, "real_window": self.real_window, "payload": dict(payload)}
        body["signer_fingerprint"] = self.lineage.signer_fingerprint
        body["signer_public_key"] = self.public_key.hex()
        body["canonical_payload_hash"] = _hash(body)
        body["signature"] = _sign(self.seed, _canonical(body)).hex()
        self.store.insert(body)
        return body

    def append_observation(self, *, recorded_at: datetime, monotonic_ns: int, input_snapshot_ref: str, intent_event_hash: str, outcome_ref: str, **kwargs: Any) -> dict[str, Any]:
        return self.append(kind="decision", recorded_at=recorded_at, monotonic_ns=monotonic_ns, payload={"input_snapshot_ref": input_snapshot_ref, "intent_event_hash": intent_event_hash, "outcome_ref": outcome_ref}, **kwargs)

    def append_daily_attestation(self, *, day: date, reconciliation: Mapping[str, Any], incidents: Iterable[Mapping[str, Any]], recorded_at: datetime, monotonic_ns: int, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        recon = self.append(kind="daily_reconciliation", recorded_at=recorded_at, monotonic_ns=monotonic_ns, payload={"day": day.isoformat(), "reconciliation": dict(reconciliation)}, **kwargs)
        incident = self.append(kind="incident_attestation", recorded_at=recorded_at, monotonic_ns=monotonic_ns + 1, payload={"day": day.isoformat(), "incidents": [dict(item) for item in incidents]}, **kwargs)
        return recon, incident


class PaperEvidenceImporter:
    def __init__(self, store: PaperEvidenceStore, lineage: EvidenceLineage, public_key: bytes) -> None:
        self.store, self.lineage, self.public_key = store, lineage, public_key

    def import_records(self, records: Iterable[Mapping[str, Any]]) -> int:
        incoming = [dict(item) for item in records]
        existing = self.store.records()
        previous = _hash(existing[-1]) if existing else "0" * 64
        decision_refs = {r.get("payload", {}).get("input_snapshot_ref") for r in existing if r.get("kind") == "decision"}
        last_when = _utc(existing[-1]["recorded_at"]) if existing else None
        last_mono = int(existing[-1]["monotonic_ns"]) if existing else None
        last_boot = existing[-1].get("boot_id") if existing else None
        for record in incoming:
            if record.get("lineage") != self.lineage.as_dict() or record.get("signer_fingerprint") != self.lineage.signer_fingerprint:
                raise PaperEvidenceError("mixed Paper lineage")
            if record.get("signer_public_key") != self.public_key.hex() or hashlib.sha256(self.public_key).hexdigest() != self.lineage.signer_fingerprint:
                raise PaperEvidenceError("public key does not match frozen fingerprint")
            signature = bytes.fromhex(str(record.get("signature", "")))
            unsigned = {k: v for k, v in record.items() if k != "signature"}
            if record.get("canonical_payload_hash") != _hash({k: v for k, v in unsigned.items() if k != "canonical_payload_hash"}) or not _verify(self.public_key, _canonical(unsigned), signature):
                raise PaperEvidenceError("signature or canonical payload hash invalid")
            if record.get("previous_hash") != previous:
                if any(r.get("observation_id") == record.get("observation_id") and _canonical(r) == _canonical(record) for r in existing):
                    continue
                raise PaperEvidenceError("paper evidence hash chain broken")
            when = _utc(str(record["recorded_at"]))
            if last_when is not None and when < last_when:
                raise PaperEvidenceError("UTC clock moved backwards")
            if last_mono is not None and int(record["monotonic_ns"]) < last_mono:
                raise PaperEvidenceError("monotonic clock moved backwards")
            if last_boot is not None and record.get("boot_id") != last_boot and record.get("kind") != "restart_bridge":
                raise PaperEvidenceError("boot change requires signed restart bridge")
            ref = record.get("payload", {}).get("input_snapshot_ref")
            if record.get("kind") == "decision":
                if not ref or ref in decision_refs:
                    raise PaperEvidenceError("duplicate decision input_snapshot_ref")
                decision_refs.add(ref)
            self.store.insert(record)
            existing.append(record)
            previous, last_when, last_mono, last_boot = _hash(record), when, int(record["monotonic_ns"]), record.get("boot_id")
        return len(incoming)


class PaperReadinessEvaluator:
    MIN_CALENDAR_DAYS = 14
    MIN_INDEPENDENT_DECISIONS = 200

    def evaluate_verified_window(self, records: Iterable[Mapping[str, Any]], *, deployment_id: str, public_key_fingerprint: str) -> dict[str, Any]:
        rows = [dict(item) for item in records]
        if not rows:
            raise PaperEvidenceIncompleteError("no verified evidence")
        if any(not row.get("real_window", False) for row in rows):
            raise PaperEvidenceIncompleteError("synthetic or fixture evidence cannot pass real window")
        lineage = rows[0].get("lineage", {})
        if lineage.get("deployment_id") != deployment_id or lineage.get("signer_fingerprint") != public_key_fingerprint or any(row.get("lineage") != lineage for row in rows):
            raise PaperEvidenceError("readiness lineage mismatch")
        decisions = {row["payload"].get("input_snapshot_ref") for row in rows if row.get("kind") == "decision"}
        days = {row["payload"].get("day") for row in rows if row.get("kind") == "daily_reconciliation"}
        incident_days = {row["payload"].get("day") for row in rows if row.get("kind") == "incident_attestation"}
        if len(days) < self.MIN_CALENDAR_DAYS or len(decisions) < self.MIN_INDEPENDENT_DECISIONS or days != incident_days:
            raise PaperEvidenceIncompleteError("fixed 14-day/200-decision window incomplete")
        parsed_days = sorted(date.fromisoformat(day) for day in days)
        if any(parsed_days[i] - parsed_days[i - 1] != timedelta(days=1) for i in range(1, len(parsed_days))):
            raise PaperEvidenceError("daily UTC calendar has a gap")
        reconciliations = [row["payload"].get("reconciliation", {}) for row in rows if row.get("kind") == "daily_reconciliation"]
        required_hashes = {"orders_hash", "fills_hash", "positions_hash", "balances_hash", "funding_hash", "ledger_residual_hash"}
        if any(not required_hashes.issubset(item) or item.get("status") != "MATCHED" or item.get("ledger_residual_hash") != "0" for item in reconciliations):
            raise PaperEvidenceError("daily reconciliation is incomplete or has residual")
        unresolved = [item for row in rows if row.get("kind") == "incident_attestation" for item in row["payload"].get("incidents", []) if str(item.get("severity", "")).upper() in {"P0", "P1"} and str(item.get("status", "OPEN")).upper() != "CLOSED"]
        if unresolved:
            raise PaperEvidenceError("unresolved P0/P1 incident")
        report = {"report_id": f"paper_ready_{secrets.token_hex(8)}", "lineage": lineage, "calendar_days": [day.isoformat() for day in parsed_days], "decision_count": len(decisions), "passed": True, "created_at": datetime.now(UTC).isoformat()}
        report["report_hash"] = _hash(report)
        return report
