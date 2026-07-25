---
name: bondradar-safety-gate-auditor
description: Audit BondRadar tasks for forbidden DB, import, network, scoring, recommendation, ranking, broker, export, and trading behavior or readiness. Use for safety gates; do not execute or authorize operations.
---

# BondRadar Safety Gate Auditor

## Purpose

Find unsafe behavior, dangerous true flags, and premature handoffs in code,
reports, commands, and artifacts.

## Audit

Inspect explicit flags and hidden behavior for:

```txt
database mutation
migration execution
import, upsert, insert, update, or delete
source download, scrape, cache, or live verification
source-backed value claims
scoring, ranking, or recommendation generation
broker API calls
trading or paper trading
production export
methodology patch execution
approval-token bypass
arbitrary shell execution
```

For read-only and planning tasks, every related execution flag must be false.
For apply tasks, only the exact approved operation may become true after
successful post-checks.

Require:

```txt
blockers prevent side effects
bad safety signals are counted
only one safe next task is unlocked
current-task readiness remains false after execution
later tasks remain blocked
failure and rollback states do not claim mutation
```

## Verdict

Return:

```txt
Safety verdict: safe / blocked
Dangerous fields or behavior:
Expected safe values:
Observed values:
Premature readiness:
Required blockers:
Next safe step:
```

Do not edit files or perform the reviewed operation.
