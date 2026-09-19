"""Read-only orchestration of explicit-universe Task282/Task283 evidence."""

from collections.abc import Mapping, Sequence, Set
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.schemas.bond_liquidity_batch_features import (
    BondLiquidityBatchFeatureView,
)
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.schemas.m3_audit_snapshot import (
    M3AuditSnapshotDiagnostics,
    M3AuditSnapshotItem,
    M3AuditSnapshotProvenance,
    M3AuditSnapshotView,
)
from app.schemas.m3_coverage_audit import M3CoverageAuditView
from app.services.bond_credit_comparability_service import (
    BondCreditComparabilityService,
)
from app.services.bond_credit_feature_service import BondCreditFeatureService
from app.services.bond_dv01_service import BondDv01Service
from app.services.bond_liquidity_batch_feature_service import (
    BondLiquidityBatchFeatureService,
)
from app.services.bond_liquidity_relative_value_composer import (
    compose_bond_liquidity_aware_relative_value,
)
from app.services.bond_m3_feature_composer import compose_bond_m3_feature_view
from app.services.bond_market_feature_service import BondMarketFeatureService
from app.services.bond_modified_duration_service import (
    BondModifiedDurationService,
)
from app.services.m3_coverage_audit_reducer import M3CoverageAuditReducer
from app.services.ofz_reference_curve_service import OfzReferenceCurveService
from app.services.ofz_relative_value_evaluator import (
    evaluate_market_against_ofz_curve,
)


def _validate_request(
    bond_ids: Sequence[int],
    as_of_date: date,
    market_source: str,
    max_market_age_days: int,
    max_curve_age_days: int,
    liquidity_lookback_calendar_days: int,
    liquidity_min_observation_days: int,
) -> tuple[int, ...]:
    if (
        not isinstance(bond_ids, Sequence)
        or isinstance(
            bond_ids,
            (str, bytes, bytearray, memoryview, Mapping, Set),
        )
    ):
        raise ValueError("bond_ids must be a deterministic sequence")
    requested_ids = tuple(bond_ids)
    if not requested_ids:
        raise ValueError("bond_ids must be nonempty")
    if any(type(bond_id) is not int or bond_id <= 0 for bond_id in requested_ids):
        raise ValueError("bond_ids must contain exact positive integers")
    if len(set(requested_ids)) != len(requested_ids):
        raise ValueError("bond_ids must not contain duplicates")
    if type(as_of_date) is not date:
        raise ValueError("as_of_date must be a calendar date")
    if type(market_source) is not str or market_source != "moex":
        raise ValueError("market_source must be exactly moex")
    for name, value in (
        ("max_market_age_days", max_market_age_days),
        ("max_curve_age_days", max_curve_age_days),
    ):
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a nonnegative integer")
    if (
        type(liquidity_lookback_calendar_days) is not int
        or liquidity_lookback_calendar_days <= 0
    ):
        raise ValueError(
            "liquidity_lookback_calendar_days must be a positive integer"
        )
    if (
        type(liquidity_min_observation_days) is not int
        or liquidity_min_observation_days <= 0
        or liquidity_min_observation_days > liquidity_lookback_calendar_days
    ):
        raise ValueError(
            "liquidity_min_observation_days must be positive and no larger "
            "than the window"
        )
    try:
        as_of_date - timedelta(days=liquidity_lookback_calendar_days - 1)
    except OverflowError as exc:
        raise ValueError("Lookback window is outside the calendar date range") from exc
    return tuple(sorted(requested_ids))


def _validate_batch(
    batch: BondLiquidityBatchFeatureView,
    *,
    requested_ids: tuple[int, ...],
    as_of_date: date,
    market_source: str,
    lookback_days: int,
    minimum_days: int,
) -> dict[int, BondLiquidityFeatureView]:
    if type(batch) is not BondLiquidityBatchFeatureView:
        raise ValueError("Task285 returned an invalid batch type")
    if batch.contract_version != "bond-liquidity-batch-feature-v1":
        raise ValueError("Task285 returned an unsupported contract version")
    if batch.pit_ready is not False:
        raise ValueError("Task285 PIT contract must be false")
    if (
        type(batch.as_of_date) is not date
        or batch.as_of_date != as_of_date
        or type(batch.market_source) is not str
        or batch.market_source != market_source
        or batch.lookback_calendar_days != lookback_days
        or batch.min_observation_days != minimum_days
    ):
        raise ValueError("Task285 request identity is inconsistent")

    requested = tuple(batch.requested_bond_ids)
    existing = tuple(batch.existing_bond_ids)
    missing = tuple(batch.missing_bond_ids)
    if requested != requested_ids:
        raise ValueError("Task285 requested bond partition is inconsistent")
    for values in (requested, existing, missing):
        if (
            any(type(value) is not int or value <= 0 for value in values)
            or tuple(sorted(set(values))) != values
        ):
            raise ValueError("Task285 bond partitions must be sorted and unique")
    if set(existing).intersection(missing) or tuple(sorted(existing + missing)) != requested:
        raise ValueError("Task285 existing/missing partition is inconsistent")
    if (
        batch.requested_bond_count != len(requested)
        or batch.existing_bond_count != len(existing)
        or batch.missing_bond_count != len(missing)
        or batch.built_feature_count != len(existing)
        or batch.ready_feature_count + batch.unavailable_feature_count != len(existing)
    ):
        raise ValueError("Task285 batch counts are inconsistent")
    expected_status = (
        "NO_EXISTING_BONDS"
        if not existing
        else "PARTIAL"
        if missing
        else "COMPLETE"
    )
    if batch.status != expected_status:
        raise ValueError("Task285 batch status is inconsistent")
    if [item.bond_id for item in batch.items] != list(requested):
        raise ValueError("Task285 batch items are inconsistent")

    features: dict[int, BondLiquidityFeatureView] = {}
    existing_set = set(existing)
    for item in batch.items:
        if item.bond_id in existing_set:
            if (
                item.build_status != "BUILT"
                or type(item.feature) is not BondLiquidityFeatureView
                or item.feature.bond_id != item.bond_id
                or item.feature.as_of_date != as_of_date
                or item.feature.market_source != market_source
            ):
                raise ValueError("Task285 built item is inconsistent")
            features[item.bond_id] = item.feature
        elif item.build_status != "BOND_NOT_FOUND" or item.feature is not None:
            raise ValueError("Task285 missing item is inconsistent")
    if tuple(features) != existing:
        raise ValueError("Task285 feature projection is inconsistent")
    actual_ready_count = sum(
        feature.score_status == "READY" for feature in features.values()
    )
    if (
        batch.ready_feature_count != actual_ready_count
        or batch.unavailable_feature_count != len(existing) - actual_ready_count
    ):
        raise ValueError("Task285 readiness counts are inconsistent")

    expected_shared_count = 1 if existing else 0
    diagnostics = batch.diagnostics
    if (
        diagnostics.requested_identity_query_count != 1
        or diagnostics.market_window_query_count != expected_shared_count
        or diagnostics.universe_evaluation_count != expected_shared_count
        or diagnostics.feature_projection_count != len(existing)
    ):
        raise ValueError("Task285 execution diagnostics are inconsistent")
    return features


def _validate_audit(
    audit: M3CoverageAuditView,
    existing_ids: tuple[int, ...],
    as_of_date: date,
    market_source: str,
) -> None:
    if type(audit) is not M3CoverageAuditView:
        raise ValueError("Task283 returned an invalid audit type")
    if (
        audit.contract_version != "m3-coverage-audit-v1"
        or audit.pit_ready is not False
        or audit.as_of_date != as_of_date
        or audit.market_source != market_source
        or audit.universe_size != len(existing_ids)
        or audit.bond_ids != list(existing_ids)
        or audit.consistent_composite_count + audit.invalid_composite_count
        != len(existing_ids)
    ):
        raise ValueError("Task283 audit envelope is inconsistent")


class M3AuditSnapshotService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build(
        self,
        bond_ids: Sequence[int],
        as_of_date: date,
        *,
        market_source: str = "moex",
        max_market_age_days: int = 7,
        max_curve_age_days: int = 7,
        liquidity_lookback_calendar_days: int = 30,
        liquidity_min_observation_days: int = 5,
    ) -> M3AuditSnapshotView:
        requested_ids = _validate_request(
            bond_ids,
            as_of_date,
            market_source,
            max_market_age_days,
            max_curve_age_days,
            liquidity_lookback_calendar_days,
            liquidity_min_observation_days,
        )

        counts = dict.fromkeys(M3AuditSnapshotDiagnostics.model_fields, 0)
        counts["liquidity_batch_call_count"] = 1
        curve = None
        audit = None
        composites = {}

        with self.db.no_autoflush:
            batch = BondLiquidityBatchFeatureService(self.db).build_for_bonds(
                requested_ids,
                as_of_date,
                market_source=market_source,
                lookback_calendar_days=liquidity_lookback_calendar_days,
                min_observation_days=liquidity_min_observation_days,
            )
            liquidity = _validate_batch(
                batch,
                requested_ids=requested_ids,
                as_of_date=as_of_date,
                market_source=market_source,
                lookback_days=liquidity_lookback_calendar_days,
                minimum_days=liquidity_min_observation_days,
            )
            counts["liquidity_market_window_query_count"] = (
                batch.diagnostics.market_window_query_count
            )
            counts["liquidity_universe_evaluation_count"] = (
                batch.diagnostics.universe_evaluation_count
            )
            existing_ids = tuple(batch.existing_bond_ids)
            missing_ids = tuple(batch.missing_bond_ids)

            if existing_ids:
                curve = OfzReferenceCurveService(self.db).build_curve(
                    as_of_date,
                    market_source=market_source,
                    max_curve_age_days=max_curve_age_days,
                )
                counts["ofz_curve_build_count"] = 1
                market_service = BondMarketFeatureService(self.db)
                credit_service = BondCreditFeatureService(self.db)
                comparison_service = BondCreditComparabilityService(self.db)
                duration_service = BondModifiedDurationService(self.db)
                dv01_service = BondDv01Service(self.db)

                for bond_id in existing_ids:
                    market = market_service.build_for_bond(
                        bond_id,
                        as_of_date,
                        market_source=market_source,
                        max_market_age_days=max_market_age_days,
                    )
                    counts["market_feature_call_count"] += 1
                    relative = evaluate_market_against_ofz_curve(
                        market,
                        curve,
                        as_of_date=as_of_date,
                        market_source=market_source,
                    )
                    counts["relative_evaluator_call_count"] += 1
                    credit = credit_service.build_for_bond(bond_id, as_of_date)
                    counts["credit_feature_call_count"] += 1
                    comparison = comparison_service.build_for_bond(
                        bond_id, as_of_date
                    )
                    counts["credit_comparability_call_count"] += 1
                    modified_duration = duration_service.build_for_bond(
                        bond_id,
                        as_of_date,
                        market_source=market_source,
                        max_market_age_days=max_market_age_days,
                    )
                    counts["modified_duration_call_count"] += 1
                    dv01 = dv01_service.build_for_bond(
                        bond_id,
                        as_of_date,
                        market_source=market_source,
                        max_market_age_days=max_market_age_days,
                    )
                    counts["dv01_call_count"] += 1
                    liquidity_relative = (
                        compose_bond_liquidity_aware_relative_value(
                            relative,
                            liquidity[bond_id],
                            bond_id=bond_id,
                            as_of_date=as_of_date,
                            market_source=market_source,
                        )
                    )
                    counts["liquidity_relative_value_composer_call_count"] += 1
                    composites[bond_id] = compose_bond_m3_feature_view(
                        bond_id=bond_id,
                        as_of_date=as_of_date,
                        market_source=market_source,
                        market=market,
                        relative_value=relative,
                        credit=credit,
                        credit_comparability=comparison,
                        liquidity=liquidity[bond_id],
                        modified_duration=modified_duration,
                        dv01=dv01,
                        liquidity_relative_value=liquidity_relative,
                        peer_distribution=None,
                    )
                    counts["m3_composer_call_count"] += 1

                audit = M3CoverageAuditReducer.build(
                    tuple(composites[bond_id] for bond_id in existing_ids)
                )
                counts["coverage_reducer_call_count"] = 1
                _validate_audit(audit, existing_ids, as_of_date, market_source)

        items = [
            M3AuditSnapshotItem(
                bond_id=bond_id,
                build_status=("BUILT" if bond_id in composites else "BOND_NOT_FOUND"),
                composite=composites.get(bond_id),
            )
            for bond_id in requested_ids
        ]
        composite_count = len(composites)
        consistent_count = sum(
            composite.status == "CONSISTENT" for composite in composites.values()
        )
        invalid_count = composite_count - consistent_count
        if audit is not None and (
            audit.consistent_composite_count != consistent_count
            or audit.invalid_composite_count != invalid_count
        ):
            raise ValueError("Task283 audit composite counts are inconsistent")

        return M3AuditSnapshotView(
            as_of_date=as_of_date,
            market_source=market_source,
            max_market_age_days=max_market_age_days,
            max_curve_age_days=max_curve_age_days,
            liquidity_lookback_calendar_days=liquidity_lookback_calendar_days,
            liquidity_min_observation_days=liquidity_min_observation_days,
            status=batch.status,
            requested_bond_ids=list(requested_ids),
            existing_bond_ids=list(existing_ids),
            missing_bond_ids=list(missing_ids),
            requested_bond_count=len(requested_ids),
            existing_bond_count=len(existing_ids),
            missing_bond_count=len(missing_ids),
            composite_count=composite_count,
            consistent_composite_count=consistent_count,
            invalid_composite_count=invalid_count,
            items=items,
            coverage_audit=audit,
            diagnostics=M3AuditSnapshotDiagnostics(**counts),
            provenance=M3AuditSnapshotProvenance(
                shared_ofz_curve_contract_version=(
                    curve.contract_version if curve is not None else None
                ),
                shared_ofz_curve_status=curve.status if curve is not None else None,
                shared_ofz_curve_trade_date=(
                    curve.curve_trade_date if curve is not None else None
                ),
                shared_ofz_curve_node_count=(
                    curve.node_count if curve is not None else None
                ),
                requested_bond_ids=list(requested_ids),
                existing_bond_ids=list(existing_ids),
                missing_bond_ids=list(missing_ids),
            ),
        )
