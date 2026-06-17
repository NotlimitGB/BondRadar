from __future__ import annotations

import hashlib
import json
from typing import Any


CONTROLLED_VALUE_NATURAL_KEY_FIELDS = (
    "company_id",
    "report_year",
    "report_standard",
    "target_type",
    "metric_key",
    "metric_role",
    "statement_page",
)


class ControlledFinancialStatementValueService:
    @staticmethod
    def build_natural_key(payload: dict[str, Any]) -> str:
        return "|".join(
            f"{field}={payload.get(field, '')}"
            for field in CONTROLLED_VALUE_NATURAL_KEY_FIELDS
        )

    @staticmethod
    def build_natural_key_sha256(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            ControlledFinancialStatementValueService.build_natural_key(payload).encode(
                "utf-8"
            )
        ).hexdigest()

    @staticmethod
    def build_row_checksum_sha256(payload: dict[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def validate_controlled_value_payload(payload: dict[str, Any]) -> list[str]:
        required = (
            *CONTROLLED_VALUE_NATURAL_KEY_FIELDS,
            "company_name",
            "metric_name_ru",
            "metric_name_en",
            "page_number",
            "value_2025",
            "value_2024",
            "raw_value_2025",
            "raw_value_2024",
            "raw_line",
            "source_pdf_sha256",
            "plan_checksum_sha256",
            "plan_rows_checksum_sha256",
            "natural_key",
            "natural_key_sha256",
            "row_checksum_sha256",
        )
        return [field for field in required if payload.get(field) in (None, "")]
