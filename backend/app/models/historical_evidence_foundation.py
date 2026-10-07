"""Independent source archive; operational state does not rewrite observations."""
from datetime import date, datetime
from typing import Any
from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, JSON, String, Integer, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base

J = JSONB().with_variant(JSON(), "sqlite")


class HistoricalEvidenceRun(Base):
    __tablename__="historical_evidence_runs"
    __table_args__=(CheckConstraint("history_end >= history_start",name="history_range"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    run_sha256: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    source_manifest_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    history_start: Mapped[date]=mapped_column(Date,nullable=False)
    history_end: Mapped[date]=mapped_column(Date,nullable=False)
    policy_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    scope_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    planned_work_count: Mapped[int]=mapped_column(Integer,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class HistoricalEvidenceWorkItem(Base):
    __tablename__="historical_evidence_work_items"
    __table_args__=(UniqueConstraint("run_id","query_sha256",name="uq_historical_work_query"),
        CheckConstraint("next_offset >= 0",name="nonnegative_offset"),
        CheckConstraint("status in ('PENDING','COMPLETE','FAILED')",name="work_status"))
    id: Mapped[int]=mapped_column(primary_key=True)
    run_id: Mapped[int]=mapped_column(ForeignKey("historical_evidence_runs.id",ondelete="RESTRICT"),nullable=False,index=True)
    query_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    query_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    next_offset: Mapped[int]=mapped_column(Integer,nullable=False,default=0)
    status: Mapped[str]=mapped_column(String(16),nullable=False,default="PENDING")
    failure_code: Mapped[str|None]=mapped_column(String(64))


class HistoricalEvidenceSourcePage(Base):
    __tablename__="historical_evidence_source_pages"
    __table_args__=(UniqueConstraint("work_id","offset","page_sha256",name="uq_historical_page_version"),
        CheckConstraint("offset >= 0",name="nonnegative_page_offset"))
    id: Mapped[int]=mapped_column(primary_key=True)
    work_id: Mapped[int]=mapped_column(ForeignKey("historical_evidence_work_items.id",ondelete="RESTRICT"),nullable=False,index=True)
    offset: Mapped[int]=mapped_column(Integer,nullable=False)
    page_sha256: Mapped[str]=mapped_column(String(64),nullable=False,index=True)
    page_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    observed_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    ingested_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)


class HistoricalEvidenceSecurity(Base):
    __tablename__="historical_evidence_securities"
    id: Mapped[int]=mapped_column(primary_key=True)
    identity_sha256: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    secid: Mapped[str]=mapped_column(String(32),nullable=False,index=True)
    isin: Mapped[str|None]=mapped_column(String(32),index=True)
    bond_id: Mapped[int|None]=mapped_column(ForeignKey("bonds.id",ondelete="RESTRICT"),index=True)


class HistoricalEvidenceObservation(Base):
    __tablename__="historical_evidence_observations"
    __table_args__=(UniqueConstraint("page_id","ordinal",name="uq_historical_observation_row"),
        CheckConstraint("ordinal >= 0",name="nonnegative_ordinal"),
        CheckConstraint("proof_state in ('DATED_SOURCE','CURRENT_OBSERVATION','UNPROVEN')",name="proof_state"))
    id: Mapped[int]=mapped_column(primary_key=True)
    page_id: Mapped[int]=mapped_column(ForeignKey("historical_evidence_source_pages.id",ondelete="RESTRICT"),nullable=False,index=True)
    security_id: Mapped[int|None]=mapped_column(ForeignKey("historical_evidence_securities.id",ondelete="RESTRICT"),index=True)
    ordinal: Mapped[int]=mapped_column(Integer,nullable=False)
    family: Mapped[str]=mapped_column(String(32),nullable=False,index=True)
    event_date: Mapped[date|None]=mapped_column(Date,index=True)
    effective_date: Mapped[date|None]=mapped_column(Date)
    available_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
    proof_state: Mapped[str]=mapped_column(String(24),nullable=False)
    normalized_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    raw_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    row_sha256: Mapped[str]=mapped_column(String(64),nullable=False,index=True)


class HistoricalEvidenceApplicationReceipt(Base):
    __tablename__="historical_evidence_application_receipts"
    __table_args__=(CheckConstraint("operation in ('ACQUIRE','APPLY')",name="receipt_operation"),)
    id: Mapped[int]=mapped_column(primary_key=True)
    run_id: Mapped[int]=mapped_column(ForeignKey("historical_evidence_runs.id",ondelete="RESTRICT"),nullable=False,index=True)
    batch_sha256: Mapped[str]=mapped_column(String(64),unique=True,nullable=False)
    operation: Mapped[str]=mapped_column(String(16),nullable=False)
    plan_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    before_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    after_sha256: Mapped[str]=mapped_column(String(64),nullable=False)
    receipt_json: Mapped[dict[str,Any]]=mapped_column(J,nullable=False)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),server_default=func.now(),nullable=False)
