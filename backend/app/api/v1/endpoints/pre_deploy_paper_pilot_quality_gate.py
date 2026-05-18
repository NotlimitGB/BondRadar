from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.pre_deploy_paper_pilot_quality_gate import (
    PreDeployPaperPilotQualityGateRequest,
    PreDeployPaperPilotQualityGateResponse,
)
from app.services.pre_deploy_paper_pilot_quality_gate_service import (
    PreDeployPaperPilotQualityGateService,
)


router = APIRouter()


@router.post("/quality-gate", response_model=PreDeployPaperPilotQualityGateResponse)
def run_pre_deploy_paper_pilot_quality_gate(
    request: PreDeployPaperPilotQualityGateRequest,
    db: Session = Depends(get_db),
) -> PreDeployPaperPilotQualityGateResponse:
    return PreDeployPaperPilotQualityGateService(db).check(request)
