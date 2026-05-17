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
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.schemas.market_history_quality import (
    MarketHistoryQualityAuditRequest,
    MarketHistoryQualityAuditResponse,
    MarketHistoryQualityBondRow,
    MarketHistoryQualityGap,
    MarketHistoryQualityIssueSummary,
    MarketHistoryQualityOverview,
    MarketHistoryQualityWarning,
)


EXPECTED_DATE_MODES = {"business_days", "observed_market_dates"}
STATUS_SEVERITY = {"ready": 0, "warning": 1, "not_ready": 2}
BUSINESS_DAY_WARNING = (
    "business_days mode uses a weekday approximation and does not account for "
    "exchange holidays"
)


class MarketHistoryQualityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def audit(
        self,
        request: MarketHistoryQualityAuditRequest,
    ) -> MarketHistoryQualityAuditResponse:
        source = self._validate_request(request)
        bonds, warnings = self._resolve_bonds(request)
        company_ids = {bond.company_id for bond in bonds}
        companies = {
            company.id: company
            for company in self.db.execute(
                select(Company).where(Company.id.in_(company_ids))
            ).scalars()
        }
        snapshots_by_bond = self._snapshots_by_bond(
            bonds=bonds,
            date_from=request.date_from,
            date_to=request.date_to,
            source=source,
        )
        expected_dates = self._expected_dates(
            request=request,
            snapshots_by_bond=snapshots_by_bond,
        )
        if request.expected_date_mode == "business_days":
            warnings.append(MarketHistoryQualityWarning(message=BUSINESS_DAY_WARNING))

        rows = [
            self._bond_row(
                bond,
                company=companies.get(bond.company_id),
                snapshots=snapshots_by_bond.get(bond.id, []),
                expected_dates=expected_dates,
                request=request,
            )
            for bond in bonds
        ]
        rows = self._sort_rows(rows)
        overview = self._overview(
            rows=rows,
            bonds=bonds,
            expected_date_count=len(expected_dates),
        )
        issue_summary = self._issue_summary(rows)
        paginated_rows = rows[request.offset : request.offset + request.limit]
        if not request.include_bond_rows:
            paginated_rows = []

        return MarketHistoryQualityAuditResponse(
            date_from=request.date_from,
            date_to=request.date_to,
            source=source,
            expected_date_mode=request.expected_date_mode,
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
        request: MarketHistoryQualityAuditRequest,
    ) -> tuple[list[Bond], list[MarketHistoryQualityWarning]]:
        warnings: list[MarketHistoryQualityWarning] = []
        if request.bond_ids is not None:
            bond_ids, deduped = self._dedupe(request.bond_ids)
            if deduped:
                warnings.append(
                    MarketHistoryQualityWarning(
                        message="Duplicate selectors were ignored"
                    )
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
                    MarketHistoryQualityWarning(
                        message="Some requested bond ids were not found",
                        details={"bond_ids": missing},
                    )
                )
            return [by_id[bond_id] for bond_id in bond_ids if bond_id in by_id], warnings

        if request.company_ids is not None:
            company_ids, deduped = self._dedupe(request.company_ids)
            if deduped:
                warnings.append(
                    MarketHistoryQualityWarning(
                        message="Duplicate selectors were ignored"
                    )
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
                    MarketHistoryQualityWarning(
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
                    MarketHistoryQualityWarning(
                        message="Duplicate selectors were ignored"
                    )
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
                    MarketHistoryQualityWarning(
                        message="Some requested secids were not found",
                        details={"secids": missing},
                    )
                )
            return [by_secid[secid] for secid in secids if secid in by_secid], warnings

        return list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars()), warnings

    def _snapshots_by_bond(
        self,
        *,
        bonds: list[Bond],
        date_from: date,
        date_to: date,
        source: str | None,
    ) -> dict[int, list[BondMarketSnapshot]]:
        bond_ids = [bond.id for bond in bonds]
        if not bond_ids:
            return {}
        stmt = select(BondMarketSnapshot).where(
            BondMarketSnapshot.bond_id.in_(bond_ids),
            BondMarketSnapshot.trade_date >= date_from,
            BondMarketSnapshot.trade_date <= date_to,
        )
        if source is not None:
            stmt = stmt.where(BondMarketSnapshot.source == source)
        stmt = stmt.order_by(
            BondMarketSnapshot.bond_id.asc(),
            BondMarketSnapshot.trade_date.asc(),
            BondMarketSnapshot.id.asc(),
        )
        grouped: dict[int, list[BondMarketSnapshot]] = defaultdict(list)
        for snapshot in self.db.execute(stmt).scalars():
            grouped[snapshot.bond_id].append(snapshot)
        return grouped

    def _expected_dates(
        self,
        *,
        request: MarketHistoryQualityAuditRequest,
        snapshots_by_bond: dict[int, list[BondMarketSnapshot]],
    ) -> list[date]:
        if request.expected_date_mode == "observed_market_dates":
            return sorted(
                {
                    snapshot.trade_date
                    for snapshots in snapshots_by_bond.values()
                    for snapshot in snapshots
                }
            )

        dates: list[date] = []
        current = request.date_from
        while current <= request.date_to:
            if current.weekday() < 5:
                dates.append(current)
            current += timedelta(days=1)
        return dates

    def _bond_row(
        self,
        bond: Bond,
        *,
        company: Company | None,
        snapshots: list[BondMarketSnapshot],
        expected_dates: list[date],
        request: MarketHistoryQualityAuditRequest,
    ) -> MarketHistoryQualityBondRow:
        expected_set = set(expected_dates)
        actual_dates = {snapshot.trade_date for snapshot in snapshots}
        missing_expected_dates = sorted(expected_set - actual_dates)
        gaps = self._gaps(missing_expected_dates, expected_dates)
        snapshot_count = len(snapshots)
        expected_date_count = len(expected_dates)
        coverage_ratio = self._ratio(snapshot_count, expected_date_count)
        first_trade_date = snapshots[0].trade_date if snapshots else None
        last_trade_date = snapshots[-1].trade_date if snapshots else None
        latest_snapshot = snapshots[-1] if snapshots else None

        price_count = sum(snapshot.price is not None for snapshot in snapshots)
        yield_count = sum(
            snapshot.yield_to_maturity is not None for snapshot in snapshots
        )
        volume_count = sum(snapshot.volume is not None for snapshot in snapshots)
        missing_price_count = snapshot_count - price_count
        missing_yield_count = snapshot_count - yield_count
        missing_volume_count = snapshot_count - volume_count
        non_positive_price_count = sum(
            snapshot.price is not None and snapshot.price <= 0
            for snapshot in snapshots
        )
        negative_yield_count = sum(
            snapshot.yield_to_maturity is not None
            and snapshot.yield_to_maturity < 0
            for snapshot in snapshots
        )
        negative_volume_count = sum(
            snapshot.volume is not None and snapshot.volume < 0
            for snapshot in snapshots
        )
        longest_gap_days = max((gap.gap_days for gap in gaps), default=None)

        issues: list[str] = []
        if not self._normalize_secid(bond.secid):
            issues.append("missing_secid")
        if snapshot_count == 0:
            issues.append("no_snapshots")
        elif snapshot_count < request.minimum_snapshot_count:
            issues.append("low_snapshot_count")
        if (
            coverage_ratio is not None
            and coverage_ratio < request.minimum_coverage_ratio
        ):
            issues.append("low_coverage")
        if longest_gap_days is not None and longest_gap_days > request.maximum_gap_days:
            issues.append("long_gap")
        if request.require_price and missing_price_count > 0:
            issues.append("missing_price")
        if request.require_yield and missing_yield_count > 0:
            issues.append("missing_yield")
        if request.require_volume and missing_volume_count > 0:
            issues.append("missing_volume")
        if non_positive_price_count > 0:
            issues.append("non_positive_price")
        if negative_yield_count > 0:
            issues.append("negative_yield")
        if negative_volume_count > 0:
            issues.append("negative_volume")
        if last_trade_date is not None and last_trade_date < request.date_to:
            issues.append("stale_history")

        status_value = self._status(issues)
        visible_gaps = gaps if request.include_gap_details else []
        return MarketHistoryQualityBondRow(
            bond_id=bond.id,
            secid=bond.secid,
            isin=bond.isin,
            bond_name=bond.name,
            company_id=bond.company_id,
            company_name=company.name if company is not None else None,
            company_ticker=company.ticker if company is not None else None,
            status=status_value,
            issue_count=len(issues),
            issues=issues,
            snapshot_count=snapshot_count,
            expected_date_count=expected_date_count,
            coverage_ratio=coverage_ratio,
            first_trade_date=first_trade_date,
            last_trade_date=last_trade_date,
            missing_expected_date_count=len(missing_expected_dates),
            longest_gap_days=longest_gap_days,
            gap_count=len(gaps),
            gaps=visible_gaps,
            price_count=price_count,
            yield_count=yield_count,
            volume_count=volume_count,
            missing_price_count=missing_price_count,
            missing_yield_count=missing_yield_count,
            missing_volume_count=missing_volume_count,
            non_positive_price_count=non_positive_price_count,
            negative_yield_count=negative_yield_count,
            negative_volume_count=negative_volume_count,
            latest_price=latest_snapshot.price if latest_snapshot else None,
            latest_yield_to_maturity=(
                latest_snapshot.yield_to_maturity if latest_snapshot else None
            ),
            latest_volume=latest_snapshot.volume if latest_snapshot else None,
            latest_trade_date=latest_snapshot.trade_date if latest_snapshot else None,
        )

    @staticmethod
    def _gaps(
        missing_dates: list[date],
        expected_dates: list[date],
    ) -> list[MarketHistoryQualityGap]:
        if not missing_dates:
            return []
        expected_positions = {value: index for index, value in enumerate(expected_dates)}
        gaps: list[MarketHistoryQualityGap] = []
        gap_start = missing_dates[0]
        previous = missing_dates[0]
        missing_count = 1
        for current in missing_dates[1:]:
            if expected_positions[current] == expected_positions[previous] + 1:
                missing_count += 1
                previous = current
                continue
            gaps.append(
                MarketHistoryQualityGap(
                    gap_start=gap_start,
                    gap_end=previous,
                    gap_days=(previous - gap_start).days + 1,
                    missing_expected_dates=missing_count,
                )
            )
            gap_start = current
            previous = current
            missing_count = 1
        gaps.append(
            MarketHistoryQualityGap(
                gap_start=gap_start,
                gap_end=previous,
                gap_days=(previous - gap_start).days + 1,
                missing_expected_dates=missing_count,
            )
        )
        return gaps

    @staticmethod
    def _status(issues: list[str]) -> str:
        not_ready_issues = {
            "missing_secid",
            "no_snapshots",
            "low_snapshot_count",
            "low_coverage",
            "long_gap",
            "missing_price",
            "non_positive_price",
        }
        warning_issues = {
            "missing_yield",
            "missing_volume",
            "negative_yield",
            "negative_volume",
            "stale_history",
        }
        issue_set = set(issues)
        if issue_set & not_ready_issues:
            return "not_ready"
        if issue_set & warning_issues:
            return "warning"
        return "ready"

    @staticmethod
    def _sort_rows(
        rows: list[MarketHistoryQualityBondRow],
    ) -> list[MarketHistoryQualityBondRow]:
        return sorted(
            rows,
            key=lambda row: (
                -STATUS_SEVERITY[row.status],
                -row.issue_count,
                0 if row.coverage_ratio is None else 1,
                row.coverage_ratio or Decimal("0"),
                row.bond_id,
            ),
        )

    def _overview(
        self,
        *,
        rows: list[MarketHistoryQualityBondRow],
        bonds: list[Bond],
        expected_date_count: int,
    ) -> MarketHistoryQualityOverview:
        coverage_values = [
            row.coverage_ratio for row in rows if row.coverage_ratio is not None
        ]
        total_snapshot_count = sum(row.snapshot_count for row in rows)
        total_price_count = sum(row.price_count for row in rows)
        total_yield_count = sum(row.yield_count for row in rows)
        total_volume_count = sum(row.volume_count for row in rows)
        return MarketHistoryQualityOverview(
            selected_bond_count=len(rows),
            selected_company_count=len({bond.company_id for bond in bonds}),
            expected_date_count=expected_date_count,
            bonds_with_snapshots=sum(row.snapshot_count > 0 for row in rows),
            bonds_without_snapshots=sum(row.snapshot_count == 0 for row in rows),
            total_snapshot_count=total_snapshot_count,
            average_coverage_ratio=self._average(coverage_values),
            median_coverage_ratio=self._median(coverage_values),
            ready_bond_count=sum(row.status == "ready" for row in rows),
            warning_bond_count=sum(row.status == "warning" for row in rows),
            not_ready_bond_count=sum(row.status == "not_ready" for row in rows),
            price_available_ratio=self._ratio(total_price_count, total_snapshot_count),
            yield_available_ratio=self._ratio(total_yield_count, total_snapshot_count),
            volume_available_ratio=self._ratio(total_volume_count, total_snapshot_count),
        )

    @staticmethod
    def _issue_summary(
        rows: list[MarketHistoryQualityBondRow],
    ) -> MarketHistoryQualityIssueSummary:
        counts = defaultdict(int)
        for row in rows:
            for issue in row.issues:
                counts[issue] += 1
        return MarketHistoryQualityIssueSummary(
            missing_secid_count=counts["missing_secid"],
            no_snapshots_count=counts["no_snapshots"],
            low_snapshot_count=counts["low_snapshot_count"],
            low_coverage_count=counts["low_coverage"],
            long_gap_count=counts["long_gap"],
            missing_price_count=counts["missing_price"],
            missing_yield_count=counts["missing_yield"],
            missing_volume_count=counts["missing_volume"],
            non_positive_price_count=counts["non_positive_price"],
            negative_yield_count=counts["negative_yield"],
            negative_volume_count=counts["negative_volume"],
            stale_history_count=counts["stale_history"],
        )

    @staticmethod
    def _validate_request(request: MarketHistoryQualityAuditRequest) -> str | None:
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
        if request.expected_date_mode not in EXPECTED_DATE_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid expected date mode",
            )
        if request.minimum_snapshot_count <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_snapshot_count must be positive",
            )
        if (
            request.minimum_coverage_ratio < 0
            or request.minimum_coverage_ratio > 1
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_coverage_ratio must be between 0 and 1",
            )
        if request.maximum_gap_days < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_gap_days must be non-negative",
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
