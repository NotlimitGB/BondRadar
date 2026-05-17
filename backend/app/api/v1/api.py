from fastapi import APIRouter

from app.api.v1.endpoints import (
    bonds,
    cashflows,
    cashflow_quality,
    companies,
    credit_risk,
    data_quality_dashboard,
    data_readiness,
    data_pipeline,
    datasets,
    financial_report_ingestion,
    financial_reports,
    health,
    imports,
    label_quality,
    market_data,
    market_history_quality,
    ml,
    ml_evaluation,
    ml_prediction_quality,
    ml_walk_forward,
    portfolio_construction,
    paper_trading,
    scores,
    strategy_backtest,
    strategy_experiment,
    strategy_promotion,
    strategy_robustness,
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
api_router.include_router(
    data_quality_dashboard.router,
    prefix="/data-quality",
    tags=["data-quality"],
)
api_router.include_router(
    cashflow_quality.router,
    prefix="/data-quality/cashflows",
    tags=["data-quality-cashflows"],
)
api_router.include_router(
    market_history_quality.router,
    prefix="/data-quality/market-history",
    tags=["data-quality-market-history"],
)
api_router.include_router(
    label_quality.router,
    prefix="/data-quality/labels",
    tags=["data-quality-labels"],
)
api_router.include_router(data_pipeline.router, prefix="/pipeline", tags=["pipeline"])
api_router.include_router(imports.router, prefix="/import", tags=["import"])
api_router.include_router(datasets.router, tags=["datasets"])
api_router.include_router(market_data.router, tags=["market-data"])
api_router.include_router(ml.router, prefix="/ml", tags=["ml"])
api_router.include_router(
    ml_walk_forward.router,
    prefix="/ml/walk-forward",
    tags=["ml-walk-forward"],
)
api_router.include_router(
    ml_evaluation.router,
    prefix="/ml/evaluation",
    tags=["ml-evaluation"],
)
api_router.include_router(
    ml_prediction_quality.router,
    prefix="/ml/evaluation/prediction-quality",
    tags=["ml-prediction-quality"],
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
    strategy_experiment.router,
    prefix="/strategy/experiments",
    tags=["strategy-experiments"],
)
api_router.include_router(
    strategy_promotion.router,
    prefix="/strategy/promotions",
    tags=["strategy-promotions"],
)
api_router.include_router(
    strategy_robustness.router,
    prefix="/strategy/robustness",
    tags=["strategy-robustness"],
)
api_router.include_router(
    portfolio_construction.router,
    prefix="/strategy/portfolio",
    tags=["strategy-portfolio"],
)
api_router.include_router(
    paper_trading.router,
    prefix="/paper-trading",
    tags=["paper-trading"],
)
