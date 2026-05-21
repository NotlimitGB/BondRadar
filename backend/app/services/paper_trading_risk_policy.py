from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status

from app.schemas.portfolio_construction import (
    PORTFOLIO_DECISION_STATUSES,
    PORTFOLIO_RISK_LEVELS,
)


PAPER_RISK_OVERRIDE_HIGH_RISK_WEIGHT_THRESHOLD = Decimal("0.20")

PAPER_RISK_POLICY_FIELDS = (
    "top_n",
    "min_probability_positive",
    "max_position_weight",
    "max_issuer_weight",
    "max_high_risk_weight",
    "min_liquidity_score",
    "exclude_blocked_by_risk",
    "exclude_insufficient_credit_data",
    "allowed_risk_levels",
    "allowed_decision_statuses",
)


def paper_risk_policy_payload(request: Any) -> dict[str, Any]:
    return {field: getattr(request, field) for field in PAPER_RISK_POLICY_FIELDS}


def risk_override_triggers(request: Any) -> list[str]:
    triggers: list[str] = []
    if getattr(request, "exclude_blocked_by_risk", True) is False:
        triggers.append("exclude_blocked_by_risk=false")
    if (
        getattr(request, "max_high_risk_weight", Decimal("0"))
        > PAPER_RISK_OVERRIDE_HIGH_RISK_WEIGHT_THRESHOLD
    ):
        triggers.append("max_high_risk_weight>0.20")
    if "critical" in set(getattr(request, "allowed_risk_levels", None) or []):
        triggers.append("allowed_risk_levels includes critical")
    if "blocked_by_risk" in set(
        getattr(request, "allowed_decision_statuses", None) or []
    ):
        triggers.append("allowed_decision_statuses includes blocked_by_risk")
    return triggers


def validate_paper_risk_policy(request: Any) -> None:
    invalid_risk = (
        set(getattr(request, "allowed_risk_levels", None) or [])
        - PORTFOLIO_RISK_LEVELS
    )
    if invalid_risk:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid risk level",
        )
    invalid_decision = (
        set(getattr(request, "allowed_decision_statuses", None) or [])
        - PORTFOLIO_DECISION_STATUSES
    )
    if invalid_decision:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid decision status",
        )

    triggers = risk_override_triggers(request)
    if not triggers:
        return
    if not getattr(request, "risk_override_enabled", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "risk_override_enabled is required for relaxed paper risk policy: "
                + ", ".join(triggers)
            ),
        )
    reason = (getattr(request, "risk_override_reason", None) or "").strip()
    if not reason:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="risk_override_reason is required when risk_override_enabled is true",
        )


def risk_override_metadata(request: Any) -> dict[str, Any]:
    return {
        "risk_override_enabled": getattr(request, "risk_override_enabled", False),
        "risk_override_reason": getattr(request, "risk_override_reason", None),
        "risk_override_triggers": risk_override_triggers(request),
    }


def risk_override_warning(request: Any) -> dict[str, Any] | None:
    metadata = risk_override_metadata(request)
    if not metadata["risk_override_enabled"]:
        return None
    return {
        "message": "Paper risk override is enabled for this virtual pilot configuration",
        "details": {
            **metadata,
            "risk_policy": paper_risk_policy_payload(request),
        },
    }
