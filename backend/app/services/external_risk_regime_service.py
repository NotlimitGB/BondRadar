from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.external_risk_regime import (
    EXTERNAL_RISK_REGIME_MODES,
    ExternalRiskRegime,
)
from app.schemas.external_risk_regime import (
    ExternalRiskRegimeResponse,
    ExternalRiskRegimeUpdateRequest,
)


DEFAULT_EXTERNAL_RISK_REASON = "Default external risk regime."


class ExternalRiskRegimeService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def current(self, *, now: datetime | None = None) -> ExternalRiskRegimeResponse:
        record = self._current_record(self._utc_now(now))
        if record is None:
            return self.default_response()
        return ExternalRiskRegimeResponse.model_validate(record)

    def update(
        self,
        request: ExternalRiskRegimeUpdateRequest,
        *,
        now: datetime | None = None,
    ) -> ExternalRiskRegimeResponse:
        as_of = self._utc_now(now)
        self._validate_request(request, as_of)
        reason = (request.reason or "").strip()
        if not reason:
            reason = DEFAULT_EXTERNAL_RISK_REASON
        source = request.source.strip() if request.source else "manual"

        self.db.execute(
            update(ExternalRiskRegime)
            .where(ExternalRiskRegime.is_active.is_(True))
            .values(is_active=False)
        )
        record = ExternalRiskRegime(
            mode=request.mode,
            reason=reason,
            source=source,
            expires_at=self._as_utc(request.expires_at)
            if request.expires_at is not None
            else None,
            is_active=True,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return ExternalRiskRegimeResponse.model_validate(record)

    @staticmethod
    def default_response() -> ExternalRiskRegimeResponse:
        return ExternalRiskRegimeResponse(
            id=None,
            mode="normal",
            reason=DEFAULT_EXTERNAL_RISK_REASON,
            source="default",
            is_active=True,
            expires_at=None,
            created_at=None,
            updated_at=None,
        )

    def _current_record(self, now: datetime) -> ExternalRiskRegime | None:
        return self.db.execute(
            select(ExternalRiskRegime)
            .where(
                ExternalRiskRegime.is_active.is_(True),
                (
                    (ExternalRiskRegime.expires_at.is_(None))
                    | (ExternalRiskRegime.expires_at > now)
                ),
            )
            .order_by(
                ExternalRiskRegime.created_at.desc(),
                ExternalRiskRegime.id.desc(),
            )
            .limit(1)
        ).scalar_one_or_none()

    @staticmethod
    def _validate_request(
        request: ExternalRiskRegimeUpdateRequest,
        now: datetime,
    ) -> None:
        if request.mode not in EXTERNAL_RISK_REGIME_MODES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="mode must be normal, elevated, or severe",
            )
        if request.mode in {"elevated", "severe"} and not (request.reason or "").strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="reason is required for elevated or severe external risk mode",
            )
        if request.source is not None and not request.source.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source must not be blank",
            )
        if request.expires_at is not None and ExternalRiskRegimeService._as_utc(request.expires_at) <= now:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expires_at must be in the future",
            )

    @staticmethod
    def _utc_now(value: datetime | None = None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        return ExternalRiskRegimeService._as_utc(value)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
