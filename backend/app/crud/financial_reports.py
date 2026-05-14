from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import AnalysisSignal
from app.models.financial_report import FinancialReport
from app.schemas.financial_report import (
    FinancialReportCreate,
    FinancialReportUpdate,
)


def get_financial_report(db: Session, report_id: int) -> FinancialReport | None:
    return db.get(FinancialReport, report_id)


def list_financial_reports(
    db: Session,
    *,
    skip: int = 0,
    limit: int = 100,
    company_id: int | None = None,
    period_year: int | None = None,
    signal: AnalysisSignal | None = None,
) -> list[FinancialReport]:
    stmt = select(FinancialReport)
    if company_id is not None:
        stmt = stmt.where(FinancialReport.company_id == company_id)
    if period_year is not None:
        stmt = stmt.where(FinancialReport.period_year == period_year)
    if signal:
        stmt = stmt.where(FinancialReport.signal == signal.value)
    stmt = (
        stmt.order_by(
            FinancialReport.period_year.desc(),
            FinancialReport.period_quarter.desc(),
        )
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(stmt).scalars())


def create_financial_report(
    db: Session, *, company_id: int, report_in: FinancialReportCreate
) -> FinancialReport:
    report = FinancialReport(company_id=company_id, **report_in.model_dump())
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def update_financial_report(
    db: Session, report: FinancialReport, report_in: FinancialReportUpdate
) -> FinancialReport:
    for field, value in report_in.model_dump(exclude_unset=True).items():
        setattr(report, field, value)
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def delete_financial_report(db: Session, report: FinancialReport) -> None:
    db.delete(report)
    db.commit()
