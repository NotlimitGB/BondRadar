from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.bond_feature_snapshot import BondFeatureSnapshot
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_return_label import BondReturnLabel
from app.models.dataset_build_run import DatasetBuildRun
from app.schemas.ml_dataset import DatasetBuildRequest, DatasetBuildResult
from app.services.feature_snapshot_service import FeatureSnapshotService
from app.services.label_builder_service import LabelBuilderService


class DatasetBuildService:
    def __init__(
        self,
        db: Session,
        feature_service: FeatureSnapshotService | None = None,
        label_service: LabelBuilderService | None = None,
    ) -> None:
        self.db = db
        self.feature_service = feature_service or FeatureSnapshotService(db)
        self.label_service = label_service or LabelBuilderService(db)

    def build(self, request: DatasetBuildRequest) -> DatasetBuildResult:
        self._validate_request(request)
        run = self._create_run(request)
        errors: list[dict[str, Any]] = []

        try:
            pairs = self._snapshot_pairs(request)
            for bond_id, trade_date in pairs:
                try:
                    feature_outcome = self.feature_service.build_for_bond_date(
                        bond_id,
                        trade_date,
                        rebuild_existing=request.rebuild_existing,
                    )
                    label_outcome = self.label_service.build_for_bond_date(
                        bond_id,
                        trade_date,
                        request.horizon_days,
                        rebuild_existing=request.rebuild_existing,
                    )
                    run = self._refresh_run(run.id)
                    self._apply_action(run, "features", feature_outcome.action)
                    self._apply_action(run, "labels", label_outcome.action)
                    self.db.add(run)
                    self.db.commit()
                except Exception as exc:
                    self.db.rollback()
                    errors.append(
                        {
                            "bond_id": bond_id,
                            "as_of_date": trade_date.isoformat(),
                            "error": self._error_detail(exc),
                        }
                    )
                    run = self._refresh_run(run.id)
                    run.errors = list(errors)
                    run.errors_count = len(errors)
                    self.db.add(run)
                    self.db.commit()

            run = self._refresh_run(run.id)
            run.status = "completed_with_errors" if errors else "completed"
            run.finished_at = datetime.now(timezone.utc)
            run.errors = list(errors)
            run.errors_count = len(errors)
            self.db.add(run)
            self.db.commit()
            self.db.refresh(run)
            return self._result(run)
        except Exception as exc:
            self.db.rollback()
            run = self._refresh_run(run.id)
            run.status = "failed"
            run.finished_at = datetime.now(timezone.utc)
            run.errors = [{"error": self._error_detail(exc)}]
            run.errors_count = 1
            self.db.add(run)
            self.db.commit()
            raise

    def list_runs(self, *, limit: int = 20) -> list[DatasetBuildRun]:
        return list(
            self.db.execute(
                select(DatasetBuildRun)
                .order_by(DatasetBuildRun.started_at.desc(), DatasetBuildRun.id.desc())
                .limit(limit)
            ).scalars()
        )

    def list_features(
        self,
        *,
        bond_id: int | None = None,
        company_id: int | None = None,
        as_of_date_from=None,
        as_of_date_to=None,
        limit: int = 100,
    ) -> list[BondFeatureSnapshot]:
        stmt = select(BondFeatureSnapshot)
        if bond_id is not None:
            stmt = stmt.where(BondFeatureSnapshot.bond_id == bond_id)
        if company_id is not None:
            stmt = stmt.where(BondFeatureSnapshot.company_id == company_id)
        if as_of_date_from is not None:
            stmt = stmt.where(BondFeatureSnapshot.as_of_date >= as_of_date_from)
        if as_of_date_to is not None:
            stmt = stmt.where(BondFeatureSnapshot.as_of_date <= as_of_date_to)
        stmt = stmt.order_by(
            BondFeatureSnapshot.as_of_date.desc(),
            BondFeatureSnapshot.id.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    def list_labels(
        self,
        *,
        bond_id: int | None = None,
        horizon_days: int | None = None,
        as_of_date_from=None,
        as_of_date_to=None,
        limit: int = 100,
    ) -> list[BondReturnLabel]:
        stmt = select(BondReturnLabel)
        if bond_id is not None:
            stmt = stmt.where(BondReturnLabel.bond_id == bond_id)
        if horizon_days is not None:
            stmt = stmt.where(BondReturnLabel.horizon_days == horizon_days)
        if as_of_date_from is not None:
            stmt = stmt.where(BondReturnLabel.as_of_date >= as_of_date_from)
        if as_of_date_to is not None:
            stmt = stmt.where(BondReturnLabel.as_of_date <= as_of_date_to)
        stmt = stmt.order_by(
            BondReturnLabel.as_of_date.desc(),
            BondReturnLabel.id.desc(),
        ).limit(limit)
        return list(self.db.execute(stmt).scalars())

    @staticmethod
    def _validate_request(request: DatasetBuildRequest) -> None:
        if request.as_of_date_from > request.as_of_date_to:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid date range",
            )
        if request.horizon_days <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="horizon_days must be positive",
            )

    def _create_run(self, request: DatasetBuildRequest) -> DatasetBuildRun:
        run = DatasetBuildRun(
            status="running",
            as_of_date_from=request.as_of_date_from,
            as_of_date_to=request.as_of_date_to,
            horizon_days=request.horizon_days,
            features_created=0,
            labels_created=0,
            features_updated=0,
            labels_updated=0,
            errors_count=0,
            errors=[],
            params={
                "as_of_date_from": request.as_of_date_from.isoformat(),
                "as_of_date_to": request.as_of_date_to.isoformat(),
                "horizon_days": request.horizon_days,
                "bond_ids": request.bond_ids,
                "rebuild_existing": request.rebuild_existing,
            },
        )
        self.db.add(run)
        self.db.commit()
        self.db.refresh(run)
        return run

    def _snapshot_pairs(self, request: DatasetBuildRequest) -> list[tuple[int, Any]]:
        stmt = (
            select(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date)
            .where(
                BondMarketSnapshot.trade_date >= request.as_of_date_from,
                BondMarketSnapshot.trade_date <= request.as_of_date_to,
            )
            .distinct()
        )
        if request.bond_ids:
            stmt = stmt.where(BondMarketSnapshot.bond_id.in_(set(request.bond_ids)))
        stmt = stmt.order_by(BondMarketSnapshot.bond_id, BondMarketSnapshot.trade_date)
        return [(row.bond_id, row.trade_date) for row in self.db.execute(stmt)]

    def _refresh_run(self, run_id: int) -> DatasetBuildRun:
        run = self.db.get(DatasetBuildRun, run_id)
        if run is None:
            raise RuntimeError(f"Dataset build run {run_id} was not found")
        return run

    @staticmethod
    def _apply_action(run: DatasetBuildRun, prefix: str, action: str) -> None:
        if action == "created":
            setattr(run, f"{prefix}_created", getattr(run, f"{prefix}_created") + 1)
        elif action == "updated":
            setattr(run, f"{prefix}_updated", getattr(run, f"{prefix}_updated") + 1)

    @staticmethod
    def _error_detail(exc: Exception) -> str:
        if isinstance(exc, HTTPException):
            return str(exc.detail)
        return str(exc)

    @staticmethod
    def _result(run: DatasetBuildRun) -> DatasetBuildResult:
        return DatasetBuildResult(
            run_id=run.id,
            status=run.status,
            features_created=run.features_created,
            features_updated=run.features_updated,
            labels_created=run.labels_created,
            labels_updated=run.labels_updated,
            errors_count=run.errors_count,
            errors=run.errors,
        )
