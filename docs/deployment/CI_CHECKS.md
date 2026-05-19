# BondRadar CI Checks

This document explains the non-deploying GitHub Actions checks used before VDS
deployment preparation.

CI is a code, build, and static deployment sanity layer. It does not prove live
data readiness, model quality, or virtual paper pilot readiness.

## Main CI Workflow

`.github/workflows/ci.yml` runs on pushes to `main`, pull requests targeting
`main`, and manual dispatch.

It checks:

- backend dependencies install from `backend/requirements-dev.txt`;
- backend code compiles with `python -m compileall backend/app`;
- backend tests pass with `python -m pytest backend/tests -q`;
- frontend dependencies install with `npm ci`;
- frontend production build passes with `npm run build`;
- `docker-compose.prod.yml` parses with `.env.production.example`.

The compose job runs config validation only. It does not start containers, build
images, or contact runtime services.

## Release Candidate Workflow

`.github/workflows/release-candidate.yml` is manual-only through
`workflow_dispatch` because it is release-oriented and heavier than the main CI
loop.

It runs focused tests for:

- release preflight;
- production-like smoke checks;
- live data bootstrap;
- live operations runner;
- production env validation;
- retention planning;
- server sanity checks;
- release candidate report aggregation;
- CI guardrails.

It also runs safe local script checks:

```bash
python scripts/ops_retention.py --json-output /tmp/bondradar_retention_plan.json
python scripts/release_candidate_report.py --logs-dir ./logs --json-output /tmp/bondradar_release_candidate_report.json --markdown-output /tmp/bondradar_release_candidate_report.md
python scripts/ci_guardrails.py
```

`.env.production.example` validation is expected to fail because the example file
contains sample secrets. The release-candidate workflow checks that failure
explicitly so the example file cannot be mistaken for a real production env.

## CI Guardrails

`scripts/ci_guardrails.py` scans only `scripts/*.py` for direct calls to
operational endpoints that should not appear in automation helpers. The intended
batch due-schedule endpoint is allowed:

```text
/api/paper-trading/live/schedules/run-due
```

The guardrail is intentionally narrow. It avoids documentation scans so operator
guides can describe risks without causing false positives.

## What CI Does Not Do

CI must not:

- deploy to VDS;
- start containers;
- build production images;
- call real market-data services;
- run MOEX sync;
- run the data pipeline;
- train ML models;
- generate predictions;
- create paper portfolios;
- execute paper schedules;
- use deploy keys or production secrets.

## Interpreting Results

Main CI passing means the repository compiles, tests, builds, and has valid
production compose structure.

Release candidate workflow passing means the deployment and operations helper
tests also pass and the safe static checks still work.

Neither workflow replaces:

- live data readiness checks;
- ML validation suite review;
- pre-deploy paper pilot quality gate review;
- production smoke checks against a running stack;
- human review of `docs/deployment/RELEASE_CANDIDATE_GO_NO_GO.md`.
