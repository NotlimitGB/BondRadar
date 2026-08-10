from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


PilotGateStatus = Literal["PASS", "FAIL", "NOT_PROVEN", "NOT_EVALUATED"]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PilotUniverseEvaluationRequest(_StrictModel):
    as_of_date: date
    required_market_trade_date: date
    sample_limit: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def validate_market_date(self) -> "PilotUniverseEvaluationRequest":
        if self.required_market_trade_date > self.as_of_date:
            raise ValueError("required_market_trade_date must not exceed as_of_date")
        return self


class PilotUniverseBondEvaluation(_StrictModel):
    bond_id: int
    isin: str | None
    secid: str | None
    bond_name: str
    company_id: int
    company_name: str | None
    identity_gate: PilotGateStatus
    identity_blockers: list[str]
    legacy_terms_gate: PilotGateStatus
    legacy_terms_blockers: list[str]
    market_gate: PilotGateStatus
    market_blockers: list[str]
    observed_cashflow_gate: PilotGateStatus
    cashflow_blockers: list[str]
    pre_pilot_data_candidate: bool
    credit_gate: PilotGateStatus = "NOT_EVALUATED"
    execution_gate: PilotGateStatus = "NOT_EVALUATED"
    final_pilot_eligibility: bool = False


class PilotUniverseBondSample(_StrictModel):
    bond_id: int
    isin: str | None
    secid: str | None
    identity_gate: PilotGateStatus
    identity_blockers: list[str]
    legacy_terms_gate: PilotGateStatus
    legacy_terms_blockers: list[str]
    market_gate: PilotGateStatus
    market_blockers: list[str]
    observed_cashflow_gate: PilotGateStatus
    cashflow_blockers: list[str]
    pre_pilot_data_candidate: bool


class PilotUniverseSummary(_StrictModel):
    contract_version: str
    as_of_date: date
    required_market_trade_date: date
    bonds_total: int
    identity_pass_count: int
    identity_fail_count: int
    legacy_terms_pass_count: int
    legacy_terms_fail_count: int
    market_pass_count: int
    market_fail_count: int
    observed_cashflow_pass_count: int
    observed_cashflow_fail_count: int
    observed_cashflow_not_proven_count: int
    pre_pilot_data_candidate_count: int
    final_pilot_eligible_count: int
    final_pilot_eligibility_evaluated: bool
    system_capability_blockers: list[str]
    identity_blocker_counts: dict[str, int]
    legacy_terms_blocker_counts: dict[str, int]
    market_blocker_counts: dict[str, int]
    cashflow_blocker_counts: dict[str, int]
    pre_pilot_candidate_samples: list[PilotUniverseBondSample]
    excluded_bond_samples: list[PilotUniverseBondSample]


class PilotUniverseEvaluationResult(_StrictModel):
    contract_version: str
    request: PilotUniverseEvaluationRequest
    summary: PilotUniverseSummary
    bond_evaluations: list[PilotUniverseBondEvaluation]
