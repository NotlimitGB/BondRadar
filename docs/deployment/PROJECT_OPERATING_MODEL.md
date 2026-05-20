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
prepared for controlled private operation, not public multi-user access.

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

## 9. Stop Conditions

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

## 10. Human Responsibilities

The operator is responsible for:

- reviewing reports;
- checking alerts;
- setting the external risk overlay when outside context changes;
- keeping backups;
- pausing execution when unsure;
- treating model output as research evidence rather than financial advice.
