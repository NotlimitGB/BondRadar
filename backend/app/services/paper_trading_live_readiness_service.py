from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.schemas.ml_candidate_strategy_robustness import (
    MLCandidateStrategyRobustnessRequest,
    MLCandidateStrategyRobustnessResponse,
    MLCandidateStrategyRobustnessSelectedCandidate,
)
from app.schemas.paper_trading_live_readiness import (
    LivePaperReadinessGate,
    LivePaperReadinessRequest,
    LivePaperReadinessResponse,
    LivePaperReadinessWarning,
)
from app.services.ml_candidate_strategy_robustness_service import (
    MLCandidateStrategyRobustnessService,
)


class LivePaperReadinessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def check(
        self,
        request: LivePaperReadinessRequest,
    ) -> LivePaperReadinessResponse:
        self._validate_request(request)
        bridge_request = self._bridge_request(request)
        bridge_result = MLCandidateStrategyRobustnessService(self.db).analyze(
            bridge_request
        )
        warnings = [
            LivePaperReadinessWarning(
                message=warning.message,
                details=warning.details,
            )
            for warning in bridge_result.warnings
        ]
        gates = self._gates(request, bridge_result)
        return LivePaperReadinessResponse(
            readiness_status=self._readiness_status(gates),
            virtual_initial_capital=request.virtual_initial_capital,
            planned_duration_days=request.planned_duration_days,
            selected_candidate=bridge_result.selected_candidate,
            candidate_comparison=bridge_result.candidate_comparison,
            robustness_analysis=(
                bridge_result.robustness_analysis
                if request.include_robustness_analysis
                else None
            ),
            gates=gates,
            warnings=warnings,
        )

    @staticmethod
    def _bridge_request(
        request: LivePaperReadinessRequest,
    ) -> MLCandidateStrategyRobustnessRequest:
        payload = request.candidate_strategy_robustness.model_dump()
        if request.include_candidate_comparison is not None:
            payload["include_candidate_comparison"] = request.include_candidate_comparison
        return MLCandidateStrategyRobustnessRequest(**payload)

    def _gates(
        self,
        request: LivePaperReadinessRequest,
        bridge_result: MLCandidateStrategyRobustnessResponse,
    ) -> list[LivePaperReadinessGate]:
        selected = bridge_result.selected_candidate
        robustness = bridge_result.robustness_analysis
        variants = self._variants(robustness)
        top_variant = variants[0] if variants else None
        fail_flags = self._flags_by_level(variants, "fail")
        warning_flags = self._flags_by_level(variants, "warning")

        return [
            self._selected_candidate_available(selected),
            self._selected_candidate_ready(selected),
            self._robustness_analysis_available(robustness),
            self._analyzed_variant_count(request, robustness),
            self._completed_subperiods(request, top_variant),
            self._robustness_fail_flags(fail_flags),
            self._robustness_warning_flags(request, warning_flags),
            self._virtual_pilot_configuration(request),
        ]

    @staticmethod
    def _selected_candidate_available(
        selected: MLCandidateStrategyRobustnessSelectedCandidate | None,
    ) -> LivePaperReadinessGate:
        if selected is None:
            return LivePaperReadinessGate(
                code="selected_candidate_available",
                status="failed",
                message="Selected ML candidate is not available",
            )
        return LivePaperReadinessGate(
            code="selected_candidate_available",
            status="passed",
            message="Selected ML candidate is available",
            details={
                "name": selected.name,
                "model_run_id": selected.model_run_id,
                "model_run_ids": selected.model_run_ids,
            },
        )

    @staticmethod
    def _selected_candidate_ready(
        selected: MLCandidateStrategyRobustnessSelectedCandidate | None,
    ) -> LivePaperReadinessGate:
        if selected is None:
            return LivePaperReadinessGate(
                code="selected_candidate_ready",
                status="failed",
                message="Selected ML candidate readiness cannot be evaluated",
            )
        if selected.ready_for_strategy_research:
            return LivePaperReadinessGate(
                code="selected_candidate_ready",
                status="passed",
                message="Selected ML candidate is ready for strategy research",
            )
        return LivePaperReadinessGate(
            code="selected_candidate_ready",
            status="warning",
            message="Selected ML candidate is not marked ready for strategy research",
        )

    @staticmethod
    def _robustness_analysis_available(
        robustness: dict[str, Any] | None,
    ) -> LivePaperReadinessGate:
        if robustness is None:
            return LivePaperReadinessGate(
                code="robustness_analysis_available",
                status="failed",
                message="Strategy robustness analysis is not available",
            )
        return LivePaperReadinessGate(
            code="robustness_analysis_available",
            status="passed",
            message="Strategy robustness analysis is available",
        )

    @staticmethod
    def _analyzed_variant_count(
        request: LivePaperReadinessRequest,
        robustness: dict[str, Any] | None,
    ) -> LivePaperReadinessGate:
        analyzed_variant_count = (
            None if robustness is None else robustness.get("analyzed_variant_count")
        )
        details = {
            "analyzed_variant_count": analyzed_variant_count,
            "configured_minimum": request.minimum_analyzed_variant_count,
        }
        if (
            analyzed_variant_count is None
            or analyzed_variant_count < request.minimum_analyzed_variant_count
        ):
            return LivePaperReadinessGate(
                code="analyzed_variant_count",
                status="failed",
                message="Analyzed robustness variant count is below configured minimum",
                details=details,
            )
        return LivePaperReadinessGate(
            code="analyzed_variant_count",
            status="passed",
            message="Analyzed robustness variant count meets configured minimum",
            details=details,
        )

    @staticmethod
    def _completed_subperiods(
        request: LivePaperReadinessRequest,
        top_variant: dict[str, Any] | None,
    ) -> LivePaperReadinessGate:
        completed_subperiod_count = (
            None if top_variant is None else top_variant.get("completed_subperiod_count")
        )
        details = {
            "completed_subperiod_count": completed_subperiod_count,
            "configured_minimum": request.minimum_completed_subperiods,
        }
        if (
            completed_subperiod_count is None
            or completed_subperiod_count < request.minimum_completed_subperiods
        ):
            return LivePaperReadinessGate(
                code="completed_subperiods",
                status="failed",
                message="Completed robustness subperiod count is below configured minimum",
                details=details,
            )
        return LivePaperReadinessGate(
            code="completed_subperiods",
            status="passed",
            message="Completed robustness subperiod count meets configured minimum",
            details=details,
        )

    @staticmethod
    def _robustness_fail_flags(
        fail_flags: list[dict[str, Any]],
    ) -> LivePaperReadinessGate:
        details = {
            "fail_flag_count": len(fail_flags),
            "fail_flags": fail_flags,
        }
        if fail_flags:
            return LivePaperReadinessGate(
                code="robustness_fail_flags",
                status="failed",
                message="Strategy robustness has fail-level flags",
                details=details,
            )
        return LivePaperReadinessGate(
            code="robustness_fail_flags",
            status="passed",
            message="Strategy robustness has no fail-level flags",
            details=details,
        )

    @staticmethod
    def _robustness_warning_flags(
        request: LivePaperReadinessRequest,
        warning_flags: list[dict[str, Any]],
    ) -> LivePaperReadinessGate:
        details = {
            "warning_flag_count": len(warning_flags),
            "configured_maximum": request.maximum_warning_flag_count,
            "allow_warning_flags": request.allow_warning_flags,
            "warning_flags": warning_flags,
        }
        if not warning_flags:
            return LivePaperReadinessGate(
                code="robustness_warning_flags",
                status="passed",
                message="Strategy robustness has no warning-level flags",
                details=details,
            )
        if (
            request.maximum_warning_flag_count is not None
            and len(warning_flags) > request.maximum_warning_flag_count
        ):
            return LivePaperReadinessGate(
                code="robustness_warning_flags",
                status="failed",
                message="Strategy robustness warning flag count exceeds configured maximum",
                details=details,
            )
        if not request.allow_warning_flags:
            return LivePaperReadinessGate(
                code="robustness_warning_flags",
                status="failed",
                message="Strategy robustness warning flags are not allowed",
                details=details,
            )
        return LivePaperReadinessGate(
            code="robustness_warning_flags",
            status="warning",
            message="Strategy robustness has warning-level flags",
            details=details,
        )

    @staticmethod
    def _virtual_pilot_configuration(
        request: LivePaperReadinessRequest,
    ) -> LivePaperReadinessGate:
        return LivePaperReadinessGate(
            code="virtual_pilot_configuration",
            status="passed",
            message="Virtual pilot configuration is valid",
            details={
                "virtual_initial_capital": request.virtual_initial_capital,
                "planned_duration_days": request.planned_duration_days,
            },
        )

    @staticmethod
    def _variants(robustness: dict[str, Any] | None) -> list[dict[str, Any]]:
        if robustness is None:
            return []
        variants = robustness.get("variants") or []
        return [variant for variant in variants if isinstance(variant, dict)]

    @staticmethod
    def _flags_by_level(
        variants: list[dict[str, Any]],
        level: str,
    ) -> list[dict[str, Any]]:
        flags: list[dict[str, Any]] = []
        for variant in variants:
            for flag in variant.get("flags") or []:
                if isinstance(flag, dict) and flag.get("level") == level:
                    flags.append(flag)
        return flags

    @staticmethod
    def _readiness_status(gates: list[LivePaperReadinessGate]) -> str:
        if any(gate.status == "failed" for gate in gates):
            return "not_ready"
        if any(gate.status == "warning" for gate in gates):
            return "warning"
        return "ready"

    @staticmethod
    def _validate_request(request: LivePaperReadinessRequest) -> None:
        if request.virtual_initial_capital <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="virtual_initial_capital must be positive",
            )
        if request.planned_duration_days < 1 or request.planned_duration_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="planned_duration_days must be between 1 and 365",
            )
        if request.minimum_analyzed_variant_count < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_analyzed_variant_count must be positive",
            )
        if request.minimum_completed_subperiods < 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="minimum_completed_subperiods must be positive",
            )
        if (
            request.maximum_warning_flag_count is not None
            and request.maximum_warning_flag_count < 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="maximum_warning_flag_count must be non-negative",
            )
