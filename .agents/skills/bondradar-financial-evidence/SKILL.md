---
name: bondradar-financial-evidence
description: Review BondRadar financial metric evidence for primary-source provenance, document hashes, periods, pages, labels, units, scales, and manual review. Use before controlled import; do not import values or mutate the DB.
---

# BondRadar Financial Evidence

## Purpose

Determine whether a proposed financial value has a complete, reproducible
primary-evidence chain.

## Required evidence

Require:

```txt
issuer identity
reporting standard
period and comparative period
primary source type and URL
document identifier and SHA-256
statement and page
table or section
raw line and metric label
raw values
normalized values
currency, unit, and scale
metric key and role
natural key and row checksum
extraction status
manual review status
```

Reject or keep blocked any value with missing, ambiguous, mismatched, or
unreviewed provenance. An intelligence link is only a source candidate until
the primary document and evidence are accepted.

Check aggregate/component roles, period alignment, statement completeness,
duplicate natural keys, checksum stability, and insert-only versus update
semantics.

## Safety

Do not download documents unless an explicit source task allows it. Do not
write controlled values, import rows, mutate the DB, score issuers, recommend,
rank, or trade.

Return accepted, needs-review, rejected, and missing-evidence rows with exact
reasons.
