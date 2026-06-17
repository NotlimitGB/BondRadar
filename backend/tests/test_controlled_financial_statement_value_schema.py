from __future__ import annotations

from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys

from sqlalchemy import UniqueConstraint

from app.models.controlled_financial_statement_value import (
    ControlledFinancialStatementValue,
)
from app.services.controlled_financial_statement_value_service import (
    ControlledFinancialStatementValueService,
)


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.append(str(SCRIPTS))

import financial_official_source_evidence_assistant as assistant  # noqa: E402


REQUIRED_CONTROLLED_VALUE_COLUMNS = {
    "company_id",
    "company_name",
    "report_year",
    "report_standard",
    "target_type",
    "metric_key",
    "metric_role",
    "metric_name_ru",
    "metric_name_en",
    "statement_page",
    "page_number",
    "value_2025",
    "value_2024",
    "raw_value_2025",
    "raw_value_2024",
    "raw_line",
    "note_reference",
    "source_pdf_sha256",
    "plan_checksum_sha256",
    "plan_rows_checksum_sha256",
    "natural_key",
    "natural_key_sha256",
    "row_checksum_sha256",
    "created_at",
    "updated_at",
}


def test_controlled_financial_statement_value_model_has_required_columns() -> None:
    columns = {column.name for column in ControlledFinancialStatementValue.__table__.columns}

    assert REQUIRED_CONTROLLED_VALUE_COLUMNS.issubset(columns)
    assert ControlledFinancialStatementValue.__tablename__ == "controlled_financial_statement_values"


def test_controlled_financial_statement_value_natural_key_sha256_is_unique() -> None:
    table = ControlledFinancialStatementValue.__table__
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    unique_indexes = {
        tuple(column.name for column in index.columns)
        for index in table.indexes
        if index.unique
    }

    assert ("natural_key_sha256",) in unique_columns | unique_indexes


def test_controlled_financial_statement_value_migration_creates_table() -> None:
    migration = ROOT / "backend" / "alembic" / "versions" / "202606170001_controlled_financial_statement_values.py"
    text = migration.read_text(encoding="utf-8")

    for expected in (
        "controlled_financial_statement_values",
        "natural_key_sha256",
        "row_checksum_sha256",
        "plan_checksum_sha256",
        "plan_rows_checksum_sha256",
        "uq_controlled_financial_statement_values_natural_key_sha256",
    ):
        assert expected in text


def test_controlled_financial_statement_value_checksum_helpers_are_deterministic() -> None:
    payload = _controlled_value_payload()

    first_natural = ControlledFinancialStatementValueService.build_natural_key_sha256(payload)
    second_natural = ControlledFinancialStatementValueService.build_natural_key_sha256(dict(payload))
    first_row = ControlledFinancialStatementValueService.build_row_checksum_sha256(payload)
    second_row = ControlledFinancialStatementValueService.build_row_checksum_sha256(dict(payload))

    assert first_natural == second_natural
    assert first_row == second_row
    assert first_natural == hashlib.sha256(
        ControlledFinancialStatementValueService.build_natural_key(payload).encode("utf-8")
    ).hexdigest()
    assert ControlledFinancialStatementValueService.validate_controlled_value_payload(payload) == []


def test_task177_schema_discovery_recommends_controlled_financial_statement_values(tmp_path: Path) -> None:
    chain = tmp_path / "chain"
    _write_task178_import_plan(chain)

    args = assistant.parse_args(
        [
            "--mode",
            "rzd-manual-official-pdf-controlled-values-db-import-target-schema-discovery",
            "--operator-resolution-chain-output-dir",
            str(chain),
        ]
    )
    report, exit_code = assistant.run_assistant(args)

    assert exit_code == 0
    assert report["status"] in {"warning", "passed"}
    assert report["schema_discovery_status"] in {"warning", "passed"}
    assert report["recommended_target_count"] == 1
    assert report["recommended_production_target_count"] == 1
    assert report["test_fixture_recommended_target_count"] == 0
    assert report["required_field_missing_count"] == 0
    assert report["missing_unique_key_field_count"] == 0
    assert report["mapping_row_count"] == report["input_import_plan_row_count"]
    assert report["mapped_import_plan_row_count"] == report["input_import_plan_row_count"]
    assert report["unmapped_import_plan_row_count"] == 0
    assert report["ready_for_controlled_import_apply"] is False
    assert report["ready_for_controlled_import"] is False
    assert report["database_mutated"] is False
    assert report["migration_executed"] is False
    recommended = [target for target in report["discovered_targets"] if target["is_recommended"]]
    assert recommended[0]["source_file"].replace("\\", "/") == "backend/app/models/controlled_financial_statement_value.py"
    assert recommended[0]["table_name"] == "controlled_financial_statement_values"
    assert recommended[0]["model_name"] == "ControlledFinancialStatementValue"


def _controlled_value_payload() -> dict[str, object]:
    return {
        "company_id": "18",
        "company_name": "RZD",
        "report_year": 2025,
        "report_standard": "IFRS",
        "target_type": "profit_or_loss",
        "metric_key": "total_revenue",
        "metric_role": "aggregate",
        "metric_name_ru": "Итого доходы",
        "metric_name_en": "Total revenue",
        "statement_page": 11,
        "page_number": 11,
        "value_2025": Decimal("100.00"),
        "value_2024": Decimal("90.00"),
        "raw_value_2025": "100",
        "raw_value_2024": "90",
        "raw_line": "Итого доходы 100 90",
        "note_reference": "",
        "source_pdf_sha256": "a" * 64,
        "plan_checksum_sha256": "b" * 64,
        "plan_rows_checksum_sha256": "c" * 64,
        "natural_key": "company_id=18|report_year=2025|report_standard=IFRS",
        "natural_key_sha256": "d" * 64,
        "row_checksum_sha256": "e" * 64,
    }


def _write_task178_import_plan(chain: Path) -> None:
    chain.mkdir(parents=True, exist_ok=True)
    payload = _controlled_value_payload()
    row = {
        **payload,
        "import_plan_row_id": "task178-row-1",
        "row_index": 1,
        "target_logical_entity": "financial_statement_value",
        "natural_key_sha256": ControlledFinancialStatementValueService.build_natural_key_sha256(payload),
        "row_checksum_sha256": ControlledFinancialStatementValueService.build_row_checksum_sha256(payload),
    }
    plan = {
        "mode": "rzd-manual-official-pdf-controlled-values-import-plan-preview",
        "status": "warning",
        "import_plan_preview_status": "warning",
        "ready_for_controlled_import_plan": True,
        "ready_for_controlled_import": False,
        "import_plan_preview_ready": True,
        "manual_review_gate_ready": True,
        "controlled_value_extraction_ready": True,
        "company_id": "18",
        "company_name": "RZD",
        "report_year": 2025,
        "report_standard": "IFRS",
        "planned_import_row_count": 1,
        "planned_aggregate_row_count": 1,
        "planned_component_row_count": 0,
        "bad_required_count": 0,
        "bad_safety_count": 0,
        "bad_import_plan_count": 0,
        "blocker_count": 0,
        "import_executed": False,
        "database_mutated": False,
        "safety_flags": {},
        "import_plan_rows": [row],
    }
    (chain / "rzd_manual_official_pdf_controlled_values_import_plan_preview_task175.json").write_text(
        json.dumps(plan, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    (chain / "rzd_manual_official_pdf_controlled_values_import_plan_preview_rows_task175.json").write_text(
        json.dumps(
            {
                "status": "warning",
                "row_count": 1,
                "planned_import_row_count": 1,
                "import_plan_rows": [row],
                "safe_hint": "No database import was executed.",
            },
            ensure_ascii=False,
            default=str,
        ),
        encoding="utf-8",
    )
