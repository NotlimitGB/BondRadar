# BondRadar Private VDS Security Baseline

This is the security baseline for the first private single-operator VDS
deployment. It is not a replacement for application authentication or RBAC. It
is acceptable only for controlled private operation.

BondRadar remains:

- virtual paper only;
- no broker actions;
- no real-money flow;
- not public multi-user access.

## 1. Private-by-default Access Model

Only SSH should be publicly reachable by default. The frontend, backend API,
and PostgreSQL should be reachable on the VDS host through localhost bindings
only.

The operator access path is:

```text
local browser -> SSH tunnel -> VDS localhost ports
```

Open a tunnel from the operator machine:

```bash
ssh -L 5173:127.0.0.1:5173 -L 8000:127.0.0.1:8000 bondradar@<SERVER_IP>
```

Then open the frontend locally:

```text
http://127.0.0.1:5173
```

Health checks through the tunnel:

```bash
curl -s http://127.0.0.1:8000/api/health
curl -s http://127.0.0.1:5173/api/health
```

## 2. Firewall Baseline

Default UFW setup:

```bash
sudo ufw allow OpenSSH
sudo ufw enable
sudo ufw status
```

Do not open application ports for the first private deployment:

```bash
sudo ufw allow 5173/tcp
sudo ufw allow 8000/tcp
```

Those commands are not recommended for the first private VDS deployment. Only
consider public app exposure after a deliberate auth, HTTPS, reverse-proxy, and
operator-surface review.

## 3. Provider Firewall Checklist

- Allow SSH only by default.
- Do not expose PostgreSQL.
- Do not expose the backend API directly.
- Do not expose the frontend publicly unless auth, HTTPS, and reverse-proxy
  decisions have been made.
- Enable provider snapshots or backups if available.
- Keep server screenshots and support tickets free of secrets.

## 4. Operator UI Exposure Warning

The frontend contains operator controls for virtual paper schedules, pilot
bootstrap, and the external risk overlay. Do not expose this UI as a public
surface before auth/RBAC hardening.

For the private VDS baseline, the production compose file should bind host app
ports to `127.0.0.1`, and the browser should access them through SSH tunneling.

## 5. Local Exposure Check

Run the local release rehearsal before buying or configuring the VDS:

```bash
python scripts/local_release_rehearsal.py \
  --json-output ./logs/rehearsal/local_release_rehearsal.json \
  --markdown-output ./logs/rehearsal/local_release_rehearsal.md
```

The rehearsal includes the private exposure check and writes a top-level
operator report.

Run the local repository check before first deploy review:

```bash
python scripts/private_vds_exposure_check.py \
  --render-commands \
  --json-output ./logs/private_vds_exposure.json
```

The check does not call remote servers, does not start Docker, and does not run
containers. It inspects repository files and rendered command output for the
private-by-default posture.

## 6. Still Missing for Public or Team Use

Public or team operation requires a separate security hardening task. Review:

```text
docs/deployment/SECURITY_DEBT_REGISTER.md
```

CI passing and private exposure checks do not mean the app is safe for public
exposure.
