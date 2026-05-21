from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.models.financial_report import FinancialReport
from app.schemas.company_identity import (
    CompanyIdentityApplyRequest,
    CompanyIdentityApplyResult,
    CompanyIdentityApplyRow,
    CompanyIdentityAffectedRowsSummary,
    CompanyIdentityDiagnosticsResult,
    CompanyIdentityDiagnosticsWarning,
    CompanyIdentityInputRow,
    CompanyIdentityPreviewRequest,
    CompanyIdentityPreviewResult,
    CompanyIdentityPreviewRow,
    CompanyIdentityProfileRead,
    CompanyIdentityRowMessage,
    CompanyIdentityTopUnknownIssuer,
)


UNKNOWN_NAME_PREFIX = "Unknown issuer for "
WEAK_IDENTITY_STATUSES = {"unknown", "weak", "conflict"}
VERIFIED_STATUSES = {"verified"}


class IssuerIdentityService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_profile(self, company_id: int) -> CompanyIdentityProfileRead:
        profile = self._profile_by_company_id(company_id)
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Company identity profile not found.",
            )
        return CompanyIdentityProfileRead.model_validate(profile)

    def diagnostics(
        self,
        *,
        active_only: bool = True,
        limit: int = 20,
        include_samples: bool = True,
    ) -> CompanyIdentityDiagnosticsResult:
        companies = self._scoped_companies(active_only=active_only)
        profiles = {
            profile.company_id: profile
            for profile in self.db.execute(select(CompanyIdentityProfile)).scalars()
        }
        report_company_ids = set(
            self.db.execute(select(FinancialReport.company_id).distinct()).scalars()
        )

        company_count = len(companies)
        unknown_company_count = 0
        missing_inn_count = 0
        weak_identity_count = 0
        verified_identity_count = 0
        moex_ticker_count = 0
        reports_with_weak_identity = 0
        weak_rows: list[Company] = []

        for company in companies:
            profile = profiles.get(company.id)
            identity_status = self._identity_status(company, profile)
            if self._is_unknown_company_name(company.name):
                unknown_company_count += 1
            if not self._clean(company.inn):
                missing_inn_count += 1
            if str(company.ticker or "").upper().startswith("MOEX_"):
                moex_ticker_count += 1
            if identity_status in WEAK_IDENTITY_STATUSES or profile is None:
                weak_identity_count += 1
                weak_rows.append(company)
                if company.id in report_company_ids:
                    reports_with_weak_identity += 1
            if identity_status in VERIFIED_STATUSES:
                verified_identity_count += 1

        top_unknowns = (
            self._top_unknown_issuers(weak_rows, profiles, limit=limit)
            if include_samples
            else []
        )
        warnings: list[CompanyIdentityDiagnosticsWarning] = []
        if company_count and weak_identity_count / company_count >= 0.5:
            warnings.append(
                CompanyIdentityDiagnosticsWarning(
                    code="issuer_identity_coverage_low",
                    message="Most companies have weak issuer identity.",
                )
            )
        if unknown_company_count:
            warnings.append(
                CompanyIdentityDiagnosticsWarning(
                    code="issuer_identity_unknown_names",
                    message="Some companies still use generated unknown issuer names.",
                )
            )
        return CompanyIdentityDiagnosticsResult(
            status="warning" if warnings else "passed",
            company_count=company_count,
            unknown_company_count=unknown_company_count,
            missing_inn_count=missing_inn_count,
            weak_identity_count=weak_identity_count,
            verified_identity_count=verified_identity_count,
            companies_with_unknown_name=unknown_company_count,
            companies_with_moex_generated_ticker=moex_ticker_count,
            companies_with_financial_reports_and_weak_identity=reports_with_weak_identity,
            top_unknown_issuers=top_unknowns,
            warnings=warnings,
        )

    def preview(
        self,
        request: CompanyIdentityPreviewRequest,
    ) -> CompanyIdentityPreviewResult:
        rows: list[CompanyIdentityPreviewRow] = []
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        create_count = update_profile_count = update_company_count = 0

        for row_index, row in enumerate(request.rows, start=1):
            preview_row = self._preview_row(
                row,
                row_index=row_index,
                rebuild_existing=request.rebuild_existing,
            )
            rows.append(preview_row)
            errors.extend(preview_row.errors)
            warnings.extend(preview_row.warnings)
            warnings.extend(preview_row.conflicts)
            if preview_row.would_create_identity_profile:
                create_count += 1
            if preview_row.would_update_identity_profile:
                update_profile_count += 1
            if preview_row.would_update_company:
                update_company_count += 1

        return CompanyIdentityPreviewResult(
            status="failed" if errors else "warning" if warnings else "passed",
            total_rows=len(request.rows),
            valid_rows=len(request.rows) - len({item.row_index for item in errors}),
            invalid_rows=len({item.row_index for item in errors}),
            would_create_identity_profiles=create_count,
            would_update_identity_profiles=update_profile_count,
            would_update_companies=update_company_count,
            rows=rows,
            errors=errors,
            warnings=warnings,
        )

    def apply(self, request: CompanyIdentityApplyRequest) -> CompanyIdentityApplyResult:
        if not request.confirm_apply:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Identity apply requires confirm_apply=true.",
            )

        preview = self.preview(
            CompanyIdentityPreviewRequest(
                rows=request.rows,
                rebuild_existing=request.rebuild_existing,
            )
        )
        rows: list[CompanyIdentityApplyRow] = []
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        created = updated = company_updates = skipped = failed = 0
        affected_company_ids: set[int] = set()
        conflict_count = 0

        for preview_row, input_row in zip(preview.rows, request.rows, strict=False):
            if preview_row.errors:
                failed += 1
                conflict_count += len(preview_row.conflicts)
                errors.extend(preview_row.errors)
                rows.append(
                    CompanyIdentityApplyRow(
                        row_index=preview_row.row_index,
                        company_id=preview_row.company_id,
                        action="failed",
                        errors=preview_row.errors,
                        warnings=preview_row.warnings,
                        conflicts=preview_row.conflicts,
                    )
                )
                continue
            if preview_row.conflicts and not request.allow_conflicts:
                failed += 1
                conflict_count += len(preview_row.conflicts)
                errors.extend(preview_row.conflicts)
                rows.append(
                    CompanyIdentityApplyRow(
                        row_index=preview_row.row_index,
                        company_id=preview_row.company_id,
                        action="blocked_conflict",
                        errors=preview_row.conflicts,
                        warnings=preview_row.warnings,
                        conflicts=preview_row.conflicts,
                    )
                )
                continue
            if (
                not preview_row.would_create_identity_profile
                and not preview_row.would_update_identity_profile
                and not preview_row.would_update_company
            ):
                skipped += 1
                rows.append(
                    CompanyIdentityApplyRow(
                        row_index=preview_row.row_index,
                        company_id=preview_row.company_id,
                        action="skipped",
                        warnings=preview_row.warnings,
                        conflicts=preview_row.conflicts,
                    )
                )
                continue

            profile_action = self._apply_row(
                input_row,
                preview_row=preview_row,
                allow_conflicts=request.allow_conflicts,
            )
            if profile_action == "created":
                created += 1
            elif profile_action == "updated":
                updated += 1
            if preview_row.would_update_company:
                company_updates += 1
            affected_company_ids.add(preview_row.company_id)
            conflict_count += len(preview_row.conflicts)
            warnings.extend(preview_row.warnings)
            warnings.extend(preview_row.conflicts)
            rows.append(
                CompanyIdentityApplyRow(
                    row_index=preview_row.row_index,
                    company_id=preview_row.company_id,
                    action=profile_action,
                    company_updated=preview_row.would_update_company,
                    warnings=preview_row.warnings,
                    conflicts=preview_row.conflicts,
                )
            )

        self.db.commit()
        return CompanyIdentityApplyResult(
            status="failed" if errors else "warning" if warnings else "completed",
            total_rows=len(request.rows),
            created=created,
            updated=updated,
            company_updates=company_updates,
            skipped=skipped,
            failed=failed,
            affected_rows_summary=CompanyIdentityAffectedRowsSummary(
                affected_company_ids=sorted(affected_company_ids),
                created_profile_count=created,
                updated_profile_count=updated,
                updated_company_count=company_updates,
                skipped_count=skipped,
                conflict_count=conflict_count,
                warning_count=len(warnings),
            ),
            rows=rows,
            errors=errors,
            warnings=warnings,
        )

    def upsert_from_moex(
        self,
        company: Company,
        *,
        metadata: dict[str, Any],
        secid: str | None = None,
        issuer_name: str | None = None,
        issuer_inn: str | None = None,
    ) -> str:
        profile = self._profile_by_company_id(company.id)
        if profile is not None and self._is_protected_profile(profile):
            return "skipped_verified"

        clean_name = self._clean(issuer_name)
        clean_inn = self._clean(issuer_inn)
        if clean_name and clean_inn:
            identity_status = "matched"
            confidence = Decimal("0.8000")
        elif clean_name or clean_inn:
            identity_status = "weak"
            confidence = Decimal("0.4000")
        else:
            identity_status = "unknown"
            confidence = Decimal("0.1000")

        payload = {"secid": secid, "metadata": dict(metadata)}
        values = {
            "legal_name": clean_name,
            "short_name": clean_name,
            "display_name": clean_name,
            "inn": clean_inn,
            "country": "RU",
            "issuer_role": "legal_issuer" if clean_name or clean_inn else "unknown",
            "identity_status": identity_status,
            "identity_confidence": confidence,
            "identity_source": "moex_iss",
            "source_payload": payload,
            "review_status": "pending",
            "review_notes": "Created or refreshed from MOEX ISS metadata.",
        }
        if profile is None:
            profile = CompanyIdentityProfile(company_id=company.id, **values)
            self.db.add(profile)
            self.db.flush()
            return "created"

        for field, value in values.items():
            if value is not None or field in {
                "identity_status",
                "identity_confidence",
                "identity_source",
                "source_payload",
                "review_status",
                "review_notes",
            }:
                setattr(profile, field, value)
        self.db.add(profile)
        self.db.flush()
        return "updated"

    def _preview_row(
        self,
        row: CompanyIdentityInputRow,
        *,
        row_index: int,
        rebuild_existing: bool,
    ) -> CompanyIdentityPreviewRow:
        company = self.db.get(Company, row.company_id)
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        conflicts: list[CompanyIdentityRowMessage] = []
        if company is None:
            errors.append(
                self._message(
                    row_index,
                    row.company_id,
                    "company_not_found",
                    "Company could not be resolved.",
                )
            )
            return CompanyIdentityPreviewRow(
                row_index=row_index,
                company_id=row.company_id,
                errors=errors,
                proposed_identity_fields=self._proposed_fields(row),
            )

        profile = self._profile_by_company_id(row.company_id)
        proposed = self._proposed_fields(row)
        if not self._clean(row.legal_name) and not self._clean(row.inn):
            warnings.append(
                self._message(
                    row_index,
                    row.company_id,
                    "identity_evidence_weak",
                    "Legal name and INN are both missing.",
                )
            )
        if row.current_company_name and self._clean(row.current_company_name) != self._clean(company.name):
            warnings.append(
                self._message(
                    row_index,
                    row.company_id,
                    "current_company_name_differs",
                    "Input current_company_name differs from stored company name.",
                )
            )

        conflicts.extend(self._identity_conflicts(row, profile=profile))
        existing_profile = self._profile_dict(profile) if profile is not None else None
        would_create = profile is None
        protected = profile is not None and self._is_protected_profile(profile)
        would_update_profile = profile is not None and (not protected or rebuild_existing)
        if protected and not rebuild_existing:
            warnings.append(
                self._message(
                    row_index,
                    row.company_id,
                    "verified_identity_protected",
                    "Existing verified identity profile is protected from default overwrite.",
                )
            )
        would_update_company = self._would_update_company(company, row, conflicts=conflicts)
        return CompanyIdentityPreviewRow(
            row_index=row_index,
            company_id=row.company_id,
            matched_company_name=company.name,
            current_company_fields={
                "id": company.id,
                "name": company.name,
                "ticker": company.ticker,
                "inn": company.inn,
                "country": company.country,
            },
            existing_identity_profile=existing_profile,
            proposed_identity_fields=proposed,
            conflicts=conflicts,
            warnings=warnings,
            errors=errors,
            would_create_identity_profile=would_create and not errors,
            would_update_identity_profile=would_update_profile and not errors,
            would_update_company=would_update_company and not errors,
        )

    def _apply_row(
        self,
        row: CompanyIdentityInputRow,
        *,
        preview_row: CompanyIdentityPreviewRow,
        allow_conflicts: bool,
    ) -> str:
        company = self.db.get(Company, row.company_id)
        if company is None:
            return "failed"
        profile = self._profile_by_company_id(row.company_id)
        values = self._model_values(row)
        if allow_conflicts and preview_row.conflicts:
            values["identity_status"] = "conflict"
            existing_notes = self._clean(values.get("review_notes"))
            conflict_text = "; ".join(item.message for item in preview_row.conflicts)
            values["review_notes"] = (
                f"{existing_notes}; Conflicts allowed: {conflict_text}"
                if existing_notes
                else f"Conflicts allowed: {conflict_text}"
            )
        if profile is None:
            profile = CompanyIdentityProfile(company_id=row.company_id, **values)
            action = "created"
        else:
            for field, value in values.items():
                setattr(profile, field, value)
            action = "updated"
        self.db.add(profile)
        if preview_row.would_update_company:
            self._update_company_identity_fields(company, row)
            self.db.add(company)
        self.db.flush()
        return action

    def _identity_conflicts(
        self,
        row: CompanyIdentityInputRow,
        *,
        profile: CompanyIdentityProfile | None,
    ) -> list[CompanyIdentityRowMessage]:
        conflicts: list[CompanyIdentityRowMessage] = []
        clean_inn = self._clean(row.inn)
        clean_ogrn = self._clean(row.ogrn)
        if clean_inn:
            company_conflict = self.db.execute(
                select(Company).where(Company.inn == clean_inn, Company.id != row.company_id)
            ).scalar_one_or_none()
            profile_conflict = self.db.execute(
                select(CompanyIdentityProfile).where(
                    CompanyIdentityProfile.inn == clean_inn,
                    CompanyIdentityProfile.company_id != row.company_id,
                )
            ).scalar_one_or_none()
            if company_conflict is not None or profile_conflict is not None:
                conflicts.append(
                    self._message(
                        None,
                        row.company_id,
                        "inn_conflict",
                        "Incoming INN is already linked to another company.",
                    )
                )
        if clean_ogrn:
            profile_conflict = self.db.execute(
                select(CompanyIdentityProfile).where(
                    CompanyIdentityProfile.ogrn == clean_ogrn,
                    CompanyIdentityProfile.company_id != row.company_id,
                )
            ).scalar_one_or_none()
            if profile_conflict is not None:
                conflicts.append(
                    self._message(
                        None,
                        row.company_id,
                        "ogrn_conflict",
                        "Incoming OGRN is already linked to another company.",
                    )
                )
        if profile is not None and self._is_protected_profile(profile):
            if clean_inn and profile.inn and clean_inn != self._clean(profile.inn):
                conflicts.append(
                    self._message(
                        None,
                        row.company_id,
                        "verified_inn_conflict",
                        "Incoming INN differs from verified identity profile.",
                    )
                )
            if clean_ogrn and profile.ogrn and clean_ogrn != self._clean(profile.ogrn):
                conflicts.append(
                    self._message(
                        None,
                        row.company_id,
                        "verified_ogrn_conflict",
                        "Incoming OGRN differs from verified identity profile.",
                    )
                )
            if (
                self._clean(row.legal_name)
                and profile.legal_name
                and self._normalize_name(row.legal_name) != self._normalize_name(profile.legal_name)
            ):
                conflicts.append(
                    self._message(
                        None,
                        row.company_id,
                        "verified_legal_name_conflict",
                        "Incoming legal name differs from verified identity profile.",
                    )
                )
        return conflicts

    def _would_update_company(
        self,
        company: Company,
        row: CompanyIdentityInputRow,
        *,
        conflicts: list[CompanyIdentityRowMessage],
    ) -> bool:
        if conflicts:
            return False
        if not self._clean(company.inn) and self._clean(row.inn):
            return True
        if self._is_unknown_company_name(company.name) and self._preferred_company_name(row):
            return True
        return False

    def _update_company_identity_fields(
        self,
        company: Company,
        row: CompanyIdentityInputRow,
    ) -> None:
        if not self._clean(company.inn) and self._clean(row.inn):
            company.inn = self._clean(row.inn)
        new_name = self._preferred_company_name(row)
        if self._is_unknown_company_name(company.name) and new_name:
            company.name = new_name[:255]

    def _top_unknown_issuers(
        self,
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        *,
        limit: int,
    ) -> list[CompanyIdentityTopUnknownIssuer]:
        scored: list[tuple[int, Company]] = []
        for company in companies:
            bonds_count = int(
                self.db.execute(
                    select(func.count()).select_from(Bond).where(Bond.company_id == company.id)
                ).scalar_one()
            )
            if self._is_unknown_company_name(company.name) or bonds_count:
                scored.append((bonds_count, company))
        scored.sort(key=lambda item: (item[0], item[1].id), reverse=True)
        rows: list[CompanyIdentityTopUnknownIssuer] = []
        for bonds_count, company in scored[: max(0, limit)]:
            bonds = list(
                self.db.execute(
                    select(Bond).where(Bond.company_id == company.id).limit(5)
                ).scalars()
            )
            profile = profiles.get(company.id)
            rows.append(
                CompanyIdentityTopUnknownIssuer(
                    company_id=company.id,
                    company_name=company.name,
                    ticker=company.ticker,
                    inn=company.inn,
                    bonds_count=bonds_count,
                    sample_secids=[bond.secid for bond in bonds if bond.secid],
                    sample_bond_names=[bond.name for bond in bonds if bond.name],
                    identity_status=self._identity_status(company, profile),
                    identity_confidence=None if profile is None else profile.identity_confidence,
                )
            )
        return rows

    def _scoped_companies(self, *, active_only: bool) -> list[Company]:
        if not active_only:
            return list(self.db.execute(select(Company)).scalars())
        stmt = select(Company).join(Bond).distinct()
        companies = []
        for company in self.db.execute(stmt).scalars():
            if not self._is_obvious_government_company(company):
                companies.append(company)
        return companies

    def _is_obvious_government_company(self, company: Company) -> bool:
        text = f"{company.name or ''} {company.ticker or ''}".upper()
        return "OFZ" in text or "ОФЗ" in text

    def _profile_by_company_id(self, company_id: int) -> CompanyIdentityProfile | None:
        return self.db.execute(
            select(CompanyIdentityProfile).where(
                CompanyIdentityProfile.company_id == company_id
            )
        ).scalar_one_or_none()

    def _model_values(self, row: CompanyIdentityInputRow) -> dict[str, Any]:
        values = row.model_dump(
            exclude={"company_id", "current_company_name", "source_file_name"},
            exclude_none=True,
        )
        source_payload = dict(values.get("source_payload") or {})
        if row.source_file_name:
            source_payload["source_file_name"] = row.source_file_name
        if row.current_company_name:
            source_payload["current_company_name"] = row.current_company_name
        values["source_payload"] = source_payload or None
        return values

    def _proposed_fields(self, row: CompanyIdentityInputRow) -> dict[str, Any]:
        values = self._model_values(row)
        values["company_id"] = row.company_id
        return values

    def _profile_dict(self, profile: CompanyIdentityProfile) -> dict[str, Any]:
        return {
            "id": profile.id,
            "company_id": profile.company_id,
            "legal_name": profile.legal_name,
            "short_name": profile.short_name,
            "display_name": profile.display_name,
            "inn": profile.inn,
            "ogrn": profile.ogrn,
            "issuer_group_name": profile.issuer_group_name,
            "issuer_group_inn": profile.issuer_group_inn,
            "issuer_role": profile.issuer_role,
            "identity_status": profile.identity_status,
            "identity_confidence": profile.identity_confidence,
            "identity_source": profile.identity_source,
            "review_status": profile.review_status,
            "review_notes": profile.review_notes,
        }

    def _identity_status(
        self,
        company: Company,
        profile: CompanyIdentityProfile | None,
    ) -> str:
        if profile is not None:
            return profile.identity_status
        if self._is_unknown_company_name(company.name):
            return "unknown"
        return "weak" if not self._clean(company.inn) else "matched"

    def _is_protected_profile(self, profile: CompanyIdentityProfile) -> bool:
        return (
            profile.identity_status == "verified"
            or profile.review_status in {"accepted", "reviewed"}
        )

    def _preferred_company_name(self, row: CompanyIdentityInputRow) -> str | None:
        return (
            self._clean(row.display_name)
            or self._clean(row.short_name)
            or self._clean(row.legal_name)
        )

    def _is_unknown_company_name(self, value: str | None) -> bool:
        return str(value or "").startswith(UNKNOWN_NAME_PREFIX)

    def _normalize_name(self, value: str | None) -> str:
        return " ".join(str(value or "").casefold().split())

    def _clean(self, value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _message(
        self,
        row_index: int | None,
        company_id: int | None,
        code: str,
        message: str,
    ) -> CompanyIdentityRowMessage:
        return CompanyIdentityRowMessage(
            row_index=row_index,
            company_id=company_id,
            code=code,
            message=message,
        )
