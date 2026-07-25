---
name: bondradar-request-lock
description: Lock a BondRadar TaskXXX implementation or review request to its exact scope, artifacts, safety invariants, and next-task handoff. Use before safety-critical work; do not use to broaden or execute the task.
---

# BondRadar Request Lock

## Purpose

Convert the request and repository state into a fixed implementation contract
before editing.

## Lock

Record:

```txt
task_id
mode_name
primary_input_artifacts
audit_input_artifacts
main_output_artifacts
wrapper_output_artifacts
allowed_files
forbidden_files
allowed_side_effects
forbidden_side_effects
required_status_semantics
required_readiness_flags
required_safety_flags
next_safe_task
verification_commands
commit_or_push_authority
```

Inspect the current implementation and tests before finalizing the lock. Treat
newer user instructions as superseding older task prompts.

## Safety

Unless the task explicitly authorizes an operation, lock these as forbidden:

```txt
DB mutation
Alembic execution
import, upsert, insert, update, or delete
external download, scraping, or cache writes
scoring, ranking, or recommendations
broker API calls
trading or paper trading
production export
```

An explicit apply task authorizes only its named operation, target, approval
token, and validation path.

## Output

Return:

```txt
Request lock:
Allowed changes:
Forbidden changes:
Inputs and outputs:
Safety invariants:
Readiness handoff:
Verification:
Open blockers:
```

Do not implement the task while preparing a request lock.
