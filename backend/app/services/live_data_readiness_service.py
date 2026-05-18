from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.ml_model_run import MLModelRun
from app.models.ml_prediction import MLPrediction
from app.schemas.live_data_readiness import (
    LiveDataReadinessCheck,
    LiveDataReadinessResponse,
    LiveDataReadinessWarning,
)


class LiveDataReadinessService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def check(
        self,
        *,
        recent_days: int = 7,
        minimum_corporate_bonds: int = 20,
        minimum_bonds_with_recent_market_snapshot: int = 20,
        minimum_bonds_with_recent_features: int = 20,
        minimum_bonds_with_predictions: int = 20,
        include_ofz: bool = False,
    ) -> LiveDataReadinessResponse:
        self._validate(
            recent_days=recent_days,
            minimum_corporate_bonds=minimum_corporate_bonds,
            minimum_bonds_with_recent_market_snapshot=(
                minimum_bonds_with_recent_market_snapshot
            ),
            minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
            minimum_bonds_with_predictions=minimum_bonds_with_predictions,
        )

        as_of = datetime.now(timezone.utc)
        recent_cutoff = as_of.date() - timedelta(days=recent_days)
        bonds = list(self.db.execute(select(Bond).order_by(Bond.id.asc())).scalars())
        corporate_bonds = [bond for bond in bonds if not self._is_ofz_bond(bond)]
        ofz_bonds = [bond for bond in bonds if self._is_ofz_bond(bond)]
        working_bonds = bonds if include_ofz else corporate_bonds
        working_bond_ids = [bond.id for bond in working_bonds]

        market = self._market_summary(working_bond_ids, recent_cutoff)
        cashflows = self._cashflow_summary(working_bond_ids)
        features = self._feature_summary(working_bond_ids, recent_cutoff)
        latest_run = self._latest_completed_model_run()
        predictions = self._prediction_summary(
            working_bond_ids=working_bond_ids,
            model_run_id=latest_run.id if latest_run is not None else None,
            recent_cutoff=recent_cutoff,
        )

        checks = self._checks(
            recent_days=recent_days,
            recent_cutoff=recent_cutoff,
            corporate_bond_count=len(corporate_bonds),
            working_bond_count=len(working_bonds),
            market=market,
            cashflows=cashflows,
            features=features,
            latest_run=latest_run,
            predictions=predictions,
            minimum_corporate_bonds=minimum_corporate_bonds,
            minimum_bonds_with_recent_market_snapshot=(
                minimum_bonds_with_recent_market_snapshot
            ),
            minimum_bonds_with_recent_features=minimum_bonds_with_recent_features,
            minimum_bonds_with_predictions=minimum_bonds_with_predictions,
        )
        status_value = self._response_status(checks)
        warnings = self._warnings(checks)

        return LiveDataReadinessResponse(
            status=status_value,
            as_of=as_of,
            corporate_bond_count=len(corporate_bonds),
            ofz_bond_count=len(ofz_bonds),
            total_bond_count=len(bonds),
            working_bond_count=len(working_bonds),
            company_count=self._company_count(working_bonds),
            latest_market_snapshot_date=market["latest_date"],
            market_snapshot_count=market["row_count"],
            bonds_with_recent_market_snapshot_count=market["recent_bond_count"],
            latest_cashflow_date=cashflows["latest_date"],
            cashflow_event_count=cashflows["row_count"],
            bonds_with_cashflows_count=cashflows["bond_count"],
            latest_feature_snapshot_date=features["latest_date"],
            feature_snapshot_count=features["row_count"],
            bonds_with_recent_features_count=features["recent_bond_count"],
            latest_completed_model_run_id=latest_run.id if latest_run else None,
            latest_completed_model_run_created_at=(
                latest_run.created_at if latest_run else None
            ),
            prediction_count_for_latest_run=predictions["row_count"],
            bonds_with_predictions_for_latest_run_count=predictions["bond_count"],
            latest_prediction_date=predictions["latest_date"],
            checks=checks,
            warnings=warnings,
            next_steps=self._next_steps(checks),
        )

    @staticmethod
    def _validate(
        *,
        recent_days: int,
        minimum_corporate_bonds: int,
        minimum_bonds_with_recent_market_snapshot: int,
        minimum_bonds_with_recent_features: int,
        minimum_bonds_with_predictions: int,
    ) -> None:
        if recent_days < 1 or recent_days > 365:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="recent_days must be between 1 and 365",
            )
        minimums = {
            "minimum_corporate_bonds": minimum_corporate_bonds,
            "minimum_bonds_with_recent_market_snapshot": (
                minimum_bonds_with_recent_market_snapshot
            ),
            "minimum_bonds_with_recent_features": minimum_bonds_with_recent_features,
            "minimum_bonds_with_predictions": minimum_bonds_with_predictions,
        }
        for name, value in minimums.items():
            if value < 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"{name} must be non-negative",
                )

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
    def _company_count(bonds: list[Bond]) -> int:
        return len({bond.company_id for bond in bonds})

    def _market_summary(
        self,
        bond_ids: list[int],
        recent_cutoff: Any,
    ) -> dict[str, Any]:
        return {
            "row_count": self._count(BondMarketSnapshot, bond_ids),
            "latest_date": self._max_date(
                BondMarketSnapshot.trade_date,
                BondMarketSnapshot.bond_id,
                bond_ids,
            ),
            "recent_bond_count": self._distinct_count(
                BondMarketSnapshot.bond_id,
                bond_ids,
                BondMarketSnapshot.trade_date >= recent_cutoff,
            ),
        }

    def _cashflow_summary(self, bond_ids: list[int]) -> dict[str, Any]:
        return {
            "row_count": self._count(BondCashflowEvent, bond_ids),
            "latest_date": self._max_date(
                BondCashflowEvent.event_date,
                BondCashflowEvent.bond_id,
                bond_ids,
            ),
            "bond_count": self._distinct_count(BondCashflowEvent.bond_id, bond_ids),
        }

    def _feature_summary(
        self,
        bond_ids: list[int],
        recent_cutoff: Any,
    ) -> dict[str, Any]:
        return {
            "row_count": self._count(BondFeatureSnapshot, bond_ids),
            "latest_date": self._max_date(
                BondFeatureSnapshot.as_of_date,
                BondFeatureSnapshot.bond_id,
                bond_ids,
            ),
            "recent_bond_count": self._distinct_count(
                BondFeatureSnapshot.bond_id,
                bond_ids,
                BondFeatureSnapshot.as_of_date >= recent_cutoff,
            ),
        }

    def _latest_completed_model_run(self) -> MLModelRun | None:
        return self.db.execute(
            select(MLModelRun)
            .where(MLModelRun.status == "completed")
            .order_by(desc(MLModelRun.created_at), desc(MLModelRun.id))
            .limit(1)
        ).scalar_one_or_none()

    def _prediction_summary(
        self,
        *,
        working_bond_ids: list[int],
        model_run_id: int | None,
        recent_cutoff: Any,
    ) -> dict[str, Any]:
        if model_run_id is None or not working_bond_ids:
            return {"row_count": 0, "bond_count": 0, "recent_bond_count": 0, "latest_date": None}
        conditions = [
            MLPrediction.model_run_id == model_run_id,
            MLPrediction.bond_id.in_(working_bond_ids),
        ]
        return {
            "row_count": int(
                self.db.execute(
                    select(func.count()).select_from(MLPrediction).where(*conditions)
                ).scalar_one()
            ),
            "bond_count": int(
                self.db.execute(
                    select(func.count(func.distinct(MLPrediction.bond_id))).where(
                        *conditions
                    )
                ).scalar_one()
            ),
            "recent_bond_count": int(
                self.db.execute(
                    select(func.count(func.distinct(MLPrediction.bond_id))).where(
                        *conditions,
                        MLPrediction.as_of_date >= recent_cutoff,
                    )
                ).scalar_one()
            ),
            "latest_date": self.db.execute(
                select(func.max(MLPrediction.as_of_date)).where(*conditions)
            ).scalar_one(),
        }

    def _checks(
        self,
        *,
        recent_days: int,
        recent_cutoff: Any,
        corporate_bond_count: int,
        working_bond_count: int,
        market: dict[str, Any],
        cashflows: dict[str, Any],
        features: dict[str, Any],
        latest_run: MLModelRun | None,
        predictions: dict[str, Any],
        minimum_corporate_bonds: int,
        minimum_bonds_with_recent_market_snapshot: int,
        minimum_bonds_with_recent_features: int,
        minimum_bonds_with_predictions: int,
    ) -> list[LiveDataReadinessCheck]:
        checks = [
            self._corporate_universe_check(
                corporate_bond_count,
                minimum_corporate_bonds,
            ),
            self._market_snapshots_check(market),
            self._recent_market_snapshots_check(
                market,
                recent_days,
                recent_cutoff,
                minimum_bonds_with_recent_market_snapshot,
            ),
            self._cashflows_check(cashflows, working_bond_count),
            self._feature_snapshots_check(features),
            self._recent_feature_snapshots_check(
                features,
                recent_days,
                recent_cutoff,
                minimum_bonds_with_recent_features,
            ),
            self._completed_model_run_check(latest_run),
            self._predictions_check(predictions, minimum_bonds_with_predictions),
            self._recent_predictions_check(predictions, recent_days, recent_cutoff),
        ]
        checks.append(self._paper_pilot_check(checks))
        return checks

    @staticmethod
    def _corporate_universe_check(
        corporate_bond_count: int,
        configured_minimum: int,
    ) -> LiveDataReadinessCheck:
        details = {
            "corporate_bond_count": corporate_bond_count,
            "configured_minimum": configured_minimum,
        }
        if corporate_bond_count < configured_minimum:
            return LiveDataReadinessService._check(
                "corporate_universe_available",
                "failed",
                "Corporate bond universe is below configured minimum",
                details,
            )
        return LiveDataReadinessService._check(
            "corporate_universe_available",
            "passed",
            "Corporate bond universe is available",
            details,
        )

    @staticmethod
    def _market_snapshots_check(market: dict[str, Any]) -> LiveDataReadinessCheck:
        details = {"market_snapshot_count": market["row_count"]}
        if market["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "market_snapshots_available",
                "failed",
                "Market snapshots are missing",
                details,
            )
        return LiveDataReadinessService._check(
            "market_snapshots_available",
            "passed",
            "Market snapshots are available",
            details,
        )

    @staticmethod
    def _recent_market_snapshots_check(
        market: dict[str, Any],
        recent_days: int,
        recent_cutoff: Any,
        configured_minimum: int,
    ) -> LiveDataReadinessCheck:
        details = {
            "latest_market_snapshot_date": market["latest_date"],
            "bonds_with_recent_market_snapshot_count": market["recent_bond_count"],
            "configured_minimum": configured_minimum,
            "recent_days": recent_days,
            "recent_cutoff": recent_cutoff,
        }
        if market["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "recent_market_snapshots_available",
                "failed",
                "Recent market snapshots are missing",
                details,
            )
        if market["latest_date"] is not None and market["latest_date"] < recent_cutoff:
            return LiveDataReadinessService._check(
                "recent_market_snapshots_available",
                "warning",
                "Market snapshots exist but are stale",
                details,
            )
        if market["recent_bond_count"] < configured_minimum:
            return LiveDataReadinessService._check(
                "recent_market_snapshots_available",
                "failed",
                "Recent market snapshot coverage is below configured minimum",
                details,
            )
        return LiveDataReadinessService._check(
            "recent_market_snapshots_available",
            "passed",
            "Recent market snapshot coverage is sufficient",
            details,
        )

    @staticmethod
    def _cashflows_check(
        cashflows: dict[str, Any],
        working_bond_count: int,
    ) -> LiveDataReadinessCheck:
        details = {
            "cashflow_event_count": cashflows["row_count"],
            "bonds_with_cashflows_count": cashflows["bond_count"],
            "working_bond_count": working_bond_count,
        }
        if cashflows["row_count"] <= 0 or cashflows["bond_count"] <= 0:
            return LiveDataReadinessService._check(
                "cashflows_available",
                "failed",
                "Cashflow data is missing",
                details,
            )
        if cashflows["bond_count"] < working_bond_count:
            return LiveDataReadinessService._check(
                "cashflows_available",
                "warning",
                "Cashflow data does not cover every working bond",
                details,
            )
        return LiveDataReadinessService._check(
            "cashflows_available",
            "passed",
            "Cashflow data is available",
            details,
        )

    @staticmethod
    def _feature_snapshots_check(features: dict[str, Any]) -> LiveDataReadinessCheck:
        details = {"feature_snapshot_count": features["row_count"]}
        if features["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "feature_snapshots_available",
                "failed",
                "Feature snapshots are missing",
                details,
            )
        return LiveDataReadinessService._check(
            "feature_snapshots_available",
            "passed",
            "Feature snapshots are available",
            details,
        )

    @staticmethod
    def _recent_feature_snapshots_check(
        features: dict[str, Any],
        recent_days: int,
        recent_cutoff: Any,
        configured_minimum: int,
    ) -> LiveDataReadinessCheck:
        details = {
            "latest_feature_snapshot_date": features["latest_date"],
            "bonds_with_recent_features_count": features["recent_bond_count"],
            "configured_minimum": configured_minimum,
            "recent_days": recent_days,
            "recent_cutoff": recent_cutoff,
        }
        if features["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "recent_feature_snapshots_available",
                "failed",
                "Recent feature snapshots are missing",
                details,
            )
        if features["latest_date"] is not None and features["latest_date"] < recent_cutoff:
            return LiveDataReadinessService._check(
                "recent_feature_snapshots_available",
                "warning",
                "Feature snapshots exist but are stale",
                details,
            )
        if features["recent_bond_count"] < configured_minimum:
            return LiveDataReadinessService._check(
                "recent_feature_snapshots_available",
                "failed",
                "Recent feature coverage is below configured minimum",
                details,
            )
        return LiveDataReadinessService._check(
            "recent_feature_snapshots_available",
            "passed",
            "Recent feature coverage is sufficient",
            details,
        )

    @staticmethod
    def _completed_model_run_check(
        latest_run: MLModelRun | None,
    ) -> LiveDataReadinessCheck:
        details = {
            "latest_completed_model_run_id": latest_run.id if latest_run else None,
            "latest_completed_model_run_created_at": (
                latest_run.created_at if latest_run else None
            ),
        }
        if latest_run is None:
            return LiveDataReadinessService._check(
                "completed_model_run_available",
                "failed",
                "Completed ML model run is missing",
                details,
            )
        return LiveDataReadinessService._check(
            "completed_model_run_available",
            "passed",
            "Completed ML model run is available",
            details,
        )

    @staticmethod
    def _predictions_check(
        predictions: dict[str, Any],
        configured_minimum: int,
    ) -> LiveDataReadinessCheck:
        details = {
            "prediction_count_for_latest_run": predictions["row_count"],
            "bonds_with_predictions_for_latest_run_count": predictions["bond_count"],
            "configured_minimum": configured_minimum,
        }
        if predictions["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "predictions_available",
                "failed",
                "Predictions for latest completed model run are missing",
                details,
            )
        if predictions["bond_count"] < configured_minimum:
            return LiveDataReadinessService._check(
                "predictions_available",
                "failed",
                "Prediction coverage is below configured minimum",
                details,
            )
        return LiveDataReadinessService._check(
            "predictions_available",
            "passed",
            "Predictions for latest completed model run are available",
            details,
        )

    @staticmethod
    def _recent_predictions_check(
        predictions: dict[str, Any],
        recent_days: int,
        recent_cutoff: Any,
    ) -> LiveDataReadinessCheck:
        details = {
            "latest_prediction_date": predictions["latest_date"],
            "bonds_with_recent_predictions_count": predictions["recent_bond_count"],
            "recent_days": recent_days,
            "recent_cutoff": recent_cutoff,
        }
        if predictions["row_count"] <= 0:
            return LiveDataReadinessService._check(
                "recent_predictions_available",
                "failed",
                "Recent predictions are missing",
                details,
            )
        if (
            predictions["latest_date"] is not None
            and predictions["latest_date"] < recent_cutoff
        ):
            return LiveDataReadinessService._check(
                "recent_predictions_available",
                "warning",
                "Predictions exist but are stale",
                details,
            )
        return LiveDataReadinessService._check(
            "recent_predictions_available",
            "passed",
            "Recent predictions are available",
            details,
        )

    @staticmethod
    def _paper_pilot_check(
        checks: list[LiveDataReadinessCheck],
    ) -> LiveDataReadinessCheck:
        blocking = [
            check.name
            for check in checks
            if check.status == "failed"
        ]
        warning = [
            check.name
            for check in checks
            if check.status == "warning"
        ]
        details = {"blocking_checks": blocking, "warning_checks": warning}
        if blocking:
            return LiveDataReadinessService._check(
                "paper_pilot_data_ready",
                "failed",
                "Live data chain is not ready for the virtual paper pilot",
                details,
            )
        if warning:
            return LiveDataReadinessService._check(
                "paper_pilot_data_ready",
                "warning",
                "Live data chain has warnings for the virtual paper pilot",
                details,
            )
        return LiveDataReadinessService._check(
            "paper_pilot_data_ready",
            "passed",
            "Live data chain is ready for the virtual paper pilot",
            details,
        )

    @staticmethod
    def _response_status(checks: list[LiveDataReadinessCheck]) -> str:
        statuses = {check.status for check in checks}
        if "failed" in statuses:
            return "not_ready"
        if "warning" in statuses:
            return "warning"
        return "ready"

    @staticmethod
    def _warnings(
        checks: list[LiveDataReadinessCheck],
    ) -> list[LiveDataReadinessWarning]:
        return [
            LiveDataReadinessWarning(
                code=check.name,
                message=check.message,
                details=check.details,
            )
            for check in checks
            if check.status == "warning"
        ]

    @staticmethod
    def _next_steps(checks: list[LiveDataReadinessCheck]) -> list[str]:
        actions_by_check = {
            "corporate_universe_available": "Sync or import more corporate bonds.",
            "market_snapshots_available": "Run market data sync for corporate bonds.",
            "recent_market_snapshots_available": (
                "Run market data sync for the latest market date."
            ),
            "cashflows_available": "Run cashflow sync for the working bond universe.",
            "feature_snapshots_available": (
                "Build feature snapshots for the latest market date."
            ),
            "recent_feature_snapshots_available": (
                "Build recent feature snapshots for the working bond universe."
            ),
            "completed_model_run_available": (
                "Train a model before generating predictions."
            ),
            "predictions_available": (
                "Generate predictions for the latest completed model run."
            ),
            "recent_predictions_available": (
                "Generate recent predictions for the latest completed model run."
            ),
            "paper_pilot_data_ready": (
                "Run pilot bootstrap only after live data readiness is ready or explicitly accepted with warnings."
            ),
        }
        actions: list[str] = []
        for check in checks:
            if check.status == "passed":
                continue
            action = actions_by_check.get(check.name)
            if action is not None and action not in actions:
                actions.append(action)
        return actions

    @staticmethod
    def _check(
        name: str,
        status_value: str,
        message: str,
        details: dict[str, Any],
    ) -> LiveDataReadinessCheck:
        return LiveDataReadinessCheck(
            name=name,
            status=status_value,
            message=message,
            details=details,
        )

    def _count(self, model: Any, bond_ids: list[int]) -> int:
        if not bond_ids:
            return 0
        return int(
            self.db.execute(
                select(func.count()).select_from(model).where(model.bond_id.in_(bond_ids))
            ).scalar_one()
        )

    def _distinct_count(
        self,
        column: Any,
        bond_ids: list[int],
        *conditions: Any,
    ) -> int:
        if not bond_ids:
            return 0
        stmt = select(func.count(func.distinct(column))).where(
            column.in_(bond_ids),
            *conditions,
        )
        return int(self.db.execute(stmt).scalar_one())

    def _max_date(
        self,
        date_column: Any,
        bond_column: Any,
        bond_ids: list[int],
    ) -> Any:
        if not bond_ids:
            return None
        return self.db.execute(
            select(func.max(date_column)).where(bond_column.in_(bond_ids))
        ).scalar_one()
