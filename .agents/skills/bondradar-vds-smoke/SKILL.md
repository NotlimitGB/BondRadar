---
name: bondradar-vds-smoke
description: Review or prepare a BondRadar VDS smoke for a TaskXXX mode, including artifacts, counts, checksums, blockers, and safety flags. Use for production-like validation; do not run approved mutation unless the task explicitly requires it.
---

# BondRadar VDS Smoke

## Purpose

Validate a TaskXXX mode against production-like files and configuration while
preserving the task's safety boundary.

## Procedure

1. Confirm the exact mode, input chain, output directory, expected status, and
   expected next-task readiness.
2. Run static or no-token smoke first.
3. Inspect the main JSON, Markdown, every wrapper, and command exit status.
4. Compare required fields, types, row counts, blockers, safety flags, and
   checksums with the task contract.
5. Run an approved apply smoke only when the task explicitly authorizes it,
   the exact token is present, prerequisites are backed up, and the user has
   requested that environment mutation.

Never infer a valid approval token. Never substitute `head`, a loose boolean,
or an arbitrary shell command for a fixed migration/import target.

## Output

Return:

```txt
VDS smoke verdict: close / patch / blocked
Command and environment:
Main status:
Expected versus observed counts:
Contract findings:
Safety findings:
Artifacts reviewed:
Next safe step:
```

Do not edit source files during a smoke review.
