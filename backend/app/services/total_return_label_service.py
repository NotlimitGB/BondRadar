from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.schemas.cashflow import (
    BondTotalReturnLabelBuildRequest,
    BondTotalReturnLabelBuildResult,
)
from app.services.bond_cashflow_service import BondCashflowService
from app.services.market_snapshot_service import MarketSnapshotService


RETURN_METHODS = {"price", "total_return", "risk_adjusted"}


@dataclass(frozen=True)
class TotalReturnLabelBuildOutcome:
    label: BondReturnLabel | None
    action: str
    warnings: list[str] | None = None


class TotalReturnLabelService:
    def __init__(
        self,
        db: Session,
        *,
        market_service: MarketSnapshotService | None = None,
        cashflow_service: BondCashflowService | None = None,
    ) -> None:
        self.db = db
        self.market_service = market_service or MarketSnapshotService(db)
        self.cashflow_service = cashflow_service or BondCashflowService(db)

    def build_for_bond_date(
        self,
        bond_id: int,
        as_of_date: date,
        horizon_days: int,
        *,
        return_method: str = "total_return",
        benchmark_return: Decimal | None = None,
        transaction_cost_rate: Decimal = Decimal("0.001"),
        use_quality_filters: bool = True,
        require_start_market_snapshot: bool = True,
        require_end_market_snapshot: bool = True,
        require_positive_start_price: bool = True,
        require_positive_end_price: bool = True,
        require_cashflow_schedule: bool = False,
        include_cashflows_in_total_return: bool = True,
        max_gap_days_to_start_snapshot: int = 0,
        max_gap_days_to_end_snapshot: int = 7,
        skip_perpetual_without_offer: bool = True,
        skip_matured_before_horizon: bool = True,
        rebuild_existing: bool = False,
    ) -> TotalReturnLabelBuildOutcome:
        self._validate_method(return_method)
        if horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        bond = self.db.get(Bond, bond_id)
        if bond is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Bond not found",
            )

        existing = self.db.execute(
            select(BondReturnLabel).where(
                BondReturnLabel.bond_id == bond_id,
                BondReturnLabel.as_of_date == as_of_date,
                BondReturnLabel.horizon_days == horizon_days,
                BondReturnLabel.return_method == return_method,
            )
        ).scalar_one_or_none()
        if existing is not None and not rebuild_existing:
            return TotalReturnLabelBuildOutcome(label=existing, action="skipped")

        target_date = as_of_date + timedelta(days=horizon_days)
        start_snapshot = self._start_snapshot(
            bond.id,
            as_of_date,
            use_quality_filters=use_quality_filters,
            max_gap_days=max_gap_days_to_start_snapshot,
        )
        end_snapshot = self._end_snapshot(
            bond.id,
            target_date,
            use_quality_filters=use_quality_filters,
            max_gap_days=max_gap_days_to_end_snapshot,
        )
        start_price = self._price(start_snapshot)
        end_price = self._price(end_snapshot)
        if use_quality_filters:
            skip_reason = self._quality_skip_reason(
                bond=bond,
                as_of_date=as_of_date,
                target_date=target_date,
                start_snapshot=start_snapshot,
                end_snapshot=end_snapshot,
                start_price=start_price,
                end_price=end_price,
                require_start_market_snapshot=require_start_market_snapshot,
                require_end_market_snapshot=require_end_market_snapshot,
                require_positive_start_price=require_positive_start_price,
                require_positive_end_price=require_positive_end_price,
                require_cashflow_schedule=require_cashflow_schedule,
                skip_perpetual_without_offer=skip_perpetual_without_offer,
                skip_matured_before_horizon=skip_matured_before_horizon,
            )
            if skip_reason is not None:
                return TotalReturnLabelBuildOutcome(
                    label=None,
                    action="skipped",
                    warnings=[skip_reason],
                )

        payload = self._build_payload(
            bond=bond,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            return_method=return_method,
            benchmark_return=benchmark_return,
            transaction_cost_rate=transaction_cost_rate,
            start_snapshot=start_snapshot,
            end_snapshot=end_snapshot,
            include_cashflows_in_total_return=include_cashflows_in_total_return,
        )
        if existing is None:
            label = BondReturnLabel(**payload)
            self.db.add(label)
            self.db.flush()
            return TotalReturnLabelBuildOutcome(
                label=label,
                action="created",
                warnings=label.return_calculation_warnings or [],
            )

        for field, value in payload.items():
            setattr(existing, field, value)
        self.db.add(existing)
        self.db.flush()
        return TotalReturnLabelBuildOutcome(
            label=existing,
            action="updated",
            warnings=existing.return_calculation_warnings or [],
        )

    def build_labels(
        self,
        request: BondTotalReturnLabelBuildRequest,
    ) -> BondTotalReturnLabelBuildResult:
        self._validate_build_request(request)
        pairs = self._snapshot_pairs(request)
        errors: list[dict[str, Any]] = []
        warnings: list[str] = []
        counters = {"created": 0, "updated": 0, "skipped": 0}

        if not pairs:
            warnings.append("No market snapshot dates found for requested period")

        for bond_id, trade_date in pairs:
            try:
                outcome = self.build_for_bond_date(
                    bond_id,
                    trade_date,
                    request.horizon_days,
                    return_method=request.return_method,
                    benchmark_return=request.benchmark_return,
                    transaction_cost_rate=request.transaction_cost_rate,
                    use_quality_filters=request.use_quality_filters,
                    require_start_market_snapshot=request.require_start_market_snapshot,
                    require_end_market_snapshot=request.require_end_market_snapshot,
                    require_positive_start_price=request.require_positive_start_price,
                    require_positive_end_price=request.require_positive_end_price,
                    require_cashflow_schedule=request.require_cashflow_schedule,
                    include_cashflows_in_total_return=(
                        request.include_cashflows_in_total_return
                    ),
                    max_gap_days_to_start_snapshot=(
                        request.max_gap_days_to_start_snapshot
                    ),
                    max_gap_days_to_end_snapshot=request.max_gap_days_to_end_snapshot,
                    skip_perpetual_without_offer=(
                        request.skip_perpetual_without_offer
                    ),
                    skip_matured_before_horizon=request.skip_matured_before_horizon,
                    rebuild_existing=request.rebuild_existing,
                )
                counters[outcome.action] += 1
                self.db.commit()
                for warning in outcome.warnings or []:
                    if warning not in warnings:
                        warnings.append(warning)
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    {
                        "bond_id": bond_id,
                        "as_of_date": trade_date.isoformat(),
                        "error": self._error_detail(exc),
                    }
                )

        return BondTotalReturnLabelBuildResult(
            total=len(pairs),
            created=counters["created"],
            updated=counters["updated"],
            skipped=counters["skipped"],
            failed=len(errors),
            errors=errors,
            warnings=warnings,
        )

    def _build_payload(
        self,
        *,
        bond: Bond,
        as_of_date: date,
        horizon_days: int,
        return_method: str,
        benchmark_return: Decimal | None,
        transaction_cost_rate: Decimal,
        start_snapshot: BondMarketSnapshot | None,
        end_snapshot: BondMarketSnapshot | None,
        include_cashflows_in_total_return: bool,
    ) -> dict[str, Any]:
        warnings: list[str] = []
        details: dict[str, Any] = {
            "return_method": return_method,
            "as_of_date": as_of_date.isoformat(),
            "horizon_days": horizon_days,
            "target_date": (as_of_date + timedelta(days=horizon_days)).isoformat(),
            "cashflows_included": include_cashflows_in_total_return,
        }
        start_price = self._price(start_snapshot)
        end_price = self._price(end_snapshot)
        price_return = self._price_return(start_price, end_price)
        if price_return is None:
            warnings.append("Start or end price is missing")

        if return_method == "price":
            return self._payload(
                bond=bond,
                as_of_date=as_of_date,
                horizon_days=horizon_days,
                return_method=return_method,
                start_snapshot=start_snapshot,
                end_snapshot=end_snapshot,
                start_price=start_price,
                end_price=end_price,
                future_return=price_return,
                price_return=price_return,
                benchmark_return=None,
                excess_return=None,
                warnings=warnings,
                details=details,
            )

        total_return_payload = self._total_return_values(
            bond=bond,
            as_of_date=as_of_date,
            end_date=end_snapshot.trade_date if end_snapshot is not None else None,
            start_snapshot=start_snapshot,
            end_snapshot=end_snapshot,
            transaction_cost_rate=transaction_cost_rate,
            benchmark_return=benchmark_return,
            warnings=warnings,
            details=details,
            include_cashflows=include_cashflows_in_total_return,
        )
        basis = total_return_payload["net_total_return"]
        if return_method == "risk_adjusted":
            required_risk_premium = self._required_risk_premium(bond.id, as_of_date)
            if required_risk_premium is None:
                required_risk_premium = Decimal("0")
                warnings.append("Required risk premium is missing, zero premium was used")
            total_return_payload["required_risk_premium"] = required_risk_premium
            if total_return_payload["excess_return"] is not None:
                total_return_payload["risk_adjusted_excess_return"] = (
                    total_return_payload["excess_return"] - required_risk_premium
                )
            basis = total_return_payload["risk_adjusted_excess_return"]

        return self._payload(
            bond=bond,
            as_of_date=as_of_date,
            horizon_days=horizon_days,
            return_method=return_method,
            start_snapshot=start_snapshot,
            end_snapshot=end_snapshot,
            start_price=start_price,
            end_price=end_price,
            future_return=basis,
            price_return=price_return,
            warnings=warnings,
            details=details,
            **total_return_payload,
        )

    def _total_return_values(
        self,
        *,
        bond: Bond,
        as_of_date: date,
        end_date: date | None,
        start_snapshot: BondMarketSnapshot | None,
        end_snapshot: BondMarketSnapshot | None,
        transaction_cost_rate: Decimal,
        benchmark_return: Decimal | None,
        warnings: list[str],
        details: dict[str, Any],
        include_cashflows: bool,
    ) -> dict[str, Decimal | None]:
        start_dirty_value = self._dirty_value(bond, start_snapshot, warnings)
        end_dirty_value = self._dirty_value(bond, end_snapshot, warnings)
        cashflows = None
        if end_date is not None and include_cashflows:
            cashflows = self.cashflow_service.sum_cashflows_for_period(
                bond=bond,
                as_of_date=as_of_date,
                end_date=end_date,
            )
            warnings.extend(
                warning for warning in cashflows.warnings if warning not in warnings
            )
        elif not include_cashflows:
            warnings.append("Cashflow inclusion disabled for label calculation")

        details["start_dirty_value"] = self._json_decimal(start_dirty_value)
        details["end_dirty_value"] = self._json_decimal(end_dirty_value)
        details["cashflow_events"] = [] if cashflows is None else cashflows.details

        coupon_cashflow = cashflows.coupon_cashflow if cashflows else Decimal("0")
        amortization_cashflow = (
            cashflows.amortization_cashflow if cashflows else Decimal("0")
        )
        redemption_cashflow = (
            cashflows.redemption_cashflow if cashflows else Decimal("0")
        )
        gross_total_return: Decimal | None = None
        estimated_costs_return: Decimal | None = None
        net_total_return: Decimal | None = None
        coupon_return: Decimal | None = None
        amortization_return: Decimal | None = None
        redemption_return: Decimal | None = None
        excess_return: Decimal | None = None

        if (
            start_dirty_value is not None
            and end_dirty_value is not None
            and start_dirty_value > 0
        ):
            coupon_return = coupon_cashflow / start_dirty_value
            amortization_return = amortization_cashflow / start_dirty_value
            redemption_return = redemption_cashflow / start_dirty_value
            gross_total_return = (
                end_dirty_value
                - start_dirty_value
                + coupon_cashflow
                + amortization_cashflow
                + redemption_cashflow
            ) / start_dirty_value
            estimated_costs_return = transaction_cost_rate + self._spread_cost(
                self._liquidity_score(bond, start_snapshot)
            )
            net_total_return = gross_total_return - estimated_costs_return
            if benchmark_return is None:
                benchmark_return = Decimal("0")
                warnings.append("Benchmark return is not provided, zero benchmark was used")
            excess_return = net_total_return - benchmark_return

        details["coupon_cashflow"] = self._json_decimal(coupon_cashflow)
        details["amortization_cashflow"] = self._json_decimal(amortization_cashflow)
        details["redemption_cashflow"] = self._json_decimal(redemption_cashflow)
        details["transaction_cost_rate"] = self._json_decimal(transaction_cost_rate)

        return {
            "coupon_return": coupon_return,
            "amortization_return": amortization_return,
            "redemption_return": redemption_return,
            "gross_total_return": gross_total_return,
            "estimated_costs_return": estimated_costs_return,
            "net_total_return": net_total_return,
            "risk_adjusted_excess_return": None,
            "required_risk_premium": None,
            "benchmark_return": benchmark_return,
            "excess_return": excess_return,
        }

    def _payload(
        self,
        *,
        bond: Bond,
        as_of_date: date,
        horizon_days: int,
        return_method: str,
        start_snapshot: BondMarketSnapshot | None,
        end_snapshot: BondMarketSnapshot | None,
        start_price: Decimal | None,
        end_price: Decimal | None,
        future_return: Decimal | None,
        price_return: Decimal | None,
        benchmark_return: Decimal | None,
        excess_return: Decimal | None,
        warnings: list[str],
        details: dict[str, Any],
        coupon_return: Decimal | None = None,
        amortization_return: Decimal | None = None,
        redemption_return: Decimal | None = None,
        gross_total_return: Decimal | None = None,
        estimated_costs_return: Decimal | None = None,
        net_total_return: Decimal | None = None,
        risk_adjusted_excess_return: Decimal | None = None,
        required_risk_premium: Decimal | None = None,
    ) -> dict[str, Any]:
        label = "insufficient_data"
        label_binary: int | None = None
        if future_return is not None:
            if future_return > 0:
                label = "positive_return"
                label_binary = 1
            else:
                label = "negative_return"
                label_binary = 0
        if future_return is None and "Start or end price is missing" not in warnings:
            warnings.append("Start or end price is missing")

        return {
            "bond_id": bond.id,
            "as_of_date": as_of_date,
            "horizon_days": horizon_days,
            "return_method": return_method,
            "start_market_snapshot_id": start_snapshot.id if start_snapshot else None,
            "end_market_snapshot_id": end_snapshot.id if end_snapshot else None,
            "start_price": start_price,
            "end_price": end_price,
            "future_return": future_return,
            "benchmark_return": benchmark_return,
            "excess_return": excess_return,
            "price_return": price_return,
            "coupon_return": coupon_return,
            "amortization_return": amortization_return,
            "redemption_return": redemption_return,
            "gross_total_return": gross_total_return,
            "estimated_costs_return": estimated_costs_return,
            "net_total_return": net_total_return,
            "risk_adjusted_excess_return": risk_adjusted_excess_return,
            "required_risk_premium": required_risk_premium,
            "return_calculation_warnings": list(dict.fromkeys(warnings)),
            "return_calculation_details": details,
            "label": label,
            "label_binary": label_binary,
        }

    def _snapshot_pairs(
        self,
        request: BondTotalReturnLabelBuildRequest,
    ) -> list[tuple[int, date]]:
        pairs: set[tuple[int, date]] = set()
        stmt = (
            select(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date)
            .where(
                BondMarketSnapshot.trade_date >= request.as_of_date_from,
                BondMarketSnapshot.trade_date <= request.as_of_date_to,
            )
            .distinct()
        )
        if request.bond_ids:
            stmt = stmt.where(BondMarketSnapshot.bond_id.in_(set(request.bond_ids)))
        stmt = stmt.order_by(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date)
        pairs.update((row.bond_id, row.trade_date) for row in self.db.execute(stmt))
        if request.max_gap_days_to_start_snapshot > 0:
            feature_stmt = (
                select(BondFeatureSnapshot.bond_id, BondFeatureSnapshot.as_of_date)
                .where(
                    BondFeatureSnapshot.as_of_date >= request.as_of_date_from,
                    BondFeatureSnapshot.as_of_date <= request.as_of_date_to,
                )
                .distinct()
            )
            if request.bond_ids:
                feature_stmt = feature_stmt.where(
                    BondFeatureSnapshot.bond_id.in_(set(request.bond_ids))
                )
            pairs.update(
                (row.bond_id, row.as_of_date) for row in self.db.execute(feature_stmt)
            )
        return sorted(pairs, key=lambda item: (item[0], item[1]))

    def _start_snapshot(
        self,
        bond_id: int,
        as_of_date: date,
        *,
        use_quality_filters: bool,
        max_gap_days: int,
    ) -> BondMarketSnapshot | None:
        if not use_quality_filters:
            return self.market_service.get_latest_for_bond(bond_id, as_of_date)
        min_date = as_of_date - timedelta(days=max_gap_days)
        return self.db.execute(
            select(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id == bond_id,
                BondMarketSnapshot.trade_date >= min_date,
                BondMarketSnapshot.trade_date <= as_of_date,
            )
            .order_by(
                BondMarketSnapshot.trade_date.desc(),
                BondMarketSnapshot.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _end_snapshot(
        self,
        bond_id: int,
        target_date: date,
        *,
        use_quality_filters: bool,
        max_gap_days: int,
    ) -> BondMarketSnapshot | None:
        max_date = target_date + timedelta(
            days=max_gap_days
            if use_quality_filters
            else MarketSnapshotService.MAX_FUTURE_LOOKUP_DAYS
        )
        return self.db.execute(
            select(BondMarketSnapshot)
            .where(
                BondMarketSnapshot.bond_id == bond_id,
                BondMarketSnapshot.trade_date >= target_date,
                BondMarketSnapshot.trade_date <= max_date,
            )
            .order_by(
                BondMarketSnapshot.trade_date.asc(),
                BondMarketSnapshot.id.asc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    def _quality_skip_reason(
        self,
        *,
        bond: Bond,
        as_of_date: date,
        target_date: date,
        start_snapshot: BondMarketSnapshot | None,
        end_snapshot: BondMarketSnapshot | None,
        start_price: Decimal | None,
        end_price: Decimal | None,
        require_start_market_snapshot: bool,
        require_end_market_snapshot: bool,
        require_positive_start_price: bool,
        require_positive_end_price: bool,
        require_cashflow_schedule: bool,
        skip_perpetual_without_offer: bool,
        skip_matured_before_horizon: bool,
    ) -> str | None:
        if bond.maturity_date is not None and bond.maturity_date <= as_of_date:
            return "Bond is already matured at label date"
        if (
            skip_matured_before_horizon
            and bond.maturity_date is not None
            and bond.maturity_date < target_date
            and not self._has_cashflow_event(
                bond.id,
                as_of_date,
                target_date,
                event_types={"redemption", "offer_redemption"},
            )
        ):
            return "Bond matures before label horizon and redemption cashflow is missing"
        if (
            skip_perpetual_without_offer
            and bond.is_perpetual
            and (
                bond.offer_date is None
                or bond.offer_date <= as_of_date
                or bond.offer_date > target_date
            )
        ):
            return "Perpetual bond offer date is missing or outside label horizon"
        if require_start_market_snapshot and start_snapshot is None:
            return "Start market snapshot is missing"
        if require_end_market_snapshot and end_snapshot is None:
            return "End market snapshot is missing"
        if require_positive_start_price and (
            start_price is None or start_price <= Decimal("0")
        ):
            return "Start market price is missing or invalid"
        if require_positive_end_price and (
            end_price is None or end_price <= Decimal("0")
        ):
            return "End market price is missing or invalid"
        if require_cashflow_schedule and not self._has_cashflow_event(
            bond.id,
            as_of_date,
            target_date,
        ):
            return "Cashflow schedule is missing for label horizon"
        return None

    def _has_cashflow_event(
        self,
        bond_id: int,
        as_of_date: date,
        target_date: date,
        *,
        event_types: set[str] | None = None,
    ) -> bool:
        stmt = select(BondCashflowEvent.id).where(
            BondCashflowEvent.bond_id == bond_id,
            BondCashflowEvent.event_date > as_of_date,
            BondCashflowEvent.event_date <= target_date,
        )
        if event_types is not None:
            stmt = stmt.where(BondCashflowEvent.event_type.in_(event_types))
        return self.db.execute(stmt.limit(1)).scalar_one_or_none() is not None

    def _dirty_value(
        self,
        bond: Bond,
        snapshot: BondMarketSnapshot | None,
        warnings: list[str],
    ) -> Decimal | None:
        if snapshot is None:
            return None
        if bond.nominal_value is None:
            if "Nominal value is missing" not in warnings:
                warnings.append("Nominal value is missing")
            return None
        if snapshot.dirty_price is not None:
            return bond.nominal_value * snapshot.dirty_price / Decimal("100")
        clean_price = snapshot.clean_price if snapshot.clean_price is not None else snapshot.price
        if clean_price is None:
            return None
        clean_value = bond.nominal_value * clean_price / Decimal("100")
        if snapshot.nkd is None:
            if (
                "NKD is missing, clean price was used as dirty value fallback"
                not in warnings
            ):
                warnings.append(
                    "NKD is missing, clean price was used as dirty value fallback"
                )
            return clean_value
        return clean_value + snapshot.nkd

    def _required_risk_premium(
        self,
        bond_id: int,
        as_of_date: date,
    ) -> Decimal | None:
        assessment = self.db.execute(
            select(BondRiskAssessment)
            .where(
                BondRiskAssessment.bond_id == bond_id,
                BondRiskAssessment.as_of_date <= as_of_date,
            )
            .order_by(
                BondRiskAssessment.as_of_date.desc(),
                BondRiskAssessment.created_at.desc(),
                BondRiskAssessment.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()
        if assessment is None:
            return None
        return assessment.required_risk_premium

    @staticmethod
    def _price(snapshot: BondMarketSnapshot | None) -> Decimal | None:
        if snapshot is None:
            return None
        if snapshot.dirty_price is not None:
            return snapshot.dirty_price
        if snapshot.clean_price is not None:
            return snapshot.clean_price
        return snapshot.price

    @staticmethod
    def _price_return(
        start_price: Decimal | None,
        end_price: Decimal | None,
    ) -> Decimal | None:
        if start_price is None or end_price is None or start_price <= 0:
            return None
        return (end_price - start_price) / start_price

    @staticmethod
    def _liquidity_score(
        bond: Bond,
        snapshot: BondMarketSnapshot | None,
    ) -> int | None:
        if snapshot is not None and snapshot.liquidity_score is not None:
            return snapshot.liquidity_score
        return bond.liquidity_score

    @staticmethod
    def _spread_cost(liquidity_score: int | None) -> Decimal:
        if liquidity_score is None:
            return Decimal("0.0030")
        if liquidity_score >= 80:
            return Decimal("0.0005")
        if liquidity_score >= 60:
            return Decimal("0.0010")
        if liquidity_score >= 40:
            return Decimal("0.0025")
        return Decimal("0.0050")

    @staticmethod
    def _json_decimal(value: Decimal | None) -> str | None:
        return None if value is None else str(value)

    @staticmethod
    def _validate_method(return_method: str) -> None:
        if return_method not in RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )

    def _validate_build_request(
        self,
        request: BondTotalReturnLabelBuildRequest,
    ) -> None:
        if request.as_of_date_from > request.as_of_date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if request.max_gap_days_to_start_snapshot < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_gap_days_to_start_snapshot must be non-negative",
            )
        if request.max_gap_days_to_end_snapshot < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_gap_days_to_end_snapshot must be non-negative",
            )
        self._validate_method(request.return_method)

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)
