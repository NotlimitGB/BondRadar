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

## 8. Stop Conditions

Pause the pilot workflow when any of these persist:

- data pipeline repeatedly fails;
- model predictions are unavailable;
- quality gate becomes blocked;
- paper readiness becomes `not_ready`;
- critical monitoring alerts appear;
- unexpected database growth appears;
- backup fails;
- operator is uncertain.

## 9. Human Responsibilities

The operator is responsible for:

- reviewing reports;
- checking alerts;
- keeping backups;
- pausing execution when unsure;
- treating model output as research evidence rather than financial advice.
