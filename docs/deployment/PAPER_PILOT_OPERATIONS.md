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

- corporate universe action plan is ready;
- live data readiness is ready or an accepted warning is documented;
- ML validation suite completed;
- recommended `model_run_id` selected;
- strategy robustness is acceptable;
- live paper readiness is ready;
- pilot bootstrap dry-run is prepared;
- scheduler dry-run is safe;
- backend tests are green;
- frontend build is green;
- database backup is created.

Recommended preflight commands:

```bash
python -m compileall backend/app
python -m pytest backend/tests -q
cd frontend && npm run build
bash scripts/postgres_backup.sh
```

## 2. Launch Sequence

Review diagnostics in this order:

```text
GET  /api/data-readiness/corporate-universe/action-plan
GET  /api/data-readiness/live
GET  /api/data-readiness/live/action-plan
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

## 3. Daily Monitoring

Daily checks:

- open `/live-paper`;
- open `/live-paper/schedules`;
- review schedule status and next run time;
- review recent cycle status;
- review warnings and errors;
- confirm latest market data and prediction coverage remain fresh;
- confirm latest backup exists.

Useful API checks:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/paper-trading/live/monitoring/overview
curl -s http://127.0.0.1:8000/api/data-readiness/live
```

## 4. Weekly Monitoring

Weekly checks:

- open `/live-paper/portfolios/:id` for active virtual portfolios;
- review snapshots and equity curve;
- review completed, blocked, and skipped cycles;
- inspect position concentration;
- review transaction and operation history;
- review data freshness;
- review quality gate status;
- confirm backup restore instructions are still current.

## 5. Stop Conditions

Pause the pilot workflow for manual review if any of these conditions persist:

- data pipeline repeatedly fails;
- model predictions are unavailable;
- paper readiness becomes `not_ready`;
- scheduler errors repeat;
- unexpected database growth;
- quality gate becomes `blocked`;
- manual review identifies data corruption;
- backups are missing or stale.

Do not treat a pilot pause as a model conclusion. It is an operational control
for data quality and system safety.

## 6. End-of-3-Month Review

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

## 7. Recovery Checklist

When recovering from an incident:

1. Stop schedule execution through the UI or API if needed.
2. Export current logs.
3. Create a database backup before manual repair.
4. Inspect `/api/data-readiness/live`.
5. Inspect `/api/pre-deploy/paper-pilot/quality-gate`.
6. Restore from backup only when the current database state is not acceptable.
7. Run scheduler dry-run before resuming schedules.
