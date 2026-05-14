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

Interactive docs:

```text
http://localhost:8000/docs
```

On startup the API container applies Alembic migrations and loads idempotent seed data.

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
