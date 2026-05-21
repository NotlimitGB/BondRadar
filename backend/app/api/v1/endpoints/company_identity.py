from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.company_identity import (
    CompanyIdentityApplyRequest,
    CompanyIdentityApplyResult,
    CompanyIdentityDiagnosticsResult,
    CompanyIdentityCanonicalGroupsResult,
    CompanyIdentityDuplicateApplyRequest,
    CompanyIdentityDuplicateApplyResult,
    CompanyIdentityDuplicateDiagnosticsResult,
    CompanyIdentityDuplicatePreviewRequest,
    CompanyIdentityDuplicatePreviewResult,
    CompanyIdentityPreviewRequest,
    CompanyIdentityPreviewResult,
    CompanyIdentityProfileRead,
)
from app.services.company_identity_duplicate_service import (
    CompanyIdentityDuplicateService,
)
from app.services.company_identity_resolution_service import (
    CompanyIdentityResolutionService,
)
from app.services.issuer_identity_service import IssuerIdentityService


router = APIRouter()


@router.get(
    "/canonical-groups",
    response_model=CompanyIdentityCanonicalGroupsResult,
)
def get_canonical_groups(
    active_only: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> CompanyIdentityCanonicalGroupsResult:
    return CompanyIdentityResolutionService(db).get_canonical_groups(
        active_only=active_only,
    )


@router.get(
    "/duplicates/diagnostics",
    response_model=CompanyIdentityDuplicateDiagnosticsResult,
)
def get_duplicate_diagnostics(
    active_only: bool = Query(default=True),
    limit: int = Query(default=50, ge=0, le=500),
    min_score: Decimal = Query(default=Decimal("0.5000"), ge=0, le=1),
    include_bonds: bool = Query(default=True),
    include_rejected: bool = Query(default=False),
    db: Session = Depends(get_db),
) -> CompanyIdentityDuplicateDiagnosticsResult:
    return CompanyIdentityDuplicateService(db).diagnostics(
        active_only=active_only,
        limit=limit,
        min_score=min_score,
        include_bonds=include_bonds,
        include_rejected=include_rejected,
    )


@router.post(
    "/duplicates/preview",
    response_model=CompanyIdentityDuplicatePreviewResult,
)
def preview_duplicate_updates(
    request: CompanyIdentityDuplicatePreviewRequest,
    db: Session = Depends(get_db),
) -> CompanyIdentityDuplicatePreviewResult:
    return CompanyIdentityDuplicateService(db).preview(request)


@router.post(
    "/duplicates/apply",
    response_model=CompanyIdentityDuplicateApplyResult,
)
def apply_duplicate_updates(
    request: CompanyIdentityDuplicateApplyRequest,
    db: Session = Depends(get_db),
) -> CompanyIdentityDuplicateApplyResult:
    return CompanyIdentityDuplicateService(db).apply(request)


@router.get("/diagnostics", response_model=CompanyIdentityDiagnosticsResult)
def get_identity_diagnostics(
    active_only: bool = Query(default=True),
    limit: int = Query(default=20, ge=0, le=200),
    include_samples: bool = Query(default=True),
    db: Session = Depends(get_db),
) -> CompanyIdentityDiagnosticsResult:
    return IssuerIdentityService(db).diagnostics(
        active_only=active_only,
        limit=limit,
        include_samples=include_samples,
    )


@router.get("/profiles/{company_id}", response_model=CompanyIdentityProfileRead)
def get_identity_profile(
    company_id: int,
    db: Session = Depends(get_db),
) -> CompanyIdentityProfileRead:
    return IssuerIdentityService(db).get_profile(company_id)


@router.post("/preview", response_model=CompanyIdentityPreviewResult)
def preview_identity_updates(
    request: CompanyIdentityPreviewRequest,
    db: Session = Depends(get_db),
) -> CompanyIdentityPreviewResult:
    return IssuerIdentityService(db).preview(request)


@router.post("/apply", response_model=CompanyIdentityApplyResult)
def apply_identity_updates(
    request: CompanyIdentityApplyRequest,
    db: Session = Depends(get_db),
) -> CompanyIdentityApplyResult:
    return IssuerIdentityService(db).apply(request)
