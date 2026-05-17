from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy.orm import Session

from app.services.total_return_label_service import (
    TotalReturnLabelBuildOutcome,
    TotalReturnLabelService,
)


class LabelBuilderService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def build_for_bond_date(
        self,
        bond_id: int,
        as_of_date: date,
        horizon_days: int,
        *,
        return_method: str = "price",
        benchmark_return: Decimal | None = None,
        transaction_cost_rate: Decimal = Decimal("0.001"),
        rebuild_existing: bool = False,
    ) -> TotalReturnLabelBuildOutcome:
        return TotalReturnLabelService(self.db).build_for_bond_date(
            bond_id,
            as_of_date,
            horizon_days,
            return_method=return_method,
            benchmark_return=benchmark_return,
            transaction_cost_rate=transaction_cost_rate,
            rebuild_existing=rebuild_existing,
        )
