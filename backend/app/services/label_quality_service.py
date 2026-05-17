from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from math import ceil
from statistics import median
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_return_label import BondReturnLabel
from app.models.company import Company
from app.schemas.label_quality import (
    LabelQualityBondRow,
    LabelQualityCompanyRow,
    LabelQualityComponentSummary,
    LabelQualityIssueSummary,
    LabelQualityMethodSummary,
    LabelQualityOverview,
    LabelQualityReportRequest,
    LabelQualityReportResponse,
    LabelQualityReturnDistribution,
    LabelQualityWarning,
    LabelQualityWarningItem,
)


RETURN_METHODS = ("price", "risk_adjusted", "total_return")
EVALUABLE_LABELS = {"positive_return", "negative_return"}
STATUS_SEVERITY = {"ready": 0, "warning": 1, "not_ready": 2}


class LabelQualityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def report(self, request: LabelQualityReportRequest) -> LabelQualityReportResponse:
        return_methods = self._validate_request(request)
        bonds, warnings = self._resolve_bonds(request)
        companies = self._companies_for_bonds(bonds)
        labels = self._labels(
            bonds=bonds,
            request=request,
            return_methods=return_methods,
        )
        labels_by_bond: dict[int, list[BondReturnLabel]] = defaultdict(list)
        for label in labels:
            labels_by_bond[label.bond_id].append(label)

        single_bond_scope = len(bonds) == 1
        bond_rows = self._sort_bond_rows(
            [
                self._bond_row(
                    bond,
                    company=companies.get(bond.company_id),
                    labels=labels_by_bond.get(bond.id, []),
                    request=request,
                    single_bond_scope=single_bond_scope,
                )
                for bond in bonds
            ]
        )
        company_rows = self._sort_company_rows(
            self._company_rows(
                bonds=bonds,
                companies=companies,
                bond_rows=bond_rows,
            )
        )
        overview = self._overview(
            bonds=bonds,
            labels=labels,
            request=request,
        )
        issue_summary = self._issue_summary(
            bond_rows=bond_rows,
            labels=labels,
            overview=overview,
            request=request,
        )
        paginated_bond_rows = bond_rows[request.offset : request.offset + request.limit]
        paginated_company_rows = company_rows[
            request.offset : request.offset + request.limit
        ]
        if not request.include_bond_rows:
            paginated_bond_rows = []
        if not request.include_company_rows:
            paginated_company_rows = []

        return LabelQualityReportResponse(
            date_from=request.date_from,
            date_to=request.date_to,
            horizon_days=request.horizon_days,
            return_methods=return_methods,
            overview=overview,
            issue_summary=issue_summary,
            method_summaries=self._method_summaries(
                labels=labels,
                request=request,
            ),
            return_distribution=(
                self._return_distribution(labels)
                if request.include_return_distribution
                else None
            ),
            component_summary=(
                self._component_summary(labels)
                if request.include_component_summary
                else None
            ),
            warning_breakdown=(
                self._warning_breakdown(labels)
                if request.include_warning_breakdown
                else []
            ),
            total_bond_rows=len(bond_rows),
            bond_rows=paginated_bond_rows,
            total_company_rows=len(company_rows),
            company_rows=paginated_company_rows,
            limit=request.limit,
            offset=request.offset,
            warnings=warnings,
        )

    def _resolve_bonds(
        self,
        request: LabelQualityReportRequest,
    ) -> tuple[list[Bond], list[LabelQualityWarning]]:
        warnings: list[LabelQualityWarning] = []
        if request.bond_ids is not None:
            bond_ids, deduped = self._dedupe(request.bond_ids)
            if deduped:
                warnings.append(LabelQualityWarning(message="Duplicate selectors were ignored"))
            if not bond_ids:
                return [], warnings
            bonds = list(
                self.db.execute(select(Bond).where(Bond.id.in_(bond_ids))).scalars()
            )
            by_id = {bond.id: bond for bond in bonds}
            missing = [bond_id for bond_id in bond_ids if bond_id not in by_id]
            if missing:
                warnings.append(
                    LabelQualityWarning(
                        message="Some requested bond ids were not found",
                        details={"bond_ids": missing},
                    )
                )
            return [by_id[bond_id] for bond_id in bond_ids if bond_id in by_id], warnings

        if request.company_ids is not None:
            company_ids, deduped = self._dedupe(request.company_ids)
            if deduped:
                warnings.append(LabelQualityWarning(message="Duplicate selectors were ignored"))
            if not company_ids:
                return [], warnings
            companies = list(
                self.db.execute(
                    select(Company).where(Company.id.in_(company_ids))
                ).scalars()
            )
            found_company_ids = {company.id for company in companies}
            missing = [
                company_id
                for company_id in company_ids
                if company_id not in found_company_ids
            ]
            if missing:
                warnings.append(
                    LabelQualityWarning(
                        message="Some requested company ids were not found",
                        details={"company_ids": missing},
                    )
                )
            bonds = list(
                self.db.execute(
                    select(Bond)
                    .where(Bond.company_id.in_(found_company_ids))
                    .order_by(Bond.company_id.asc(), Bond.id.asc())
                ).scalars()
            )
            bonds_by_company: dict[int, list[Bond]] = defaultdict(list)
            for bond in bonds:
                bonds_by_company[bond.company_id].append(bond)
            selected: list[Bond] = []
            for company_id in company_ids:
                selected.extend(bonds_by_company.get(company_id, []))
            return selected, warnings

        if request.secids is not None:
            secids, deduped = self._dedupe_secids(request.secids)
            if deduped:
                warnings.append(LabelQualityWarning(message="Duplicate selectors were ignored"))
            if not secids:
                return [], warnings
            bonds = list(
                self.db.execute(select(Bond).where(Bond.secid.in_(secids))).scalars()
            )
            by_secid = {self._normalize_secid(bond.secid): bond for bond in bonds}
            missing = [secid for secid in secids if secid not in by_secid]
            if missing:
                warnings.append(
                    LabelQualityWarning(
                        message="Some requested secids were not found",
                        details={"secids": missing},
                    )
                )
            return [by_secid[secid] for secid in secids if secid in by_secid], warnings

        return list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars()), warnings

    def _companies_for_bonds(self, bonds: list[Bond]) -> dict[int, Company]:
        company_ids = {bond.company_id for bond in bonds}
        if not company_ids:
            return {}
        return {
            company.id: company
            for company in self.db.execute(
                select(Company).where(Company.id.in_(company_ids))
            ).scalars()
        }

    def _labels(
        self,
        *,
        bonds: list[Bond],
        request: LabelQualityReportRequest,
        return_methods: list[str],
    ) -> list[BondReturnLabel]:
        bond_ids = [bond.id for bond in bonds]
        if not bond_ids or not return_methods:
            return []
        stmt = select(BondReturnLabel).where(
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.as_of_date >= request.date_from,
            BondReturnLabel.as_of_date <= request.date_to,
            BondReturnLabel.return_method.in_(return_methods),
        )
        if request.horizon_days is not None:
            stmt = stmt.where(BondReturnLabel.horizon_days == request.horizon_days)
        stmt = stmt.order_by(
            BondReturnLabel.bond_id.asc(),
            BondReturnLabel.as_of_date.asc(),
            BondReturnLabel.return_method.asc(),
            BondReturnLabel.horizon_days.asc(),
            BondReturnLabel.id.asc(),
        )
        return list(self.db.execute(stmt).scalars())

    def _overview(
        self,
        *,
        bonds: list[Bond],
        labels: list[BondReturnLabel],
        request: LabelQualityReportRequest,
    ) -> LabelQualityOverview:
        label_row_count = len(labels)
        evaluable = [label for label in labels if self._is_evaluable(label)]
        positive_count = sum(label.label == "positive_return" for label in evaluable)
        negative_count = sum(label.label == "negative_return" for label in evaluable)
        insufficient_count = sum(label.label == "insufficient_data" for label in labels)
        extreme_count = self._extreme_return_count(labels, request)
        insufficient_ratio = self._ratio(insufficient_count, label_row_count)
        ready_for_ml_dataset = (
            len(evaluable) >= request.minimum_evaluable_rows
            and positive_count >= request.minimum_positive_rows
            and negative_count >= request.minimum_negative_rows
            and (
                insufficient_ratio is None
                or insufficient_ratio <= request.maximum_insufficient_ratio
            )
            and extreme_count == 0
        )
        return LabelQualityOverview(
            selected_bond_count=len(bonds),
            selected_company_count=len({bond.company_id for bond in bonds}),
            label_row_count=label_row_count,
            evaluable_label_count=len(evaluable),
            positive_label_count=positive_count,
            negative_label_count=negative_count,
            insufficient_label_count=insufficient_count,
            insufficient_ratio=insufficient_ratio,
            positive_ratio=self._ratio(positive_count, len(evaluable)),
            negative_ratio=self._ratio(negative_count, len(evaluable)),
            ready_for_ml_dataset=ready_for_ml_dataset,
            labels_with_start_snapshot_count=sum(
                label.start_market_snapshot_id is not None for label in labels
            ),
            labels_with_end_snapshot_count=sum(
                label.end_market_snapshot_id is not None for label in labels
            ),
            labels_with_warnings_count=sum(self._has_warnings(label) for label in labels),
            labels_with_details_count=sum(
                bool(label.return_calculation_details) for label in labels
            ),
            extreme_return_count=extreme_count,
            null_future_return_count=sum(label.future_return is None for label in labels),
        )

    def _method_summaries(
        self,
        *,
        labels: list[BondReturnLabel],
        request: LabelQualityReportRequest,
    ) -> list[LabelQualityMethodSummary]:
        grouped: dict[tuple[str, int], list[BondReturnLabel]] = defaultdict(list)
        for label in labels:
            grouped[(label.return_method, label.horizon_days)].append(label)
        summaries = [
            self._method_summary(
                return_method=return_method,
                horizon_days=horizon_days,
                labels=items,
                request=request,
            )
            for (return_method, horizon_days), items in grouped.items()
        ]
        return sorted(
            summaries,
            key=lambda item: (item.return_method, item.horizon_days is None, item.horizon_days or 0),
        )

    def _method_summary(
        self,
        *,
        return_method: str,
        horizon_days: int | None,
        labels: list[BondReturnLabel],
        request: LabelQualityReportRequest,
    ) -> LabelQualityMethodSummary:
        evaluable = [label for label in labels if self._is_evaluable(label)]
        positive_count = sum(label.label == "positive_return" for label in evaluable)
        negative_count = sum(label.label == "negative_return" for label in evaluable)
        insufficient_count = sum(label.label == "insufficient_data" for label in labels)
        returns = self._return_values(labels)
        return LabelQualityMethodSummary(
            return_method=return_method,
            horizon_days=horizon_days,
            label_row_count=len(labels),
            evaluable_label_count=len(evaluable),
            positive_label_count=positive_count,
            negative_label_count=negative_count,
            insufficient_label_count=insufficient_count,
            insufficient_ratio=self._ratio(insufficient_count, len(labels)),
            positive_ratio=self._ratio(positive_count, len(evaluable)),
            negative_ratio=self._ratio(negative_count, len(evaluable)),
            average_future_return=self._average(returns),
            median_future_return=self._median(returns),
            min_future_return=min(returns) if returns else None,
            max_future_return=max(returns) if returns else None,
            labels_with_warnings_count=sum(self._has_warnings(label) for label in labels),
            extreme_return_count=self._extreme_return_count(labels, request),
        )

    def _bond_row(
        self,
        bond: Bond,
        *,
        company: Company | None,
        labels: list[BondReturnLabel],
        request: LabelQualityReportRequest,
        single_bond_scope: bool,
    ) -> LabelQualityBondRow:
        evaluable = [label for label in labels if self._is_evaluable(label)]
        positive_count = sum(label.label == "positive_return" for label in evaluable)
        negative_count = sum(label.label == "negative_return" for label in evaluable)
        insufficient_count = sum(label.label == "insufficient_data" for label in labels)
        return_values = self._return_values(labels)
        labels_with_warnings_count = sum(self._has_warnings(label) for label in labels)
        extreme_return_count = self._extreme_return_count(labels, request)
        missing_start_snapshot_count = sum(
            label.start_market_snapshot_id is None for label in labels
        )
        missing_end_snapshot_count = sum(
            label.end_market_snapshot_id is None for label in labels
        )
        insufficient_ratio = self._ratio(insufficient_count, len(labels))
        issues = self._bond_issues(
            label_count=len(labels),
            evaluable_count=len(evaluable),
            positive_count=positive_count,
            negative_count=negative_count,
            insufficient_count=insufficient_count,
            insufficient_ratio=insufficient_ratio,
            labels_with_warnings_count=labels_with_warnings_count,
            extreme_return_count=extreme_return_count,
            missing_start_snapshot_count=missing_start_snapshot_count,
            missing_end_snapshot_count=missing_end_snapshot_count,
            request=request,
            single_bond_scope=single_bond_scope,
        )
        return LabelQualityBondRow(
            bond_id=bond.id,
            secid=bond.secid,
            isin=bond.isin,
            bond_name=bond.name,
            company_id=bond.company_id,
            company_name=company.name if company is not None else None,
            company_ticker=company.ticker if company is not None else None,
            status=self._bond_status(issues),
            issue_count=len(issues),
            issues=issues,
            label_row_count=len(labels),
            evaluable_label_count=len(evaluable),
            positive_label_count=positive_count,
            negative_label_count=negative_count,
            insufficient_label_count=insufficient_count,
            insufficient_ratio=insufficient_ratio,
            positive_ratio=self._ratio(positive_count, len(evaluable)),
            negative_ratio=self._ratio(negative_count, len(evaluable)),
            first_label_date=min((label.as_of_date for label in labels), default=None),
            last_label_date=max((label.as_of_date for label in labels), default=None),
            average_future_return=self._average(return_values),
            min_future_return=min(return_values) if return_values else None,
            max_future_return=max(return_values) if return_values else None,
            labels_with_warnings_count=labels_with_warnings_count,
            extreme_return_count=extreme_return_count,
            missing_start_snapshot_count=missing_start_snapshot_count,
            missing_end_snapshot_count=missing_end_snapshot_count,
        )

    @staticmethod
    def _bond_issues(
        *,
        label_count: int,
        evaluable_count: int,
        positive_count: int,
        negative_count: int,
        insufficient_count: int,
        insufficient_ratio: Decimal | None,
        labels_with_warnings_count: int,
        extreme_return_count: int,
        missing_start_snapshot_count: int,
        missing_end_snapshot_count: int,
        request: LabelQualityReportRequest,
        single_bond_scope: bool,
    ) -> list[str]:
        issues: list[str] = []
        if label_count == 0:
            issues.append("no_labels")
        if single_bond_scope and evaluable_count < request.minimum_evaluable_rows:
            issues.append("low_evaluable_rows")
        if (
            insufficient_ratio is not None
            and insufficient_ratio > request.maximum_insufficient_ratio
        ):
            issues.append("high_insufficient_ratio")
        if missing_start_snapshot_count > 0:
            issues.append("missing_start_snapshot")
        if missing_end_snapshot_count > 0:
            issues.append("missing_end_snapshot")
        if extreme_return_count > 0:
            issues.append("extreme_return")
        if labels_with_warnings_count > 0:
            issues.append("warning_labels")
        if positive_count == 0 and evaluable_count > 0:
            issues.append("no_positive_labels")
        if negative_count == 0 and evaluable_count > 0:
            issues.append("no_negative_labels")
        if insufficient_count > 0:
            issues.append("insufficient_labels")
        return issues

    @staticmethod
    def _bond_status(issues: list[str]) -> str:
        not_ready = {
            "no_labels",
            "low_evaluable_rows",
            "high_insufficient_ratio",
            "missing_start_snapshot",
            "missing_end_snapshot",
            "extreme_return",
        }
        warning = {
            "warning_labels",
            "no_positive_labels",
            "no_negative_labels",
            "insufficient_labels",
        }
        issue_set = set(issues)
        if issue_set & not_ready:
            return "not_ready"
        if issue_set & warning:
            return "warning"
        return "ready"

    def _company_rows(
        self,
        *,
        bonds: list[Bond],
        companies: dict[int, Company],
        bond_rows: list[LabelQualityBondRow],
    ) -> list[LabelQualityCompanyRow]:
        rows_by_company: dict[int, list[LabelQualityBondRow]] = defaultdict(list)
        for row in bond_rows:
            if row.company_id is not None:
                rows_by_company[row.company_id].append(row)
        company_ids = [bond.company_id for bond in bonds]
        ordered_company_ids = list(dict.fromkeys(company_ids))
        result: list[LabelQualityCompanyRow] = []
        for company_id in ordered_company_ids:
            items = rows_by_company.get(company_id, [])
            company = companies.get(company_id)
            ready_count = sum(item.status == "ready" for item in items)
            warning_count = sum(item.status == "warning" for item in items)
            not_ready_count = sum(item.status == "not_ready" for item in items)
            issues: list[str] = []
            for item in items:
                for issue in item.issues:
                    if issue not in issues:
                        issues.append(issue)
            status_value = "ready"
            if not_ready_count > 0:
                status_value = "not_ready"
            elif warning_count > 0:
                status_value = "warning"
            label_row_count = sum(item.label_row_count for item in items)
            evaluable_count = sum(item.evaluable_label_count for item in items)
            positive_count = sum(item.positive_label_count for item in items)
            negative_count = sum(item.negative_label_count for item in items)
            insufficient_count = sum(item.insufficient_label_count for item in items)
            result.append(
                LabelQualityCompanyRow(
                    company_id=company_id,
                    company_name=company.name if company is not None else None,
                    company_ticker=company.ticker if company is not None else None,
                    bond_count=len(items),
                    status=status_value,
                    issue_count=len(issues),
                    issues=issues,
                    label_row_count=label_row_count,
                    evaluable_label_count=evaluable_count,
                    positive_label_count=positive_count,
                    negative_label_count=negative_count,
                    insufficient_label_count=insufficient_count,
                    insufficient_ratio=self._ratio(insufficient_count, label_row_count),
                    labels_with_warnings_count=sum(
                        item.labels_with_warnings_count for item in items
                    ),
                    extreme_return_count=sum(item.extreme_return_count for item in items),
                    ready_bond_count=ready_count,
                    warning_bond_count=warning_count,
                    not_ready_bond_count=not_ready_count,
                )
            )
        return result

    @staticmethod
    def _sort_bond_rows(rows: list[LabelQualityBondRow]) -> list[LabelQualityBondRow]:
        return sorted(
            rows,
            key=lambda row: (
                -STATUS_SEVERITY[row.status],
                -row.issue_count,
                row.label_row_count,
                row.bond_id,
            ),
        )

    @staticmethod
    def _sort_company_rows(
        rows: list[LabelQualityCompanyRow],
    ) -> list[LabelQualityCompanyRow]:
        return sorted(
            rows,
            key=lambda row: (
                -STATUS_SEVERITY[row.status],
                -row.issue_count,
                row.label_row_count,
                row.company_id,
            ),
        )

    def _issue_summary(
        self,
        *,
        bond_rows: list[LabelQualityBondRow],
        labels: list[BondReturnLabel],
        overview: LabelQualityOverview,
        request: LabelQualityReportRequest,
    ) -> LabelQualityIssueSummary:
        no_labels_count = sum(row.label_row_count == 0 for row in bond_rows)
        return LabelQualityIssueSummary(
            no_labels_count=no_labels_count,
            insufficient_labels_count=overview.insufficient_label_count,
            null_future_return_count=overview.null_future_return_count,
            missing_start_snapshot_count=sum(
                label.start_market_snapshot_id is None for label in labels
            ),
            missing_end_snapshot_count=sum(
                label.end_market_snapshot_id is None for label in labels
            ),
            warning_label_count=overview.labels_with_warnings_count,
            extreme_return_count=overview.extreme_return_count,
            low_evaluable_rows=int(
                overview.evaluable_label_count < request.minimum_evaluable_rows
            ),
            low_positive_rows=int(
                overview.positive_label_count < request.minimum_positive_rows
            ),
            low_negative_rows=int(
                overview.negative_label_count < request.minimum_negative_rows
            ),
            high_insufficient_ratio=int(
                overview.insufficient_ratio is not None
                and overview.insufficient_ratio > request.maximum_insufficient_ratio
            ),
        )

    def _warning_breakdown(
        self,
        labels: list[BondReturnLabel],
    ) -> list[LabelQualityWarningItem]:
        grouped: dict[str, list[BondReturnLabel]] = defaultdict(list)
        for label in labels:
            for message in label.return_calculation_warnings or []:
                grouped[str(message)].append(label)
        result: list[LabelQualityWarningItem] = []
        for message, items in grouped.items():
            first = sorted(items, key=lambda item: item.id)[0]
            result.append(
                LabelQualityWarningItem(
                    message=message,
                    count=len(items),
                    first_seen_label_id=first.id,
                    example_bond_id=first.bond_id,
                    example_as_of_date=first.as_of_date,
                )
            )
        return sorted(result, key=lambda item: (-item.count, item.message))

    def _return_distribution(
        self,
        labels: list[BondReturnLabel],
    ) -> LabelQualityReturnDistribution:
        values = sorted(self._return_values(labels))
        return LabelQualityReturnDistribution(
            count=len(values),
            average=self._average(values),
            median=self._median(values),
            minimum=min(values) if values else None,
            maximum=max(values) if values else None,
            p10=self._percentile(values, Decimal("0.10")),
            p25=self._percentile(values, Decimal("0.25")),
            p75=self._percentile(values, Decimal("0.75")),
            p90=self._percentile(values, Decimal("0.90")),
        )

    def _component_summary(
        self,
        labels: list[BondReturnLabel],
    ) -> LabelQualityComponentSummary:
        return LabelQualityComponentSummary(
            price_return_average=self._average(
                [label.price_return for label in labels if label.price_return is not None]
            ),
            coupon_return_average=self._average(
                [label.coupon_return for label in labels if label.coupon_return is not None]
            ),
            amortization_return_average=self._average(
                [
                    label.amortization_return
                    for label in labels
                    if label.amortization_return is not None
                ]
            ),
            redemption_return_average=self._average(
                [
                    label.redemption_return
                    for label in labels
                    if label.redemption_return is not None
                ]
            ),
            gross_total_return_average=self._average(
                [
                    label.gross_total_return
                    for label in labels
                    if label.gross_total_return is not None
                ]
            ),
            estimated_costs_return_average=self._average(
                [
                    label.estimated_costs_return
                    for label in labels
                    if label.estimated_costs_return is not None
                ]
            ),
            net_total_return_average=self._average(
                [
                    label.net_total_return
                    for label in labels
                    if label.net_total_return is not None
                ]
            ),
            risk_adjusted_excess_return_average=self._average(
                [
                    label.risk_adjusted_excess_return
                    for label in labels
                    if label.risk_adjusted_excess_return is not None
                ]
            ),
            required_risk_premium_average=self._average(
                [
                    label.required_risk_premium
                    for label in labels
                    if label.required_risk_premium is not None
                ]
            ),
            cashflow_included_count=sum(
                self._details(label).get("cashflows_included") is True
                for label in labels
            ),
            cashflow_disabled_count=sum(
                self._details(label).get("cashflows_included") is False
                for label in labels
            ),
            benchmark_missing_count=sum(
                self._has_warning(label, "Benchmark return is not provided")
                for label in labels
            ),
            risk_premium_missing_count=sum(
                self._has_warning(label, "Required risk premium is missing")
                for label in labels
            ),
        )

    @staticmethod
    def _validate_request(request: LabelQualityReportRequest) -> list[str]:
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if (request.date_to - request.date_from).days > 3660:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="date range must not exceed 3660 days",
            )
        if request.horizon_days is not None and request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        return_methods = (
            list(dict.fromkeys(request.return_methods))
            if request.return_methods is not None
            else list(RETURN_METHODS)
        )
        if any(method not in RETURN_METHODS for method in return_methods):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        selectors = [
            request.bond_ids is not None,
            request.company_ids is not None,
            request.secids is not None,
        ]
        if sum(selectors) > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Use only one selector type: bond_ids, company_ids, or secids",
            )
        if request.extreme_return_abs_limit <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="extreme_return_abs_limit must be positive",
            )
        if request.minimum_evaluable_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_evaluable_rows must be positive",
            )
        if request.minimum_positive_rows < 0 or request.minimum_negative_rows < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="class minimums must be non-negative",
            )
        if (
            request.maximum_insufficient_ratio < 0
            or request.maximum_insufficient_ratio > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_insufficient_ratio must be between 0 and 1",
            )
        if request.limit < 1 or request.limit > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 500",
            )
        if request.offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )
        return return_methods

    @staticmethod
    def _is_evaluable(label: BondReturnLabel) -> bool:
        return (
            label.label in EVALUABLE_LABELS
            and label.label_binary is not None
            and label.future_return is not None
        )

    @staticmethod
    def _has_warnings(label: BondReturnLabel) -> bool:
        return bool(label.return_calculation_warnings)

    @staticmethod
    def _has_warning(label: BondReturnLabel, pattern: str) -> bool:
        return any(pattern in str(message) for message in label.return_calculation_warnings or [])

    @staticmethod
    def _details(label: BondReturnLabel) -> dict[str, Any]:
        if isinstance(label.return_calculation_details, dict):
            return label.return_calculation_details
        return {}

    @staticmethod
    def _return_values(labels: list[BondReturnLabel]) -> list[Decimal]:
        return [label.future_return for label in labels if label.future_return is not None]

    @staticmethod
    def _ratio(numerator: int, denominator: int) -> Decimal | None:
        if denominator == 0:
            return None
        return Decimal(numerator) / Decimal(denominator)

    @staticmethod
    def _average(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return sum(values, Decimal("0")) / Decimal(len(values))

    @staticmethod
    def _median(values: list[Decimal]) -> Decimal | None:
        if not values:
            return None
        return Decimal(str(median(values)))

    @staticmethod
    def _percentile(values: list[Decimal], percentile: Decimal) -> Decimal | None:
        if not values:
            return None
        index = ceil(float(percentile) * len(values)) - 1
        index = min(max(index, 0), len(values) - 1)
        return values[index]

    @staticmethod
    def _extreme_return_count(
        labels: list[BondReturnLabel],
        request: LabelQualityReportRequest,
    ) -> int:
        return sum(
            label.future_return is not None
            and abs(label.future_return) > request.extreme_return_abs_limit
            for label in labels
        )

    @staticmethod
    def _dedupe(values: list[int]) -> tuple[list[int], bool]:
        result: list[int] = []
        seen: set[int] = set()
        for value in values:
            if value in seen:
                continue
            seen.add(value)
            result.append(value)
        return result, len(result) != len(values)

    @classmethod
    def _dedupe_secids(cls, values: list[str]) -> tuple[list[str], bool]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            secid = cls._normalize_secid(value)
            if not secid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="secids cannot contain empty values",
                )
            if secid in seen:
                continue
            seen.add(secid)
            result.append(secid)
        return result, len(result) != len(values)

    @staticmethod
    def _normalize_secid(value: Any) -> str | None:
        if value is None or value == "":
            return None
        text = str(value).strip().upper()
        return text or None
