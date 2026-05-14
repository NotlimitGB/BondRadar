from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import companies as companies_crud
from app.crud import financial_reports as reports_crud
from app.models.enums import AnalysisSignal
from app.schemas.financial_report import (
    FinancialReportCreate,
    FinancialReportRead,
    FinancialReportUpdate,
)


router = APIRouter()


def get_existing_company(company_id: int, db: Session) -> None:
    if companies_crud.get_company(db, company_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )


def get_company_report(
    company_id: int, report_id: int, db: Session
):
    get_existing_company(company_id, db)
    report = reports_crud.get_financial_report(db, report_id)
    if report is None or report.company_id != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Financial report not found.",
        )
    return report


@router.get("", response_model=list[FinancialReportRead])
def list_company_reports(
    company_id: int,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    period_year: int | None = Query(default=None, ge=1900, le=2100),
    signal: AnalysisSignal | None = None,
    db: Session = Depends(get_db),
) -> list[FinancialReportRead]:
    get_existing_company(company_id, db)
    return reports_crud.list_financial_reports(
        db,
        skip=skip,
        limit=limit,
        company_id=company_id,
        period_year=period_year,
        signal=signal,
    )


@router.post(
    "",
    response_model=FinancialReportRead,
    status_code=status.HTTP_201_CREATED,
)
def create_company_report(
    company_id: int,
    report_in: FinancialReportCreate,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    get_existing_company(company_id, db)
    try:
        return reports_crud.create_financial_report(
            db,
            company_id=company_id,
            report_in=report_in,
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Financial report for this company and period already exists.",
        ) from exc


@router.get("/{report_id}", response_model=FinancialReportRead)
def get_financial_report(
    company_id: int,
    report_id: int,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    return get_company_report(company_id, report_id, db)


@router.patch("/{report_id}", response_model=FinancialReportRead)
def update_financial_report(
    company_id: int,
    report_id: int,
    report_in: FinancialReportUpdate,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    report = get_company_report(company_id, report_id, db)
    try:
        return reports_crud.update_financial_report(db, report, report_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Financial report for this company and period already exists.",
        ) from exc


@router.delete("/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_financial_report(
    company_id: int,
    report_id: int,
    db: Session = Depends(get_db),
) -> Response:
    report = get_company_report(company_id, report_id, db)
    reports_crud.delete_financial_report(db, report)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
