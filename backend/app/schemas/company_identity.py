from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


IssuerRole = Literal[
    "legal_issuer",
    "spv",
    "finance_subsidiary",
    "operating_company",
    "parent_group",
    "unknown",
]
IdentityStatus = Literal["unknown", "weak", "matched", "verified", "conflict"]
IdentitySource = Literal[
    "moex_iss",
    "operator_csv",
    "operator_json",
    "manual_review",
    "existing_company",
    "mixed",
]
ReviewStatus = Literal["pending", "reviewed", "accepted", "rejected"]
DuplicateMatchType = Literal[
    "exact_inn",
    "exact_ogrn",
    "exact_legal_name",
    "normalized_name",
    "bond_name_phrase",
    "same_group_name",
    "manual_review",
    "mixed",
]
DuplicateStatus = Literal[
    "candidate",
    "accepted",
    "rejected",
    "needs_review",
    "conflict",
]


class CompanyIdentityProfileBase(BaseModel):
    legal_name: str | None = Field(default=None, max_length=255)
    short_name: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)
    inn: str | None = Field(default=None, max_length=16)
    ogrn: str | None = Field(default=None, max_length=32)
    kpp: str | None = Field(default=None, max_length=16)
    okpo: str | None = Field(default=None, max_length=16)
    country: str | None = Field(default=None, max_length=64)
    issuer_group_name: str | None = Field(default=None, max_length=255)
    issuer_group_inn: str | None = Field(default=None, max_length=16)
    issuer_role: IssuerRole = "unknown"
    identity_status: IdentityStatus = "unknown"
    identity_confidence: Decimal | None = Field(default=None, ge=0, le=1)
    identity_source: IdentitySource = "manual_review"
    source_url: str | None = None
    source_payload: dict[str, Any] | None = None
    review_status: ReviewStatus = "pending"
    review_notes: str | None = None


class CompanyIdentityInputRow(CompanyIdentityProfileBase):
    company_id: int = Field(..., ge=1)
    current_company_name: str | None = Field(default=None, max_length=255)
    source_file_name: str | None = Field(default=None, max_length=255)


class CompanyIdentityPreviewRequest(BaseModel):
    rows: list[CompanyIdentityInputRow]
    rebuild_existing: bool = False


class CompanyIdentityApplyRequest(CompanyIdentityPreviewRequest):
    confirm_apply: bool = False
    allow_conflicts: bool = False


class CompanyIdentityProfileRead(CompanyIdentityProfileBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CompanyIdentityRowMessage(BaseModel):
    row_index: int | None = None
    company_id: int | None = None
    code: str
    message: str


class CompanyIdentityPreviewRow(BaseModel):
    row_index: int
    company_id: int
    matched_company_name: str | None = None
    current_company_fields: dict[str, Any] = Field(default_factory=dict)
    existing_identity_profile: dict[str, Any] | None = None
    proposed_identity_fields: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    would_create_identity_profile: bool = False
    would_update_identity_profile: bool = False
    would_update_company: bool = False


class CompanyIdentityPreviewResult(BaseModel):
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    would_create_identity_profiles: int
    would_update_identity_profiles: int
    would_update_companies: int
    rows: list[CompanyIdentityPreviewRow]
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityApplyRow(BaseModel):
    row_index: int
    company_id: int
    action: str
    company_updated: bool = False
    conflicts: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityAffectedRowsSummary(BaseModel):
    affected_company_ids: list[int] = Field(default_factory=list)
    created_profile_count: int = 0
    updated_profile_count: int = 0
    updated_company_count: int = 0
    skipped_count: int = 0
    conflict_count: int = 0
    warning_count: int = 0


class CompanyIdentityApplyResult(BaseModel):
    status: str
    total_rows: int
    created: int
    updated: int
    company_updates: int
    skipped: int
    failed: int
    affected_rows_summary: CompanyIdentityAffectedRowsSummary
    rows: list[CompanyIdentityApplyRow]
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityDiagnosticsWarning(BaseModel):
    code: str
    message: str


class CompanyIdentityTopUnknownIssuer(BaseModel):
    company_id: int
    company_name: str
    ticker: str | None = None
    inn: str | None = None
    bonds_count: int
    sample_secids: list[str] = Field(default_factory=list)
    sample_bond_names: list[str] = Field(default_factory=list)
    identity_status: str
    identity_confidence: Decimal | None = None


class CompanyIdentityDiagnosticsResult(BaseModel):
    status: str
    company_count: int
    unknown_company_count: int
    missing_inn_count: int
    weak_identity_count: int
    verified_identity_count: int
    companies_with_unknown_name: int
    companies_with_moex_generated_ticker: int
    companies_with_financial_reports_and_weak_identity: int
    top_unknown_issuers: list[CompanyIdentityTopUnknownIssuer] = Field(default_factory=list)
    warnings: list[CompanyIdentityDiagnosticsWarning] = Field(default_factory=list)


class CompanyIdentityDuplicateWarning(BaseModel):
    code: str
    message: str


class CompanyIdentityDuplicateCandidateSummary(BaseModel):
    company_id: int
    company_name: str
    match_score: Decimal
    match_type: str
    match_reasons: list[str] = Field(default_factory=list)
    sample_secids: list[str] = Field(default_factory=list)
    sample_bond_names: list[str] = Field(default_factory=list)
    recommended_action: str = "review"
    persisted_status: str | None = None
    review_status: str | None = None


class CompanyIdentityDuplicateGroup(BaseModel):
    group_key: str
    canonical_company_id: int
    canonical_company_name: str
    canonical_identity_status: str
    candidates: list[CompanyIdentityDuplicateCandidateSummary] = Field(default_factory=list)


class CompanyIdentityDuplicateDiagnosticsResult(BaseModel):
    status: str
    candidate_group_count: int
    candidate_pair_count: int
    high_confidence_count: int
    medium_confidence_count: int
    low_confidence_count: int
    groups: list[CompanyIdentityDuplicateGroup] = Field(default_factory=list)
    warnings: list[CompanyIdentityDuplicateWarning] = Field(default_factory=list)


class CompanyIdentityDuplicateReviewRow(BaseModel):
    canonical_company_id: int = Field(..., ge=1)
    canonical_company_name: str | None = None
    candidate_company_id: int = Field(..., ge=1)
    candidate_company_name: str | None = None
    match_type: DuplicateMatchType = "manual_review"
    match_score: Decimal = Field(default=Decimal("0.5000"), ge=0, le=1)
    match_reasons: list[str] = Field(default_factory=list)
    sample_secids: list[str] = Field(default_factory=list)
    sample_bond_names: list[str] = Field(default_factory=list)
    status: DuplicateStatus = "needs_review"
    review_status: ReviewStatus = "pending"
    review_notes: str | None = None
    source: str = Field(default="manual_review", min_length=1, max_length=64)


class CompanyIdentityDuplicatePreviewRequest(BaseModel):
    rows: list[CompanyIdentityDuplicateReviewRow]
    allow_conflicts: bool = False
    allow_weak_canonical: bool = False


class CompanyIdentityDuplicateApplyRequest(CompanyIdentityDuplicatePreviewRequest):
    confirm_apply: bool = False


class CompanyIdentityDuplicatePreviewRow(BaseModel):
    row_index: int
    canonical_company_id: int
    candidate_company_id: int
    current_canonical_company: dict[str, Any] | None = None
    current_candidate_company: dict[str, Any] | None = None
    canonical_identity_profile: dict[str, Any] | None = None
    candidate_identity_profile: dict[str, Any] | None = None
    existing_duplicate_candidate: dict[str, Any] | None = None
    proposed_duplicate_fields: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    would_create_duplicate_candidate: bool = False
    would_update_duplicate_candidate: bool = False
    would_update_identity_profile: bool = False
    would_update_company: bool = False


class CompanyIdentityDuplicatePreviewResult(BaseModel):
    status: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    would_create_duplicate_candidates: int
    would_update_duplicate_candidates: int
    rows: list[CompanyIdentityDuplicatePreviewRow]
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityDuplicateApplyRow(BaseModel):
    row_index: int
    canonical_company_id: int
    candidate_company_id: int
    action: str
    conflicts: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityDuplicateAffectedRowsSummary(BaseModel):
    affected_candidate_ids: list[int] = Field(default_factory=list)
    affected_pairs: list[dict[str, int]] = Field(default_factory=list)
    created_candidate_count: int = 0
    updated_candidate_count: int = 0
    skipped_count: int = 0
    conflict_count: int = 0
    warning_count: int = 0


class CompanyIdentityDuplicateApplyResult(BaseModel):
    status: str
    total_rows: int
    created: int
    updated: int
    skipped: int
    failed: int
    affected_rows_summary: CompanyIdentityDuplicateAffectedRowsSummary
    rows: list[CompanyIdentityDuplicateApplyRow]
    errors: list[CompanyIdentityRowMessage] = Field(default_factory=list)
    warnings: list[CompanyIdentityRowMessage] = Field(default_factory=list)


class CompanyIdentityResolutionWarning(BaseModel):
    code: str
    message: str
    company_id: int | None = None


class CompanyIdentityResolution(BaseModel):
    company_id: int
    company_name: str
    canonical_company_id: int
    canonical_company_name: str
    is_canonical: bool
    is_duplicate_candidate: bool
    duplicate_mapping_status: str | None = None
    duplicate_review_status: str | None = None
    duplicate_match_type: str | None = None
    duplicate_match_score: Decimal | None = None
    warnings: list[CompanyIdentityResolutionWarning] = Field(default_factory=list)


class CompanyIdentityCanonicalDuplicateMember(BaseModel):
    company_id: int
    company_name: str
    ticker: str | None = None
    inn: str | None = None
    duplicate_mapping_status: str
    duplicate_review_status: str
    duplicate_match_type: str
    duplicate_match_score: Decimal


class CompanyIdentityCanonicalGroup(BaseModel):
    canonical_company_id: int
    canonical_company_name: str
    canonical_ticker: str | None = None
    canonical_inn: str | None = None
    canonical_identity_status: str | None = None
    duplicate_count: int
    duplicate_company_ids: list[int] = Field(default_factory=list)
    duplicate_members: list[CompanyIdentityCanonicalDuplicateMember] = Field(default_factory=list)
    warnings: list[CompanyIdentityResolutionWarning] = Field(default_factory=list)


class CompanyIdentityCanonicalGroupsResult(BaseModel):
    status: str
    group_count: int
    duplicate_mapping_count: int
    conflict_count: int
    groups: list[CompanyIdentityCanonicalGroup] = Field(default_factory=list)
    warnings: list[CompanyIdentityResolutionWarning] = Field(default_factory=list)
