# BondRadar - Codex project instructions

## Project

BondRadar is a web app for analyzing bonds and issuer companies.

Corporate bond issuers are the primary working universe. OFZ and other
government-bond issuers are excluded from, or explicitly de-prioritized in,
the current corporate issuer workflow.

The app must not provide direct investment recommendations like "buy" or
"sell". Use only informational signals:

- interesting_for_analysis
- neutral
- increased_risk
- high_risk
- insufficient_data

## Stack

Backend:

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Pydantic
- pytest

Frontend:

- React
- TypeScript
- Vite
- TanStack Query
- Tailwind CSS
- Recharts

Infrastructure:

- Docker Compose
- PostgreSQL

## Backend rules

- Do not put business logic in routers.
- Routers should only validate requests, call services, and return responses.
- Put calculations and business logic into services.
- Use Alembic migrations for database changes.
- Do not rewrite old migrations.
- New DB fields should be nullable when needed for backward compatibility.
- Do not create duplicate fields if existing fields already solve the task.
- Use existing FinancialReport period fields: period_year and period_quarter.
- period_quarter = 0 means FY and has higher priority than Q4.

## Financial scoring rules

- CompanyScore is a snapshot of one calculation.
- Each recalculation creates a new CompanyScore record.
- score should duplicate final_company_score for backward compatibility.
- signal must not contain investment recommendations.
- signal = insufficient_data if risk_level = insufficient_data, otherwise neutral.
- explanation must be JSON/dict, not a JSON string.

## Controlled workflow rules

- Do not mutate the DB without a separate explicit apply task and its required
  operator approval.
- Do not import controlled values before source-backed evidence, provenance,
  manual review, and readiness gates pass.
- Do not enable scoring, recommendations, ranking, trading, or paper trading
  before their separate explicit gates.
- Read-only and planning tasks must keep mutation and execution flags false.
- TaskXXX reports must have type-stable main and wrapper contracts, including
  blocked and failed/default paths.
- A completed gate may unlock only one explicitly named safe next task.
- Verify focused tests first, then relevant broad tests, then the full suite
  when application code changes.
- Run a VDS smoke when a task requires production-like artifact validation.

## Source trust model

BondRadar separates source discovery, intelligence, evidence, and decisions.

### Level A - primary evidence

Examples:

- issuer official website
- audited financial statements
- official disclosure center
- Moscow Exchange official instrument data
- recognized credit-rating agency publication

May become source-backed evidence after document, period, metric, unit, and
review validation.

### Level B - intelligence with primary-source link

Examples:

- Pulse post linking to an official disclosure
- analyst note linking to an issuer report
- event summary linking to an official announcement

May create:

- source candidate
- event candidate
- verification task
- risk hypothesis

Must not directly create controlled financial values.

### Level C - opinion or unverified intelligence

Examples:

- Pulse opinion without an official source
- valuation opinion
- recommendation
- rumor
- sentiment
- forum discussion

May create:

- sentiment signal
- risk hypothesis
- attention signal
- claim candidate

Must not create source-backed values, controlled imports, issuer scores,
recommendations, or trading actions.

## Pulse policy

T-Investments Pulse is an intelligence layer, not a primary financial evidence
layer.

Allowed Pulse uses:

- detect issuer or bond events
- discover links to official disclosures
- collect investor risk hypotheses
- classify sentiment
- identify frequently discussed risks
- create claims for later verification
- distinguish equity opinions from bond-credit analysis

Forbidden Pulse uses:

- directly populate controlled financial statement values
- treat an author estimate as an audited metric
- treat sentiment as credit quality
- automatically change issuer score
- automatically generate investment recommendations
- automatically trade
- treat a single coupon payment as proof of issuer stability

A Pulse claim becomes verified only through an accepted primary source.

## Skill invocation guidance

Use skills implicitly when their descriptions match.

Use explicit skill invocation when the task is safety-critical or when
deterministic behavior is required.

Examples:

```txt
$bondradar-request-lock
$bondradar-output-contract-auditor
$bondradar-safety-gate-auditor
$bondradar-pulse-intelligence
$bondradar-claim-provenance
```

For implementation tasks, use the built-in worker only after request-lock and
safety analysis.

For reviews, prefer narrow read-only BondRadar custom agents.

## Commands

Backend checks:

- python -m compileall backend/app
- pytest

Docker checks:

- docker compose config --quiet

Task and contract checks:

- run the task-focused test selection
- run the relevant sibling-task test selection
- run the full backend test suite when application code changes
- run the specified static or VDS smoke without unsafe approvals
- run git diff --check

## Git rules

- Do not push without explicit user request.
- Do not commit unless explicitly asked.
- Show changed files after completing a task.
- Show verification commands after completing a task.

## Prompting behavior

Before making large changes:

- inspect the current project structure;
- check existing models, schemas, routers, migrations, task modes, and tests;
- avoid duplicate architecture;
- lock the requested scope, outputs, safety invariants, and next-task readiness;
- explain the plan briefly before implementation.

## Library documentation

Use Context7 MCP when you need current documentation for FastAPI, SQLAlchemy,
Alembic, Pydantic, React, Vite, TanStack Query, Tailwind CSS, Recharts, pytest,
or Docker Compose.
