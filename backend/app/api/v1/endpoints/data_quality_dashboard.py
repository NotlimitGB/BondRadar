from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.data_quality_dashboard import (
    DataQualityBondRowsResponse,
    DataQualityCompanyRowsResponse,
    DataQualityOverviewResponse,
)
from app.services.data_quality_dashboard_service import DataQualityDashboardService


router = APIRouter()


@router.get("/overview", response_model=DataQualityOverviewResponse)
def get_data_quality_overview(
    date_from: date | None = None,
    date_to: date | None = None,
    include_demo: bool = True,
    db: Session = Depends(get_db),
) -> DataQualityOverviewResponse:
    return DataQualityDashboardService(db).overview(
        date_from=date_from,
        date_to=date_to,
        include_demo=include_demo,
    )


@router.get("/bonds", response_model=DataQualityBondRowsResponse)
def list_data_quality_bonds(
    date_from: date | None = None,
    date_to: date | None = None,
    include_demo: bool = True,
    limit: int = 100,
    offset: int = 0,
    company_id: int | None = None,
    has_secid: bool | None = None,
    has_market_snapshots: bool | None = None,
    has_cashflows: bool | None = None,
    has_features: bool | None = None,
    has_labels: bool | None = None,
    has_risk_assessment: bool | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
) -> DataQualityBondRowsResponse:
    return DataQualityDashboardService(db).bonds(
        date_from=date_from,
        date_to=date_to,
        include_demo=include_demo,
        limit=limit,
        offset=offset,
        company_id=company_id,
        has_secid=has_secid,
        has_market_snapshots=has_market_snapshots,
        has_cashflows=has_cashflows,
        has_features=has_features,
        has_labels=has_labels,
        has_risk_assessment=has_risk_assessment,
        source=source,
    )


@router.get("/companies", response_model=DataQualityCompanyRowsResponse)
def list_data_quality_companies(
    date_from: date | None = None,
    date_to: date | None = None,
    include_demo: bool = True,
    limit: int = 100,
    offset: int = 0,
    has_financial_reports: bool | None = None,
    has_credit_health: bool | None = None,
    has_bonds: bool | None = None,
    source: str | None = None,
    db: Session = Depends(get_db),
) -> DataQualityCompanyRowsResponse:
    return DataQualityDashboardService(db).companies(
        date_from=date_from,
        date_to=date_to,
        include_demo=include_demo,
        limit=limit,
        offset=offset,
        has_financial_reports=has_financial_reports,
        has_credit_health=has_credit_health,
        has_bonds=has_bonds,
        source=source,
    )
