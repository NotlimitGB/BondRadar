from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.company import Company
from app.schemas.corporate_universe_action_plan import (
    CorporateUniverseAction,
    CorporateUniverseActionPlanResponse,
    CorporateUniverseCommand,
    CorporateUniverseQualityCheck,
    CorporateUniverseSyncPayloadPreview,
)


class CorporateUniverseActionPlanService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def plan(
        self,
        *,
        board: str = "TQCB",
        minimum_corporate_bonds: int = 20,
        include_ofz: bool = False,
        active_only: bool = True,
        create_missing_companies: bool = True,
        rebuild_existing: bool = False,
        max_pages: int = 100,
        page_size: int = 100,
        sample_limit: int = 20,
    ) -> CorporateUniverseActionPlanResponse:
        clean_board = self._validate(
            board=board,
            minimum_corporate_bonds=minimum_corporate_bonds,
            max_pages=max_pages,
            page_size=page_size,
            sample_limit=sample_limit,
        )
        bonds = list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars())
        companies = list(self.db.execute(select(Company)).scalars())
        corporate_bonds = [bond for bond in bonds if not self._is_ofz_bond(bond)]
        ofz_bonds = [bond for bond in bonds if self._is_ofz_bond(bond)]
        working_bonds = bonds if include_ofz else corporate_bonds

        bonds_with_secid_count = sum(1 for bond in working_bonds if self._has_text(bond.secid))
        bonds_with_isin_count = sum(1 for bond in working_bonds if self._has_text(bond.isin))
        bonds_with_company_count = sum(1 for bond in working_bonds if bond.company_id is not None)

        sync_payload = CorporateUniverseSyncPayloadPreview(
            board=clean_board,
            active_only=active_only,
            create_missing_companies=create_missing_companies,
            rebuild_existing=rebuild_existing,
            max_pages=max_pages,
            page_size=page_size,
        ).model_dump()
        checks = self._checks(
            corporate_count=len(corporate_bonds),
            working_count=len(working_bonds),
            ofz_count=len(ofz_bonds),
            bonds_with_secid_count=bonds_with_secid_count,
            bonds_with_isin_count=bonds_with_isin_count,
            bonds_with_company_count=bonds_with_company_count,
            minimum_corporate_bonds=minimum_corporate_bonds,
            sync_payload=sync_payload,
        )
        status_value = self._response_status(checks)
        can_sync_universe = status_value != "blocked"
        can_continue_to_data_pipeline = len(corporate_bonds) >= minimum_corporate_bonds

        return CorporateUniverseActionPlanResponse(
            status=status_value,
            as_of=datetime.now(timezone.utc),
            board=clean_board,
            include_ofz=include_ofz,
            local_total_bond_count=len(bonds),
            local_corporate_bond_count=len(corporate_bonds),
            local_ofz_bond_count=len(ofz_bonds),
            local_working_bond_count=len(working_bonds),
            local_company_count=len(companies),
            bonds_with_secid_count=bonds_with_secid_count,
            bonds_with_isin_count=bonds_with_isin_count,
            bonds_with_company_count=bonds_with_company_count,
            sample_corporate_bonds=self._sample(corporate_bonds, sample_limit),
            sample_ofz_bonds=self._sample(ofz_bonds, sample_limit),
            checks=checks,
            actions=self._actions(
                status_value=status_value,
                can_continue_to_data_pipeline=can_continue_to_data_pipeline,
                corporate_count=len(corporate_bonds),
                minimum_corporate_bonds=minimum_corporate_bonds,
            ),
            commands=self._commands(
                sync_payload=sync_payload,
                action_plan_query=self._action_plan_query(
                    board=clean_board,
                    minimum_corporate_bonds=minimum_corporate_bonds,
                    include_ofz=include_ofz,
                    active_only=active_only,
                    create_missing_companies=create_missing_companies,
                    rebuild_existing=rebuild_existing,
                    max_pages=max_pages,
                    page_size=page_size,
                    sample_limit=sample_limit,
                ),
                minimum_corporate_bonds=minimum_corporate_bonds,
                can_continue_to_data_pipeline=can_continue_to_data_pipeline,
            ),
            sync_payload=sync_payload,
            curl_example=self._curl_example(sync_payload),
            can_sync_universe=can_sync_universe,
            can_continue_to_data_pipeline=can_continue_to_data_pipeline,
            warnings=self._warnings(checks),
            errors=[] if status_value != "blocked" else self._errors(checks),
            next_steps=self._next_steps(
                status_value=status_value,
                can_continue_to_data_pipeline=can_continue_to_data_pipeline,
            ),
        )

    @staticmethod
    def _validate(
        *,
        board: str,
        minimum_corporate_bonds: int,
        max_pages: int,
        page_size: int,
        sample_limit: int,
    ) -> str:
        clean_board = board.strip().upper()
        if not clean_board:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="board must not be blank",
            )
        if minimum_corporate_bonds < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_corporate_bonds must be non-negative",
            )
        if max_pages < 1 or max_pages > 1000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_pages must be between 1 and 1000",
            )
        if page_size < 1 or page_size > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="page_size must be between 1 and 500",
            )
        if sample_limit < 1 or sample_limit > 100:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="sample_limit must be between 1 and 100",
            )
        return clean_board

    @staticmethod
    def _is_ofz_bond(bond: Bond) -> bool:
        fields = " ".join(
            value
            for value in [bond.name, bond.secid or "", bond.isin or ""]
            if value
        ).upper()
        isin = (bond.isin or "").upper()
        return (
            "ОФЗ" in fields
            or "OFZ" in fields
            or "FEDERAL LOAN BOND" in fields
            or isin.startswith("SU")
        )

    @staticmethod
    def _has_text(value: str | None) -> bool:
        return value is not None and bool(value.strip())

    @staticmethod
    def _checks(
        *,
        corporate_count: int,
        working_count: int,
        ofz_count: int,
        bonds_with_secid_count: int,
        bonds_with_isin_count: int,
        bonds_with_company_count: int,
        minimum_corporate_bonds: int,
        sync_payload: dict[str, Any],
    ) -> list[CorporateUniverseQualityCheck]:
        return [
            CorporateUniverseActionPlanService._size_check(
                "local_corporate_universe_size",
                corporate_count,
                minimum_corporate_bonds,
                "Local corporate bond universe",
            ),
            CorporateUniverseActionPlanService._size_check(
                "local_working_universe_size",
                working_count,
                minimum_corporate_bonds,
                "Local working bond universe",
            ),
            CorporateUniverseActionPlanService._coverage_check(
                "bond_secid_coverage",
                bonds_with_secid_count,
                working_count,
                "Bond secid coverage",
            ),
            CorporateUniverseActionPlanService._coverage_check(
                "bond_isin_coverage",
                bonds_with_isin_count,
                working_count,
                "Bond isin coverage",
            ),
            CorporateUniverseActionPlanService._coverage_check(
                "bond_company_coverage",
                bonds_with_company_count,
                working_count,
                "Bond company coverage",
            ),
            CorporateUniverseQualityCheck(
                name="ofz_separation_available",
                status="passed",
                message="Corporate and OFZ counts are available",
                details={"local_ofz_bond_count": ofz_count},
            ),
            CorporateUniverseQualityCheck(
                name="sync_payload_ready",
                status="passed",
                message="MOEX universe sync payload is ready",
                details={"sync_payload": sync_payload},
            ),
            CorporateUniverseQualityCheck(
                name="pipeline_can_follow_after_sync",
                status=(
                    "passed"
                    if corporate_count >= minimum_corporate_bonds
                    else "warning"
                ),
                message=(
                    "Live data pipeline can follow the current universe"
                    if corporate_count >= minimum_corporate_bonds
                    else "Live data pipeline should wait for universe sync"
                ),
                details={
                    "local_corporate_bond_count": corporate_count,
                    "configured_minimum": minimum_corporate_bonds,
                },
            ),
        ]

    @staticmethod
    def _size_check(
        name: str,
        count: int,
        configured_minimum: int,
        label: str,
    ) -> CorporateUniverseQualityCheck:
        details = {"count": count, "configured_minimum": configured_minimum}
        if count >= configured_minimum:
            return CorporateUniverseQualityCheck(
                name=name,
                status="passed",
                message=f"{label} meets configured minimum",
                details=details,
            )
        return CorporateUniverseQualityCheck(
            name=name,
            status="warning",
            message=f"{label} is below configured minimum",
            details=details,
        )

    @staticmethod
    def _coverage_check(
        name: str,
        covered_count: int,
        working_count: int,
        label: str,
    ) -> CorporateUniverseQualityCheck:
        details = {"covered_count": covered_count, "working_bond_count": working_count}
        if working_count == covered_count:
            return CorporateUniverseQualityCheck(
                name=name,
                status="passed",
                message=f"{label} is complete",
                details=details,
            )
        return CorporateUniverseQualityCheck(
            name=name,
            status="warning",
            message=f"{label} is incomplete",
            details=details,
        )

    @staticmethod
    def _response_status(checks: list[CorporateUniverseQualityCheck]) -> str:
        statuses = {check.status for check in checks}
        if "failed" in statuses:
            return "blocked"
        if "warning" in statuses:
            return "needs_sync"
        return "ready"

    @staticmethod
    def _sample(bonds: list[Bond], sample_limit: int) -> list[dict[str, Any]]:
        return [
            jsonable_encoder(
                {
                    "id": bond.id,
                    "secid": bond.secid,
                    "isin": bond.isin,
                    "name": bond.name,
                    "company_id": bond.company_id,
                    "currency": bond.currency,
                    "maturity_date": bond.maturity_date,
                    "yield_to_maturity": bond.yield_to_maturity,
                    "liquidity_score": bond.liquidity_score,
                }
            )
            for bond in bonds[:sample_limit]
        ]

    @staticmethod
    def _actions(
        *,
        status_value: str,
        can_continue_to_data_pipeline: bool,
        corporate_count: int,
        minimum_corporate_bonds: int,
    ) -> list[CorporateUniverseAction]:
        sync_status = "optional" if status_value == "ready" else "recommended"
        return [
            CorporateUniverseAction(
                name="sync_moex_corporate_universe",
                status=sync_status,
                reason=(
                    "MOEX universe sync can refresh the local corporate bond universe"
                    if sync_status == "optional"
                    else "MOEX universe sync should expand or refresh the local bond universe"
                ),
                details={
                    "local_corporate_bond_count": corporate_count,
                    "configured_minimum": minimum_corporate_bonds,
                },
            ),
            CorporateUniverseAction(
                name="review_ofz_separation",
                status="optional",
                reason="Review corporate and OFZ split before live data pipeline work",
                details={},
            ),
            CorporateUniverseAction(
                name="recheck_corporate_universe",
                status="recommended",
                reason="Re-check the corporate universe action plan after sync",
                details={},
            ),
            CorporateUniverseAction(
                name="run_live_data_action_plan",
                status=(
                    "recommended" if can_continue_to_data_pipeline else "blocked"
                ),
                reason=(
                    "Live data action plan can be reviewed next"
                    if can_continue_to_data_pipeline
                    else "Live data action plan should wait for enough corporate bonds"
                ),
                details={
                    "local_corporate_bond_count": corporate_count,
                    "configured_minimum": minimum_corporate_bonds,
                },
            ),
        ]

    @staticmethod
    def _commands(
        *,
        sync_payload: dict[str, Any],
        action_plan_query: str,
        minimum_corporate_bonds: int,
        can_continue_to_data_pipeline: bool,
    ) -> list[CorporateUniverseCommand]:
        commands = [
            CorporateUniverseCommand(
                label="Run MOEX universe sync",
                method="POST",
                path="/api/market-data/moex/bonds/sync",
                body=sync_payload,
                description="Run the MOEX corporate bond universe sync manually.",
            ),
            CorporateUniverseCommand(
                label="Re-check corporate universe action plan",
                method="GET",
                path=action_plan_query,
                description="Inspect local universe counts and metadata coverage again.",
            ),
            CorporateUniverseCommand(
                label="Check live data readiness",
                method="GET",
                path=(
                    "/api/data-readiness/live"
                    f"?minimum_corporate_bonds={minimum_corporate_bonds}"
                ),
                description="Review live data readiness after universe sync.",
            ),
        ]
        if can_continue_to_data_pipeline:
            commands.append(
                CorporateUniverseCommand(
                    label="Open live data action plan",
                    method="GET",
                    path=(
                        "/api/data-readiness/live/action-plan"
                        f"?minimum_corporate_bonds={minimum_corporate_bonds}"
                    ),
                    description="Plan the next data pipeline run after universe validation.",
                )
            )
        return commands

    @staticmethod
    def _action_plan_query(
        *,
        board: str,
        minimum_corporate_bonds: int,
        include_ofz: bool,
        active_only: bool,
        create_missing_companies: bool,
        rebuild_existing: bool,
        max_pages: int,
        page_size: int,
        sample_limit: int,
    ) -> str:
        params = {
            "board": board,
            "minimum_corporate_bonds": minimum_corporate_bonds,
            "include_ofz": str(include_ofz).lower(),
            "active_only": str(active_only).lower(),
            "create_missing_companies": str(create_missing_companies).lower(),
            "rebuild_existing": str(rebuild_existing).lower(),
            "max_pages": max_pages,
            "page_size": page_size,
            "sample_limit": sample_limit,
        }
        query = "&".join(f"{key}={value}" for key, value in params.items())
        return f"/api/data-readiness/corporate-universe/action-plan?{query}"

    @staticmethod
    def _curl_example(sync_payload: dict[str, Any]) -> str:
        payload_json = json.dumps(
            jsonable_encoder(sync_payload),
            ensure_ascii=False,
            indent=2,
        )
        return (
            'curl -s -X POST "http://127.0.0.1:8000/api/market-data/moex/bonds/sync" \\\n'
            '  -H "Content-Type: application/json" \\\n'
            f"  -d '{payload_json}'"
        )

    @staticmethod
    def _warnings(checks: list[CorporateUniverseQualityCheck]) -> list[dict[str, Any]]:
        return [
            {
                "code": check.name,
                "message": check.message,
                "details": check.details,
            }
            for check in checks
            if check.status == "warning"
        ]

    @staticmethod
    def _errors(checks: list[CorporateUniverseQualityCheck]) -> list[dict[str, Any]]:
        return [
            {
                "code": check.name,
                "message": check.message,
                "details": check.details,
            }
            for check in checks
            if check.status == "failed"
        ]

    @staticmethod
    def _next_steps(
        *,
        status_value: str,
        can_continue_to_data_pipeline: bool,
    ) -> list[str]:
        if status_value == "ready":
            return [
                "Optionally refresh MOEX corporate bond universe.",
                "Open live data action plan before pipeline execution.",
            ]
        steps = [
            "Run MOEX corporate bond universe sync.",
            "Re-check corporate universe action plan after sync.",
        ]
        if can_continue_to_data_pipeline:
            steps.append("Open live data action plan before pipeline execution.")
        else:
            steps.append("Wait for enough corporate bonds before data pipeline execution.")
        return steps
