# ADR-020: M4 v0.5 Real Paper Time Gate v4

## Status

Accepted design gate (2026-08-12). This supersedes the M4 v3 time-gate
evidence design only; ADR-019 remains authoritative for the Paper evidence
record format and fixed thresholds.

## Background

`M4-PAPER-TIME-GATE` in matrix v3 invoked
`external-fixture-runner m4-paper-stability-window`. That fixture is
intentionally hard-coded to return exit 77 and does not read a JournalDB.
Consequently a genuine Paper window could never satisfy the v3 integration
proof. This is a design defect, not an unavailable environment.

## Decision

M4 v4 replaces the v3 time-gate row with `M4V4-REAL-PAPER-TIME-GATE`.
The only passing integration command is:

```sh
paper-stability-evidence-runner verify \
  --journal <absolute-journal-path> \
  --deployment <deployment-id> \
  --public-key-fingerprint <signer-fingerprint>
```

The command verifies the actual append-only Paper JournalDB through
`PaperEvidenceImporter` and `PaperReadinessEvaluator`. It applies the fixed
ADR-019 conditions: one frozen lineage, 14 consecutive UTC days, 200 distinct
persisted `input_snapshot_ref` values, signed daily reconciliation and incident
attestation, and no unresolved P0/P1 incident.

Exit status is fixed: 0 only for a passing real window; 77 only for valid but
incomplete real evidence; all other nonzero values mean invalid evidence and
must fail closed. `external-fixture-runner m4-paper-stability-window` remains
an environment-blocked fixture and cannot be an M4 integration proof.

The v4 dependency graph is:

```text
M4-CHAOS-INVARIANTS
  -> M4V3-PAPER-OBSERVATION-LINEAGE
  -> M4V3-CLOCK-CHAIN-AND-IMPORT
  -> M4V3-DAILY-RECONCILIATION-INCIDENTS
  -> M4V4-REAL-PAPER-TIME-GATE
  -> M4V3-PAPER-READINESS-FAIL-CLOSED
```

## Evidence and Migration

Matrix v3, its task definition, preflight report and evidence file remain
historical. Matrix v4 starts with an empty evidence file. Every v4 proof,
including unchanged local controls, must be re-recorded through
`review_preflight.py run`; no v3 passed record is migrated.

Required negative coverage is retained in
`test_m4_paper_readiness_gate.py` and must reject caller-controlled thresholds,
fixtures, replay/duplicate decisions, mixed lineage, missing daily evidence,
tampered signatures and incomplete windows. These tests cannot substitute for
the real integration proof.

## Non-goals

This ADR does not permit a real exchange API, funds, M5 Live behavior,
multi-strategy allocation, automatic approval, capital increases or unfreeze.
