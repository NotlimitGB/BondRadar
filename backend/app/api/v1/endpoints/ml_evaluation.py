from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_evaluation import (
    MLModelComparisonResponse,
    MLPredictionEvaluationRowsResponse,
    MLRunEvaluationReport,
)
from app.services.ml_evaluation_service import (
    MLEvaluationFilters,
    MLEvaluationService,
)


router = APIRouter()


@router.get("/runs/{run_id}", response_model=MLRunEvaluationReport)
def evaluate_ml_run(
    run_id: int,
    as_of_date_from: date | None = None,
    as_of_date_to: date | None = None,
    bond_id: int | None = Query(default=None, ge=1),
    company_id: int | None = Query(default=None, ge=1),
    min_probability: float | None = None,
    max_probability: float | None = None,
    bucket_size: float = 0.1,
    db: Session = Depends(get_db),
) -> MLRunEvaluationReport:
    return MLEvaluationService(db).evaluate_run(
        run_id,
        filters=MLEvaluationFilters(
            as_of_date_from=as_of_date_from,
            as_of_date_to=as_of_date_to,
            bond_id=bond_id,
            company_id=company_id,
            min_probability=min_probability,
            max_probability=max_probability,
            bucket_size=bucket_size,
        ),
    )


@router.get("/runs/{run_id}/rows", response_model=MLPredictionEvaluationRowsResponse)
def list_ml_evaluation_rows(
    run_id: int,
    as_of_date_from: date | None = None,
    as_of_date_to: date | None = None,
    bond_id: int | None = Query(default=None, ge=1),
    company_id: int | None = Query(default=None, ge=1),
    min_probability: float | None = None,
    max_probability: float | None = None,
    bucket_size: float = 0.1,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> MLPredictionEvaluationRowsResponse:
    return MLEvaluationService(db).evaluation_rows(
        run_id,
        filters=MLEvaluationFilters(
            as_of_date_from=as_of_date_from,
            as_of_date_to=as_of_date_to,
            bond_id=bond_id,
            company_id=company_id,
            min_probability=min_probability,
            max_probability=max_probability,
            bucket_size=bucket_size,
        ),
        limit=limit,
        offset=offset,
    )


@router.get("/compare", response_model=MLModelComparisonResponse)
def compare_ml_runs(
    run_ids: list[int] | None = Query(default=None),
    return_method: str | None = None,
    limit: int = 20,
    db: Session = Depends(get_db),
) -> MLModelComparisonResponse:
    return MLEvaluationService(db).compare_runs(
        run_ids=run_ids,
        return_method=return_method,
        limit=limit,
    )
