from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import inspect, select

from app.models.bond import Bond
from app.models.company import Company
from app.models.credit_risk_evidence import (
    CreditDefaultEvent,
    CreditRatingEvent,
    CreditRiskSourceArtifact,
)
from app.models.legal_issuer import LegalIssuer
from app.services.credit_risk_evidence import (
    CreditRiskEvidenceCollision,
    CreditRiskEvidenceError,
    CreditRiskEvidenceStore,
    CreditRiskIdentityResolver,
    DefaultClass,
    DefaultEventInput,
    IdentityResolutionState,
    ObligationType,
    PublicationPrecision,
    RatingEventInput,
    RatingTarget,
    SourceArtifactInput,
    SourceKind,
    SourceProvider,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 16, 10, 0, tzinfo=UTC)
ISIN = "RU000A123456"
INN = "7701234567"


def _artifact_input(provider=SourceProvider.ACRA, content=b"official evidence"):
    return SourceArtifactInput(
        provider=provider,
        kind=(
            SourceKind.DEFAULT_INFORMATION
            if provider is SourceProvider.MOEX
            else SourceKind.RATING_RELEASE
        ),
        source_url=f"https://example.invalid/{provider.value.lower()}/event",
        content_type="application/pdf",
        content_bytes=content,
        retrieved_at=NOW,
    )


def _rating_input(**overrides):
    base = RatingEventInput(
        agency=SourceProvider.ACRA,
        target=RatingTarget.LEGAL_ISSUER,
        source_object_id="acra-event-1",
        source_issuer_inn=INN,
        source_bond_isin=None,
        source_name_raw="Exact issuer title is evidence only",
        event_date=date(2026, 9, 15),
        publication_precision=PublicationPrecision.DATE,
        publication_date=date(2026, 9, 15),
        publication_at=None,
        rating_scale_raw="ACRA national scale",
        rating_value_raw="AA(RU)",
        rating_outlook_raw="Stable",
    )
    return replace(base, **overrides)


def _default_input(**overrides):
    base = DefaultEventInput(
        source_object_id="moex-default-1",
        source_bond_isin=ISIN,
        source_name_raw="Bond title",
        event_date=date(2026, 9, 14),
        publication_precision=PublicationPrecision.TIMESTAMP,
        publication_date=None,
        publication_at=datetime(2026, 9, 14, 12, 30, tzinfo=UTC),
        default_class=DefaultClass.TECHNICAL_DEFAULT,
        obligation_type=ObligationType.COUPON,
        default_status_raw="technical default",
        default_reason_raw="raw MOEX reason",
        amount_raw="1000.00 RUB",
    )
    return replace(base, **overrides)


def _seed_issuer(db_session, *, source_id="issuer-1", state="verified"):
    row = LegalIssuer(
        identity_source="moex_security_reference",
        source_issuer_id=source_id,
        resolution_state=state,
        issuer_title="Issuer",
        issuer_inn=INN,
        last_resolved_at=NOW,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _seed_bond(db_session):
    company = Company(name="Company", ticker="TASK263")
    db_session.add(company)
    db_session.flush()
    bond = Bond(company_id=company.id, isin=ISIN, name="Bond")
    db_session.add(bond)
    db_session.flush()
    return bond


def test_artifact_hash_replay_changed_bytes_and_model_contract(db_session) -> None:
    store = CreditRiskEvidenceStore(db_session)
    first = store.persist_source_artifact(_artifact_input())
    replay = store.persist_source_artifact(
        replace(_artifact_input(), retrieved_at=NOW + timedelta(days=1))
    )
    changed = store.persist_source_artifact(_artifact_input(content=b"restated bytes"))

    assert first.inserted is True
    assert replay.inserted is False
    assert replay.row.id == first.row.id
    assert replay.row.retrieved_at == first.row.retrieved_at
    assert changed.inserted is True and changed.row.id != first.row.id
    assert len(first.row.content_sha256) == 64
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_source_artifact(_artifact_input(content=b""))

    inspector = inspect(db_session.get_bind())
    artifact_fks = inspector.get_foreign_keys("credit_rating_events")
    assert all(item["options"].get("ondelete") == "RESTRICT" for item in artifact_fks)


def test_exact_identity_resolution_has_no_name_fallback(db_session) -> None:
    resolver = CreditRiskIdentityResolver(db_session)
    assert resolver.resolve_issuer_inn(INN).state is IdentityResolutionState.UNRESOLVED
    issuer = _seed_issuer(db_session)
    resolution = resolver.resolve_issuer_inn(INN)
    assert resolution.legal_issuer_id == issuer.id
    assert resolution.state is IdentityResolutionState.RESOLVED

    _seed_issuer(db_session, source_id="issuer-2")
    assert resolver.resolve_issuer_inn(INN).state is IdentityResolutionState.AMBIGUOUS
    assert resolver.resolve_bond_isin(ISIN).state is IdentityResolutionState.UNRESOLVED
    bond = _seed_bond(db_session)
    assert resolver.resolve_bond_isin(ISIN).bond_id == bond.id
    with pytest.raises(CreditRiskEvidenceError):
        resolver.resolve_issuer_inn("Issuer title")
    with pytest.raises(CreditRiskEvidenceError):
        resolver.resolve_bond_isin(ISIN.lower())


def test_rating_events_preserve_raw_semantics_and_publication_precision(db_session) -> None:
    issuer = _seed_issuer(db_session)
    store = CreditRiskEvidenceStore(db_session)
    artifact = store.persist_source_artifact(_artifact_input()).row
    first = store.persist_rating_event(artifact, _rating_input())
    replay = store.persist_rating_event(artifact, _rating_input())

    assert first.inserted is True and replay.inserted is False
    assert first.row.legal_issuer_id == issuer.id
    assert first.row.bond_id is None
    assert first.row.rating_value_raw == "AA(RU)"
    assert first.row.rating_outlook_raw == "Stable"
    assert first.row.publication_date == date(2026, 9, 15)
    assert first.row.publication_at is None
    assert not hasattr(first.row, "score")

    withdrawn = store.persist_rating_event(
        artifact,
        _rating_input(
            source_object_id="acra-event-2",
            rating_value_raw=None,
            rating_outlook_raw=None,
            rating_action_raw="Withdrawn",
            publication_precision=PublicationPrecision.UNKNOWN,
            publication_date=None,
        ),
    )
    assert withdrawn.row.rating_value_raw is None
    assert withdrawn.row.rating_action_raw == "Withdrawn"
    assert withdrawn.row.publication_date is None
    assert withdrawn.row.publication_at is None

    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(
            artifact,
            _rating_input(
                source_object_id="empty",
                rating_value_raw=None,
                rating_outlook_raw=None,
                rating_action_raw=None,
            ),
        )
    with pytest.raises(CreditRiskEvidenceError):
        store.persist_rating_event(
            artifact,
            _rating_input(
                publication_precision=PublicationPrecision.UNKNOWN,
                publication_date=None,
                publication_at=NOW,
            ),
        )


def test_bond_rating_and_default_are_issue_specific_and_idempotent(db_session) -> None:
    bond = _seed_bond(db_session)
    store = CreditRiskEvidenceStore(db_session)
    rating_artifact = store.persist_source_artifact(_artifact_input()).row
    bond_rating = store.persist_rating_event(
        rating_artifact,
        _rating_input(
            target=RatingTarget.BOND,
            source_object_id="issue-rating",
            source_issuer_inn=None,
            source_bond_isin=ISIN,
        ),
    )
    moex_artifact = store.persist_source_artifact(
        _artifact_input(provider=SourceProvider.MOEX)
    ).row
    technical = store.persist_default_event(moex_artifact, _default_input())
    ordinary = store.persist_default_event(
        moex_artifact,
        _default_input(
            source_object_id="moex-default-2",
            default_class=DefaultClass.DEFAULT,
            obligation_type=ObligationType.PRINCIPAL,
        ),
    )
    replay = store.persist_default_event(moex_artifact, _default_input())

    assert bond_rating.row.bond_id == bond.id
    assert bond_rating.row.legal_issuer_id is None
    assert technical.row.default_class == "TECHNICAL_DEFAULT"
    assert ordinary.row.default_class == "DEFAULT"
    assert replay.inserted is False
    assert technical.row.bond_id == bond.id
    assert not hasattr(technical.row, "legal_issuer_id")


def test_resolution_change_for_same_source_event_is_collision(db_session) -> None:
    store = CreditRiskEvidenceStore(db_session)
    artifact = store.persist_source_artifact(_artifact_input()).row
    unresolved = store.persist_rating_event(artifact, _rating_input())
    assert unresolved.row.resolution_state == "UNRESOLVED"
    _seed_issuer(db_session)
    with pytest.raises(CreditRiskEvidenceCollision):
        store.persist_rating_event(artifact, _rating_input())


def test_store_flushes_without_committing_and_caller_can_roll_back(db_session) -> None:
    store = CreditRiskEvidenceStore(db_session)
    store.persist_source_artifact(_artifact_input())
    assert db_session.in_transaction()
    db_session.rollback()
    assert db_session.execute(select(CreditRiskSourceArtifact)).scalars().all() == []
