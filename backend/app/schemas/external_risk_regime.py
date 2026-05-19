from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


EXTERNAL_RISK_REGIME_MODES = {"normal", "elevated", "severe"}


class ExternalRiskRegimeUpdateRequest(BaseModel):
    mode: str
    reason: str | None = None
    source: str = "manual"
    expires_at: datetime | None = None


class ExternalRiskRegimeResponse(BaseModel):
    id: int | None = None
    mode: str
    reason: str
    source: str
    is_active: bool
    expires_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)
