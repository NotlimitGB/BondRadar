# Task302B — Frozen Execution-Terms Evidence Bridge v1

## 1. Purpose and observed context

Operator-supplied read-only coverage observations reported 132 target Bonds,
38 CORE M3 Bonds with dirty-value evidence, and no verified lot/board terms.
These are dated context, never runtime invariants, fixture expectations or
authorization. Task302 correctly failed closed and remains unchanged.

## 2. Boundary

This development package supplies an offline PLAN/APPLY foundation. It neither
loads files nor discovers sources. No VDS, production, token, MOEX/T-Invest
request, deployment, Shadow execution or Task303 work occurs here. Tests use
disposable SQLite. A future operator must separately authorize deployment,
read-only production PLAN and subsequently APPLY.

## 3. Public API and session ownership

ExecutionTermsEvidenceBridgeService(session_factory) exposes keyword-only plan
and apply methods. plan receives frozen_admission, preflight, target_bond_ids and
optional board_observed_at. apply receives the same frozen sources, reviewed_plan
and an explicit authorization. The factory must return a fresh Session without
an active transaction or pending state. A rejected nonfresh Session is not closed,
committed or rolled back. Owned Sessions are always closed. No caller Session
is accepted for transaction completion.

## 4. Frozen source validation

Task296C2 validation/replay checks the complete artifact, source sets and hashes;
acquisition_complete and ready_for_production_freeze must both be true.
Task296C1 frozen-preflight validation is reused with the replayed Task296B manifest.
The contract is tinvest-bond-import-preflight-v2; the global board scan must be
COMPLETE and its provenance must require observation on TQCB. Invalid inputs
return type-stable BLOCKED contracts before Session acquisition.

## 5. Exact identity

Targets are a nonempty deterministic sequence of unique positive exact integers,
canonicalized by Bond ID. Every current Bond requires one exact preflight candidate
with the same ISIN and SECID. Source UIDs come only from that frozen candidate.
No normalization, ticker/name/issuer/FIGI matching, off-board substitution or
legacy Bond.lot_size fallback is permitted. Missing Bonds and profiles block.

## 6. Lot resolution

Lot authority is TInvestBondUniverseInstrument.lot inside frozen source_bonds.
All candidate-bound exact TQCB UIDs are inspected; buy availability remains
separate and cannot suppress another UID's conflicting lot. Supporting positive
exact-int values must agree. Null yields SOURCE_LOT_NOT_SUPPLIED diagnostics and
does not block another valid value. Zero, negative, invalid type or values that
cannot fit PostgreSQL Integer block. No automatic string conversion or lot=1
default. Off-board UIDs remain diagnostic evidence but supply no assertion.
Source keys must also fit the existing bounded-text API without trimming or
truncation; nonrepresentable exact keys block rather than changing their identity.

## 7. Board resolution

Board authority is actual independent MOEX TQCB observations, not import policy
or description legacy markers. Require primary_board=TQCB, board_observed=true,
COMPLETE scan with zero warnings and at least one exact ISIN+SECID TQCB observation.
Partial identity contradictions block; unmatched observations cannot be used
as substitutes. Board evidence stores exact matching observations and counts.

## 8. Observation timestamps

Lot assertions use FrozenAdmissionEvidence.captured_at. Board time is supplied
explicitly as an aware datetime and canonicalized to UTC; its absence leaves PLAN
diagnostic and blocks APPLY. Assertion times are included in plan authorization.
No wall clock is used as source observation time. Existing ingestion_at and
resolver bookkeeping timestamps retain their unchanged Security Master behavior.
Reused fingerprint rows preserve their original recorded observation timestamps.

## 9. Source extension and migration

tinvest_universe is added alongside the three existing MOEX sources in the ORM
source set and CHECK constraint. The unchanged Security Master service imports
that set. Migration 202610030001 follows 202609160001 and only replaces the source
constraint, without a new table, column or data rewrite. Downgrade checks for
remaining T-Invest evidence before any DDL and refuses rather than deleting it.
Constraint names are discovered safely, including PostgreSQL name truncation.

## 10. PLAN and current state

PLAN uses SELECT-only projections under no_autoflush: Bond identity, profile
scalars and relevant lot/board evidence. It evaluates current latest-per-source
evidence with the existing resolver's temporal rule. Existing different values
or conflict states are blockers, never auto-repaired. Profile creation is excluded.
Unrelated profile fields are hashed as drift evidence and checked after resolution.
The resolver may update last_resolved_at and updated_at bookkeeping.

## 11. Row and batch statuses

READY_TO_APPLY requires exact sources and an existing compatible profile.
ALREADY_SATISFIED additionally requires both verified values and every expected
supporting fingerprint/compact lineage; values alone are insufficient.
BLOCKED carries every applicable sorted unique blocker. Any blocked row makes
the complete batch nonexecutable. Missing timestamps also prevent execution.

## 12. Canonical hashes and authorization

Sorted-key ASCII compact JSON with NaN/Infinity forbidden produces SHA-256 hashes
for the target ID set, frozen admission artifact, full preflight, current DB state
and complete plan content excluding its own SHA. DB dates/Decimals have canonical
representations; no connection credentials or Python repr are hashed.
Authorization requires explicit_apply=true and exact plan/target/source/state
hashes. Supplied source timestamps remain bound by the plan even though the
Task296C2 artifact content hash intentionally excludes captured_at.

## 13. Transaction and locking

APPLY validates authorization before acquiring its fresh Session. PostgreSQL uses
one transaction and SHARE ROW EXCLUSIVE NOWAIT locks, ordered Bonds, evidence,
profiles. Ordinary reads remain possible. SQLite isolated tests use BEGIN IMMEDIATE.
Other dialects and unavailable locks block. After locks, source validation and the
entire deterministic plan/state check are repeated before assertions are recorded.
There are no per-row commits or partial-batch continuation.

## 14. Assertion persistence and lineage

Every supporting exact UID produces a scalar lot_size assertion with source
tinvest_universe, source_key=UID and source_table=Task295 contract version.
Four raw fields preserve ISIN, class code, lot and availability classification,
within the existing narrow raw proof limit. Full input flags remain in frozen
source evidence and plan rows. Board uses moex_universe, source_key=SECID,
source_table=TQCB, scalar trading_board=TQCB and four compact identity fields.
Only record_assertion writes evidence; only resolve_profile writes profile terms.
Existing fingerprints and resolver semantics are not modified.

## 15. Verification and outcomes

Before one commit, audit verifies all identities, unchanged profile IDs/version,
verified exact lot/board and effective supporting lineage. Unrelated profile
values must stay equal. Any failure rolls back the whole batch. After commit,
a fresh read-only Session repeats audit and post-state hash verification.
Receipts distinguish attempted/created/reused/committed evidence and profiles.
Confirmed rollback reports zero committed evidence; uncertain commit reports
COMMIT_OUTCOME_UNKNOWN with nullable committed counts. A failed postcommit read
reports POST_COMMIT_AUDIT_FAILED and never claims rollback.

## 16. Idempotency

An old authorization is refused after the first APPLY changes DB state. Operators
must review a new PLAN and authorize its current hash. Exact complete lineage
then yields IDEMPOTENT_NOOP: no insertion, resolver call or commit, and identical
pre/post-state hashes. Matching values lacking lineage still require an APPLY.
Later conflicting evidence blocks even if an older matching assertion exists.
Existing fingerprints whose stored source keys or compact proof contradict the
planned lineage produce CURRENT_LINEAGE_CONFLICT; proof is never overwritten.

## 17. Contracts and capabilities

All new schemas are strict frozen extra-forbid models with PIT false. Versions
identify lot evidence, board evidence, bridge plan, authorization and receipt.
Bridge, exact identity, frozen lot/board evidence, PLAN, drift, explicit authorization,
atomic APPLY, idempotency and post-audit capabilities describe implementation
readiness. Live-source requirement, broker surface, Shadow Ledger, Shadow started
and PIT remain false. A capability is never an operator permission.

## 18. Hermetic verification

Focused tests cover source/UID matrices, exact identity, malformed frozen inputs,
board gates, SELECT-only deterministic PLAN, authorization/hash tampering, drift,
shared transaction rollback, lineage, locks, uncertain outcomes, strict serialization
and unchanged Task302 terms loading. Migration tests exercise upgrade, four sources,
unknown rejection and safe downgrade on isolated SQLite. Regress Security Master,
Task295, Task296 identity, preflight and Task302 loader/planner, then compile and
working/staged diff/scope review. Broad suite remains exact-commit CI responsibility.

## 19. Delivery and controlled handoff

Seven allowed files, one commit Add frozen execution terms evidence bridge, one
normal push, one exact-commit CI snapshot without polling. Temporary directories
are preserved. After independent review and green CI, the named next task is
TASK302B_PRODUCTION_PLAN; deployment/migration and production read-only execution
need separate authorization. A zero-blocker reviewed production PLAN may later
support separately authorized atomic APPLY and another execution-terms coverage
audit. Task303 and Shadow tests never start automatically.
