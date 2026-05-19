# BondRadar Runtime Hardening

This guide connects the production env checks, server sanity checks, backup
helpers, retention cleanup, and operations runner templates for VDS runtime
use.

It does not deploy anything and does not start the 50k virtual paper pilot.
BondRadar pilot operation remains virtual paper only:

- no broker actions;
- no real-money flow;
- paper due execution requires explicit runner confirmation;
- data refresh cadence is separate from paper execution cadence.

## 1. Recommended Runtime Sequence

Use this sequence after cloning the project and creating `.env.production`:

```text
VDS provisioning checklist
first-deploy command rendering
validate .env.production
server sanity check
release preflight
production compose up
production-like smoke check
live data bootstrap
pre-deploy quality gate
operations runner
runtime retention and backups
release candidate report
```

Provisioning and first-deploy references:

```text
docs/deployment/VDS_PROVISIONING.md
docs/deployment/FIRST_DEPLOY_CHECKLIST.md
scripts/render_first_deploy_commands.py
```

Runtime hardening starts after the server is provisioned and the first deploy
verification commands have been reviewed.

## 2. Production Env Validation

Validate `.env.production` before building or starting services:

```bash
python scripts/validate_production_env.py \
  --env-file .env.production \
  --json-output ./logs/env_validation.json
```

Strict mode treats warnings as failures:

```bash
python scripts/validate_production_env.py \
  --env-file .env.production \
  --strict
```

The example env file is expected to fail validation until sample secrets are
replaced.

## 3. Server Sanity Check

Run a local server-side sanity check before enabling pilot operations:

```bash
python scripts/server_sanity_check.py \
  --env-file .env.production \
  --json-output ./logs/server_sanity.json
```

This checks required runtime files, creates `./logs` and `./backups` when
needed, validates production env values, and verifies production Docker Compose
configuration unless `--skip-docker` is provided.

Use this variant when Docker is unavailable in the current shell:

```bash
python scripts/server_sanity_check.py \
  --env-file .env.production \
  --skip-docker
```

## 4. Cron Examples

Cron examples live in:

```text
deploy/cron/bondradar.example.crontab
```

They are commented by default and must be reviewed before use. The examples
cover:

- hourly monitoring;
- weekday data refresh;
- daily paper dry-run;
- confirmed virtual paper execution;
- daily PostgreSQL backup;
- retention planning.

Do not install cron entries until production smoke, live data bootstrap, and the
pre-deploy quality gate have been reviewed.

## 5. Systemd Timer Examples

Systemd examples live in:

```text
deploy/systemd/
```

They use:

```text
WorkingDirectory=/opt/BondRadar
EnvironmentFile=/opt/BondRadar/.env.production
```

Review every file before copying it to `/etc/systemd/system`. Example install
flow:

```bash
sudo cp deploy/systemd/bondradar-live-operations-monitoring.* /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bondradar-live-operations-monitoring.timer
```

Enable only monitoring first. Add data refresh, dry-run, and confirmed virtual
paper execution after stable observation.

## 6. Log and Backup Retention

Plan cleanup without deleting anything:

```bash
python scripts/ops_retention.py \
  --json-output ./logs/ops_retention_plan.json
```

Execute cleanup only after reviewing candidates:

```bash
python scripts/ops_retention.py \
  --execute \
  --json-output ./logs/ops_retention_completed.json
```

The helper deletes only matching files older than the configured retention days.
It does not delete directories and does not follow symlinks.

## 7. Backup Verification

Create a backup before deployments and at least daily during the pilot:

```bash
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
export BACKUP_DIR=./backups
bash scripts/postgres_backup.sh
```

Periodically test restore on a non-production database. Do not restore over the
runtime database unless an operator has reviewed the situation and set:

```bash
export RESTORE_CONFIRM=yes
```

## 8. After Restart

After a restart or deployment update, check:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl -s http://127.0.0.1:8000/api/health
python scripts/prod_smoke_check.py --skip-quality-gate
python scripts/live_operations_runner.py --mode monitoring
```

When a model run is available, include the quality gate smoke and the pre-deploy
quality gate before any pilot launch action.

## 9. Pause Paper Execution

To pause confirmed virtual paper execution while keeping monitoring and data
refresh:

1. Disable the paper execute cron entry or systemd timer.
2. Keep monitoring enabled.
3. Keep data refresh enabled only if the data pipeline is stable.
4. Run paper dry-run manually before resuming execution.
5. Review `/api/paper-trading/live/monitoring/overview`.
6. Review the latest pre-deploy quality gate report.

For systemd:

```bash
sudo systemctl disable --now bondradar-live-operations-paper-execute.timer
sudo systemctl status bondradar-live-operations-monitoring.timer
```

For cron, comment out the paper execute line and keep monitoring/data-refresh
entries as appropriate.

## 10. Safety Notes

- Runtime helpers do not install services automatically.
- Cron and systemd files are examples.
- The operations runner uses explicit confirmation for data pipeline and
  confirmed virtual paper execution.
- The retention helper is dry-run by default.
- Runtime hardening does not prove model quality or data readiness by itself.

## 11. Release Candidate Aggregation

After required JSON reports are saved under `./logs`, generate the final local
release candidate report:

```bash
python scripts/release_candidate_report.py \
  --logs-dir ./logs \
  --json-output ./logs/release_candidate_report.json \
  --markdown-output ./logs/release_candidate_report.md
```

Review the output together with:

```text
docs/deployment/RELEASE_CANDIDATE_GO_NO_GO.md
docs/deployment/PROJECT_OPERATING_MODEL.md
```

The report is a local artifact aggregator. It does not call HTTP endpoints, does
not run commands, and does not replace human review.
