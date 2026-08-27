from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.enums import AnalysisSignal
from app.schemas.moex_bond_universe import (
    MoexBondUniverseSyncError,
    MoexBondUniverseSyncRequest,
    MoexBondUniverseSyncResult,
    MoexBondUniverseSyncWarning,
)
from app.services.bond_security_master_service import BondSecurityMasterService
from app.services.issuer_identity_service import IssuerIdentityService
from app.services.moex_iss_client import MoexIssClient, MoexIssClientError
from app.services.moex_normalization import canonicalize_moex_currency


@dataclass
class SecurityOutcome:
    company_id: int | None
    company_action: str | None
    bond_action: str | None
    warnings: list[MoexBondUniverseSyncWarning]
    error: MoexBondUniverseSyncError | None = None


@dataclass(frozen=True)
class SecurityMasterObservation:
    source: str
    metadata: dict[str, Any]
    observed_at: datetime
    board_observed: bool


@dataclass(frozen=True)
class FetchedSecurity:
    metadata: dict[str, Any]
    observations: tuple[SecurityMasterObservation, ...]


class MoexBondUniverseService:
    def __init__(
        self,
        db: Session,
        *,
        moex_client: MoexIssClient | None = None,
    ) -> None:
        self.db = db
        self.moex_client = moex_client or MoexIssClient()

    def sync(self, request: MoexBondUniverseSyncRequest) -> MoexBondUniverseSyncResult:
        secids = self._validate_request(request)
        errors: list[MoexBondUniverseSyncError] = []
        warnings: list[MoexBondUniverseSyncWarning] = []
        company_actions: dict[int, str] = {}
        processed_securities = 0
        bonds_created = 0
        bonds_updated = 0
        bonds_skipped = 0

        securities = self._fetch_securities(request, secids, errors, warnings)
        for fetched in securities:
            metadata = fetched.metadata
            processed_securities += 1
            secid = self._text(metadata.get("secid"), upper=True)
            isin = self._valid_isin(metadata.get("isin"), warnings, secid)
            if request.active_only and self._is_inactive(metadata):
                warnings.append(
                    MoexBondUniverseSyncWarning(
                        secid=secid,
                        isin=isin,
                        message="MOEX security is inactive and was skipped",
                    )
                )
                bonds_skipped += 1
                continue

            try:
                outcome = self._process_security(
                    metadata,
                    request=request,
                    secid=secid,
                    isin=isin,
                    observations=fetched.observations,
                )
                warnings.extend(outcome.warnings)
                if outcome.error is not None:
                    errors.append(outcome.error)
                    bonds_skipped += 1
                    self.db.rollback()
                    continue
                if outcome.company_id is not None and outcome.company_action is not None:
                    self._record_company_action(
                        company_actions,
                        outcome.company_id,
                        outcome.company_action,
                    )
                if outcome.bond_action == "created":
                    bonds_created += 1
                elif outcome.bond_action == "updated":
                    bonds_updated += 1
                else:
                    bonds_skipped += 1
                self.db.commit()
            except Exception as exc:
                self.db.rollback()
                errors.append(
                    MoexBondUniverseSyncError(
                        secid=secid,
                        isin=isin,
                        message=self._error_message(exc),
                    )
                )
                bonds_skipped += 1

        return MoexBondUniverseSyncResult(
            requested_securities=len(secids) if secids is not None else None,
            processed_securities=processed_securities,
            companies_created=sum(
                action == "created" for action in company_actions.values()
            ),
            companies_updated=sum(
                action == "updated" for action in company_actions.values()
            ),
            companies_skipped=sum(
                action == "skipped" for action in company_actions.values()
            ),
            bonds_created=bonds_created,
            bonds_updated=bonds_updated,
            bonds_skipped=bonds_skipped,
            errors=errors,
            warnings=warnings,
        )

    def _fetch_securities(
        self,
        request: MoexBondUniverseSyncRequest,
        secids: list[str] | None,
        errors: list[MoexBondUniverseSyncError],
        warnings: list[MoexBondUniverseSyncWarning],
    ) -> list[FetchedSecurity]:
        if secids is not None:
            securities: list[FetchedSecurity] = []
            for secid in secids:
                try:
                    metadata, item_warnings = self.moex_client.fetch_bond_description(
                        secid,
                        board=request.board,
                    )
                    observed_at = datetime.now(timezone.utc)
                    metadata["secid"] = metadata.get("secid") or secid
                    warnings.extend(self._warnings(secid, metadata, item_warnings))
                    securities.append(
                        FetchedSecurity(
                            metadata=metadata,
                            observations=(
                                SecurityMasterObservation(
                                    source="moex_description",
                                    metadata=metadata,
                                    observed_at=observed_at,
                                    board_observed=bool(
                                        metadata.get("__moex_board_observed")
                                    ),
                                ),
                            ),
                        )
                    )
                except Exception as exc:
                    errors.append(
                        MoexBondUniverseSyncError(
                            secid=secid,
                            isin=None,
                            message=self._error_message(exc),
                        )
                    )
            return securities

        securities = []
        start = 0
        for _ in range(request.max_pages):
            try:
                rows, page_warnings = self.moex_client.fetch_bond_universe(
                    request.board,
                    start=start,
                    limit=request.page_size,
                )
                page_observed_at = datetime.now(timezone.utc)
            except Exception as exc:
                errors.append(
                    MoexBondUniverseSyncError(
                        secid=None,
                        isin=None,
                        message=self._error_message(exc),
                    )
                )
                break
            warnings.extend(
                MoexBondUniverseSyncWarning(secid=None, isin=None, message=message)
                for message in page_warnings
            )
            if not rows:
                break

            for row in rows:
                observations = [
                    SecurityMasterObservation(
                        source="moex_universe",
                        metadata=row,
                        observed_at=page_observed_at,
                        board_observed=bool(
                            row.get("__moex_board_observed", True)
                        ),
                    )
                ]
                secid = self._text(row.get("secid"), upper=True)
                if not secid:
                    securities.append(
                        FetchedSecurity(
                            metadata=row,
                            observations=tuple(observations),
                        )
                    )
                    continue
                try:
                    description, item_warnings = (
                        self.moex_client.fetch_bond_description(
                            secid,
                            board=request.board,
                        )
                    )
                    description_observed_at = datetime.now(timezone.utc)
                    warnings.extend(self._warnings(secid, row, item_warnings))
                    observations.append(
                        SecurityMasterObservation(
                            source="moex_description",
                            metadata=description,
                            observed_at=description_observed_at,
                            board_observed=bool(
                                description.get("__moex_board_observed")
                            ),
                        )
                    )
                    securities.append(
                        FetchedSecurity(
                            metadata=self._merge_metadata(row, description),
                            observations=tuple(observations),
                        )
                    )
                except Exception as exc:
                    warnings.append(
                        MoexBondUniverseSyncWarning(
                            secid=secid,
                            isin=self._text(row.get("isin"), upper=True),
                            message=(
                                "MOEX description fetch failed; universe row was used"
                            ),
                        )
                    )
                    warnings.append(
                        MoexBondUniverseSyncWarning(
                            secid=secid,
                            isin=self._text(row.get("isin"), upper=True),
                            message=self._error_message(exc),
                        )
                    )
                    securities.append(
                        FetchedSecurity(
                            metadata=row,
                            observations=tuple(observations),
                        )
                    )
            start += len(rows)
        else:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=None,
                    isin=None,
                    message="MOEX bond universe pagination max_pages reached",
                )
            )
        return securities

    def _process_security(
        self,
        metadata: dict[str, Any],
        *,
        request: MoexBondUniverseSyncRequest,
        secid: str | None,
        isin: str | None,
        observations: tuple[SecurityMasterObservation, ...],
    ) -> SecurityOutcome:
        warnings: list[MoexBondUniverseSyncWarning] = []
        if not secid:
            return SecurityOutcome(
                company_id=None,
                company_action=None,
                bond_action=None,
                warnings=warnings,
                error=MoexBondUniverseSyncError(
                    secid=None,
                    isin=isin,
                    message="Bond secid is missing",
                ),
            )
        if not isin:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=None,
                    message="Bond isin is missing",
                )
            )

        bond, conflict_error = self._find_existing_bond(secid=secid, isin=isin)
        if conflict_error is not None:
            return SecurityOutcome(
                company_id=None,
                company_action=None,
                bond_action=None,
                warnings=warnings,
                error=conflict_error,
            )

        nominal_currency = canonicalize_moex_currency(metadata.get("currency"))
        if nominal_currency is None:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=isin,
                    message="bond_currency_unresolved",
                )
            )
            if bond is None:
                return SecurityOutcome(
                    company_id=None,
                    company_action=None,
                    bond_action=None,
                    warnings=warnings,
                    error=MoexBondUniverseSyncError(
                        secid=secid,
                        isin=isin,
                        message="bond_currency_unresolved",
                    ),
                )

        company, company_action, company_error = self._resolve_company(
            metadata,
            request=request,
            secid=secid,
            warnings=warnings,
        )
        if company_error is not None:
            return SecurityOutcome(
                company_id=None,
                company_action=None,
                bond_action=None,
                warnings=warnings,
                error=company_error,
            )

        bond_values = self._bond_values(
            metadata,
            company_id=company.id,
            secid=secid,
            isin=isin,
            nominal_currency=nominal_currency,
            warnings=warnings,
        )

        if bond is None:
            bond = Bond(**bond_values)
            self.db.add(bond)
            self.db.flush()
            self._ingest_security_master(
                bond,
                observations=observations,
                board=request.board,
            )
            return SecurityOutcome(
                company_id=company.id,
                company_action=company_action,
                bond_action="created",
                warnings=warnings,
            )

        self._ingest_security_master(
            bond,
            observations=observations,
            board=request.board,
        )

        if not request.rebuild_existing:
            return SecurityOutcome(
                company_id=company.id,
                company_action=company_action,
                bond_action="skipped",
                warnings=warnings,
            )

        changed = self._update_bond(bond, bond_values)
        if changed:
            self.db.add(bond)
            self.db.flush()
        return SecurityOutcome(
            company_id=company.id,
            company_action=company_action,
            bond_action="updated" if changed else "skipped",
            warnings=warnings,
        )

    def _ingest_security_master(
        self,
        bond: Bond,
        *,
        observations: tuple[SecurityMasterObservation, ...],
        board: str,
    ) -> None:
        service = BondSecurityMasterService(self.db)
        for observation in observations:
            service.ingest_moex_metadata(
                bond,
                observation.metadata,
                source=observation.source,
                board=board,
                board_observed=observation.board_observed,
                observed_at=observation.observed_at,
            )

    def _resolve_company(
        self,
        metadata: dict[str, Any],
        *,
        request: MoexBondUniverseSyncRequest,
        secid: str,
        warnings: list[MoexBondUniverseSyncWarning],
    ) -> tuple[Company | None, str | None, MoexBondUniverseSyncError | None]:
        issuer_inn = self._issuer_inn(metadata.get("issuer_inn"), warnings, secid)
        issuer_name = self._text(metadata.get("issuer_name"), max_length=255)
        if not issuer_name:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="Issuer metadata is missing",
                )
            )
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="issuer_name_missing",
                )
            )
        if not issuer_inn:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="issuer_inn_missing",
                )
            )

        company = self._company_by_inn(issuer_inn) if issuer_inn else None
        if company is None and issuer_name:
            company = self._company_by_name(issuer_name)
        if company is not None:
            changed = False
            if request.rebuild_existing and issuer_inn and not company.inn:
                company.inn = issuer_inn
                changed = True
            if changed:
                self.db.add(company)
                self.db.flush()
                self._upsert_identity_from_moex(
                    company,
                    metadata=metadata,
                    secid=secid,
                    issuer_name=issuer_name,
                    issuer_inn=issuer_inn,
                    warnings=warnings,
                )
                return company, "updated", None
            self._upsert_identity_from_moex(
                company,
                metadata=metadata,
                secid=secid,
                issuer_name=issuer_name,
                issuer_inn=issuer_inn,
                warnings=warnings,
            )
            return company, "skipped", None

        if not request.create_missing_companies:
            return (
                None,
                None,
                MoexBondUniverseSyncError(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="Company could not be resolved",
                ),
            )

        company = Company(
            name=issuer_name or f"Unknown issuer for {secid}",
            ticker=self._unique_ticker(issuer_inn=issuer_inn, secid=secid),
            inn=issuer_inn,
            country="RU",
            signal=AnalysisSignal.INSUFFICIENT_DATA.value,
            notes="Created by MOEX bond universe sync",
        )
        self.db.add(company)
        self.db.flush()
        self._upsert_identity_from_moex(
            company,
            metadata=metadata,
            secid=secid,
            issuer_name=issuer_name,
            issuer_inn=issuer_inn,
            warnings=warnings,
        )
        return company, "created", None

    def _upsert_identity_from_moex(
        self,
        company: Company,
        *,
        metadata: dict[str, Any],
        secid: str,
        issuer_name: str | None,
        issuer_inn: str | None,
        warnings: list[MoexBondUniverseSyncWarning],
    ) -> None:
        action = IssuerIdentityService(self.db).upsert_from_moex(
            company,
            metadata=metadata,
            secid=secid,
            issuer_name=issuer_name,
            issuer_inn=issuer_inn,
        )
        if action == "created" and (not issuer_name or not issuer_inn):
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="company_identity_created_weak",
                )
            )
        elif action == "updated":
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=self._text(metadata.get("isin"), upper=True),
                    message="company_identity_enriched",
                )
            )

    def _bond_values(
        self,
        metadata: dict[str, Any],
        *,
        company_id: int,
        secid: str,
        isin: str | None,
        nominal_currency: str | None,
        warnings: list[MoexBondUniverseSyncWarning],
    ) -> dict[str, Any]:
        name = (
            self._text(metadata.get("name"), max_length=255)
            or self._text(metadata.get("shortname"), max_length=255)
            or secid
        )
        is_subordinated = self._bool_value(
            metadata.get("is_subordinated"),
            warnings=warnings,
            secid=secid,
            isin=isin,
            field_name="is_subordinated",
        )
        is_perpetual = self._bool_value(
            metadata.get("is_perpetual"),
            warnings=warnings,
            secid=secid,
            isin=isin,
            field_name="is_perpetual",
        )
        has_amortization = self._bool_value(
            metadata.get("has_amortization"),
            warnings=warnings,
            secid=secid,
            isin=isin,
            field_name="has_amortization",
        )
        return {
            "company_id": company_id,
            "secid": secid,
            "isin": isin,
            "name": name,
            "currency": nominal_currency,
            "nominal_value": self._decimal_value(
                metadata.get("nominal_value"),
                warnings=warnings,
                secid=secid,
                isin=isin,
                field_name="nominal_value",
            ),
            "coupon_rate": self._decimal_value(
                metadata.get("coupon_rate"),
                warnings=warnings,
                secid=secid,
                isin=isin,
                field_name="coupon_rate",
            ),
            "maturity_date": self._date_value(
                metadata.get("maturity_date"),
                warnings=warnings,
                secid=secid,
                isin=isin,
                field_name="maturity_date",
            ),
            "offer_date": self._date_value(
                metadata.get("offer_date"),
                warnings=warnings,
                secid=secid,
                isin=isin,
                field_name="offer_date",
            ),
            "is_subordinated": bool(is_subordinated),
            "is_perpetual": bool(is_perpetual),
            "is_floating_coupon": False,
            "amortization": has_amortization,
            "signal": AnalysisSignal.INSUFFICIENT_DATA.value,
        }

    def _find_existing_bond(
        self,
        *,
        secid: str,
        isin: str | None,
    ) -> tuple[Bond | None, MoexBondUniverseSyncError | None]:
        by_secid = self.db.execute(
            select(Bond).where(Bond.secid == secid)
        ).scalar_one_or_none()
        by_isin = None
        if isin:
            by_isin = self.db.execute(
                select(Bond).where(Bond.isin == isin)
            ).scalar_one_or_none()
        if by_secid is not None and by_isin is not None and by_secid.id != by_isin.id:
            return (
                None,
                MoexBondUniverseSyncError(
                    secid=secid,
                    isin=isin,
                    message="Bond secid and isin match different existing bonds",
                ),
            )
        return by_secid or by_isin, None

    @staticmethod
    def _update_bond(bond: Bond, values: dict[str, Any]) -> bool:
        changed = False
        fields = (
            "company_id",
            "secid",
            "isin",
            "name",
            "currency",
            "nominal_value",
            "coupon_rate",
            "maturity_date",
            "offer_date",
            "is_subordinated",
            "is_perpetual",
            "amortization",
        )
        for field in fields:
            value = values.get(field)
            if value is None and field not in {"isin", "amortization"}:
                continue
            if getattr(bond, field) != value:
                setattr(bond, field, value)
                changed = True
        return changed

    def _company_by_inn(self, inn: str | None) -> Company | None:
        if not inn:
            return None
        return self.db.execute(select(Company).where(Company.inn == inn)).scalar_one_or_none()

    def _company_by_name(self, name: str) -> Company | None:
        normalized_name = self._normalize_name(name)
        companies = list(self.db.execute(select(Company)).scalars())
        for company in companies:
            if self._normalize_name(company.name) == normalized_name:
                return company
        return None

    def _unique_ticker(self, *, issuer_inn: str | None, secid: str) -> str:
        base = f"MOEX_{issuer_inn or secid}"
        base = re.sub(r"[^A-Z0-9_]", "_", base.upper())
        base = base[:32] or "MOEX_BOND"
        ticker = base
        suffix = 2
        while self.db.execute(select(Company.id).where(Company.ticker == ticker)).first():
            suffix_text = f"_{suffix}"
            ticker = f"{base[: 32 - len(suffix_text)]}{suffix_text}"
            suffix += 1
        return ticker

    @staticmethod
    def _merge_metadata(
        base: dict[str, Any],
        description: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in description.items():
            if key == "raw":
                continue
            if MoexBondUniverseService._has_value(value):
                merged[key] = value
        return merged

    @staticmethod
    def _record_company_action(
        company_actions: dict[int, str],
        company_id: int,
        action: str,
    ) -> None:
        priority = {"created": 3, "updated": 2, "skipped": 1}
        current = company_actions.get(company_id)
        if current is None or priority[action] > priority[current]:
            company_actions[company_id] = action

    @staticmethod
    def _validate_request(request: MoexBondUniverseSyncRequest) -> list[str] | None:
        if request.max_pages < 1 or request.max_pages > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_pages must be between 1 and 500",
            )
        if request.page_size < 1 or request.page_size > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="page_size must be between 1 and 500",
            )
        if request.secids is not None:
            normalized: list[str] = []
            for secid in request.secids:
                clean = str(secid).strip().upper()
                if not clean:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="secids cannot contain empty values",
                    )
                if clean not in normalized:
                    normalized.append(clean)
            return normalized
        return None

    @staticmethod
    def _warnings(
        secid: str | None,
        metadata: dict[str, Any],
        messages: list[str],
    ) -> list[MoexBondUniverseSyncWarning]:
        isin = MoexBondUniverseService._text(metadata.get("isin"), upper=True)
        return [
            MoexBondUniverseSyncWarning(secid=secid, isin=isin, message=message)
            for message in messages
        ]

    @staticmethod
    def _is_inactive(metadata: dict[str, Any]) -> bool:
        traded = MoexBondUniverseService._bool_value(metadata.get("is_traded"))
        if traded is False:
            return True
        status_value = MoexBondUniverseService._text(metadata.get("status"))
        if not status_value:
            return False
        return status_value.lower() in {
            "inactive",
            "archived",
            "not_traded",
            "not traded",
            "delisted",
            "погашен",
        }

    @staticmethod
    def _valid_isin(
        value: Any,
        warnings: list[MoexBondUniverseSyncWarning],
        secid: str | None,
    ) -> str | None:
        isin = MoexBondUniverseService._text(value, upper=True)
        if not isin:
            return None
        if len(isin) != 12:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=isin,
                    message="Bond isin is invalid and was ignored",
                )
            )
            return None
        return isin

    @staticmethod
    def _issuer_inn(
        value: Any,
        warnings: list[MoexBondUniverseSyncWarning],
        secid: str,
    ) -> str | None:
        inn = MoexBondUniverseService._text(value)
        if not inn:
            return None
        if len(inn) > 16:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=None,
                    message="Issuer inn is too long and was ignored",
                )
            )
            return None
        return inn

    @staticmethod
    def _decimal_value(
        value: Any,
        *,
        warnings: list[MoexBondUniverseSyncWarning],
        secid: str,
        isin: str | None,
        field_name: str,
    ) -> Decimal | None:
        if not MoexBondUniverseService._has_value(value):
            return None
        try:
            return Decimal(str(value).replace(",", "."))
        except (InvalidOperation, ValueError):
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=isin,
                    message=f"Bond {field_name} is invalid and was ignored",
                )
            )
            return None

    @staticmethod
    def _date_value(
        value: Any,
        *,
        warnings: list[MoexBondUniverseSyncWarning],
        secid: str,
        isin: str | None,
        field_name: str,
    ) -> date | None:
        if not MoexBondUniverseService._has_value(value):
            return None
        if isinstance(value, date):
            return value
        text = str(value).strip()
        if text in {"0000-00-00", "0001-01-01"}:
            return None
        try:
            return date.fromisoformat(text)
        except ValueError:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=isin,
                    message=f"Bond {field_name} is invalid and was ignored",
                )
            )
            return None

    @staticmethod
    def _bool_value(
        value: Any,
        *,
        warnings: list[MoexBondUniverseSyncWarning] | None = None,
        secid: str | None = None,
        isin: str | None = None,
        field_name: str | None = None,
    ) -> bool | None:
        if not MoexBondUniverseService._has_value(value):
            return None
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "да"}:
            return True
        if text in {"0", "false", "no", "n", "нет"}:
            return False
        if warnings is not None and field_name is not None:
            warnings.append(
                MoexBondUniverseSyncWarning(
                    secid=secid,
                    isin=isin,
                    message=f"Bond {field_name} is invalid and was ignored",
                )
            )
        return None

    @staticmethod
    def _text(
        value: Any,
        *,
        max_length: int | None = None,
        upper: bool = False,
    ) -> str | None:
        if not MoexBondUniverseService._has_value(value):
            return None
        text = str(value).strip()
        if not text:
            return None
        if upper:
            text = text.upper()
        if max_length is not None:
            text = text[:max_length]
        return text

    @staticmethod
    def _normalize_name(value: str) -> str:
        return " ".join(value.lower().split())

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != ""

    @staticmethod
    def _error_message(exc: Exception) -> str:
        if isinstance(exc, MoexIssClientError):
            return str(exc)
        return str(exc)
