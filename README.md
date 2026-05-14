# BondRadar

Backend foundation for a bond and issuer analysis service.

The application stores informational analysis signals only:

- `interesting_for_analysis`
- `neutral`
- `elevated_risk`
- `insufficient_data`

It is not an investment advisor and does not expose buy/sell recommendations.

## Run locally with Docker

```bash
docker compose up --build
```

The API will be available at `http://localhost:8000`.
If local port `5432` is already occupied, set another host port for PostgreSQL:

```bash
POSTGRES_PORT=55432 docker compose up --build
```

You can also put `POSTGRES_PORT=55432` into `.env`.

Interactive docs:

```text
http://localhost:8000/docs
```

On startup the API container applies Alembic migrations and loads idempotent seed data.

## API

Current backend endpoints:

- `GET /api/health`
- `GET /api/companies`
- `POST /api/companies`
- `GET /api/companies/{company_id}`
- `PATCH /api/companies/{company_id}`
- `DELETE /api/companies/{company_id}`
- `GET /api/bonds`
- `POST /api/bonds`
- `GET /api/bonds/{bond_id}`
- `PATCH /api/bonds/{bond_id}`
- `DELETE /api/bonds/{bond_id}`
- `GET /api/companies/{company_id}/reports`
- `POST /api/companies/{company_id}/reports`
- `GET /api/companies/{company_id}/reports/{report_id}`
- `PATCH /api/companies/{company_id}/reports/{report_id}`
- `DELETE /api/companies/{company_id}/reports/{report_id}`

The database schema also contains `company_scores` and `bond_scores` tables for
historical informational signal snapshots. Public score endpoints are not part
of this first task.

Not included in this stage: frontend, ML, MOEX API, or CSV import.

## Backend commands

From the `backend` directory:

```bash
pip install -r requirements.txt
alembic upgrade head
python -m app.db.seed
uvicorn app.main:app --reload
```

For local execution set `DATABASE_URL` to a reachable PostgreSQL database, for example:

```text
postgresql+psycopg://bondradar:bondradar@localhost:5432/bondradar
```
