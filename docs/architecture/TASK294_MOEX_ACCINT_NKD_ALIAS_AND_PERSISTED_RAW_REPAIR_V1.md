# Task294 — MOEX ACCINT/NKD Alias & Persisted Raw Evidence Repair Foundation v1

## Purpose

MOEX `ACCINT` is direct accrued-interest/NKD evidence. Task294 connects that
source field to the existing canonical snapshot `nkd` value for future
ingestion, and provides an offline preview/apply service for explicitly chosen
historical snapshots whose own raw payload already contains the evidence.
It does not calculate accrued interest or fetch a replacement observation.

## Future ingestion

Market-history normalization recognizes `ACCINT` and `ACCRUEDINT` regardless
of source-field casing and retains the original row. Both the direct MOEX
mapper and normalized history mapper use the same strict NKD parser. It accepts
finite nonnegative decimal values, including zero; booleans, negative values,
nonfinite numbers, and malformed values are ignored with a warning. Blank
aliases are ignored.

When both aliases are populated, both must parse and their canonical Decimal
values must be equal. Equal representations such as `31.560` and `31.56` are
accepted. An invalid populated alias or disagreement leaves canonical NKD
null and exposes the source field names and canonical Decimal values in
diagnostics; neither alias has priority over the other.

## Persisted snapshot repair

`MoexNkdRepairService.preview(refs)` and `apply(refs)` accept only a
nonempty, deterministic sequence of unique positive snapshot IDs or persisted
snapshot instances. The service queries only those IDs, joins their attached
Bond, and sorts results by snapshot ID. It never discovers a universe or
contacts MOEX.

A row is repairable only if it is a MOEX snapshot with null NKD, mapping-shaped
raw and MOEX payloads, one valid nonnegative source NKD value, a raw SECID that
matches its Bond SECID after trim/case normalization, a valid raw TRADEDATE,
and an exact trade-date match. Existing NKD is reported as
`ALREADY_POPULATED` and is never reconciled or overwritten. Failed gates are
returned as sorted flags with a deterministic primary status.

Preview is SELECT-only under `no_autoflush`. Apply re-evaluates the selected
rows and changes only `BondMarketSnapshot.nkd` for safe candidates in the
caller-owned session. It does not flush or commit; the caller controls
transaction completion and rollback. Results distinguish `SAFE_TO_REPAIR`
from `REPAIRED` in the current session. Repeated apply sees the populated
value and does not change it again.

## Boundaries and declarations

Raw payload, prices, yields, duration, volume, liquidity, spreads, trade date,
source, bond identity, timestamps, and every other snapshot field remain
unchanged. The repair uses evidence from the exact stored snapshot only; it
does not infer identity from ISIN, select another date, or claim global PIT
readiness. Task267 selection and Task272/273 formulas and readiness gates are
unchanged. No API, schema, migration, production repair, or live-source
operation is introduced.

```text
ACCINT_ALIAS_READY=true
LEGACY_ACCRUEDINT_PRESERVED=true
DIRECT_MAPPER_ACCINT_READY=true
HISTORY_MAPPER_ACCINT_READY=true
DUAL_ALIAS_AGREEMENT_READY=true
DUAL_ALIAS_CONFLICT_FAIL_CLOSED=true
ZERO_NKD_VALID=true
NEGATIVE_NKD_REJECTED=true
NONFINITE_NKD_REJECTED=true
MALFORMED_NKD_REJECTED=true
PERSISTED_RAW_REPAIR_PREVIEW_READY=true
PERSISTED_RAW_REPAIR_APPLY_READY=true
RAW_SECID_MATCH_REQUIRED=true
RAW_TRADE_DATE_MATCH_REQUIRED=true
MOEX_SOURCE_REQUIRED=true
EXISTING_NKD_OVERWRITE=false
RAW_PAYLOAD_MUTATED=false
UNRELATED_SNAPSHOT_FIELDS_MUTATED=false
REPAIR_IDEMPOTENT=true
REPAIR_NETWORK_ACCESS=false
CALLER_TRANSACTION_CONTROL_PRESERVED=true
TASK267_SELECTION_CHANGED=false
TASK272_CHANGED=false
TASK273_CHANGED=false
DV01_RAW_PAYLOAD_FALLBACK_ADDED=false
NKD_CALCULATION_ADDED=false
LIVE_MOEX_REQUIRED_FOR_REPAIR=false
DATABASE_MIGRATION_ADDED=false
PRODUCTION_ACCESSED=false
PRODUCTION_BACKFILL=false
SHADOW_TEST_STARTED=false
PIT_READY=false
```
