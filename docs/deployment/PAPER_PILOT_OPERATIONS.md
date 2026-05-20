# BondRadar Paper Pilot Operations

This document describes operational checks for the future 50k virtual paper
pilot. The pilot is a field observation workflow for further research. It does
not use real money and does not call broker actions.

Pilot context:

- virtual initial capital: 50000 RUB;
- planned duration: 60-90 days;
- mode: virtual paper only;
- focus: corporate bonds;
- OFZ separated and de-prioritized from the main working universe.

## 1. Before Pilot Launch

Complete this checklist before creating any live paper schedule:

- production env validation has passed;
- server sanity check has passed;
- production-like dry launch smoke check has passed;
- live data bootstrap plan has been reviewed;
- corporate universe action plan is ready;
- live data readiness is ready or an accepted warning is documented;
- ML validation suite completed;
- recommended `model_run_id` selected;
- strategy robustness is acceptable;
- live paper readiness is ready;
- pilot bootstrap dry-run is prepared;
- scheduler dry-run is safe;
- external risk regime is reviewed;
- backend tests are green;
- frontend build is green;
- database backup is created.
- live operations runner cadence has been reviewed.
- runtime retention plan has been reviewed.

Recommended preflight commands:

```bash
python -m compileall backend/app
python -m pytest backend/tests -q
cd frontend && npm run build
python scripts/validate_production_env.py --env-file .env.production
python scripts/server_sanity_check.py --env-file .env.production
python scripts/prod_smoke_check.py --skip-quality-gate
python scripts/live_data_bootstrap.py --json-output ./live_data_bootstrap_plan.json
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
bash scripts/postgres_backup.sh
```

The backup helper is intended to run from the VDS host against the production
compose localhost-only PostgreSQL binding.

See `docs/deployment/PRODUCTION_DRY_LAUNCH.md` for the production-like smoke
flow and optional quality gate smoke command.

See `docs/deployment/LIVE_DATA_BOOTSTRAP.md` for controlled data sync, pipeline,
ML validation, and quality-gate orchestration.

See `docs/deployment/LIVE_OPERATIONS_RUNNER.md` for monitoring, data-refresh,
paper dry-run, and confirmed virtual paper execution cadence examples.

See `docs/deployment/RUNTIME_HARDENING.md` for runtime hardening, backup
verification, retention cleanup, and pause procedures.

See `docs/deployment/PROJECT_OPERATING_MODEL.md` for the plain-language
operating model and `docs/deployment/RELEASE_CANDIDATE_GO_NO_GO.md` for the
final release candidate review before VDS preparation.

Operator UI map:

- `/`: bond and company overview;
- `/live-paper`: virtual paper monitoring dashboard;
- `/live-paper/schedules`: schedules and safe run checks;
- `/live-paper/pilot-bootstrap`: pilot schedule preparation;
- `/live-paper/portfolios/:id`: portfolio details, positions, operations, and
  snapshots;
- `/risk/external-regime`: external risk overlay.

Auth/RBAC is a separate pre-public hardening task. Do not expose the operator UI
as a public multi-user surface until that hardening is complete.

## 2. Launch Sequence

Review diagnostics in this order:

```text
GET  /api/data-readiness/corporate-universe/action-plan
GET  /api/data-readiness/live
GET  /api/data-readiness/live/action-plan
GET  /api/risk/external-regime
python scripts/live_data_bootstrap.py --json-output ./live_data_bootstrap_plan.json
POST /api/ml/validation-suite/run
POST /api/pre-deploy/paper-pilot/quality-gate
POST /api/paper-trading/live/pilots/bootstrap
GET  /api/paper-trading/live/monitoring/overview
```

Use `POST /api/paper-trading/live/pilots/bootstrap` first with:

```json
{
  "dry_run_only": true,
  "create_schedule": true
}
```

Actual schedule creation should happen only after dry-run review confirms that
readiness, payloads, next run time, interval, and virtual capital are correct.

After a schedule is created, use scheduler dry-run before any scheduled paper
cycle is executed:

```text
POST /api/paper-trading/live/schedules/run-due
```

with:

```json
{
  "dry_run": true
}
```

The recommended operational sequence after schedule creation is:

```text
release preflight
production-like smoke
live data bootstrap
pre-deploy quality gate
pilot bootstrap dry-run
create schedule
live operations runner monitoring
live operations runner data-refresh
paper dry-run
confirmed virtual paper execution
```

## 3. External Risk Overlay

The external risk overlay is controlled by the operator. It records current
outside-context caution without pretending the ML model understands news.

Modes:

- `normal`: normal virtual paper operation may continue;
- `elevated`: confirmed paper execution requires explicit acknowledgement in the
  operations runner;
- `severe`: confirmed paper execution is blocked by default.

Data refresh may continue in elevated or severe modes. Paper dry-run may
continue because it does not mutate paper state.

Current regime:

```bash
curl -s http://127.0.0.1:8000/api/risk/external-regime
```

Operators can also review and update the current external risk regime from the
frontend route `/risk/external-regime`.

Manual elevated regime:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/risk/external-regime \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "elevated",
    "reason": "Manual operator caution before paper execution window.",
    "source": "manual"
  }'
```

## 4. Daily Monitoring

Daily checks:

- open `/live-paper`;
- open `/live-paper/schedules`;
- review schedule status and next run time;
- review recent cycle status;
- review warnings and errors;
- confirm latest market data and prediction coverage remain fresh;
- confirm latest backup exists.
- confirm retention plan was reviewed before any cleanup execution.

Useful API checks:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/risk/external-regime
curl -s http://127.0.0.1:8000/api/paper-trading/live/monitoring/overview
curl -s http://127.0.0.1:8000/api/data-readiness/live
python scripts/live_operations_runner.py --mode monitoring
python scripts/ops_retention.py --json-output ./logs/ops_retention_plan.json
```

## 5. Weekly Monitoring

Weekly checks:

- open `/live-paper/portfolios/:id` for active virtual portfolios;
- review snapshots and equity curve;
- review completed, blocked, and skipped cycles;
- inspect position concentration;
- review transaction and operation history;
- review data freshness;
- review quality gate status;
- confirm backup restore instructions are still current.

## 6. Stop Conditions

Pause the pilot workflow for manual review if any of these conditions persist:

- data pipeline repeatedly fails;
- model predictions are unavailable;
- paper readiness becomes `not_ready`;
- scheduler errors repeat;
- external risk regime is `severe`;
- unexpected database growth;
- quality gate becomes `blocked`;
- manual review identifies data corruption;
- backups are missing or stale.

Do not treat a pilot pause as a model conclusion. It is an operational control
for data quality and system safety.

## 7. End-of-3-Month Review

At the end of the observation period, prepare a review using:

- portfolio value trajectory;
- drawdown;
- completed cycle count;
- blocked and skipped cycle counts;
- warning and error counts;
- position concentration;
- data freshness;
- prediction coverage;
- comparison with baseline where available.

The pilot result is evidence for further research, not proof that a model is
ready for any real-money workflow.

## 8. Recovery Checklist

When recovering from an incident:

1. Stop schedule execution through the UI or API if needed.
2. Export current logs.
3. Create a database backup before manual repair.
4. Inspect `/api/data-readiness/live`.
5. Inspect `/api/pre-deploy/paper-pilot/quality-gate`.
6. Restore from backup only when the current database state is not acceptable.
7. Run scheduler dry-run before resuming schedules.

To pause confirmed virtual paper execution while keeping monitoring and data
refresh, disable only the paper execute cron entry or systemd timer. Keep
monitoring active, and run paper dry-run before resuming confirmed execution.
