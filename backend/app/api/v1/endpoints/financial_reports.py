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


@router.get("", response_model=list[FinancialReportRead])
def list_financial_reports(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    company_id: int | None = Query(default=None, ge=1),
    period_year: int | None = Query(default=None, ge=1900, le=2100),
    signal: AnalysisSignal | None = None,
    db: Session = Depends(get_db),
) -> list[FinancialReportRead]:
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
def create_financial_report(
    report_in: FinancialReportCreate,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    if companies_crud.get_company(db, report_in.company_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    try:
        return reports_crud.create_financial_report(db, report_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Financial report for this company and period already exists.",
        ) from exc


@router.get("/{report_id}", response_model=FinancialReportRead)
def get_financial_report(
    report_id: int,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    report = reports_crud.get_financial_report(db, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Financial report not found.",
        )
    return report


@router.patch("/{report_id}", response_model=FinancialReportRead)
def update_financial_report(
    report_id: int,
    report_in: FinancialReportUpdate,
    db: Session = Depends(get_db),
) -> FinancialReportRead:
    report = reports_crud.get_financial_report(db, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Financial report not found.",
        )
    if report_in.company_id is not None:
        if companies_crud.get_company(db, report_in.company_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found.",
            )
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
    report_id: int,
    db: Session = Depends(get_db),
) -> Response:
    report = reports_crud.get_financial_report(db, report_id)
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Financial report not found.",
        )
    reports_crud.delete_financial_report(db, report)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

