# BondRadar VDS Provisioning

This guide prepares a clean Ubuntu VDS for BondRadar. It is provider-neutral and
intended for a beginner operator who wants a safe first production-like launch.

This guide does not start the 50k virtual paper pilot and does not enable paper
execution automatically. BondRadar remains virtual paper only:

- no broker actions;
- no real-money flow;
- no automatic paper execution timers during first deploy.

## 1. Recommended VDS Baseline

Use a simple, stable Ubuntu server:

- Ubuntu 22.04 LTS or Ubuntu 24.04 LTS;
- 2 vCPU minimum;
- 4 GB RAM minimum, with 4-8 GB preferred;
- 40-80 GB SSD minimum;
- public IPv4;
- SSH access;
- provider snapshots or backups available.

For first paper observation, stability matters more than oversized hardware.

## 2. Provider Purchase Checklist

Before creating the server:

- choose an Ubuntu LTS image;
- choose a stable region close enough for normal administration;
- enable provider backups or snapshots if available;
- add an SSH key if the provider supports it;
- do not expose the database port publicly;
- record the server IP;
- do not paste secrets into screenshots or tickets.

## 3. First SSH Login

Password-based login, if the provider uses it:

```bash
ssh root@<SERVER_IP>
```

Key-based login:

```bash
ssh -i ~/.ssh/<key_name> root@<SERVER_IP>
```

Provider images vary. If first login is under a user other than `root`, adapt
the commands and use `sudo` where needed.

## 4. Create a Non-root Deploy User

Create the application operator:

```bash
adduser bondradar
usermod -aG sudo bondradar
```

Optionally copy SSH keys from root to the deploy user:

```bash
rsync --archive --chown=bondradar:bondradar ~/.ssh /home/bondradar
```

Then log in as the deploy user for project work:

```bash
su - bondradar
```

## 5. Basic Firewall

Example `ufw` setup:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 5173/tcp
sudo ufw allow 8000/tcp
sudo ufw enable
sudo ufw status
```

PostgreSQL must not be opened publicly. `docker-compose.prod.yml` binds
PostgreSQL to `127.0.0.1` only for host-level backup and restore scripts.

## 6. Install Docker and Compose Plugin

Use the Docker packages recommended for Ubuntu. A typical flow is:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker bondradar
```

Log out and back in after adding the deploy user to the `docker` group.

If the provider image differs from a standard Ubuntu LTS image, prefer the
official Docker installation guide for that environment.

## 7. Clone the Project

```bash
sudo mkdir -p /opt/BondRadar
sudo chown -R bondradar:bondradar /opt/BondRadar
git clone <REPO_URL> /opt/BondRadar
cd /opt/BondRadar
```

If permissions need correction later:

```bash
sudo chown -R bondradar:bondradar /opt/BondRadar
```

## 8. Prepare Production Env

```bash
cp .env.production.example .env.production
nano .env.production
```

Review these rules before saving:

- replace sample passwords;
- keep `POSTGRES_PASSWORD` and `PGPASSWORD` equal;
- keep `DATABASE_URL` host as `postgres`;
- keep `POSTGRES_HOST` as `127.0.0.1`;
- set `BACKEND_CORS_ORIGINS` for the frontend URL you will use;
- do not commit `.env.production`.

## 9. Validate Before Starting

```bash
mkdir -p logs backups
python3 scripts/validate_production_env.py --env-file .env.production --json-output ./logs/env_validation.json
python3 scripts/server_sanity_check.py --env-file .env.production --json-output ./logs/server_sanity.json
```

Both reports should be reviewed before starting containers.

## 10. First Start

```bash
docker compose -f docker-compose.prod.yml --env-file .env.production build
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
docker compose -f docker-compose.prod.yml --env-file .env.production ps
```

## 11. First Verification

Backend health:

```bash
curl -s http://127.0.0.1:8000/api/health
```

Production-like smoke check:

```bash
python3 scripts/prod_smoke_check.py --json-output ./logs/prod_smoke.json
```

Live data bootstrap plan:

```bash
python3 scripts/live_data_bootstrap.py --json-output ./logs/live_data_bootstrap_plan.json
```

Monitoring check:

```bash
python3 scripts/live_operations_runner.py --mode monitoring --json-output ./logs/live_ops_monitoring.json
```

Backup check:

```bash
set -a && . ./.env.production && set +a && bash scripts/postgres_backup.sh
ls -lah backups
```

## 12. Render Custom Commands

Generate a command pack for the exact server and repository:

```bash
python3 scripts/render_first_deploy_commands.py \
  --server-ip <SERVER_IP> \
  --repo-url <REPO_URL> \
  --markdown-output ./logs/first_deploy_commands.md \
  --json-output ./logs/first_deploy_commands.json
```

The render script prints commands only. It does not connect to the server, run
Docker, edit files, or start services.

## 13. Do Not Enable Paper Execution Yet

Do not enable cron or systemd paper execution until all of these are complete:

- release candidate review is complete;
- production smoke check passes;
- live data bootstrap plan is reviewed;
- pre-deploy quality gate core gates pass;
- pilot bootstrap dry-run is reviewed;
- database backup was created;
- operator understands the pause procedure.

Operations timers are documented in `docs/deployment/LIVE_OPERATIONS_RUNNER.md`
and `docs/deployment/RUNTIME_HARDENING.md`, but they should not be enabled
blindly during first deploy.
