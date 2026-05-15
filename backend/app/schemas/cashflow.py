from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict


class BondCashflowEventCreate(BaseModel):
    bond_id: int
    event_date: date
    event_type: str
    amount: Decimal | None = None
    amount_percent: Decimal | None = None
    currency: str = "RUB"
    source: str = "manual"
    raw_payload: dict[str, Any] | None = None


class BondCashflowEventRead(BaseModel):
    id: int
    bond_id: int
    event_date: date
    event_type: str
    amount: Decimal | None
    amount_percent: Decimal | None
    currency: str
    source: str
    raw_payload: dict[str, Any] | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class BondCashflowImportResult(BaseModel):
    total: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: list[dict[str, Any]]
    warnings: list[str]


class BondTotalReturnLabelBuildRequest(BaseModel):
    as_of_date_from: date
    as_of_date_to: date
    horizon_days: int = 30
    bond_ids: list[int] | None = None
    return_method: str = "total_return"
    benchmark_return: Decimal | None = None
    transaction_cost_rate: Decimal = Decimal("0.001")
    rebuild_existing: bool = False


class BondTotalReturnLabelBuildResult(BaseModel):
    total: int
    created: int
    updated: int
    skipped: int
    failed: int
    errors: list[dict[str, Any]]
    warnings: list[str]
