from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from fastapi import HTTPException, status
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.ml_model_run import MLModelRun
from app.schemas.ml_model import MLModelRunRead, MLTrainRequest, MLTrainResult
from app.services.ml_feature_builder import MLFeatureBuilder, RETURN_METHODS


class MLTrainingService:
    TARGET = "label_binary"
    MODEL_TYPE = "logistic_regression"

    def __init__(self, db: Session, artifact_dir: str | None = None) -> None:
        self.db = db
        self.artifact_dir = Path(artifact_dir or settings.ML_ARTIFACT_DIR)

    def train(self, request: MLTrainRequest) -> MLTrainResult:
        self._validate_request(request)
        feature_names = MLFeatureBuilder.feature_names(
            include_credit_risk_features=request.include_credit_risk_features
        )
        feature_builder = MLFeatureBuilder(self.db)
        run = self._create_run(request, feature_names)
        try:
            rows = self._load_training_rows(request)
            self._validate_training_rows(rows, request.min_rows)
            train_rows, test_rows = self._split_rows(rows, request.test_size)
            if len({target for _, target in train_rows}) < 2:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Training dataset must contain at least two classes",
                )

            x_train = [
                feature_builder.vector(feature, feature_names)
                for feature, _ in train_rows
            ]
            y_train = [target for _, target in train_rows]
            x_test = [
                feature_builder.vector(feature, feature_names)
                for feature, _ in test_rows
            ]
            y_test = [target for _, target in test_rows]
            pipeline = self._pipeline(request)
            pipeline.fit(x_train, y_train)
            metrics = self._metrics(pipeline, x_test, y_test)
            feature_importance = self._feature_importance(pipeline, feature_names)
            artifact_path = self._save_artifact(
                run.id,
                pipeline,
                request,
                feature_names,
            )

            run = self._refresh_run(run.id)
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            run.train_rows = len(train_rows)
            run.test_rows = len(test_rows)
            run.positive_rows = sum(target == 1 for _, target in rows)
            run.negative_rows = sum(target == 0 for _, target in rows)
            run.metrics = metrics
            run.feature_importance = feature_importance
            run.artifact_path = str(artifact_path)
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            return self._result(run)
        except Exception as exc:
            self.db.rollback()
            run = self._refresh_run(run.id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.error = self._error_detail(exc)
            self.db.add(run)
            self.db.commit()
            raise

    def list_runs(
        self,
        *,
        limit: int = 20,
        status_filter: str | None = None,
        model_type: str | None = None,
    ) -> list[MLModelRun]:
        stmt = select(MLModelRun)
        if status_filter is not None:
            stmt = stmt.where(MLModelRun.status == status_filter)
        if model_type is not None:
            stmt = stmt.where(MLModelRun.model_type == model_type)
        stmt = stmt.order_by(MLModelRun.started_at.desc(), MLModelRun.id.desc()).limit(
            limit
        )
        return list(self.db.execute(stmt).scalars())

    def get_run(self, run_id: int) -> MLModelRun:
        run = self.db.get(MLModelRun, run_id)
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="ML model run not found",
            )
        return run

    @staticmethod
    def _validate_request(request: MLTrainRequest) -> None:
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if (
            request.as_of_date_from is not None
            and request.as_of_date_to is not None
            and request.as_of_date_from > request.as_of_date_to
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.test_size <= 0 or request.test_size >= 0.5:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="test_size must be greater than 0 and less than 0.5",
            )
        if request.min_rows < 10:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_rows must be at least 10",
            )
        if request.max_rows is not None and request.max_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_rows must be positive",
            )
        if request.return_method not in RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        if request.model_type != MLTrainingService.MODEL_TYPE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Unsupported model type",
            )

    def _create_run(
        self,
        request: MLTrainRequest,
        feature_names: list[str],
    ) -> MLModelRun:
        run = MLModelRun(
            status="running",
            model_type=request.model_type,
            horizon_days=request.horizon_days,
            features=list(feature_names),
            target=self.TARGET,
            as_of_date_from=request.as_of_date_from,
            as_of_date_to=request.as_of_date_to,
            train_rows=0,
            test_rows=0,
            positive_rows=0,
            negative_rows=0,
            metrics={},
            feature_importance=[],
            params={
                "horizon_days": request.horizon_days,
                "return_method": request.return_method,
                "include_credit_risk_features": request.include_credit_risk_features,
                "as_of_date_from": (
                    request.as_of_date_from.isoformat()
                    if request.as_of_date_from
                    else None
                ),
                "as_of_date_to": (
                    request.as_of_date_to.isoformat()
                    if request.as_of_date_to
                    else None
                ),
                "bond_ids": request.bond_ids,
                "company_ids": request.company_ids,
                "model_type": request.model_type,
                "test_size": request.test_size,
                "min_rows": request.min_rows,
                "random_state": request.random_state,
                "max_rows": request.max_rows,
            },
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _load_training_rows(
        self,
        request: MLTrainRequest,
    ) -> list[tuple[BondFeatureSnapshot, int]]:
        stmt = (
            select(BondFeatureSnapshot, BondReturnLabel)
            .join(
                BondReturnLabel,
                and_(
                    BondFeatureSnapshot.bond_id == BondReturnLabel.bond_id,
                    BondFeatureSnapshot.as_of_date == BondReturnLabel.as_of_date,
                ),
            )
            .where(
                BondReturnLabel.horizon_days == request.horizon_days,
                BondReturnLabel.return_method == request.return_method,
                BondReturnLabel.label.in_(("positive_return", "negative_return")),
                BondReturnLabel.label_binary.is_not(None),
            )
            .order_by(
                BondFeatureSnapshot.as_of_date.asc(),
                BondFeatureSnapshot.bond_id.asc(),
                BondFeatureSnapshot.id.asc(),
            )
        )
        if request.as_of_date_from is not None:
            stmt = stmt.where(BondFeatureSnapshot.as_of_date >= request.as_of_date_from)
        if request.as_of_date_to is not None:
            stmt = stmt.where(BondFeatureSnapshot.as_of_date <= request.as_of_date_to)
        if request.bond_ids:
            stmt = stmt.where(BondFeatureSnapshot.bond_id.in_(set(request.bond_ids)))
        if request.company_ids:
            stmt = stmt.where(
                BondFeatureSnapshot.company_id.in_(set(request.company_ids))
            )
        if request.max_rows is not None:
            stmt = stmt.limit(request.max_rows)

        return [
            (feature, int(label.label_binary))
            for feature, label in self.db.execute(stmt).all()
            if label.label_binary is not None
        ]

    @staticmethod
    def _validate_training_rows(
        rows: list[tuple[BondFeatureSnapshot, int]],
        min_rows: int,
    ) -> None:
        if len(rows) < min_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Not enough training rows",
            )
        if len({target for _, target in rows}) < 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Training dataset must contain at least two classes",
            )

    @staticmethod
    def _split_rows(
        rows: list[tuple[BondFeatureSnapshot, int]],
        test_size: float,
    ) -> tuple[list[tuple[BondFeatureSnapshot, int]], list[tuple[BondFeatureSnapshot, int]]]:
        test_rows_count = max(1, int(len(rows) * test_size))
        train_rows_count = len(rows) - test_rows_count
        return rows[:train_rows_count], rows[train_rows_count:]

    @staticmethod
    def _pipeline(request: MLTrainRequest) -> Pipeline:
        return Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=1000,
                        class_weight="balanced",
                        random_state=request.random_state,
                    ),
                ),
            ]
        )

    @staticmethod
    def _metrics(
        pipeline: Pipeline,
        x_test: list[list[float | None]],
        y_test: list[int],
    ) -> dict[str, Any]:
        predictions = pipeline.predict(x_test)
        probabilities = pipeline.predict_proba(x_test)
        positive_index = list(pipeline.classes_).index(1)
        positive_probabilities = probabilities[:, positive_index]
        tn, fp, fn, tp = confusion_matrix(y_test, predictions, labels=[0, 1]).ravel()
        roc_auc = None
        if len(set(y_test)) == 2:
            roc_auc = float(roc_auc_score(y_test, positive_probabilities))
        return {
            "accuracy": float(accuracy_score(y_test, predictions)),
            "precision": float(precision_score(y_test, predictions, zero_division=0)),
            "recall": float(recall_score(y_test, predictions, zero_division=0)),
            "f1": float(f1_score(y_test, predictions, zero_division=0)),
            "roc_auc": roc_auc,
            "confusion_matrix": {
                "true_positive": int(tp),
                "true_negative": int(tn),
                "false_positive": int(fp),
                "false_negative": int(fn),
            },
        }

    @staticmethod
    def _feature_importance(
        pipeline: Pipeline,
        feature_names: list[str],
    ) -> list[dict[str, float | str]]:
        model = pipeline.named_steps["model"]
        coefficients = getattr(model, "coef_", None)
        if coefficients is None:
            return []
        items = [
            {"feature": feature, "importance": float(coefficient)}
            for feature, coefficient in zip(feature_names, coefficients[0])
        ]
        return sorted(items, key=lambda item: abs(item["importance"]), reverse=True)

    def _save_artifact(
        self,
        run_id: int,
        pipeline: Pipeline,
        request: MLTrainRequest,
        feature_names: list[str],
    ) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact_path = (
            self.artifact_dir / f"baseline_logistic_regression_run_{run_id}.joblib"
        )
        joblib.dump(
            {
                "model": pipeline,
                "features": list(feature_names),
                "model_type": request.model_type,
                "horizon_days": request.horizon_days,
                "return_method": request.return_method,
                "include_credit_risk_features": request.include_credit_risk_features,
            },
            artifact_path,
        )
        return artifact_path

    def _refresh_run(self, run_id: int) -> MLModelRun:
        run = self.db.get(MLModelRun, run_id)
        if run is None:
            raise RuntimeError(f"ML model run {run_id} was not found")
        return run

    @staticmethod
    def _result(run: MLModelRun) -> MLTrainResult:
        return MLTrainResult(
            run_id=run.id,
            status=run.status,
            model_type=run.model_type,
            horizon_days=run.horizon_days,
            train_rows=run.train_rows,
            test_rows=run.test_rows,
            positive_rows=run.positive_rows,
            negative_rows=run.negative_rows,
            metrics=run.metrics,
            feature_importance=run.feature_importance,
            artifact_path=run.artifact_path,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)
