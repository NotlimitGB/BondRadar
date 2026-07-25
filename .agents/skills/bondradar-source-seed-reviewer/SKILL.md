---
name: bondradar-source-seed-reviewer
description: Review BondRadar issuer and source candidate seed structure, identifiers, source types, URLs, placeholders, and provenance. Use for offline seed review; do not fetch URLs or accept candidates as source-backed evidence.
---

# BondRadar Source Seed Reviewer

## Purpose

Review whether a source candidate seed is structurally safe for a later,
explicit discovery or evidence task.

## Review

Check:

```txt
candidate and issuer identifiers
corporate issuer scope
OFZ exclusion
source type
URL and locator field shape
placeholder versus concrete values
official-source claim
document and period metadata
duplicate candidates
review and readiness status
provenance state
forbidden execution flags
```

Do not access the network, fetch URLs, scrape pages, download documents, or
claim live availability during current seed-review tasks.

## Intelligence sources

Social posts, Pulse posts, forums, blogs, broker commentary, and news may be
accepted only as intelligence candidates.

They may produce:

- source locator candidate
- official URL candidate
- event candidate
- risk hypothesis
- sentiment claim
- verification task

They must not be treated as:

- official evidence
- controlled financial values
- verified financial metrics
- accepted rating evidence
- controlled import rows

If an intelligence item contains a primary-source URL, extract it only as a
candidate for later review. The intelligence item itself remains non-primary.

## Verdict

Return structural findings, provenance status, allowed downstream planning,
blocked actions, and the next safe review step. Do not edit the seed.
