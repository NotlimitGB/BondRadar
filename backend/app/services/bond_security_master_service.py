from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from math import isfinite
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_security_master_evidence import (
    SECURITY_MASTER_ASSERTION_TYPES,
    SECURITY_MASTER_EVIDENCE_SOURCES,
    BondSecurityMasterEvidence,
)
from app.models.bond_security_master_profile import (
    AMORTIZATION_STRUCTURE_VALUES,
    COUPON_STRUCTURE_VALUES,
    LISTING_STATUS_VALUES,
    OFFER_STRUCTURE_VALUES,
    PERPETUAL_STRUCTURE_VALUES,
    SECURITY_MASTER_CONTRACT_VERSION,
    SUBORDINATION_STRUCTURE_VALUES,
    BondSecurityMasterProfile,
)
from app.services.moex_iss_client import MoexCashflowScheduleResult
from app.services.moex_normalization import canonicalize_moex_currency


_BOARD_CODE = re.compile(r"[A-Z0-9._-]{1,32}")
_PRINCIPAL_DATE_ALIASES = (
    "amortdate",
    "amortizationdate",
    "amort_date",
    "date",
)
_PRINCIPAL_AMOUNT_ALIASES = (
    "value",
    "amortvalue",
    "amortization_value",
)
_PRINCIPAL_PERCENT_ALIASES = (
    "valueprc",
    "amortpercent",
    "amortization_percent",
)
_METADATA_ALIASES = {
    "currency_code": ("currency", "CURRENCY", "currencyid", "CURRENCYID", "faceunit", "FACEUNIT"),
    "nominal_value": ("nominal_value", "NOMINAL_VALUE", "facevalue", "FACEVALUE", "faceval", "FACEVAL", "nominal", "NOMINAL"),
    "coupon_rate": ("coupon_rate", "COUPON_RATE", "couponpercent", "COUPONPERCENT", "coupon_rate_percent"),
    "maturity_date": ("maturity_date", "MATURITY_DATE", "matdate", "MATDATE", "maturitydate", "MATURITYDATE"),
    "coupon_structure": ("is_floating_coupon", "IS_FLOATING_COUPON", "floating_coupon", "FLOATING_COUPON"),
    "amortization_structure": ("has_amortization", "HAS_AMORTIZATION", "amortization", "AMORTIZATION", "amortized", "AMORTIZED"),
    "subordination_structure": ("is_subordinated", "IS_SUBORDINATED", "subordinated", "SUBORDINATED"),
    "perpetual_structure": ("is_perpetual", "IS_PERPETUAL", "perpetual", "PERPETUAL"),
    "offer_structure": ("offer_date", "OFFER_DATE", "offerdate", "OFFERDATE"),
}
_SCALAR_FIELDS = {
    "currency_code": ("currency_state", str),
    "nominal_value": ("nominal_state", Decimal),
    "coupon_rate": ("coupon_rate_state", Decimal),
    "maturity_date": ("maturity_state", date),
    "lot_size": ("lot_size_state", int),
    "trading_board": ("trading_board_state", str),
    "coupon_frequency_per_year": ("coupon_frequency_state", int),
    "coupon_formula": ("coupon_formula_state", str),
    "outstanding_nominal": ("outstanding_nominal_state", Decimal),
}
_CLASSIFICATION_FIELDS = {
    "coupon_structure": COUPON_STRUCTURE_VALUES,
    "amortization_structure": AMORTIZATION_STRUCTURE_VALUES,
    "subordination_structure": SUBORDINATION_STRUCTURE_VALUES,
    "perpetual_structure": PERPETUAL_STRUCTURE_VALUES,
    "offer_structure": OFFER_STRUCTURE_VALUES,
    "listing_status": LISTING_STATUS_VALUES,
}


@dataclass(frozen=True)
class _UsablePrincipalPayment:
    payment_date: date
    amount: Decimal | None
    percent: Decimal | None


@dataclass(frozen=True)
class _PrincipalScheduleClassification:
    value: str
    basis: str
    observed_row_count: int
    usable_row_count: int
    source_tables: list[str]


class BondSecurityMasterService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def record_assertion(
        self,
        *,
        bond: Bond,
        field_name: str,
        source: str,
        assertion_type: str,
        normalized_value: Any,
        observed_at: datetime,
        source_key: str | None = None,
        source_table: str | None = None,
        raw_value: dict[str, Any] | None = None,
        effective_at: datetime | None = None,
    ) -> tuple[BondSecurityMasterEvidence, bool]:
        if bond.id is None:
            raise ValueError("Bond must be persisted before evidence ingestion")
        if source not in SECURITY_MASTER_EVIDENCE_SOURCES:
            raise ValueError("Unsupported security-master evidence source")
        if assertion_type not in SECURITY_MASTER_ASSERTION_TYPES:
            raise ValueError("Unsupported security-master assertion type")
        canonical_value = self._validated_canonical_value(
            field_name, assertion_type, normalized_value
        )
        observed_at = self._aware_timestamp(observed_at, "observed_at")
        if effective_at is not None:
            effective_at = self._aware_timestamp(effective_at, "effective_at")
        source_key = self._bounded_text(source_key, 128)
        source_table = self._bounded_text(source_table, 128)
        normalized = {"value": canonical_value}
        fingerprint = self._fingerprint(
            bond_id=bond.id,
            field_name=field_name,
            source=source,
            source_key=source_key,
            source_table=source_table,
            assertion_type=assertion_type,
            normalized_value=normalized,
            effective_at=effective_at,
        )
        existing = self.db.execute(
            select(BondSecurityMasterEvidence).where(
                BondSecurityMasterEvidence.evidence_fingerprint == fingerprint
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing, False

        evidence = BondSecurityMasterEvidence(
            bond_id=bond.id,
            field_name=field_name,
            source=source,
            source_key=source_key,
            source_table=source_table,
            assertion_type=assertion_type,
            normalized_value_json=normalized,
            raw_value_json=self._narrow_raw_value(raw_value),
            effective_at=effective_at,
            observed_at=observed_at,
            ingestion_at=datetime.now(timezone.utc),
            evidence_fingerprint=fingerprint,
            contract_version=SECURITY_MASTER_CONTRACT_VERSION,
        )
        try:
            with self.db.begin_nested():
                self.db.add(evidence)
                self.db.flush()
        except IntegrityError:
            existing = self.db.execute(
                select(BondSecurityMasterEvidence).where(
                    BondSecurityMasterEvidence.evidence_fingerprint == fingerprint
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing, False
        return evidence, True

    def ingest_moex_metadata(
        self,
        bond: Bond,
        metadata: dict[str, Any],
        *,
        source: str,
        board: str | None,
        board_observed: bool,
        observed_at: datetime,
    ) -> BondSecurityMasterProfile | None:
        if source not in {"moex_universe", "moex_description"}:
            raise ValueError("Unsupported MOEX metadata source")
        source_key = bond.secid or bond.isin or str(bond.id)
        inserted = False

        for field_name in (
            "currency_code",
            "nominal_value",
            "coupon_rate",
            "maturity_date",
            "coupon_structure",
            "amortization_structure",
            "subordination_structure",
            "perpetual_structure",
            "offer_structure",
        ):
            present, raw_field, raw_value = self._metadata_value(
                metadata, _METADATA_ALIASES[field_name]
            )
            if not present or raw_value is None or raw_value == "":
                continue
            normalized = self._normalize_metadata_value(field_name, raw_value)
            if normalized is None:
                continue
            _, created = self.record_assertion(
                bond=bond,
                field_name=field_name,
                source=source,
                assertion_type=(
                    "classification"
                    if field_name in _CLASSIFICATION_FIELDS
                    else "scalar_value"
                ),
                normalized_value=normalized,
                observed_at=observed_at,
                source_key=source_key,
                raw_value={"source_field": raw_field, "value": self._json_scalar(raw_value)},
            )
            inserted = inserted or created

        if board_observed and board is not None:
            normalized_board = str(board).strip().upper()
            if _BOARD_CODE.fullmatch(normalized_board):
                _, created = self.record_assertion(
                    bond=bond,
                    field_name="trading_board",
                    source=source,
                    assertion_type="scalar_value",
                    normalized_value=normalized_board,
                    observed_at=observed_at,
                    source_key=source_key,
                    raw_value={"source_field": "board", "value": normalized_board},
                )
                inserted = inserted or created

        profile = self._profile_for_bond(bond.id)
        if inserted or profile is None and self._has_evidence(bond.id):
            return self.resolve_profile(bond)
        return profile

    def ingest_moex_cashflow_structure(
        self,
        bond: Bond,
        schedule: MoexCashflowScheduleResult,
        *,
        observed_at: datetime,
    ) -> BondSecurityMasterProfile | None:
        inserted = False
        source_key = bond.secid or bond.isin or str(bond.id)
        profile = self._profile_for_bond(bond.id)
        verified_maturity = None
        if (
            profile is not None
            and profile.maturity_state == "verified"
            and profile.maturity_date is not None
        ):
            verified_maturity = profile.maturity_date

        principal_classification = self._classify_moex_principal_schedule(
            schedule.amortizations,
            verified_maturity=verified_maturity,
        )
        if principal_classification is not None:
            _, created = self.record_assertion(
                bond=bond,
                field_name="amortization_structure",
                source="moex_cashflows",
                assertion_type="classification",
                normalized_value=principal_classification.value,
                observed_at=observed_at,
                source_key=source_key,
                source_table=",".join(principal_classification.source_tables),
                raw_value={
                    "classification_basis": principal_classification.basis,
                    "observed_row_count": principal_classification.observed_row_count,
                    "usable_principal_row_count": principal_classification.usable_row_count,
                    "source_tables": principal_classification.source_tables,
                },
            )
            inserted = inserted or created

        if schedule.offers:
            source_tables = sorted(
                {
                    str(row.get("__moex_source_table") or "offers")
                    for row in schedule.offers
                }
            )
            _, created = self.record_assertion(
                bond=bond,
                field_name="offer_structure",
                source="moex_cashflows",
                assertion_type="classification",
                normalized_value="present",
                observed_at=observed_at,
                source_key=source_key,
                source_table=",".join(source_tables),
                raw_value={
                    "observed_row_count": len(schedule.offers),
                    "source_tables": source_tables,
                },
            )
            inserted = inserted or created

        profile = self._profile_for_bond(bond.id)
        if inserted or profile is None and self._has_evidence(bond.id):
            return self.resolve_profile(bond)
        return profile

    @classmethod
    def _classify_moex_principal_schedule(
        cls,
        rows: list[dict[str, Any]],
        *,
        verified_maturity: date | None,
    ) -> _PrincipalScheduleClassification | None:
        usable: list[_UsablePrincipalPayment] = []
        for row in rows:
            payment_date = cls._date(cls._row_value(row, _PRINCIPAL_DATE_ALIASES))
            if payment_date is None:
                continue
            amount = cls._positive_decimal(
                cls._row_value(row, _PRINCIPAL_AMOUNT_ALIASES)
            )
            percent = cls._positive_decimal(
                cls._row_value(row, _PRINCIPAL_PERCENT_ALIASES),
                maximum=Decimal("100"),
            )
            if amount is None and percent is None:
                continue
            usable.append(
                _UsablePrincipalPayment(
                    payment_date=payment_date,
                    amount=amount,
                    percent=percent,
                )
            )

        if not usable:
            return None

        value: str | None = None
        basis: str | None = None
        if any(
            row.percent is not None and Decimal("0") < row.percent < Decimal("100")
            for row in usable
        ):
            value = "amortizing"
            basis = "partial_principal_percent"
        elif len({row.payment_date for row in usable}) >= 2:
            value = "amortizing"
            basis = "multiple_principal_dates"
        elif verified_maturity is not None and any(
            row.payment_date < verified_maturity for row in usable
        ):
            value = "amortizing"
            basis = "principal_before_verified_maturity"
        elif (
            verified_maturity is not None
            and len(usable) == 1
            and usable[0].payment_date == verified_maturity
            and usable[0].percent == Decimal("100")
        ):
            value = "bullet"
            basis = "single_100pct_at_verified_maturity"

        if value is None or basis is None:
            return None
        source_tables = sorted(
            {
                str(row.get("__moex_source_table") or "amortizations")
                for row in rows
            }
        )
        return _PrincipalScheduleClassification(
            value=value,
            basis=basis,
            observed_row_count=len(rows),
            usable_row_count=len(usable),
            source_tables=source_tables,
        )

    def resolve_profile(self, bond: Bond) -> BondSecurityMasterProfile:
        evidence_rows = list(
            self.db.execute(
                select(BondSecurityMasterEvidence)
                .where(BondSecurityMasterEvidence.bond_id == bond.id)
                .order_by(
                    BondSecurityMasterEvidence.field_name,
                    BondSecurityMasterEvidence.evidence_fingerprint,
                )
            ).scalars()
        )
        profile = self._profile_for_bond(bond.id)
        if profile is None:
            profile = BondSecurityMasterProfile(
                bond_id=bond.id,
                contract_version=SECURITY_MASTER_CONTRACT_VERSION,
            )
            self.db.add(profile)

        by_field: dict[str, list[Any]] = {}
        for evidence in evidence_rows:
            if not isinstance(evidence.normalized_value_json, dict):
                raise ValueError("Invalid persisted security-master evidence")
            canonical = self._validated_canonical_value(
                evidence.field_name,
                evidence.assertion_type,
                evidence.normalized_value_json.get("value"),
            )
            by_field.setdefault(evidence.field_name, []).append(canonical)

        for field_name, (state_name, target_type) in _SCALAR_FIELDS.items():
            values = self._distinct(by_field.get(field_name, []))
            if not values:
                setattr(profile, state_name, "unknown")
                setattr(profile, field_name, None)
            elif len(values) == 1:
                setattr(profile, state_name, "verified")
                setattr(profile, field_name, self._profile_value(values[0], target_type))
            else:
                setattr(profile, state_name, "conflict")
                setattr(profile, field_name, None)

        for field_name in _CLASSIFICATION_FIELDS:
            values = self._distinct(by_field.get(field_name, []))
            if not values:
                setattr(profile, field_name, "unknown")
            elif len(values) == 1:
                setattr(profile, field_name, values[0])
            else:
                setattr(profile, field_name, "conflict")

        profile.contract_version = SECURITY_MASTER_CONTRACT_VERSION
        if evidence_rows:
            latest_ingestion = max(
                (
                    row.ingestion_at
                    if row.ingestion_at.tzinfo is not None
                    else row.ingestion_at.replace(tzinfo=timezone.utc)
                )
                for row in evidence_rows
            )
            profile.last_resolved_at = latest_ingestion
        self.db.add(profile)
        self.db.flush()
        return profile

    def _profile_for_bond(self, bond_id: int) -> BondSecurityMasterProfile | None:
        return self.db.execute(
            select(BondSecurityMasterProfile).where(
                BondSecurityMasterProfile.bond_id == bond_id
            )
        ).scalar_one_or_none()

    def _has_evidence(self, bond_id: int) -> bool:
        return self.db.execute(
            select(BondSecurityMasterEvidence.id)
            .where(BondSecurityMasterEvidence.bond_id == bond_id)
            .limit(1)
        ).first() is not None

    @staticmethod
    def _metadata_value(
        metadata: dict[str, Any], aliases: tuple[str, ...]
    ) -> tuple[bool, str | None, Any]:
        raw = metadata.get("raw")
        source = raw if isinstance(raw, dict) else metadata
        lowered = {str(key).lower(): (str(key), value) for key, value in source.items()}
        for alias in aliases:
            match = lowered.get(alias.lower())
            if match is not None:
                return True, match[0], match[1]
        if isinstance(raw, dict):
            normalized_key = aliases[0].lower()
            for key, value in metadata.items():
                if str(key).lower() == normalized_key and value is not None:
                    return True, str(key), value
        return False, None, None

    @classmethod
    def _normalize_metadata_value(cls, field_name: str, raw_value: Any) -> Any | None:
        if field_name == "currency_code":
            return canonicalize_moex_currency(raw_value)
        if field_name in {"nominal_value", "coupon_rate"}:
            value = cls._decimal(raw_value)
            if value is None:
                return None
            if field_name == "nominal_value" and value <= 0:
                return None
            if field_name == "coupon_rate" and value < 0:
                return None
            return cls._canonical_decimal(value)
        if field_name == "maturity_date":
            value = cls._date(raw_value)
            return value.isoformat() if value is not None else None
        if field_name == "offer_structure":
            return "present" if cls._date(raw_value) is not None else None
        flag = cls._boolean(raw_value)
        if flag is None:
            return None
        mappings = {
            "coupon_structure": ("fixed", "floating"),
            "amortization_structure": ("bullet", "amortizing"),
            "subordination_structure": ("senior", "subordinated"),
            "perpetual_structure": ("dated", "perpetual"),
        }
        false_value, true_value = mappings[field_name]
        return true_value if flag else false_value

    @classmethod
    def _validated_canonical_value(
        cls, field_name: str, assertion_type: str, value: Any
    ) -> Any:
        if assertion_type == "classification":
            allowed = _CLASSIFICATION_FIELDS.get(field_name)
            if allowed is None or value not in allowed or value in {"unknown", "conflict"}:
                raise ValueError("Invalid security-master classification assertion")
            return value
        if assertion_type != "scalar_value" or field_name not in _SCALAR_FIELDS:
            raise ValueError("Invalid security-master scalar assertion")
        if field_name == "currency_code":
            currency = canonicalize_moex_currency(value)
            if currency is None:
                raise ValueError("Invalid security-master currency assertion")
            return currency
        if field_name in {"nominal_value", "coupon_rate", "outstanding_nominal"}:
            decimal_value = cls._decimal(value)
            if decimal_value is None:
                raise ValueError("Invalid security-master decimal assertion")
            if field_name != "coupon_rate" and decimal_value <= 0:
                raise ValueError("Security-master decimal assertion must be positive")
            if field_name == "coupon_rate" and decimal_value < 0:
                raise ValueError("Security-master coupon rate cannot be negative")
            return cls._canonical_decimal(decimal_value)
        if field_name == "maturity_date":
            parsed = cls._date(value)
            if parsed is None:
                raise ValueError("Invalid security-master maturity assertion")
            return parsed.isoformat()
        if field_name in {"lot_size", "coupon_frequency_per_year"}:
            if isinstance(value, bool):
                raise ValueError("Invalid security-master integer assertion")
            try:
                integer = int(value)
            except (TypeError, ValueError) as exc:
                raise ValueError("Invalid security-master integer assertion") from exc
            if str(integer) != str(value).strip() or integer <= 0:
                raise ValueError("Security-master integer assertion must be positive")
            return integer
        text = str(value).strip()
        if not text:
            raise ValueError("Invalid security-master text assertion")
        if field_name == "trading_board":
            text = text.upper()
            if not _BOARD_CODE.fullmatch(text):
                raise ValueError("Invalid security-master board assertion")
        return text

    @staticmethod
    def _profile_value(value: Any, target_type: type) -> Any:
        if target_type is Decimal:
            return Decimal(value)
        if target_type is date:
            return date.fromisoformat(value)
        if target_type is int:
            return int(value)
        return value

    @staticmethod
    def _distinct(values: list[Any]) -> list[Any]:
        return sorted(set(values), key=lambda value: json.dumps(value, sort_keys=True))

    @staticmethod
    def _row_value(row: dict[str, Any], aliases: tuple[str, ...]) -> Any:
        lowered = {str(key).lower(): value for key, value in row.items()}
        for alias in aliases:
            value = lowered.get(alias.lower())
            if value is not None and (not isinstance(value, str) or value.strip()):
                return value
        return None

    @classmethod
    def _positive_decimal(
        cls,
        value: Any,
        *,
        maximum: Decimal | None = None,
    ) -> Decimal | None:
        parsed = cls._decimal(value)
        if parsed is None or parsed <= 0:
            return None
        if maximum is not None and parsed > maximum:
            return None
        return parsed

    @staticmethod
    def _decimal(value: Any) -> Decimal | None:
        try:
            parsed = Decimal(str(value).strip().replace(",", "."))
        except (InvalidOperation, ValueError, TypeError):
            return None
        return parsed if parsed.is_finite() else None

    @staticmethod
    def _canonical_decimal(value: Decimal) -> str:
        text = format(value, "f")
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return text or "0"

    @staticmethod
    def _date(value: Any) -> date | None:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            parsed = date.fromisoformat(str(value).strip())
        except (TypeError, ValueError):
            return None
        if parsed.isoformat() in {"0001-01-01"}:
            return None
        return parsed

    @staticmethod
    def _boolean(value: Any) -> bool | None:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "да"}:
            return True
        if text in {"0", "false", "no", "n", "нет"}:
            return False
        return None

    @staticmethod
    def _aware_timestamp(value: datetime, field_name: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(f"{field_name} must be timezone-aware")
        return value.astimezone(timezone.utc)

    @staticmethod
    def _bounded_text(value: str | None, limit: int) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text or len(text) > limit:
            raise ValueError("Invalid security-master provenance text")
        return text

    @classmethod
    def _narrow_raw_value(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, dict) or len(value) > 4:
            raise ValueError("Security-master raw evidence must be narrow")
        normalized: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if len(key_text) > 64:
                raise ValueError("Security-master raw evidence key is too long")
            if isinstance(item, list):
                if len(item) > 8 or not all(isinstance(part, (str, int, bool)) for part in item):
                    raise ValueError("Security-master raw evidence list is invalid")
                normalized[key_text] = item
            elif item is None or isinstance(item, (str, int, bool)):
                normalized[key_text] = item
            else:
                normalized[key_text] = cls._json_scalar(item)
        return normalized

    @staticmethod
    def _json_scalar(value: Any) -> str | int | float | bool | None:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            if not isfinite(value):
                raise ValueError("Security-master raw evidence value must be scalar")
            return value
        if isinstance(value, (Decimal, date, datetime)):
            return str(value)
        raise ValueError("Security-master raw evidence value must be scalar")

    @staticmethod
    def _fingerprint(
        *,
        bond_id: int,
        field_name: str,
        source: str,
        source_key: str | None,
        source_table: str | None,
        assertion_type: str,
        normalized_value: dict[str, Any],
        effective_at: datetime | None,
    ) -> str:
        payload = {
            "assertion_type": assertion_type,
            "bond_id": bond_id,
            "contract_version": SECURITY_MASTER_CONTRACT_VERSION,
            "effective_at": effective_at.isoformat() if effective_at else None,
            "field_name": field_name,
            "normalized_value": normalized_value,
            "source": source,
            "source_key": source_key,
            "source_table": source_table,
        }
        canonical = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def research_terms_blockers(
    profile: BondSecurityMasterProfile | None, as_of_date: date
) -> list[str]:
    if profile is None:
        return ["SECURITY_MASTER_PROFILE_MISSING"]
    blockers: list[str] = []
    if profile.currency_state != "verified":
        blockers.append("CURRENCY_NOT_VERIFIED")
    elif profile.currency_code != "RUB":
        blockers.append("CURRENCY_NOT_RUB")
    if profile.nominal_state != "verified" or profile.nominal_value is None or profile.nominal_value <= 0:
        blockers.append("NOMINAL_NOT_VERIFIED")
    if profile.maturity_state != "verified" or profile.maturity_date is None:
        blockers.append("MATURITY_NOT_VERIFIED")
    elif profile.maturity_date <= as_of_date:
        blockers.append("MATURITY_NOT_FUTURE")
    for field_name, code, allowed in (
        ("coupon_structure", "COUPON_STRUCTURE_UNKNOWN_OR_CONFLICT", {"fixed", "floating"}),
        ("amortization_structure", "AMORTIZATION_STRUCTURE_UNKNOWN_OR_CONFLICT", {"bullet", "amortizing"}),
        ("subordination_structure", "SUBORDINATION_STRUCTURE_UNKNOWN_OR_CONFLICT", {"senior", "subordinated"}),
        ("perpetual_structure", "PERPETUAL_STRUCTURE_UNKNOWN_OR_CONFLICT", {"dated", "perpetual"}),
        ("offer_structure", "OFFER_STRUCTURE_UNKNOWN_OR_CONFLICT", {"none", "present"}),
    ):
        if getattr(profile, field_name) not in allowed:
            blockers.append(code)
    if _profile_has_conflict(profile):
        blockers.append("SECURITY_MASTER_CONFLICT")
    return blockers


def plain_vanilla_strategy_blockers(
    profile: BondSecurityMasterProfile | None, as_of_date: date
) -> list[str]:
    if profile is None:
        return ["SECURITY_MASTER_PROFILE_MISSING"]
    blockers: list[str] = []
    if profile.currency_state != "verified":
        blockers.append("CURRENCY_NOT_VERIFIED")
    elif profile.currency_code != "RUB":
        blockers.append("CURRENCY_NOT_RUB")
    if profile.nominal_state != "verified" or profile.nominal_value is None or profile.nominal_value <= 0:
        blockers.append("NOMINAL_NOT_VERIFIED")
    if profile.maturity_state != "verified" or profile.maturity_date is None:
        blockers.append("MATURITY_NOT_VERIFIED")
    elif profile.maturity_date <= as_of_date:
        blockers.append("MATURITY_NOT_FUTURE")
    for field_name, required, code in (
        ("coupon_structure", "fixed", "COUPON_STRUCTURE_NOT_FIXED"),
        ("amortization_structure", "bullet", "AMORTIZATION_STRUCTURE_NOT_BULLET"),
        ("subordination_structure", "senior", "SUBORDINATION_STRUCTURE_NOT_SENIOR"),
        ("perpetual_structure", "dated", "PERPETUAL_STRUCTURE_NOT_DATED"),
        ("offer_structure", "none", "OFFER_STRUCTURE_NOT_NONE"),
    ):
        if getattr(profile, field_name) != required:
            blockers.append(code)
    if _profile_has_conflict(profile):
        blockers.append("SECURITY_MASTER_CONFLICT")
    return blockers


def execution_terms_blockers(profile: BondSecurityMasterProfile | None) -> list[str]:
    if profile is None:
        return [
            "LOT_SIZE_NOT_VERIFIED",
            "TRADING_BOARD_NOT_VERIFIED",
            "COUPON_FREQUENCY_NOT_VERIFIED",
            "OUTSTANDING_NOMINAL_NOT_VERIFIED",
        ]
    blockers: list[str] = []
    if profile.lot_size_state != "verified" or profile.lot_size is None or profile.lot_size <= 0:
        blockers.append("LOT_SIZE_NOT_VERIFIED")
    if profile.trading_board_state != "verified" or not profile.trading_board:
        blockers.append("TRADING_BOARD_NOT_VERIFIED")
    if profile.coupon_frequency_state != "verified" or profile.coupon_frequency_per_year is None or profile.coupon_frequency_per_year <= 0:
        blockers.append("COUPON_FREQUENCY_NOT_VERIFIED")
    if profile.outstanding_nominal_state != "verified" or profile.outstanding_nominal is None or profile.outstanding_nominal <= 0:
        blockers.append("OUTSTANDING_NOMINAL_NOT_VERIFIED")
    return blockers


def _profile_has_conflict(profile: BondSecurityMasterProfile) -> bool:
    scalar_states = (
        profile.currency_state,
        profile.nominal_state,
        profile.coupon_rate_state,
        profile.maturity_state,
        profile.lot_size_state,
        profile.trading_board_state,
        profile.coupon_frequency_state,
        profile.coupon_formula_state,
        profile.outstanding_nominal_state,
    )
    classifications = (
        profile.coupon_structure,
        profile.amortization_structure,
        profile.subordination_structure,
        profile.perpetual_structure,
        profile.offer_structure,
        profile.listing_status,
    )
    return "conflict" in scalar_states or "conflict" in classifications
