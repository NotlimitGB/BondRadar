from pydantic import BaseModel


class ImportErrorRead(BaseModel):
    row_number: int
    identifier: str | None = None
    error: str


class ImportSummaryRead(BaseModel):
    total_rows: int
    processed_rows: int
    failed_rows: int
    created: int
    updated: int
    skipped: int
    companies_created: int
    companies_updated: int
    errors: list[ImportErrorRead]
