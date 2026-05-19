# BondRadar Live Data Bootstrap

This guide describes `scripts/live_data_bootstrap.py`, a safe helper for moving a
running backend from empty or demo data toward live corporate-bond data and model
readiness.

The default mode is plan-only. It only calls read-only readiness and action-plan
endpoints. Data-changing remote actions require explicit flags and:

```bash
--confirm-live-data-bootstrap yes
```

The script does not create paper schedules directly, does not execute paper
cycles, does not call scheduler execution with live execution mode, does not call
broker APIs, and does not use real money.

## 1. Safe Plan-only Run

Use this first on every environment:

```bash
python scripts/live_data_bootstrap.py \
  --json-output ./live_data_bootstrap_plan.json
```

This collects:

```text
GET /api/data-readiness/corporate-universe/action-plan
GET /api/data-readiness/live
GET /api/data-readiness/live/action-plan
```

The script can return `planned` even when readiness is `not_ready` or the action
plan is `blocked`; this means the plan was collected successfully.

## 2. Universe Sync

Run MOEX corporate universe sync only after reviewing the plan:

```bash
python scripts/live_data_bootstrap.py \
  --execute-universe-sync \
  --confirm-live-data-bootstrap yes \
  --json-output ./live_data_bootstrap_universe.json
```

The script uses `sync_payload` returned by the corporate universe action plan. It
does not invent request fields.

## 3. Data Pipeline Run

Run the proposed data pipeline only after reviewing `pipeline_payload`:

```bash
python scripts/live_data_bootstrap.py \
  --execute-data-pipeline \
  --wait-pipeline \
  --confirm-live-data-bootstrap yes \
  --json-output ./live_data_bootstrap_pipeline.json
```

The script uses `pipeline_payload` returned by the live data action plan, then
optionally polls:

```text
GET /api/pipeline/runs/{run_id}
```

until a terminal status is reached or the timeout expires.

## 4. ML Validation Suite

Run ML validation only after the data chain is ready enough for validation:

```bash
python scripts/live_data_bootstrap.py \
  --run-ml-validation \
  --confirm-live-data-bootstrap yes \
  --json-output ./live_data_bootstrap_ml.json
```

The script uses service defaults for training configs and records
`recommended_model_run_id` when the API returns it.

## 5. Quality Gate

Run the pre-deploy quality gate with an explicit model:

```bash
python scripts/live_data_bootstrap.py \
  --run-quality-gate \
  --model-run-id <MODEL_RUN_ID> \
  --date-from <YYYY-MM-DD> \
  --date-to <YYYY-MM-DD> \
  --json-output ./live_data_bootstrap_gate.json
```

If ML validation was run in the same invocation and returns a recommended model,
the quality gate can use that id when `--model-run-id` is omitted:

```bash
python scripts/live_data_bootstrap.py \
  --run-ml-validation \
  --run-quality-gate \
  --confirm-live-data-bootstrap yes \
  --json-output ./live_data_bootstrap_ml_gate.json
```

The script validates that quality-gate payload previews remain dry-run safe.

## 6. Full Controlled Bootstrap

Use this only after a successful plan-only run and operator review:

```bash
python scripts/live_data_bootstrap.py \
  --execute-universe-sync \
  --execute-data-pipeline \
  --wait-pipeline \
  --run-ml-validation \
  --run-quality-gate \
  --confirm-live-data-bootstrap yes \
  --json-output ./live_data_bootstrap_full.json
```

This still does not create live paper schedules directly and does not execute
paper cycles.

## 7. Useful Options

```text
--backend-url
--recent-days
--minimum-corporate-bonds
--minimum-bonds-with-recent-market-snapshot
--minimum-bonds-with-recent-features
--minimum-bonds-with-predictions
--include-ofz
--date-from
--date-to
--horizon-days
--return-method
--fail-fast
```

If dates are omitted:

```text
date_to = today's UTC date
date_from = date_to - 90 days
```

## 8. After Bootstrap

Review:

```text
GET /api/data-readiness/live
POST /api/pre-deploy/paper-pilot/quality-gate
/live-paper
/live-paper/schedules
```

Bootstrap completion is not proof that the model or pilot is ready. Treat the
result as operational evidence for the next review step.

After the quality gate and pilot bootstrap dry-run are acceptable, move to the
live operations runner for separate cadences:

```bash
python scripts/live_operations_runner.py \
  --mode monitoring \
  --json-output ./logs/live_ops_monitoring.json
```

Recommended sequence:

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

See `docs/deployment/LIVE_OPERATIONS_RUNNER.md`.
