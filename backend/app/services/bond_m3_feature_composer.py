"""Pure integrity composer for existing M3 bond feature views."""

from datetime import date
from decimal import Decimal

from app.schemas.bond_credit_comparability import BondCreditComparabilityView
from app.schemas.bond_credit_features import BondCreditFeatureView
from app.schemas.bond_dv01 import BondDv01View
from app.schemas.bond_liquidity_features import BondLiquidityFeatureView
from app.schemas.bond_liquidity_relative_value import (
    BondLiquidityAwareRelativeValueView,
)
from app.schemas.bond_m3_feature_view import (
    BondM3FeatureAvailability,
    BondM3FeatureProvenance,
    BondM3FeatureView,
)
from app.schemas.bond_market_features import BondMarketFeatureView
from app.schemas.bond_modified_duration import BondModifiedDurationView
from app.schemas.credit_cohort_peer_distribution_orchestration import (
    CreditCohortPeerDistributionOrchestrationView,
)
from app.schemas.ofz_reference_curve import (
    BondRelativeValueView,
    OfzReferenceCurveView,
)


def _field(value: object, name: str) -> object:
    return getattr(value, name, None)


def _exact_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    try:
        return bool(left == right)
    except Exception:
        return False


def _finite_decimal(value: object) -> bool:
    return type(value) is Decimal and value.is_finite()


def _decimal_evidence_mismatch(left: object, right: object) -> bool:
    if left is not None and not _finite_decimal(left):
        return True
    if right is not None and not _finite_decimal(right):
        return True
    if _finite_decimal(left) and _finite_decimal(right):
        return not _exact_equal(left, right)
    return False


def _valid_bond_id(value: object) -> bool:
    return type(value) is int and value > 0


def _valid_date(value: object) -> bool:
    return type(value) is date


def _valid_source(value: object) -> bool:
    return type(value) is str and bool(value.strip())


def _safe_string(value: object) -> str | None:
    return value if type(value) is str else None


def _safe_int(value: object) -> int | None:
    return value if type(value) is int else None


def _safe_date(value: object) -> date | None:
    return value if type(value) is date else None


def _identity_flags(
    value: object,
    *,
    bond_id: int,
    as_of_date: date,
    market_source: str | None,
) -> list[str]:
    flags: list[str] = []
    value_bond_id = _field(value, "bond_id")
    value_as_of_date = _field(value, "as_of_date")
    if not _valid_bond_id(value_bond_id) or value_bond_id != bond_id:
        flags.append("BOND_IDENTITY_MISMATCH")
    if not _valid_date(value_as_of_date) or value_as_of_date != as_of_date:
        flags.append("AS_OF_DATE_MISMATCH")
    if market_source is not None:
        value_source = _field(value, "market_source")
        if not _valid_source(value_source) or value_source != market_source:
            flags.append("MARKET_SOURCE_MISMATCH")
    return flags


def compose_bond_m3_feature_view(
    *,
    bond_id: int,
    as_of_date: date,
    market_source: str,
    market: BondMarketFeatureView,
    relative_value: BondRelativeValueView,
    credit: BondCreditFeatureView,
    credit_comparability: BondCreditComparabilityView,
    liquidity: BondLiquidityFeatureView,
    modified_duration: BondModifiedDurationView,
    dv01: BondDv01View,
    liquidity_relative_value: BondLiquidityAwareRelativeValueView,
    peer_distribution: CreditCohortPeerDistributionOrchestrationView | None = None,
) -> BondM3FeatureView:
    """Validate and compose already-built M3 evidence without recalculation."""

    if not _valid_bond_id(bond_id):
        raise ValueError("bond_id must be an exact positive int")
    if not _valid_date(as_of_date):
        raise ValueError("as_of_date must be an exact date")
    if not _valid_source(market_source):
        raise ValueError("market_source must be a nonblank string")

    required_types = (
        (market, BondMarketFeatureView, "market"),
        (relative_value, BondRelativeValueView, "relative_value"),
        (credit, BondCreditFeatureView, "credit"),
        (credit_comparability, BondCreditComparabilityView, "credit_comparability"),
        (liquidity, BondLiquidityFeatureView, "liquidity"),
        (modified_duration, BondModifiedDurationView, "modified_duration"),
        (dv01, BondDv01View, "dv01"),
        (
            liquidity_relative_value,
            BondLiquidityAwareRelativeValueView,
            "liquidity_relative_value",
        ),
    )
    for value, expected, name in required_types:
        if type(value) is not expected:
            raise ValueError(f"{name} must be an exact {expected.__name__}")
    if peer_distribution is not None and type(peer_distribution) is not (
        CreditCohortPeerDistributionOrchestrationView
    ):
        raise ValueError(
            "peer_distribution must be an exact "
            "CreditCohortPeerDistributionOrchestrationView or None"
        )

    flags: list[str] = []
    curve = _field(relative_value, "curve")

    version_checks = (
        (market, "bond-market-feature-v1", "MARKET_CONTRACT_INVALID"),
        (
            relative_value,
            "bond-relative-value-v1",
            "RELATIVE_VALUE_CONTRACT_INVALID",
        ),
        (credit, "bond-credit-feature-v1", "CREDIT_CONTRACT_INVALID"),
        (
            credit_comparability,
            "bond-credit-comparability-v1",
            "CREDIT_COMPARABILITY_CONTRACT_INVALID",
        ),
        (liquidity, "bond-liquidity-feature-v1", "LIQUIDITY_CONTRACT_INVALID"),
        (
            modified_duration,
            "bond-modified-duration-v1",
            "MODIFIED_DURATION_CONTRACT_INVALID",
        ),
        (dv01, "bond-dv01-v1", "DV01_CONTRACT_INVALID"),
        (
            liquidity_relative_value,
            "bond-liquidity-aware-relative-value-v1",
            "LIQUIDITY_RELATIVE_VALUE_CONTRACT_INVALID",
        ),
    )
    for value, expected_version, flag in version_checks:
        if _field(value, "contract_version") != expected_version:
            flags.append(flag)
    if (
        type(curve) is not OfzReferenceCurveView
        or _field(curve, "contract_version") != "ofz-reference-curve-v1"
    ):
        flags.append("OFZ_CURVE_CONTRACT_INVALID")
    if peer_distribution is not None and _field(
        peer_distribution, "contract_version"
    ) != "credit-cohort-peer-distribution-orchestration-v1":
        flags.append("PEER_DISTRIBUTION_CONTRACT_INVALID")

    pit_inputs = [value for value, _, _ in version_checks]
    pit_inputs.append(curve)
    if peer_distribution is not None:
        pit_inputs.append(peer_distribution)
    if any(_field(value, "pit_ready") is not False for value in pit_inputs):
        flags.append("PIT_CONTRACT_INVALID")

    for value in (
        market,
        relative_value,
        credit,
        credit_comparability,
        liquidity,
        modified_duration,
        dv01,
        liquidity_relative_value,
    ):
        source = market_source if value in (
            market,
            relative_value,
            liquidity,
            modified_duration,
            dv01,
            liquidity_relative_value,
        ) else None
        flags.extend(
            _identity_flags(
                value,
                bond_id=bond_id,
                as_of_date=as_of_date,
                market_source=source,
            )
        )
    if type(curve) is OfzReferenceCurveView:
        if not _valid_date(_field(curve, "as_of_date")) or _field(
            curve, "as_of_date"
        ) != as_of_date:
            flags.append("AS_OF_DATE_MISMATCH")
        if not _valid_source(_field(curve, "market_source")) or _field(
            curve, "market_source"
        ) != market_source:
            flags.append("MARKET_SOURCE_MISMATCH")

    relative_provenance = _field(relative_value, "provenance")
    market_relative_mismatch = (
        not _exact_equal(_field(relative_value, "bond_id"), _field(market, "bond_id"))
        or not _exact_equal(
            _field(relative_value, "as_of_date"), _field(market, "as_of_date")
        )
        or not _exact_equal(
            _field(relative_value, "market_source"), _field(market, "market_source")
        )
        or not _exact_equal(
            _field(relative_provenance, "target_market_snapshot_id"),
            _field(market, "market_snapshot_id"),
        )
        or not _exact_equal(
            _field(relative_provenance, "target_market_trade_date"),
            _field(market, "market_trade_date"),
        )
        or _decimal_evidence_mismatch(
            _field(relative_value, "target_yield_to_maturity_pct"),
            _field(market, "yield_to_maturity_pct"),
        )
        or _decimal_evidence_mismatch(
            _field(relative_value, "target_duration_years"),
            _field(market, "duration_years"),
        )
    )
    if market_relative_mismatch:
        flags.append("MARKET_RELATIVE_VALUE_EVIDENCE_MISMATCH")

    comparison_provenance = _field(credit_comparability, "provenance")
    credit_comparison_mismatch = (
        not _exact_equal(
            _field(credit_comparability, "bond_id"), _field(credit, "bond_id")
        )
        or not _exact_equal(
            _field(credit_comparability, "as_of_date"), _field(credit, "as_of_date")
        )
        or not _exact_equal(
            _field(comparison_provenance, "credit_feature_contract_version"),
            _field(credit, "contract_version"),
        )
        or not _exact_equal(
            _field(comparison_provenance, "credit_feature_bond_id"),
            _field(credit, "bond_id"),
        )
        or not _exact_equal(
            _field(comparison_provenance, "credit_feature_as_of_date"),
            _field(credit, "as_of_date"),
        )
    )
    if (
        _field(credit, "issuer_link_status") == "VERIFIED"
        and _field(credit, "legal_issuer_id") is not None
        and _field(comparison_provenance, "legal_issuer_id") is not None
        and not _exact_equal(
            _field(comparison_provenance, "legal_issuer_id"),
            _field(credit, "legal_issuer_id"),
        )
    ):
        credit_comparison_mismatch = True
    if credit_comparison_mismatch:
        flags.append("CREDIT_COMPARABILITY_EVIDENCE_MISMATCH")

    liquidity_provenance = _field(liquidity, "provenance")
    if (
        not _exact_equal(
            _field(liquidity_provenance, "as_of_date"), _field(liquidity, "as_of_date")
        )
        or not _exact_equal(
            _field(liquidity_provenance, "market_source"),
            _field(liquidity, "market_source"),
        )
    ):
        flags.append("LIQUIDITY_CONTRACT_INVALID")

    duration_provenance = _field(modified_duration, "provenance")
    duration_market_mismatch = (
        not _exact_equal(_field(modified_duration, "bond_id"), _field(market, "bond_id"))
        or not _exact_equal(
            _field(modified_duration, "as_of_date"), _field(market, "as_of_date")
        )
        or not _exact_equal(
            _field(modified_duration, "market_source"), _field(market, "market_source")
        )
        or not _exact_equal(
            _field(modified_duration, "market_snapshot_id"),
            _field(market, "market_snapshot_id"),
        )
        or not _exact_equal(
            _field(modified_duration, "market_trade_date"),
            _field(market, "market_trade_date"),
        )
        or not _exact_equal(
            _field(duration_provenance, "market_snapshot_id"),
            _field(modified_duration, "market_snapshot_id"),
        )
        or not _exact_equal(
            _field(duration_provenance, "market_trade_date"),
            _field(modified_duration, "market_trade_date"),
        )
        or not _exact_equal(
            _field(duration_provenance, "market_source"),
            _field(modified_duration, "market_source"),
        )
        or not _exact_equal(
            _field(duration_provenance, "as_of_date"),
            _field(modified_duration, "as_of_date"),
        )
    )
    if duration_market_mismatch:
        flags.append("MODIFIED_DURATION_MARKET_EVIDENCE_MISMATCH")

    dv01_provenance = _field(dv01, "provenance")
    dv01_market_mismatch = (
        not _exact_equal(_field(dv01, "bond_id"), _field(market, "bond_id"))
        or not _exact_equal(_field(dv01, "as_of_date"), _field(market, "as_of_date"))
        or not _exact_equal(
            _field(dv01, "market_source"), _field(market, "market_source")
        )
        or not _exact_equal(
            _field(dv01, "market_snapshot_id"), _field(market, "market_snapshot_id")
        )
        or not _exact_equal(
            _field(dv01, "market_trade_date"), _field(market, "market_trade_date")
        )
        or not _exact_equal(
            _field(dv01_provenance, "market_snapshot_id"),
            _field(dv01, "market_snapshot_id"),
        )
        or not _exact_equal(
            _field(dv01_provenance, "market_trade_date"),
            _field(dv01, "market_trade_date"),
        )
        or not _exact_equal(
            _field(dv01_provenance, "market_source"), _field(dv01, "market_source")
        )
        or not _exact_equal(
            _field(dv01_provenance, "as_of_date"), _field(dv01, "as_of_date")
        )
    )
    for identity_name in ("market_identity", "modified_duration_market_identity"):
        identity = _field(dv01_provenance, identity_name)
        if (
            not _exact_equal(_field(identity, "bond_id"), _field(dv01, "bond_id"))
            or not _exact_equal(
                _field(identity, "market_snapshot_id"),
                _field(dv01, "market_snapshot_id"),
            )
            or not _exact_equal(
                _field(identity, "market_trade_date"),
                _field(dv01, "market_trade_date"),
            )
            or not _exact_equal(
                _field(identity, "market_source"), _field(dv01, "market_source")
            )
        ):
            dv01_market_mismatch = True
    if dv01_market_mismatch:
        flags.append("DV01_MARKET_EVIDENCE_MISMATCH")

    if (
        not _exact_equal(
            _field(dv01, "modified_duration_status"),
            _field(modified_duration, "status"),
        )
        or _decimal_evidence_mismatch(
            _field(dv01, "modified_duration_years"),
            _field(modified_duration, "modified_duration_years"),
        )
    ):
        flags.append("DV01_MODIFIED_DURATION_EVIDENCE_MISMATCH")

    liquidity_relative_mismatch = any(
        not _exact_equal(_field(liquidity_relative_value, target), _field(source, field))
        for target, source, field in (
            ("relative_value_status", relative_value, "status"),
            ("spread_to_ofz_pp", relative_value, "spread_to_ofz_pp"),
            ("spread_to_ofz_bps", relative_value, "spread_to_ofz_bps"),
            (
                "reference_ofz_yield_pct",
                relative_value,
                "reference_ofz_yield_pct",
            ),
            ("interpolation_method", relative_value, "interpolation_method"),
            ("liquidity_status", liquidity, "score_status"),
            ("liquidity_score_v1", liquidity, "liquidity_score_v1"),
        )
    )
    score_components = _field(liquidity, "score_components")
    for field in (
        "turnover_percentile",
        "trade_count_percentile",
        "recency_percentile",
    ):
        if not _exact_equal(
            _field(liquidity_relative_value, field), _field(score_components, field)
        ):
            liquidity_relative_mismatch = True
    if liquidity_relative_mismatch:
        flags.append("LIQUIDITY_RELATIVE_VALUE_EVIDENCE_MISMATCH")

    if peer_distribution is not None:
        peer_mismatch = (
            not _exact_equal(_field(peer_distribution, "target_bond_id"), bond_id)
            or not _exact_equal(_field(peer_distribution, "as_of_date"), as_of_date)
            or not _exact_equal(_field(peer_distribution, "market_source"), market_source)
        )
        distribution = _field(peer_distribution, "distribution")
        if distribution is not None:
            if not _exact_equal(_field(distribution, "target_bond_id"), bond_id):
                peer_mismatch = True
            distribution_date = _field(distribution, "as_of_date")
            distribution_source = _field(distribution, "market_source")
            if distribution_date is not None and not _exact_equal(
                distribution_date, as_of_date
            ):
                peer_mismatch = True
            if distribution_source is not None and not _exact_equal(
                distribution_source, market_source
            ):
                peer_mismatch = True
        if peer_mismatch:
            flags.append("PEER_DISTRIBUTION_EVIDENCE_MISMATCH")

    sorted_flags = sorted(set(flags))
    status = "EVIDENCE_INVALID" if sorted_flags else "CONSISTENT"
    market_identity_matches = not _identity_flags(
        market,
        bond_id=bond_id,
        as_of_date=as_of_date,
        market_source=market_source,
    )

    credit_availability = _field(credit, "availability")
    comparison_availability = _field(credit_comparability, "availability")
    availability = BondM3FeatureAvailability(
        market_fresh=_field(market, "market_status") == "FRESH",
        relative_value_ready=_field(relative_value, "status") == "READY",
        has_credit_rating_evidence=(
            _field(credit_availability, "has_bond_rating_event") is True
            or _field(credit_availability, "has_issuer_rating_event") is True
        ),
        has_credit_comparable_cohort=_field(
            comparison_availability, "has_any_comparable_cohort"
        )
        is True,
        liquidity_score_ready=_field(liquidity, "score_status") == "READY",
        modified_duration_ready=_field(modified_duration, "status") == "READY",
        dv01_ready=_field(dv01, "status") == "READY",
        liquidity_relative_value_ready=(
            _field(liquidity_relative_value, "status") == "READY"
        ),
        has_peer_distribution_context=peer_distribution is not None,
        peer_distribution_ready=(
            peer_distribution is not None
            and _field(peer_distribution, "status") == "READY"
        ),
        all_supplied_evidence_consistent=status == "CONSISTENT",
    )

    return BondM3FeatureView(
        bond_id=bond_id,
        as_of_date=as_of_date,
        market_source=market_source,
        isin=_field(market, "isin") if market_identity_matches else None,
        secid=_field(market, "secid") if market_identity_matches else None,
        status=status,
        market=market,
        relative_value=relative_value,
        credit=credit,
        credit_comparability=credit_comparability,
        liquidity=liquidity,
        modified_duration=modified_duration,
        dv01=dv01,
        liquidity_relative_value=liquidity_relative_value,
        peer_distribution=peer_distribution,
        availability=availability,
        quality_flags=sorted_flags,
        provenance=BondM3FeatureProvenance(
            market_contract_version=_safe_string(_field(market, "contract_version")),
            relative_value_contract_version=_safe_string(
                _field(relative_value, "contract_version")
            ),
            ofz_curve_contract_version=_safe_string(
                _field(curve, "contract_version")
            ),
            credit_contract_version=_safe_string(_field(credit, "contract_version")),
            credit_comparability_contract_version=_safe_string(
                _field(credit_comparability, "contract_version")
            ),
            liquidity_contract_version=_safe_string(
                _field(liquidity, "contract_version")
            ),
            modified_duration_contract_version=_safe_string(
                _field(modified_duration, "contract_version")
            ),
            dv01_contract_version=_safe_string(_field(dv01, "contract_version")),
            liquidity_relative_value_contract_version=_safe_string(
                _field(liquidity_relative_value, "contract_version")
            ),
            peer_distribution_contract_version=(
                _safe_string(_field(peer_distribution, "contract_version"))
                if peer_distribution is not None
                else None
            ),
            requested_bond_id=bond_id,
            requested_as_of_date=as_of_date,
            requested_market_source=market_source,
            market_snapshot_id=_safe_int(_field(market, "market_snapshot_id")),
            market_trade_date=_safe_date(_field(market, "market_trade_date")),
            curve_trade_date=_safe_date(_field(curve, "curve_trade_date")),
            peer_context_supplied=peer_distribution is not None,
        ),
    )
