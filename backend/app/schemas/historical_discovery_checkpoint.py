"""Task306A4 source-only checkpoint, authorization and progress contracts."""
from datetime import date
from typing import Literal
from pydantic import Field, model_validator, field_validator
from app.schemas.historical_evidence_foundation import Contract, HistoricalEvidencePolicy, HistoricalSourcePage


class DiscoverySourcePage(HistoricalSourcePage):
    completion_basis: Literal["METADATA", "CURSOR", "EMPTY_TERMINATOR"]

    @model_validator(mode="after")
    def consistent(self):
        if self.transient_failures!=self.attempts-1:raise ValueError("INVALID_SOURCE_PAGE")
        if self.completion_basis=="METADATA":
            if self.query.family not in ("DATES","COLUMNS") or self.query.offset!=0 or self.cursor_total is not None or self.cursor_page_size is not None:
                raise ValueError("INVALID_METADATA_COMPLETION")
            expected=None
        elif self.completion_basis=="CURSOR":
            size=self.cursor_page_size
            if type(size) is not int or not 1<=size<=100 or type(self.cursor_total) is not int or self.cursor_total<self.query.offset or len(self.rows)!=min(size,self.cursor_total-self.query.offset):
                raise ValueError("INVALID_SOURCE_CURSOR")
            expected=None if self.query.offset+size>=self.cursor_total else self.query.offset+size
        else:
            if self.query.family not in ("MARKET","LISTING") or len(self.rows)>100 or self.cursor_total is not None or self.cursor_page_size is not None:
                raise ValueError("INVALID_OFFSET_COMPLETION")
            expected=self.query.offset+len(self.rows) if self.rows else None
        if self.complete!=(expected is None) or self.next_offset!=expected:raise ValueError("INVALID_SOURCE_COMPLETION")
        return self


class DiscoveryContract(Contract):
    @field_validator("historical_universe_complete","db_mutation",check_fields=False,mode="before")
    @classmethod
    def exact_false(cls,value):
        if value is not False:raise ValueError("DISCOVERY_SAFETY_DECLARATION_INVALID")
        return value


class DiscoveryLimits(DiscoveryContract):
    max_requests: int = Field(default=100, ge=1, le=100000)
    max_pages: int = Field(default=100, ge=1, le=100000)
    max_seconds: int = Field(default=120, ge=1, le=86400)


class DiscoveryReadAuthorization(DiscoveryContract):
    contract_version: Literal["historical-discovery-read-authorization-v1"] = "historical-discovery-read-authorization-v1"
    operation: Literal["DISCOVERY_READ"] = "DISCOVERY_READ"
    explicit_authorization: Literal[True]
    evidence_root: str
    scope_sha256: str
    implementation_sha256: str
    limits: DiscoveryLimits
    probe_dates: tuple[date, ...] = ()
    authorization_sha256: str

    @field_validator("explicit_authorization",mode="before")
    @classmethod
    def exact_explicit(cls,value):
        if value is not True:raise ValueError("EXPLICIT_AUTHORIZATION_REQUIRED")
        return value

    @model_validator(mode="after")
    def explicit(self):
        if self.explicit_authorization is not True or tuple(sorted(set(self.probe_dates))) != self.probe_dates:
            raise ValueError("DISCOVERY_AUTHORIZATION_INVALID")
        return self


DiscoveryStatus = Literal["COMPLETE", "IN_PROGRESS", "BUDGET_STOP", "SOURCE_FAILED", "CHECKPOINT_INVALID", "AUTHORIZATION_FAILED", "EVIDENCE_INCONSISTENT"]


class DiscoveryProgress(DiscoveryContract):
    contract_version: Literal["historical-discovery-progress-v1"] = "historical-discovery-progress-v1"
    status: DiscoveryStatus
    scope_sha256: str | None = None
    requested_partition_count: int = 0
    completed_partition_count: int = 0
    absent_partition_count: int = 0
    failed_partition_count: int = 0
    incomplete_partition_count: int = 0
    accepted_page_count: int = 0
    http_attempt_count: int = 0
    retry_count: int = 0
    execution_http_attempt_count: int = 0
    execution_page_count: int = 0
    blockers: tuple[str, ...] = ()
    numtrades_filter_semantics_verified: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    historical_universe_complete: Literal[False] = False
    acquisition_ready: Literal[False] = False
    db_mutation: Literal[False] = False
    network_access: bool = False


class DiscoveryManifestIndex(DiscoveryContract):
    contract_version: Literal["historical-discovery-manifest-index-v1"] = "historical-discovery-manifest-index-v1"
    policy: HistoricalEvidencePolicy
    scope_sha256: str
    implementation_sha256: str
    source_content_sha256: str
    source_manifest_sha256: str
    completed_partition_count: int
    absent_partition_count: int
    source_page_count: int
    identity_count: int
    exact_binding_count: int
    listing_identity_count: int
    observed_identity_count: int
    board_count: int
    unresolved_binding_count: int
    identity_conflict_secid_count: int
    market_row_count: int
    actual_trade_row_count: int
    zero_trade_row_count: int
    unknown_trade_row_count: int
    usable_price_row_count: int
    observed_dates: tuple[date, ...]
    index_sha256s: dict[str, str]
    numtrades_filter_semantics_verified: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    historical_universe_complete: Literal[False] = False
    acquisition_ready: Literal[False] = False
    db_mutation: Literal[False] = False


class DiscoveryProbeResult(DiscoveryContract):
    contract_version: Literal["historical-discovery-probe-v1"] = "historical-discovery-probe-v1"
    status: DiscoveryStatus
    scope_sha256: str | None = None
    compared_dates: tuple[date, ...] = ()
    equal_population_dates: tuple[date, ...] = ()
    different_population_dates: tuple[date, ...] = ()
    http_attempt_count: int = 0
    page_count: int = 0
    blockers: tuple[str, ...] = ()
    numtrades_filter_semantics_verified: Literal["NOT_VERIFIED"] = "NOT_VERIFIED"
    db_mutation: Literal[False] = False
    network_access: bool = False
