from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.external_risk_regime import ExternalRiskRegime
from tests.helpers.assertions import assert_no_forbidden_investment_vocabulary


URL = "/api/risk/external-regime"


def test_get_returns_default_normal_regime(client: TestClient) -> None:
    response = client.get(URL)

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "normal"
    assert payload["reason"] == "Default external risk regime."
    assert payload["source"] == "default"
    assert payload["is_active"] is True
    assert payload["expires_at"] is None


def test_put_normal_saves_and_returns_normal(client: TestClient) -> None:
    response = client.put(URL, json={"mode": "normal"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "normal"
    assert payload["reason"] == "Default external risk regime."
    assert payload["source"] == "manual"
    assert payload["is_active"] is True


def test_put_elevated_requires_reason(client: TestClient) -> None:
    response = client.put(URL, json={"mode": "elevated"})

    assert response.status_code == 400
    assert response.json()["detail"] == "reason is required for elevated or severe external risk mode"


def test_put_severe_requires_reason(client: TestClient) -> None:
    response = client.put(URL, json={"mode": "severe", "reason": " "})

    assert response.status_code == 400
    assert response.json()["detail"] == "reason is required for elevated or severe external risk mode"


def test_invalid_mode_returns_400(client: TestClient) -> None:
    response = client.put(URL, json={"mode": "unknown", "reason": "Manual review."})

    assert response.status_code == 400
    assert response.json()["detail"] == "mode must be normal, elevated, or severe"


def test_expires_at_in_past_returns_400(client: TestClient) -> None:
    response = client.put(
        URL,
        json={
            "mode": "normal",
            "expires_at": "2025-01-01T00:00:00Z",
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "expires_at must be in the future"


def test_latest_active_regime_is_returned(
    client: TestClient,
    db_session: Session,
) -> None:
    old = ExternalRiskRegime(
        mode="normal",
        reason="Old regime.",
        source="manual",
        is_active=True,
    )
    db_session.add(old)
    db_session.commit()

    response = client.put(
        URL,
        json={
            "mode": "elevated",
            "reason": "Manual operator caution before paper execution window.",
            "source": "manual",
            "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).isoformat(),
        },
    )
    current = client.get(URL)

    assert response.status_code == 200
    assert current.status_code == 200
    assert current.json()["mode"] == "elevated"
    db_session.refresh(old)
    assert old.is_active is False


def test_expired_active_regime_falls_back_to_default(
    client: TestClient,
    db_session: Session,
) -> None:
    db_session.add(
        ExternalRiskRegime(
            mode="severe",
            reason="Expired manual regime.",
            source="manual",
            is_active=True,
            expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
    )
    db_session.commit()

    response = client.get(URL)

    assert response.status_code == 200
    assert response.json()["mode"] == "normal"
    assert response.json()["source"] == "default"


def test_response_has_no_project_banned_vocabulary(client: TestClient) -> None:
    response = client.put(
        URL,
        json={
            "mode": "elevated",
            "reason": "Manual operator caution before paper execution window.",
        },
    )

    assert response.status_code == 200
    assert_no_forbidden_investment_vocabulary(response.json())
