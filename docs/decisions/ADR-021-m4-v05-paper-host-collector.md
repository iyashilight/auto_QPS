# ADR-021: M4 v0.5 Paper Host Collector 编排边界

## Status

Accepted design gate (2026-08-12). This is a supplemental design for M4 v4; it does not supersede ADR-019 or ADR-020.

## Context

M4 v4 defines the authoritative real Paper verification command and its 14-day/200-decision threshold, but the repository only provides library-level evidence signing/importing. A host-only operational boundary is required to collect durable runtime facts, daily reconciliation, incidents, and restart continuity without granting trading authority.

## Decision

Create the independent Critical task `M4-v05-paper-host-collector` with matrix `M4-v05-paper-host-collector-v1`. The frozen call graph is:

```text
durable Paper runtime facts
  -> PaperEvidenceHostCollector (host-only signer and orchestration)
  -> append-only JournalDB evidence records
  -> paper-stability-evidence-runner verify
  -> M4 v4 real Paper time gate
```

The collector may consume only persisted decisions and existing ports/journal adapters. It must use the host environment key `QPS_PAPER_EVIDENCE_SIGNING_KEY`, expose only its fingerprint, and never call Broker, Live APIs, strategy activation, or risk-increasing controls. All timestamps are UTC; records are append-only and bound to one deployment and one lineage.

The host entrypoint must provide JSON `status`, `start`, `collect`, and `daily` operations. Mutating operations require explicit `--confirm`. Missing durable facts, stale data, invalid signatures, mixed lineage, clock/chain discontinuity, storage failure, reconciliation residual, or unresolved P0/P1 incidents fail closed. Restarts append a signed, idempotent `restart_bridge` before collection resumes. Daily reconciliation hashes orders, fills, positions, balances, funding, and ledger residuals; an explicit signed attestation is required even when the window is empty.

This task supplies collection controls only. Passing it cannot satisfy the M4 v4 real-time proof until the same deployment and lineage produce the required real 14 consecutive UTC days and 200 persisted decisions in an isolated Paper host and dedicated JournalDB.

## Consequences

The implementation and its tests are isolated in a supplemental matrix. M4 v4 remains `IMPLEMENTING` while host, JournalDB, deployment ID, signer fingerprint, and the real observation window are unavailable. The code developer must produce evidence for every matrix row before requesting preflight.
