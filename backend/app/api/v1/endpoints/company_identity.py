from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.company_identity import (
    CompanyIdentityApplyRequest,
    CompanyIdentityApplyResult,
    CompanyIdentityDiagnosticsResult,
    CompanyIdentityPreviewRequest,
    CompanyIdentityPreviewResult,
    CompanyIdentityProfileRead,
)
from app.services.issuer_identity_service import IssuerIdentityService


router = APIRouter()


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
