from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_dataset import (
    BondFeatureSnapshotRead,
    BondMarketSnapshotCreate,
    BondMarketSnapshotRead,
    BondReturnLabelRead,
    DatasetBuildRequest,
    DatasetBuildResult,
    DatasetBuildRunRead,
)
from app.services.dataset_build_service import DatasetBuildService
from app.services.market_snapshot_service import MarketSnapshotService


router = APIRouter()


@router.post("/market-snapshots", response_model=BondMarketSnapshotRead)
def create_market_snapshot(
    snapshot_in: BondMarketSnapshotCreate,
    db: Session = Depends(get_db),
) -> BondMarketSnapshotRead:
    return MarketSnapshotService(db).create_or_update(snapshot_in)


@router.get("/market-snapshots", response_model=list[BondMarketSnapshotRead])
def list_market_snapshots(
    bond_id: int | None = Query(default=None, ge=1),
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = Query(default=None, min_length=1, max_length=64),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[BondMarketSnapshotRead]:
    return MarketSnapshotService(db).list_snapshots(
        bond_id=bond_id,
        date_from=date_from,
        date_to=date_to,
        source=source,
        limit=limit,
    )


@router.post("/datasets/build", response_model=DatasetBuildResult)
def build_dataset(
    request: DatasetBuildRequest,
    db: Session = Depends(get_db),
) -> DatasetBuildResult:
    return DatasetBuildService(db).build(request)


@router.get("/datasets/runs", response_model=list[DatasetBuildRunRead])
def list_dataset_runs(
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DatasetBuildRunRead]:
    return DatasetBuildService(db).list_runs(limit=limit)


@router.get("/datasets/features", response_model=list[BondFeatureSnapshotRead])
def list_dataset_features(
    bond_id: int | None = Query(default=None, ge=1),
    company_id: int | None = Query(default=None, ge=1),
    as_of_date_from: date | None = None,
    as_of_date_to: date | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[BondFeatureSnapshotRead]:
    return DatasetBuildService(db).list_features(
        bond_id=bond_id,
        company_id=company_id,
        as_of_date_from=as_of_date_from,
        as_of_date_to=as_of_date_to,
        limit=limit,
    )


@router.get("/datasets/labels", response_model=list[BondReturnLabelRead])
def list_dataset_labels(
    bond_id: int | None = Query(default=None, ge=1),
    horizon_days: int | None = Query(default=None),
    as_of_date_from: date | None = None,
    as_of_date_to: date | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[BondReturnLabelRead]:
    return DatasetBuildService(db).list_labels(
        bond_id=bond_id,
        horizon_days=horizon_days,
        as_of_date_from=as_of_date_from,
        as_of_date_to=as_of_date_to,
        limit=limit,
    )
