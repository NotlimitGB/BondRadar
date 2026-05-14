# BondRadar — Codex project instructions

## Project

BondRadar is a web app for analyzing bonds and issuer companies.

The app must not provide direct investment recommendations like "buy" or "sell".
Use only informational signals:

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
- Routers should only validate request, call services, and return response.
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

## Commands

Backend checks:

- python -m compileall backend/app
- pytest

Docker checks:

- docker compose config --quiet

## Git rules

- Do not push without explicit user request.
- Do not commit unless explicitly asked.
- Show changed files after completing a task.
- Show verification commands after completing a task.

## Prompting behavior

Before making large changes:

- inspect the current project structure;
- check existing models, schemas, routers, and migrations;
- avoid duplicate architecture;
- explain the plan briefly before implementation.

## Library documentation

Use Context7 MCP when you need current documentation for FastAPI, SQLAlchemy, Alembic, Pydantic, React, Vite, TanStack Query, Tailwind CSS, Recharts, pytest, or Docker Compose.
