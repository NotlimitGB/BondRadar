from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.data_pipeline import (
    DataPipelineRunRead,
    DataPipelineRunRequest,
    DataPipelineRunResult,
    DataPipelineStepRunRead,
)
from app.services.data_pipeline_service import DataPipelineService


router = APIRouter()


@router.post("/run", response_model=DataPipelineRunResult)
def run_data_pipeline(
    request: DataPipelineRunRequest,
    db: Session = Depends(get_db),
) -> DataPipelineRunResult:
    return DataPipelineService(db).run(request)


@router.get("/runs", response_model=list[DataPipelineRunRead])
def list_data_pipeline_runs(
    status: str | None = None,
    mode: str | None = None,
    limit: int = Query(default=20, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[DataPipelineRunRead]:
    return DataPipelineService(db).list_runs(
        status_filter=status,
        mode=mode,
        limit=limit,
    )


@router.get("/runs/{run_id}", response_model=DataPipelineRunRead)
def get_data_pipeline_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> DataPipelineRunRead:
    return DataPipelineService(db).get_run(run_id)


@router.get("/runs/{run_id}/steps", response_model=list[DataPipelineStepRunRead])
def list_data_pipeline_steps(
    run_id: int,
    db: Session = Depends(get_db),
) -> list[DataPipelineStepRunRead]:
    return DataPipelineService(db).list_steps(run_id)
