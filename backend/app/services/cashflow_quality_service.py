from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from statistics import median
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.company import Company
from app.schemas.cashflow_quality import (
    CashflowQualityAuditRequest,
    CashflowQualityAuditResponse,
    CashflowQualityBondRow,
    CashflowQualityEventTypeSummary,
    CashflowQualityIssueDetail,
    CashflowQualityIssueSummary,
    CashflowQualityOverview,
    CashflowQualityWarning,
)


STATUS_SEVERITY = {"ready": 0, "warning": 1, "not_ready": 2}
CANONICAL_EVENT_TYPES = ("coupon", "amortization", "offer", "redemption", "other")
EVENT_TYPE_ALIASES = {
    "coupon": {"coupon", "coupons", "купон"},
    "amortization": {"amortization", "amortisation", "amort", "амортизация"},
    "offer": {"offer", "put_offer", "call_offer", "offer_redemption", "оферта"},
    "redemption": {"redemption", "maturity", "repayment", "погашение"},
}


class CashflowQualityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def audit(
        self,
        request: CashflowQualityAuditRequest,
    ) -> CashflowQualityAuditResponse:
        source = self._validate_request(request)
        audit_end_date = request.date_to + timedelta(days=request.horizon_days)
        bonds, warnings = self._resolve_bonds(request)
        companies = self._companies_for_bonds(bonds)
        events_by_bond = self._events_by_bond(
            bonds=bonds,
            date_from=request.date_from,
            date_to=audit_end_date,
            source=source,
        )
        rows = [
            self._bond_row(
                bond,
                company=companies.get(bond.company_id),
                events=events_by_bond.get(bond.id, []),
                request=request,
            )
            for bond in bonds
        ]
        rows = self._sort_rows(rows)
        overview = self._overview(rows=rows, bonds=bonds)
        issue_summary = self._issue_summary(rows)
        paginated_rows = rows[request.offset : request.offset + request.limit]
        if not request.include_bond_rows:
            paginated_rows = []

        return CashflowQualityAuditResponse(
            date_from=request.date_from,
            date_to=request.date_to,
            audit_end_date=audit_end_date,
            horizon_days=request.horizon_days,
            source=source,
            overview=overview,
            issue_summary=issue_summary,
            total_bond_rows=len(rows),
            limit=request.limit,
            offset=request.offset,
            bond_rows=paginated_rows,
            warnings=warnings,
        )

    def _resolve_bonds(
        self,
        request: CashflowQualityAuditRequest,
    ) -> tuple[list[Bond], list[CashflowQualityWarning]]:
        warnings: list[CashflowQualityWarning] = []
        if request.bond_ids is not None:
            bond_ids, deduped = self._dedupe(request.bond_ids)
            if deduped:
                warnings.append(
                    CashflowQualityWarning(message="Duplicate selectors were ignored")
                )
            if not bond_ids:
                return [], warnings
            bonds = list(
                self.db.execute(select(Bond).where(Bond.id.in_(bond_ids))).scalars()
            )
            by_id = {bond.id: bond for bond in bonds}
            missing = [bond_id for bond_id in bond_ids if bond_id not in by_id]
            if missing:
                warnings.append(
                    CashflowQualityWarning(
                        message="Some requested bond ids were not found",
                        details={"bond_ids": missing},
                    )
                )
            return [by_id[bond_id] for bond_id in bond_ids if bond_id in by_id], warnings

        if request.company_ids is not None:
            company_ids, deduped = self._dedupe(request.company_ids)
            if deduped:
                warnings.append(
                    CashflowQualityWarning(message="Duplicate selectors were ignored")
                )
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
                    CashflowQualityWarning(
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
                warnings.append(
                    CashflowQualityWarning(message="Duplicate selectors were ignored")
                )
            if not secids:
                return [], warnings
            bonds = list(
                self.db.execute(select(Bond).where(Bond.secid.in_(secids))).scalars()
            )
            by_secid = {self._normalize_secid(bond.secid): bond for bond in bonds}
            missing = [secid for secid in secids if secid not in by_secid]
            if missing:
                warnings.append(
                    CashflowQualityWarning(
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

    def _events_by_bond(
        self,
        *,
        bonds: list[Bond],
        date_from: date,
        date_to: date,
        source: str | None,
    ) -> dict[int, list[BondCashflowEvent]]:
        bond_ids = [bond.id for bond in bonds]
        if not bond_ids:
            return {}
        stmt = select(BondCashflowEvent).where(
            BondCashflowEvent.bond_id.in_(bond_ids),
            BondCashflowEvent.event_date >= date_from,
            BondCashflowEvent.event_date <= date_to,
        )
        if source is not None:
            stmt = stmt.where(BondCashflowEvent.source == source)
        stmt = stmt.order_by(
            BondCashflowEvent.bond_id.asc(),
            BondCashflowEvent.event_date.asc(),
            BondCashflowEvent.id.asc(),
        )
        grouped: dict[int, list[BondCashflowEvent]] = defaultdict(list)
        for event in self.db.execute(stmt).scalars():
            grouped[event.bond_id].append(event)
        return grouped

    def _bond_row(
        self,
        bond: Bond,
        *,
        company: Company | None,
        events: list[BondCashflowEvent],
        request: CashflowQualityAuditRequest,
    ) -> CashflowQualityBondRow:
        canonical_types = [self._canonical_event_type(event.event_type) for event in events]
        event_count = len(events)
        future_events = [event for event in events if event.event_date >= request.date_to]
        future_event_count = len(future_events)
        first_event_date = events[0].event_date if events else None
        last_event_date = events[-1].event_date if events else None
        next_event_date = future_events[0].event_date if future_events else None
        days_to_next_event = (
            (next_event_date - request.date_to).days
            if next_event_date is not None
            else None
        )
        type_counts = {
            canonical_type: canonical_types.count(canonical_type)
            for canonical_type in CANONICAL_EVENT_TYPES
        }
        missing_amount_count = sum(event.amount is None for event in events)
        non_positive_amount_count = sum(
            event.amount is not None and event.amount <= 0 for event in events
        )
        currency_mismatch_count = sum(
            bool(event.currency)
            and bool(bond.currency)
            and event.currency.upper() != bond.currency.upper()
            for event in events
        )
        duplicate_event_count = self._duplicate_event_count(events)
        event_type_breakdown = self._event_type_breakdown(events)
        issues, issue_details = self._issues(
            bond=bond,
            events=events,
            canonical_types=canonical_types,
            missing_amount_count=missing_amount_count,
            non_positive_amount_count=non_positive_amount_count,
            currency_mismatch_count=currency_mismatch_count,
            duplicate_event_count=duplicate_event_count,
            future_event_count=future_event_count,
            days_to_next_event=days_to_next_event,
            request=request,
        )
        visible_breakdown = (
            event_type_breakdown if request.include_event_type_breakdown else []
        )
        visible_details = issue_details if request.include_issue_details else []
        return CashflowQualityBondRow(
            bond_id=bond.id,
            secid=bond.secid,
            isin=bond.isin,
            bond_name=bond.name,
            company_id=bond.company_id,
            company_name=company.name if company is not None else None,
            company_ticker=company.ticker if company is not None else None,
            status=self._status(issues),
            issue_count=len(issues),
            issues=issues,
            event_count=event_count,
            future_event_count=future_event_count,
            first_event_date=first_event_date,
            last_event_date=last_event_date,
            next_event_date=next_event_date,
            days_to_next_event=days_to_next_event,
            coupon_event_count=type_counts["coupon"],
            amortization_event_count=type_counts["amortization"],
            offer_event_count=type_counts["offer"],
            redemption_event_count=type_counts["redemption"],
            other_event_count=type_counts["other"],
            missing_amount_count=missing_amount_count,
            non_positive_amount_count=non_positive_amount_count,
            currency_mismatch_count=currency_mismatch_count,
            duplicate_event_count=duplicate_event_count,
            event_type_breakdown=visible_breakdown,
            issue_details=visible_details,
        )

    def _issues(
        self,
        *,
        bond: Bond,
        events: list[BondCashflowEvent],
        canonical_types: list[str],
        missing_amount_count: int,
        non_positive_amount_count: int,
        currency_mismatch_count: int,
        duplicate_event_count: int,
        future_event_count: int,
        days_to_next_event: int | None,
        request: CashflowQualityAuditRequest,
    ) -> tuple[list[str], list[CashflowQualityIssueDetail]]:
        issues: list[str] = []
        details: list[CashflowQualityIssueDetail] = []

        def add_issue(
            code: str,
            message: str,
            *,
            event: BondCashflowEvent | None = None,
            extra: dict[str, Any] | None = None,
        ) -> None:
            if code not in issues:
                issues.append(code)
            details.append(
                CashflowQualityIssueDetail(
                    code=code,
                    message=message,
                    event_date=event.event_date if event is not None else None,
                    event_type=event.event_type if event is not None else None,
                    details=extra or {},
                )
            )

        if not self._normalize_secid(bond.secid):
            add_issue("missing_secid", "Bond secid is missing")
        if not events:
            add_issue("no_cashflows", "No cashflow events were found")
        if request.require_future_cashflows and future_event_count == 0:
            add_issue("no_future_cashflows", "No future cashflow events were found")
        if request.require_coupon_events and "coupon" not in canonical_types:
            add_issue("no_coupon_events", "Coupon event category is missing")
        if (
            request.require_redemption_or_maturity
            and "redemption" not in canonical_types
        ):
            add_issue(
                "no_redemption_or_maturity",
                "Redemption or maturity event category is missing",
            )
        for event, canonical_type in zip(events, canonical_types, strict=False):
            if canonical_type == "other":
                add_issue(
                    "invalid_event_type",
                    "Cashflow event type is not a recognized category",
                    event=event,
                )
        if missing_amount_count > 0:
            add_issue(
                "missing_amount",
                "One or more cashflow events have no amount",
                extra={"count": missing_amount_count},
            )
        if non_positive_amount_count > 0:
            add_issue(
                "non_positive_amount",
                "One or more cashflow events have non-positive amount",
                extra={"count": non_positive_amount_count},
            )
        if currency_mismatch_count > 0:
            add_issue(
                "currency_mismatch",
                "One or more cashflow events have currency different from bond currency",
                extra={"count": currency_mismatch_count},
            )
        if duplicate_event_count > request.max_duplicate_events_per_bond:
            add_issue(
                "duplicate_events",
                "Duplicate cashflow events were detected",
                extra={"count": duplicate_event_count},
            )
        if (
            days_to_next_event is not None
            and days_to_next_event > request.maximum_days_without_future_event
        ):
            add_issue(
                "stale_future_schedule",
                "Next future cashflow event is far from the audit date",
                extra={"days_to_next_event": days_to_next_event},
            )
        return issues, details

    @staticmethod
    def _status(issues: list[str]) -> str:
        not_ready_issues = {
            "missing_secid",
            "no_cashflows",
            "no_future_cashflows",
        }
        warning_issues = {
            "no_coupon_events",
            "no_redemption_or_maturity",
            "invalid_event_type",
            "missing_amount",
            "non_positive_amount",
            "currency_mismatch",
            "duplicate_events",
            "stale_future_schedule",
        }
        issue_set = set(issues)
        if issue_set & not_ready_issues:
            return "not_ready"
        if issue_set & warning_issues:
            return "warning"
        return "ready"

    def _event_type_breakdown(
        self,
        events: list[BondCashflowEvent],
    ) -> list[CashflowQualityEventTypeSummary]:
        grouped: dict[str, list[BondCashflowEvent]] = {
            canonical_type: [] for canonical_type in CANONICAL_EVENT_TYPES
        }
        for event in events:
            grouped[self._canonical_event_type(event.event_type)].append(event)

        summaries: list[CashflowQualityEventTypeSummary] = []
        for canonical_type in CANONICAL_EVENT_TYPES:
            items = grouped[canonical_type]
            amount_values = [event.amount for event in items if event.amount is not None]
            summaries.append(
                CashflowQualityEventTypeSummary(
                    event_type=canonical_type,
                    count=len(items),
                    first_event_date=items[0].event_date if items else None,
                    last_event_date=items[-1].event_date if items else None,
                    total_amount=(
                        sum(amount_values, Decimal("0")) if amount_values else None
                    ),
                )
            )
        return summaries

    def _duplicate_event_count(self, events: list[BondCashflowEvent]) -> int:
        counts: dict[tuple[date, str, str | None], int] = defaultdict(int)
        for event in events:
            key = (
                event.event_date,
                self._canonical_event_type(event.event_type),
                None if event.amount is None else str(event.amount),
            )
            counts[key] += 1
        return sum(max(0, count - 1) for count in counts.values())

    @staticmethod
    def _sort_rows(
        rows: list[CashflowQualityBondRow],
    ) -> list[CashflowQualityBondRow]:
        return sorted(
            rows,
            key=lambda row: (
                -STATUS_SEVERITY[row.status],
                -row.issue_count,
                row.future_event_count,
                row.bond_id,
            ),
        )

    def _overview(
        self,
        *,
        rows: list[CashflowQualityBondRow],
        bonds: list[Bond],
    ) -> CashflowQualityOverview:
        future_counts = [row.future_event_count for row in rows]
        return CashflowQualityOverview(
            selected_bond_count=len(rows),
            selected_company_count=len({bond.company_id for bond in bonds}),
            bonds_with_cashflows=sum(row.event_count > 0 for row in rows),
            bonds_without_cashflows=sum(row.event_count == 0 for row in rows),
            total_event_count=sum(row.event_count for row in rows),
            future_event_count=sum(row.future_event_count for row in rows),
            ready_bond_count=sum(row.status == "ready" for row in rows),
            warning_bond_count=sum(row.status == "warning" for row in rows),
            not_ready_bond_count=sum(row.status == "not_ready" for row in rows),
            coupon_event_count=sum(row.coupon_event_count for row in rows),
            amortization_event_count=sum(row.amortization_event_count for row in rows),
            offer_event_count=sum(row.offer_event_count for row in rows),
            redemption_event_count=sum(row.redemption_event_count for row in rows),
            other_event_count=sum(row.other_event_count for row in rows),
            average_future_event_count=self._average(future_counts),
            median_future_event_count=self._median(future_counts),
        )

    @staticmethod
    def _issue_summary(rows: list[CashflowQualityBondRow]) -> CashflowQualityIssueSummary:
        counts = defaultdict(int)
        for row in rows:
            for issue in row.issues:
                counts[issue] += 1
        return CashflowQualityIssueSummary(
            missing_secid_count=counts["missing_secid"],
            no_cashflows_count=counts["no_cashflows"],
            no_future_cashflows_count=counts["no_future_cashflows"],
            no_coupon_events_count=counts["no_coupon_events"],
            no_redemption_or_maturity_count=counts["no_redemption_or_maturity"],
            invalid_event_date_count=counts["invalid_event_date"],
            invalid_event_type_count=counts["invalid_event_type"],
            missing_amount_count=counts["missing_amount"],
            non_positive_amount_count=counts["non_positive_amount"],
            currency_mismatch_count=counts["currency_mismatch"],
            duplicate_event_count=counts["duplicate_events"],
            stale_future_schedule_count=counts["stale_future_schedule"],
        )

    @staticmethod
    def _validate_request(request: CashflowQualityAuditRequest) -> str | None:
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
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
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
        if request.max_duplicate_events_per_bond < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_duplicate_events_per_bond must be non-negative",
            )
        if request.maximum_days_without_future_event < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_days_without_future_event must be non-negative",
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
        if request.source is None:
            return None
        source = str(request.source).strip()
        if not source:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source must not be empty when provided",
            )
        return source

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

    @staticmethod
    def _canonical_event_type(value: str | None) -> str:
        if value is None:
            return "other"
        normalized = str(value).strip().lower()
        for canonical_type, aliases in EVENT_TYPE_ALIASES.items():
            if normalized in aliases:
                return canonical_type
        return "other"

    @staticmethod
    def _average(values: list[int]) -> Decimal | None:
        if not values:
            return None
        return Decimal(sum(values)) / Decimal(len(values))

    @staticmethod
    def _median(values: list[int]) -> Decimal | None:
        if not values:
            return None
        return Decimal(str(median(values)))
