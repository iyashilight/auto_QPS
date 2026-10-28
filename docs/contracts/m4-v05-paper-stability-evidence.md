# M4 v0.5 Paper Stability Evidence Contract

ADR-019 defines the only acceptable real-calendar Paper stability evidence.

## Fixed Thresholds

- `min_calendar_days = 14` consecutive UTC dates.
- `min_independent_decisions = 200` distinct persisted input snapshot refs.
- One frozen tuple is required throughout: `deployment_id`, `release_id`,
  `policy_id`, `account_id`, `journal_id`, and evidence public-key fingerprint.
- Every UTC day has one valid reconciliation record and one incident attestation.
- No P0/P1 may be unresolved at the end of the window.

## Canonical Records

`PaperObservation` is append-only and has `observation_id`, the frozen tuple,
`recorded_at` UTC, `monotonic_ns`, `boot_id`, `previous_hash`, canonical payload
hash, Ed25519 signature and signer fingerprint. Decision records additionally
carry `input_snapshot_ref`, canonical intent/event hash and outcome reference.

`PaperDailyReconciliation` contains the UTC date, source order/fill/position/
balance/funding hashes, ledger residual hash, result and signature. A nonzero
or unclassified residual is not a passing day.

`PaperIncidentAttestation` contains the UTC date, all observed operational
incidents, severity, resolution reference, open/closed status and operator
signature. An absent declaration is invalid, not an implicit clean day.

`PaperReadinessReport` contains the frozen tuple, start/end attestations,
calendar dates, decision refs/count, daily hashes, incident hashes, report hash
and pass/fail reason. It is derived only by a verifier, never supplied by a
strategy or caller.

## Enforcement

The host collector writes records to a dedicated append-only JournalDB table.
`PaperEvidenceImporter` verifies signatures, chain continuity, UTC/monotonic
clock continuity, restart bridge, source lineage and daily completeness before
persisting imported records. `PaperReadinessEvaluator` reads only verified
records and fixed thresholds. Invalid, tampered, incomplete, mixed-lineage or
clock-regressed input must fail closed without creating a passing report.

The command `paper-stability-evidence-runner verify --journal <path>
--deployment <id> --public-key-fingerprint <hash>` returns 0 only for a passing
real window, 77 only for a valid-but-incomplete window, and nonzero failure for
invalid evidence. It must emit canonical JSON suitable for `review_preflight`.
