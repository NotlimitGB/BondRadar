from fastapi import APIRouter

from app.api.v1.endpoints import bonds, companies, financial_reports, health


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(companies.router, prefix="/companies", tags=["companies"])
api_router.include_router(bonds.router, prefix="/bonds", tags=["bonds"])
api_router.include_router(
    financial_reports.router,
    prefix="/companies/{company_id}/reports",
    tags=["company-reports"],
)
