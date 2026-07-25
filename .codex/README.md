# BondRadar Codex Agents and Skills

BondRadar keeps project-specific skills in `.agents/skills` and narrow,
read-only custom reviewers in `.codex/agents`. The main Codex agent remains the
orchestrator.

## Discover skills

```txt
/skills
```

## Explicitly use a skill

```txt
$bondradar-request-lock
Prepare a request lock for Task212. Do not implement it.
```

## Implement a task

```txt
Read AGENTS.md.

Use:
$bondradar-request-lock
$bondradar-output-contract-auditor
$bondradar-safety-gate-auditor

Implement only TaskXXX.
Use Plan mode when required by AGENTS.md.
```

Use the built-in worker for implementation only after the request scope and
safety boundaries are locked.

## Review a task with subagents

```txt
Review TaskXXX against its expected contract.

Spawn:
- bondradar_contract_auditor
- bondradar_safety_gate_auditor

Wait for both.
Return one consolidated close-or-patch verdict.
Do not edit files.
```

Parallel agents are intended mainly for independent read-only reviews, not
simultaneous edits.

## Review VDS output

```txt
Have bondradar_vds_smoke_reviewer review this VDS output.
Also have bondradar_safety_gate_auditor independently verify dangerous flags.
Wait for both and consolidate.
```

## Review Pulse examples

```txt
Have bondradar_pulse_intelligence_reviewer classify these Pulse posts.
Have bondradar_source_seed_reviewer independently identify any official-source candidates.
Do not treat Pulse claims as source-backed.
```

## Review financial evidence

```txt
Have bondradar_financial_evidence_reviewer audit the proposed metric evidence.
Have bondradar_ratio_methodology_reviewer check whether the evidence is sufficient for ratio computation.
Do not import or score.
```

After adding or changing repository skills or agents, restart Codex if they do
not appear. Run `/skills`, then ask:

```txt
Summarize the project instructions, available BondRadar skills, and available BondRadar custom agents. Do not modify files.
```
