# BondRadar VDS Deployment Runbook

This runbook prepares BondRadar for a future VDS deployment. It does not deploy
the project by itself and does not start the 50k virtual paper pilot.

BondRadar production operation is virtual paper mode only:

- no broker actions;
- no real-money flow;
- paper schedules and portfolios are local virtual records;
- corporate bonds are the main working universe, with OFZ separated and
  de-prioritized for the pilot workflow.

## 1. Architecture Overview

```text
VDS
|-- backend API
|-- frontend UI served by nginx
|-- PostgreSQL
|-- Docker Compose
|-- persistent volumes
|-- backups
`-- scheduled paper/data operations
```

The backend exposes FastAPI endpoints under `/api`. PostgreSQL stores issuer,
bond, market-data, ML, and virtual paper records. The production frontend serves
the built Vite bundle through nginx and proxies `/api` to the backend container.

## 2. Server Prerequisites

Recommended baseline:

- Ubuntu-based VDS;
- Docker Engine;
- Docker Compose plugin;
- Git;
- SSH access with restricted keys;
- basic firewall rules;
- enough disk space for PostgreSQL data, backups, logs, and ML artifacts.

Do not expose PostgreSQL to the public internet. The production compose file
binds PostgreSQL to `127.0.0.1` only so host-level backup and restore scripts can
reach it while public network access stays closed.

## 3. Required Environment Variables

Create `.env.production` from `.env.production.example` and review every value.
Do not commit `.env.production`.

Core variables used by the current project:

```text
POSTGRES_DB
POSTGRES_USER
POSTGRES_PASSWORD
POSTGRES_PORT
DATABASE_URL
BACKEND_CORS_ORIGINS
MOEX_ISS_BASE_URL
MOEX_ISS_TIMEOUT_SECONDS
ML_ARTIFACT_DIR
```

Operational variables used by helper scripts:

```text
POSTGRES_HOST
PGPASSWORD
BACKUP_DIR
LOG_LEVEL
BACKEND_PORT
FRONTEND_PORT
```

`DATABASE_URL` should keep using the Docker service name:

```text
postgresql+psycopg://<user>:<password>@postgres:5432/<database>
```

Host-level backup scripts should use:

```text
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

`BACKEND_CORS_ORIGINS` is parsed by Pydantic settings. Use JSON-list syntax:

```text
BACKEND_CORS_ORIGINS=["https://bondradar.example.com"]
```

## 4. First Deployment Flow

Run from the VDS shell after installing prerequisites:

```bash
git clone <repo-url> BondRadar
cd BondRadar
cp .env.production.example .env.production
nano .env.production
```

Run the release preflight before starting services:

```bash
python scripts/release_preflight.py
```

Build and start the production compose stack:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

`docker-compose.prod.yml` uses `frontend/Dockerfile.prod`, which builds the Vite
bundle and serves it through nginx. The nginx config proxies `/api` to
`http://backend:8000`.

Check services:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/data-readiness/live
```

Run the production-like smoke check:

```bash
python scripts/prod_smoke_check.py --skip-quality-gate
```

When a completed model run is available, include the pre-deploy quality gate
smoke:

```bash
python scripts/prod_smoke_check.py \
  --model-run-id <MODEL_RUN_ID> \
  --date-from <YYYY-MM-DD> \
  --date-to <YYYY-MM-DD>
```

See `docs/deployment/PRODUCTION_DRY_LAUNCH.md` for troubleshooting and expected
outcomes.

Open the frontend:

```text
http://<server-ip>:5173
```

Do not start the 50k paper pilot during first deployment. Review readiness and
quality-gate reports first.

## 5. Update Flow

Create a database backup before updating:

```bash
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
export BACKUP_DIR=./backups
bash scripts/postgres_backup.sh
```

Update code and run checks:

```bash
git pull --ff-only
python scripts/release_preflight.py
```

Rebuild and restart:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
docker compose -f docker-compose.prod.yml --env-file .env.production ps
curl -s http://127.0.0.1:8000/api/health
```

Run the pre-deploy quality gate before any pilot launch action:

```bash
curl -s -X POST "http://127.0.0.1:8000/api/pre-deploy/paper-pilot/quality-gate" \
  -H "Content-Type: application/json" \
  -d '{
    "model_run_id": 1,
    "return_method": "risk_adjusted",
    "horizon_days": 30,
    "date_from": "2025-01-10",
    "date_to": "2025-03-14"
  }'
```

## 6. Rollback Flow

If an update fails:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production down
git checkout <previous-known-good-revision>
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
curl -s http://127.0.0.1:8000/api/health
```

Restore the database only when required and only from a verified backup:

```bash
export RESTORE_CONFIRM=yes
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
bash scripts/postgres_restore.sh ./backups/<backup-file>.dump
```

## 7. Logs and Diagnostics

Stack status:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

Logs:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs --tail=200
docker compose -f docker-compose.prod.yml --env-file .env.production logs backend --tail=200
docker compose -f docker-compose.prod.yml --env-file .env.production logs frontend --tail=200
docker compose -f docker-compose.prod.yml --env-file .env.production logs postgres --tail=200
```

Health and readiness:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:8000/api/data-readiness/corporate-universe/action-plan
curl -s http://127.0.0.1:8000/api/data-readiness/live
curl -s http://127.0.0.1:8000/api/data-readiness/live/action-plan
```

Pre-deploy quality gate:

```bash
curl -s -X POST "http://127.0.0.1:8000/api/pre-deploy/paper-pilot/quality-gate" \
  -H "Content-Type: application/json" \
  -d '{"model_run_id":1,"date_from":"2025-01-10","date_to":"2025-03-14"}'
```

## 8. Backups

Recommended policy:

- create a PostgreSQL backup before each deployment update;
- run daily PostgreSQL backups during the pilot;
- store backups outside the container volume;
- keep at least 7 daily backups and 4 weekly backups if disk space allows;
- periodically test restore on a non-production database.

In production compose, PostgreSQL is bound as:

```text
127.0.0.1:${POSTGRES_PORT:-5432}:5432
```

This binding is for host-level backup and restore scripts only. Do not change it
to a public bind address.

Backup:

```bash
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
export BACKUP_DIR=./backups
bash scripts/postgres_backup.sh
```

Restore:

```bash
export RESTORE_CONFIRM=yes
export POSTGRES_HOST=127.0.0.1
export POSTGRES_PORT=5432
export POSTGRES_DB=<database-name>
export POSTGRES_USER=<database-user>
export PGPASSWORD=<database-password>
bash scripts/postgres_restore.sh ./backups/<backup-file>.dump
```

## 9. Security Notes

- Never commit `.env.production`.
- Use a strong PostgreSQL password.
- Restrict SSH access to key-based login.
- Do not expose PostgreSQL publicly.
- Put HTTPS and a reverse proxy in front of public traffic before external use.
- Keep backups readable only by trusted server users.
- Rotate secrets after an accidental exposure.

## 10. Known Current Limitations

- This runbook prepares the server process; it does not prove live market data is
  ready.
- The 50k paper pilot should start only after the quality gate core gates pass.
- Backend tests and frontend build remain manual checks before deployment.
- The deployment runbook does not replace operational review of data freshness,
  model validation, strategy robustness, and paper readiness.
