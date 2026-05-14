from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.schemas.imports import ImportSummaryRead
from app.services.csv_import import CSVImportService


router = APIRouter()


@router.post("/bonds-csv", response_model=ImportSummaryRead)
async def import_bonds_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ImportSummaryRead:
    return await CSVImportService(db).import_bonds_csv(file)


@router.post("/reports-csv", response_model=ImportSummaryRead)
async def import_reports_csv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ImportSummaryRead:
    return await CSVImportService(db).import_reports_csv(file)
