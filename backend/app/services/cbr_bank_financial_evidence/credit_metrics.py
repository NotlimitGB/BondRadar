from __future__ import annotations

from contextlib import nullcontext
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
    CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION,
    CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION,
    CbrBankCreditMetric,
    CbrBankNormalizedObservation,
)
from app.services.cbr_legal_issuer_bridge.contracts import canonical_regn

from .fingerprints import sha256_canonical


SUPPORTED_CREDIT_FORMS = ("0409123", "0409135")
FORM_123_METRIC_KEYS = {
    "000": "CBR_123_000",
    "102": "CBR_123_102",
    "105": "CBR_123_105",
    "203": "CBR_123_203",
}
FORM_135_METRIC_KEYS = {
    "N1.0": "CBR_135_N1_0",
    "N1.1": "CBR_135_N1_1",
    "N1.2": "CBR_135_N1_2",
    "N1.3": "CBR_135_N1_3",
    "N2": "CBR_135_N2",
    "N3": "CBR_135_N3",
    "N4": "CBR_135_N4",
    "N15": "CBR_135_N15",
    "N15.1": "CBR_135_N15_1",
    "N16": "CBR_135_N16",
    "N16.1": "CBR_135_N16_1",
    "N16.2": "CBR_135_N16_2",
    "N18": "CBR_135_N18",
    "N27": "CBR_135_N27",
}
ALL_METRIC_KEYS = tuple(
    sorted((*FORM_123_METRIC_KEYS.values(), *FORM_135_METRIC_KEYS.values()))
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class CreditMetricFamily(StrEnum):
    REGULATORY_CAPITAL = "REGULATORY_CAPITAL"
    REGULATORY_RATIO = "REGULATORY_RATIO"


class UnsupportedMetricSource(ValueError):
    code = "UNSUPPORTED_METRIC_SOURCE"


class CreditMetricSemanticCollision(ValueError):
    code = "CREDIT_METRIC_SEMANTIC_COLLISION"


@dataclass(frozen=True, slots=True)
class CreditMetricDraft:
    normalized_observation_id: int
    subject_regn: str
    report_date: date
    metric_key: str
    metric_family: CreditMetricFamily
    source_form: str
    source_code: str
    metric_value: Decimal
    metric_unit: str
    metric_fingerprint: str


@dataclass(frozen=True, slots=True)
class CreditMetricWriteCounts:
    inserted: int = 0
    reused: int = 0


def _unsupported() -> None:
    raise UnsupportedMetricSource("normalized observation is not reviewed by v1")


def _mapping(
    form: str, source_code: str
) -> tuple[str, CreditMetricFamily, str] | None:
    if form == "0409123":
        key = FORM_123_METRIC_KEYS.get(source_code)
        return (
            (key, CreditMetricFamily.REGULATORY_CAPITAL, "RUB")
            if key is not None
            else None
        )
    if form == "0409135":
        key = FORM_135_METRIC_KEYS.get(source_code)
        return (
            (key, CreditMetricFamily.REGULATORY_RATIO, "PERCENT")
            if key is not None
            else None
        )
    return None


def credit_metric_values(draft: CreditMetricDraft) -> dict[str, Any]:
    return {
        "contract_version": CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION,
        "normalized_observation_id": draft.normalized_observation_id,
        "subject_regn": draft.subject_regn,
        "report_date": draft.report_date,
        "metric_key": draft.metric_key,
        "metric_family": draft.metric_family.value,
        "source_form": draft.source_form,
        "source_code": draft.source_code,
        "metric_value": draft.metric_value,
        "metric_unit": draft.metric_unit,
        "metric_fingerprint": draft.metric_fingerprint,
    }


def assert_credit_metric_matches(
    row: CbrBankCreditMetric, draft: CreditMetricDraft
) -> None:
    for name, expected in credit_metric_values(draft).items():
        if getattr(row, name) != expected:
            raise CreditMetricSemanticCollision("credit metric semantic collision")


def project_credit_metric(
    normalized: CbrBankNormalizedObservation,
) -> CreditMetricDraft | None:
    if not isinstance(normalized, CbrBankNormalizedObservation):
        _unsupported()
    if normalized.form not in SUPPORTED_CREDIT_FORMS:
        return None
    if (
        normalized.contract_version
        != CBR_BANK_NORMALIZED_OBSERVATION_CONTRACT_VERSION
        or not isinstance(normalized.id, int)
        or isinstance(normalized.id, bool)
        or normalized.id <= 0
        or not isinstance(normalized.report_date, date)
        or not isinstance(normalized.source_code, str)
        or not normalized.source_code
        or normalized.source_code != normalized.source_code.strip()
        or not isinstance(normalized.normalization_fingerprint, str)
        or not _SHA256.fullmatch(normalized.normalization_fingerprint)
    ):
        _unsupported()
    try:
        subject_regn = canonical_regn(normalized.subject_regn)
    except (TypeError, ValueError):
        _unsupported()
    if subject_regn != normalized.subject_regn:
        _unsupported()

    mapping = _mapping(normalized.form, normalized.source_code)
    if normalized.value_state == "SOURCE_VALUE_UNAVAILABLE":
        if normalized.normalized_value is not None:
            _unsupported()
        return None
    if (
        normalized.value_state != "VALUE"
        or normalized.source_disclosure_state != "PUBLIC_VALUE"
        or not isinstance(normalized.normalized_value, Decimal)
        or not normalized.normalized_value.is_finite()
    ):
        _unsupported()
    if mapping is None:
        _unsupported()

    metric_key, family, metric_unit = mapping
    if normalized.normalized_unit != metric_unit:
        _unsupported()
    if normalized.form == "0409123":
        if (
            normalized.normalized_currency != "RUB"
            or normalized.transformation_kind != "SCALE_BY_SOURCE_MULTIPLIER"
            or normalized.applied_multiplier != 1000
        ):
            _unsupported()
    elif (
        normalized.normalized_currency is not None
        or normalized.transformation_kind != "IDENTITY"
        or normalized.applied_multiplier is not None
    ):
        _unsupported()

    metric_fingerprint = sha256_canonical(
        {
            "contract_version": CBR_BANK_CREDIT_METRIC_CONTRACT_VERSION,
            "normalization_fingerprint": normalized.normalization_fingerprint,
            "metric_key": metric_key,
            "metric_family": family,
            "metric_value": normalized.normalized_value,
            "metric_unit": metric_unit,
        }
    )
    return CreditMetricDraft(
        normalized_observation_id=normalized.id,
        subject_regn=subject_regn,
        report_date=normalized.report_date,
        metric_key=metric_key,
        metric_family=family,
        source_form=normalized.form,
        source_code=normalized.source_code,
        metric_value=normalized.normalized_value,
        metric_unit=metric_unit,
        metric_fingerprint=metric_fingerprint,
    )


def _chunks(values: list[int], size: int = 500) -> Iterable[list[int]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


class CbrBankCreditMetricStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _savepoint(self):
        return (
            nullcontext()
            if self.session.get_bind().dialect.name == "sqlite"
            else self.session.begin_nested()
        )

    def persist(self, drafts: Iterable[CreditMetricDraft]) -> CreditMetricWriteCounts:
        planned = tuple(drafts)
        if any(not isinstance(item, CreditMetricDraft) for item in planned):
            raise TypeError("drafts must contain CreditMetricDraft values")
        normalized_ids = [item.normalized_observation_id for item in planned]
        if len(set(normalized_ids)) != len(normalized_ids):
            raise ValueError("normalized observation ids must be unique per batch")

        existing: dict[int, CbrBankCreditMetric] = {}
        with self.session.no_autoflush:
            for batch in _chunks(normalized_ids):
                rows = self.session.execute(
                    select(CbrBankCreditMetric).where(
                        CbrBankCreditMetric.normalized_observation_id.in_(batch)
                    )
                ).scalars()
                existing.update(
                    {row.normalized_observation_id: row for row in rows}
                )

        additions: list[CbrBankCreditMetric] = []
        reused = 0
        for draft in planned:
            row = existing.get(draft.normalized_observation_id)
            if row is not None:
                assert_credit_metric_matches(row, draft)
                reused += 1
            else:
                additions.append(CbrBankCreditMetric(**credit_metric_values(draft)))

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
                        row.normalized_observation_id: row
                        for row in self.session.execute(
                            select(CbrBankCreditMetric).where(
                                CbrBankCreditMetric.normalized_observation_id.in_(
                                    [
                                        item.normalized_observation_id
                                        for item in additions
                                    ]
                                )
                            )
                        ).scalars()
                    }
                if len(raced) != len(additions):
                    raise
                draft_by_id = {
                    item.normalized_observation_id: item for item in planned
                }
                for normalized_id, row in raced.items():
                    assert_credit_metric_matches(row, draft_by_id[normalized_id])
                reused += len(additions)
                additions = []
        self.session.flush()
        return CreditMetricWriteCounts(inserted=len(additions), reused=reused)
