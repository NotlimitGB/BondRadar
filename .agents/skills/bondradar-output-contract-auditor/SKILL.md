---
name: bondradar-output-contract-auditor
description: Audit BondRadar TaskXXX JSON, Markdown, and wrapper output contracts for required fields, stable types, counts, checksums, and readiness. Use for contract review; do not edit artifacts or loosen expected fields.
---

# BondRadar Output Contract Auditor

## Purpose

Determine whether a TaskXXX report is deterministic and type-stable across
warning, passed, blocked, and failed/default paths.

## Inspect

Check:

```txt
main JSON required fields
wrapper artifact paths and payloads
required bool, int, string, and list fields
row field contracts
aliases required by VDS inspection
source and generated row counts
count-to-list consistency
checksums and carried lineage checksums
status and sub-status values
blocker and bad-safety counts
single next-task readiness
Markdown sections and safety text
```

Reject `None` where a contract requires `str`, `int`, `bool`, or `list`.
Allow `None` only for explicitly documented placeholders or unavailable numeric
values.

Compare wrappers with the normalized main report. Check blocked and missing
input paths, not only the happy path.

## Verdict

Return:

```txt
Contract verdict: close / patch
Exact mismatches:
Affected artifacts:
Expected field/type/value:
Observed field/type/value:
Readiness findings:
Checksum findings:
Required patch:
```

Do not edit files, rewrite evidence, or weaken tests.
