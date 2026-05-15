from fastapi import APIRouter

from app.api.v1.endpoints import (
    bonds,
    cashflows,
    companies,
    credit_risk,
    data_readiness,
    data_pipeline,
    datasets,
    financial_report_ingestion,
    financial_reports,
    health,
    imports,
    market_data,
    ml,
    ml_evaluation,
    portfolio_construction,
    scores,
    strategy_backtest,
)


api_router = APIRouter()
api_router.include_router(health.router, tags=["health"])
api_router.include_router(companies.router, prefix="/companies", tags=["companies"])
api_router.include_router(bonds.router, prefix="/bonds", tags=["bonds"])
api_router.include_router(cashflows.router, prefix="/cashflows", tags=["cashflows"])
api_router.include_router(scores.router, prefix="/scores", tags=["scores"])
api_router.include_router(
    credit_risk.router,
    prefix="/credit-risk",
    tags=["credit-risk"],
)
api_router.include_router(
    data_readiness.router,
    prefix="/data-readiness",
    tags=["data-readiness"],
)
api_router.include_router(data_pipeline.router, prefix="/pipeline", tags=["pipeline"])
api_router.include_router(imports.router, prefix="/import", tags=["import"])
api_router.include_router(datasets.router, tags=["datasets"])
api_router.include_router(market_data.router, tags=["market-data"])
api_router.include_router(ml.router, prefix="/ml", tags=["ml"])
api_router.include_router(
    ml_evaluation.router,
    prefix="/ml/evaluation",
    tags=["ml-evaluation"],
)
api_router.include_router(
    financial_report_ingestion.router,
    prefix="/financial-reports",
    tags=["financial-report-ingestion"],
)
api_router.include_router(
    financial_reports.router,
    prefix="/companies/{company_id}/reports",
    tags=["company-reports"],
)
api_router.include_router(
    strategy_backtest.router,
    prefix="/strategy/backtests",
    tags=["strategy-backtests"],
)
api_router.include_router(
    portfolio_construction.router,
    prefix="/strategy/portfolio",
    tags=["strategy-portfolio"],
)
