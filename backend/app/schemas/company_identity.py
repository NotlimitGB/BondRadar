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
