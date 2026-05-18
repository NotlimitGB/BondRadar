from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.live_data_readiness import LiveDataReadinessResponse
from app.schemas.ml_candidate_comparison import MLCandidateComparisonResponse


ML_VALIDATION_SUITE_STATUSES = {
    "completed",
    "completed_with_warnings",
    "blocked",
    "failed",
}
ML_VALIDATION_TRAINING_STATUSES = {"completed", "failed", "skipped"}


class MLValidationTrainingConfig(BaseModel):
    name: str
    horizon_days: int = 30
    return_method: str = "risk_adjusted"
    include_credit_risk_features: bool = True
    as_of_date_from: date | None = None
    as_of_date_to: date | None = None
    bond_ids: list[int] | None = None
    company_ids: list[int] | None = None
    model_type: str = "logistic_regression"
    test_size: float = 0.2
    min_rows: int = 30
    random_state: int = 42
    max_rows: int | None = None


class MLValidationSuiteRequest(BaseModel):
    suite_name: str = "pre_deploy_ml_validation"
    require_live_data_ready: bool = True
    allow_readiness_warning: bool = False

    recent_days: int = 7
    minimum_corporate_bonds: int = 20
    minimum_bonds_with_recent_market_snapshot: int = 20
    minimum_bonds_with_recent_features: int = 20
    minimum_bonds_with_predictions: int = 0
    include_ofz: bool = False

    training_configs: list[MLValidationTrainingConfig] = Field(default_factory=list)

    include_ml_training: bool = True
    generate_predictions: bool = True
    prediction_as_of_date_from: date | None = None
    prediction_as_of_date_to: date | None = None
    prediction_limit: int = 5000
    save_predictions: bool = True

    run_candidate_comparison: bool = True
    comparison_date_from: date | None = None
    comparison_date_to: date | None = None
    comparison_return_method: str = "risk_adjusted"
    comparison_horizon_days: int = 30
    positive_probability_cutoff: Decimal = Decimal("0.50")
    ranking_metric: str = "probability_separation"
    ranking_direction: str = "desc"
    minimum_evaluable_predictions: int = 30
    minimum_positive_labels: int = 5
    minimum_negative_labels: int = 5
    maximum_missing_label_ratio: Decimal = Decimal("0.30")


class MLValidationTrainingResult(BaseModel):
    name: str
    status: str
    model_run_id: int | None
    train_rows: int | None
    test_rows: int | None
    positive_rows: int | None
    negative_rows: int | None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifact_path: str | None
    error: str | None
    warnings: list[str] = Field(default_factory=list)


class MLValidationPredictionResult(BaseModel):
    name: str
    model_run_id: int
    status: str
    total: int | None
    saved: bool
    error: str | None
    warnings: list[str] = Field(default_factory=list)


class MLValidationSelectedCandidate(BaseModel):
    name: str
    model_run_id: int | None
    ranking_metric: str
    ranking_value: Decimal | int | bool | None
    ready_for_strategy_research: bool
    issues: list[str] = Field(default_factory=list)


class MLValidationSuiteResponse(BaseModel):
    status: str
    suite_name: str
    as_of: datetime

    readiness_status: str | None
    readiness: LiveDataReadinessResponse | None

    training_result_count: int
    completed_training_count: int
    failed_training_count: int

    prediction_result_count: int
    completed_prediction_count: int
    failed_prediction_count: int

    selected_candidate: MLValidationSelectedCandidate | None
    candidate_comparison: MLCandidateComparisonResponse | None

    training_results: list[MLValidationTrainingResult]
    prediction_results: list[MLValidationPredictionResult]

    recommended_model_run_id: int | None
    can_continue_to_robustness: bool
    can_continue_to_paper_readiness: bool

    warnings: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)
    next_steps: list[str] = Field(default_factory=list)
