---
name: bondradar-brief-mode
description: Produce a compact BondRadar review verdict with exact evidence, blockers, safety state, and next safe step. Use when a concise audit summary is requested; do not omit material failures or perform edits.
---

# BondRadar Brief Mode

## Purpose

Compress a completed BondRadar review without losing contract or safety
evidence.

## Format

Return:

```txt
Verdict: close / patch / blocked
Status:
Key evidence:
Contract mismatches:
Safety findings:
Ready next task:
Blocked actions:
Verification:
```

Use exact field names, values, counts, paths, and test outcomes. Put critical
or blocking findings first. State clearly when no findings were found and name
remaining verification gaps.

Do not replace missing evidence with assumptions. Do not edit files, loosen
tests, authorize side effects, or hide warning-worthy methodology issues.
