from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import companies as companies_crud
from app.models.enums import AnalysisSignal
from app.schemas.company_score import CompanyScoreCalculationRead
from app.schemas.company import CompanyCreate, CompanyRead, CompanyUpdate
from app.services.company_scoring import CompanyScoreService


router = APIRouter()


@router.get("", response_model=list[CompanyRead])
def list_companies(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    query: str | None = Query(default=None, min_length=1),
    signal: AnalysisSignal | None = None,
    db: Session = Depends(get_db),
) -> list[CompanyRead]:
    return companies_crud.list_companies(
        db,
        skip=skip,
        limit=limit,
        query=query,
        signal=signal,
    )


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
def create_company(
    company_in: CompanyCreate,
    db: Session = Depends(get_db),
) -> CompanyRead:
    try:
        return companies_crud.create_company(db, company_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Company with the same ticker or INN already exists.",
        ) from exc


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(company_id: int, db: Session = Depends(get_db)) -> CompanyRead:
    company = companies_crud.get_company(db, company_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    return company


@router.post(
    "/{company_id}/calculate-score",
    response_model=CompanyScoreCalculationRead,
)
def calculate_company_score(
    company_id: int,
    db: Session = Depends(get_db),
) -> CompanyScoreCalculationRead:
    return CompanyScoreService(db).calculate_for_company(company_id)


@router.patch("/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: int,
    company_in: CompanyUpdate,
    db: Session = Depends(get_db),
) -> CompanyRead:
    company = companies_crud.get_company(db, company_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    try:
        return companies_crud.update_company(db, company, company_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Company with the same ticker or INN already exists.",
        ) from exc


@router.delete("/{company_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_company(company_id: int, db: Session = Depends(get_db)) -> Response:
    company = companies_crud.get_company(db, company_id)
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    companies_crud.delete_company(db, company)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
