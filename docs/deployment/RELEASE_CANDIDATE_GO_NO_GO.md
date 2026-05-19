# BondRadar Release Candidate Go/No-Go

This is the final local checklist before VDS deployment preparation.

A release candidate does not mean the model is proven. It means the repository
and operations pack are technically ready for controlled virtual paper
observation.

BondRadar pilot operation is:

- virtual paper only;
- no broker actions;
- no real-money flow;
- focused on corporate bonds, with OFZ separated from the main working
  universe.

## 1. Required Local Checks

Run these before treating the repository as a release candidate:

```bash
python -m compileall backend/app
python -m pytest backend/tests -q
cd frontend && npm run build
docker compose -f docker-compose.prod.yml --env-file .env.production.example config --quiet
python scripts/release_preflight.py --json-output ./logs/release_preflight.json
python scripts/validate_production_env.py --env-file .env.production --json-output ./logs/env_validation.json
python scripts/server_sanity_check.py --env-file .env.production --skip-docker --json-output ./logs/server_sanity.json
```

`.env.production.example` is expected to fail env validation because sample
secrets are intentionally present. Validate the real `.env.production` file
before deployment.

## 2. Production-like Checks

Run these against a local or VDS-like production compose stack:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
python scripts/prod_smoke_check.py --json-output ./logs/prod_smoke.json
python scripts/live_data_bootstrap.py --json-output ./logs/live_data_bootstrap_plan.json
python scripts/live_operations_runner.py --mode monitoring --json-output ./logs/live_ops_monitoring.json
```

If a completed model run exists:

```bash
python scripts/prod_smoke_check.py \
  --model-run-id <MODEL_RUN_ID> \
  --date-from <YYYY-MM-DD> \
  --date-to <YYYY-MM-DD> \
  --json-output ./logs/prod_smoke_with_gate.json
```

## 3. Data, Model, and Paper Gates

Review these gates before the 50k virtual paper pilot:

- corporate universe action plan is ready, or sync plan is understood;
- live data readiness is ready, or warnings are explicitly accepted;
- ML validation suite produced a `recommended_model_run_id`;
- strategy robustness has no fail-level flags;
- live paper readiness is ready, or warnings are explicitly accepted;
- pre-deploy quality gate core gates pass;
- pilot bootstrap dry-run is prepared.

Warnings are not automatically acceptable. They require human review.

## 4. VDS Deployment No-Go Conditions

Do not proceed with VDS deployment preparation when any of these are true:

- backend tests fail;
- frontend build fails;
- production compose config fails;
- production env validation fails;
- server sanity check fails;
- production smoke check fails;
- backup scripts have not been reviewed;
- operator has not reviewed the runbook and runtime hardening docs.

## 5. 50k Virtual Paper Pilot No-Go Conditions

Do not start the 50k virtual paper pilot when any of these are true:

- no real corporate universe is available;
- live data readiness is `not_ready`;
- no recommended model run is available;
- quality gate is `blocked`;
- pilot bootstrap dry-run is blocked;
- monitoring overview has critical alerts;
- database backup has not been created.

## 6. Required Saved Artifacts

Save these under `./logs`:

```text
release_preflight.json
env_validation.json
server_sanity.json
prod_smoke.json
live_data_bootstrap_plan.json
ml_validation_suite.json
quality_gate.json
pilot_bootstrap_dry_run.json
live_ops_monitoring.json
```

Create the final aggregation report:

```bash
python scripts/release_candidate_report.py \
  --logs-dir ./logs \
  --json-output ./logs/release_candidate_report.json \
  --markdown-output ./logs/release_candidate_report.md
```

## 7. Final Human Review

Before VDS deployment preparation, confirm:

- I understand the project is virtual paper only.
- I understand no broker integration exists.
- I understand 3 months is field observation, not proof.
- I know how to pause paper execution.
- I know how to restore backup.
- I know where JSON reports are stored.
- I know which reports block deployment and which reports block pilot launch.
