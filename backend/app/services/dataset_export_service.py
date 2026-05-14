from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from io import StringIO
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, false, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.schemas.dataset_export import (
    DatasetExportResponse,
    DatasetExportRow,
    DatasetQualityBondCoverage,
    DatasetQualityCompanyCoverage,
    DatasetQualityLabelDistribution,
    DatasetQualityMissingFeature,
    DatasetQualityNumericFeatureStats,
    DatasetQualityReport,
)


ALLOWED_DATASET_LABELS = {
    "positive_return",
    "negative_return",
    "insufficient_data",
}

EXPORT_COLUMNS = list(DatasetExportRow.model_fields.keys())

MISSING_FEATURES = [
    "bond_score",
    "company_score",
    "yield_to_maturity",
    "duration_years",
    "liquidity_score",
    "volume",
    "spread_to_ofz",
    "net_debt_to_ebitda",
    "debt_to_equity",
    "interest_coverage",
    "cash_to_short_term_debt",
    "ocf_to_total_debt",
    "net_profit_margin",
    "days_to_maturity",
]

NUMERIC_FEATURES = [
    "bond_score",
    "company_score",
    "yield_to_maturity",
    "duration_years",
    "liquidity_score",
    "volume",
    "future_return",
    "missing_data_count",
]


@dataclass(frozen=True)
class DatasetExportFilters:
    bond_id: int | None = None
    company_id: int | None = None
    horizon_days: int = 30
    as_of_date_from: date | None = None
    as_of_date_to: date | None = None
    label: str | None = None
    include_insufficient: bool = True


class DatasetExportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def export_json(
        self,
        *,
        filters: DatasetExportFilters,
        limit: int = 100,
        offset: int = 0,
    ) -> DatasetExportResponse:
        self._validate_filters(filters)
        self._validate_pagination(limit=limit, offset=offset, max_limit=5000)
        total = self._count_rows(filters)
        rows = self._fetch_rows(filters, limit=limit, offset=offset)
        return DatasetExportResponse(
            total=total,
            limit=limit,
            offset=offset,
            rows=rows,
        )

    def export_csv(
        self,
        *,
        filters: DatasetExportFilters,
        limit: int = 1000,
        offset: int = 0,
    ) -> str:
        self._validate_filters(filters)
        self._validate_pagination(limit=limit, offset=offset, max_limit=10000)
        rows = self._fetch_rows(filters, limit=limit, offset=offset)
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=EXPORT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    column: self._csv_value(getattr(row, column))
                    for column in EXPORT_COLUMNS
                }
            )
        return output.getvalue()

    def quality_report(
        self,
        *,
        filters: DatasetExportFilters,
    ) -> DatasetQualityReport:
        self._validate_filters(filters)
        rows = self._fetch_rows(filters, limit=None, offset=0)
        total_rows = len(rows)
        empty_distribution = DatasetQualityLabelDistribution(
            positive_return=0,
            negative_return=0,
            insufficient_data=0,
            label_binary_1=0,
            label_binary_0=0,
            label_binary_null=0,
        )
        if total_rows == 0:
            return DatasetQualityReport(
                total_rows=0,
                horizon_days=filters.horizon_days,
                as_of_date_min=None,
                as_of_date_max=None,
                bond_count=0,
                company_count=0,
                label_distribution=empty_distribution,
                missing_features=[],
                numeric_feature_stats=[],
                coverage_by_bond=[],
                coverage_by_company=[],
            )

        label_distribution = self._label_distribution(rows)
        return DatasetQualityReport(
            total_rows=total_rows,
            horizon_days=filters.horizon_days,
            as_of_date_min=min(row.as_of_date for row in rows),
            as_of_date_max=max(row.as_of_date for row in rows),
            bond_count=len({row.bond_id for row in rows}),
            company_count=len({row.company_id for row in rows}),
            label_distribution=label_distribution,
            missing_features=self._missing_features(rows),
            numeric_feature_stats=self._numeric_feature_stats(rows),
            coverage_by_bond=self._coverage_by_bond(rows),
            coverage_by_company=self._coverage_by_company(rows),
        )

    def _count_rows(self, filters: DatasetExportFilters) -> int:
        stmt = (
            select(func.count())
            .select_from(BondFeatureSnapshot)
            .join(
                BondReturnLabel,
                and_(
                    BondFeatureSnapshot.bond_id == BondReturnLabel.bond_id,
                    BondFeatureSnapshot.as_of_date == BondReturnLabel.as_of_date,
                ),
            )
            .join(Bond, Bond.id == BondFeatureSnapshot.bond_id)
            .join(Company, Company.id == BondFeatureSnapshot.company_id)
            .where(*self._conditions(filters))
        )
        return int(self.db.execute(stmt).scalar_one())

    def _fetch_rows(
        self,
        filters: DatasetExportFilters,
        *,
        limit: int | None,
        offset: int,
    ) -> list[DatasetExportRow]:
        stmt = (
            select(BondFeatureSnapshot, BondReturnLabel, Bond, Company)
            .join(
                BondReturnLabel,
                and_(
                    BondFeatureSnapshot.bond_id == BondReturnLabel.bond_id,
                    BondFeatureSnapshot.as_of_date == BondReturnLabel.as_of_date,
                ),
            )
            .join(Bond, Bond.id == BondFeatureSnapshot.bond_id)
            .join(Company, Company.id == BondFeatureSnapshot.company_id)
            .where(*self._conditions(filters))
            .order_by(
                BondFeatureSnapshot.as_of_date.desc(),
                BondFeatureSnapshot.bond_id.asc(),
                BondReturnLabel.id.asc(),
            )
            .offset(offset)
        )
        if limit is not None:
            stmt = stmt.limit(limit)

        return [
            self._row(feature, label, bond, company)
            for feature, label, bond, company in self.db.execute(stmt).all()
        ]

    def _conditions(self, filters: DatasetExportFilters) -> list[Any]:
        conditions: list[Any] = [
            BondReturnLabel.horizon_days == filters.horizon_days,
        ]
        if filters.bond_id is not None:
            conditions.append(BondFeatureSnapshot.bond_id == filters.bond_id)
        if filters.company_id is not None:
            conditions.append(BondFeatureSnapshot.company_id == filters.company_id)
        if filters.as_of_date_from is not None:
            conditions.append(BondFeatureSnapshot.as_of_date >= filters.as_of_date_from)
        if filters.as_of_date_to is not None:
            conditions.append(BondFeatureSnapshot.as_of_date <= filters.as_of_date_to)
        if filters.label is not None:
            conditions.append(BondReturnLabel.label == filters.label)
            if (
                filters.label == "insufficient_data"
                and not filters.include_insufficient
            ):
                conditions.append(false())
        elif not filters.include_insufficient:
            conditions.append(BondReturnLabel.label != "insufficient_data")
        return conditions

    @staticmethod
    def _row(
        feature: BondFeatureSnapshot,
        label: BondReturnLabel,
        bond: Bond,
        company: Company,
    ) -> DatasetExportRow:
        return DatasetExportRow(
            bond_id=feature.bond_id,
            company_id=feature.company_id,
            as_of_date=feature.as_of_date,
            horizon_days=label.horizon_days,
            bond_name=bond.name,
            isin=bond.isin,
            secid=bond.secid,
            company_name=company.name,
            company_ticker=company.ticker,
            bond_score=feature.bond_score,
            company_score=feature.company_score,
            yield_to_maturity=feature.yield_to_maturity,
            duration_years=feature.duration_years,
            liquidity_score=feature.liquidity_score,
            volume=feature.volume,
            spread_to_ofz=feature.spread_to_ofz,
            net_debt_to_ebitda=feature.net_debt_to_ebitda,
            debt_to_equity=feature.debt_to_equity,
            interest_coverage=feature.interest_coverage,
            cash_to_short_term_debt=feature.cash_to_short_term_debt,
            ocf_to_total_debt=feature.ocf_to_total_debt,
            net_profit_margin=feature.net_profit_margin,
            days_to_maturity=feature.days_to_maturity,
            has_offer=feature.has_offer,
            has_amortization=feature.has_amortization,
            missing_data_count=feature.missing_data_count,
            future_return=label.future_return,
            benchmark_return=label.benchmark_return,
            excess_return=label.excess_return,
            label=label.label,
            label_binary=label.label_binary,
            market_snapshot_id=feature.market_snapshot_id,
            bond_score_id=feature.bond_score_id,
            company_score_id=feature.company_score_id,
            financial_report_id=feature.financial_report_id,
            label_id=label.id,
            feature_snapshot_id=feature.id,
            feature_created_at=feature.created_at,
            label_created_at=label.created_at,
        )

    @staticmethod
    def _validate_filters(filters: DatasetExportFilters) -> None:
        if filters.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if (
            filters.as_of_date_from is not None
            and filters.as_of_date_to is not None
            and filters.as_of_date_from > filters.as_of_date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if filters.label is not None and filters.label not in ALLOWED_DATASET_LABELS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid label",
            )

    @staticmethod
    def _validate_pagination(*, limit: int, offset: int, max_limit: int) -> None:
        if limit <= 0 or limit > max_limit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"limit must be between 1 and {max_limit}",
            )
        if offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )

    @staticmethod
    def _label_distribution(rows: list[DatasetExportRow]) -> DatasetQualityLabelDistribution:
        return DatasetQualityLabelDistribution(
            positive_return=sum(row.label == "positive_return" for row in rows),
            negative_return=sum(row.label == "negative_return" for row in rows),
            insufficient_data=sum(row.label == "insufficient_data" for row in rows),
            label_binary_1=sum(row.label_binary == 1 for row in rows),
            label_binary_0=sum(row.label_binary == 0 for row in rows),
            label_binary_null=sum(row.label_binary is None for row in rows),
        )

    @staticmethod
    def _missing_features(rows: list[DatasetExportRow]) -> list[DatasetQualityMissingFeature]:
        total = len(rows)
        return [
            DatasetQualityMissingFeature(
                feature=feature,
                missing_count=missing_count,
                missing_ratio=missing_count / total if total else 0.0,
            )
            for feature in MISSING_FEATURES
            for missing_count in [sum(getattr(row, feature) is None for row in rows)]
        ]

    @staticmethod
    def _numeric_feature_stats(
        rows: list[DatasetExportRow],
    ) -> list[DatasetQualityNumericFeatureStats]:
        stats: list[DatasetQualityNumericFeatureStats] = []
        total = len(rows)
        for feature in NUMERIC_FEATURES:
            values = [
                DatasetExportService._to_decimal(getattr(row, feature))
                for row in rows
                if getattr(row, feature) is not None
            ]
            missing_count = total - len(values)
            stats.append(
                DatasetQualityNumericFeatureStats(
                    feature=feature,
                    count=len(values),
                    missing_count=missing_count,
                    min=min(values) if values else None,
                    max=max(values) if values else None,
                    avg=sum(values) / Decimal(len(values)) if values else None,
                )
            )
        return stats

    @staticmethod
    def _coverage_by_bond(
        rows: list[DatasetExportRow],
    ) -> list[DatasetQualityBondCoverage]:
        grouped: dict[int, list[DatasetExportRow]] = {}
        for row in rows:
            grouped.setdefault(row.bond_id, []).append(row)

        coverage = []
        for bond_id, bond_rows in grouped.items():
            first = bond_rows[0]
            coverage.append(
                DatasetQualityBondCoverage(
                    bond_id=bond_id,
                    bond_name=first.bond_name,
                    secid=first.secid,
                    rows_count=len(bond_rows),
                    as_of_date_min=min(row.as_of_date for row in bond_rows),
                    as_of_date_max=max(row.as_of_date for row in bond_rows),
                    positive_return_count=sum(
                        row.label == "positive_return" for row in bond_rows
                    ),
                    negative_return_count=sum(
                        row.label == "negative_return" for row in bond_rows
                    ),
                    insufficient_data_count=sum(
                        row.label == "insufficient_data" for row in bond_rows
                    ),
                )
            )
        return sorted(coverage, key=lambda item: item.bond_id)

    @staticmethod
    def _coverage_by_company(
        rows: list[DatasetExportRow],
    ) -> list[DatasetQualityCompanyCoverage]:
        grouped: dict[int, list[DatasetExportRow]] = {}
        for row in rows:
            grouped.setdefault(row.company_id, []).append(row)

        coverage = []
        for company_id, company_rows in grouped.items():
            first = company_rows[0]
            coverage.append(
                DatasetQualityCompanyCoverage(
                    company_id=company_id,
                    company_name=first.company_name,
                    company_ticker=first.company_ticker,
                    rows_count=len(company_rows),
                    bond_count=len({row.bond_id for row in company_rows}),
                    positive_return_count=sum(
                        row.label == "positive_return" for row in company_rows
                    ),
                    negative_return_count=sum(
                        row.label == "negative_return" for row in company_rows
                    ),
                    insufficient_data_count=sum(
                        row.label == "insufficient_data" for row in company_rows
                    ),
                )
            )
        return sorted(coverage, key=lambda item: item.company_id)

    @staticmethod
    def _to_decimal(value: Any) -> Decimal:
        if isinstance(value, Decimal):
            return value
        return Decimal(str(value))

    @staticmethod
    def _csv_value(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        return str(value)
