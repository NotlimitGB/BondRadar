from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import case, func, select, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.models.bond import Bond  # noqa: E402
from app.models.bond_cashflow_event import BondCashflowEvent  # noqa: E402
from app.models.bond_market_snapshot import BondMarketSnapshot  # noqa: E402
from app.models.bond_risk_assessment import BondRiskAssessment  # noqa: E402
from app.models.bond_score import BondScore  # noqa: E402
from app.models.company import Company  # noqa: E402
from app.models.company_credit_health_snapshot import (  # noqa: E402
    CompanyCreditHealthSnapshot,
)
from app.models.company_score import CompanyScore  # noqa: E402
from app.models.controlled_financial_statement_value import (  # noqa: E402
    ControlledFinancialStatementValue,
)
from app.models.financial_report import FinancialReport  # noqa: E402
from app.models.financial_report_source_document import (  # noqa: E402
    FinancialReportSourceDocument,
)


SCHEMA = "bondradar.agent_data_readiness_audit.v1"
PLACEHOLDER_ISSUER_PREFIX = "Unknown issuer for "
INTERSECTION_LABELS = [
    "DIAGNOSTIC_INTERSECTION_ONLY",
    "NOT_FINAL_AGENT_ELIGIBILITY",
]
MARKET_CORE_FIELDS = (
    "price",
    "yield_to_maturity",
    "duration_years",
    "volume",
    "liquidity_score",
)
CONTROLLED_METRIC_KEYS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "total_revenue"),
    "operating_profit_or_ebitda": ("operating_profit", "ebitda"),
    "net_profit": ("net_profit", "profit_for_the_year"),
    "cash": ("cash_and_cash_equivalents",),
    "total_debt": ("total_debt",),
    "short_term_debt": ("short_term_debt",),
    "long_term_debt": ("long_term_debt",),
    "interest_expense": ("interest_expense",),
    "operating_cash_flow": (
        "operating_cash_flow",
        "net_cash_from_operating_activities",
    ),
    "equity": ("equity", "total_equity"),
    "assets": ("total_assets",),
}
LEGACY_REPORT_FIELDS = (
    "revenue",
    "ebitda",
    "net_profit",
    "cash",
    "total_debt",
    "short_term_debt",
    "interest_expense",
    "operating_cash_flow",
    "equity",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Produce a strictly read-only BondRadar agent data readiness audit.",
    )
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, default=None)
    return parser.parse_args(argv)


def _ranked_latest_market():
    source_priority = case((BondMarketSnapshot.source == "moex", 0), else_=1)
    return (
        select(
            BondMarketSnapshot.bond_id.label("bond_id"),
            BondMarketSnapshot.trade_date.label("trade_date"),
            BondMarketSnapshot.price.label("price"),
            BondMarketSnapshot.yield_to_maturity.label("yield_to_maturity"),
            BondMarketSnapshot.duration_years.label("duration_years"),
            BondMarketSnapshot.volume.label("volume"),
            BondMarketSnapshot.liquidity_score.label("liquidity_score"),
            BondMarketSnapshot.nkd.label("nkd"),
            BondMarketSnapshot.spread_to_ofz.label("spread_to_ofz"),
            func.row_number()
            .over(
                partition_by=BondMarketSnapshot.bond_id,
                order_by=(
                    BondMarketSnapshot.trade_date.desc(),
                    source_priority.asc(),
                    BondMarketSnapshot.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .subquery()
    )


def _ranked_latest_risk():
    return (
        select(
            BondRiskAssessment.bond_id.label("bond_id"),
            func.row_number()
            .over(
                partition_by=BondRiskAssessment.bond_id,
                order_by=(
                    BondRiskAssessment.as_of_date.desc(),
                    BondRiskAssessment.created_at.desc(),
                    BondRiskAssessment.id.desc(),
                ),
            )
            .label("row_number"),
        )
        .subquery()
    )


def _age_bucket(trade_date: date, as_of_date: date) -> str:
    age = (as_of_date - trade_date).days
    if age < 0:
        return "future"
    if age <= 1:
        return "age_0_1_days"
    if age <= 5:
        return "age_2_5_days"
    if age <= 30:
        return "age_6_30_days"
    if age <= 90:
        return "age_31_90_days"
    return "age_over_90_days"


def _ratio(numerator: int, denominator: int) -> float:
    if denominator == 0:
        return 0.0
    return round(numerator / denominator, 6)


def _bond_and_issuer_coverage(db: Session) -> tuple[dict[str, Any], dict[str, Any], dict[int, int]]:
    companies_total = int(db.scalar(select(func.count()).select_from(Company)) or 0)
    rows = db.execute(
        select(Bond.id, Bond.company_id, Company.name)
        .outerjoin(Company, Company.id == Bond.company_id)
        .order_by(Bond.id.asc())
    ).all()
    resolved: dict[int, int] = {}
    placeholder_count = 0
    missing_company_count = 0
    for bond_id, company_id, company_name in rows:
        if company_name is None:
            missing_company_count += 1
        elif company_name.startswith(PLACEHOLDER_ISSUER_PREFIX):
            placeholder_count += 1
        else:
            resolved[int(bond_id)] = int(company_id)
    bonds_total = len(rows)
    unresolved = placeholder_count + missing_company_count
    universe = {
        "companies_total": companies_total,
        "bonds_total": bonds_total,
        "audit_scope": "all_persisted_bonds",
        "final_investable_universe_policy_applied": False,
    }
    issuer_identity = {
        "placeholder_pattern": PLACEHOLDER_ISSUER_PREFIX,
        "placeholder_issuer_bonds": placeholder_count,
        "missing_company_bonds": missing_company_count,
        "unresolved_issuer_bonds": unresolved,
        "resolved_issuer_bonds": len(resolved),
        "resolved_issuer_ratio": _ratio(len(resolved), bonds_total),
        "fuzzy_identity_matching_used": False,
    }
    return universe, issuer_identity, resolved


def _market_coverage(
    db: Session,
    *,
    bonds_total: int,
    as_of_date: date,
) -> tuple[dict[str, Any], set[int], set[int]]:
    snapshots_total = int(
        db.scalar(select(func.count()).select_from(BondMarketSnapshot)) or 0
    )
    ranked = _ranked_latest_market()
    rows = db.execute(
        select(
            ranked.c.bond_id,
            ranked.c.trade_date,
            ranked.c.price,
            ranked.c.yield_to_maturity,
            ranked.c.duration_years,
            ranked.c.volume,
            ranked.c.liquidity_score,
            ranked.c.nkd,
            ranked.c.spread_to_ofz,
        )
        .where(ranked.c.row_number == 1)
        .order_by(ranked.c.bond_id.asc())
    ).all()
    field_counts = {
        "price": 0,
        "ytm": 0,
        "duration": 0,
        "volume": 0,
        "liquidity": 0,
        "nkd": 0,
        "spread": 0,
    }
    age_buckets = {
        "age_0_1_days": 0,
        "age_2_5_days": 0,
        "age_6_30_days": 0,
        "age_31_90_days": 0,
        "age_over_90_days": 0,
        "future": 0,
        "missing": max(0, bonds_total - len(rows)),
    }
    market_bond_ids: set[int] = set()
    core_bond_ids: set[int] = set()
    market_dates: list[date] = []
    for row in rows:
        mapping = row._mapping
        bond_id = int(mapping["bond_id"])
        market_bond_ids.add(bond_id)
        market_dates.append(mapping["trade_date"])
        age_buckets[_age_bucket(mapping["trade_date"], as_of_date)] += 1
        for output_key, column_key in (
            ("price", "price"),
            ("ytm", "yield_to_maturity"),
            ("duration", "duration_years"),
            ("volume", "volume"),
            ("liquidity", "liquidity_score"),
            ("nkd", "nkd"),
            ("spread", "spread_to_ofz"),
        ):
            if mapping[column_key] is not None:
                field_counts[output_key] += 1
        if all(mapping[field] is not None for field in MARKET_CORE_FIELDS):
            core_bond_ids.add(bond_id)
    market = {
        "latest_row_order": [
            "trade_date_desc",
            "moex_source_priority",
            "id_desc",
        ],
        "market_snapshots_total": snapshots_total,
        "bonds_with_any_market": len(market_bond_ids),
        "bonds_without_market": max(0, bonds_total - len(market_bond_ids)),
        "latest_market_min_date": min(market_dates).isoformat() if market_dates else "",
        "latest_market_max_date": max(market_dates).isoformat() if market_dates else "",
        "latest_market_age_buckets": age_buckets,
        "freshness_policy_kind": "diagnostic_calendar_age_only",
        "bonds_latest_price_present": field_counts["price"],
        "bonds_latest_ytm_present": field_counts["ytm"],
        "bonds_latest_duration_present": field_counts["duration"],
        "bonds_latest_volume_present": field_counts["volume"],
        "bonds_latest_liquidity_present": field_counts["liquidity"],
        "bonds_latest_nkd_present": field_counts["nkd"],
        "bonds_latest_spread_present": field_counts["spread"],
        "market_core_fields": list(MARKET_CORE_FIELDS),
        "bonds_market_core_complete": len(core_bond_ids),
    }
    return market, market_bond_ids, core_bond_ids


def _bond_terms_and_cashflows(
    db: Session,
    *,
    bonds_total: int,
) -> tuple[dict[str, int], dict[str, int], set[int]]:
    term_row = db.execute(
        select(
            func.count(Bond.id).filter(Bond.isin.is_not(None)),
            func.count(Bond.id).filter(Bond.secid.is_not(None)),
            func.count(Bond.id).filter(Bond.nominal_value.is_not(None)),
            func.count(Bond.id).filter(Bond.coupon_rate.is_not(None)),
            func.count(Bond.id).filter(Bond.maturity_date.is_not(None)),
            func.count(Bond.id).filter(Bond.offer_date.is_not(None)),
            func.count(Bond.id).filter(Bond.is_floating_coupon.is_(True)),
            func.count(Bond.id).filter(Bond.is_subordinated.is_(True)),
            func.count(Bond.id).filter(Bond.is_perpetual.is_(True)),
            func.count(Bond.id).filter(Bond.amortization.is_(True)),
        )
    ).one()
    bond_terms = dict(
        zip(
            (
                "bonds_isin_present",
                "bonds_secid_present",
                "bonds_nominal_present",
                "bonds_coupon_present",
                "bonds_maturity_present",
                "bonds_offer_present",
                "floating_coupon_count",
                "subordinated_count",
                "perpetual_count",
                "amortizing_count",
            ),
            (int(value or 0) for value in term_row),
            strict=True,
        )
    )
    cashflow_bond_ids = {
        int(value)
        for value in db.scalars(
            select(BondCashflowEvent.bond_id).distinct().order_by(BondCashflowEvent.bond_id)
        )
    }
    cashflows = {
        "cashflow_events_total": int(
            db.scalar(select(func.count()).select_from(BondCashflowEvent)) or 0
        ),
        "bonds_with_cashflows": len(cashflow_bond_ids),
        "bonds_without_cashflows": max(0, bonds_total - len(cashflow_bond_ids)),
    }
    return bond_terms, cashflows, cashflow_bond_ids


def _credit_coverage(db: Session, *, bonds_total: int) -> tuple[dict[str, int | list[str]], set[int]]:
    latest_risk = _ranked_latest_risk()
    latest_risk_ids = {
        int(value)
        for value in db.scalars(
            select(latest_risk.c.bond_id)
            .where(latest_risk.c.row_number == 1)
            .order_by(latest_risk.c.bond_id.asc())
        )
    }
    row = db.execute(
        select(
            select(func.count(func.distinct(CompanyCreditHealthSnapshot.company_id))).scalar_subquery(),
            select(func.count(func.distinct(BondRiskAssessment.bond_id))).scalar_subquery(),
            select(func.count()).select_from(BondScore).scalar_subquery(),
            select(func.count()).select_from(CompanyScore).scalar_subquery(),
        )
    ).one()
    credit = {
        "latest_risk_order": ["as_of_date_desc", "created_at_desc", "id_desc"],
        "companies_with_credit_health": int(row[0] or 0),
        "bonds_with_risk_assessment": int(row[1] or 0),
        "bond_scores_count": int(row[2] or 0),
        "company_scores_count": int(row[3] or 0),
        "bonds_with_latest_risk": len(latest_risk_ids),
        "bonds_without_latest_risk": max(0, bonds_total - len(latest_risk_ids)),
    }
    return credit, latest_risk_ids


def _financial_coverage(db: Session) -> tuple[dict[str, Any], set[int]]:
    report_company_ids = {
        int(value)
        for value in db.scalars(
            select(FinancialReport.company_id).distinct().order_by(FinancialReport.company_id)
        )
    }
    counts = db.execute(
        select(
            select(func.count()).select_from(FinancialReport).scalar_subquery(),
            select(func.count()).select_from(FinancialReportSourceDocument).scalar_subquery(),
            select(func.count()).select_from(ControlledFinancialStatementValue).scalar_subquery(),
            select(func.count(func.distinct(ControlledFinancialStatementValue.company_id))).scalar_subquery(),
        )
    ).one()
    legacy_row = db.execute(
        select(
            *[
                func.count(func.distinct(FinancialReport.company_id)).filter(
                    getattr(FinancialReport, field).is_not(None)
                )
                for field in LEGACY_REPORT_FIELDS
            ]
        )
    ).one()
    legacy_metric_coverage = {
        field: int(value or 0)
        for field, value in zip(LEGACY_REPORT_FIELDS, legacy_row, strict=True)
    }
    exact_keys = sorted({key for keys in CONTROLLED_METRIC_KEYS.values() for key in keys})
    controlled_rows = db.execute(
        select(
            ControlledFinancialStatementValue.metric_key,
            ControlledFinancialStatementValue.company_id,
        )
        .where(ControlledFinancialStatementValue.metric_key.in_(exact_keys))
        .distinct()
        .order_by(
            ControlledFinancialStatementValue.metric_key.asc(),
            ControlledFinancialStatementValue.company_id.asc(),
        )
    ).all()
    entities_by_key: dict[str, set[str]] = {key: set() for key in exact_keys}
    for metric_key, company_id in controlled_rows:
        entities_by_key[str(metric_key)].add(str(company_id))
    controlled_metric_coverage = {}
    for concept, keys in CONTROLLED_METRIC_KEYS.items():
        entities = set().union(*(entities_by_key[key] for key in keys))
        controlled_metric_coverage[concept] = {
            "status": "MEASURABLE",
            "exact_metric_keys": list(keys),
            "controlled_entity_count": len(entities),
        }
    financial = {
        "financial_reports_count": int(counts[0] or 0),
        "companies_with_financial_reports": len(report_company_ids),
        "source_documents_count": int(counts[1] or 0),
        "controlled_financial_rows_count": int(counts[2] or 0),
        "controlled_financial_entities_count": int(counts[3] or 0),
        "controlled_company_linkage": "NOT_MEASURABLE",
        "controlled_company_linkage_reason": "string_company_id_without_company_foreign_key",
        "legacy_financial_report_company_metric_coverage": legacy_metric_coverage,
        "controlled_metric_coverage": controlled_metric_coverage,
        "fuzzy_metric_matching_used": False,
    }
    return financial, report_company_ids


def build_agent_data_readiness_audit(
    db: Session,
    *,
    as_of_date: date | None = None,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    diagnostic_date = as_of_date or datetime.now(timezone.utc).date()
    generated = generated_at or datetime.now(timezone.utc)
    if generated.tzinfo is None:
        generated = generated.replace(tzinfo=timezone.utc)

    universe, issuer_identity, resolved = _bond_and_issuer_coverage(db)
    market, market_ids, market_core_ids = _market_coverage(
        db,
        bonds_total=universe["bonds_total"],
        as_of_date=diagnostic_date,
    )
    bond_terms, cashflows, _cashflow_ids = _bond_terms_and_cashflows(
        db,
        bonds_total=universe["bonds_total"],
    )
    credit, latest_risk_ids = _credit_coverage(
        db,
        bonds_total=universe["bonds_total"],
    )
    financial, report_company_ids = _financial_coverage(db)

    resolved_ids = set(resolved)
    resolved_market = resolved_ids & market_ids
    resolved_market_core = resolved_ids & market_core_ids
    resolved_market_risk = resolved_market & latest_risk_ids
    resolved_market_risk_report = {
        bond_id
        for bond_id in resolved_market_risk
        if resolved[bond_id] in report_company_ids
    }
    intersections = {
        "labels": INTERSECTION_LABELS,
        "bonds_with_resolved_issuer_and_market": len(resolved_market),
        "bonds_with_resolved_issuer_and_market_core": len(resolved_market_core),
        "bonds_with_resolved_issuer_market_and_risk": len(resolved_market_risk),
        "bonds_with_resolved_issuer_market_risk_and_financial_report": len(
            resolved_market_risk_report
        ),
        "bonds_with_resolved_issuer_market_risk_and_controlled_financials": "NOT_MEASURABLE",
    }
    return {
        "schema": SCHEMA,
        "generated_at": generated.astimezone(timezone.utc).isoformat(),
        "as_of_date": diagnostic_date.isoformat(),
        "database_dialect": db.get_bind().dialect.name,
        "universe": universe,
        "issuer_identity": issuer_identity,
        "market": market,
        "bond_terms": bond_terms,
        "cashflows": cashflows,
        "credit": credit,
        "financial": financial,
        "intersections": intersections,
        "readiness": {
            "audit_kind": "coverage_and_readiness_diagnostics",
            "coverage_audit_completed": True,
            "final_agent_eligibility_decided": False,
            "paper_pilot_started": False,
            "domain_database_mutation_executed": False,
            "network_used": False,
            "moex_called": False,
            "recalculation_executed": False,
        },
    }


def enforce_read_only_transaction(db: Session) -> bool:
    if db.get_bind().dialect.name != "postgresql":
        return False
    db.execute(text("SET TRANSACTION READ ONLY"))
    value = db.execute(text("SHOW transaction_read_only")).scalar_one()
    if str(value).lower() not in {"on", "true", "1"}:
        raise RuntimeError("read_only_transaction_not_enforced")
    return True


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# BondRadar Agent Data Readiness Audit",
        "",
        f"Schema: `{report['schema']}`",
        f"Generated at: `{report['generated_at']}`",
        f"Diagnostic date: `{report['as_of_date']}`",
        f"Database dialect: `{report['database_dialect']}`",
        "",
    ]
    for section in (
        "universe",
        "issuer_identity",
        "market",
        "bond_terms",
        "cashflows",
        "credit",
        "financial",
        "intersections",
        "readiness",
    ):
        lines.extend((f"## {section.replace('_', ' ').title()}", "", "```json"))
        lines.append(json.dumps(report[section], ensure_ascii=False, indent=2, sort_keys=True))
        lines.extend(("```", ""))
    return "\n".join(lines)


def serialize_report(report: dict[str, Any], output_format: str) -> str:
    if output_format == "markdown":
        return render_markdown(report)
    return json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        from app.db.session import SessionLocal

        with SessionLocal() as db:
            try:
                enforce_read_only_transaction(db)
                report = build_agent_data_readiness_audit(db)
            finally:
                db.rollback()
        rendered = serialize_report(report, args.format)
        if args.output is None:
            sys.stdout.write(rendered)
        else:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        return 0
    except Exception:
        failure = {
            "schema": SCHEMA,
            "status": "failed",
            "error": "agent_data_readiness_audit_failed",
        }
        sys.stderr.write(json.dumps(failure, sort_keys=True) + "\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
