from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.crud import bonds as bonds_crud
from app.crud import companies as companies_crud
from app.models.enums import AnalysisSignal
from app.schemas.bond import BondCreate, BondProductRead, BondRead, BondUpdate
from app.schemas.bond_score import BondScoreCalculationRead
from app.services.bond_product_read_service import BondProductReadService
from app.services.bond_score_service import BondScoreService


router = APIRouter()


@router.get("", response_model=list[BondProductRead])
def list_bonds(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    company_id: int | None = Query(default=None, ge=1),
    signal: AnalysisSignal | None = None,
    db: Session = Depends(get_db),
) -> list[BondProductRead]:
    return BondProductReadService(db).list_product_bonds(
        skip=skip,
        limit=limit,
        company_id=company_id,
        signal=signal,
    )


@router.post("", response_model=BondRead, status_code=status.HTTP_201_CREATED)
def create_bond(bond_in: BondCreate, db: Session = Depends(get_db)) -> BondRead:
    if companies_crud.get_company(db, bond_in.company_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company not found.",
        )
    try:
        return bonds_crud.create_bond(db, bond_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bond with the same ISIN already exists.",
        ) from exc


@router.get("/{bond_id}", response_model=BondProductRead)
def get_bond(bond_id: int, db: Session = Depends(get_db)) -> BondProductRead:
    bond = BondProductReadService(db).get_product_bond(bond_id)
    if bond is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bond not found.",
        )
    return bond


@router.post("/{bond_id}/calculate-score", response_model=BondScoreCalculationRead)
def calculate_bond_score(
    bond_id: int,
    db: Session = Depends(get_db),
) -> BondScoreCalculationRead:
    return BondScoreService(db).calculate_for_bond(bond_id)


@router.get("/{bond_id}/score", response_model=BondScoreCalculationRead)
def get_latest_bond_score(
    bond_id: int,
    db: Session = Depends(get_db),
) -> BondScoreCalculationRead:
    return BondScoreService(db).get_latest_score(bond_id)


@router.patch("/{bond_id}", response_model=BondRead)
def update_bond(
    bond_id: int,
    bond_in: BondUpdate,
    db: Session = Depends(get_db),
) -> BondRead:
    bond = bonds_crud.get_bond(db, bond_id)
    if bond is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bond not found.",
        )
    if bond_in.company_id is not None:
        if companies_crud.get_company(db, bond_in.company_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company not found.",
            )
    try:
        return bonds_crud.update_bond(db, bond, bond_in)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Bond with the same ISIN already exists.",
        ) from exc


@router.delete("/{bond_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_bond(bond_id: int, db: Session = Depends(get_db)) -> Response:
    bond = bonds_crud.get_bond(db, bond_id)
    if bond is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bond not found.",
        )
    bonds_crud.delete_bond(db, bond)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
