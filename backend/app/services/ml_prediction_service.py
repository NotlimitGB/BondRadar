from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from math import nan
from pathlib import Path
from typing import Any

import joblib
from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.ml_model import (
    MLPredictionRead,
    MLPredictionRequest,
    MLPredictionResponse,
)


class MLPredictionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def predict(self, request: MLPredictionRequest) -> MLPredictionResponse:
        self._validate_prediction_request(request)
        run = self._get_completed_run(request.model_run_id)
        artifact = self._load_artifact(run)
        features = list(artifact["features"])
        model = artifact["model"]
        total = self._count_feature_snapshots(
            bond_id=request.bond_id,
            company_id=request.company_id,
            as_of_date_from=request.as_of_date_from,
            as_of_date_to=request.as_of_date_to,
        )
        snapshots = self._list_feature_snapshots(
            bond_id=request.bond_id,
            company_id=request.company_id,
            as_of_date_from=request.as_of_date_from,
            as_of_date_to=request.as_of_date_to,
            limit=request.limit,
            offset=request.offset,
        )
        if not snapshots:
            return MLPredictionResponse(
                model_run_id=run.id,
                total=total,
                limit=request.limit,
                offset=request.offset,
                predictions=[],
            )

        matrix = [self._model_vector(snapshot, features) for snapshot in snapshots]
        probabilities = model.predict_proba(matrix)
        positive_index = list(model.classes_).index(1)
        predictions: list[MLPredictionRead] = []
        for snapshot, probability_row in zip(snapshots, probabilities):
            probability = float(probability_row[positive_index])
            predicted_label = (
                "predicted_positive_return"
                if probability >= 0.5
                else "predicted_negative_return"
            )
            feature_payload = self._feature_payload(snapshot, features)
            if request.save_predictions:
                prediction = self._upsert_prediction(
                    run=run,
                    snapshot=snapshot,
                    probability=probability,
                    predicted_label=predicted_label,
                    feature_payload=feature_payload,
                )
                predictions.append(MLPredictionRead.model_validate(prediction))
            else:
                predictions.append(
                    MLPredictionRead(
                        id=None,
                        model_run_id=run.id,
                        feature_snapshot_id=snapshot.id,
                        bond_id=snapshot.bond_id,
                        company_id=snapshot.company_id,
                        as_of_date=snapshot.as_of_date,
                        horizon_days=run.horizon_days,
                        probability_positive=Decimal(str(probability)),
                        predicted_label=predicted_label,
                        features=feature_payload,
                        created_at=None,
                    )
                )
        if request.save_predictions:
            self.db.commit()
        return MLPredictionResponse(
            model_run_id=run.id,
            total=total,
            limit=request.limit,
            offset=request.offset,
            predictions=predictions,
        )

    def list_predictions(
        self,
        *,
        model_run_id: int | None = None,
        bond_id: int | None = None,
        company_id: int | None = None,
        as_of_date_from: date | None = None,
        as_of_date_to: date | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> MLPredictionResponse:
        self._validate_list_request(
            as_of_date_from=as_of_date_from,
            as_of_date_to=as_of_date_to,
            limit=limit,
            offset=offset,
        )
        conditions = self._prediction_conditions(
            model_run_id=model_run_id,
            bond_id=bond_id,
            company_id=company_id,
            as_of_date_from=as_of_date_from,
            as_of_date_to=as_of_date_to,
        )
        total = int(
            self.db.execute(
                select(func.count()).select_from(MLPrediction).where(*conditions)
            ).scalar_one()
        )
        stmt = (
            select(MLPrediction)
            .where(*conditions)
            .order_by(
                MLPrediction.created_at.desc(),
                MLPrediction.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        predictions = list(self.db.execute(stmt).scalars())
        return MLPredictionResponse(
            model_run_id=model_run_id,
            total=total,
            limit=limit,
            offset=offset,
            predictions=[
                MLPredictionRead.model_validate(prediction)
                for prediction in predictions
            ],
        )

    def _get_completed_run(self, run_id: int) -> MLModelRun:
        run = self.db.get(MLModelRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ML model run not found",
            )
        if run.status != "completed":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="ML model run is not completed",
            )
        return run

    @staticmethod
    def _load_artifact(run: MLModelRun) -> dict[str, Any]:
        if not run.artifact_path or not Path(run.artifact_path).exists():
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="ML model artifact is missing",
            )
        return joblib.load(run.artifact_path)

    def _count_feature_snapshots(
        self,
        *,
        bond_id: int | None,
        company_id: int | None,
        as_of_date_from: date | None,
        as_of_date_to: date | None,
    ) -> int:
        return int(
            self.db.execute(
                select(func.count())
                .select_from(BondFeatureSnapshot)
                .where(
                    *self._feature_conditions(
                        bond_id=bond_id,
                        company_id=company_id,
                        as_of_date_from=as_of_date_from,
                        as_of_date_to=as_of_date_to,
                    )
                )
            ).scalar_one()
        )

    def _list_feature_snapshots(
        self,
        *,
        bond_id: int | None,
        company_id: int | None,
        as_of_date_from: date | None,
        as_of_date_to: date | None,
        limit: int,
        offset: int,
    ) -> list[BondFeatureSnapshot]:
        stmt = (
            select(BondFeatureSnapshot)
            .where(
                *self._feature_conditions(
                    bond_id=bond_id,
                    company_id=company_id,
                    as_of_date_from=as_of_date_from,
                    as_of_date_to=as_of_date_to,
                )
            )
            .order_by(
                BondFeatureSnapshot.as_of_date.desc(),
                BondFeatureSnapshot.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(self.db.execute(stmt).scalars())

    @staticmethod
    def _feature_conditions(
        *,
        bond_id: int | None,
        company_id: int | None,
        as_of_date_from: date | None,
        as_of_date_to: date | None,
    ) -> list[Any]:
        conditions: list[Any] = []
        if bond_id is not None:
            conditions.append(BondFeatureSnapshot.bond_id == bond_id)
        if company_id is not None:
            conditions.append(BondFeatureSnapshot.company_id == company_id)
        if as_of_date_from is not None:
            conditions.append(BondFeatureSnapshot.as_of_date >= as_of_date_from)
        if as_of_date_to is not None:
            conditions.append(BondFeatureSnapshot.as_of_date <= as_of_date_to)
        return conditions

    @staticmethod
    def _prediction_conditions(
        *,
        model_run_id: int | None,
        bond_id: int | None,
        company_id: int | None,
        as_of_date_from: date | None,
        as_of_date_to: date | None,
    ) -> list[Any]:
        conditions: list[Any] = []
        if model_run_id is not None:
            conditions.append(MLPrediction.model_run_id == model_run_id)
        if bond_id is not None:
            conditions.append(MLPrediction.bond_id == bond_id)
        if company_id is not None:
            conditions.append(MLPrediction.company_id == company_id)
        if as_of_date_from is not None:
            conditions.append(MLPrediction.as_of_date >= as_of_date_from)
        if as_of_date_to is not None:
            conditions.append(MLPrediction.as_of_date <= as_of_date_to)
        return conditions

    def _upsert_prediction(
        self,
        *,
        run: MLModelRun,
        snapshot: BondFeatureSnapshot,
        probability: float,
        predicted_label: str,
        feature_payload: dict[str, Any],
    ) -> MLPrediction:
        now = datetime.now(timezone.utc)
        prediction = self.db.execute(
            select(MLPrediction).where(
                and_(
                    MLPrediction.model_run_id == run.id,
                    MLPrediction.feature_snapshot_id == snapshot.id,
                )
            )
        ).scalar_one_or_none()
        if prediction is None:
            prediction = MLPrediction(
                model_run_id=run.id,
                feature_snapshot_id=snapshot.id,
                bond_id=snapshot.bond_id,
                company_id=snapshot.company_id,
                as_of_date=snapshot.as_of_date,
                horizon_days=run.horizon_days,
                probability_positive=Decimal(str(probability)),
                predicted_label=predicted_label,
                features=feature_payload,
                created_at=now,
            )
        else:
            prediction.probability_positive = Decimal(str(probability))
            prediction.predicted_label = predicted_label
            prediction.features = feature_payload
            prediction.created_at = now
        self.db.add(prediction)
        self.db.flush()
        return prediction

    @staticmethod
    def _model_vector(
        snapshot: BondFeatureSnapshot,
        features: list[str],
    ) -> list[float]:
        return [
            MLPredictionService._model_value(getattr(snapshot, feature))
            for feature in features
        ]

    @staticmethod
    def _feature_payload(
        snapshot: BondFeatureSnapshot,
        features: list[str],
    ) -> dict[str, float | None]:
        return {
            feature: MLPredictionService._json_value(getattr(snapshot, feature))
            for feature in features
        }

    @staticmethod
    def _model_value(value: Any) -> float:
        if value is None:
            return nan
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    @staticmethod
    def _json_value(value: Any) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return 1.0 if value else 0.0
        if isinstance(value, Decimal):
            return float(value)
        return float(value)

    @staticmethod
    def _validate_prediction_request(request: MLPredictionRequest) -> None:
        MLPredictionService._validate_list_request(
            as_of_date_from=request.as_of_date_from,
            as_of_date_to=request.as_of_date_to,
            limit=request.limit,
            offset=request.offset,
        )

    @staticmethod
    def _validate_list_request(
        *,
        as_of_date_from: date | None,
        as_of_date_to: date | None,
        limit: int,
        offset: int,
    ) -> None:
        if (
            as_of_date_from is not None
            and as_of_date_to is not None
            and as_of_date_from > as_of_date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if limit <= 0 or limit > 5000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="limit must be between 1 and 5000",
            )
        if offset < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="offset must be non-negative",
            )
