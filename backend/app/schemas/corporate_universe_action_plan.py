from __future__ import annotations

from datetime import date as Date, datetime
from typing import Any

from pydantic import BaseModel, Field


CORPORATE_UNIVERSE_PLAN_STATUSES = {"ready", "needs_sync", "blocked"}
CORPORATE_UNIVERSE_ACTION_STATUSES = {
    "recommended",
    "optional",
    "blocked",
    "not_needed",
}


class CorporateUniverseQualityCheck(BaseModel):
    name: str
    status: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class CorporateUniverseAction(BaseModel):
    name: str
    status: str
    reason: str
    details: dict[str, Any] = Field(default_factory=dict)


class CorporateUniverseCommand(BaseModel):
    label: str
    method: str
    path: str
    body: dict[str, Any] | None = None
    description: str


class CorporateUniverseSyncPayloadPreview(BaseModel):
    secids: list[str] | None = None
    board: str
    date: Date | None = None
    active_only: bool
    create_missing_companies: bool
    rebuild_existing: bool
    max_pages: int
    page_size: int


class CorporateUniverseActionPlanResponse(BaseModel):
    status: str
    as_of: datetime

    board: str
    include_ofz: bool

    local_total_bond_count: int
    local_corporate_bond_count: int
    local_ofz_bond_count: int
    local_working_bond_count: int
    local_company_count: int

    bonds_with_secid_count: int
    bonds_with_isin_count: int
    bonds_with_company_count: int

    sample_corporate_bonds: list[dict[str, Any]]
    sample_ofz_bonds: list[dict[str, Any]]

    checks: list[CorporateUniverseQualityCheck]
    actions: list[CorporateUniverseAction]
    commands: list[CorporateUniverseCommand]

    sync_payload: dict[str, Any]
    curl_example: str

    can_sync_universe: bool
    can_continue_to_data_pipeline: bool

    warnings: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    next_steps: list[str]
