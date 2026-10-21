"""Task308B2: source-only, separately authorized cashflow qualification."""
from datetime import datetime, date
from decimal import Decimal
from typing import Literal
from pydantic import Field
from app.schemas.historical_identity_feasibility import Contract, ProbeLimits

Table = Literal["coupons", "amortizations", "redemptions", "offers"]
TABLES = ("coupons", "amortizations", "redemptions", "offers")


class Policy(Contract):
    contract_version: Literal["bounded-cashflow-policy-v1"] = "bounded-cashflow-policy-v1"
    policy_version: Literal["bounded-cashflow-policy-v1"] = "bounded-cashflow-policy-v1"
    maximum_secids: Literal[6] = 6
    maximum_partitions: Literal[24] = 24
    completeness: Literal["EXPLICIT_CURSOR_REQUIRED"] = "EXPLICIT_CURSOR_REQUIRED"
    amount_storage: Literal["NUMERIC_18_6_NO_ROUNDING"] = "NUMERIC_18_6_NO_ROUNDING"
    joint_execution_coverage: Literal["NOT_ESTABLISHED"] = "NOT_ESTABLISHED"
    actual_payments_confirmed: Literal[False] = False
    historical_backtest_ready: Literal[False] = False
    total_return_execution_ready: Literal[False] = False
    executable_market_ready: Literal[False] = False


class Candidate(Contract):
    secid: str
    accepted_current_isin: str
    selection_order: int
    selection_reasons: tuple[str, ...]
    observed: bool
    canonical_ofz: bool
    actual_trade_indicator: bool
    usable_price_nkd_indicator: bool
    boards: tuple[str, ...]
    strata: tuple[str, ...]
    lineage_sha256: str
    request_sha256s: tuple[str, ...]
    response_sha256s: tuple[str, ...]
    observations: tuple[datetime, ...]


class PilotPlan(Policy):
    contract_version: Literal["bounded-cashflow-plan-v1"] = "bounded-cashflow-plan-v1"
    status: Literal["PLANNED", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    requested_size: int = Field(ge=1, le=6)
    cohort: tuple[Candidate, ...] = ()
    tables: tuple[Table, ...] = TABLES
    upstream_hashes: tuple[tuple[str, str], ...] = ()
    exclusions: tuple[tuple[str, int], ...] = ()
    recommended_limits: ProbeLimits = Field(default_factory=ProbeLimits)
    plan_sha256: str = ""


class Authorization(Contract):
    contract_version: Literal["bounded-cashflow-authorization-v1"] = "bounded-cashflow-authorization-v1"
    explicit_confirmation: Literal[True]
    plan_sha256: str
    secids: tuple[str, ...]
    tables: tuple[Table, ...]
    limits: ProbeLimits
    destination_sha256: str
    authorization_sha256: str = ""


class Request(Contract):
    secid: str
    table: Table
    offset: int = Field(ge=0)


class Intent(Contract):
    sequence: int
    authorization_sha256: str
    request: Request
    previous_receipt_sha256: str | None
    started_at: datetime
    intent_sha256: str = ""


class Receipt(Contract):
    sequence: int
    intent_sha256: str
    request: Request
    previous_receipt_sha256: str | None
    observed_at: datetime
    status: Literal["ACCEPTED", "SOURCE_ERROR", "BUDGET_STOP"]
    category: str
    retryable: bool
    retry_after: int = Field(ge=0, le=60)
    received_bytes: int = Field(ge=0)
    body_sha256: str | None
    response_sha256: str | None = None
    complete: bool = False
    next_offset: int | None = None
    cursor_total: int | None = None
    cursor_page_size: int | None = None
    row_count: int = 0
    content_sha256: str | None = None
    receipt_sha256: str = ""


class ProbeResult(Policy):
    contract_version: Literal["bounded-cashflow-probe-v1"] = "bounded-cashflow-probe-v1"
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    authorization_sha256: str
    receipt_sha256s: tuple[str, ...] = ()
    attempts: int = 0
    accepted_pages: int = 0
    received_bytes: int = 0
    probe_sha256: str = ""


class ProbeState(Contract):
    authorization_sha256: str
    receipt_sha256s: tuple[str, ...] = ()
    pending_sequence: int | None = None
    deadline_at: datetime
    stop_reason: Literal["GLOBAL_BUDGET_STOP"] | None = None
    state_sha256: str = ""


class Event(Contract):
    requested_secid: str
    accepted_current_isin_context: str
    table: Table
    offset: int
    row_index: int
    row_sha256: str
    page_sha256: str
    returned_source_secid: str | None
    returned_source_isin: str | None
    source_identity_asserted: bool
    source_identity_conflict: bool
    binding_provenance: Literal["RETURNED_IDENTITY", "REQUEST_CONTEXT_ONLY"]
    event_date: date | None
    amount: Decimal | None
    currency: str | None
    amount_unit: str | None
    unit_authority: Literal["UNPROVEN"] = "UNPROVEN"
    status: Literal["SCHEDULED_OBSERVATION", "EVENT_INVALID"]
    diagnostics: tuple[str, ...]
    source_context: tuple[tuple[str, str | None], ...]


class Partition(Contract):
    secid: str
    table: Table
    execution: Literal["NOT_REQUESTED", "NOT_STARTED", "SOURCE_ERROR", "INCOMPLETE", "COMPLETE"]
    completeness: Literal["PROVEN", "UNPROVEN"]
    attempts: int
    pages: int
    raw_rows: int
    valid_rows: int
    invalid_rows: int
    date_rows: int
    amount_rows: int
    currency_rows: int
    unit_rows: int = 0
    duplicate_occurrences: int = 0
    conflicting_occurrences: int = 0
    diagnostics: tuple[str, ...]


class Evaluation(Policy):
    contract_version: Literal["bounded-cashflow-evaluation-v1"] = "bounded-cashflow-evaluation-v1"
    status: Literal["CASHFLOW_SOURCE_FEASIBLE_WITH_LIMITATIONS", "SOURCE_FEASIBILITY_UNPROVEN", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    plan_sha256: str
    authorization_sha256: str
    probe_sha256: str
    upstream_hashes: tuple[tuple[str, str], ...] = ()
    cohort: tuple[Candidate, ...] = ()
    partitions: tuple[Partition, ...] = ()
    events: tuple[Event, ...] = ()
    duplicate_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    conflicting_event_groups: tuple[tuple[str, tuple[str, ...]], ...] = ()
    economic_qualification: Literal["UNPROVEN", "CONFLICTING"] = "UNPROVEN"
    limitations: tuple[str, ...] = ("SCHEDULED_NOT_ACTUAL_PAYMENT", "AMOUNT_UNIT_AUTHORITY_UNPROVEN", "HISTORICAL_PUBLICATION_UNPROVEN")
    report_sha256: str = ""
