# BondRadar

## Project Overview

BondRadar is an application for analyzing bonds and issuer companies.

The app helps evaluate:

- bond yield;
- duration;
- liquidity;
- issuer financial condition;
- company debt load;
- insufficient-data risk.

The current repository contains a FastAPI backend and a React frontend MVP.
ML and MOEX API integration are not included yet.

## Important Disclaimer

BondRadar is not an investment advisor.
The app does not provide individual investment recommendations.
Signals such as `interesting_for_analysis`, `neutral`, `increased_risk`,
`high_risk`, and `insufficient_data` are informational only.
Do not use the app as the only basis for investment decisions.

The backend also keeps the legacy `elevated_risk` signal for compatibility with
existing seed data and migrations.

## Tech Stack

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
- Tailwind
- Recharts

Infrastructure:

- Docker Compose

## Current Features

- Companies CRUD
- Bonds CRUD
- Financial reports for companies
- Company financial ratios
- Company scoring
- Bond scoring
- Batch recalculation for all bonds
- CSV import for bonds and financial reports
- Frontend Bonds dashboard
- Frontend bond and issuer detail pages
- JSON explanations for scoring results
- Alembic migrations up to `202605140005`
- Idempotent seed data
- Backend tests

## Scoring Signals

BondRadar stores and returns informational signals only:

- `interesting_for_analysis`: the object may be interesting for additional analysis;
- `neutral`: no strong informational signal;
- `increased_risk`: elevated risk factors require attention;
- `high_risk`: high risk based on available factors;
- `insufficient_data`: there is not enough data for a full assessment.

The legacy `elevated_risk` signal is still supported for compatibility with
existing data and migrations. The app must not return `buy`, `sell`, `hold`,
`strong_buy`, or `strong_sell` signals.

## API Endpoints

Interactive API docs are available after startup:

```text
http://localhost:8000/docs
```

Health:

- `GET /api/health`

Companies:

- `GET /api/companies`
- `GET /api/companies/{company_id}`
- `POST /api/companies`
- `PATCH /api/companies/{company_id}`
- `DELETE /api/companies/{company_id}`
- `POST /api/companies/{company_id}/calculate-score`

Bonds:

- `GET /api/bonds`
- `GET /api/bonds/{bond_id}`
- `POST /api/bonds`
- `PATCH /api/bonds/{bond_id}`
- `DELETE /api/bonds/{bond_id}`
- `POST /api/bonds/{bond_id}/calculate-score`
- `GET /api/bonds/{bond_id}/score`

Reports:

- `GET /api/companies/{company_id}/reports`
- `POST /api/companies/{company_id}/reports`
- `GET /api/companies/{company_id}/reports/{report_id}`
- `PATCH /api/companies/{company_id}/reports/{report_id}`
- `DELETE /api/companies/{company_id}/reports/{report_id}`

Scores:

- `POST /api/scores/recalculate-all`

Import:

- `POST /api/import/bonds-csv`
- `POST /api/import/reports-csv`

## Local Development

Backend setup:

Create and activate a virtual environment, then install dependencies:

```bash
cd backend
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

For local execution without Docker, set `DATABASE_URL` to a reachable
PostgreSQL database, for example:

```text
postgresql+psycopg://bondradar:bondradar@localhost:5432/bondradar
```

Useful local checks from the repository root:

```bash
python -m compileall backend/app
python -m pytest backend/tests -q
```

Frontend setup:

```bash
cd frontend
npm install
npm run dev
```

The frontend development server is available at:

```text
http://localhost:5173
```

Vite proxies `/api` requests to the backend. Start the backend first on
`http://localhost:8000`.

## Docker Run

Run the app with PostgreSQL:

```bash
docker compose up --build
```

The API and frontend will be available at:

```text
http://localhost:8000
http://localhost:5173
```

If local port `5432` is already occupied, set another host port for PostgreSQL:

```bash
POSTGRES_PORT=55432 docker compose up --build
```

On PowerShell:

```powershell
$env:POSTGRES_PORT = "55432"
docker compose up --build
```

On startup the backend container applies Alembic migrations and loads
idempotent seed data.

## Database Migrations

The current Alembic head is:

```text
202605140005
```

Docker startup runs migrations automatically. To run migrations manually from
the `backend` directory:

```bash
alembic upgrade head
```

To inspect migration history:

```bash
alembic history
alembic current
```

With Docker, check the applied migration version:

```bash
docker compose exec backend alembic current
```

Or query PostgreSQL directly:

```bash
docker compose exec postgres psql -U bondradar -d bondradar -c "select version_num from alembic_version;"
```

The expected version is:

```text
202605140005
```

To load seed data manually:

```bash
python -m app.db.seed
```

## Tests

Run tests from the repository root:

```bash
python -m pytest backend/tests -q
```

Run Python compile checks:

```bash
python -m compileall backend/app
```

Build the frontend:

```bash
cd frontend
npm run build
```

## Example API Calls

Health check:

```bash
curl http://localhost:8000/api/health
```

Calculate company score:

```bash
curl -X POST http://localhost:8000/api/companies/1/calculate-score
```

Calculate bond score:

```bash
curl -X POST http://localhost:8000/api/bonds/1/calculate-score
```

Get latest bond score:

```bash
curl http://localhost:8000/api/bonds/1/score
```

Recalculate all bond scores:

```bash
curl -X POST http://localhost:8000/api/scores/recalculate-all
```

Import bonds from CSV:

```bash
curl -X POST http://localhost:8000/api/import/bonds-csv \
  -F "file=@bonds.csv"
```

Import financial reports from CSV:

```bash
curl -X POST http://localhost:8000/api/import/reports-csv \
  -F "file=@reports.csv"
```

Example score responses include JSON explanations with positive factors,
negative factors, missing data, risk warnings, source data, and calculated
scores.

## Project Structure

```text
backend/
  alembic/                 Alembic configuration and migrations
  app/
    api/v1/endpoints/      FastAPI routers
    core/                  Settings
    crud/                  CRUD helpers
    db/                    Database session, base metadata, seed data
    models/                SQLAlchemy models
    schemas/               Pydantic schemas
    services/              Financial ratios and scoring services
  tests/                   pytest test suite
frontend/
  src/                     React app, API client, pages, components
  vite.config.ts           Vite dev server and /api proxy
docker-compose.yml         Backend and PostgreSQL services
```

## Roadmap

- CSV upload controls in the frontend
- MOEX API integration
- More complete bond market data
- More advanced scoring calibration
- Optional ML modules after the backend MVP is stable
