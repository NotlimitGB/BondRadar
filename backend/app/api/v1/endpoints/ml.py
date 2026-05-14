from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.ml_model import (
    MLModelRunRead,
    MLPredictionRequest,
    MLPredictionResponse,
    MLTrainRequest,
    MLTrainResult,
)
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService


router = APIRouter()


@router.post("/train", response_model=MLTrainResult)
def train_ml_model(
    request: MLTrainRequest,
    db: Session = Depends(get_db),
) -> MLTrainResult:
    return MLTrainingService(db).train(request)


@router.get("/runs", response_model=list[MLModelRunRead])
def list_ml_model_runs(
    limit: int = Query(default=20, ge=1, le=200),
    status: str | None = None,
    model_type: str | None = None,
    db: Session = Depends(get_db),
) -> list[MLModelRunRead]:
    return MLTrainingService(db).list_runs(
        limit=limit,
        status_filter=status,
        model_type=model_type,
    )


@router.get("/runs/{run_id}", response_model=MLModelRunRead)
def get_ml_model_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> MLModelRunRead:
    return MLTrainingService(db).get_run(run_id)


@router.post("/predict", response_model=MLPredictionResponse)
def predict_with_ml_model(
    request: MLPredictionRequest,
    db: Session = Depends(get_db),
) -> MLPredictionResponse:
    return MLPredictionService(db).predict(request)


@router.get("/predictions", response_model=MLPredictionResponse)
def list_ml_predictions(
    model_run_id: int | None = None,
    bond_id: int | None = None,
    company_id: int | None = None,
    as_of_date_from: date | None = None,
    as_of_date_to: date | None = None,
    limit: int = 100,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> MLPredictionResponse:
    return MLPredictionService(db).list_predictions(
        model_run_id=model_run_id,
        bond_id=bond_id,
        company_id=company_id,
        as_of_date_from=as_of_date_from,
        as_of_date_to=as_of_date_to,
        limit=limit,
        offset=offset,
    )
