# BondRadar Live Operations Runner

`scripts/live_operations_runner.py` is the operations runner for the future
60-90 day virtual paper pilot. It is designed for manual use, cron, or a
systemd timer on a VDS.

The runner separates data refresh cadence from virtual paper execution cadence.
The default mode is safe monitoring: no data pipeline mutation, no paper schedule
execution, and no paper portfolio mutation.

The pilot remains virtual paper only:

- no broker actions;
- no real-money flow;
- no external trading actions;
- all reports should be treated as operational evidence, not proof of model
  success.

## 1. Recommended Cadence

Suggested starting cadence:

- monitoring: every hour;
- data-refresh: every 1-3 hours during market or business hours;
- paper-dry-run: once per day before the execution window;
- paper-execute: once per day, after dry-run review or after a stable automation
  period.

Adjust the cadence after observing API stability, data freshness, and scheduler
behavior.

## 2. Monitoring Mode

Default command:

```bash
python scripts/live_operations_runner.py \
  --mode monitoring \
  --json-output ./logs/live_ops_monitoring.json
```

This calls:

```text
GET /api/health
GET /api/risk/external-regime
GET /api/data-readiness/live
GET /api/data-readiness/live/action-plan
GET /api/paper-trading/live/monitoring/overview
```

Readiness may still be `not_ready`, `warning`, `blocked`, or
`needs_attention`; that does not fail the runner when endpoints respond with the
expected shape.

## 3. Frequent Data Refresh

Plan-only data refresh check:

```bash
python scripts/live_operations_runner.py \
  --mode data-refresh \
  --json-output ./logs/live_ops_data_refresh_plan.json
```

Run the proposed data pipeline only with explicit confirmation:

```bash
python scripts/live_operations_runner.py \
  --mode data-refresh \
  --execute-data-pipeline \
  --wait-pipeline \
  --confirm-live-operations yes \
  --json-output ./logs/live_ops_data_refresh.json
```

The runner uses `pipeline_payload` returned by:

```text
GET /api/data-readiness/live/action-plan
```

It does not invent pipeline request fields.

Data refresh does not stop because external risk is elevated or severe. The
overlay is for confirmed paper execution safety.

## 4. Paper Dry-run

Use this before the execution window:

```bash
python scripts/live_operations_runner.py \
  --mode paper-dry-run \
  --json-output ./logs/live_ops_paper_dry_run.json
```

This calls only:

```text
POST /api/paper-trading/live/schedules/run-due
```

with:

```json
{
  "dry_run": true
}
```

If the response does not confirm `dry_run=true`, the runner returns
`safety_failed`.

Paper dry-run may continue during elevated or severe external risk modes because
it is non-mutating.

## 5. Paper Execution

Virtual paper due execution requires explicit confirmation:

```bash
python scripts/live_operations_runner.py \
  --mode paper-execute \
  --execute-due-schedules \
  --confirm-live-operations yes \
  --json-output ./logs/live_ops_paper_execute.json
```

The runner first calls `run-due` with `dry_run=true`. It sends `dry_run=false`
only after the dry-run response confirms the safe marker.

If the execution response does not confirm `dry_run=false`, the runner returns
`safety_failed`.

External risk rules for confirmed paper execution:

- `elevated` requires `--ack-external-risk-elevated`;
- `severe` is blocked by default;
- `severe` can continue only with `--override-external-risk-severe`;
- `--confirm-live-operations yes` is still required separately.

Example for elevated mode after manual review:

```bash
python scripts/live_operations_runner.py \
  --mode paper-execute \
  --execute-due-schedules \
  --ack-external-risk-elevated \
  --confirm-live-operations yes \
  --json-output ./logs/live_ops_paper_execute.json
```

## 6. Full Cycle

Use this only after the bootstrap flow and quality gate review are acceptable:

```bash
python scripts/live_operations_runner.py \
  --mode full-cycle \
  --execute-data-pipeline \
  --wait-pipeline \
  --execute-due-schedules \
  --confirm-live-operations yes \
  --json-output ./logs/live_ops_full_cycle.json
```

The sequence is:

```text
health
live data readiness
live data action plan
optional data pipeline run
optional pipeline polling
post-pipeline readiness/action-plan checks
monitoring overview
run-due dry-run
confirmed due execution
post-execution monitoring overview
```

## 6.1 Prediction Date And Risk Policy

Pilot bootstrap schedules use the tested prediction date by default. The
schedule keeps the bootstrap `date_to` value as `as_of_date` for both the live
cycle and the paper rebalance request. This avoids silently switching from a
tested prediction date to the server's current date.

Use `use_current_date_as_of_date=true` only when the daily flow refreshes market
data, features, and predictions before paper execution. If current-date mode is
enabled and predictions are missing for the execution date, the cycle reports a
clear diagnostic telling the operator to refresh predictions or disable current
date mode.

Risk policy is explicit in the bootstrap payload and is copied into readiness
robustness, the schedule `cycle_request_json`, and the paper rebalance request.
The conservative defaults keep blocked risk candidates and insufficient credit
data out of portfolio construction.

Risk override mode is paper-only and requires both:

- `risk_override_enabled=true`;
- a non-empty `risk_override_reason`.

Use it only for technical validation or a controlled experiment. It should not
be the normal strategy posture.

## 7. Cron Examples

Create `./logs` before installing cron entries:

```bash
mkdir -p ./logs
```

Frequent monitoring:

```cron
0 * * * * cd /opt/BondRadar && /usr/bin/python3 scripts/live_operations_runner.py --mode monitoring --json-output ./logs/live_ops_monitoring_$(date +\%Y\%m\%d_\%H).json >> ./logs/live_ops_monitoring.log 2>&1
```

Frequent data refresh during weekdays:

```cron
0 9-18/2 * * 1-5 cd /opt/BondRadar && /usr/bin/python3 scripts/live_operations_runner.py --mode data-refresh --execute-data-pipeline --wait-pipeline --confirm-live-operations yes --json-output ./logs/live_ops_data_refresh_$(date +\%Y\%m\%d_\%H).json >> ./logs/live_ops_data_refresh.log 2>&1
```

Daily dry-run:

```cron
30 8 * * 1-5 cd /opt/BondRadar && /usr/bin/python3 scripts/live_operations_runner.py --mode paper-dry-run --json-output ./logs/live_ops_paper_dry_run_$(date +\%Y\%m\%d).json >> ./logs/live_ops_paper_dry_run.log 2>&1
```

Daily virtual paper execution:

```cron
45 8 * * 1-5 cd /opt/BondRadar && /usr/bin/python3 scripts/live_operations_runner.py --mode paper-execute --execute-due-schedules --confirm-live-operations yes --json-output ./logs/live_ops_paper_execute_$(date +\%Y\%m\%d).json >> ./logs/live_ops_paper_execute.log 2>&1
```

Cron examples are not installed automatically. Review paths, Python location,
timezone, and logs before enabling them.

The repository also includes a commented cron template:

```text
deploy/cron/bondradar.example.crontab
```

Review `docs/deployment/RUNTIME_HARDENING.md` before installing it.

## 8. Systemd Timer Examples

Example service and timer files live in:

```text
deploy/systemd/
```

They cover monitoring, data refresh, paper dry-run, confirmed virtual paper
execution, and PostgreSQL backup. The templates use:

```text
WorkingDirectory=/opt/BondRadar
EnvironmentFile=/opt/BondRadar/.env.production
```

Copy and enable only after reviewing paths, cadence, and safety settings.

## 9. Retention and Sanity Helpers

Before enabling recurring operations, run:

```bash
python scripts/server_sanity_check.py --env-file .env.production
```

Plan cleanup of old operation reports and database backups:

```bash
python scripts/ops_retention.py \
  --json-output ./logs/ops_retention_plan.json
```

`scripts/ops_retention.py` is dry-run by default. Use `--execute` only after
reviewing candidates.

## 10. Stop and Pause Guidance

To pause operations:

1. Disable the cron entry or systemd timer.
2. Run monitoring mode only.
3. Run paper dry-run mode only when needed.
4. Check `/live-paper/schedules`.
5. Check the pre-deploy quality gate.
6. Review JSON reports and backend logs.

Resume confirmed execution only after the cause of the pause is understood.

## 11. Safety Notes

The runner does not call single-schedule execution, cycle execution, portfolio
rebalance, or mark-period endpoints. It uses only the batch run-due endpoint for
paper due handling and always requires a dry-run before confirmed due execution.

Runner completion is not a model-quality conclusion. Treat each run as an
operations report for readiness, cadence, and safety review.

## 12. External Risk API

Check current external risk mode:

```bash
curl -s http://127.0.0.1:8000/api/risk/external-regime
```

The same regime can be reviewed and updated in the frontend at
`/risk/external-regime`.

Set elevated mode:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/risk/external-regime \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "elevated",
    "reason": "Manual operator caution before paper execution window.",
    "source": "manual"
  }'
```
