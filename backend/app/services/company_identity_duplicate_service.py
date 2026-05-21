from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.company_identity_profile import CompanyIdentityProfile
from app.schemas.company_identity import (
    CompanyIdentityDuplicateAffectedRowsSummary,
    CompanyIdentityDuplicateApplyRequest,
    CompanyIdentityDuplicateApplyResult,
    CompanyIdentityDuplicateApplyRow,
    CompanyIdentityDuplicateCandidateSummary,
    CompanyIdentityDuplicateDiagnosticsResult,
    CompanyIdentityDuplicateGroup,
    CompanyIdentityDuplicatePreviewRequest,
    CompanyIdentityDuplicatePreviewResult,
    CompanyIdentityDuplicatePreviewRow,
    CompanyIdentityDuplicateReviewRow,
    CompanyIdentityDuplicateWarning,
    CompanyIdentityRowMessage,
)
from app.services.issuer_identity_normalization import (
    extract_issuer_phrase_from_bond_name,
    issuer_phrase_tokens,
    normalize_issuer_name,
)


UNKNOWN_NAME_PREFIX = "Unknown issuer for "
STRONG_STATUSES = {"matched", "verified"}
PROTECTED_STATUSES = {"verified"}
PROTECTED_REVIEW_STATUSES = {"accepted", "reviewed"}
HIGH_SCORE = Decimal("0.9000")
MEDIUM_SCORE = Decimal("0.7000")
LOW_SCORE = Decimal("0.5000")


@dataclass
class DuplicateCandidate:
    canonical_company_id: int
    candidate_company_id: int
    group_key: str
    match_type: str
    match_score: Decimal
    match_reasons: list[str] = field(default_factory=list)
    sample_secids: list[str] = field(default_factory=list)
    sample_bond_names: list[str] = field(default_factory=list)
    persisted_status: str | None = None
    review_status: str | None = None


class CompanyIdentityDuplicateService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def diagnostics(
        self,
        *,
        active_only: bool = True,
        limit: int = 50,
        min_score: Decimal = Decimal("0.5000"),
        include_bonds: bool = True,
        include_rejected: bool = False,
    ) -> CompanyIdentityDuplicateDiagnosticsResult:
        companies = self._scoped_companies(active_only=active_only)
        scoped_company_ids = {company.id for company in companies}
        profiles = self._profiles_by_company_id()
        bonds = self._bonds_by_company_id(companies) if include_bonds else {}
        persisted = {
            pair: row
            for pair, row in self._persisted_by_pair().items()
            if not active_only
            or (
                pair[0] in scoped_company_ids
                and pair[1] in scoped_company_ids
            )
        }
        candidates = self._detect_candidates(companies, profiles, bonds, min_score)

        if not include_rejected:
            candidates = {
                pair: candidate
                for pair, candidate in candidates.items()
                if persisted.get(pair) is None or persisted[pair].status != "rejected"
            }

        for pair, row in persisted.items():
            if row.status == "rejected" and not include_rejected:
                continue
            if pair in candidates:
                candidates[pair].persisted_status = row.status
                candidates[pair].review_status = row.review_status
            elif row.match_score >= min_score:
                candidates[pair] = self._candidate_from_persisted(row, bonds)

        sorted_candidates = sorted(
            candidates.values(),
            key=lambda item: (
                -item.match_score,
                item.group_key,
                item.canonical_company_id,
                item.candidate_company_id,
            ),
        )[: max(0, limit)]
        groups = self._groups(sorted_candidates, companies, profiles)
        pair_count = sum(len(group.candidates) for group in groups)
        warnings: list[CompanyIdentityDuplicateWarning] = []
        if pair_count:
            warnings.append(
                CompanyIdentityDuplicateWarning(
                    code="duplicate_identity_candidates_found",
                    message="Potential same-issuer company rows require review.",
                )
            )
        return CompanyIdentityDuplicateDiagnosticsResult(
            status="warning" if warnings else "passed",
            candidate_group_count=len(groups),
            candidate_pair_count=pair_count,
            high_confidence_count=sum(
                1 for item in sorted_candidates if item.match_score >= HIGH_SCORE
            ),
            medium_confidence_count=sum(
                1
                for item in sorted_candidates
                if MEDIUM_SCORE <= item.match_score < HIGH_SCORE
            ),
            low_confidence_count=sum(
                1
                for item in sorted_candidates
                if LOW_SCORE <= item.match_score < MEDIUM_SCORE
            ),
            groups=groups,
            warnings=warnings,
        )

    def preview(
        self,
        request: CompanyIdentityDuplicatePreviewRequest,
    ) -> CompanyIdentityDuplicatePreviewResult:
        rows: list[CompanyIdentityDuplicatePreviewRow] = []
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        create_count = update_count = 0

        for row_index, row in enumerate(request.rows, start=1):
            preview_row = self._preview_row(
                row,
                row_index=row_index,
                allow_conflicts=request.allow_conflicts,
                allow_weak_canonical=request.allow_weak_canonical,
            )
            rows.append(preview_row)
            errors.extend(preview_row.errors)
            warnings.extend(preview_row.warnings)
            warnings.extend(preview_row.conflicts)
            if preview_row.would_create_duplicate_candidate:
                create_count += 1
            if preview_row.would_update_duplicate_candidate:
                update_count += 1

        invalid_indexes = {item.row_index for item in errors if item.row_index is not None}
        return CompanyIdentityDuplicatePreviewResult(
            status="failed" if errors else "warning" if warnings else "passed",
            total_rows=len(request.rows),
            valid_rows=len(request.rows) - len(invalid_indexes),
            invalid_rows=len(invalid_indexes),
            would_create_duplicate_candidates=create_count,
            would_update_duplicate_candidates=update_count,
            rows=rows,
            errors=errors,
            warnings=warnings,
        )

    def apply(
        self,
        request: CompanyIdentityDuplicateApplyRequest,
    ) -> CompanyIdentityDuplicateApplyResult:
        if not request.confirm_apply:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Duplicate candidate apply requires confirm_apply=true.",
            )

        preview = self.preview(
            CompanyIdentityDuplicatePreviewRequest(
                rows=request.rows,
                allow_conflicts=request.allow_conflicts,
                allow_weak_canonical=request.allow_weak_canonical,
            )
        )
        rows: list[CompanyIdentityDuplicateApplyRow] = []
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        created = updated = skipped = failed = conflict_count = 0
        affected_pairs: list[dict[str, int]] = []
        affected_candidate_ids: set[int] = set()

        for preview_row, input_row in zip(preview.rows, request.rows, strict=False):
            if preview_row.errors:
                failed += 1
                errors.extend(preview_row.errors)
                conflict_count += len(preview_row.conflicts)
                rows.append(
                    CompanyIdentityDuplicateApplyRow(
                        row_index=preview_row.row_index,
                        canonical_company_id=preview_row.canonical_company_id,
                        candidate_company_id=preview_row.candidate_company_id,
                        action="failed",
                        errors=preview_row.errors,
                        warnings=preview_row.warnings,
                        conflicts=preview_row.conflicts,
                    )
                )
                continue
            if preview_row.conflicts and not request.allow_conflicts:
                failed += 1
                errors.extend(preview_row.conflicts)
                conflict_count += len(preview_row.conflicts)
                rows.append(
                    CompanyIdentityDuplicateApplyRow(
                        row_index=preview_row.row_index,
                        canonical_company_id=preview_row.canonical_company_id,
                        candidate_company_id=preview_row.candidate_company_id,
                        action="blocked_conflict",
                        errors=preview_row.conflicts,
                        warnings=preview_row.warnings,
                        conflicts=preview_row.conflicts,
                    )
                )
                continue
            action = self._apply_row(input_row, preview_row=preview_row)
            if action == "created":
                created += 1
            elif action == "updated":
                updated += 1
            else:
                skipped += 1
            warnings.extend(preview_row.warnings)
            warnings.extend(preview_row.conflicts)
            conflict_count += len(preview_row.conflicts)
            affected_candidate_ids.add(preview_row.candidate_company_id)
            affected_pairs.append(
                {
                    "canonical_company_id": preview_row.canonical_company_id,
                    "candidate_company_id": preview_row.candidate_company_id,
                }
            )
            rows.append(
                CompanyIdentityDuplicateApplyRow(
                    row_index=preview_row.row_index,
                    canonical_company_id=preview_row.canonical_company_id,
                    candidate_company_id=preview_row.candidate_company_id,
                    action=action,
                    warnings=preview_row.warnings,
                    conflicts=preview_row.conflicts,
                )
            )

        self.db.commit()
        return CompanyIdentityDuplicateApplyResult(
            status="failed" if errors else "warning" if warnings else "completed",
            total_rows=len(request.rows),
            created=created,
            updated=updated,
            skipped=skipped,
            failed=failed,
            affected_rows_summary=CompanyIdentityDuplicateAffectedRowsSummary(
                affected_candidate_ids=sorted(affected_candidate_ids),
                affected_pairs=affected_pairs,
                created_candidate_count=created,
                updated_candidate_count=updated,
                skipped_count=skipped,
                conflict_count=conflict_count,
                warning_count=len(warnings),
            ),
            rows=rows,
            errors=errors,
            warnings=warnings,
        )

    def _detect_candidates(
        self,
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        bonds: dict[int, list[Bond]],
        min_score: Decimal,
    ) -> dict[tuple[int, int], DuplicateCandidate]:
        result: dict[tuple[int, int], DuplicateCandidate] = {}
        self._detect_exact_field(result, companies, profiles, "inn", "exact_inn", Decimal("1.0000"))
        self._detect_exact_field(result, companies, profiles, "ogrn", "exact_ogrn", Decimal("1.0000"))
        self._detect_normalized_names(result, companies, profiles)
        self._detect_group_identity(result, companies, profiles)
        self._detect_bond_name_contains_known_identity(result, companies, profiles, bonds)
        self._detect_shared_bond_phrase(result, companies, profiles, bonds)
        return {
            pair: candidate
            for pair, candidate in result.items()
            if candidate.match_score >= min_score
        }

    def _detect_exact_field(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        field_name: str,
        match_type: str,
        score_value: Decimal,
    ) -> None:
        grouped: dict[str, list[Company]] = defaultdict(list)
        for company in companies:
            profile = profiles.get(company.id)
            value = self._clean(getattr(profile, field_name, None)) if profile else None
            if field_name == "inn" and value is None:
                value = self._clean(company.inn)
            if value:
                grouped[value].append(company)
        for value, rows in grouped.items():
            if len(rows) < 2:
                continue
            self._record_group_pairs(
                result,
                rows,
                profiles,
                group_key=f"{match_type}:{value}",
                match_type=match_type,
                score_value=score_value,
                reason=f"Same {field_name.upper()}: {value}",
            )

    def _detect_normalized_names(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
    ) -> None:
        legal_grouped: dict[str, list[Company]] = defaultdict(list)
        display_grouped: dict[str, list[Company]] = defaultdict(list)
        for company in companies:
            profile = profiles.get(company.id)
            legal = normalize_issuer_name(None if profile is None else profile.legal_name)
            if len(legal) >= 3:
                legal_grouped[legal].append(company)
            seen_display_values: set[str] = set()
            for value in (
                company.name,
                None if profile is None else profile.display_name,
                None if profile is None else profile.short_name,
            ):
                normalized = normalize_issuer_name(value)
                if (
                    len(normalized) >= 3
                    and normalized not in seen_display_values
                    and not self._is_unknown_company_name(value)
                ):
                    display_grouped[normalized].append(company)
                    seen_display_values.add(normalized)
        for value, rows in legal_grouped.items():
            if len(rows) >= 2:
                self._record_group_pairs(
                    result,
                    rows,
                    profiles,
                    group_key=f"legal:{value}",
                    match_type="exact_legal_name",
                    score_value=Decimal("0.9500"),
                    reason=f"Same normalized legal name: {value}",
                )
        for value, rows in display_grouped.items():
            if len(rows) >= 2:
                self._record_group_pairs(
                    result,
                    rows,
                    profiles,
                    group_key=f"name:{value}",
                    match_type="normalized_name",
                    score_value=Decimal("0.7500"),
                    reason=f"Same normalized display or company name: {value}",
                )

    def _detect_group_identity(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
    ) -> None:
        grouped_by_inn: dict[str, list[Company]] = defaultdict(list)
        grouped_by_name: dict[str, list[Company]] = defaultdict(list)
        for company in companies:
            profile = profiles.get(company.id)
            if profile is None:
                continue
            group_inn = self._clean(profile.issuer_group_inn)
            group_name = normalize_issuer_name(profile.issuer_group_name)
            if group_inn:
                grouped_by_inn[group_inn].append(company)
            if len(group_name) >= 3:
                grouped_by_name[group_name].append(company)
        for value, rows in grouped_by_inn.items():
            if len(rows) >= 2:
                self._record_group_pairs(
                    result,
                    rows,
                    profiles,
                    group_key=f"group_inn:{value}",
                    match_type="same_group_name",
                    score_value=Decimal("1.0000"),
                    reason=f"Same issuer group INN: {value}",
                )
        for value, rows in grouped_by_name.items():
            if len(rows) >= 2:
                self._record_group_pairs(
                    result,
                    rows,
                    profiles,
                    group_key=f"group_name:{value}",
                    match_type="same_group_name",
                    score_value=Decimal("0.7500"),
                    reason=f"Same normalized issuer group name: {value}",
                )

    def _detect_bond_name_contains_known_identity(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        bonds: dict[int, list[Bond]],
    ) -> None:
        known_companies = [
            company
            for company in companies
            if (profiles.get(company.id) is not None and profiles[company.id].identity_status in STRONG_STATUSES)
        ]
        candidate_companies = [
            company
            for company in companies
            if company not in known_companies
            or self._is_unknown_company_name(company.name)
            or profiles.get(company.id) is None
            or profiles[company.id].identity_status in {"unknown", "weak", "conflict"}
        ]
        for canonical in known_companies:
            profile = profiles.get(canonical.id)
            labels = self._identity_labels(canonical, profile)
            for candidate in candidate_companies:
                if candidate.id == canonical.id:
                    continue
                for bond in bonds.get(candidate.id, []):
                    normalized_bond = normalize_issuer_name(bond.name)
                    for label in labels:
                        if len(label) >= 2 and label in normalized_bond:
                            score_value = (
                                Decimal("0.7500")
                                if len(label) >= 3
                                else Decimal("0.4000")
                            )
                            self._record_candidate(
                                result,
                                canonical,
                                candidate,
                                profiles,
                                bonds,
                                group_key=f"bond_phrase:{label}",
                                match_type="bond_name_phrase",
                                score_value=score_value,
                                reason=f"Bond name contains canonical identity phrase: {label}",
                            )
                            break

    def _detect_shared_bond_phrase(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        bonds: dict[int, list[Bond]],
    ) -> None:
        grouped: dict[str, list[Company]] = defaultdict(list)
        for company in companies:
            phrases = {
                phrase
                for bond in bonds.get(company.id, [])
                if (phrase := extract_issuer_phrase_from_bond_name(bond.name))
                and len(phrase) >= 3
                and len(issuer_phrase_tokens(phrase)) >= 1
            }
            for phrase in phrases:
                grouped[phrase].append(company)
        for phrase, rows in grouped.items():
            if len(rows) < 2:
                continue
            self._record_group_pairs(
                result,
                rows,
                profiles,
                group_key=f"bond_extract:{phrase}",
                match_type="bond_name_phrase",
                score_value=Decimal("0.6500"),
                reason=f"Same extracted issuer phrase from bond names: {phrase}",
            )

    def _record_group_pairs(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        rows: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
        *,
        group_key: str,
        match_type: str,
        score_value: Decimal,
        reason: str,
    ) -> None:
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                canonical, candidate = self._canonical_pair(left, right, profiles)
                if canonical.id == candidate.id:
                    continue
                self._record_candidate(
                    result,
                    canonical,
                    candidate,
                    profiles,
                    {},
                    group_key=group_key,
                    match_type=match_type,
                    score_value=score_value,
                    reason=reason,
                )

    def _record_candidate(
        self,
        result: dict[tuple[int, int], DuplicateCandidate],
        canonical: Company,
        candidate: Company,
        profiles: dict[int, CompanyIdentityProfile],
        bonds: dict[int, list[Bond]],
        *,
        group_key: str,
        match_type: str,
        score_value: Decimal,
        reason: str,
    ) -> None:
        pair = (canonical.id, candidate.id)
        existing = result.get(pair)
        sample_bonds = bonds.get(candidate.id, [])
        sample_secids = [bond.secid for bond in sample_bonds if bond.secid][:5]
        sample_names = [bond.name for bond in sample_bonds if bond.name][:5]
        if existing is None or score_value > existing.match_score:
            result[pair] = DuplicateCandidate(
                canonical_company_id=canonical.id,
                candidate_company_id=candidate.id,
                group_key=group_key,
                match_type=match_type,
                match_score=score_value,
                match_reasons=[reason],
                sample_secids=sample_secids,
                sample_bond_names=sample_names,
            )
        elif score_value == existing.match_score and reason not in existing.match_reasons:
            existing.match_reasons.append(reason)
            existing.match_type = existing.match_type if existing.match_type == match_type else "mixed"

    def _preview_row(
        self,
        row: CompanyIdentityDuplicateReviewRow,
        *,
        row_index: int,
        allow_conflicts: bool,
        allow_weak_canonical: bool,
    ) -> CompanyIdentityDuplicatePreviewRow:
        errors: list[CompanyIdentityRowMessage] = []
        warnings: list[CompanyIdentityRowMessage] = []
        conflicts: list[CompanyIdentityRowMessage] = []
        canonical = self.db.get(Company, row.canonical_company_id)
        candidate = self.db.get(Company, row.candidate_company_id)
        canonical_profile = self._profile_by_company_id(row.canonical_company_id)
        candidate_profile = self._profile_by_company_id(row.candidate_company_id)
        existing = self._persisted_pair(row.canonical_company_id, row.candidate_company_id)

        if row.canonical_company_id == row.candidate_company_id:
            errors.append(self._message(row_index, row.candidate_company_id, "same_company", "Canonical and candidate company IDs must differ."))
        if canonical is None:
            errors.append(self._message(row_index, row.canonical_company_id, "canonical_company_not_found", "Canonical company could not be resolved."))
        if candidate is None:
            errors.append(self._message(row_index, row.candidate_company_id, "candidate_company_not_found", "Candidate company could not be resolved."))
        if canonical is not None and canonical_profile is None and not allow_weak_canonical:
            errors.append(self._message(row_index, row.canonical_company_id, "canonical_identity_missing", "Canonical company has no identity profile."))
        elif canonical_profile is not None and canonical_profile.identity_status not in STRONG_STATUSES and not allow_weak_canonical:
            errors.append(self._message(row_index, row.canonical_company_id, "canonical_identity_weak", "Canonical identity is not matched or verified."))

        conflicts.extend(
            self._candidate_conflicts(
                row_index,
                row,
                canonical_profile=canonical_profile,
                candidate_profile=candidate_profile,
            )
        )
        if conflicts and allow_conflicts:
            warnings.extend(conflicts)
            conflicts = []

        proposed = self._proposed_fields(row)
        return CompanyIdentityDuplicatePreviewRow(
            row_index=row_index,
            canonical_company_id=row.canonical_company_id,
            candidate_company_id=row.candidate_company_id,
            current_canonical_company=None if canonical is None else self._company_dict(canonical),
            current_candidate_company=None if candidate is None else self._company_dict(candidate),
            canonical_identity_profile=None if canonical_profile is None else self._profile_dict(canonical_profile),
            candidate_identity_profile=None if candidate_profile is None else self._profile_dict(candidate_profile),
            existing_duplicate_candidate=None if existing is None else self._duplicate_dict(existing),
            proposed_duplicate_fields=proposed,
            conflicts=conflicts,
            warnings=warnings,
            errors=errors,
            would_create_duplicate_candidate=existing is None and not errors and not conflicts,
            would_update_duplicate_candidate=existing is not None and not errors and not conflicts,
            would_update_identity_profile=False,
            would_update_company=False,
        )

    def _candidate_conflicts(
        self,
        row_index: int,
        row: CompanyIdentityDuplicateReviewRow,
        *,
        canonical_profile: CompanyIdentityProfile | None,
        candidate_profile: CompanyIdentityProfile | None,
    ) -> list[CompanyIdentityRowMessage]:
        if candidate_profile is None or canonical_profile is None:
            return []
        if not self._is_protected_profile(candidate_profile):
            return []
        conflicts: list[CompanyIdentityRowMessage] = []
        if (
            canonical_profile.inn
            and candidate_profile.inn
            and canonical_profile.inn != candidate_profile.inn
        ):
            conflicts.append(self._message(row_index, row.candidate_company_id, "candidate_verified_inn_conflict", "Candidate verified INN differs from canonical profile."))
        if (
            canonical_profile.ogrn
            and candidate_profile.ogrn
            and canonical_profile.ogrn != candidate_profile.ogrn
        ):
            conflicts.append(self._message(row_index, row.candidate_company_id, "candidate_verified_ogrn_conflict", "Candidate verified OGRN differs from canonical profile."))
        canonical_name = normalize_issuer_name(canonical_profile.legal_name)
        candidate_name = normalize_issuer_name(candidate_profile.legal_name)
        if canonical_name and candidate_name and canonical_name != candidate_name:
            conflicts.append(self._message(row_index, row.candidate_company_id, "candidate_verified_legal_name_conflict", "Candidate verified legal name differs from canonical profile."))
        return conflicts

    def _apply_row(
        self,
        row: CompanyIdentityDuplicateReviewRow,
        *,
        preview_row: CompanyIdentityDuplicatePreviewRow,
    ) -> str:
        existing = self._persisted_pair(row.canonical_company_id, row.candidate_company_id)
        values = self._model_values(row)
        if existing is None:
            existing = CompanyIdentityDuplicateCandidate(**values)
            self.db.add(existing)
            action = "created"
        else:
            for field, value in values.items():
                setattr(existing, field, value)
            self.db.add(existing)
            action = "updated"
        self.db.flush()
        return action

    def _model_values(self, row: CompanyIdentityDuplicateReviewRow) -> dict[str, Any]:
        return {
            "canonical_company_id": row.canonical_company_id,
            "candidate_company_id": row.candidate_company_id,
            "group_key": self._group_key(row),
            "match_type": row.match_type,
            "match_score": row.match_score,
            "match_reasons": list(row.match_reasons),
            "status": row.status,
            "review_status": row.review_status,
            "review_notes": row.review_notes,
            "source": row.source,
        }

    def _proposed_fields(self, row: CompanyIdentityDuplicateReviewRow) -> dict[str, Any]:
        return self._model_values(row)

    def _candidate_from_persisted(
        self,
        row: CompanyIdentityDuplicateCandidate,
        bonds: dict[int, list[Bond]],
    ) -> DuplicateCandidate:
        sample_bonds = bonds.get(row.candidate_company_id, [])
        return DuplicateCandidate(
            canonical_company_id=row.canonical_company_id,
            candidate_company_id=row.candidate_company_id,
            group_key=row.group_key,
            match_type=row.match_type,
            match_score=row.match_score,
            match_reasons=self._reason_list(row.match_reasons),
            sample_secids=[bond.secid for bond in sample_bonds if bond.secid][:5],
            sample_bond_names=[bond.name for bond in sample_bonds if bond.name][:5],
            persisted_status=row.status,
            review_status=row.review_status,
        )

    def _groups(
        self,
        candidates: list[DuplicateCandidate],
        companies: list[Company],
        profiles: dict[int, CompanyIdentityProfile],
    ) -> list[CompanyIdentityDuplicateGroup]:
        companies_by_id = {company.id: company for company in companies}
        grouped: dict[tuple[str, int], list[DuplicateCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[(candidate.group_key, candidate.canonical_company_id)].append(candidate)
        groups: list[CompanyIdentityDuplicateGroup] = []
        for (group_key, canonical_id), rows in grouped.items():
            canonical = companies_by_id.get(canonical_id) or self.db.get(Company, canonical_id)
            if canonical is None:
                continue
            profile = profiles.get(canonical_id)
            groups.append(
                CompanyIdentityDuplicateGroup(
                    group_key=group_key,
                    canonical_company_id=canonical_id,
                    canonical_company_name=canonical.name,
                    canonical_identity_status="unknown" if profile is None else profile.identity_status,
                    candidates=[
                        self._summary(row, companies_by_id)
                        for row in sorted(rows, key=lambda item: -item.match_score)
                    ],
                )
            )
        return sorted(groups, key=lambda group: (-max((c.match_score for c in group.candidates), default=Decimal("0")), group.group_key))

    def _summary(
        self,
        candidate: DuplicateCandidate,
        companies_by_id: dict[int, Company],
    ) -> CompanyIdentityDuplicateCandidateSummary:
        company = companies_by_id.get(candidate.candidate_company_id) or self.db.get(
            Company,
            candidate.candidate_company_id,
        )
        return CompanyIdentityDuplicateCandidateSummary(
            company_id=candidate.candidate_company_id,
            company_name="" if company is None else company.name,
            match_score=candidate.match_score,
            match_type=candidate.match_type,
            match_reasons=candidate.match_reasons,
            sample_secids=candidate.sample_secids,
            sample_bond_names=candidate.sample_bond_names,
            recommended_action="review",
            persisted_status=candidate.persisted_status,
            review_status=candidate.review_status,
        )

    def _canonical_pair(
        self,
        left: Company,
        right: Company,
        profiles: dict[int, CompanyIdentityProfile],
    ) -> tuple[Company, Company]:
        left_key = self._canonical_rank(left, profiles.get(left.id))
        right_key = self._canonical_rank(right, profiles.get(right.id))
        return (left, right) if left_key <= right_key else (right, left)

    def _canonical_rank(
        self,
        company: Company,
        profile: CompanyIdentityProfile | None,
    ) -> tuple[int, int, int]:
        status_rank = {"verified": 0, "matched": 1, "weak": 2, "conflict": 3, "unknown": 4}
        status = "unknown" if profile is None else profile.identity_status
        richness = 0
        if profile is not None:
            richness = sum(
                1
                for value in (
                    profile.legal_name,
                    profile.display_name,
                    profile.short_name,
                    profile.inn,
                    profile.ogrn,
                )
                if self._clean(value)
            )
        return (status_rank.get(status, 5), -richness, company.id)

    def _identity_labels(
        self,
        company: Company,
        profile: CompanyIdentityProfile | None,
    ) -> list[str]:
        values = [
            company.name,
            None if profile is None else profile.display_name,
            None if profile is None else profile.short_name,
            None if profile is None else profile.legal_name,
        ]
        labels: list[str] = []
        for value in values:
            normalized = normalize_issuer_name(value)
            if normalized and normalized not in labels and not self._is_unknown_company_name(value):
                labels.append(normalized)
        return labels

    def _scoped_companies(self, *, active_only: bool) -> list[Company]:
        if not active_only:
            return list(self.db.execute(select(Company)).scalars())
        return list(self.db.execute(select(Company).join(Bond).distinct()).scalars())

    def _profiles_by_company_id(self) -> dict[int, CompanyIdentityProfile]:
        return {
            profile.company_id: profile
            for profile in self.db.execute(select(CompanyIdentityProfile)).scalars()
        }

    def _bonds_by_company_id(self, companies: list[Company]) -> dict[int, list[Bond]]:
        company_ids = [company.id for company in companies]
        if not company_ids:
            return {}
        rows = self.db.execute(select(Bond).where(Bond.company_id.in_(company_ids))).scalars()
        grouped: dict[int, list[Bond]] = defaultdict(list)
        for bond in rows:
            grouped[bond.company_id].append(bond)
        return grouped

    def _persisted_by_pair(self) -> dict[tuple[int, int], CompanyIdentityDuplicateCandidate]:
        return {
            (row.canonical_company_id, row.candidate_company_id): row
            for row in self.db.execute(select(CompanyIdentityDuplicateCandidate)).scalars()
        }

    def _persisted_pair(
        self,
        canonical_company_id: int,
        candidate_company_id: int,
    ) -> CompanyIdentityDuplicateCandidate | None:
        return self.db.execute(
            select(CompanyIdentityDuplicateCandidate).where(
                CompanyIdentityDuplicateCandidate.canonical_company_id == canonical_company_id,
                CompanyIdentityDuplicateCandidate.candidate_company_id == candidate_company_id,
            )
        ).scalar_one_or_none()

    def _profile_by_company_id(self, company_id: int) -> CompanyIdentityProfile | None:
        return self.db.execute(
            select(CompanyIdentityProfile).where(
                CompanyIdentityProfile.company_id == company_id
            )
        ).scalar_one_or_none()

    def _group_key(self, row: CompanyIdentityDuplicateReviewRow) -> str:
        reasons = "_".join(row.match_reasons[:1]) if row.match_reasons else row.match_type
        normalized = normalize_issuer_name(reasons)
        return normalized[:255] or f"manual:{row.canonical_company_id}:{row.candidate_company_id}"

    def _reason_list(self, value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        return [str(value)] if value else []

    def _company_dict(self, company: Company) -> dict[str, Any]:
        return {
            "id": company.id,
            "name": company.name,
            "ticker": company.ticker,
            "inn": company.inn,
        }

    def _profile_dict(self, profile: CompanyIdentityProfile) -> dict[str, Any]:
        return {
            "id": profile.id,
            "company_id": profile.company_id,
            "legal_name": profile.legal_name,
            "display_name": profile.display_name,
            "short_name": profile.short_name,
            "inn": profile.inn,
            "ogrn": profile.ogrn,
            "issuer_group_name": profile.issuer_group_name,
            "issuer_group_inn": profile.issuer_group_inn,
            "identity_status": profile.identity_status,
            "review_status": profile.review_status,
        }

    def _duplicate_dict(self, row: CompanyIdentityDuplicateCandidate) -> dict[str, Any]:
        return {
            "id": row.id,
            "canonical_company_id": row.canonical_company_id,
            "candidate_company_id": row.candidate_company_id,
            "group_key": row.group_key,
            "match_type": row.match_type,
            "match_score": row.match_score,
            "match_reasons": row.match_reasons,
            "status": row.status,
            "review_status": row.review_status,
            "review_notes": row.review_notes,
            "source": row.source,
        }

    def _is_protected_profile(self, profile: CompanyIdentityProfile) -> bool:
        return (
            profile.identity_status in PROTECTED_STATUSES
            or profile.review_status in PROTECTED_REVIEW_STATUSES
        )

    def _is_unknown_company_name(self, value: str | None) -> bool:
        return str(value or "").startswith(UNKNOWN_NAME_PREFIX)

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
