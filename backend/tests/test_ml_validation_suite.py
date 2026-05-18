from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.data_pipeline_run import DataPipelineRun
from app.models.data_pipeline_step_run import DataPipelineStepRun
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.models.paper_live_cycle_run import PaperLiveCycleRun
from app.models.paper_live_schedule import PaperLiveSchedule
from app.models.paper_portfolio import PaperPortfolio
from app.models.paper_portfolio_position import PaperPortfolioPosition
from app.models.paper_portfolio_snapshot import PaperPortfolioSnapshot
from app.models.paper_portfolio_transaction import PaperPortfolioTransaction
from app.schemas.live_data_readiness import (
    LiveDataReadinessCheck,
    LiveDataReadinessResponse,
)
from app.schemas.ml_candidate_comparison import (
    MLCandidateComparisonResponse,
    MLCandidateComparisonSelectedCandidate,
    MLCandidateComparisonWarning,
)
from app.schemas.ml_model import MLPredictionResponse, MLTrainResult
from app.services.data_pipeline_service import DataPipelineService
from app.services.live_data_readiness_service import LiveDataReadinessService
from app.services.ml_candidate_comparison_service import MLCandidateComparisonService
from app.services.ml_prediction_service import MLPredictionService
from app.services.ml_training_service import MLTrainingService
from app.services.moex_bond_universe_service import MoexBondUniverseService
from app.services.moex_cashflow_service import MoexCashflowService
from app.services.moex_market_data_service import MoexMarketDataService
from app.services.paper_trading_live_cycle_service import LivePaperCycleService
from app.services.paper_trading_live_schedule_service import LivePaperScheduleService
from app.services.paper_trading_service import PaperTradingService
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary
from tests.test_live_data_readiness import count_rows


VALIDATION_SUITE_URL = "/api/ml/validation-suite/run"


def now() -> datetime:
    return datetime.now(timezone.utc)


def readiness_response(status_value: str) -> LiveDataReadinessResponse:
    check_status = {
        "ready": "passed",
        "warning": "warning",
        "not_ready": "failed",
    }[status_value]
    checks = [
        LiveDataReadinessCheck(
            name="corporate_universe_available",
            status=check_status,
            message="Corporate bond universe is available",
            details={},
        ),
        LiveDataReadinessCheck(
            name="paper_pilot_data_ready",
            status=check_status,
            message="Live data chain state was checked",
            details={
                "blocking_checks": (
                    ["corporate_universe_available"]
                    if status_value == "not_ready"
                    else []
                )
            },
        ),
    ]
    return LiveDataReadinessResponse(
        status=status_value,
        as_of=now(),
        corporate_bond_count=20 if status_value != "not_ready" else 0,
        ofz_bond_count=0,
        total_bond_count=20 if status_value != "not_ready" else 0,
        working_bond_count=20 if status_value != "not_ready" else 0,
        company_count=20 if status_value != "not_ready" else 0,
        latest_market_snapshot_date=None,
        market_snapshot_count=20 if status_value != "not_ready" else 0,
        bonds_with_recent_market_snapshot_count=20 if status_value != "not_ready" else 0,
        latest_cashflow_date=None,
        cashflow_event_count=20 if status_value != "not_ready" else 0,
        bonds_with_cashflows_count=20 if status_value != "not_ready" else 0,
        latest_feature_snapshot_date=None,
        feature_snapshot_count=20 if status_value != "not_ready" else 0,
        bonds_with_recent_features_count=20 if status_value != "not_ready" else 0,
        latest_completed_model_run_id=None,
        latest_completed_model_run_created_at=None,
        prediction_count_for_latest_run=0,
        bonds_with_predictions_for_latest_run_count=0,
        latest_prediction_date=None,
        checks=checks,
        warnings=[],
        next_steps=["Run live data action plan."],
    )


def model_gap_readiness_response(
    *,
    include_feature_failure: bool = False,
) -> LiveDataReadinessResponse:
    failed_names = [
        "completed_model_run_available",
        "predictions_available",
        "recent_predictions_available",
    ]
    if include_feature_failure:
        failed_names.insert(0, "feature_snapshots_available")
    checks = [
        LiveDataReadinessCheck(
            name=name,
            status="failed",
            message=f"{name} failed",
            details={},
        )
        for name in failed_names
    ]
    checks.append(
        LiveDataReadinessCheck(
            name="paper_pilot_data_ready",
            status="failed",
            message="Live data chain state was checked",
            details={"blocking_checks": list(failed_names)},
        )
    )
    return LiveDataReadinessResponse(
        status="not_ready",
        as_of=now(),
        corporate_bond_count=20,
        ofz_bond_count=0,
        total_bond_count=20,
        working_bond_count=20,
        company_count=20,
        latest_market_snapshot_date=None,
        market_snapshot_count=20,
        bonds_with_recent_market_snapshot_count=20,
        latest_cashflow_date=None,
        cashflow_event_count=20,
        bonds_with_cashflows_count=20,
        latest_feature_snapshot_date=None,
        feature_snapshot_count=0 if include_feature_failure else 20,
        bonds_with_recent_features_count=0 if include_feature_failure else 20,
        latest_completed_model_run_id=None,
        latest_completed_model_run_created_at=None,
        prediction_count_for_latest_run=0,
        bonds_with_predictions_for_latest_run_count=0,
        latest_prediction_date=None,
        checks=checks,
        warnings=[],
        next_steps=["Run ML validation suite after data checks."],
    )


def train_result(run_id: int, status_value: str = "completed") -> MLTrainResult:
    return MLTrainResult(
        run_id=run_id,
        status=status_value,
        model_type="logistic_regression",
        horizon_days=30,
        train_rows=80 if status_value == "completed" else 0,
        test_rows=20 if status_value == "completed" else 0,
        positive_rows=50 if status_value == "completed" else 0,
        negative_rows=50 if status_value == "completed" else 0,
        metrics={"accuracy": 0.7} if status_value == "completed" else {},
        feature_importance=[],
        artifact_path=f"/tmp/model-{run_id}.joblib" if status_value == "completed" else None,
        started_at=now(),
        finished_at=now(),
    )


def prediction_response(model_run_id: int) -> MLPredictionResponse:
    return MLPredictionResponse(
        model_run_id=model_run_id,
        total=25,
        limit=5000,
        offset=0,
        predictions=[],
    )


def comparison_response(
    *,
    selected_model_run_id: int | None,
    warnings: bool = False,
    ready_for_strategy_research: bool = True,
    issues: list[str] | None = None,
) -> MLCandidateComparisonResponse:
    selected = (
        MLCandidateComparisonSelectedCandidate(
            name="candidate",
            rank=1,
            ranking_metric="probability_separation",
            ranking_value=Decimal("0.20"),
            model_run_id=selected_model_run_id,
            model_run_ids=[selected_model_run_id],
            prediction_source_mode="single_model",
            ready_for_strategy_research=ready_for_strategy_research,
            issues=list(issues or []),
        )
        if selected_model_run_id is not None
        else None
    )
    return MLCandidateComparisonResponse(
        ranking_metric="probability_separation",
        ranking_direction="desc",
        candidate_count=1,
        completed_candidate_count=1 if selected is not None else 0,
        failed_candidate_count=0,
        selected_candidate=selected,
        leaderboard=[],
        candidates=[],
        limit=100,
        offset=0,
        warnings=(
            [MLCandidateComparisonWarning(message="No selected model candidate")]
            if warnings
            else []
        ),
    )


def core_counts(db: Session) -> dict[str, int]:
    models = [
        Bond,
        Company,
        BondMarketSnapshot,
        BondCashflowEvent,
        BondFeatureSnapshot,
        MLModelRun,
        MLPrediction,
        DataPipelineRun,
        DataPipelineStepRun,
        PaperPortfolio,
        PaperPortfolioPosition,
        PaperPortfolioTransaction,
        PaperPortfolioSnapshot,
        PaperLiveSchedule,
        PaperLiveCycleRun,
    ]
    return {model.__name__: count_rows(db, model) for model in models}


def test_readiness_not_ready_blocks_training(
    client: TestClient,
    monkeypatch,
) -> None:
    def fail_train(*args, **kwargs):
        raise AssertionError("training should not run")

    monkeypatch.setattr(MLTrainingService, "train", fail_train)

    response = client.post(VALIDATION_SUITE_URL, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["readiness_status"] == "not_ready"
    assert payload["training_result_count"] == 0
    assert payload["recommended_model_run_id"] is None


def test_readiness_warning_blocks_when_not_allowed(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda *args, **kwargs: readiness_response("warning"),
    )

    def fail_train(*args, **kwargs):
        raise AssertionError("training should not run")

    monkeypatch.setattr(MLTrainingService, "train", fail_train)

    response = client.post(VALIDATION_SUITE_URL, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["training_result_count"] == 0


def test_readiness_warning_allowed_continues(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda *args, **kwargs: readiness_response("warning"),
    )

    def fake_train(self, request):
        calls.append(request.random_state)
        return train_result(len(calls))

    monkeypatch.setattr(MLTrainingService, "train", fake_train)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "allow_readiness_warning": True,
            "generate_predictions": False,
            "run_candidate_comparison": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls
    assert payload["completed_training_count"] == 3
    assert any(warning["code"] == "readiness_warning" for warning in payload["warnings"])


def test_no_completed_model_run_can_continue_when_training_can_fix_it(
    client: TestClient,
    monkeypatch,
) -> None:
    training_calls: list[int] = []
    prediction_calls: list[int] = []
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda *args, **kwargs: model_gap_readiness_response(),
    )

    def fake_train(self, request):
        training_calls.append(request.random_state)
        return train_result(909)

    def fake_predict(self, request):
        prediction_calls.append(request.model_run_id)
        return prediction_response(request.model_run_id)

    monkeypatch.setattr(MLTrainingService, "train", fake_train)
    monkeypatch.setattr(MLPredictionService, "predict", fake_predict)
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(selected_model_run_id=909),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={"training_configs": [{"name": "model_gap", "min_rows": 10}]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"completed", "completed_with_warnings"}
    assert training_calls
    assert prediction_calls == [909]
    assert any(warning["code"] == "model_readiness_gap" for warning in payload["warnings"])


def test_missing_model_still_blocks_when_training_disabled(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda *args, **kwargs: model_gap_readiness_response(),
    )

    def fail_train(*args, **kwargs):
        raise AssertionError("training should not run")

    monkeypatch.setattr(MLTrainingService, "train", fail_train)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={"include_ml_training": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["training_result_count"] == 0


def test_non_ml_data_failure_still_blocks(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        LiveDataReadinessService,
        "check",
        lambda *args, **kwargs: model_gap_readiness_response(include_feature_failure=True),
    )

    def fail_train(*args, **kwargs):
        raise AssertionError("training should not run")

    monkeypatch.setattr(MLTrainingService, "train", fail_train)

    response = client.post(VALIDATION_SUITE_URL, json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "blocked"
    assert payload["training_result_count"] == 0


def test_default_training_configs_run(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[int] = []

    def fake_train(self, request):
        calls.append(request.random_state)
        return train_result(len(calls))

    monkeypatch.setattr(MLTrainingService, "train", fake_train)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "generate_predictions": False,
            "run_candidate_comparison": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["training_result_count"] == 3
    assert len(calls) == 3


def test_one_training_failure_does_not_abort_suite(
    client: TestClient,
    monkeypatch,
) -> None:
    calls: list[str] = []

    def fake_train(self, request):
        calls.append(request.return_method)
        if len(calls) == 1:
            raise RuntimeError("training failed")
        return train_result(22)

    monkeypatch.setattr(MLTrainingService, "train", fake_train)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "generate_predictions": False,
            "run_candidate_comparison": False,
            "training_configs": [
                {"name": "first", "min_rows": 10},
                {"name": "second", "min_rows": 10},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_warnings"
    assert payload["completed_training_count"] == 1
    assert payload["failed_training_count"] == 1


def test_predictions_generated_for_completed_runs_only(
    client: TestClient,
    monkeypatch,
) -> None:
    prediction_calls: list[int] = []

    def fake_train(self, request):
        if request.random_state == 1:
            return train_result(101)
        return train_result(202, status_value="failed")

    def fake_predict(self, request):
        prediction_calls.append(request.model_run_id)
        return prediction_response(request.model_run_id)

    monkeypatch.setattr(MLTrainingService, "train", fake_train)
    monkeypatch.setattr(MLPredictionService, "predict", fake_predict)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "run_candidate_comparison": False,
            "training_configs": [
                {"name": "completed", "random_state": 1, "min_rows": 10},
                {"name": "failed", "random_state": 2, "min_rows": 10},
            ],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert prediction_calls == [101]
    assert payload["prediction_result_count"] == 1


def test_candidate_comparison_selects_best_model(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(303),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(selected_model_run_id=303),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "selected", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_model_run_id"] == 303
    assert payload["can_continue_to_robustness"] is True
    assert payload["can_continue_to_paper_readiness"] is True


def test_no_selected_candidate_yields_warning_status(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(404),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(
            selected_model_run_id=None,
            warnings=True,
        ),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "candidate", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_warnings"
    assert payload["recommended_model_run_id"] is None
    assert payload["can_continue_to_robustness"] is False


def test_selected_candidate_not_ready_yields_warning_status(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(414),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(
            selected_model_run_id=414,
            ready_for_strategy_research=False,
            issues=["not enough evaluable predictions"],
        ),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "unready_candidate", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "completed_with_warnings"
    assert payload["recommended_model_run_id"] is None
    assert payload["can_continue_to_robustness"] is False
    assert payload["can_continue_to_paper_readiness"] is False
    assert any(
        warning["code"] == "selected_candidate_not_ready"
        for warning in payload["warnings"]
    )


def test_generate_predictions_false_skips_prediction_calls(
    client: TestClient,
    monkeypatch,
) -> None:
    def fail_predict(*args, **kwargs):
        raise AssertionError("prediction should not run")

    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(505),
    )
    monkeypatch.setattr(MLPredictionService, "predict", fail_predict)

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "generate_predictions": False,
            "run_candidate_comparison": False,
            "training_configs": [{"name": "no_predictions", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["prediction_result_count"] == 0
    assert any(
        warning["code"] == "prediction_generation_skipped"
        for warning in payload["warnings"]
    )


def test_invalid_params_return_exact_400_details(client: TestClient) -> None:
    too_many_configs = [
        {"name": f"config_{index}", "min_rows": 10}
        for index in range(11)
    ]
    cases = [
        ({"suite_name": "   "}, "suite_name must not be blank"),
        ({"recent_days": 0}, "recent_days must be between 1 and 365"),
        ({"recent_days": 366}, "recent_days must be between 1 and 365"),
        (
            {"minimum_corporate_bonds": -1},
            "minimum_corporate_bonds must be non-negative",
        ),
        (
            {"minimum_bonds_with_recent_market_snapshot": -1},
            "minimum_bonds_with_recent_market_snapshot must be non-negative",
        ),
        (
            {"minimum_bonds_with_recent_features": -1},
            "minimum_bonds_with_recent_features must be non-negative",
        ),
        (
            {"minimum_bonds_with_predictions": -1},
            "minimum_bonds_with_predictions must be non-negative",
        ),
        (
            {"training_configs": too_many_configs},
            "training_configs length must be between 1 and 10",
        ),
        (
            {"training_configs": [{"name": "   "}]},
            "training config name must not be blank",
        ),
        (
            {"training_configs": [{"name": "bad", "horizon_days": 0}]},
            "horizon_days must be between 1 and 365",
        ),
        (
            {"training_configs": [{"name": "bad", "return_method": "unknown"}]},
            "return_method must be one of supported return methods",
        ),
        (
            {"training_configs": [{"name": "bad", "model_type": "tree"}]},
            "model_type must be supported by MLTrainingService",
        ),
        (
            {"training_configs": [{"name": "bad", "test_size": 0.5}]},
            "test_size must be greater than 0 and less than 0.5",
        ),
        (
            {"training_configs": [{"name": "bad", "min_rows": 9}]},
            "min_rows must be at least 10",
        ),
        (
            {"training_configs": [{"name": "bad", "max_rows": 0}]},
            "max_rows must be positive when provided",
        ),
        (
            {
                "training_configs": [
                    {
                        "name": "bad",
                        "as_of_date_from": "2025-03-14",
                        "as_of_date_to": "2025-01-10",
                    }
                ]
            },
            "Invalid date range",
        ),
        ({"prediction_limit": 0}, "prediction_limit must be between 1 and 5000"),
        ({"prediction_limit": 5001}, "prediction_limit must be between 1 and 5000"),
        ({"ranking_direction": "sideways"}, "ranking_direction must be asc or desc"),
        ({"ranking_metric": "unknown"}, "ranking_metric must be supported"),
        (
            {"maximum_missing_label_ratio": "1.1"},
            "maximum_missing_label_ratio must be between 0 and 1",
        ),
    ]
    for payload, detail in cases:
        response = client.post(VALIDATION_SUITE_URL, json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_no_paper_pipeline_or_moex_calls(
    client: TestClient,
    monkeypatch,
) -> None:
    def fail_call(*args, **kwargs):
        raise AssertionError("unexpected service call")

    monkeypatch.setattr(DataPipelineService, "run", fail_call)
    monkeypatch.setattr(MoexBondUniverseService, "sync", fail_call)
    monkeypatch.setattr(MoexMarketDataService, "sync", fail_call)
    monkeypatch.setattr(MoexCashflowService, "sync", fail_call)
    monkeypatch.setattr(PaperTradingService, "rebalance", fail_call)
    monkeypatch.setattr(PaperTradingService, "mark_period", fail_call)
    monkeypatch.setattr(LivePaperCycleService, "run", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_due", fail_call)
    monkeypatch.setattr(LivePaperScheduleService, "run_schedule_once", fail_call)
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(606),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(selected_model_run_id=606),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "safe", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    assert response.json()["recommended_model_run_id"] == 606


def test_non_ml_counts_unchanged_with_monkeypatched_services(
    client: TestClient,
    db_session: Session,
    monkeypatch,
) -> None:
    before = core_counts(db_session)
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(707),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(selected_model_run_id=707),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "counts", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    after = core_counts(db_session)
    assert after == before


def test_forbidden_vocabulary_helper(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        MLTrainingService,
        "train",
        lambda self, request: train_result(808),
    )
    monkeypatch.setattr(
        MLPredictionService,
        "predict",
        lambda self, request: prediction_response(request.model_run_id),
    )
    monkeypatch.setattr(
        MLCandidateComparisonService,
        "compare",
        lambda self, request: comparison_response(selected_model_run_id=808),
    )

    response = client.post(
        VALIDATION_SUITE_URL,
        json={
            "require_live_data_ready": False,
            "training_configs": [{"name": "vocabulary", "min_rows": 10}],
        },
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
