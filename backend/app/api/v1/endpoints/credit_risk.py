from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.credit_risk import (
    BondRiskAssessmentCalculateRequest,
    BondRiskAssessmentRead,
    CompanyCreditHealthCalculateRequest,
    CompanyCreditHealthRead,
    RecalculateCreditRiskResult,
)
from app.services.bond_risk_assessment_service import BondRiskAssessmentService
from app.services.company_credit_health_service import CompanyCreditHealthService


router = APIRouter()


@router.post("/companies/{company_id}/calculate", response_model=CompanyCreditHealthRead)
def calculate_company_credit_health(
    company_id: int,
    request: CompanyCreditHealthCalculateRequest | None = None,
    db: Session = Depends(get_db),
) -> CompanyCreditHealthRead:
    request = request or CompanyCreditHealthCalculateRequest()
    return CompanyCreditHealthService(db).calculate_for_company(
        company_id,
        as_of_date=request.as_of_date,
    )


@router.get("/companies/{company_id}/latest", response_model=CompanyCreditHealthRead)
def get_latest_company_credit_health(
    company_id: int,
    as_of_date: date | None = None,
    db: Session = Depends(get_db),
) -> CompanyCreditHealthRead:
    return CompanyCreditHealthService(db).get_latest(
        company_id,
        as_of_date=as_of_date,
    )


@router.post("/bonds/{bond_id}/assess", response_model=BondRiskAssessmentRead)
def assess_bond_risk(
    bond_id: int,
    request: BondRiskAssessmentCalculateRequest | None = None,
    db: Session = Depends(get_db),
) -> BondRiskAssessmentRead:
    request = request or BondRiskAssessmentCalculateRequest()
    return BondRiskAssessmentService(db).assess_bond(
        bond_id,
        as_of_date=request.as_of_date,
        recalculate_company_health=request.recalculate_company_health,
    )


@router.get("/bonds/{bond_id}/latest", response_model=BondRiskAssessmentRead)
def get_latest_bond_risk_assessment(
    bond_id: int,
    as_of_date: date | None = None,
    db: Session = Depends(get_db),
) -> BondRiskAssessmentRead:
    return BondRiskAssessmentService(db).get_latest(
        bond_id,
        as_of_date=as_of_date,
    )


@router.post("/recalculate-all", response_model=RecalculateCreditRiskResult)
def recalculate_all_credit_risk(
    db: Session = Depends(get_db),
) -> RecalculateCreditRiskResult:
    return BondRiskAssessmentService(db).recalculate_all()
