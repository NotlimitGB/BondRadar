from datetime import date, datetime, timezone
from decimal import Decimal
import json
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.schemas.ml_model import (
    MLPredictionRead,
    MLPredictionResponse,
    MLTrainResult,
)


def base_payload(**overrides):
    payload = {
        "date_from": "2025-01-10",
        "date_to": "2025-02-20",
        "min_train_date": "2025-01-01",
        "test_window_days": 30,
        "step_days": 30,
        "horizon_days": 30,
        "return_method": "risk_adjusted",
        "model_type": "logistic_regression",
        "include_credit_risk_features": True,
        "min_rows": 10,
        "min_positive_rows": 2,
        "min_negative_rows": 2,
        "test_size": "0.2",
        "save_predictions": True,
        "skip_not_ready_folds": True,
        "run_readiness_check": False,
        "max_folds": 10,
    }
    payload.update(overrides)
    return payload


def fake_train_result(
    *,
    run_id: int,
    train_rows: int = 80,
    test_rows: int = 20,
    positive_rows: int = 50,
    negative_rows: int = 50,
    metrics: dict | None = None,
) -> MLTrainResult:
    return MLTrainResult(
        run_id=run_id,
        status="completed",
        model_type="logistic_regression",
        horizon_days=30,
        train_rows=train_rows,
        test_rows=test_rows,
        positive_rows=positive_rows,
        negative_rows=negative_rows,
        metrics=metrics or {"accuracy": 0.8, "roc_auc": 0.7},
        feature_importance=[],
        artifact_path=f"fake-{run_id}.joblib",
        started_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        finished_at=datetime(2025, 1, 1, 1, tzinfo=timezone.utc),
    )


def fake_prediction_response(
    *,
    model_run_id: int,
    as_of_date: date,
    count: int = 2,
    save_predictions: bool = True,
) -> MLPredictionResponse:
    predictions = [
        MLPredictionRead(
            id=index if save_predictions else None,
            model_run_id=model_run_id,
            feature_snapshot_id=1000 + index,
            bond_id=index,
            company_id=1,
            as_of_date=as_of_date,
            horizon_days=30,
            probability_positive=Decimal("0.60"),
            predicted_label="predicted_positive_return",
            features={"bond_score": 80},
            created_at=(
                datetime(2025, 1, 1, tzinfo=timezone.utc)
                if save_predictions
                else None
            ),
        )
        for index in range(1, count + 1)
    ]
    return MLPredictionResponse(
        model_run_id=model_run_id,
        total=count,
        limit=5000,
        offset=0,
        predictions=predictions,
    )


def install_successful_services(monkeypatch, *, prediction_count: int = 2):
    calls = {"train": 0, "predict": 0}

    def fake_train(self, request):
        calls["train"] += 1
        return fake_train_result(run_id=calls["train"])

    def fake_predict(self, request):
        calls["predict"] += 1
        return fake_prediction_response(
            model_run_id=request.model_run_id,
            as_of_date=request.as_of_date_from,
            count=prediction_count,
            save_predictions=request.save_predictions,
        )

    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLTrainingService.train",
        fake_train,
    )
    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLPredictionService.predict",
        fake_predict,
    )
    return calls


def install_readiness(monkeypatch, *, status: str = "ready"):
    def fake_check(self, request):
        summary = SimpleNamespace(
            evaluable_label_count=20,
            positive_label_count=10,
            negative_label_count=10,
            insufficient_ratio=Decimal("0"),
        )
        return SimpleNamespace(status=status, summary=summary, warnings=[])

    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.DataReadinessService.check",
        fake_check,
    )


def test_generates_folds_without_train_predict_overlap(
    client: TestClient,
    monkeypatch,
) -> None:
    install_successful_services(monkeypatch)

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(date_to="2025-03-10"),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fold_count"] == 2
    for fold in payload["folds"]:
        assert date.fromisoformat(fold["train_date_to"]) < date.fromisoformat(
            fold["predict_date_from"]
        )


def test_completed_fold_trains_and_predicts(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = install_successful_services(monkeypatch, prediction_count=3)

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(max_folds=1),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed_fold_count"] == 1
    assert payload["folds"][0]["status"] == "completed"
    assert payload["folds"][0]["model_run_id"] == 1
    assert payload["folds"][0]["prediction_count"] == 3
    assert payload["folds"][0]["saved_prediction_count"] == 3
    assert calls == {"train": 1, "predict": 1}


def test_readiness_not_ready_skips_by_default(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = install_successful_services(monkeypatch)
    install_readiness(monkeypatch, status="not_ready")

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(run_readiness_check=True, max_folds=1),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["skipped_fold_count"] == 1
    assert payload["folds"][0]["readiness_status"] == "not_ready"
    assert calls["train"] == 0
    assert calls["predict"] == 0


def test_skip_not_ready_false_attempts_training(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = install_successful_services(monkeypatch)
    install_readiness(monkeypatch, status="not_ready")

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(
            run_readiness_check=True,
            skip_not_ready_folds=False,
            max_folds=1,
        ),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["completed_fold_count"] == 1
    assert calls["train"] == 1
    messages = [item["message"] for item in payload["folds"][0]["warnings"]]
    assert "Fold readiness status was not ready; training was attempted" in messages


def test_training_failure_is_fold_level_and_later_folds_continue(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = {"train": 0, "predict": 0}

    def fake_train(self, request):
        calls["train"] += 1
        if calls["train"] == 1:
            raise HTTPException(status_code=400, detail="training unavailable")
        return fake_train_result(run_id=2)

    def fake_predict(self, request):
        calls["predict"] += 1
        return fake_prediction_response(
            model_run_id=request.model_run_id,
            as_of_date=request.as_of_date_from,
        )

    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLTrainingService.train",
        fake_train,
    )
    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLPredictionService.predict",
        fake_predict,
    )

    response = client.post("/api/ml/walk-forward/run", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["failed_fold_count"] == 1
    assert payload["completed_fold_count"] == 1
    assert [fold["status"] for fold in payload["folds"]] == ["failed", "completed"]
    assert calls == {"train": 2, "predict": 1}


def test_prediction_failure_captures_model_run_and_continues(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = {"train": 0, "predict": 0}

    def fake_train(self, request):
        calls["train"] += 1
        return fake_train_result(run_id=calls["train"])

    def fake_predict(self, request):
        calls["predict"] += 1
        if calls["predict"] == 1:
            raise HTTPException(status_code=400, detail="prediction unavailable")
        return fake_prediction_response(
            model_run_id=request.model_run_id,
            as_of_date=request.as_of_date_from,
        )

    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLTrainingService.train",
        fake_train,
    )
    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLPredictionService.predict",
        fake_predict,
    )

    response = client.post("/api/ml/walk-forward/run", json=base_payload())

    assert response.status_code == 200
    payload = response.json()
    assert payload["failed_fold_count"] == 1
    assert payload["completed_fold_count"] == 1
    assert payload["folds"][0]["model_run_id"] == 1
    assert payload["folds"][0]["status"] == "failed"
    assert payload["folds"][1]["status"] == "completed"


def test_max_folds_truncates_generated_folds(
    client: TestClient,
    monkeypatch,
) -> None:
    install_successful_services(monkeypatch)

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(date_to="2025-12-31", max_folds=2),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fold_count"] == 2
    assert (
        payload["warnings"][0]["message"]
        == "Walk-forward folds were truncated by max_folds"
    )


def test_invalid_requests_return_400(client: TestClient) -> None:
    cases = [
        (
            base_payload(date_from="2025-02-01", date_to="2025-01-01"),
            "Invalid date range",
        ),
        (
            base_payload(train_window_days=0),
            "train_window_days must be positive when provided",
        ),
        (base_payload(test_window_days=0), "test_window_days must be positive"),
        (base_payload(step_days=0), "step_days must be positive"),
        (base_payload(horizon_days=0), "horizon_days must be positive"),
        (base_payload(return_method="not_real"), "Invalid return method"),
        (base_payload(model_type="not_real"), "Invalid model type"),
        (base_payload(min_rows=0), "min_rows must be positive"),
        (base_payload(min_positive_rows=-1), "min class rows must be non-negative"),
        (
            base_payload(readiness_max_insufficient_ratio="1.5"),
            "readiness_max_insufficient_ratio must be between 0 and 1",
        ),
        (
            base_payload(test_size="0.5"),
            "test_size must be greater than 0 and less than 0.5",
        ),
        (base_payload(max_folds=0), "max_folds must be between 1 and 200"),
    ]

    for payload, detail in cases:
        response = client.post("/api/ml/walk-forward/run", json=payload)
        assert response.status_code == 400
        assert response.json()["detail"] == detail


def test_summary_aggregates_completed_folds(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = {"train": 0}

    def fake_train(self, request):
        calls["train"] += 1
        metrics = (
            {"accuracy": 0.8, "roc_auc": 0.7}
            if calls["train"] == 1
            else {"accuracy": 0.6, "roc_auc": 0.5}
        )
        return fake_train_result(
            run_id=calls["train"],
            train_rows=80 + (calls["train"] * 10),
            test_rows=20,
            positive_rows=40,
            negative_rows=40,
            metrics=metrics,
        )

    def fake_predict(self, request):
        return fake_prediction_response(
            model_run_id=request.model_run_id,
            as_of_date=request.as_of_date_from,
            count=2,
        )

    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLTrainingService.train",
        fake_train,
    )
    monkeypatch.setattr(
        "app.services.ml_walk_forward_service.MLPredictionService.predict",
        fake_predict,
    )

    response = client.post("/api/ml/walk-forward/run", json=base_payload())

    assert response.status_code == 200
    summary = response.json()["summary"]
    assert summary["model_run_ids"] == [1, 2]
    assert summary["total_predictions"] == 4
    assert Decimal(str(summary["average_train_rows"])) == Decimal("95")
    assert Decimal(str(summary["average_accuracy"])) == Decimal("0.7")
    assert Decimal(str(summary["average_auc"])) == Decimal("0.6")


def test_walk_forward_payload_has_no_recommendation_vocabulary(
    client: TestClient,
    monkeypatch,
) -> None:
    install_successful_services(monkeypatch)

    response = client.post(
        "/api/ml/walk-forward/run",
        json=base_payload(max_folds=1),
    )

    assert response.status_code == 200
    payload = json.dumps(response.json()).lower()
    forbidden = [
        "buy",
        "sell",
        "hold",
        "strong_buy",
        "strong_sell",
        "must_buy",
        "must_sell",
        "\u043f\u043e\u043a\u0443\u043f\u0430\u0442\u044c",
        "\u043f\u0440\u043e\u0434\u0430\u0432\u0430\u0442\u044c",
        "threshold",
    ]
    assert all(word not in payload for word in forbidden)
