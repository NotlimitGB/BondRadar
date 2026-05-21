from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.models.company_identity_duplicate_candidate import (
    CompanyIdentityDuplicateCandidate,
)
from app.models.company_identity_profile import CompanyIdentityProfile
from app.schemas.company_identity import (
    CompanyIdentityCanonicalDuplicateMember,
    CompanyIdentityCanonicalGroup,
    CompanyIdentityCanonicalGroupsResult,
    CompanyIdentityResolution,
    CompanyIdentityResolutionWarning,
)


ACCEPTED_DUPLICATE_STATUS = "accepted"
ACCEPTED_REVIEW_STATUSES = {"reviewed", "accepted"}


class CompanyIdentityResolutionService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_company(self, company_id: int) -> CompanyIdentityResolution:
        company = self.db.get(Company, company_id)
        if company is None:
            return CompanyIdentityResolution(
                company_id=company_id,
                company_name="",
                canonical_company_id=company_id,
                canonical_company_name="",
                is_canonical=True,
                is_duplicate_candidate=False,
                warnings=[
                    CompanyIdentityResolutionWarning(
                        code="company_not_found",
                        message="Company could not be resolved.",
                        company_id=company_id,
                    )
                ],
            )
        rows = self._accepted_rows_by_candidate().get(company_id, [])
        if not rows:
            return self._self_resolution(company)
        canonical_ids = {row.canonical_company_id for row in rows}
        if len(canonical_ids) > 1:
            return CompanyIdentityResolution(
                company_id=company.id,
                company_name=company.name,
                canonical_company_id=company.id,
                canonical_company_name=company.name,
                is_canonical=True,
                is_duplicate_candidate=False,
                warnings=[
                    CompanyIdentityResolutionWarning(
                        code="multiple_accepted_canonical_mappings",
                        message=(
                            "Company has multiple accepted canonical mappings; "
                            "canonical issuer was not guessed."
                        ),
                        company_id=company.id,
                    )
                ],
            )
        row = rows[0]
        canonical = self.db.get(Company, row.canonical_company_id)
        if canonical is None:
            return CompanyIdentityResolution(
                company_id=company.id,
                company_name=company.name,
                canonical_company_id=company.id,
                canonical_company_name=company.name,
                is_canonical=True,
                is_duplicate_candidate=False,
                warnings=[
                    CompanyIdentityResolutionWarning(
                        code="canonical_company_not_found",
                        message="Accepted mapping points to a missing canonical company.",
                        company_id=company.id,
                    )
                ],
            )
        return CompanyIdentityResolution(
            company_id=company.id,
            company_name=company.name,
            canonical_company_id=canonical.id,
            canonical_company_name=canonical.name,
            is_canonical=False,
            is_duplicate_candidate=True,
            duplicate_mapping_status=row.status,
            duplicate_review_status=row.review_status,
            duplicate_match_type=row.match_type,
            duplicate_match_score=row.match_score,
        )

    def resolve_many(self, company_ids: list[int]) -> dict[int, CompanyIdentityResolution]:
        return {company_id: self.resolve_company(company_id) for company_id in company_ids}

    def get_canonical_groups(
        self,
        *,
        active_only: bool = True,
    ) -> CompanyIdentityCanonicalGroupsResult:
        scoped_ids = self._scoped_company_ids(active_only=active_only)
        if active_only and not scoped_ids:
            accepted_rows: list[CompanyIdentityDuplicateCandidate] = []
        else:
            accepted_rows = [
                row
                for row in self._accepted_rows()
                if not active_only
                or (
                    row.canonical_company_id in scoped_ids
                    and row.candidate_company_id in scoped_ids
                )
            ]
        companies = self._companies_by_id(
            sorted(
                {
                    row.canonical_company_id
                    for row in accepted_rows
                }
                | {row.candidate_company_id for row in accepted_rows}
            )
        )
        profiles = self._profiles_by_company_id()
        rows_by_candidate: dict[int, list[CompanyIdentityDuplicateCandidate]] = defaultdict(list)
        for row in accepted_rows:
            rows_by_candidate[row.candidate_company_id].append(row)

        warnings: list[CompanyIdentityResolutionWarning] = []
        conflict_candidates = {
            company_id: rows
            for company_id, rows in rows_by_candidate.items()
            if len({row.canonical_company_id for row in rows}) > 1
        }
        for company_id in sorted(conflict_candidates):
            warnings.append(
                CompanyIdentityResolutionWarning(
                    code="multiple_accepted_canonical_mappings",
                    message=(
                        "Company has multiple accepted canonical mappings; "
                        "excluded from canonical group output."
                    ),
                    company_id=company_id,
                )
            )

        grouped: dict[int, list[CompanyIdentityDuplicateCandidate]] = defaultdict(list)
        for row in accepted_rows:
            if row.candidate_company_id in conflict_candidates:
                continue
            grouped[row.canonical_company_id].append(row)

        groups: list[CompanyIdentityCanonicalGroup] = []
        for canonical_id, rows in grouped.items():
            canonical = companies.get(canonical_id)
            if canonical is None:
                warnings.append(
                    CompanyIdentityResolutionWarning(
                        code="canonical_company_not_found",
                        message="Accepted duplicate mapping points to missing canonical company.",
                        company_id=canonical_id,
                    )
                )
                continue
            members = [
                self._member(row, companies)
                for row in sorted(rows, key=lambda item: item.candidate_company_id)
                if row.candidate_company_id in companies
            ]
            profile = profiles.get(canonical_id)
            groups.append(
                CompanyIdentityCanonicalGroup(
                    canonical_company_id=canonical.id,
                    canonical_company_name=canonical.name,
                    canonical_ticker=canonical.ticker,
                    canonical_inn=canonical.inn,
                    canonical_identity_status=None if profile is None else profile.identity_status,
                    duplicate_count=len(members),
                    duplicate_company_ids=[member.company_id for member in members],
                    duplicate_members=members,
                )
            )
        return CompanyIdentityCanonicalGroupsResult(
            status="warning" if warnings else "passed",
            group_count=len(groups),
            duplicate_mapping_count=sum(group.duplicate_count for group in groups),
            conflict_count=len(conflict_candidates),
            groups=sorted(groups, key=lambda group: group.canonical_company_id),
            warnings=warnings,
        )

    def _accepted_rows(self) -> list[CompanyIdentityDuplicateCandidate]:
        return list(
            self.db.execute(
                select(CompanyIdentityDuplicateCandidate).where(
                    CompanyIdentityDuplicateCandidate.status == ACCEPTED_DUPLICATE_STATUS,
                    CompanyIdentityDuplicateCandidate.review_status.in_(
                        ACCEPTED_REVIEW_STATUSES
                    ),
                )
            ).scalars()
        )

    def _accepted_rows_by_candidate(
        self,
    ) -> dict[int, list[CompanyIdentityDuplicateCandidate]]:
        grouped: dict[int, list[CompanyIdentityDuplicateCandidate]] = defaultdict(list)
        for row in self._accepted_rows():
            grouped[row.candidate_company_id].append(row)
        return grouped

    def _self_resolution(self, company: Company) -> CompanyIdentityResolution:
        return CompanyIdentityResolution(
            company_id=company.id,
            company_name=company.name,
            canonical_company_id=company.id,
            canonical_company_name=company.name,
            is_canonical=True,
            is_duplicate_candidate=False,
        )

    def _member(
        self,
        row: CompanyIdentityDuplicateCandidate,
        companies: dict[int, Company],
    ) -> CompanyIdentityCanonicalDuplicateMember:
        company = companies[row.candidate_company_id]
        return CompanyIdentityCanonicalDuplicateMember(
            company_id=company.id,
            company_name=company.name,
            ticker=company.ticker,
            inn=company.inn,
            duplicate_mapping_status=row.status,
            duplicate_review_status=row.review_status,
            duplicate_match_type=row.match_type,
            duplicate_match_score=row.match_score,
        )

    def _scoped_company_ids(self, *, active_only: bool) -> set[int]:
        if not active_only:
            return set()
        return set(self.db.execute(select(Bond.company_id).distinct()).scalars())

    def _companies_by_id(self, company_ids: list[int]) -> dict[int, Company]:
        if not company_ids:
            return {}
        return {
            company.id: company
            for company in self.db.execute(
                select(Company).where(Company.id.in_(company_ids))
            ).scalars()
        }

    def _profiles_by_company_id(self) -> dict[int, CompanyIdentityProfile]:
        return {
            profile.company_id: profile
            for profile in self.db.execute(select(CompanyIdentityProfile)).scalars()
        }
