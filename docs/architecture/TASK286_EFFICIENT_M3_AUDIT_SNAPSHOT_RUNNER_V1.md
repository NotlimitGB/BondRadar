# Task286 — Efficient M3 Audit Snapshot Runner v1

## 1. Decision

Task286 adds a read-only database orchestrator that builds factual Task282 M3
composites and one Task283 coverage audit for an explicitly supplied bond
universe. It does not issue a readiness verdict.

## 2. Explicit audit universe

The caller supplies a deterministic, nonempty sequence of unique positive bond
IDs. IDs are sorted before orchestration. Task286 does not discover or rank an
investment universe and supports only exact market source `moex` in v1.

## 3. Existing/missing partition

Task285 is called once and is authoritative for requested, existing and missing
IDs. Existing IDs receive `BUILT` items and a complete Task282 view. Missing IDs
receive `BOND_NOT_FOUND` with a null composite. No second existence query or
synthetic composite is created.

## 4. Shared OFZ curve

When at least one requested bond exists, Task268 `build_curve` runs once. Every
existing bond is evaluated against the same curve through the pure Task278
evaluator. Task268 `evaluate_bond` is not called and the curve is not rebuilt per
bond.

## 5. Shared Task285 liquidity

The runner calls Task285 once for the complete requested universe. Each Task282
composition uses the feature stored in its Task285 `BUILT` item. Task270 single
builds, repeated liquidity window scans, percentile recalculation and score
recalculation are forbidden.

## 6. Per-bond M3 orchestration

For each existing ID in ascending order, the runner calls Task267, Task278,
Task269, Task275, Task272 and Task273, then the Task284 and Task282 pure
composers. Direct call counts describe Task286 orchestration only; transitive
calls inside dependencies are not included.

## 7. Task284 composition

Task284 receives the Task278 relative-value view and the prebuilt Task285
liquidity view. Its evidence validation, status precedence and side-preservation
semantics remain authoritative. Task274 loader service is not used.

## 8. Task282 composite construction

Task282 receives all eight feature views and `peer_distribution=None`. Its
`CONSISTENT` or `EVIDENCE_INVALID` status is preserved. Task286 does not require
every feature to be READY and does not repair contradictory evidence.

## 9. Task283 coverage reduction

After all existing bonds are composed, Task283 runs exactly once over the
sorted composites. Unexpected dependency or reducer exceptions propagate; v1
does not return a partial snapshot or fabricate fallback evidence.

## 10. Audit denominator

The Task283 denominator is `EXISTING_REQUESTED_BONDS`. It equals the number and
ordered IDs of Task285 existing bonds. Missing requested IDs remain visible at
the snapshot level but are not included in coverage percentages.

## 11. Valid unavailable evidence

Coherent stale, missing or insufficient feature evidence remains a valid
Task282 `CONSISTENT` composite. The corresponding availability fields are false
and Task283 reports the factual coverage gap without causing runner failure.

## 12. Evidence-invalid composites

Schema-valid contradictory evidence may produce Task282 `EVIDENCE_INVALID`.
Such composites remain in the result and common audit denominator. Task283
counts them under invalid composites and the evidence-invalid bottleneck.

## 13. Peer context boundary

Task286 does not build Task281 or peer distributions. Every Task282 call uses
`peer_distribution=None`; provenance records `peer_context_mode=NOT_BUILT`.
Core coverage is meaningful, while peer coverage and extended completeness are
zero for this runner. This is an orchestration boundary, not a statement about
future peer availability.

## 14. Performance guarantees

For any nonempty existing universe, the OFZ curve build, Task285 liquidity
window scan and Task285 universe evaluation each occur once. They do not scale
with bond count. Per-bond feature services remain O(N), and Task286 does not
claim total SQL query complexity is constant.

## 15. Known O(N) dependency duplication

Task286 removes repeated universe-level OFZ/liquidity work. It does not yet
eliminate all per-bond repeated DB reads inside Task275, Task272 and Task273.
Task275 obtains Task269 evidence internally, Task272 obtains Task267 evidence,
and Task273 obtains Task272 plus Task267 evidence. Those paths remain O(N) and
are accepted for the first coverage audit. Future extraction requires separate
profiling and scope.

## 16. Read-only boundary

All orchestration runs under `Session.no_autoflush`. Task286 performs no direct
SQL and calls no add, add_all, delete, flush, commit or merge operation. It does
not mutate source rows or dependency views and performs no network, filesystem,
production or persistence action.

## 17. No M4 readiness threshold

Snapshot statuses `COMPLETE`, `PARTIAL` and `NO_EXISTING_BONDS` describe build
completeness only. Task286 does not translate coverage percentages into M3/M4
readiness, risk, score, ranking, recommendation or investment interpretation.

## 18. PIT boundary

Every snapshot declares `pit_ready=false`. Task286 does not upgrade the PIT
status of Task267–285 evidence.

Capabilities are:

```text
EXPLICIT_AUDIT_UNIVERSE_READY=true
SHARED_OFZ_CURVE_READY=true
SHARED_LIQUIDITY_BATCH_READY=true
TASK282_COMPOSITE_SNAPSHOT_READY=true
TASK283_COVERAGE_AUDIT_READY=true
CORE_M3_AUDIT_READY=true
PEER_CONTEXT_BUILT=false
EXTENDED_M3_AUDIT_INTERPRETATION_READY=false
AUTOMATIC_INVESTMENT_UNIVERSE_DISCOVERY_READY=false
M4_READINESS_DECISION_READY=false
INVESTMENT_SCORE_READY=false
INVESTMENT_RANKING_READY=false
RECOMMENDATION_READY=false
PIT_READY=false
```

## 19. Verification

Focused tests cover validation, all identity partitions, shared-work and
per-bond call counts for 1/2/10 bonds, full manual Task282 and Task283 parity,
unavailable and invalid evidence, exception propagation, peer boundaries,
determinism, Decimal isolation, caller session state and static safety. Relevant
Task285, Task282, Task283 and Task284 regressions plus compile and exact
four-file diff checks form local acceptance. The full backend suite is delegated
to exact-commit CI.

## 20. Handoff

After independent Task286 verification, Task287 may prepare the M3 VDS
deployment preflight under a separate request. Task286 does not access VDS,
deploy, run production coverage, start Task287 or begin the CFA foundation.
