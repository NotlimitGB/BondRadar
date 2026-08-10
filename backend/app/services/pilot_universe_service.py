from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.bond import Bond
from app.models.bond_cashflow_event import BondCashflowEvent
from app.models.bond_market_snapshot import BondMarketSnapshot
from app.models.company import Company
from app.models.company_identity_profile import CompanyIdentityProfile
from app.schemas.pilot_universe import (
    PilotUniverseBondEvaluation,
    PilotUniverseBondSample,
    PilotUniverseEvaluationRequest,
    PilotUniverseEvaluationResult,
    PilotUniverseSummary,
)


PILOT_UNIVERSE_CONTRACT_VERSION = "pilot-universe-v1"
PLACEHOLDER_ISSUER_PREFIX = "Unknown issuer for "
SUPPORTED_ISSUER_ROLES = {"legal_issuer", "operating_company"}

IDENTITY_BLOCKER_CODES = (
    "ISSUER_COMPANY_MISSING",
    "ISSUER_PLACEHOLDER_NAME",
    "IDENTITY_PROFILE_MISSING",
    "IDENTITY_STATUS_NOT_VERIFIED",
    "IDENTITY_REVIEW_NOT_ACCEPTED",
    "ISSUER_INN_MISSING",
    "IDENTITY_PROFILE_INN_MISSING",
    "ISSUER_INN_MISMATCH",
    "ISSUER_ROLE_UNSUPPORTED",
)
LEGACY_TERMS_BLOCKER_CODES = (
    "CURRENCY_NOT_RUB",
    "ISIN_MISSING",
    "SECID_MISSING",
    "NOMINAL_MISSING",
    "NOMINAL_NON_POSITIVE",
    "COUPON_RATE_MISSING",
    "COUPON_RATE_NON_POSITIVE",
    "MATURITY_MISSING",
    "MATURITY_NOT_FUTURE",
    "FLOATING_COUPON_UNSUPPORTED",
    "SUBORDINATED_UNSUPPORTED",
    "PERPETUAL_UNSUPPORTED",
    "AMORTIZING_UNSUPPORTED",
    "OFFER_BOND_UNSUPPORTED",
)
MARKET_BLOCKER_CODES = (
    "MARKET_SNAPSHOT_MISSING",
    "MARKET_SNAPSHOT_STALE",
    "MARKET_SOURCE_NOT_MOEX",
    "EXECUTABLE_PRICE_MISSING",
    "YTM_MISSING",
    "DURATION_MISSING",
    "DURATION_NON_POSITIVE",
    "VOLUME_MISSING",
    "LIQUIDITY_MISSING",
    "SPREAD_TO_OFZ_MISSING",
)
CASHFLOW_BLOCKER_CODES = (
    "FUTURE_COUPON_MISSING",
    "MATURITY_REDEMPTION_MISSING",
    "CASHFLOW_ECONOMIC_EVENT_AMBIGUOUS",
    "UNEXPECTED_AMORTIZATION_EVENT",
    "UNEXPECTED_OFFER_REDEMPTION_EVENT",
)
SYSTEM_CAPABILITY_BLOCKERS = (
    "LOT_SIZE_NOT_MODELED",
    "TRADING_BOARD_NOT_MODELED",
    "COUPON_FREQUENCY_NOT_MODELED",
    "COUPON_FORMULA_NOT_MODELED",
    "OUTSTANDING_NOMINAL_NOT_MODELED",
    "LISTING_HISTORY_NOT_MODELED",
    "DEFAULT_DELISTING_HISTORY_NOT_MODELED",
    "TERMS_HISTORY_NOT_MODELED",
    "BID_ASK_NOT_MODELED",
    "EXECUTION_LEDGER_NOT_IMPLEMENTED",
    "FINANCIAL_SUFFICIENCY_GATE_NOT_IMPLEMENTED",
    "POINT_IN_TIME_DATASET_NOT_QUALIFIED",
    "BUY_HOLD_SELL_POLICY_NOT_COMPLETE",
    "PAPER_IDEMPOTENCY_NOT_QUALIFIED",
)


@dataclass(frozen=True)
class _CashflowGroup:
    event_date: date
    event_type: str
    source_count: int


class PilotUniverseService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def evaluate(
        self,
        request: PilotUniverseEvaluationRequest,
    ) -> PilotUniverseEvaluationResult:
        bond_rows = self.db.execute(self._bond_identity_statement()).mappings().all()
        market_rows = {
            int(row["bond_id"]): row
            for row in self.db.execute(self._latest_market_statement()).mappings()
        }
        cashflow_rows: dict[int, list[_CashflowGroup]] = defaultdict(list)
        for row in self.db.execute(self._cashflow_statement()).mappings():
            cashflow_rows[int(row["bond_id"])].append(
                _CashflowGroup(
                    event_date=row["event_date"],
                    event_type=str(row["event_type"]),
                    source_count=int(row["source_count"]),
                )
            )

        evaluations = [
            self._evaluate_bond(
                row,
                market=market_rows.get(int(row["bond_id"])),
                cashflows=cashflow_rows.get(int(row["bond_id"]), []),
                request=request,
            )
            for row in bond_rows
        ]
        summary = self._summary(evaluations, request=request)
        return PilotUniverseEvaluationResult(
            contract_version=PILOT_UNIVERSE_CONTRACT_VERSION,
            request=request,
            summary=summary,
            bond_evaluations=evaluations,
        )

    @staticmethod
    def _bond_identity_statement():
        return (
            select(
                Bond.id.label("bond_id"),
                Bond.company_id.label("company_id"),
                Bond.isin,
                Bond.secid,
                Bond.name.label("bond_name"),
                Bond.currency,
                Bond.nominal_value,
                Bond.coupon_rate,
                Bond.maturity_date,
                Bond.offer_date,
                Bond.is_floating_coupon,
                Bond.is_subordinated,
                Bond.is_perpetual,
                Bond.amortization,
                Company.id.label("joined_company_id"),
                Company.name.label("company_name"),
                Company.inn.label("company_inn"),
                CompanyIdentityProfile.id.label("identity_profile_id"),
                CompanyIdentityProfile.inn.label("identity_profile_inn"),
                CompanyIdentityProfile.identity_status,
                CompanyIdentityProfile.review_status,
                CompanyIdentityProfile.issuer_role,
            )
            .select_from(Bond)
            .outerjoin(Company, Company.id == Bond.company_id)
            .outerjoin(
                CompanyIdentityProfile,
                CompanyIdentityProfile.company_id == Company.id,
            )
            .order_by(Bond.id.asc())
        )

    @staticmethod
    def _latest_market_statement():
        source_priority = case((BondMarketSnapshot.source == "moex", 0), else_=1)
        ranked = (
            select(
                BondMarketSnapshot.id.label("snapshot_id"),
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
        return (
            select(
                BondMarketSnapshot.bond_id,
                BondMarketSnapshot.trade_date,
                BondMarketSnapshot.source,
                BondMarketSnapshot.price,
                BondMarketSnapshot.clean_price,
                BondMarketSnapshot.dirty_price,
                BondMarketSnapshot.nkd,
                BondMarketSnapshot.yield_to_maturity,
                BondMarketSnapshot.duration_years,
                BondMarketSnapshot.volume,
                BondMarketSnapshot.liquidity_score,
                BondMarketSnapshot.spread_to_ofz,
            )
            .join(ranked, ranked.c.snapshot_id == BondMarketSnapshot.id)
            .where(ranked.c.row_number == 1)
            .order_by(BondMarketSnapshot.bond_id.asc())
        )

    @staticmethod
    def _cashflow_statement():
        return (
            select(
                BondCashflowEvent.bond_id,
                BondCashflowEvent.event_date,
                BondCashflowEvent.event_type,
                func.count(func.distinct(BondCashflowEvent.source)).label(
                    "source_count"
                ),
            )
            .group_by(
                BondCashflowEvent.bond_id,
                BondCashflowEvent.event_date,
                BondCashflowEvent.event_type,
            )
            .order_by(
                BondCashflowEvent.bond_id.asc(),
                BondCashflowEvent.event_date.asc(),
                BondCashflowEvent.event_type.asc(),
            )
        )

    def _evaluate_bond(
        self,
        row: Any,
        *,
        market: Any | None,
        cashflows: list[_CashflowGroup],
        request: PilotUniverseEvaluationRequest,
    ) -> PilotUniverseBondEvaluation:
        identity_blockers = self._identity_blockers(row)
        terms_blockers = self._legacy_terms_blockers(row, request.as_of_date)
        market_blockers = self._market_blockers(
            market,
            required_market_trade_date=request.required_market_trade_date,
        )
        cashflow_gate, cashflow_blockers = self._cashflow_result(
            cashflows,
            maturity_date=row["maturity_date"],
            as_of_date=request.as_of_date,
        )
        identity_gate = "FAIL" if identity_blockers else "PASS"
        legacy_terms_gate = "FAIL" if terms_blockers else "PASS"
        market_gate = "FAIL" if market_blockers else "PASS"
        candidate = (
            identity_gate == "PASS"
            and legacy_terms_gate == "PASS"
            and market_gate == "PASS"
            and cashflow_gate == "PASS"
        )
        return PilotUniverseBondEvaluation(
            bond_id=int(row["bond_id"]),
            isin=row["isin"],
            secid=row["secid"],
            bond_name=str(row["bond_name"]),
            company_id=int(row["company_id"]),
            company_name=row["company_name"],
            identity_gate=identity_gate,
            identity_blockers=identity_blockers,
            legacy_terms_gate=legacy_terms_gate,
            legacy_terms_blockers=terms_blockers,
            market_gate=market_gate,
            market_blockers=market_blockers,
            observed_cashflow_gate=cashflow_gate,
            cashflow_blockers=cashflow_blockers,
            pre_pilot_data_candidate=candidate,
            credit_gate="NOT_EVALUATED",
            execution_gate="NOT_EVALUATED",
            final_pilot_eligibility=False,
        )

    @staticmethod
    def _identity_blockers(row: Any) -> list[str]:
        blockers: list[str] = []
        company_exists = row["joined_company_id"] is not None
        profile_exists = row["identity_profile_id"] is not None
        company_inn = PilotUniverseService._clean(row["company_inn"])
        profile_inn = PilotUniverseService._clean(row["identity_profile_inn"])
        if not company_exists:
            blockers.append("ISSUER_COMPANY_MISSING")
        if company_exists and str(row["company_name"] or "").startswith(
            PLACEHOLDER_ISSUER_PREFIX
        ):
            blockers.append("ISSUER_PLACEHOLDER_NAME")
        if not profile_exists:
            blockers.append("IDENTITY_PROFILE_MISSING")
        if profile_exists and row["identity_status"] != "verified":
            blockers.append("IDENTITY_STATUS_NOT_VERIFIED")
        if profile_exists and row["review_status"] != "accepted":
            blockers.append("IDENTITY_REVIEW_NOT_ACCEPTED")
        if company_inn is None:
            blockers.append("ISSUER_INN_MISSING")
        if profile_inn is None:
            blockers.append("IDENTITY_PROFILE_INN_MISSING")
        if company_inn is not None and profile_inn is not None and company_inn != profile_inn:
            blockers.append("ISSUER_INN_MISMATCH")
        if profile_exists and row["issuer_role"] not in SUPPORTED_ISSUER_ROLES:
            blockers.append("ISSUER_ROLE_UNSUPPORTED")
        return PilotUniverseService._ordered(blockers, IDENTITY_BLOCKER_CODES)

    @staticmethod
    def _legacy_terms_blockers(row: Any, as_of_date: date) -> list[str]:
        blockers: list[str] = []
        nominal: Decimal | None = row["nominal_value"]
        coupon_rate: Decimal | None = row["coupon_rate"]
        maturity_date: date | None = row["maturity_date"]
        if row["currency"] != "RUB":
            blockers.append("CURRENCY_NOT_RUB")
        if PilotUniverseService._clean(row["isin"]) is None:
            blockers.append("ISIN_MISSING")
        if PilotUniverseService._clean(row["secid"]) is None:
            blockers.append("SECID_MISSING")
        if nominal is None:
            blockers.append("NOMINAL_MISSING")
        elif nominal <= 0:
            blockers.append("NOMINAL_NON_POSITIVE")
        if coupon_rate is None:
            blockers.append("COUPON_RATE_MISSING")
        elif coupon_rate <= 0:
            blockers.append("COUPON_RATE_NON_POSITIVE")
        if maturity_date is None:
            blockers.append("MATURITY_MISSING")
        elif maturity_date <= as_of_date:
            blockers.append("MATURITY_NOT_FUTURE")
        if row["is_floating_coupon"] is not False:
            blockers.append("FLOATING_COUPON_UNSUPPORTED")
        if row["is_subordinated"] is not False:
            blockers.append("SUBORDINATED_UNSUPPORTED")
        if row["is_perpetual"] is not False:
            blockers.append("PERPETUAL_UNSUPPORTED")
        if row["amortization"] is not False:
            blockers.append("AMORTIZING_UNSUPPORTED")
        if row["offer_date"] is not None:
            blockers.append("OFFER_BOND_UNSUPPORTED")
        return PilotUniverseService._ordered(blockers, LEGACY_TERMS_BLOCKER_CODES)

    @staticmethod
    def _market_blockers(
        market: Any | None,
        *,
        required_market_trade_date: date,
    ) -> list[str]:
        if market is None:
            return ["MARKET_SNAPSHOT_MISSING"]
        blockers: list[str] = []
        if market["trade_date"] < required_market_trade_date:
            blockers.append("MARKET_SNAPSHOT_STALE")
        if market["source"] != "moex":
            blockers.append("MARKET_SOURCE_NOT_MOEX")
        if market["dirty_price"] is None and not (
            market["clean_price"] is not None and market["nkd"] is not None
        ):
            blockers.append("EXECUTABLE_PRICE_MISSING")
        if market["yield_to_maturity"] is None:
            blockers.append("YTM_MISSING")
        duration = market["duration_years"]
        if duration is None:
            blockers.append("DURATION_MISSING")
        elif duration <= 0:
            blockers.append("DURATION_NON_POSITIVE")
        if market["volume"] is None or market["volume"] < 0:
            blockers.append("VOLUME_MISSING")
        if market["liquidity_score"] is None:
            blockers.append("LIQUIDITY_MISSING")
        if market["spread_to_ofz"] is None:
            blockers.append("SPREAD_TO_OFZ_MISSING")
        return PilotUniverseService._ordered(blockers, MARKET_BLOCKER_CODES)

    @staticmethod
    def _cashflow_result(
        cashflows: list[_CashflowGroup],
        *,
        maturity_date: date | None,
        as_of_date: date,
    ) -> tuple[str, list[str]]:
        blockers: list[str] = []
        if not any(
            event.event_type == "coupon" and event.event_date > as_of_date
            for event in cashflows
        ):
            blockers.append("FUTURE_COUPON_MISSING")
        if maturity_date is None or not any(
            event.event_type == "redemption" and event.event_date == maturity_date
            for event in cashflows
        ):
            blockers.append("MATURITY_REDEMPTION_MISSING")
        if any(event.source_count > 1 for event in cashflows):
            blockers.append("CASHFLOW_ECONOMIC_EVENT_AMBIGUOUS")
        if any(
            event.event_type == "amortization" and event.event_date > as_of_date
            for event in cashflows
        ):
            blockers.append("UNEXPECTED_AMORTIZATION_EVENT")
        if any(
            event.event_type == "offer_redemption" and event.event_date > as_of_date
            for event in cashflows
        ):
            blockers.append("UNEXPECTED_OFFER_REDEMPTION_EVENT")
        ordered = PilotUniverseService._ordered(blockers, CASHFLOW_BLOCKER_CODES)
        hard_failures = {
            "CASHFLOW_ECONOMIC_EVENT_AMBIGUOUS",
            "UNEXPECTED_AMORTIZATION_EVENT",
            "UNEXPECTED_OFFER_REDEMPTION_EVENT",
        }
        if any(code in hard_failures for code in ordered):
            return "FAIL", ordered
        if ordered:
            return "NOT_PROVEN", ordered
        return "PASS", []

    @staticmethod
    def _summary(
        evaluations: list[PilotUniverseBondEvaluation],
        *,
        request: PilotUniverseEvaluationRequest,
    ) -> PilotUniverseSummary:
        candidates = [row for row in evaluations if row.pre_pilot_data_candidate]
        excluded = [row for row in evaluations if not row.pre_pilot_data_candidate]
        return PilotUniverseSummary(
            contract_version=PILOT_UNIVERSE_CONTRACT_VERSION,
            as_of_date=request.as_of_date,
            required_market_trade_date=request.required_market_trade_date,
            bonds_total=len(evaluations),
            identity_pass_count=sum(row.identity_gate == "PASS" for row in evaluations),
            identity_fail_count=sum(row.identity_gate == "FAIL" for row in evaluations),
            legacy_terms_pass_count=sum(
                row.legacy_terms_gate == "PASS" for row in evaluations
            ),
            legacy_terms_fail_count=sum(
                row.legacy_terms_gate == "FAIL" for row in evaluations
            ),
            market_pass_count=sum(row.market_gate == "PASS" for row in evaluations),
            market_fail_count=sum(row.market_gate == "FAIL" for row in evaluations),
            observed_cashflow_pass_count=sum(
                row.observed_cashflow_gate == "PASS" for row in evaluations
            ),
            observed_cashflow_fail_count=sum(
                row.observed_cashflow_gate == "FAIL" for row in evaluations
            ),
            observed_cashflow_not_proven_count=sum(
                row.observed_cashflow_gate == "NOT_PROVEN" for row in evaluations
            ),
            pre_pilot_data_candidate_count=len(candidates),
            final_pilot_eligible_count=0,
            final_pilot_eligibility_evaluated=False,
            system_capability_blockers=list(SYSTEM_CAPABILITY_BLOCKERS),
            identity_blocker_counts=PilotUniverseService._blocker_counts(
                evaluations, "identity_blockers", IDENTITY_BLOCKER_CODES
            ),
            legacy_terms_blocker_counts=PilotUniverseService._blocker_counts(
                evaluations, "legacy_terms_blockers", LEGACY_TERMS_BLOCKER_CODES
            ),
            market_blocker_counts=PilotUniverseService._blocker_counts(
                evaluations, "market_blockers", MARKET_BLOCKER_CODES
            ),
            cashflow_blocker_counts=PilotUniverseService._blocker_counts(
                evaluations, "cashflow_blockers", CASHFLOW_BLOCKER_CODES
            ),
            pre_pilot_candidate_samples=[
                PilotUniverseService._sample(row)
                for row in candidates[: request.sample_limit]
            ],
            excluded_bond_samples=[
                PilotUniverseService._sample(row)
                for row in excluded[: request.sample_limit]
            ],
        )

    @staticmethod
    def _sample(row: PilotUniverseBondEvaluation) -> PilotUniverseBondSample:
        return PilotUniverseBondSample(
            bond_id=row.bond_id,
            isin=row.isin,
            secid=row.secid,
            identity_gate=row.identity_gate,
            identity_blockers=row.identity_blockers,
            legacy_terms_gate=row.legacy_terms_gate,
            legacy_terms_blockers=row.legacy_terms_blockers,
            market_gate=row.market_gate,
            market_blockers=row.market_blockers,
            observed_cashflow_gate=row.observed_cashflow_gate,
            cashflow_blockers=row.cashflow_blockers,
            pre_pilot_data_candidate=row.pre_pilot_data_candidate,
        )

    @staticmethod
    def _blocker_counts(
        evaluations: list[PilotUniverseBondEvaluation],
        field: str,
        ordered_codes: tuple[str, ...],
    ) -> dict[str, int]:
        return {
            code: sum(code in getattr(row, field) for row in evaluations)
            for code in ordered_codes
        }

    @staticmethod
    def _ordered(values: list[str], order: tuple[str, ...]) -> list[str]:
        selected = set(values)
        return [code for code in order if code in selected]

    @staticmethod
    def _clean(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None
