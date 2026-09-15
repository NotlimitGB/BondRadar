from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Float, Numeric, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
    CbrBankArtifactAvailabilityEvidence,
    CbrBankNormalizedObservation,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankSourceArtifact,
)
from app.services.cbr_bank_financial_evidence.normalization import (
    CBR_BANK_SOURCE_ITEM_NAMESPACE,
    CbrBankNormalizedObservationStore,
    NormalizationSemanticCollision,
    NormalizedValueState,
    NormalizationTransformationKind,
    UnsupportedNormalizationInput,
    normalize_raw_observation,
)
from app.services.cbr_bank_financial_evidence.store import (
    CbrBankRawFinancialEvidenceStore,
)
from app.services.cbr_bank_reporting.bundle import CbrBankRegulatoryBundleService
from app.services.cbr_bank_reporting.contracts import (
    CbrArtifactReference,
    CbrBankArtifact,
    CbrBankForm,
)


FIXTURES = Path(__file__).parent / "fixtures" / "cbr_bank_reporting"
REPORT_DATE = date(2026, 8, 1)
OBSERVED_AT = datetime(2026, 8, 30, 12, tzinfo=timezone.utc)
EXPECTED_ROWS = {
    "0409101": 25_654,
    "0409102": 10_079,
    "0409123": 1_400,
    "0409135": 1_709,
}


def _raw(
    *,
    raw_id: int = 1,
    form: str = "0409101",
    value: Decimal | None = Decimal("123.456"),
    disclosure: str = "PUBLIC_VALUE",
) -> CbrBankRawObservation:
    dimensions = {
        "0409101": [["PLAN", "A"], ["NUM_SC", "100"], ["A_P", "1"]],
        "0409102": [["CODE", "100"]],
        "0409123": [["C1", "100"]],
        "0409135": [["C1_3", "N1.0"]],
    }[form]
    source_code = dict(dimensions)[
        {"0409101": "NUM_SC", "0409102": "CODE", "0409123": "C1", "0409135": "C1_3"}[form]
    ]
    monetary = form != "0409135"
    return CbrBankRawObservation(
        id=raw_id,
        snapshot_id=1,
        reporting_subject_id=1,
        contract_version="cbr-bank-raw-financial-evidence-v1",
        form=form,
        report_date=REPORT_DATE,
        subject_regn="1",
        archive_member_name="VALUE.DBF",
        source_row_number=raw_id,
        source_row_fingerprint=f"{raw_id % 10}" * 64,
        source_value_field="VALUE",
        source_code=source_code,
        source_subcode=None,
        source_dimensions=dimensions,
        source_fields_sha256="a" * 64,
        raw_value_text="" if value is None else format(value, "f"),
        parsed_decimal_value=value,
        disclosure_state=disclosure,
        source_unit="RUB_THOUSANDS" if monetary else "PERCENT",
        source_currency="RUB" if monetary else None,
        source_multiplier=1000 if monetary else None,
        source_date=None,
        parser_contract_version="task251-test",
        ingested_at=OBSERVED_AT,
        observation_fingerprint=hashlib.sha256(f"raw-{raw_id}".encode()).hexdigest(),
    )


def _artifact(form: CbrBankForm) -> CbrBankArtifact:
    filename = f"{form.short_code}-20260801.rar"
    content = (FIXTURES / filename).read_bytes()
    return CbrBankArtifact(
        reference=CbrArtifactReference(
            form=form,
            source_href=f"/vfs/credit/forms/{filename}",
            source_url=f"https://www.cbr.ru/vfs/credit/forms/{filename}",
            artifact_filename=filename,
            report_date=REPORT_DATE,
            discovered_at=OBSERVED_AT,
        ),
        content=content,
        content_sha256=hashlib.sha256(content).hexdigest(),
        compressed_size=len(content),
        content_type="application/octet-stream",
        retrieved_at=OBSERVED_AT,
    )


@pytest.fixture(scope="module")
def task251_bundle():
    return CbrBankRegulatoryBundleService().build_snapshot(
        report_date=REPORT_DATE,
        artifacts=tuple(_artifact(form) for form in CbrBankForm),
    )


@pytest.mark.parametrize("form", ["0409101", "0409102", "0409123"])
def test_monetary_normalization_is_exact_decimal_scaling(form: str) -> None:
    draft = normalize_raw_observation(_raw(form=form))
    assert draft.normalized_value == Decimal("123456.000")
    assert draft.normalized_unit == "RUB"
    assert draft.normalized_currency == "RUB"
    assert draft.applied_multiplier == 1000
    assert (
        draft.transformation_kind
        == NormalizationTransformationKind.SCALE_BY_SOURCE_MULTIPLIER
    )
    assert isinstance(draft.normalized_value, Decimal)


def test_percentage_is_identity_and_blank_is_never_zero() -> None:
    percentage = normalize_raw_observation(
        _raw(form="0409135", value=Decimal("12.345"))
    )
    assert percentage.normalized_value == Decimal("12.345")
    assert percentage.normalized_unit == "PERCENT"
    assert percentage.normalized_currency is None
    assert percentage.applied_multiplier is None
    assert percentage.transformation_kind == NormalizationTransformationKind.IDENTITY

    blank = normalize_raw_observation(
        _raw(
            form="0409102",
            value=None,
            disclosure="PUBLIC_VALUE_BLANK",
        )
    )
    assert blank.value_state == NormalizedValueState.SOURCE_VALUE_UNAVAILABLE
    assert blank.normalized_value is None
    assert blank.normalized_value != Decimal(0)
    assert blank.source_disclosure_state == "PUBLIC_VALUE_BLANK"


def test_135_preserves_cyrillic_source_dimension_and_validates_task251_code() -> None:
    raw = _raw(form="0409135")
    raw.source_dimensions = [["C1_3", "Н1.0"]]
    draft = normalize_raw_observation(raw)
    assert draft.source_code == "N1.0"
    assert draft.source_dimensions == (("C1_3", "Н1.0"),)


def test_item_identity_is_order_stable_but_dimensions_are_preserved() -> None:
    raw = _raw()
    reordered = _raw(raw_id=2)
    reordered.source_dimensions = list(reversed(reordered.source_dimensions))
    left = normalize_raw_observation(raw)
    right = normalize_raw_observation(reordered)
    assert left.item_fingerprint == right.item_fingerprint
    assert left.source_dimensions != right.source_dimensions
    assert CBR_BANK_SOURCE_ITEM_NAMESPACE == "cbr-bank-source-item-v1"


def test_source_date_is_preserved_and_runtime_timestamps_do_not_select_pit() -> None:
    raw = _raw()
    raw.source_date = date(2026, 7, 31)
    first = normalize_raw_observation(raw)
    raw.ingested_at = datetime(2099, 1, 1, tzinfo=timezone.utc)
    second = normalize_raw_observation(raw)
    assert first.source_date == date(2026, 7, 31)
    assert first == second


def test_unsupported_form_and_exact_dimension_disagreement_fail_closed() -> None:
    unsupported = _raw()
    unsupported.form = "0409999"
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(unsupported)
    mismatch = _raw()
    mismatch.source_dimensions = [
        ["PLAN", "A"],
        ["NUM_SC", "different"],
        ["A_P", "1"],
    ]
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(mismatch)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: setattr(row, "source_multiplier", 1),
        lambda row: setattr(row, "source_currency", None),
        lambda row: setattr(row, "source_unit", "RUB"),
        lambda row: setattr(row, "source_code", ""),
        lambda row: setattr(row, "source_dimensions", [["NUM_SC", "different"]]),
        lambda row: setattr(row, "parsed_decimal_value", None),
        lambda row: setattr(row, "parsed_decimal_value", 1.25),
    ],
)
def test_invalid_monetary_inputs_fail_closed(mutation) -> None:
    raw = _raw()
    mutation(raw)
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(raw)


@pytest.mark.parametrize(
    "field,value",
    (("source_currency", "RUB"), ("source_multiplier", 1000), ("source_unit", "RUB")),
)
def test_invalid_percentage_inputs_fail_closed(field: str, value) -> None:
    raw = _raw(form="0409135")
    setattr(raw, field, value)
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(raw)


def test_non_public_numeric_and_public_null_fail_closed() -> None:
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(
            _raw(value=Decimal("1"), disclosure="SUPPRESSED_OR_REDUCED")
        )
    with pytest.raises(UnsupportedNormalizationInput):
        normalize_raw_observation(_raw(value=None, disclosure="PUBLIC_VALUE"))


def test_model_contract_types_constraints_indexes_and_relationship() -> None:
    table = CbrBankNormalizedObservation.__table__
    assert table.name == "cbr_bank_normalized_observations"
    assert CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION == (
        "cbr-bank-normalized-financial-observation-v1"
    )
    assert isinstance(table.c.normalized_value.type, Numeric)
    assert not isinstance(table.c.normalized_value.type, Float)
    assert next(iter(table.c.raw_observation_id.foreign_keys)).ondelete == "RESTRICT"
    assert {tuple(item.columns.keys()) for item in table.constraints if hasattr(item, "columns")} >= {
        ("raw_observation_id",),
        ("normalization_fingerprint",),
    }
    assert {index.name for index in table.indexes} == {
        "ix_cbr_bank_normalized_observations_subject_report",
        "ix_cbr_bank_normalized_observations_form_code_report",
        "ix_cbr_bank_normalized_observations_item_fingerprint",
    }
    assert CbrBankRawObservation.normalized_observation.property.uselist is False


def _persist_normalized_in_batches(session: Session) -> tuple[int, int]:
    inserted = reused = 0
    last_id = 0
    while True:
        rows = list(
            session.execute(
                select(CbrBankRawObservation)
                .where(CbrBankRawObservation.id > last_id)
                .order_by(CbrBankRawObservation.id)
                .limit(2_000)
            ).scalars()
        )
        if not rows:
            break
        counts = CbrBankNormalizedObservationStore(session).persist(
            normalize_raw_observation(row) for row in rows
        )
        inserted += counts.inserted
        reused += counts.reused
        last_id = rows[-1].id
    return inserted, reused


def test_full_approved_fixture_normalizes_one_to_one_and_is_idempotent(
    db_session: Session,
    task251_bundle,
) -> None:
    raw_result = CbrBankRawFinancialEvidenceStore(db_session).persist_bundle(
        task251_bundle,
        observed_at=OBSERVED_AT,
        ingested_at=OBSERVED_AT,
    )
    assert raw_result.observations.inserted == 38_842
    raw_counts = dict(
        db_session.execute(
            select(CbrBankRawObservation.form, func.count())
            .group_by(CbrBankRawObservation.form)
            .order_by(CbrBankRawObservation.form)
        ).all()
    )
    assert raw_counts == EXPECTED_ROWS
    lineage_counts = {
        "raw": db_session.scalar(select(func.count()).select_from(CbrBankRawObservation)),
        "snapshots": db_session.scalar(select(func.count()).select_from(CbrBankReportSnapshot)),
        "artifacts": db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)),
        "availability": db_session.scalar(
            select(func.count()).select_from(CbrBankArtifactAvailabilityEvidence)
        ),
    }

    assert _persist_normalized_in_batches(db_session) == (38_842, 0)
    assert db_session.scalar(
        select(func.count()).select_from(CbrBankNormalizedObservation)
    ) == 38_842
    assert db_session.scalar(
        select(func.count())
        .select_from(CbrBankNormalizedObservation)
        .where(
            CbrBankNormalizedObservation.form == "0409102",
            CbrBankNormalizedObservation.source_disclosure_state
            == "PUBLIC_VALUE_BLANK",
            CbrBankNormalizedObservation.normalized_value.is_(None),
        )
    ) == 6
    assert _persist_normalized_in_batches(db_session) == (0, 38_842)
    for form in ("0409101", "0409102", "0409123"):
        for predicate in (
            CbrBankRawObservation.parsed_decimal_value > 0,
            CbrBankRawObservation.parsed_decimal_value == 0,
            CbrBankRawObservation.parsed_decimal_value < 0,
        ):
            pair = db_session.execute(
                select(CbrBankRawObservation, CbrBankNormalizedObservation)
                .join(
                    CbrBankNormalizedObservation,
                    CbrBankNormalizedObservation.raw_observation_id
                    == CbrBankRawObservation.id,
                )
                .where(CbrBankRawObservation.form == form, predicate)
                .limit(1)
            ).first()
            if pair is not None:
                raw, normalized_row = pair
                assert normalized_row.normalized_value == (
                    raw.parsed_decimal_value * Decimal(1000)
                )
                assert isinstance(normalized_row.normalized_value, Decimal)
    ratio_pair = db_session.execute(
        select(CbrBankRawObservation, CbrBankNormalizedObservation)
        .join(
            CbrBankNormalizedObservation,
            CbrBankNormalizedObservation.raw_observation_id
            == CbrBankRawObservation.id,
        )
        .where(CbrBankRawObservation.form == "0409135")
        .limit(1)
    ).one()
    assert ratio_pair[1].normalized_value == ratio_pair[0].parsed_decimal_value
    assert ratio_pair[1].normalized_unit == "PERCENT"
    assert lineage_counts == {
        "raw": db_session.scalar(select(func.count()).select_from(CbrBankRawObservation)),
        "snapshots": db_session.scalar(select(func.count()).select_from(CbrBankReportSnapshot)),
        "artifacts": db_session.scalar(select(func.count()).select_from(CbrBankSourceArtifact)),
        "availability": db_session.scalar(
            select(func.count()).select_from(CbrBankArtifactAvailabilityEvidence)
        ),
    }
    normalized = db_session.scalar(
        select(CbrBankNormalizedObservation).order_by(CbrBankNormalizedObservation.id)
    )
    assert normalized.raw_observation.snapshot.artifact.content_sha256


def test_two_raw_versions_normalize_independently(db_session: Session) -> None:
    raw_a = _raw(raw_id=101, value=Decimal("10.000"))
    raw_b = _raw(raw_id=102, value=Decimal("11.000"))
    draft_a = normalize_raw_observation(raw_a)
    draft_b = normalize_raw_observation(raw_b)
    assert draft_a.item_fingerprint == draft_b.item_fingerprint
    assert draft_a.normalization_fingerprint != draft_b.normalization_fingerprint
    counts = CbrBankNormalizedObservationStore(db_session).persist(
        (draft_a, draft_b)
    )
    assert counts.inserted == 2
    rows = list(
        db_session.execute(
            select(CbrBankNormalizedObservation).order_by(
                CbrBankNormalizedObservation.raw_observation_id
            )
        ).scalars()
    )
    assert [row.raw_observation_id for row in rows] == [101, 102]
    assert [row.normalized_value for row in rows] == [
        Decimal("10000.000"),
        Decimal("11000.000"),
    ]


def test_persistence_is_caller_owned_and_collision_fails_closed(
    db_session: Session,
) -> None:
    raw = _raw()
    draft = normalize_raw_observation(raw)
    counts = CbrBankNormalizedObservationStore(db_session).persist((draft,))
    assert counts.inserted == 1
    db_session.rollback()
    assert db_session.scalar(
        select(func.count()).select_from(CbrBankNormalizedObservation)
    ) == 0

    row = CbrBankNormalizedObservation(
        contract_version=CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
        raw_observation_id=draft.raw_observation_id,
        form=draft.form,
        report_date=draft.report_date,
        subject_regn=draft.subject_regn,
        source_date=draft.source_date,
        source_code=draft.source_code,
        source_subcode=draft.source_subcode,
        source_dimensions=[list(item) for item in draft.source_dimensions],
        source_disclosure_state=draft.source_disclosure_state,
        value_state=draft.value_state.value,
        normalized_value=Decimal("999"),
        normalized_unit=draft.normalized_unit,
        normalized_currency=draft.normalized_currency,
        transformation_kind=draft.transformation_kind.value,
        applied_multiplier=draft.applied_multiplier,
        item_fingerprint=draft.item_fingerprint,
        normalization_fingerprint="f" * 64,
    )
    db_session.add(row)
    db_session.flush()
    with pytest.raises(NormalizationSemanticCollision):
        CbrBankNormalizedObservationStore(db_session).persist((draft,))


def test_database_constraints_reject_inconsistent_value_state(db_session: Session) -> None:
    draft = normalize_raw_observation(_raw())
    values = {
        "contract_version": CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
        "raw_observation_id": draft.raw_observation_id,
        "form": draft.form,
        "report_date": draft.report_date,
        "subject_regn": draft.subject_regn,
        "source_date": draft.source_date,
        "source_code": draft.source_code,
        "source_subcode": draft.source_subcode,
        "source_dimensions": [list(item) for item in draft.source_dimensions],
        "source_disclosure_state": "PUBLIC_VALUE_BLANK",
        "value_state": "SOURCE_VALUE_UNAVAILABLE",
        "normalized_value": Decimal("0"),
        "normalized_unit": draft.normalized_unit,
        "normalized_currency": draft.normalized_currency,
        "transformation_kind": draft.transformation_kind.value,
        "applied_multiplier": draft.applied_multiplier,
        "item_fingerprint": draft.item_fingerprint,
        "normalization_fingerprint": draft.normalization_fingerprint,
    }
    db_session.add(CbrBankNormalizedObservation(**values))
    with pytest.raises(IntegrityError):
        db_session.flush()
