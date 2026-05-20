# BondRadar Local Release Rehearsal

This is the last local rehearsal before buying or configuring a private VDS.
It validates local code, build, static deployment posture, private exposure
posture, and release-candidate reporting in one controlled flow.

It does not deploy anything. It does not start containers. It does not prove
model quality, live data readiness, pilot readiness, public/team readiness, or
auth/RBAC readiness.

BondRadar remains:

- private single-operator operation for the first VDS observation period;
- virtual paper only;
- no broker actions;
- no real-money flow.

## How To Run

```bash
python scripts/local_release_rehearsal.py \
  --json-output ./logs/rehearsal/local_release_rehearsal.json \
  --markdown-output ./logs/rehearsal/local_release_rehearsal.md
```

The script uses already installed local dependencies. It does not install npm or
Python packages.

## Expected Artifacts

The default run writes child reports under `./logs/rehearsal`:

```text
./logs/rehearsal/private_vds_exposure.json
./logs/rehearsal/release_preflight.json
./logs/rehearsal/first_deploy_commands.json
./logs/rehearsal/first_deploy_commands.md
./logs/rehearsal/release_candidate_report.json
./logs/rehearsal/release_candidate_report.md
./logs/rehearsal/local_release_rehearsal.json
./logs/rehearsal/local_release_rehearsal.md
```

## What Passing Means

A passing rehearsal means:

- backend code compiled;
- backend tests passed;
- frontend production build passed;
- production compose config was structurally valid;
- private VDS exposure posture passed;
- release preflight passed;
- private first-deploy commands rendered;
- release candidate report generation worked.

## What Passing Does Not Mean

A passing rehearsal does not mean:

- public or team operation is safe;
- app auth/RBAC is complete;
- live data is ready;
- model quality is proven;
- the 50k virtual paper pilot is ready to launch;
- a real `.env.production` has been validated.

`.env.production.example` is used for compose shape validation only. The real
`.env.production` file must be validated on the private VDS environment before
starting services.

## Skips And Fail-fast

Use skip flags only when a check is intentionally unavailable in the current
shell:

```bash
python scripts/local_release_rehearsal.py \
  --skip-frontend-build \
  --json-output ./logs/rehearsal/local_release_rehearsal.json
```

Use fail-fast to stop after the first failing command while still writing the
rehearsal report:

```bash
python scripts/local_release_rehearsal.py \
  --fail-fast \
  --json-output ./logs/rehearsal/local_release_rehearsal.json
```

## When To Proceed To VDS Purchase

Proceed only when:

- local rehearsal passed or any warnings were reviewed;
- private exposure check passed;
- operator reviewed `docs/deployment/SECURITY_DEBT_REGISTER.md`;
- operator understands SSH tunnel access;
- provider choice and rollback expectations are clear.

Review this report together with:

```text
docs/deployment/RELEASE_CANDIDATE_GO_NO_GO.md
docs/deployment/PRIVATE_VDS_SECURITY_BASELINE.md
docs/deployment/FIRST_DEPLOY_CHECKLIST.md
```
