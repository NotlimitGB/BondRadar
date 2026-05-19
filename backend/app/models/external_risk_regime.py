from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, String, Text, func, true
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


EXTERNAL_RISK_REGIME_MODES = {"normal", "elevated", "severe"}
EXTERNAL_RISK_REGIME_SOURCES = {"default", "manual"}

EXTERNAL_RISK_REGIME_MODE_SQL = "mode in ('normal', 'elevated', 'severe')"
EXTERNAL_RISK_REGIME_SOURCE_SQL = "length(source) > 0"


class ExternalRiskRegime(Base):
    __tablename__ = "external_risk_regimes"
    __table_args__ = (
        CheckConstraint(
            EXTERNAL_RISK_REGIME_MODE_SQL,
            name="external_risk_regime_mode_allowed",
        ),
        CheckConstraint(
            EXTERNAL_RISK_REGIME_SOURCE_SQL,
            name="external_risk_regime_source_present",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="manual",
        server_default="manual",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default=true(),
        index=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
