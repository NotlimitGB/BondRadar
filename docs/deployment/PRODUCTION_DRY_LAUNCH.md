# BondRadar Production-like Dry Launch

This guide validates production-like wiring before a real VDS launch or paper
pilot launch. It does not sync market data, run ML training, create schedules,
or execute paper cycles.

## 1. Purpose

The dry launch smoke pack checks:

- Docker Compose production config;
- backend health;
- frontend static serving;
- nginx `/api` proxy to backend;
- live data readiness endpoint availability;
- corporate universe action-plan endpoint availability;
- live data action-plan endpoint availability;
- optional pre-deploy quality gate dry-run safety.

Smoke success means the stack wiring works. It does not mean the data chain,
model candidate, or 50k virtual paper pilot is ready.

## 2. Local Production-like Dry Launch

Prepare environment:

```bash
cp .env.production.example .env.production
# edit .env.production and replace placeholders
```

Build and start the production-like stack:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
```

Run smoke checks without quality gate:

```bash
python scripts/prod_smoke_check.py --skip-quality-gate
```

Run smoke checks with quality gate:

```bash
python scripts/prod_smoke_check.py \
  --model-run-id <MODEL_RUN_ID> \
  --date-from 2025-01-10 \
  --date-to 2025-03-14 \
  --json-output ./prod_smoke_report.json
```

The quality gate smoke call may return `blocked` or `warning`. That can still be
a smoke success when the endpoint responds with the expected shape and dry-run
payloads remain safe.

## 3. Expected Outcomes

Smoke passed means:

- backend API is reachable;
- frontend root page is reachable;
- frontend nginx `/api` proxy reaches backend;
- readiness/action-plan endpoints return expected JSON shape;
- optional quality gate returns dry-run-safe payload previews.

Smoke passed does not mean:

- live market data is ready;
- ML candidate quality is acceptable;
- paper pilot schedule should be created;
- VDS deployment is complete.

Readiness can still be `not_ready`. Quality gate can still be `blocked`.

## 4. Troubleshooting

Backend health fails:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production logs backend --tail=200
docker compose -f docker-compose.prod.yml --env-file .env.production logs postgres --tail=200
```

Frontend opens but `/api` proxy fails:

- inspect `frontend/nginx.conf`;
- confirm backend container is healthy;
- run `curl -s http://127.0.0.1:5173/api/health`.

Data readiness returns 500:

- inspect backend logs;
- confirm migrations completed;
- confirm `DATABASE_URL` points to `postgres:5432` inside Docker.

Quality gate returns 400:

- provide `--model-run-id`;
- provide a valid date range when using explicit dates;
- confirm the model run exists and is completed before interpreting business
  readiness.

PostgreSQL is not healthy:

- inspect `.env.production`;
- confirm `POSTGRES_DB`, `POSTGRES_USER`, and `POSTGRES_PASSWORD` are set;
- inspect postgres logs.

Docker Hub TLS timeout during build:

- retry after network recovers;
- check VDS DNS/network settings;
- avoid treating image pull failures as application code failures.

## 5. Cleanup

Stop the production-like stack:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production down
```

Do not remove volumes unless intentionally resetting the local or production-like
database.
