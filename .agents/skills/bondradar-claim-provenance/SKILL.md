---
name: bondradar-claim-provenance
description: Audit provenance for financial, credit-risk, corporate-event, Pulse, or analyst claims before BondRadar treats them as verified or source-backed.
---

# BondRadar Claim Provenance Skill

## Purpose

Determine where a claim came from and what BondRadar may do with it.

## Provenance fields

Require:

```txt
claim_id
claim_text
claim_domain
issuer
instrument
period
raw_source_type
raw_source_url
primary_source_url
primary_source_present
document_id
document_hash
page
table
metric_label
unit
scale
verification_status
review_status
```

## Verification states

```txt
unverified
primary_source_candidate
primary_source_located
evidence_extracted
manually_reviewed
accepted_source_backed
rejected
contradicted
```

## Rules

Pulse, news, commentary, and analyst opinions start as:

```txt
unverified
```

A primary-source link changes the claim only to:

```txt
primary_source_candidate
```

It does not make the claim source-backed.

Only an accepted evidence chain may reach:

```txt
accepted_source_backed
```

## Audit verdict

```txt
Provenance complete / incomplete
Primary source present / absent
Claim verified / unverified / contradicted
Allowed downstream actions
Blocked downstream actions
```
