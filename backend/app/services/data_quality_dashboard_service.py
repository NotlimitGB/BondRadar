from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.financial_report import FinancialReport
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.data_quality_dashboard import (
    DataQualityBondRow,
    DataQualityBondRowsResponse,
    DataQualityCompanyRow,
    DataQualityCompanyRowsResponse,
    DataQualityCounts,
    DataQualityCoverage,
    DataQualityDateRange,
    DataQualityIssueSummary,
    DataQualityLabelBreakdown,
    DataQualityOverviewResponse,
    DataQualityReturnMethodBreakdown,
    DataQualitySourceBreakdown,
    DataQualityWarning,
)


RETURN_METHODS = ("price", "total_return", "risk_adjusted")
DEMO_WARNING = (
    "Demo data detection uses source fields where available and fallback naming "
    "heuristics elsewhere"
)


class DataQualityDashboardService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def overview(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        include_demo: bool = True,
    ) -> DataQualityOverviewResponse:
        self._validate_filters(date_from=date_from, date_to=date_to)
        context = self._context(
            date_from=date_from,
            date_to=date_to,
            include_demo=include_demo,
        )
        counts = self._counts(context)
        return DataQualityOverviewResponse(
            date_from=date_from,
            date_to=date_to,
            include_demo=include_demo,
            counts=counts,
            coverage=self._coverage(counts),
            date_ranges=self._date_ranges(context),
            source_breakdowns=self._source_breakdowns(context),
            label_breakdowns=self._label_breakdowns(context["labels"]),
            return_method_breakdowns=self._return_method_breakdowns(context["labels"]),
            issue_summary=self._issue_summary(context),
            warnings=self._warnings(),
        )

    def bonds(
        self,
        *,
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
    ) -> DataQualityBondRowsResponse:
        self._validate_filters(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        context = self._context(
            date_from=date_from,
            date_to=date_to,
            include_demo=include_demo,
        )
        rows = [
            self._bond_row(bond, context)
            for bond in context["bonds"]
            if company_id is None or bond.company_id == company_id
        ]
        rows = [
            row
            for row in rows
            if self._matches_bool(has_secid, row.secid is not None)
            and self._matches_bool(has_market_snapshots, row.market_snapshot_count > 0)
            and self._matches_bool(has_cashflows, row.cashflow_count > 0)
            and self._matches_bool(has_features, row.feature_count > 0)
            and self._matches_bool(
                has_labels,
                (
                    row.price_label_count
                    + row.total_return_label_count
                    + row.risk_adjusted_label_count
                )
                > 0,
            )
            and self._matches_bool(has_risk_assessment, row.risk_assessment_count > 0)
            and self._matches_source_for_bond(row.bond_id, source, context)
        ]
        rows = sorted(rows, key=lambda item: (-len(item.issue_flags), item.bond_id))
        return DataQualityBondRowsResponse(
            total=len(rows),
            limit=limit,
            offset=offset,
            items=rows[offset : offset + limit],
            warnings=self._warnings(),
        )

    def companies(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        include_demo: bool = True,
        limit: int = 100,
        offset: int = 0,
        has_financial_reports: bool | None = None,
        has_credit_health: bool | None = None,
        has_bonds: bool | None = None,
        source: str | None = None,
    ) -> DataQualityCompanyRowsResponse:
        self._validate_filters(
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
        )
        context = self._context(
            date_from=date_from,
            date_to=date_to,
            include_demo=include_demo,
        )
        rows = [self._company_row(company, context) for company in context["companies"]]
        rows = [
            row
            for row in rows
            if self._matches_bool(has_financial_reports, row.financial_report_count > 0)
            and self._matches_bool(has_credit_health, row.credit_health_count > 0)
            and self._matches_bool(has_bonds, row.bond_count > 0)
            and self._matches_source_for_company(row.company_id, source, context)
        ]
        rows = sorted(rows, key=lambda item: (-len(item.issue_flags), item.company_id))
        return DataQualityCompanyRowsResponse(
            total=len(rows),
            limit=limit,
            offset=offset,
            items=rows[offset : offset + limit],
            warnings=self._warnings(),
        )

    def _context(
        self,
        *,
        date_from: date | None,
        date_to: date | None,
        include_demo: bool,
    ) -> dict[str, Any]:
        all_companies = list(
            self.db.execute(select(Company).order_by(Company.id.asc())).scalars()
        )
        all_bonds = list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars())
        demo_company_ids = {
            company.id for company in all_companies if self._company_is_demo(company)
        }
        demo_bond_ids = {
            bond.id
            for bond in all_bonds
            if self._bond_is_demo(bond) or bond.company_id in demo_company_ids
        }
        company_ids = {
            company.id
            for company in all_companies
            if include_demo or company.id not in demo_company_ids
        }
        bonds = [
            bond
            for bond in all_bonds
            if bond.company_id in company_ids
            and (include_demo or bond.id not in demo_bond_ids)
        ]
        bond_ids = {bond.id for bond in bonds}
        companies = [company for company in all_companies if company.id in company_ids]
        if not include_demo:
            companies = [
                company
                for company in companies
                if company.id in {bond.company_id for bond in bonds}
                or company.id not in demo_company_ids
            ]

        market = [
            row
            for row in self.db.execute(select(BondMarketSnapshot)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.trade_date, date_from, date_to)
            and (include_demo or self._source(row.source) != "demo")
        ]
        cashflows = [
            row
            for row in self.db.execute(select(BondCashflowEvent)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.event_date, date_from, date_to)
            and (include_demo or self._source(row.source) != "demo")
        ]
        reports = [
            row
            for row in self.db.execute(select(FinancialReport)).scalars()
            if row.company_id in company_ids
            and self._in_date_range(self._report_date(row), date_from, date_to)
            and (include_demo or self._source(row.source) != "demo")
        ]
        health = [
            row
            for row in self.db.execute(select(CompanyCreditHealthSnapshot)).scalars()
            if row.company_id in company_ids
            and self._in_date_range(row.as_of_date, date_from, date_to)
        ]
        risk = [
            row
            for row in self.db.execute(select(BondRiskAssessment)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.as_of_date, date_from, date_to)
        ]
        features = [
            row
            for row in self.db.execute(select(BondFeatureSnapshot)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.as_of_date, date_from, date_to)
        ]
        labels = [
            row
            for row in self.db.execute(select(BondReturnLabel)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.as_of_date, date_from, date_to)
        ]
        predictions = [
            row
            for row in self.db.execute(select(MLPrediction)).scalars()
            if row.bond_id in bond_ids
            and self._in_date_range(row.as_of_date, date_from, date_to)
        ]
        model_runs = [
            row
            for row in self.db.execute(select(MLModelRun)).scalars()
            if self._in_date_range(self._as_date(row.created_at), date_from, date_to)
        ]

        return {
            "companies": companies,
            "bonds": bonds,
            "company_by_id": {company.id: company for company in companies},
            "bond_by_id": {bond.id: bond for bond in bonds},
            "demo_company_ids": demo_company_ids,
            "demo_bond_ids": demo_bond_ids,
            "market": market,
            "cashflows": cashflows,
            "reports": reports,
            "health": health,
            "risk": risk,
            "features": features,
            "labels": labels,
            "predictions": predictions,
            "model_runs": model_runs,
        }

    def _counts(self, context: dict[str, Any]) -> DataQualityCounts:
        bonds = context["bonds"]
        companies = context["companies"]
        company_ids_with_bonds = {bond.company_id for bond in bonds}
        labels = context["labels"]
        return DataQualityCounts(
            companies_total=len(companies),
            companies_with_bonds=len(company_ids_with_bonds),
            companies_with_financial_reports=len(
                {row.company_id for row in context["reports"]}
            ),
            companies_with_credit_health=len({row.company_id for row in context["health"]}),
            bonds_total=len(bonds),
            bonds_with_secid=sum(1 for bond in bonds if self._has_text(bond.secid)),
            bonds_with_isin=sum(1 for bond in bonds if self._has_text(bond.isin)),
            bonds_with_market_snapshots=len({row.bond_id for row in context["market"]}),
            bonds_with_cashflows=len({row.bond_id for row in context["cashflows"]}),
            bonds_with_features=len({row.bond_id for row in context["features"]}),
            bonds_with_price_labels=len(
                {row.bond_id for row in labels if row.return_method == "price"}
            ),
            bonds_with_total_return_labels=len(
                {row.bond_id for row in labels if row.return_method == "total_return"}
            ),
            bonds_with_risk_adjusted_labels=len(
                {row.bond_id for row in labels if row.return_method == "risk_adjusted"}
            ),
            bonds_with_risk_assessment=len({row.bond_id for row in context["risk"]}),
            market_snapshots_total=len(context["market"]),
            cashflow_events_total=len(context["cashflows"]),
            financial_reports_total=len(context["reports"]),
            company_credit_health_total=len(context["health"]),
            bond_risk_assessments_total=len(context["risk"]),
            feature_snapshots_total=len(context["features"]),
            labels_total=len(labels),
            ml_model_runs_total=len(context["model_runs"]),
            ml_predictions_total=len(context["predictions"]),
        )

    def _coverage(self, counts: DataQualityCounts) -> DataQualityCoverage:
        return DataQualityCoverage(
            company_report_coverage=self._ratio(
                counts.companies_with_financial_reports,
                counts.companies_total,
            ),
            company_credit_health_coverage=self._ratio(
                counts.companies_with_credit_health,
                counts.companies_total,
            ),
            bond_secid_coverage=self._ratio(
                counts.bonds_with_secid,
                counts.bonds_total,
            ),
            bond_isin_coverage=self._ratio(counts.bonds_with_isin, counts.bonds_total),
            bond_market_snapshot_coverage=self._ratio(
                counts.bonds_with_market_snapshots,
                counts.bonds_total,
            ),
            bond_cashflow_coverage=self._ratio(
                counts.bonds_with_cashflows,
                counts.bonds_total,
            ),
            bond_feature_coverage=self._ratio(
                counts.bonds_with_features,
                counts.bonds_total,
            ),
            bond_price_label_coverage=self._ratio(
                counts.bonds_with_price_labels,
                counts.bonds_total,
            ),
            bond_total_return_label_coverage=self._ratio(
                counts.bonds_with_total_return_labels,
                counts.bonds_total,
            ),
            bond_risk_adjusted_label_coverage=self._ratio(
                counts.bonds_with_risk_adjusted_labels,
                counts.bonds_total,
            ),
            bond_risk_assessment_coverage=self._ratio(
                counts.bonds_with_risk_assessment,
                counts.bonds_total,
            ),
        )

    def _date_ranges(self, context: dict[str, Any]) -> dict[str, DataQualityDateRange]:
        return {
            "market_snapshots": self._range(
                [row.trade_date for row in context["market"]]
            ),
            "cashflows": self._range([row.event_date for row in context["cashflows"]]),
            "financial_reports_published_at": self._range(
                [self._report_date(row) for row in context["reports"]]
            ),
            "company_credit_health": self._range(
                [row.as_of_date for row in context["health"]]
            ),
            "bond_risk_assessments": self._range(
                [row.as_of_date for row in context["risk"]]
            ),
            "feature_snapshots": self._range(
                [row.as_of_date for row in context["features"]]
            ),
            "labels": self._range([row.as_of_date for row in context["labels"]]),
            "ml_predictions": self._range(
                [row.as_of_date for row in context["predictions"]]
            ),
        }

    def _source_breakdowns(
        self,
        context: dict[str, Any],
    ) -> dict[str, list[DataQualitySourceBreakdown]]:
        return {
            "market_snapshots_by_source": self._source_rows(context["market"]),
            "cashflows_by_source": self._source_rows(context["cashflows"]),
            "financial_reports_by_source": self._source_rows(context["reports"]),
        }

    def _label_breakdowns(
        self,
        labels: list[BondReturnLabel],
    ) -> list[DataQualityLabelBreakdown]:
        counts: dict[tuple[str, str, int], int] = defaultdict(int)
        for label in labels:
            counts[
                (
                    label.return_method or "unknown",
                    label.label or "unknown",
                    label.horizon_days,
                )
            ] += 1
        return [
            DataQualityLabelBreakdown(
                return_method=return_method,
                label=label,
                horizon_days=horizon_days,
                rows=rows,
            )
            for (return_method, label, horizon_days), rows in sorted(counts.items())
        ]

    def _return_method_breakdowns(
        self,
        labels: list[BondReturnLabel],
    ) -> list[DataQualityReturnMethodBreakdown]:
        rows = []
        for return_method in RETURN_METHODS:
            method_labels = [
                label for label in labels if label.return_method == return_method
            ]
            rows.append(
                DataQualityReturnMethodBreakdown(
                    return_method=return_method,
                    rows=len(method_labels),
                    bonds=len({label.bond_id for label in method_labels}),
                    positive_rows=sum(
                        label.label == "positive_return" for label in method_labels
                    ),
                    negative_rows=sum(
                        label.label == "negative_return" for label in method_labels
                    ),
                    insufficient_rows=sum(
                        label.label == "insufficient_data" for label in method_labels
                    ),
                    min_as_of_date=(
                        min(label.as_of_date for label in method_labels)
                        if method_labels
                        else None
                    ),
                    max_as_of_date=(
                        max(label.as_of_date for label in method_labels)
                        if method_labels
                        else None
                    ),
                )
            )
        return rows

    def _issue_summary(self, context: dict[str, Any]) -> DataQualityIssueSummary:
        bond_ids = {bond.id for bond in context["bonds"]}
        company_ids = {company.id for company in context["companies"]}
        market_bond_ids = {row.bond_id for row in context["market"]}
        cashflow_bond_ids = {row.bond_id for row in context["cashflows"]}
        feature_bond_ids = {row.bond_id for row in context["features"]}
        label_bond_ids = {row.bond_id for row in context["labels"]}
        risk_bond_ids = {row.bond_id for row in context["risk"]}
        report_company_ids = {row.company_id for row in context["reports"]}
        health_company_ids = {row.company_id for row in context["health"]}
        prediction_run_ids = {row.model_run_id for row in context["predictions"]}
        return DataQualityIssueSummary(
            bonds_missing_secid=sum(
                1 for bond in context["bonds"] if not self._has_text(bond.secid)
            ),
            bonds_missing_isin=sum(
                1 for bond in context["bonds"] if not self._has_text(bond.isin)
            ),
            bonds_without_market_snapshots=len(bond_ids - market_bond_ids),
            bonds_without_cashflows=len(bond_ids - cashflow_bond_ids),
            bonds_without_features=len(bond_ids - feature_bond_ids),
            bonds_without_any_labels=len(bond_ids - label_bond_ids),
            bonds_without_risk_assessment=len(bond_ids - risk_bond_ids),
            companies_without_financial_reports=len(company_ids - report_company_ids),
            companies_without_credit_health=len(company_ids - health_company_ids),
            companies_without_bonds=len(
                company_ids - {bond.company_id for bond in context["bonds"]}
            ),
            labels_with_insufficient_data=sum(
                label.label == "insufficient_data" for label in context["labels"]
            ),
            labels_without_label_binary=sum(
                label.label_binary is None for label in context["labels"]
            ),
            ml_runs_without_predictions=sum(
                run.id not in prediction_run_ids for run in context["model_runs"]
            ),
        )

    def _bond_row(self, bond: Bond, context: dict[str, Any]) -> DataQualityBondRow:
        company = context["company_by_id"].get(bond.company_id)
        market = [row for row in context["market"] if row.bond_id == bond.id]
        cashflows = [row for row in context["cashflows"] if row.bond_id == bond.id]
        features = [row for row in context["features"] if row.bond_id == bond.id]
        labels = [row for row in context["labels"] if row.bond_id == bond.id]
        risk = [row for row in context["risk"] if row.bond_id == bond.id]
        latest_market = max(market, key=lambda row: (row.trade_date, row.id), default=None)
        latest_risk = max(risk, key=lambda row: (row.as_of_date, row.id), default=None)
        issue_flags = self._bond_issue_flags(
            bond=bond,
            is_demo=bond.id in context["demo_bond_ids"],
            market=market,
            cashflows=cashflows,
            features=features,
            labels=labels,
            risk=risk,
        )
        return DataQualityBondRow(
            bond_id=bond.id,
            company_id=bond.company_id,
            company_name=None if company is None else company.name,
            name=bond.name,
            isin=bond.isin,
            secid=bond.secid,
            currency=bond.currency,
            is_demo=bond.id in context["demo_bond_ids"],
            market_snapshot_count=len(market),
            market_snapshot_min_date=self._min_date(row.trade_date for row in market),
            market_snapshot_max_date=self._max_date(row.trade_date for row in market),
            cashflow_count=len(cashflows),
            cashflow_min_date=self._min_date(row.event_date for row in cashflows),
            cashflow_max_date=self._max_date(row.event_date for row in cashflows),
            feature_count=len(features),
            feature_min_date=self._min_date(row.as_of_date for row in features),
            feature_max_date=self._max_date(row.as_of_date for row in features),
            price_label_count=sum(label.return_method == "price" for label in labels),
            total_return_label_count=sum(
                label.return_method == "total_return" for label in labels
            ),
            risk_adjusted_label_count=sum(
                label.return_method == "risk_adjusted" for label in labels
            ),
            label_min_date=self._min_date(label.as_of_date for label in labels),
            label_max_date=self._max_date(label.as_of_date for label in labels),
            risk_assessment_count=len(risk),
            risk_assessment_min_date=self._min_date(row.as_of_date for row in risk),
            risk_assessment_max_date=self._max_date(row.as_of_date for row in risk),
            latest_liquidity_score=(
                latest_market.liquidity_score
                if latest_market is not None
                else (latest_risk.liquidity_score if latest_risk is not None else None)
            ),
            latest_yield_to_maturity=(
                latest_market.yield_to_maturity
                if latest_market is not None
                else (latest_risk.yield_to_maturity if latest_risk is not None else None)
            ),
            latest_decision_status=(
                latest_risk.decision_status if latest_risk is not None else None
            ),
            latest_risk_level=latest_risk.risk_level if latest_risk is not None else None,
            issue_flags=issue_flags,
        )

    def _company_row(
        self,
        company: Company,
        context: dict[str, Any],
    ) -> DataQualityCompanyRow:
        bonds = [bond for bond in context["bonds"] if bond.company_id == company.id]
        bond_ids = {bond.id for bond in bonds}
        reports = [row for row in context["reports"] if row.company_id == company.id]
        health = [row for row in context["health"] if row.company_id == company.id]
        latest_health = max(health, key=lambda row: (row.as_of_date, row.id), default=None)
        market_bond_ids = {
            row.bond_id for row in context["market"] if row.bond_id in bond_ids
        }
        cashflow_bond_ids = {
            row.bond_id for row in context["cashflows"] if row.bond_id in bond_ids
        }
        feature_bond_ids = {
            row.bond_id for row in context["features"] if row.bond_id in bond_ids
        }
        label_bond_ids = {
            row.bond_id for row in context["labels"] if row.bond_id in bond_ids
        }
        risk_bond_ids = {
            row.bond_id for row in context["risk"] if row.bond_id in bond_ids
        }
        issue_flags = self._company_issue_flags(
            company=company,
            is_demo=company.id in context["demo_company_ids"],
            bonds=bonds,
            reports=reports,
            health=health,
            market_bond_ids=market_bond_ids,
            cashflow_bond_ids=cashflow_bond_ids,
            feature_bond_ids=feature_bond_ids,
            label_bond_ids=label_bond_ids,
            risk_bond_ids=risk_bond_ids,
        )
        return DataQualityCompanyRow(
            company_id=company.id,
            name=company.name,
            ticker=company.ticker,
            inn=company.inn,
            country=company.country,
            is_demo=company.id in context["demo_company_ids"],
            bond_count=len(bonds),
            bonds_with_market_snapshots=len(market_bond_ids),
            bonds_with_cashflows=len(cashflow_bond_ids),
            bonds_with_features=len(feature_bond_ids),
            bonds_with_labels=len(label_bond_ids),
            bonds_with_risk_assessment=len(risk_bond_ids),
            financial_report_count=len(reports),
            financial_report_min_period_year=(
                min(row.period_year for row in reports) if reports else None
            ),
            financial_report_max_period_year=(
                max(row.period_year for row in reports) if reports else None
            ),
            financial_report_latest_published_at=self._max_date(
                self._report_date(row) for row in reports
            ),
            credit_health_count=len(health),
            credit_health_min_date=self._min_date(row.as_of_date for row in health),
            credit_health_max_date=self._max_date(row.as_of_date for row in health),
            latest_credit_status=(
                latest_health.credit_status if latest_health is not None else None
            ),
            latest_credit_health_score=(
                latest_health.credit_health_score if latest_health is not None else None
            ),
            latest_data_quality_level=(
                latest_health.data_quality_level if latest_health is not None else None
            ),
            issue_flags=issue_flags,
        )

    @staticmethod
    def _bond_issue_flags(
        *,
        bond: Bond,
        is_demo: bool,
        market: list[BondMarketSnapshot],
        cashflows: list[BondCashflowEvent],
        features: list[BondFeatureSnapshot],
        labels: list[BondReturnLabel],
        risk: list[BondRiskAssessment],
    ) -> list[str]:
        flags = []
        if not DataQualityDashboardService._has_text(bond.secid):
            flags.append("missing_secid")
        if not DataQualityDashboardService._has_text(bond.isin):
            flags.append("missing_isin")
        if not market:
            flags.append("no_market_snapshots")
        if not cashflows:
            flags.append("no_cashflows")
        if not features:
            flags.append("no_features")
        if not labels:
            flags.append("no_labels")
        if not risk:
            flags.append("no_risk_assessment")
        if is_demo:
            flags.append("only_demo_data")
        return flags

    @staticmethod
    def _company_issue_flags(
        *,
        company: Company,
        is_demo: bool,
        bonds: list[Bond],
        reports: list[FinancialReport],
        health: list[CompanyCreditHealthSnapshot],
        market_bond_ids: set[int],
        cashflow_bond_ids: set[int],
        feature_bond_ids: set[int],
        label_bond_ids: set[int],
        risk_bond_ids: set[int],
    ) -> list[str]:
        flags = []
        bond_ids = {bond.id for bond in bonds}
        if not bonds:
            flags.append("no_bonds")
        if not reports:
            flags.append("no_financial_reports")
        if not health:
            flags.append("no_credit_health")
        if bond_ids and market_bond_ids != bond_ids:
            flags.append("bonds_missing_market_snapshots")
        if bond_ids and cashflow_bond_ids != bond_ids:
            flags.append("bonds_missing_cashflows")
        if bond_ids and feature_bond_ids != bond_ids:
            flags.append("bonds_missing_features")
        if bond_ids and label_bond_ids != bond_ids:
            flags.append("bonds_missing_labels")
        if bond_ids and risk_bond_ids != bond_ids:
            flags.append("bonds_missing_risk_assessment")
        if is_demo or DataQualityDashboardService._company_is_demo(company):
            flags.append("only_demo_data")
        return flags

    def _matches_source_for_bond(
        self,
        bond_id: int,
        source: str | None,
        context: dict[str, Any],
    ) -> bool:
        if source is None:
            return True
        normalized = self._source(source)
        if normalized == "demo" and bond_id in context["demo_bond_ids"]:
            return True
        company_id = context["bond_by_id"][bond_id].company_id
        return any(
            self._source(row.source) == normalized
            for row in context["market"]
            if row.bond_id == bond_id
        ) or any(
            self._source(row.source) == normalized
            for row in context["cashflows"]
            if row.bond_id == bond_id
        ) or any(
            self._source(row.source) == normalized
            for row in context["reports"]
            if row.company_id == company_id
        )

    def _matches_source_for_company(
        self,
        company_id: int,
        source: str | None,
        context: dict[str, Any],
    ) -> bool:
        if source is None:
            return True
        normalized = self._source(source)
        if normalized == "demo" and company_id in context["demo_company_ids"]:
            return True
        bond_ids = {
            bond.id for bond in context["bonds"] if bond.company_id == company_id
        }
        return any(
            self._source(row.source) == normalized
            for row in context["reports"]
            if row.company_id == company_id
        ) or any(
            self._source(row.source) == normalized
            for row in context["market"]
            if row.bond_id in bond_ids
        ) or any(
            self._source(row.source) == normalized
            for row in context["cashflows"]
            if row.bond_id in bond_ids
        )

    @staticmethod
    def _validate_filters(
        *,
        date_from: date | None,
        date_to: date | None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> None:
        if date_from is not None and date_to is not None and date_from > date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if limit is not None and (limit < 1 or limit > 500):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 500",
            )
        if offset is not None and offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )

    @staticmethod
    def _company_is_demo(company: Company) -> bool:
        return any(
            DataQualityDashboardService._demo_marker(value)
            for value in (company.ticker, company.name, company.notes)
        )

    @staticmethod
    def _bond_is_demo(bond: Bond) -> bool:
        return any(
            DataQualityDashboardService._demo_marker(value)
            for value in (bond.secid, bond.isin, bond.name, bond.risk_notes)
        )

    @staticmethod
    def _demo_marker(value: str | None) -> bool:
        if value is None:
            return False
        text = value.strip().lower()
        return text.startswith("demo_") or "demo" in text

    @staticmethod
    def _matches_bool(expected: bool | None, actual: bool) -> bool:
        return expected is None or expected is actual

    @staticmethod
    def _warnings() -> list[DataQualityWarning]:
        return [DataQualityWarning(message=DEMO_WARNING)]

    @staticmethod
    def _source_rows(rows: Iterable[Any]) -> list[DataQualitySourceBreakdown]:
        counts: dict[str, int] = defaultdict(int)
        for row in rows:
            counts[DataQualityDashboardService._source(getattr(row, "source", None))] += 1
        return [
            DataQualitySourceBreakdown(source=source, rows=count)
            for source, count in sorted(counts.items())
        ]

    @staticmethod
    def _source(value: Any) -> str:
        if value is None or str(value).strip() == "":
            return "unknown"
        return str(value).strip().lower()

    @staticmethod
    def _range(values: Iterable[date | None]) -> DataQualityDateRange:
        cleaned = [value for value in values if value is not None]
        return DataQualityDateRange(
            min_date=min(cleaned) if cleaned else None,
            max_date=max(cleaned) if cleaned else None,
            row_count=len(cleaned),
        )

    @classmethod
    def _min_date(cls, values: Iterable[date | None]) -> date | None:
        cleaned = [value for value in values if value is not None]
        return min(cleaned) if cleaned else None

    @classmethod
    def _max_date(cls, values: Iterable[date | None]) -> date | None:
        cleaned = [value for value in values if value is not None]
        return max(cleaned) if cleaned else None

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        return Decimal(numerator) / Decimal(denominator)

    @staticmethod
    def _has_text(value: str | None) -> bool:
        return value is not None and value.strip() != ""

    @staticmethod
    def _report_date(report: FinancialReport) -> date:
        return (
            DataQualityDashboardService._as_date(report.published_at)
            or DataQualityDashboardService._as_date(report.created_at)
            or date.min
        )

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
    def _in_date_range(
        value: date | None,
        date_from: date | None,
        date_to: date | None,
    ) -> bool:
        if value is None:
            return False
        if date_from is not None and value < date_from:
            return False
        if date_to is not None and value > date_to:
            return False
        return True
