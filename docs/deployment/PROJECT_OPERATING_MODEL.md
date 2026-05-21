# BondRadar Project Operating Model

BondRadar is a personal research and administration system for corporate bond
monitoring, ML-assisted candidate selection, and virtual paper portfolio
observation.

## 1. What BondRadar Is

BondRadar helps collect market and issuer data, prepare feature snapshots, train
and compare ML model candidates, and observe a virtual paper portfolio over
time.

The system is built for controlled research workflow and operational review.

## 2. What BondRadar Is Not

BondRadar is not:

- a broker;
- a real-money trading system;
- investment advice;
- proof of profitability;
- an automatic real portfolio manager.

## 3. Main Flow

```text
MOEX data
  |
  v
Corporate bond universe
  |
  v
Market snapshots / cashflows
  |
  v
Financial reports
  |
  v
Company credit health
  |
  v
Bond risk assessment
  |
  v
Feature snapshots
  |
  v
Labels
  |
  v
ML training
  |
  v
Predictions
  |
  v
Candidate comparison / robustness
  |
  v
Live paper readiness
  |
  v
Virtual paper portfolio
  |
  v
Operations / positions / snapshots
  |
  v
Monitoring / frontend
```

## 3.1 Operator UI Map

- `/`: bond and company overview;
- `/live-paper`: virtual paper monitoring dashboard;
- `/live-paper/schedules`: schedules and safe run checks;
- `/live-paper/pilot-bootstrap`: pilot schedule preparation;
- `/live-paper/portfolios/:id`: portfolio details, positions, operations, and
  snapshots;
- `/risk/external-regime`: external risk overlay.

Auth/RBAC is a separate pre-public hardening task. The current operator UI is
prepared for controlled private operation, not public multi-user access. For the
first VDS observation period, use the private access baseline:

```text
docs/deployment/PRIVATE_VDS_SECURITY_BASELINE.md
docs/deployment/SECURITY_DEBT_REGISTER.md
scripts/private_vds_exposure_check.py
```

## 4. Virtual Operation Meaning

A virtual operation is a database record that simulates portfolio state changes.
It does not send an order anywhere. It does not reserve real cash. It does not
interact with a broker.

Neutral operation terms include:

- position opened;
- position increased;
- position reduced;
- position closed;
- rebalance;
- cash balance;
- portfolio snapshot.

## 5. 50k Virtual Paper Pilot

The planned pilot uses:

- initial virtual capital: 50000 RUB;
- duration: 60-90 days;
- corporate bonds as the main working universe;
- data refresh that can run more often than paper schedule execution;
- scheduled virtual paper cycles that record operations and snapshots;
- monitoring for warnings, errors, and data freshness.

The pilot is field observation for further research.

## 6. Different Cadences

Data refresh and paper execution have different cadences because market data
freshness is useful while portfolio state changes should remain controlled.

Bond workflows are not intraday trading workflows. Frequent paper execution can
create noisy virtual operations, so the operations runner separates:

- monitoring;
- data refresh;
- paper dry-run;
- confirmed virtual paper execution.

Live paper schedules use the tested prediction date by default. Current-date
execution should be enabled only when predictions are refreshed before the paper
execution window. Risk override is available only as an explicit paper-pilot
control with a recorded reason.

## 7. Evaluation Signals

Review pilot evidence cautiously:

- stable data pipeline;
- fresh predictions;
- completed cycles;
- controlled warning and error count;
- portfolio value trajectory;
- drawdown;
- position concentration;
- comparison with baseline where available.

Short field observation does not prove model quality.

## 8. External Risk Overlay

External risk is a manual operator overlay for macro, geopolitical, or market
stress context. It does not make the ML model understand news automatically.
There is no news scraping, NLP, or external geopolitical API in this layer.

Supported modes:

- `normal`: normal virtual paper operation may continue;
- `elevated`: confirmed paper execution requires manual review;
- `severe`: confirmed paper execution is blocked by safety checks by default.

Data refresh may continue during elevated or severe modes. Paper dry-run may
continue because it is non-mutating and useful for review.

Check the current mode:

```bash
curl -s http://127.0.0.1:8000/api/risk/external-regime
```

The current external risk regime can also be reviewed and updated in the
frontend at `/risk/external-regime`.

Set an elevated mode manually:

```bash
curl -s -X PUT http://127.0.0.1:8000/api/risk/external-regime \
  -H "Content-Type: application/json" \
  -d '{
    "mode": "elevated",
    "reason": "Manual operator caution before paper execution window.",
    "source": "manual"
  }'
```

## 9. Financial Report Import Workflow

Company financial reports are a first-class input for corporate bond analysis.
They feed this chain:

```text
financial_reports -> company_credit_health -> bond_risk_assessment -> bond_feature_snapshots -> ML model -> paper portfolio
```

Before daily paper pilot review, check coverage:

```bash
curl -s "http://127.0.0.1:8000/api/data-readiness/financial-reports/coverage?as_of_date=2026-05-19&active_only=true&stale_after_days=540"
```

File-based imports are documented in:

```text
docs/deployment/FINANCIAL_REPORT_IMPORT.md
```

Missing financial reports should remain explicit. Use empty or `null` values
for missing fields, not fake zeros. After import, rebuild credit health, bond
risk assessments, and feature snapshots before reviewing model or paper pilot
readiness.

## 10. Stop Conditions

Pause the pilot workflow when any of these persist:

- data pipeline repeatedly fails;
- model predictions are unavailable;
- quality gate becomes blocked;
- paper readiness becomes `not_ready`;
- external risk regime is `severe`;
- critical monitoring alerts appear;
- unexpected database growth appears;
- backup fails;
- operator is uncertain.

## 11. Human Responsibilities

The operator is responsible for:

- reviewing reports;
- checking alerts;
- setting the external risk overlay when outside context changes;
- keeping backups;
- pausing execution when unsure;
- treating model output as research evidence rather than financial advice.
