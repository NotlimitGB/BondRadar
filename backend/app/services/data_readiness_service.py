from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.bond_risk_assessment import BondRiskAssessment
from app.models.company import Company
from app.models.company_credit_health_snapshot import CompanyCreditHealthSnapshot
from app.models.financial_report import FinancialReport
from app.schemas.data_readiness import (
    READINESS_RETURN_METHODS,
    DataReadinessBondIssue,
    DataReadinessCheckRequest,
    DataReadinessClassDistribution,
    DataReadinessCoverage,
    DataReadinessGate,
    DataReadinessResponse,
    DataReadinessSummary,
)


EVALUABLE_LABELS = {"positive_return", "negative_return"}
RETURN_METHODS_WITH_CASHFLOWS = {"total_return", "risk_adjusted"}


class DataReadinessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def check(self, request: DataReadinessCheckRequest) -> DataReadinessResponse:
        self._validate(request)
        bonds = self._selected_bonds(request)
        bond_ids = [bond.id for bond in bonds]
        companies = self._selected_companies(request, bonds)
        company_ids = [company.id for company in companies]

        coverage = self._coverage_sets(request, bond_ids, company_ids)
        counts = self._counts(request, bond_ids, company_ids)
        label_counts = self._label_counts(request, bond_ids)
        label_row_count = counts["label_row_count"]
        insufficient_ratio = (
            Decimal(label_counts["insufficient_label_count"]) / Decimal(label_row_count)
            if label_row_count
            else Decimal("0")
        )

        summary = self._summary(
            request,
            bonds=bonds,
            companies=companies,
            counts=counts,
            coverage=coverage,
            label_counts=label_counts,
            insufficient_ratio=insufficient_ratio,
            ready_for_ml_training=False,
        )
        gates = self._gates(request, summary, coverage)
        ready_for_ml_training = self._ready_for_ml_training(request, gates, summary)
        summary.ready_for_ml_training = ready_for_ml_training
        response_status = self._response_status(gates)
        return DataReadinessResponse(
            status=response_status,
            summary=summary,
            gates=gates,
            bond_issues=self._bond_issues(
                request,
                bonds=bonds,
                coverage=coverage,
            ),
            warnings=[gate.message for gate in gates if gate.status == "warning"],
            recommended_next_actions=self._recommended_next_actions(gates),
        )

    def _validate(self, request: DataReadinessCheckRequest) -> None:
        if request.date_from > request.date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )
        if request.return_method not in READINESS_RETURN_METHODS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid return method",
            )
        if request.min_rows <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_rows must be positive",
            )
        if request.min_positive_rows < 0 or request.min_negative_rows < 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="class minimums must be non-negative",
            )
        if request.max_insufficient_ratio < 0 or request.max_insufficient_ratio > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_insufficient_ratio must be between 0 and 1",
            )
        if request.max_bond_issues < 1 or request.max_bond_issues > 500:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="max_bond_issues must be between 1 and 500",
            )

    def _selected_bonds(self, request: DataReadinessCheckRequest) -> list[Bond]:
        stmt = select(Bond)
        if request.bond_ids:
            stmt = stmt.where(Bond.id.in_(set(request.bond_ids)))
        elif request.company_ids:
            stmt = stmt.where(Bond.company_id.in_(set(request.company_ids)))
        stmt = stmt.order_by(Bond.id.asc())
        return list(self.db.execute(stmt).scalars())

    def _selected_companies(
        self,
        request: DataReadinessCheckRequest,
        bonds: list[Bond],
    ) -> list[Company]:
        if request.company_ids:
            stmt = select(Company).where(Company.id.in_(set(request.company_ids)))
        else:
            company_ids = {bond.company_id for bond in bonds}
            if not company_ids:
                return []
            stmt = select(Company).where(Company.id.in_(company_ids))
        stmt = stmt.order_by(Company.id.asc())
        return list(self.db.execute(stmt).scalars())

    def _coverage_sets(
        self,
        request: DataReadinessCheckRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> dict[str, set[int]]:
        cashflow_to = request.date_to + timedelta(days=request.horizon_days)
        return {
            "market_bond_ids": self._distinct_set(
                BondMarketSnapshot.bond_id,
                bond_ids,
                BondMarketSnapshot.bond_id.in_(bond_ids),
                BondMarketSnapshot.trade_date >= request.date_from,
                BondMarketSnapshot.trade_date <= request.date_to,
            ),
            "cashflow_bond_ids": self._distinct_set(
                BondCashflowEvent.bond_id,
                bond_ids,
                BondCashflowEvent.bond_id.in_(bond_ids),
                BondCashflowEvent.event_date >= request.date_from,
                BondCashflowEvent.event_date <= cashflow_to,
            ),
            "financial_report_company_ids": self._distinct_set(
                FinancialReport.company_id,
                company_ids,
                FinancialReport.company_id.in_(company_ids),
                self._available_report_condition(request),
            ),
            "credit_health_company_ids": self._distinct_set(
                CompanyCreditHealthSnapshot.company_id,
                company_ids,
                CompanyCreditHealthSnapshot.company_id.in_(company_ids),
                CompanyCreditHealthSnapshot.as_of_date <= request.date_to,
            ),
            "risk_bond_ids": self._distinct_set(
                BondRiskAssessment.bond_id,
                bond_ids,
                BondRiskAssessment.bond_id.in_(bond_ids),
                BondRiskAssessment.as_of_date <= request.date_to,
            ),
            "feature_bond_ids": self._distinct_set(
                BondFeatureSnapshot.bond_id,
                bond_ids,
                BondFeatureSnapshot.bond_id.in_(bond_ids),
                BondFeatureSnapshot.as_of_date >= request.date_from,
                BondFeatureSnapshot.as_of_date <= request.date_to,
            ),
            "label_bond_ids": self._distinct_set(
                BondReturnLabel.bond_id,
                bond_ids,
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.as_of_date >= request.date_from,
                BondReturnLabel.as_of_date <= request.date_to,
                BondReturnLabel.horizon_days == request.horizon_days,
                BondReturnLabel.return_method == request.return_method,
            ),
            "only_insufficient_label_bond_ids": self._only_insufficient_label_bond_ids(
                request,
                bond_ids,
            ),
        }

    def _counts(
        self,
        request: DataReadinessCheckRequest,
        bond_ids: list[int],
        company_ids: list[int],
    ) -> dict[str, int]:
        cashflow_to = request.date_to + timedelta(days=request.horizon_days)
        return {
            "market_snapshot_count": self._count(
                BondMarketSnapshot,
                bond_ids,
                BondMarketSnapshot.bond_id.in_(bond_ids),
                BondMarketSnapshot.trade_date >= request.date_from,
                BondMarketSnapshot.trade_date <= request.date_to,
            ),
            "cashflow_event_count": self._count(
                BondCashflowEvent,
                bond_ids,
                BondCashflowEvent.bond_id.in_(bond_ids),
                BondCashflowEvent.event_date >= request.date_from,
                BondCashflowEvent.event_date <= cashflow_to,
            ),
            "financial_report_count": self._count(
                FinancialReport,
                company_ids,
                FinancialReport.company_id.in_(company_ids),
                self._available_report_condition(request),
            ),
            "credit_health_snapshot_count": self._count(
                CompanyCreditHealthSnapshot,
                company_ids,
                CompanyCreditHealthSnapshot.company_id.in_(company_ids),
                CompanyCreditHealthSnapshot.as_of_date <= request.date_to,
            ),
            "bond_risk_assessment_count": self._count(
                BondRiskAssessment,
                bond_ids,
                BondRiskAssessment.bond_id.in_(bond_ids),
                BondRiskAssessment.as_of_date <= request.date_to,
            ),
            "feature_row_count": self._count(
                BondFeatureSnapshot,
                bond_ids,
                BondFeatureSnapshot.bond_id.in_(bond_ids),
                BondFeatureSnapshot.as_of_date >= request.date_from,
                BondFeatureSnapshot.as_of_date <= request.date_to,
            ),
            "label_row_count": self._count(
                BondReturnLabel,
                bond_ids,
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.as_of_date >= request.date_from,
                BondReturnLabel.as_of_date <= request.date_to,
                BondReturnLabel.horizon_days == request.horizon_days,
                BondReturnLabel.return_method == request.return_method,
            ),
            "joined_feature_label_row_count": self._joined_feature_label_count(
                request,
                bond_ids,
            ),
        }

    def _label_counts(
        self,
        request: DataReadinessCheckRequest,
        bond_ids: list[int],
    ) -> dict[str, int]:
        if not bond_ids:
            return {
                "evaluable_label_count": 0,
                "positive_label_count": 0,
                "negative_label_count": 0,
                "insufficient_label_count": 0,
            }
        positive = self._count(
            BondReturnLabel,
            bond_ids,
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.as_of_date >= request.date_from,
            BondReturnLabel.as_of_date <= request.date_to,
            BondReturnLabel.horizon_days == request.horizon_days,
            BondReturnLabel.return_method == request.return_method,
            BondReturnLabel.label == "positive_return",
            BondReturnLabel.label_binary.is_not(None),
        )
        negative = self._count(
            BondReturnLabel,
            bond_ids,
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.as_of_date >= request.date_from,
            BondReturnLabel.as_of_date <= request.date_to,
            BondReturnLabel.horizon_days == request.horizon_days,
            BondReturnLabel.return_method == request.return_method,
            BondReturnLabel.label == "negative_return",
            BondReturnLabel.label_binary.is_not(None),
        )
        insufficient = self._count(
            BondReturnLabel,
            bond_ids,
            BondReturnLabel.bond_id.in_(bond_ids),
            BondReturnLabel.as_of_date >= request.date_from,
            BondReturnLabel.as_of_date <= request.date_to,
            BondReturnLabel.horizon_days == request.horizon_days,
            BondReturnLabel.return_method == request.return_method,
            BondReturnLabel.label == "insufficient_data",
        )
        return {
            "evaluable_label_count": positive + negative,
            "positive_label_count": positive,
            "negative_label_count": negative,
            "insufficient_label_count": insufficient,
        }

    def _summary(
        self,
        request: DataReadinessCheckRequest,
        *,
        bonds: list[Bond],
        companies: list[Company],
        counts: dict[str, int],
        coverage: dict[str, set[int]],
        label_counts: dict[str, int],
        insufficient_ratio: Decimal,
        ready_for_ml_training: bool,
    ) -> DataReadinessSummary:
        bonds_with_secid = sum(1 for bond in bonds if bond.secid)
        class_distribution = DataReadinessClassDistribution(**label_counts)
        coverage_payload = DataReadinessCoverage(
            bonds_with_market_snapshots_count=len(coverage["market_bond_ids"]),
            bonds_with_cashflows_count=len(coverage["cashflow_bond_ids"]),
            companies_with_financial_reports_count=len(
                coverage["financial_report_company_ids"]
            ),
            companies_with_credit_health_count=len(
                coverage["credit_health_company_ids"]
            ),
            bonds_with_risk_assessments_count=len(coverage["risk_bond_ids"]),
            bonds_with_features_count=len(coverage["feature_bond_ids"]),
            bonds_with_labels_count=len(coverage["label_bond_ids"]),
        )
        return DataReadinessSummary(
            date_from=request.date_from,
            date_to=request.date_to,
            horizon_days=request.horizon_days,
            return_method=request.return_method,
            selected_bonds_count=len(bonds),
            selected_companies_count=len(companies),
            bonds_with_secid_count=bonds_with_secid,
            bonds_without_secid_count=len(bonds) - bonds_with_secid,
            market_snapshot_count=counts["market_snapshot_count"],
            cashflow_event_count=counts["cashflow_event_count"],
            financial_report_count=counts["financial_report_count"],
            credit_health_snapshot_count=counts["credit_health_snapshot_count"],
            bond_risk_assessment_count=counts["bond_risk_assessment_count"],
            feature_row_count=counts["feature_row_count"],
            label_row_count=counts["label_row_count"],
            joined_feature_label_row_count=counts["joined_feature_label_row_count"],
            evaluable_label_count=label_counts["evaluable_label_count"],
            positive_label_count=label_counts["positive_label_count"],
            negative_label_count=label_counts["negative_label_count"],
            insufficient_label_count=label_counts["insufficient_label_count"],
            insufficient_ratio=insufficient_ratio,
            ready_for_ml_training=ready_for_ml_training,
            class_distribution=class_distribution,
            coverage=coverage_payload,
        )

    def _gates(
        self,
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
        coverage: dict[str, set[int]],
    ) -> list[DataReadinessGate]:
        gates = [
            self._selected_bonds_gate(summary),
            self._secid_gate(request, summary),
            self._market_snapshot_gate(summary),
            self._cashflow_gate(request, summary),
            self._financial_report_gate(request, summary, coverage),
            self._credit_health_gate(request, summary, coverage),
            self._bond_risk_gate(request, summary, coverage),
            self._feature_rows_gate(summary),
            self._label_rows_gate(summary),
            self._evaluable_rows_gate(request, summary),
            self._class_balance_gate(request, summary),
            self._insufficient_ratio_gate(request, summary),
        ]
        return gates

    @staticmethod
    def _selected_bonds_gate(summary: DataReadinessSummary) -> DataReadinessGate:
        if summary.selected_bonds_count == 0:
            return DataReadinessService._gate(
                "selected_bonds",
                "fail",
                "No bonds selected for readiness check",
                {"selected_bonds_count": 0},
            )
        return DataReadinessService._gate(
            "selected_bonds",
            "pass",
            "Selected bonds are available",
            {"selected_bonds_count": summary.selected_bonds_count},
        )

    @staticmethod
    def _secid_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
    ) -> DataReadinessGate:
        details = {
            "bonds_with_secid_count": summary.bonds_with_secid_count,
            "bonds_without_secid_count": summary.bonds_without_secid_count,
            "required": request.require_moex_secid,
        }
        if summary.selected_bonds_count == 0:
            return DataReadinessService._gate(
                "moex_secid_coverage",
                "fail",
                "No selected bonds can be checked for secid coverage",
                details,
            )
        if summary.bonds_without_secid_count == 0:
            return DataReadinessService._gate(
                "moex_secid_coverage",
                "pass",
                "MOEX secid coverage is complete",
                details,
            )
        if request.require_moex_secid and summary.bonds_with_secid_count == 0:
            return DataReadinessService._gate(
                "moex_secid_coverage",
                "fail",
                "All selected bonds are missing MOEX secid",
                details,
            )
        return DataReadinessService._gate(
            "moex_secid_coverage",
            "warning",
            "Some selected bonds are missing MOEX secid",
            details,
        )

    @staticmethod
    def _market_snapshot_gate(summary: DataReadinessSummary) -> DataReadinessGate:
        details = {
            "market_snapshot_count": summary.market_snapshot_count,
            "bonds_with_market_snapshots_count": (
                summary.coverage.bonds_with_market_snapshots_count
            ),
            "selected_bonds_count": summary.selected_bonds_count,
        }
        if summary.market_snapshot_count == 0:
            return DataReadinessService._gate(
                "market_snapshot_coverage",
                "fail",
                "No market snapshots found in selected period",
                details,
            )
        if (
            summary.selected_bonds_count > 0
            and summary.coverage.bonds_with_market_snapshots_count
            < summary.selected_bonds_count / 2
        ):
            return DataReadinessService._gate(
                "market_snapshot_coverage",
                "warning",
                "Market snapshot coverage is below half of selected bonds",
                details,
            )
        return DataReadinessService._gate(
            "market_snapshot_coverage",
            "pass",
            "Market snapshot coverage is sufficient",
            details,
        )

    @staticmethod
    def _cashflow_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
    ) -> DataReadinessGate:
        details = {
            "cashflow_event_count": summary.cashflow_event_count,
            "bonds_with_cashflows_count": summary.coverage.bonds_with_cashflows_count,
            "return_method": request.return_method,
            "required": request.require_cashflows,
        }
        if summary.cashflow_event_count > 0:
            return DataReadinessService._gate(
                "cashflow_coverage",
                "pass",
                "Cashflow event coverage is available",
                details,
            )
        if request.return_method in RETURN_METHODS_WITH_CASHFLOWS:
            gate_status = "fail" if request.require_cashflows else "warning"
            return DataReadinessService._gate(
                "cashflow_coverage",
                gate_status,
                "Cashflow events are missing for selected period",
                details,
            )
        if request.require_cashflows:
            return DataReadinessService._gate(
                "cashflow_coverage",
                "warning",
                "Cashflow events are missing, but selected return method can proceed",
                details,
            )
        return DataReadinessService._gate(
            "cashflow_coverage",
            "pass",
            "Cashflow events are optional for selected return method",
            details,
        )

    @staticmethod
    def _financial_report_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
        coverage: dict[str, set[int]],
    ) -> DataReadinessGate:
        details = {
            "financial_report_count": summary.financial_report_count,
            "companies_with_financial_reports_count": len(
                coverage["financial_report_company_ids"]
            ),
            "selected_companies_count": summary.selected_companies_count,
            "required": request.require_financial_reports,
        }
        if summary.financial_report_count == 0:
            return DataReadinessService._gate(
                "financial_report_coverage",
                "fail" if request.require_financial_reports else "warning",
                "No available financial reports found for selected companies",
                details,
            )
        if len(coverage["financial_report_company_ids"]) < summary.selected_companies_count:
            return DataReadinessService._gate(
                "financial_report_coverage",
                "warning",
                "Financial report coverage is incomplete",
                details,
            )
        return DataReadinessService._gate(
            "financial_report_coverage",
            "pass",
            "Financial report coverage is available",
            details,
        )

    @staticmethod
    def _credit_health_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
        coverage: dict[str, set[int]],
    ) -> DataReadinessGate:
        details = {
            "credit_health_snapshot_count": summary.credit_health_snapshot_count,
            "companies_with_credit_health_count": len(
                coverage["credit_health_company_ids"]
            ),
            "selected_companies_count": summary.selected_companies_count,
            "required": request.require_credit_risk,
        }
        if summary.credit_health_snapshot_count == 0:
            return DataReadinessService._gate(
                "credit_health_coverage",
                "fail" if request.require_credit_risk else "warning",
                "No company credit health snapshots found",
                details,
            )
        if len(coverage["credit_health_company_ids"]) < summary.selected_companies_count:
            return DataReadinessService._gate(
                "credit_health_coverage",
                "warning",
                "Company credit health coverage is incomplete",
                details,
            )
        return DataReadinessService._gate(
            "credit_health_coverage",
            "pass",
            "Company credit health coverage is available",
            details,
        )

    @staticmethod
    def _bond_risk_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
        coverage: dict[str, set[int]],
    ) -> DataReadinessGate:
        details = {
            "bond_risk_assessment_count": summary.bond_risk_assessment_count,
            "bonds_with_risk_assessments_count": len(coverage["risk_bond_ids"]),
            "selected_bonds_count": summary.selected_bonds_count,
            "required": request.require_credit_risk,
        }
        if summary.bond_risk_assessment_count == 0:
            return DataReadinessService._gate(
                "bond_risk_assessment_coverage",
                "fail" if request.require_credit_risk else "warning",
                "No bond risk assessments found",
                details,
            )
        if len(coverage["risk_bond_ids"]) < summary.selected_bonds_count:
            return DataReadinessService._gate(
                "bond_risk_assessment_coverage",
                "warning",
                "Bond risk assessment coverage is incomplete",
                details,
            )
        return DataReadinessService._gate(
            "bond_risk_assessment_coverage",
            "pass",
            "Bond risk assessment coverage is available",
            details,
        )

    @staticmethod
    def _feature_rows_gate(summary: DataReadinessSummary) -> DataReadinessGate:
        details = {
            "feature_row_count": summary.feature_row_count,
            "market_snapshot_count": summary.market_snapshot_count,
            "bonds_with_features_count": summary.coverage.bonds_with_features_count,
        }
        if summary.feature_row_count == 0:
            return DataReadinessService._gate(
                "feature_rows",
                "fail",
                "No feature snapshots found in selected period",
                details,
            )
        if (
            summary.market_snapshot_count > 0
            and summary.feature_row_count < summary.market_snapshot_count / 4
        ):
            return DataReadinessService._gate(
                "feature_rows",
                "warning",
                "Feature rows are much lower than market snapshot rows",
                details,
            )
        return DataReadinessService._gate(
            "feature_rows",
            "pass",
            "Feature rows are available",
            details,
        )

    @staticmethod
    def _label_rows_gate(summary: DataReadinessSummary) -> DataReadinessGate:
        details = {
            "label_row_count": summary.label_row_count,
            "joined_feature_label_row_count": summary.joined_feature_label_row_count,
            "return_method": summary.return_method,
        }
        if summary.label_row_count == 0:
            return DataReadinessService._gate(
                "label_rows",
                "fail",
                "No labels found for selected return method",
                details,
            )
        return DataReadinessService._gate(
            "label_rows",
            "pass",
            "Labels are available for selected return method",
            details,
        )

    @staticmethod
    def _evaluable_rows_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
    ) -> DataReadinessGate:
        details = {
            "evaluable_label_count": summary.evaluable_label_count,
            "required_min": request.min_rows,
        }
        if summary.evaluable_label_count < request.min_rows:
            return DataReadinessService._gate(
                "evaluable_rows",
                "fail",
                "Not enough evaluable rows for ML training",
                details,
            )
        return DataReadinessService._gate(
            "evaluable_rows",
            "pass",
            "Evaluable rows are sufficient for ML training",
            details,
        )

    @staticmethod
    def _class_balance_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
    ) -> DataReadinessGate:
        details = {
            "positive_label_count": summary.positive_label_count,
            "negative_label_count": summary.negative_label_count,
            "required_positive_min": request.min_positive_rows,
            "required_negative_min": request.min_negative_rows,
        }
        if (
            summary.positive_label_count < request.min_positive_rows
            or summary.negative_label_count < request.min_negative_rows
        ):
            return DataReadinessService._gate(
                "class_balance",
                "fail",
                "Training dataset needs enough positive and negative examples",
                details,
            )
        return DataReadinessService._gate(
            "class_balance",
            "pass",
            "Positive and negative examples are sufficient",
            details,
        )

    @staticmethod
    def _insufficient_ratio_gate(
        request: DataReadinessCheckRequest,
        summary: DataReadinessSummary,
    ) -> DataReadinessGate:
        details = {
            "insufficient_ratio": summary.insufficient_ratio,
            "allowed_max": request.max_insufficient_ratio,
            "insufficient_label_count": summary.insufficient_label_count,
            "label_row_count": summary.label_row_count,
        }
        if (
            summary.label_row_count > 0
            and summary.insufficient_ratio > request.max_insufficient_ratio
        ):
            return DataReadinessService._gate(
                "insufficient_ratio",
                "fail",
                "Too many insufficient_data labels",
                details,
            )
        return DataReadinessService._gate(
            "insufficient_ratio",
            "pass",
            "insufficient_data label ratio is acceptable",
            details,
        )

    def _bond_issues(
        self,
        request: DataReadinessCheckRequest,
        *,
        bonds: list[Bond],
        coverage: dict[str, set[int]],
    ) -> list[DataReadinessBondIssue]:
        company_names = self._company_names({bond.company_id for bond in bonds})
        issues: list[DataReadinessBondIssue] = []
        for bond in bonds:
            bond_issues: list[str] = []
            details: dict[str, Any] = {}
            if not bond.secid:
                bond_issues.append("Bond secid is missing")
            if bond.id not in coverage["market_bond_ids"]:
                bond_issues.append("No market snapshots in selected period")
            if (
                request.return_method in RETURN_METHODS_WITH_CASHFLOWS
                or request.require_cashflows
            ) and bond.id not in coverage["cashflow_bond_ids"]:
                bond_issues.append("No cashflow events found")
            if bond.id not in coverage["feature_bond_ids"]:
                bond_issues.append("No feature snapshots found")
            if bond.id not in coverage["label_bond_ids"]:
                bond_issues.append("No labels found for selected return method")
            elif bond.id in coverage["only_insufficient_label_bond_ids"]:
                bond_issues.append("Only insufficient labels found")
            if bond.id not in coverage["risk_bond_ids"]:
                bond_issues.append("No bond risk assessment found")
            if bond.company_id not in coverage["financial_report_company_ids"]:
                bond_issues.append("Company financial report is missing")
            if bond.company_id not in coverage["credit_health_company_ids"]:
                bond_issues.append("Company credit health snapshot is missing")
            if bond_issues:
                details["issue_count"] = len(bond_issues)
                issues.append(
                    DataReadinessBondIssue(
                        bond_id=bond.id,
                        bond_name=bond.name,
                        isin=bond.isin,
                        secid=bond.secid,
                        company_id=bond.company_id,
                        company_name=company_names.get(bond.company_id),
                        issues=bond_issues,
                        details=details,
                    )
                )
            if len(issues) >= request.max_bond_issues:
                break
        return issues

    def _company_names(self, company_ids: set[int]) -> dict[int, str]:
        if not company_ids:
            return {}
        return dict(
            self.db.execute(
                select(Company.id, Company.name).where(Company.id.in_(company_ids))
            ).all()
        )

    @staticmethod
    def _ready_for_ml_training(
        request: DataReadinessCheckRequest,
        gates: list[DataReadinessGate],
        summary: DataReadinessSummary,
    ) -> bool:
        return (
            all(gate.status != "fail" for gate in gates)
            and summary.evaluable_label_count >= request.min_rows
            and summary.positive_label_count >= request.min_positive_rows
            and summary.negative_label_count >= request.min_negative_rows
        )

    @staticmethod
    def _response_status(gates: list[DataReadinessGate]) -> str:
        statuses = {gate.status for gate in gates}
        if "fail" in statuses:
            return "not_ready"
        if "warning" in statuses:
            return "warning"
        return "ready"

    @staticmethod
    def _recommended_next_actions(gates: list[DataReadinessGate]) -> list[str]:
        actions_by_gate = {
            "selected_bonds": "Add bond records or pass existing bond_ids",
            "moex_secid_coverage": "Add MOEX secid values for selected bonds",
            "market_snapshot_coverage": (
                "Run pipeline step moex_market_sync for the selected bonds and period"
            ),
            "cashflow_coverage": (
                "Run pipeline step moex_cashflow_sync or import cashflow events"
            ),
            "financial_report_coverage": (
                "Import financial reports with published_at before the analysis date"
            ),
            "credit_health_coverage": "Run pipeline step credit_health",
            "bond_risk_assessment_coverage": (
                "Run pipeline step bond_risk_assessment"
            ),
            "feature_rows": "Run dataset_build_price and requested return label steps",
            "label_rows": "Run dataset_build_price and requested return label steps",
            "evaluable_rows": (
                "Expand the date range, add more bonds, or use another horizon_days value"
            ),
            "class_balance": (
                "Expand the date range, add more bonds, or use another horizon_days value"
            ),
            "insufficient_ratio": (
                "Review source data gaps, then rebuild features and labels"
            ),
        }
        actions: list[str] = []
        for gate in gates:
            if gate.status == "pass":
                continue
            action = actions_by_gate.get(gate.name)
            if action is not None and action not in actions:
                actions.append(action)
        return actions

    @staticmethod
    def _gate(
        name: str,
        status_value: str,
        message: str,
        details: dict[str, Any],
    ) -> DataReadinessGate:
        return DataReadinessGate(
            name=name,
            status=status_value,
            message=message,
            details=details,
        )

    def _count(self, model: Any, scope_ids: list[int], *conditions: Any) -> int:
        if not scope_ids:
            return 0
        stmt = select(func.count()).select_from(model).where(*conditions)
        return int(self.db.execute(stmt).scalar_one())

    def _distinct_set(
        self,
        column: Any,
        scope_ids: list[int],
        *conditions: Any,
    ) -> set[int]:
        if not scope_ids:
            return set()
        stmt = select(column).where(*conditions).distinct()
        return {int(value) for value in self.db.execute(stmt).scalars() if value is not None}

    def _joined_feature_label_count(
        self,
        request: DataReadinessCheckRequest,
        bond_ids: list[int],
    ) -> int:
        if not bond_ids:
            return 0
        stmt = (
            select(func.count())
            .select_from(BondFeatureSnapshot)
            .join(
                BondReturnLabel,
                and_(
                    BondReturnLabel.bond_id == BondFeatureSnapshot.bond_id,
                    BondReturnLabel.as_of_date == BondFeatureSnapshot.as_of_date,
                    BondReturnLabel.horizon_days == request.horizon_days,
                    BondReturnLabel.return_method == request.return_method,
                ),
            )
            .where(
                BondFeatureSnapshot.bond_id.in_(bond_ids),
                BondFeatureSnapshot.as_of_date >= request.date_from,
                BondFeatureSnapshot.as_of_date <= request.date_to,
            )
        )
        return int(self.db.execute(stmt).scalar_one())

    def _only_insufficient_label_bond_ids(
        self,
        request: DataReadinessCheckRequest,
        bond_ids: list[int],
    ) -> set[int]:
        if not bond_ids:
            return set()
        rows = self.db.execute(
            select(
                BondReturnLabel.bond_id,
                BondReturnLabel.label,
            ).where(
                BondReturnLabel.bond_id.in_(bond_ids),
                BondReturnLabel.as_of_date >= request.date_from,
                BondReturnLabel.as_of_date <= request.date_to,
                BondReturnLabel.horizon_days == request.horizon_days,
                BondReturnLabel.return_method == request.return_method,
            )
        ).all()
        labels_by_bond: dict[int, list[str]] = defaultdict(list)
        for bond_id, label in rows:
            labels_by_bond[int(bond_id)].append(label)
        return {
            bond_id
            for bond_id, labels in labels_by_bond.items()
            if labels and all(label not in EVALUABLE_LABELS for label in labels)
        }

    @staticmethod
    def _available_report_condition(request: DataReadinessCheckRequest) -> Any:
        end_of_day = datetime.combine(
            request.date_to,
            time.max,
            tzinfo=timezone.utc,
        )
        return or_(
            FinancialReport.published_at <= end_of_day,
            FinancialReport.published_at.is_(None),
        )
