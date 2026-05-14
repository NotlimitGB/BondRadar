from fastapi import APIRouter

from app.api.v1.endpoints import (
    bonds,
    companies,
    credit_risk,
    datasets,
    financial_reports,
    health,
    imports,
    market_data,
    ml,
    scores,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(companies.router, prefix="/companies", tags=["companies"])
api_router.include_router(bonds.router, prefix="/bonds", tags=["bonds"])
api_router.include_router(scores.router, prefix="/scores", tags=["scores"])
api_router.include_router(
    credit_risk.router,
    prefix="/credit-risk",
    tags=["credit-risk"],
)
api_router.include_router(imports.router, prefix="/import", tags=["import"])
api_router.include_router(datasets.router, tags=["datasets"])
api_router.include_router(market_data.router, tags=["market-data"])
api_router.include_router(ml.router, prefix="/ml", tags=["ml"])
api_router.include_router(
    financial_reports.router,
    prefix="/companies/{company_id}/reports",
    tags=["company-reports"],
)
