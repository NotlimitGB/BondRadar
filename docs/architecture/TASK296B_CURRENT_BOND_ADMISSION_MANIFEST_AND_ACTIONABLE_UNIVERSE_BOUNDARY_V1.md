# Task296B — Current Bond Admission Manifest & Actionable Universe Boundary v1

## 1. Purpose

Task296B combines frozen Task295 T-Invest BASE bond facts, the Task296 exact-ISIN bridge, caller-supplied exact MOEX resolutions, internal Bond identity projections, and CORE M3 IDs into a deterministic admission and coverage manifest. It classifies evidence; it does not import instruments.

## 2. Dated evidence context

Task296 reported 1,613 current T-Invest BASE bonds, 274 unmatched UIDs across 267 unique ISINs, 251 MOEX exact-ISIN recoveries, 16 not-found ISINs, and 49 canonical OFZ ISINs among recovered records. These are dated observations only. Runtime code contains no such expected counts.

## 3. Architecture boundary

The pure reducer accepts already-built evidence. It does not call Task295, Task296, MOEX clients, database services, or M3 services. All supplied lists are copied before calculation and source contracts remain unchanged.

## 4. Inputs

The keyword-only `TInvestBondAdmissionManifestService.build(...)` accepts deterministic sequences of Task295 bond rows, internal `BondIdentityProjection` rows, exact MOEX resolution projections, and CORE M3 Bond IDs, plus one Task296 bridge object.

## 5. MOEX projection

`MoexBondResolutionProjection` is frozen and rejects extra fields. It preserves source ISIN, match status, matched SECID/ISIN, candidate counts, primary board, issuer metadata status, and optional issuer identifiers without using issuer fields to classify or match a security.

## 6. Accepted MOEX statuses

Only `EXACT_ISIN_RECOVERED`, `SECURITY_NOT_FOUND`, `SECURITY_AMBIGUOUS`, `SECURITY_IDENTIFIER_CONFLICT`, and `SOURCE_ERROR` are accepted. Every unique nonblank unmatched ISIN must have exactly one projection. Missing and duplicate projections raise typed errors; no partial manifest is returned.

## 7. Exact identity

Task296 remains authoritative for existing Bond matches. New-candidate identity requires exact `source_isin == matched_isin`. No ticker, name, issuer, FIGI, normalized-only ISIN, or fuzzy fallback is used.

## 8. Internal normalized-only evidence

MOEX exact recovery cannot upgrade Task296 `UNRESOLVED_NORMALIZED_ONLY_ISIN_CANDIDATE` to `EXISTING_MATCHED`. One normalized-only Bond candidate makes the source ISIN review-only; multiple normalized-only candidates are an identity conflict. These rows are never silently added to current Bond coverage.

## 9. Admission states

UID and unmatched ISIN outputs use `EXISTING_MATCHED`, `IMPORT_CANDIDATE_CURRENT_PIPELINE`, `REVIEW_REQUIRED`, `MOEX_NOT_RESOLVED`, or `IDENTITY_CONFLICT`. Detailed reasons remain a sorted, unique set of reason codes.

## 10. Canonical OFZ

After exact MOEX identity only, OFZ classification calls the shared `is_ofz_instrument(isin=matched_isin, secid=matched_secid)` helper. A canonical OFZ is review-only. Task296B does not widen the identity helper or the eight-node curve.

## 11. Government and municipal evidence

Only exact source `sector == "government"` or `sector == "municipal"` creates those review reasons. Names, issuer titles, tickers, country, and other labels do not infer government identity. Canonical OFZ remains a distinct fact; its reason suppresses redundant government-sector wording.

## 12. Non-government wording

`no_government_evidence` is true only when canonical OFZ is false and valid nonblank sector evidence is present without government, municipal, malformed, or classification-conflict evidence. It does not mean legally proven corporate security.

## 13. Blank metadata

For optional metadata, `None` and strings containing only whitespace become `NOT_SUPPLIED`. Nonblank source strings are preserved exactly and compared without trimming or case folding. Malformed supplied types become `INVALID_SOURCE_VALUE` and are never promoted into source values.

## 14. New-candidate current pipeline envelope

An unmatched exact-MOEX candidate requires non-OFZ identity, supplied sector other than government or municipal, `API_BUY_AVAILABLE`, `for_qual_investor == false`, exact `currency == "rub"`, supplied `bondType` other than `BOND_TYPE_REPLACED`, a supplied SECID, and exact MOEX `primary_board == "TQCB"`. Missing country is diagnostic and nonblocking.

## 15. Existing Bond pipeline evidence

Existing Bond rows remain `EXISTING_MATCHED` and are not re-imported. Current-pipeline TQCB compatibility for existing rows uses exact Task295 `class_code == "TQCB"`; the UID output names this source separately from MOEX `primary_board`. Bond type is not required for compatibility and missing evidence is counted separately.

## 16. Replaced bonds and qualification

`BOND_TYPE_REPLACED` and a true qualification restriction produce review reasons, not rejection. Unknown qualification also requires review. `required_tests` and its supplied/empty state are preserved as factual source data and do not gate import eligibility.

## 17. Multiple UIDs per ISIN

All UIDs remain visible. An unmatched ISIN aggregate may be an import candidate when at least one UID independently meets the gates and critical classification fields have no conflicting nonblank values. Exact disagreement in sector, currency, or bond type forces `REVIEW_REQUIRED` with `SOURCE_CLASSIFICATION_CONFLICT`; per-UID availability and qualification remain separate.

## 18. Existing actionable non-government KPI

The Bond-level denominator includes only exact Task296 matches where a current UID is API-buyable, canonical OFZ is false, a buyable UID has valid non-government sector evidence, and the Bond has no government/municipal evidence. Each Bond ID is counted once.

## 19. CORE M3 coverage

CORE IDs must be unique exact positive integers contained in the explicit internal projection. Coverage percentages are computed from the intersection with the current Bond denominator using `Decimal(count) * Decimal("100") / Decimal(denominator)` in a fresh precision-28 `ROUND_HALF_EVEN` context. A zero denominator returns `Decimal("0")`.

## 20. Current pipeline-compatible KPI

The stricter existing-Bond count additionally requires the same UID to be API-buyable, have exact RUB currency and Task295 `class_code == "TQCB"`, with non-government evidence. The CORE numerator is its unique-Bond intersection. Missing bond-type evidence is reported separately and does not remove an otherwise compatible existing Bond.

## 21. Import gap boundary

Unmatched import candidates are reported as a separate unique-ISIN and UID gap. They are excluded from all existing-universe coverage denominators. `post_import_addressable_universe_size` is informational, not a coverage claim.

## 22. Metadata and reason audits

The output includes sorted per-field source metadata breakdowns with `SOURCE_VALUE`, `NOT_SUPPLIED`, and `INVALID_SOURCE_VALUE` states, plus sorted reason counts and associated UID/ISIN lists. Optional metadata is not interpreted as legal venue or corporate classification.

## 23. Deterministic hashes

Five SHA-256 values cover sorted canonical JSON for UID admission rows, unmatched ISIN aggregates, import candidate ISINs, review-required ISINs, and MOEX-not-resolved ISINs. Hash payloads contain no timestamps and use no Python `repr()`.

## 24. Read-only and no-side-effect guarantee

The service imports only Python standard-library utilities, existing schemas, and the shared OFZ identity helper. It performs no SQL, network, environment, filesystem, persistence, broker-write, import, flush, or commit operation.

## 25. Capability declarations

The manifest declares current admission, exact MOEX identity, the five admission states, government/municipal review, blank hygiene, RUB/TQCB boundaries, actionable and current-pipeline coverage, and deterministic hashes ready. OFZ contract change, legal corporate inference, name inference, replacement auto-admission, personal qualification resolution, persistence, migration, network, live requests, M3 changes, broker writes, Shadow, and PIT remain false.

## 26. Verification

Focused Task296B synthetic tests run first, followed by Task296 identity-bridge and Task295 universe regressions, compile checks, and `git diff --check`. Broad regression is delegated to exact-commit CI. No production or live-source test is part of this contract.

## 27. Handoff

The result supports an independent Task296B review and exact-commit CI. Any later production read-only probe and any import batch require their own explicit authorization and safety gates.
