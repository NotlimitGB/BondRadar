"""Read-only scalar inventory. No loaders, source requests or resolved-state writes."""
from dataclasses import dataclass
from types import MappingProxyType
from sqlalchemy import select, func, case, and_
from app.models.bond import Bond
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_security_master_profile import BondSecurityMasterProfile
from app.models.bond_security_master_evidence import BondSecurityMasterEvidence
from app.models.bond_legal_issuer_profile import BondLegalIssuerProfile
from app.models.bond_legal_issuer_evidence import BondLegalIssuerEvidence
from app.models.legal_issuer import LegalIssuer
from app.models.credit_risk_evidence import CreditRatingEvent, CreditDefaultEvent, CreditRiskSourceArtifact
from app.models.controlled_financial_statement_value import ControlledFinancialStatementValue
from app.models.cbr_bank_financial_evidence import (
    CbrBankSourceArtifact, CbrBankReportSnapshot, CbrBankArtifactAvailabilityEvidence,
    CbrBankRawObservation, CbrBankNormalizedObservation, CbrBankCreditMetric,
    CbrBankSubjectLegalIssuerEvidence, CbrBankSubjectLegalIssuerProfile,
)
from app.models.bond_return_label import BondReturnLabel


MARKET_FIELDS = ("price", "clean_price", "dirty_price", "nkd", "yield_to_maturity",
                 "duration_years", "volume", "liquidity_score")
LABEL_FIELDS = ("future_return", "price_return", "coupon_return", "amortization_return",
                "redemption_return", "gross_total_return", "net_total_return")
SPECS = (
    (Bond, ("id", "isin", "secid", "name", "maturity_date", "created_at")),
    (BondMarketSnapshot, ("id", "bond_id", "trade_date", "source", *MARKET_FIELDS,
                          "raw_payload", "created_at")),
    (BondCashflowEvent, ("id", "bond_id", "event_date", "source", "event_type", "currency", "amount", "created_at")),
    (BondSecurityMasterProfile, ("id", "bond_id", "contract_version", "currency_code", "currency_state",
        "nominal_value", "nominal_state", "coupon_structure", "amortization_structure", "perpetual_structure",
        "lot_size", "lot_size_state", "trading_board", "trading_board_state", "coupon_frequency_per_year",
        "coupon_frequency_state", "maturity_date", "maturity_state", "listing_status",
        "outstanding_nominal", "outstanding_nominal_state", "created_at", "updated_at", "last_resolved_at")),
    (BondSecurityMasterEvidence, ("id", "bond_id", "field_name", "source", "contract_version",
        "normalized_value_json", "effective_at", "observed_at", "ingestion_at")),
    (BondLegalIssuerProfile, ("id", "bond_id", "contract_version", "mapping_state", "mapping_source", "source_issuer_id",
        "last_observed_at", "last_resolved_at", "created_at", "updated_at")),
    (BondLegalIssuerEvidence, ("id", "bond_id", "source_issuer_id", "matched_isin", "matched_secid",
        "effective_at", "observed_at", "ingestion_at", "security_match_status")),
    (LegalIssuer, ("id", "identity_source", "source_issuer_id", "resolution_state", "issuer_inn")),
    (CreditRiskSourceArtifact, ("id", "contract_version", "source_provider", "source_kind", "content_sha256", "retrieved_at")),
    (CreditRatingEvent, ("id", "bond_id", "legal_issuer_id", "artifact_id", "contract_version",
        "target_kind", "resolution_state", "source_bond_isin", "source_issuer_inn", "rating_agency",
        "event_date", "publication_precision", "publication_date", "publication_at",
        "rating_scale_raw", "rating_value_raw")),
    (CreditDefaultEvent, ("id", "bond_id", "artifact_id", "resolution_state", "event_date",
        "publication_precision", "publication_date", "publication_at", "default_class")),
    (ControlledFinancialStatementValue, ("id", "report_year", "company_id", "created_at", "updated_at")),
    (CbrBankSourceArtifact, ("id", "form", "report_date", "first_discovered_at", "first_retrieved_at", "ingested_at")),
    (CbrBankReportSnapshot, ("id", "artifact_id", "form", "report_date", "publication_status",
        "publication_at", "observed_at", "retrieved_at", "ingested_at")),
    (CbrBankArtifactAvailabilityEvidence, ("id", "artifact_id", "evidence_source", "observed_at", "exact_payload_bound")),
    (CbrBankRawObservation, ("id", "report_date", "form", "ingested_at")),
    (CbrBankNormalizedObservation, ("id", "report_date", "form", "created_at")),
    (CbrBankCreditMetric, ("id", "report_date", "source_form", "created_at")),
    (CbrBankSubjectLegalIssuerEvidence, ("id", "legal_issuer_id", "bridge_state", "observed_at", "retrieved_at", "ingested_at")),
    (CbrBankSubjectLegalIssuerProfile, ("reporting_subject_id", "legal_issuer_id", "bridge_state", "last_observed_at")),
    (BondReturnLabel, ("id", "bond_id", "as_of_date", "horizon_days", "return_method", "start_market_snapshot_id", "end_market_snapshot_id")),
)


@dataclass(frozen=True)
class HistoricalEvidenceRead:
    tables: object
    decision_windows: tuple
    market_date_counts: tuple
    label_nonnull_counts: object


def _rows(db, model, names):
    columns = [getattr(model, n) for n in names]
    statement = select(*columns).order_by(*model.__mapper__.primary_key)
    if model is BondMarketSnapshot:
        statement = statement.where(model.source == "moex")
    return tuple(MappingProxyType(dict(row)) for row in db.execute(statement).mappings())


def read_evidence(db):
    """One set of scalar reads and set-based date/Bond window aggregation."""
    with db.no_autoflush:
        tables = {model.__tablename__: _rows(db, model, names) for model, names in SPECS}
        market = BondMarketSnapshot
        dates = select(market.trade_date.label("entry_date")).where(market.source == "moex").distinct().cte("entry_dates")
        day = dates.c.entry_date
        if db.get_bind().dialect.name == "sqlite":
            start, fresh = func.date(day, "-30 days"), func.date(day, "-7 days")
        else:
            start, fresh = day - 30, day - 7
        windows = select(day, market.bond_id, func.count(func.distinct(market.trade_date)).label("observation_days"),
            func.max(case((market.trade_date >= fresh, market.trade_date), else_=None)).label("decision_trade_date")
        ).select_from(dates.join(market, and_(market.source == "moex", market.trade_date < day,
            market.trade_date >= start))).group_by(day, market.bond_id).order_by(day, market.bond_id)
        grouped = select(market.trade_date, func.count().label("row_count"),
            func.count(func.distinct(market.bond_id)).label("bond_count")
        ).where(market.source == "moex").group_by(market.trade_date).order_by(market.trade_date)
        labels = dict(db.execute(select(*(func.count(getattr(BondReturnLabel, f)).label(f)
            for f in LABEL_FIELDS))).mappings().one())
        return HistoricalEvidenceRead(MappingProxyType(tables),
            tuple(MappingProxyType(dict(r)) for r in db.execute(windows).mappings()),
            tuple(MappingProxyType(dict(r)) for r in db.execute(grouped).mappings()), MappingProxyType(labels))
