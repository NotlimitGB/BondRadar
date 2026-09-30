# Task296C1 — MOEX Multi-Source Import Preflight Evidence v1

## 1. Purpose

Task296C1 corrects the provenance used by the read-only Task296C import preflight. It separates MOEX security-reference issuer identity, the TQCB board universe, and security-description facts before the unchanged admission rules classify a candidate.

## 2. Scope

The change adds strict evidence DTOs, refactors the pure Task296C classifier to consume a typed evidence batch, adds a read-only acquisition adapter, updates the focused tests, and records this contract. It does not change Task296B candidates, MOEX shared client semantics, normal bond sync, persistence, or any import path.

## 3. Original defect

The earlier adapter treated the description response requested with `board="TQCB"` as if it proved issuer identity and actual TQCB membership. Those facts came from independent MOEX surfaces. A description request parameter is not proof that an instrument appeared in the board universe, and description issuer fields are not security-reference evidence.

## 4. Evidence flow

```text
Task296B candidate manifest
    ├── MOEX Security Reference → issuer identity and primary board
    ├── one shared paginated TQCB universe scan → actual board observations
    └── MOEX security description → bond facts
                         ↓
              typed evidence batch
                         ↓
            pure Task296C classifier
```

The evidence adapter validates the frozen admission manifest before source calls. The classifier itself has no client, network, database, or persistence dependency.

## 5. Contract versions

The outer preflight contract is `tinvest-bond-import-preflight-v2`, reflecting the source-separated evidence and provenance. The evidence batch is `moex-bond-import-evidence-batch-v1`. Candidate, issuer, and board DTO versions are independently declared. The Task296B admission manifest and Task295/Task296 source versions remain unchanged.

## 6. Issuer evidence

`MoexIssuerIdentitySourceService.lookup(requested_secid=..., expected_isin=...)` is the only issuer reference path. Accepted security-match statuses are `EXACT_SECID`, `EXACT_SECID_ISIN_CORROBORATED`, and `EXACT_ISIN_RECOVERED`. `issuer_title`, `issuer_inn`, and `primary_board` are read only from that result. A title and an INN are both required. INN follows existing import formatting: trim and require a nonempty value no longer than 16 characters; no additional checksum rule is introduced.

Missing, ambiguous, and source-error outcomes remain review evidence. An explicit security identifier conflict becomes `IDENTITY_CONFLICT`. A missing primary board and a primary board other than exact `TQCB` remain separate review reasons.

## 7. Board-universe evidence

The adapter scans the actual `TQCB` securities universe once per evidence batch, paging in 100-row increments up to the existing client page bound. It retains rows with an exact candidate SECID or exact candidate ISIN so one identifier cannot hide a contradiction in the other; only an exact SECID-and-ISIN pair proves board membership. A scan that reaches the page bound without a short terminal page, reports warnings, or fails is incomplete or unavailable and cannot produce READY.

Board observations preserve SECID, ISIN, `is_traded`, and status from the board rows. `board_observed` is derived from a matching SECID/ISIN row in this board evidence. Missing ISIN is incomplete membership evidence. A different nonblank SECID or ISIN is an identity contradiction.

## 8. Description evidence

The description projection remains authoritative for name, currency, nominal, maturity, offer, coupon, and structural facts. Its legacy `issuer_name`, `issuer_inn`, `primary_board`, and `board_observed` fields remain in the DTO for compatibility and diagnosis, but the classifier never uses them. The new adapter explicitly leaves these fields unset even though the shared client may expose similarly named source fields.

The description call may still carry `board="TQCB"` as request context. That request parameter does not satisfy the separate board-universe gate.

## 9. Exact identity join

The frozen candidate and every nonblank identity assertion from issuer reference, board rows, and description must agree on exact SECID and ISIN. No trimming, fuzzy matching, title lookup, issuer-name matching, ticker fallback, or identity borrowing is added by the classifier. Missing identity fields cause review; contradictory nonblank identities produce `SECURITY_IDENTITY_CONFLICT` and top-level `IDENTITY_CONFLICT`.

## 10. Classification and precedence

The Task296C states remain `READY_FOR_CONTROLLED_IMPORT`, `REVIEW_REQUIRED`, `ALREADY_IMPORTED_OR_COLLISION`, and `IDENTITY_CONFLICT`. Existing precedence is retained: identity conflict, then existing Bond collision, then any review reason, otherwise READY. New evidence gates add diagnostics; they do not relax the existing frozen candidate, currency, positive nominal, maturity-or-perpetual, company-preview, or collision requirements.

## 11. Activity semantics

Explicit `is_traded=false` or a recognized inactive status from either the board universe or description adds `NOT_ACTIVE_OR_NOT_TRADED` and blocks READY. `is_traded=true` is sufficient positive activity evidence. Missing or uninterpretable activity remains `ACTIVE_STATUS_UNKNOWN`, which is diagnostic only. The adapter does not infer an active state from absence.

## 12. Reason codes

Existing Task296C codes continue to represent description availability/identity, nominal/currency, structure, company preview, collision, and activity. Added codes distinguish `ISSUER_REFERENCE_MISSING`, `ISSUER_REFERENCE_SOURCE_ERROR`, `ISSUER_REFERENCE_AMBIGUOUS`, `PRIMARY_BOARD_MISSING`, `PRIMARY_BOARD_MISMATCH`, `BOARD_SCAN_INCOMPLETE`, `BOARD_SOURCE_ERROR`, `DESCRIPTION_SOURCE_ERROR`, and `DESCRIPTION_IDENTITY_INCOMPLETE`. Source exceptions are not copied into reason messages or serialized evidence.

## 13. Provenance

Each candidate row and READY row retains separate issuer evidence, board evidence, and description projection. Top-level provenance records the evidence contract versions, candidate evidence count, issuer lookup/query counts, one board-scan status with page/row/warning counts, description lookup/projection counts, and the original Task296B hashes. No complete raw HTTP payload or exception text is retained.

## 14. Determinism and hashes

Candidates continue to use Task296B's canonical frozen order. The adapter processes candidates in that order, sorts matching board observations deterministically, and emits timestamp-free frozen DTOs. Task296C ready/review hashes continue to use the existing sorted canonical JSON SHA-256 function; only the serialized contract now truthfully includes source evidence. Task296B candidate hashes are neither regenerated nor redefined. No expected future READY count or hash is hard-coded.

## 15. Request discipline

The board universe is fetched as a shared set-level resource, not once per candidate. Each candidate then receives one issuer lookup invocation (which may make an exact-ISIN fallback query under the existing resolver contract) and one description request. No concurrency, retry, title search, live verification by tests, or arbitrary row selection is introduced.

## 16. Failure behavior

Manifest validation happens before acquisition. Board, issuer, and description failures are converted to sanitized source states. Incomplete board scans cannot authorize READY. Missing source evidence remains review; contradictions remain identity conflicts. Unexpected parsing of an unsupported response shape fails closed for that evidence source without exposing raw bodies.

## 17. Read-only and safety boundary

The new adapter only calls the existing MOEX read methods and the existing issuer identity resolver. The pure classifier only reduces supplied DTOs. Neither uses SQLAlchemy, a database session, controlled import, sync, filesystem writes, token creation, scoring, ranking, recommendation, or broker APIs. No production, VDS, database, or live MOEX request was part of this implementation.

## 18. Backward compatibility

The shared `MoexIssClient`, `MoexIssuerIdentitySourceService`, and `MoexBondUniverseService` are unchanged. Other consumers continue to receive their existing normalized fields and behavior. Task296B and its candidate universe are unchanged. The v2 Task296C output is the explicit interface boundary for the corrected evidence composition.

## 19. Verification

Hermetic tests cover the pure classifier and the acquisition adapter, including issuer/board/description separation, exact identity contradictions, missing and failed evidence, incomplete scans, duplicate observations, both-source activity handling, deterministic output, manifest validation before requests, and preservation of collision and Task296B hashes. Existing issuer-resolver and MOEX bond-universe regression tests are part of the focused verification.

## 20. Readiness and handoff

`READY_FOR_CONTROLLED_IMPORT` remains a per-candidate result from the read-only classifier and is not authorization to import. Candidate counts and READY hashes must be learned from a separately authorized read-only production preflight after review. The next production operation requires separate user authorization; Task296C1 performs no deployment, synchronization, preflight execution against production, or controlled import.
