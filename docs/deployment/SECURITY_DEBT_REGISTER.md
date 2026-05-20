# BondRadar Security Debt Register

This register tracks security work that must be completed before public or team
operation. The first VDS deployment is allowed only as private single-operator
operation with network-level restrictions.

CI passing does not mean the app is safe for public exposure. The private VDS
baseline is a temporary containment posture, not an auth/RBAC substitute.

## P0 Before Public or Team Use

- Application authentication.
- RBAC roles, at minimum: reader, analyst, admin.
- Protection for mutation endpoints.
- Protection for schedule controls.
- Protection for import, pipeline, ML, and admin actions.
- Audit log for changing operations.
- HTTPS and reverse-proxy contract.
- Security scheme in OpenAPI.
- Public docs protection or disabled public docs.
- Rate limiting for heavy endpoints.

## P1 Hardening

- CSRF posture if cookie auth is used.
- API token rotation if token auth is used.
- Structured audit events.
- Dependency vulnerability scans.
- Container image scans.
- Secret rotation process.
- Backup encryption policy.

## P2 Maturity

- Security regression tests.
- Periodic access review.
- Incident runbook.
- Observability security alerts.

## Current Allowed Posture

The first 60-90 day observation period may run only as:

- private VDS;
- single operator;
- virtual paper only;
- no broker actions;
- no real-money flow;
- SSH tunnel access to frontend and API;
- PostgreSQL not exposed publicly.

Before changing this posture, complete the P0 items and update the deployment
runbook.
