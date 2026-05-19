# BondRadar First Deploy Checklist

Use this checklist with `docs/deployment/VDS_PROVISIONING.md` and
`docs/deployment/VDS_RUNBOOK.md`.

## 1. Before VDS Purchase

- [ ] Release candidate docs reviewed.
- [ ] GitHub Actions checks are green or failures are understood.
- [ ] `.env.production` values are prepared outside git.
- [ ] Operator understands BondRadar is virtual paper only.
- [ ] Operator understands paper execution must not be enabled during first deploy.

## 2. During Server Purchase

- [ ] Ubuntu 22.04 LTS or 24.04 LTS selected.
- [ ] 2 vCPU minimum selected.
- [ ] 4 GB RAM minimum selected.
- [ ] 40-80 GB SSD minimum selected.
- [ ] Provider backups or snapshots enabled if available.
- [ ] SSH key configured if provider supports it.
- [ ] Server IP recorded.

## 3. First SSH Login

- [ ] SSH login works.
- [ ] Non-root `bondradar` user created.
- [ ] `bondradar` user added to `sudo`.
- [ ] SSH access for `bondradar` verified.

## 4. Docker Setup

- [ ] Docker Engine installed.
- [ ] Docker Compose plugin installed.
- [ ] `bondradar` user added to the `docker` group.
- [ ] User logged out and back in after group change.
- [ ] `docker compose version` works.

## 5. Repository Setup

- [ ] Repository cloned to `/opt/BondRadar`.
- [ ] Repository files owned by `bondradar`.
- [ ] `logs` directory exists.
- [ ] `backups` directory exists.

## 6. `.env.production` Setup

- [ ] `.env.production.example` copied to `.env.production`.
- [ ] Sample passwords replaced.
- [ ] `POSTGRES_PASSWORD` equals `PGPASSWORD`.
- [ ] `DATABASE_URL` uses `postgres:5432`.
- [ ] `POSTGRES_HOST=127.0.0.1`.
- [ ] `BACKEND_CORS_ORIGINS` reviewed.
- [ ] `.env.production` is not committed.

## 7. First Compose Start

- [ ] Production env validation passed.
- [ ] Server sanity check passed.
- [ ] Production compose build completed.
- [ ] Production compose stack started.
- [ ] `docker compose ... ps` shows expected services.

## 8. First Health Checks

- [ ] `GET /api/health` returns OK.
- [ ] Frontend root opens.
- [ ] Frontend `/api` proxy reaches backend health.
- [ ] Production smoke report saved.

## 9. Data and Model Bootstrap

- [ ] Live data bootstrap plan saved.
- [ ] Corporate universe action plan reviewed.
- [ ] Live data action plan reviewed.
- [ ] ML validation has not been run without explicit confirmation.

## 10. Quality Gate

- [ ] Completed model run id is known, if available.
- [ ] Pre-deploy quality gate report saved, if model id is available.
- [ ] Quality gate blockers reviewed.

## 11. Backup Check

- [ ] `backups` directory exists.
- [ ] PostgreSQL backup script ran successfully.
- [ ] Backup file is visible under `./backups`.
- [ ] Restore procedure has been read.

## 12. Operations Runner Dry-run

- [ ] Monitoring mode report saved.
- [ ] Paper dry-run mode reviewed before any schedule execution.
- [ ] Cron/systemd examples reviewed but not blindly enabled.

## 13. What Not To Enable Yet

- [ ] Do not enable confirmed paper execution timers.
- [ ] Do not enable paper execution cron entries.
- [ ] Do not start the 50k virtual paper pilot during first deploy.
- [ ] Do not expose PostgreSQL publicly.

## Stop Before Pilot Launch

Do not start the 50k paper pilot until:

- quality gate core gates pass;
- pilot bootstrap dry-run is reviewed;
- backup was created;
- monitoring has no critical alerts;
- operator understands the pause procedure.
