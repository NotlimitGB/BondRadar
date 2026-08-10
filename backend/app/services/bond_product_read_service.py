from __future__ import annotations

from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.crud import bonds as bonds_crud
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.schemas.bond import (
    BondIssuerRead,
    BondMarketRead,
    BondProductRead,
    BondRead,
    BondRiskRead,
)


class BondProductReadService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_product_bonds(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        company_id: int | None = None,
        signal: AnalysisSignal | None = None,
    ) -> list[BondProductRead]:
        bonds = bonds_crud.list_bonds(
            self.db,
            skip=skip,
            limit=limit,
            company_id=company_id,
            signal=signal,
        )
        return self._assemble(bonds)

    def get_product_bond(self, bond_id: int) -> BondProductRead | None:
        bond = bonds_crud.get_bond(self.db, bond_id)
        if bond is None:
            return None
        return self._assemble([bond])[0]

    def _assemble(self, bonds: Sequence[Bond]) -> list[BondProductRead]:
        if not bonds:
            return []

        bond_ids = [bond.id for bond in bonds]
        company_ids = {bond.company_id for bond in bonds}
        issuers = self._load_issuers(company_ids)
        missing_issuer_ids = company_ids - issuers.keys()
        if missing_issuer_ids:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Bond issuer not found.",
            )

        latest_market = self._load_latest_market(bond_ids)
        latest_risk = self._load_latest_risk(bond_ids)
        return [
            self._build_product_read(
                bond,
                issuer=issuers[bond.company_id],
                market=latest_market.get(bond.id),
                risk=latest_risk.get(bond.id),
            )
            for bond in bonds
        ]

    def _load_issuers(self, company_ids: set[int]) -> dict[int, Company]:
        rows = self.db.execute(
            select(Company).where(Company.id.in_(company_ids))
        ).scalars()
        return {company.id: company for company in rows}

    def _load_latest_market(
        self,
        bond_ids: Sequence[int],
    ) -> dict[int, BondMarketSnapshot]:
        rows = self.db.execute(self._latest_market_statement(bond_ids)).scalars()
        return {snapshot.bond_id: snapshot for snapshot in rows}

    def _load_latest_risk(
        self,
        bond_ids: Sequence[int],
    ) -> dict[int, BondRiskAssessment]:
        rows = self.db.execute(self._latest_risk_statement(bond_ids)).scalars()
        return {assessment.bond_id: assessment for assessment in rows}

    @staticmethod
    def _latest_market_statement(bond_ids: Sequence[int]):
        source_priority = case((BondMarketSnapshot.source == "moex", 0), else_=1)
        ranked = (
            select(
                BondMarketSnapshot.id.label("snapshot_id"),
                func.row_number()
                .over(
                    partition_by=BondMarketSnapshot.bond_id,
                    order_by=(
                        BondMarketSnapshot.trade_date.desc(),
                        source_priority.asc(),
                        BondMarketSnapshot.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(BondMarketSnapshot.bond_id.in_(bond_ids))
            .subquery()
        )
        return (
            select(BondMarketSnapshot)
            .join(ranked, ranked.c.snapshot_id == BondMarketSnapshot.id)
            .where(ranked.c.row_number == 1)
        )

    @staticmethod
    def _latest_risk_statement(bond_ids: Sequence[int]):
        ranked = (
            select(
                BondRiskAssessment.id.label("assessment_id"),
                func.row_number()
                .over(
                    partition_by=BondRiskAssessment.bond_id,
                    order_by=(
                        BondRiskAssessment.as_of_date.desc(),
                        BondRiskAssessment.created_at.desc(),
                        BondRiskAssessment.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(BondRiskAssessment.bond_id.in_(bond_ids))
            .subquery()
        )
        return (
            select(BondRiskAssessment)
            .join(ranked, ranked.c.assessment_id == BondRiskAssessment.id)
            .where(ranked.c.row_number == 1)
        )

    @staticmethod
    def _build_product_read(
        bond: Bond,
        *,
        issuer: Company,
        market: BondMarketSnapshot | None,
        risk: BondRiskAssessment | None,
    ) -> BondProductRead:
        bond_payload = BondRead.model_validate(bond).model_dump()
        bond_payload.update(
            current_price=BondProductReadService._prefer_market(
                market.price if market is not None else None,
                bond.current_price,
            ),
            yield_to_maturity=BondProductReadService._prefer_market(
                market.yield_to_maturity if market is not None else None,
                bond.yield_to_maturity,
            ),
            duration_years=BondProductReadService._prefer_market(
                market.duration_years if market is not None else None,
                bond.duration_years,
            ),
            volume=BondProductReadService._prefer_market(
                market.volume if market is not None else None,
                bond.volume,
            ),
            liquidity_score=BondProductReadService._prefer_market(
                market.liquidity_score if market is not None else None,
                bond.liquidity_score,
            ),
        )
        return BondProductRead(
            **bond_payload,
            issuer=BondIssuerRead.model_validate(issuer),
            latest_market=(
                BondMarketRead.model_validate(market) if market is not None else None
            ),
            latest_risk=(BondRiskRead.model_validate(risk) if risk is not None else None),
        )

    @staticmethod
    def _prefer_market(market_value, bond_value):
        return market_value if market_value is not None else bond_value
