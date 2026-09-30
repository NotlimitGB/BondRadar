"""Frozen authorization, plan, execution and audit contracts for Task297."""

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tinvest_bond_import_preflight import BondImportBatchIdentity


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class ControlledBondImportAuthorization(Frozen):
    contract_version: Literal["controlled-bond-import-authorization-v1"] = "controlled-bond-import-authorization-v1"
    expected_ready_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_batch_identity: BondImportBatchIdentity
    pit_ready: Literal[False] = False


class ImportPlanStatus(StrEnum):
    EXECUTABLE = "EXECUTABLE"
    NOT_EXECUTABLE = "NOT_EXECUTABLE"


class ImportAction(StrEnum):
    CREATE = "CREATE"
    ALREADY_PRESENT_EXACT = "ALREADY_PRESENT_EXACT"
    BLOCKED = "BLOCKED"


class ImportExecutionStatus(StrEnum):
    BLOCKED = "BLOCKED"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    COMMIT_OUTCOME_UNKNOWN = "COMMIT_OUTCOME_UNKNOWN"
    APPLIED_AUDIT_FAILED = "APPLIED_AUDIT_FAILED"


class ImportAuditStatus(StrEnum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"


class PersistedBondValues(Frozen):
    isin: str
    secid: str
    name: str
    currency: Literal["RUB"] = "RUB"
    nominal_value: Decimal
    maturity_date: date | None
    is_perpetual: bool
    coupon_rate: Decimal | None
    offer_date: date | None
    is_subordinated: bool
    amortization: bool | None
    is_floating_coupon: Literal[False] = False
    signal: Literal["insufficient_data"] = "insufficient_data"


class CompanyImportAction(Frozen):
    inn: str
    name: str
    ticker: str
    action: ImportAction
    company_id: int | None = Field(default=None, gt=0)
    country: Literal["RU"] = "RU"
    signal: Literal["insufficient_data"] = "insufficient_data"


class BondImportAction(Frozen):
    isin: str
    secid: str
    issuer_inn: str
    action: ImportAction
    bond_id: int | None = Field(default=None, gt=0)
    company_id: int | None = Field(default=None, gt=0)
    values: PersistedBondValues | None = None
    unknown_source_fields: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()


class ControlledImportCapabilities(Frozen):
    plan_ready: Literal[True] = True
    explicit_authorization_required: Literal[True] = True
    atomic_apply_ready: Literal[True] = True
    post_import_audit_ready: Literal[True] = True
    verified_security_master_created: Literal[False] = False
    production_import_authorized: Literal[False] = False
    network_access: Literal[False] = False
    sync_called: Literal[False] = False
    scoring_ready: Literal[False] = False
    ranking_ready: Literal[False] = False
    recommendations_ready: Literal[False] = False
    pit_ready: Literal[False] = False


class ControlledBondImportPlan(Frozen):
    contract_version: Literal["controlled-bond-import-plan-v1"] = "controlled-bond-import-plan-v1"
    status: ImportPlanStatus = ImportPlanStatus.NOT_EXECUTABLE
    ready_sha256: str | None = None
    batch_identity: BondImportBatchIdentity | None = None
    authorization: ControlledBondImportAuthorization | None = None
    companies: tuple[CompanyImportAction, ...] = ()
    bonds: tuple[BondImportAction, ...] = ()
    planned_company_creates: int = Field(default=0, ge=0)
    planned_bond_creates: int = Field(default=0, ge=0)
    already_present_bonds: int = Field(default=0, ge=0)
    blockers: tuple[str, ...] = ()
    diagnostics: tuple[str, ...] = ()
    plan_sha256: str | None = None
    capabilities: ControlledImportCapabilities = Field(default_factory=ControlledImportCapabilities)
    pit_ready: Literal[False] = False


class BondImportAuditRow(Frozen):
    isin: str
    secid: str
    bond_id: int | None = Field(default=None, gt=0)
    company_id: int | None = Field(default=None, gt=0)
    verified: bool
    diagnostics: tuple[str, ...] = ()


class ControlledBondImportAudit(Frozen):
    contract_version: Literal["controlled-bond-import-audit-v1"] = "controlled-bond-import-audit-v1"
    status: ImportAuditStatus
    ready_sha256: str | None = None
    rows: tuple[BondImportAuditRow, ...] = ()
    expected_bond_count: int = Field(default=0, ge=0)
    verified_bond_count: int = Field(default=0, ge=0)
    committed_company_creates: int | None = Field(default=None, ge=0)
    committed_bond_creates: int | None = Field(default=None, ge=0)
    reused_company_count: int | None = Field(default=None, ge=0)
    already_present_bond_count: int | None = Field(default=None, ge=0)
    unexpected_mutation_count: int | None = Field(default=None, ge=0)
    diagnostics: tuple[str, ...] = ()
    pit_ready: Literal[False] = False


class ControlledBondImportExecutionResult(Frozen):
    contract_version: Literal["controlled-bond-import-execution-v1"] = "controlled-bond-import-execution-v1"
    status: ImportExecutionStatus
    ready_sha256: str | None = None
    plan_sha256: str | None = None
    batch_identity: BondImportBatchIdentity | None = None
    planned_company_creates: int = Field(default=0, ge=0)
    planned_bond_creates: int = Field(default=0, ge=0)
    attempted_company_creates: int = Field(default=0, ge=0)
    attempted_bond_creates: int = Field(default=0, ge=0)
    committed_company_creates: int | None = Field(default=0, ge=0)
    committed_bond_creates: int | None = Field(default=0, ge=0)
    reused_company_count: int = Field(default=0, ge=0)
    already_present_bond_count: int = Field(default=0, ge=0)
    created_company_ids: tuple[Annotated[int, Field(gt=0)], ...] = ()
    created_bond_ids: tuple[Annotated[int, Field(gt=0)], ...] = ()
    db_mutated: bool | None = False
    commit_count: int | None = Field(default=0, ge=0)
    rollback_confirmed: bool = False
    pre_commit_audit: ControlledBondImportAudit | None = None
    post_commit_audit: ControlledBondImportAudit | None = None
    diagnostics: tuple[str, ...] = ()
    capabilities: ControlledImportCapabilities = Field(default_factory=ControlledImportCapabilities)
    pit_ready: Literal[False] = False
