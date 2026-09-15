from __future__ import annotations

from contextlib import nullcontext
from copy import deepcopy
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
import re
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
    CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    CBR_DISCLOSURE_STATES,
    CbrBankNormalizedObservation,
    CbrBankRawObservation,
)
from app.services.cbr_legal_issuer_bridge.contracts import canonical_regn

from .fingerprints import canonical_value, sha256_canonical


CBR_BANK_SOURCE_ITEM_NAMESPACE = "cbr-bank-source-item-v1"
SUPPORTED_FORMS = ("0409101", "0409102", "0409123", "0409135")
MONETARY_FORMS = frozenset(("0409101", "0409102", "0409123"))
_EXPECTED_DIMENSIONS = {
    "0409101": ("PLAN", "NUM_SC", "A_P"),
    "0409102": ("CODE",),
    "0409123": ("C1",),
    "0409135": ("C1_3",),
}
_PRIMARY_DIMENSION = {
    "0409101": "NUM_SC",
    "0409102": "CODE",
    "0409123": "C1",
    "0409135": "C1_3",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class NormalizedValueState(StrEnum):
    VALUE = "VALUE"
    SOURCE_VALUE_UNAVAILABLE = "SOURCE_VALUE_UNAVAILABLE"


class NormalizationTransformationKind(StrEnum):
    SCALE_BY_SOURCE_MULTIPLIER = "SCALE_BY_SOURCE_MULTIPLIER"
    IDENTITY = "IDENTITY"


class UnsupportedNormalizationInput(ValueError):
    code = "UNSUPPORTED_NORMALIZATION_INPUT"


class NormalizationSemanticCollision(ValueError):
    code = "NORMALIZATION_SEMANTIC_COLLISION"


@dataclass(frozen=True, slots=True)
class NormalizedObservationDraft:
    raw_observation_id: int
    form: str
    report_date: date
    subject_regn: str
    source_date: date | None
    source_code: str
    source_subcode: str | None
    source_dimensions: tuple[tuple[str, Any], ...]
    source_disclosure_state: str
    value_state: NormalizedValueState
    normalized_value: Decimal | None
    normalized_unit: str
    normalized_currency: str | None
    transformation_kind: NormalizationTransformationKind
    applied_multiplier: int | None
    item_fingerprint: str
    normalization_fingerprint: str


@dataclass(frozen=True, slots=True)
class NormalizedWriteCounts:
    inserted: int = 0
    reused: int = 0


def _fail() -> None:
    raise UnsupportedNormalizationInput("raw observation is not supported by v1")


def _dimensions(value: Any, *, form: str) -> tuple[tuple[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        _fail()
    pairs: list[tuple[str, Any]] = []
    for item in value:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            _fail()
        name, dimension_value = item
        if not isinstance(name, str) or not name or name != name.strip():
            _fail()
        try:
            canonical_value(dimension_value)
        except (TypeError, ValueError):
            _fail()
        pairs.append((name, deepcopy(dimension_value)))
    expected = _EXPECTED_DIMENSIONS[form]
    if len(pairs) != len(expected) or set(name for name, _ in pairs) != set(expected):
        _fail()
    if len({name for name, _ in pairs}) != len(pairs):
        _fail()
    return tuple(pairs)


def _canonical_primary_dimension(form: str, value: Any) -> Any:
    # Task251 preserves the source C1_3 lexical code (Cyrillic En) in dimensions
    # while exposing the documented canonical source_code with Latin N.
    if form == "0409135" and isinstance(value, str) and value.startswith("Н"):
        return "N" + value[1:]
    return value


def normalized_observation_values(
    draft: NormalizedObservationDraft,
) -> dict[str, Any]:
    return {
        "contract_version": CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
        "raw_observation_id": draft.raw_observation_id,
        "form": draft.form,
        "report_date": draft.report_date,
        "subject_regn": draft.subject_regn,
        "source_date": draft.source_date,
        "source_code": draft.source_code,
        "source_subcode": draft.source_subcode,
        "source_dimensions": [list(item) for item in draft.source_dimensions],
        "source_disclosure_state": draft.source_disclosure_state,
        "value_state": draft.value_state.value,
        "normalized_value": draft.normalized_value,
        "normalized_unit": draft.normalized_unit,
        "normalized_currency": draft.normalized_currency,
        "transformation_kind": draft.transformation_kind.value,
        "applied_multiplier": draft.applied_multiplier,
        "item_fingerprint": draft.item_fingerprint,
        "normalization_fingerprint": draft.normalization_fingerprint,
    }


def assert_normalized_observation_matches(
    row: CbrBankNormalizedObservation,
    draft: NormalizedObservationDraft,
) -> None:
    expected = normalized_observation_values(draft)
    for name, value in expected.items():
        actual = getattr(row, name)
        if name == "source_dimensions":
            equal = canonical_value(actual) == canonical_value(value)
        else:
            equal = actual == value
        if not equal:
            raise NormalizationSemanticCollision(
                "normalized observation semantic collision"
            )


def normalize_raw_observation(
    raw: CbrBankRawObservation,
) -> NormalizedObservationDraft:
    if not isinstance(raw, CbrBankRawObservation):
        _fail()
    if raw.contract_version != CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION:
        _fail()
    if not isinstance(raw.id, int) or isinstance(raw.id, bool) or raw.id <= 0:
        _fail()
    form = raw.form
    if form not in SUPPORTED_FORMS:
        _fail()
    if not isinstance(raw.report_date, date) or (
        raw.source_date is not None and not isinstance(raw.source_date, date)
    ):
        _fail()
    try:
        regn = canonical_regn(raw.subject_regn)
    except (TypeError, ValueError):
        _fail()
    if regn != raw.subject_regn:
        _fail()
    if (
        not isinstance(raw.source_code, str)
        or not raw.source_code
        or raw.source_code != raw.source_code.strip()
    ):
        _fail()
    if raw.source_subcode is not None and not isinstance(raw.source_subcode, str):
        _fail()
    if not isinstance(raw.observation_fingerprint, str) or not _SHA256.fullmatch(
        raw.observation_fingerprint
    ):
        _fail()

    dimensions = _dimensions(raw.source_dimensions, form=form)
    dimensions_by_name = dict(dimensions)
    if (
        _canonical_primary_dimension(
            form, dimensions_by_name[_PRIMARY_DIMENSION[form]]
        )
        != raw.source_code
    ):
        _fail()

    disclosure = raw.disclosure_state
    if disclosure not in CBR_DISCLOSURE_STATES:
        _fail()
    source_value = raw.parsed_decimal_value
    if source_value is not None and (
        not isinstance(source_value, Decimal) or not source_value.is_finite()
    ):
        _fail()
    if disclosure == "PUBLIC_VALUE":
        if source_value is None:
            _fail()
        value_state = NormalizedValueState.VALUE
    else:
        if source_value is not None:
            _fail()
        value_state = NormalizedValueState.SOURCE_VALUE_UNAVAILABLE

    if form in MONETARY_FORMS:
        if (
            raw.source_unit != "RUB_THOUSANDS"
            or raw.source_currency != "RUB"
            or not isinstance(raw.source_multiplier, int)
            or isinstance(raw.source_multiplier, bool)
            or raw.source_multiplier != 1000
        ):
            _fail()
        normalized_value = (
            source_value * Decimal(1000) if source_value is not None else None
        )
        normalized_unit = "RUB"
        normalized_currency = "RUB"
        transformation = NormalizationTransformationKind.SCALE_BY_SOURCE_MULTIPLIER
        applied_multiplier = 1000
    else:
        if (
            raw.source_unit != "PERCENT"
            or raw.source_currency is not None
            or raw.source_multiplier is not None
        ):
            _fail()
        normalized_value = source_value
        normalized_unit = "PERCENT"
        normalized_currency = None
        transformation = NormalizationTransformationKind.IDENTITY
        applied_multiplier = None

    canonical_dimensions = [
        [name, dimensions_by_name[name]] for name in _EXPECTED_DIMENSIONS[form]
    ]
    item_fingerprint = sha256_canonical(
        {
            "namespace": CBR_BANK_SOURCE_ITEM_NAMESPACE,
            "form": form,
            "source_code": raw.source_code,
            "source_subcode": raw.source_subcode,
            "source_dimensions": canonical_dimensions,
        }
    )
    normalization_fingerprint = sha256_canonical(
        {
            "contract_version": CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
            "raw_observation_fingerprint": raw.observation_fingerprint,
            "item_fingerprint": item_fingerprint,
            "source_disclosure_state": disclosure,
            "value_state": value_state,
            "normalized_value": normalized_value,
            "normalized_unit": normalized_unit,
            "normalized_currency": normalized_currency,
            "transformation_kind": transformation,
            "applied_multiplier": applied_multiplier,
        }
    )
    return NormalizedObservationDraft(
        raw_observation_id=raw.id,
        form=form,
        report_date=raw.report_date,
        subject_regn=regn,
        source_date=raw.source_date,
        source_code=raw.source_code,
        source_subcode=raw.source_subcode,
        source_dimensions=dimensions,
        source_disclosure_state=disclosure,
        value_state=value_state,
        normalized_value=normalized_value,
        normalized_unit=normalized_unit,
        normalized_currency=normalized_currency,
        transformation_kind=transformation,
        applied_multiplier=applied_multiplier,
        item_fingerprint=item_fingerprint,
        normalization_fingerprint=normalization_fingerprint,
    )


def _chunks(values: list[int], size: int = 500) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class CbrBankNormalizedObservationStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _savepoint(self):
        return (
            nullcontext()
            if self.session.get_bind().dialect.name == "sqlite"
            else self.session.begin_nested()
        )

    def persist(
        self, drafts: Iterable[NormalizedObservationDraft]
    ) -> NormalizedWriteCounts:
        planned = tuple(drafts)
        if any(not isinstance(item, NormalizedObservationDraft) for item in planned):
            raise TypeError("drafts must contain NormalizedObservationDraft values")
        raw_ids = [item.raw_observation_id for item in planned]
        if len(set(raw_ids)) != len(raw_ids):
            raise ValueError("raw observation ids must be unique per batch")
        existing: dict[int, CbrBankNormalizedObservation] = {}
        with self.session.no_autoflush:
            for batch in _chunks(raw_ids):
                rows = self.session.execute(
                    select(CbrBankNormalizedObservation).where(
                        CbrBankNormalizedObservation.raw_observation_id.in_(batch)
                    )
                ).scalars()
                existing.update({row.raw_observation_id: row for row in rows})

        additions: list[CbrBankNormalizedObservation] = []
        reused = 0
        for draft in planned:
            row = existing.get(draft.raw_observation_id)
            if row is not None:
                assert_normalized_observation_matches(row, draft)
                reused += 1
                continue
            values = normalized_observation_values(draft)
            additions.append(CbrBankNormalizedObservation(**values))

        if additions:
            try:
                with self._savepoint():
                    self.session.add_all(additions)
                    self.session.flush()
            except IntegrityError:
                if self.session.get_bind().dialect.name == "sqlite":
                    raise
                with self.session.no_autoflush:
                    raced = {
                        row.raw_observation_id: row
                        for row in self.session.execute(
                            select(CbrBankNormalizedObservation).where(
                                CbrBankNormalizedObservation.raw_observation_id.in_(
                                    [item.raw_observation_id for item in additions]
                                )
                            )
                        ).scalars()
                    }
                if len(raced) != len(additions):
                    raise
                draft_by_id = {item.raw_observation_id: item for item in planned}
                for raw_id, row in raced.items():
                    assert_normalized_observation_matches(row, draft_by_id[raw_id])
                reused += len(additions)
                additions = []
        self.session.flush()
        return NormalizedWriteCounts(inserted=len(additions), reused=reused)
