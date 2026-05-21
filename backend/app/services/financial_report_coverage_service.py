from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, time, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.company import Company
from app.models.financial_report import FinancialReport
from app.schemas.financial_report_coverage import (
    FinancialReportCoverageResponse,
    FinancialReportCoverageWarning,
    FinancialReportFeatureSnapshotCoverage,
)


IMPORTANT_FINANCIAL_REPORT_FIELDS = (
    "revenue",
    "ebitda",
    "net_debt",
    "total_debt",
    "cash",
    "equity",
    "short_term_debt",
    "operating_cash_flow",
    "net_profit",
    "interest_expense",
)

FINANCIAL_RATIO_FIELDS = (
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
    "cash_to_short_term_debt",
    "ocf_to_total_debt",
    "net_profit_margin",
)

CORE_FINANCIAL_RATIO_FIELDS = (
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
)

LOW_COVERAGE_THRESHOLD = Decimal("0.50")


class FinancialReportCoverageService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def coverage(
        self,
        *,
        as_of_date: date | None = None,
        active_only: bool = True,
        stale_after_days: int = 540,
    ) -> FinancialReportCoverageResponse:
        if stale_after_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="stale_after_days must be positive",
            )
        target_date = as_of_date or date.today()
        bonds = list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars())
        working_bonds = [
            bond for bond in bonds if not active_only or not self._is_ofz_bond(bond)
        ]
        working_bond_ids = [bond.id for bond in working_bonds]
        working_company_ids = sorted({bond.company_id for bond in working_bonds})
        if not active_only:
            all_company_ids = [
                company_id
                for company_id in self.db.execute(
                    select(Company.id).order_by(Company.id.asc())
                ).scalars()
            ]
            working_company_ids = all_company_ids

        latest_reports = self._latest_reports_by_company(
            working_company_ids,
            target_date,
        )
        feature_coverage = self._feature_snapshot_coverage(
            bond_ids=working_bond_ids,
            as_of_date=target_date,
        )

        report_reference_dates = [
            self._report_reference_date(report)
            for report in latest_reports.values()
            if self._report_reference_date(report) is not None
        ]
        stale_company_ids = {
            company_id
            for company_id, report in latest_reports.items()
            for report_date in [self._report_reference_date(report)]
            if report_date is not None
            and (target_date - report_date).days > stale_after_days
        }
        active_bonds_with_reports = sum(
            1 for bond in working_bonds if bond.company_id in latest_reports
        )
        missing_field_counts = self._missing_field_counts(latest_reports.values())
        warnings = self._warnings(
            company_count=len(working_company_ids),
            companies_with_reports=len(latest_reports),
            stale_report_company_count=len(stale_company_ids),
            feature_coverage=feature_coverage,
        )
        return FinancialReportCoverageResponse(
            status="warning" if warnings else "ready",
            as_of_date=target_date,
            active_only=active_only,
            stale_after_days=stale_after_days,
            company_count=len(working_company_ids),
            companies_with_financial_reports=len(latest_reports),
            companies_without_financial_reports=max(
                0,
                len(working_company_ids) - len(latest_reports),
            ),
            coverage_ratio=self._ratio(len(latest_reports), len(working_company_ids)),
            recent_report_company_count=len(latest_reports) - len(stale_company_ids),
            stale_report_company_count=len(stale_company_ids),
            active_bond_count=len(working_bonds),
            active_bonds_with_financial_reports=active_bonds_with_reports,
            active_bonds_without_financial_reports=max(
                0,
                len(working_bonds) - active_bonds_with_reports,
            ),
            active_bond_coverage_ratio=self._ratio(
                active_bonds_with_reports,
                len(working_bonds),
            ),
            latest_report_period_end_date=(
                max(report_reference_dates) if report_reference_dates else None
            ),
            oldest_latest_report_period_end_date=(
                min(report_reference_dates) if report_reference_dates else None
            ),
            missing_field_counts=missing_field_counts,
            feature_snapshot_coverage=feature_coverage,
            warnings=warnings,
        )

    def _latest_reports_by_company(
        self,
        company_ids: list[int],
        as_of_date: date,
    ) -> dict[int, FinancialReport]:
        if not company_ids:
            return {}
        end_of_day = datetime.combine(as_of_date, time.max, tzinfo=timezone.utc)
        rows = list(
            self.db.execute(
                select(FinancialReport)
                .where(
                    FinancialReport.company_id.in_(company_ids),
                    (
                        (FinancialReport.published_at <= end_of_day)
                        | FinancialReport.published_at.is_(None)
                    ),
                )
                .order_by(FinancialReport.company_id.asc(), FinancialReport.id.asc())
            ).scalars()
        )
        grouped: dict[int, list[FinancialReport]] = {}
        for report in rows:
            grouped.setdefault(report.company_id, []).append(report)

        latest: dict[int, FinancialReport] = {}
        for company_id, reports in grouped.items():
            latest[company_id] = sorted(
                reports,
                key=self._report_sort_key,
                reverse=True,
            )[0]
        return latest

    def _feature_snapshot_coverage(
        self,
        *,
        bond_ids: list[int],
        as_of_date: date,
    ) -> FinancialReportFeatureSnapshotCoverage:
        if not bond_ids:
            return FinancialReportFeatureSnapshotCoverage(
                feature_snapshot_count=0,
                feature_snapshots_with_financial_report_id=0,
                feature_snapshots_with_any_financial_ratio=0,
                feature_snapshots_with_core_ratios=0,
                feature_snapshot_financial_report_ratio=None,
                feature_snapshot_financial_ratio_ratio=None,
                average_missing_data_count=None,
                ratio_field_counts={field: 0 for field in FINANCIAL_RATIO_FIELDS},
            )
        snapshots = list(
            self.db.execute(
                select(BondFeatureSnapshot).where(
                    BondFeatureSnapshot.bond_id.in_(set(bond_ids)),
                    BondFeatureSnapshot.as_of_date <= as_of_date,
                )
            ).scalars()
        )
        total = len(snapshots)
        ratio_field_counts = {
            field: sum(getattr(snapshot, field) is not None for snapshot in snapshots)
            for field in FINANCIAL_RATIO_FIELDS
        }
        any_ratio_count = sum(
            any(getattr(snapshot, field) is not None for field in FINANCIAL_RATIO_FIELDS)
            for snapshot in snapshots
        )
        core_ratio_count = sum(
            all(getattr(snapshot, field) is not None for field in CORE_FINANCIAL_RATIO_FIELDS)
            for snapshot in snapshots
        )
        missing_counts = [
            snapshot.missing_data_count
            for snapshot in snapshots
            if snapshot.missing_data_count is not None
        ]
        average_missing = (
            Decimal(sum(missing_counts)) / Decimal(len(missing_counts))
            if missing_counts
            else None
        )
        with_report_id = sum(
            snapshot.financial_report_id is not None for snapshot in snapshots
        )
        return FinancialReportFeatureSnapshotCoverage(
            feature_snapshot_count=total,
            feature_snapshots_with_financial_report_id=with_report_id,
            feature_snapshots_with_any_financial_ratio=any_ratio_count,
            feature_snapshots_with_core_ratios=core_ratio_count,
            feature_snapshot_financial_report_ratio=self._ratio(with_report_id, total),
            feature_snapshot_financial_ratio_ratio=self._ratio(any_ratio_count, total),
            average_missing_data_count=average_missing,
            ratio_field_counts=ratio_field_counts,
        )

    @staticmethod
    def _missing_field_counts(reports: Any) -> dict[str, int]:
        counts = {field: 0 for field in IMPORTANT_FINANCIAL_REPORT_FIELDS}
        for report in reports:
            for field in IMPORTANT_FINANCIAL_REPORT_FIELDS:
                if getattr(report, field) is None:
                    counts[field] += 1
        return counts

    @staticmethod
    def _warnings(
        *,
        company_count: int,
        companies_with_reports: int,
        stale_report_company_count: int,
        feature_coverage: FinancialReportFeatureSnapshotCoverage,
    ) -> list[FinancialReportCoverageWarning]:
        warnings: list[FinancialReportCoverageWarning] = []
        coverage_ratio = FinancialReportCoverageService._ratio(
            companies_with_reports,
            company_count,
        )
        if company_count > 0 and companies_with_reports == 0:
            warnings.append(
                FinancialReportCoverageWarning(
                    code="financial_report_coverage_missing",
                    message="No financial reports cover the working company universe",
                    details={"company_count": company_count},
                )
            )
        elif (
            coverage_ratio is not None
            and coverage_ratio < LOW_COVERAGE_THRESHOLD
        ):
            warnings.append(
                FinancialReportCoverageWarning(
                    code="financial_report_coverage_low",
                    message="Financial report coverage is below the review limit",
                    details={
                        "coverage_ratio": coverage_ratio,
                        "configured_minimum_ratio": LOW_COVERAGE_THRESHOLD,
                    },
                )
            )
        if stale_report_company_count > 0:
            warnings.append(
                FinancialReportCoverageWarning(
                    code="financial_report_stale",
                    message="Some latest company financial reports are stale",
                    details={"stale_report_company_count": stale_report_company_count},
                )
            )
        ratio = feature_coverage.feature_snapshot_financial_ratio_ratio
        if (
            feature_coverage.feature_snapshot_count > 0
            and ratio is not None
            and ratio < LOW_COVERAGE_THRESHOLD
        ):
            warnings.append(
                FinancialReportCoverageWarning(
                    code="financial_ratio_coverage_low",
                    message="Financial ratio coverage in feature snapshots is low",
                    details={
                        "feature_snapshot_financial_ratio_ratio": ratio,
                        "configured_minimum_ratio": LOW_COVERAGE_THRESHOLD,
                    },
                )
            )
        return warnings

    @staticmethod
    def _report_sort_key(report: FinancialReport) -> tuple[Any, ...]:
        report_date = FinancialReportCoverageService._report_reference_date(report)
        published = FinancialReportCoverageService._as_date(report.published_at)
        created = FinancialReportCoverageService._as_date(report.created_at)
        return (
            report_date or date.min,
            report.period_year,
            FinancialReportCoverageService._period_priority(report.period_quarter),
            published or date.min,
            created or date.min,
            report.id,
        )

    @staticmethod
    def _report_reference_date(report: FinancialReport) -> date | None:
        return (
            report.period_end_date
            or FinancialReportCoverageService._as_date(report.published_at)
            or FinancialReportCoverageService._derived_period_end(
                report.period_year,
                report.period_quarter,
            )
        )

    @staticmethod
    def _derived_period_end(year: int | None, quarter: int | None) -> date | None:
        if year is None:
            return None
        month = {
            0: 12,
            1: 3,
            2: 6,
            3: 9,
            4: 12,
        }.get(quarter or 0, 12)
        return date(year, month, monthrange(year, month)[1])

    @staticmethod
    def _period_priority(quarter: int | None) -> int:
        if quarter == 0:
            return 5
        return int(quarter or 0)

    @staticmethod
    def _as_date(value: Any) -> date | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        return None

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        return Decimal(numerator) / Decimal(denominator)

    @staticmethod
    def _is_ofz_bond(bond: Bond) -> bool:
        fields = " ".join(
            value
            for value in [bond.name, bond.secid or "", bond.isin or ""]
            if value
        ).upper()
        isin = (bond.isin or "").upper()
        return (
            "РћР¤Р—" in fields
            or "OFZ" in fields
            or "FEDERAL LOAN BOND" in fields
            or isin.startswith("SU")
        )
