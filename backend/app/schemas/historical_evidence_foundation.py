"""Task306A3 frozen requests, bounded plans and type-stable receipts."""
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True,revalidate_instances="always")
    pit_ready: Literal[False] = False

    @field_validator("pit_ready",mode="before")
    @classmethod
    def exact_declarations(cls,value):
        if value is not False:raise ValueError("PIT_DECLARATION_INVALID")
        return value


class HistoricalEvidencePolicy(Contract):
    contract_version: Literal["historical-evidence-policy-v1"] = "historical-evidence-policy-v1"
    history_start: date = date(2020, 9, 1)
    cutoff: date
    horizons: tuple[int, int, int] = (90, 180, 365)
    primary_horizon: Literal[365] = 365
    entry_policy: Literal["FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"] = "FIRST_MOEX_TRADE_DATE_PER_CALENDAR_MONTH"
    entry_span: tuple[date, date] = (date(2020, 10, 1), date(2025, 9, 1))
    page_size: Literal[100] = 100
    response_limit_bytes: Literal[8388608] = 8388608
    batch_row_limit: Literal[1000] = 1000
    batch_payload_limit_bytes: Literal[8388608] = 8388608

    @model_validator(mode="after")
    def range_valid(self):
        if self.history_start != date(2020,9,1): raise ValueError("FROZEN_POLICY_OVERRIDE")
        if self.cutoff < self.history_start: raise ValueError("INVALID_HISTORY_RANGE")
        if self.horizons!=(90,180,365) or self.entry_span!=(date(2020,10,1),date(2025,9,1)):raise ValueError("FROZEN_POLICY_OVERRIDE")
        return self


Family = Literal["DATES", "COLUMNS", "LISTING", "MARKET", "DESCRIPTION", "REFERENCE", "CASHFLOWS"]


class HistoricalSourceQuery(Contract):
    family: Family
    table: str
    trade_date: date | None = None
    secid: str | None = None
    expected_isin: str | None = None
    offset: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid(self):
        tables={"DATES":{"dates"},"COLUMNS":{"history"},"LISTING":{"securities"},"MARKET":{"history"},
                "DESCRIPTION":{"description"},"REFERENCE":{"securities"},"CASHFLOWS":{"coupons","amortizations","redemptions","offers"}}
        if self.table not in tables[self.family]: raise ValueError("SOURCE_TABLE_NOT_ALLOWED")
        if (self.family=="MARKET") != (self.trade_date is not None): raise ValueError("SOURCE_DATE_REQUIRED")
        if self.family in ("DESCRIPTION","REFERENCE","CASHFLOWS"):
            if not self.secid or not self.secid.strip() or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for c in self.secid):
                raise ValueError("SOURCE_IDENTITY_REQUIRED")
        elif self.secid is not None or self.expected_isin is not None: raise ValueError("UNEXPECTED_SOURCE_IDENTITY")
        return self


class HistoricalSourcePage(Contract):
    query: HistoricalSourceQuery
    rows: tuple[dict[str, Any], ...]
    observed_at: datetime
    complete: bool
    next_offset: int | None
    cursor_total: int | None = None
    cursor_page_size: int | None = None
    attempts: int = Field(ge=1, le=3)
    transient_failures: int = Field(ge=0, le=2)
    page_sha256: str

    @field_validator("observed_at")
    @classmethod
    def utc(cls,value):
        if value.tzinfo is None or value.utcoffset()!=timezone.utc.utcoffset(value): raise ValueError("UTC_OBSERVATION_REQUIRED")
        return value

    @model_validator(mode="after")
    def consistent(self):
        metadata = self.query.family in ("DATES", "COLUMNS")
        if (not metadata and len(self.rows)>100) or self.transient_failures!=self.attempts-1:raise ValueError("INVALID_SOURCE_PAGE")
        if metadata:
            if self.query.offset != 0 or self.cursor_total is not None or self.cursor_page_size is not None or self.next_offset is not None or not self.complete:
                raise ValueError("INVALID_METADATA_COMPLETION")
            return self
        if self.cursor_total is None:
            if self.cursor_page_size is not None:raise ValueError("INVALID_SOURCE_CURSOR")
            expected=None if len(self.rows)<100 else self.query.offset+100
        else:
            size=self.cursor_page_size
            if type(size) is not int or not 1<=size<=100 or self.cursor_total<self.query.offset or len(self.rows)!=min(size,self.cursor_total-self.query.offset):
                raise ValueError("INVALID_SOURCE_CURSOR")
            expected=None if self.query.offset+size>=self.cursor_total else self.query.offset+size
        if self.next_offset!=expected or self.complete!=(expected is None):raise ValueError("INVALID_SOURCE_COMPLETION")
        return self


class HistoricalSecurity(Contract):
    secid: str
    isin: str | None = None
    board: str | None = None
    interval_start: date | None = None
    interval_end: date | None = None
    authority: Literal["OBSERVED_HISTORY", "SOURCE_LISTING"]


class HistoricalDiscovery(Contract):
    policy: HistoricalEvidencePolicy
    securities: tuple[HistoricalSecurity, ...] = ()
    observed_dates: tuple[date, ...] = ()
    source_page_hashes: tuple[str, ...] = ()
    status: Literal["COMPLETE", "PARTIAL", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    historical_universe_complete: Literal[False] = False
    source_manifest_sha256: str


class BoardBinding(Contract):
    secid: str
    isin: str
    board: str
    start: date
    end: date
    listing_observation_id: int = Field(gt=0)

    @model_validator(mode="after")
    def valid(self):
        if self.end<self.start or not self.board.strip() or not self.secid.strip() or not self.isin.strip():raise ValueError("INVALID_BOARD_BINDING")
        return self


class RepresentativeBinding(Contract):
    bond_id: int = Field(gt=0)
    snapshot_id: int = Field(gt=0)
    secid: str
    isin: str
    selection_date: date
    source_audit_sha256: str


class HistoricalAcquisitionPlan(Contract):
    contract_version: Literal["historical-acquisition-plan-v1"] = "historical-acquisition-plan-v1"
    policy: HistoricalEvidencePolicy
    source_manifest_sha256: str
    queries: tuple[HistoricalSourceQuery, ...]
    representatives: tuple[RepresentativeBinding, ...] = ()
    current_db_sha256: str
    status: Literal["EXECUTABLE", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    plan_sha256: str

    @model_validator(mode="after")
    def scope_valid(self):
        if len(set(self.queries))!=len(self.queries):raise ValueError("DUPLICATE_QUERY")
        if any(q.family=="MARKET" and not self.policy.history_start<=q.trade_date<=self.policy.cutoff for q in self.queries):
            raise ValueError("QUERY_OUTSIDE_FROZEN_RANGE")
        return self


class HistoricalProjectionAction(Contract):
    observation_id: int
    family: Literal["MARKET", "CASHFLOWS"]
    secid: str
    isin: str
    bond_id: int | None
    target_id: int | None
    action: Literal["CREATE", "ENRICH_NULL", "RETAIN", "CONFLICT", "STORAGE_UNREPRESENTABLE", "BLOCKED"]
    values: dict[str, Any]
    before_sha256: str
    issuer_title: str | None = None
    issuer_inn: str | None = None
    blockers: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class HistoricalProjectionPlan(Contract):
    contract_version: Literal["historical-projection-plan-v1"] = "historical-projection-plan-v1"
    policy: HistoricalEvidencePolicy
    source_manifest_sha256: str
    run_sha256: str
    bindings: tuple[BoardBinding, ...]
    observation_ids: tuple[int, ...]
    actions: tuple[HistoricalProjectionAction, ...]
    current_db_sha256: str
    status: Literal["EXECUTABLE", "BLOCKED"]
    blockers: tuple[str, ...] = ()
    plan_sha256: str


class HistoricalAuthorization(Contract):
    operation: Literal["READ_SOURCE", "ACQUIRE", "APPLY"]
    explicit_authorization: Literal[True]
    plan_sha256: str
    source_manifest_sha256: str
    current_db_sha256: str
    scope_sha256: str

    @field_validator("explicit_authorization",mode="before")
    @classmethod
    def explicit(cls,value):
        if value is not True:raise ValueError("EXPLICIT_AUTHORIZATION_REQUIRED")
        return value


class HistoricalBatchReceipt(Contract):
    contract_version: Literal["historical-evidence-batch-receipt-v1"] = "historical-evidence-batch-receipt-v1"
    status: Literal["COMMITTED", "IDEMPOTENT_NOOP", "BLOCKED", "ROLLED_BACK", "COMMIT_OUTCOME_UNKNOWN", "POST_COMMIT_AUDIT_FAILED"]
    operation: Literal["ACQUIRE", "APPLY"]
    plan_sha256: str | None = None
    batch_sha256: str | None = None
    attempted_mutations: int = 0
    committed_mutations: int | None = 0
    commit_count: int | None = 0
    rollback_confirmed: bool = False
    db_mutated: bool | None = False
    blockers: tuple[str, ...] = ()
    failed_partition_sha256s: tuple[str, ...] = ()
    source_attempt_count: int | None = None


class HistoricalFoundationAudit(Contract):
    contract_version: Literal["historical-evidence-foundation-audit-v1"] = "historical-evidence-foundation-audit-v1"
    status: Literal["COMPLETE", "BLOCKED"]
    policy: HistoricalEvidencePolicy | None = None
    run_sha256: str | None = None
    observation_counts: tuple[tuple[str, int], ...] = ()
    observed_range: tuple[date | None, date | None] = (None, None)
    family_ranges: tuple[tuple[str, date | None, date | None], ...] = ()
    unresolved_security_count: int = 0
    unresolved_security_ids: tuple[int, ...] = ()
    unresolved_securities: tuple[tuple[int,str,str | None], ...] = ()
    pending_work_count: int = 0
    pending_partition_sha256s: tuple[str, ...] = ()
    absent_partition_sha256s: tuple[str, ...] = ()
    failed_partition_sha256s: tuple[str, ...] = ()
    failed_work_count: int = 0
    application_count: int = 0
    projection_counts: tuple[tuple[str,int], ...] = ()
    annual_calendar_slot_capacity: int = 0
    limitations: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    audit_sha256: str
    db_mutation: Literal[False] = False
    network_access: Literal[False] = False
