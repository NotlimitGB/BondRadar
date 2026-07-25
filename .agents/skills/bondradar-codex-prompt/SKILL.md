---
name: bondradar-codex-prompt
description: Draft a decision-complete BondRadar TaskXXX Codex prompt with exact scope, contracts, safety gates, tests, and handoff. Use for task writing; do not implement the task or authorize unsafe execution.
---

# BondRadar Codex Prompt

## Purpose

Write an implementation-ready TaskXXX request that extends the current chain
without mixing planning, preview, review, readiness, and apply responsibilities.

## Required prompt structure

Include:

```txt
Title and mode name
Summary
Current upstream baseline
Explicit-first input resolution
Main and wrapper artifact names
Deterministic row and field contracts
Status and readiness semantics
Allowed behavior
Forbidden behavior
Stable blocker rules
Markdown sections and safety phrases
Focused, sibling, broad, and smoke verification
Assumptions
```

Lock one safe next-task unlock. Distinguish expected evidence gaps from fatal
report blockers. Require type-stable warning, blocked, and failed/default
outputs.

Apply tasks must name the exact approval token, operation, target, pre-check,
transaction or rollback behavior, and post-check. Planning and review tasks
must keep execution flags false.

## Output

Return only the proposed TaskXXX prompt. Do not edit files, implement the mode,
run apply commands, or invent production evidence.
