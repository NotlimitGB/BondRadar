"""Forward calendar-day cashflows and persisted-evidence dirty-value marking."""
from datetime import date, timedelta
from decimal import localcontext
import re
from sqlalchemy import select
from app.models.bond_cashflow_event import BondCashflowEvent
from app.schemas.bond_dv01 import BondDv01View
from app.schemas.shadow_ledger import ShadowDailyPlanV1 as Plan, ShadowDailyAuthorizationV1 as Authorization, ShadowDailyApplyReceiptV1 as Receipt
from app.services.bond_dv01_service import BondDv01Service
from app.services import shadow_ledger_repository as repository


def validate_context(key, day):
    repository.require(type(day) is date and type(key) is str and re.fullmatch(r"[0-9a-f]{64}",key) is not None, "INPUT_INVALID")


def validate_cashflows(source):
    identities = [(r["bond_id"],r["event_type"]) for r in source]
    repository.require(len(identities) == len(set(identities)), "CASHFLOW_DUPLICATE")
    for row in source:
        repository.require(type(row["bond_id"]) is int and row["bond_id"] > 0 and
            type(row["event_date"]) is date and row["source"] == "moex", "CASHFLOW_EVIDENCE_INVALID")
        if row["event_type"] == "offer_redemption":
            continue
        repository.require(row["event_type"] in ("coupon","amortization","redemption"), "UNKNOWN_CASHFLOW_EVENT")
        repository.require(row["amount"] is not None, "CASHFLOW_AMOUNT_MISSING")
        repository.require(row["currency"] == "RUB" and repository.finite(row["amount"]), "CASHFLOW_EVIDENCE_INVALID")


def validate_mark(view, bond_id, day):
    repository.strict(view, BondDv01View)
    p = view.provenance
    repository.require(view.contract_version == "bond-dv01-v1" and view.pit_ready is False and
        (view.bond_id,view.as_of_date,view.market_source) == (bond_id,day,"moex"), "MARKET_EVIDENCE_INVALID")
    repository.require(view.availability.has_dirty_value is True and repository.finite(view.dirty_value_currency, positive=True), "DIRTY_VALUE_UNAVAILABLE")
    repository.require(view.currency_code == "RUB" and view.currency_state == p.currency_state == "verified" and
        view.nominal_state == p.nominal_state == "verified" and repository.finite(view.nominal_value,positive=True), "MARKET_EVIDENCE_INVALID")
    identity = (bond_id,view.market_snapshot_id,view.market_trade_date,"moex")
    repository.require(type(view.market_snapshot_id) is int and view.market_snapshot_id > 0 and
        type(view.market_trade_date) is date and type(view.market_age_days) is int and
        view.market_age_days == (day-view.market_trade_date).days and 0 <= view.market_age_days <= 7 and
        (p.market_identity.bond_id,p.market_identity.market_snapshot_id,p.market_identity.market_trade_date,p.market_identity.market_source) == identity and
        (p.modified_duration_market_identity.bond_id,p.modified_duration_market_identity.market_snapshot_id,
            p.modified_duration_market_identity.market_trade_date,p.modified_duration_market_identity.market_source) == identity and
        (p.market_snapshot_id,p.market_trade_date,p.market_source,p.as_of_date,p.max_market_age_days) ==
            (view.market_snapshot_id,view.market_trade_date,"moex",day,7) and
        type(p.security_master_profile_id) is int and p.security_master_profile_id > 0 and
        p.security_master_contract_version == "bond-security-master-v2" and view.price_basis == p.price_basis and
        view.price_basis in ("CLEAN_PRICE","PRICE_FALLBACK"), "MARKET_EVIDENCE_INVALID")


class ShadowDailyCycleService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def _plan(self, db, key, day):
        state = repository.read_state(db,key)
        run = state["run"]
        repository.require(run is not None, "RUN_NOT_FOUND")
        repository.require(run["horizon_days"] == 90 and run["planned_end_date"] == run["genesis_date"]+timedelta(days=90) and
            run["base_currency"] == "RUB" and run["market_source"] == "moex" and
            repository.finite(run["initial_capital_rub"],positive=True) and state["snapshots"], "SHADOW_HISTORY_INVALID")
        repository.require(day <= run["planned_end_date"], "HORIZON_EXCEEDED")
        existing = next((s for s in state["snapshots"] if s["as_of_date"] == day), None)
        if not existing:
            repository.require(run["status"] == "ACTIVE", "RUN_NOT_ACTIVE")
            latest = state["snapshots"][-1]
            repository.require(day > latest["as_of_date"], "DATE_NOT_FORWARD")
            repository.require(day == latest["as_of_date"]+timedelta(days=1), "DAILY_SEQUENCE_GAP")
        previous = next((s for s in state["snapshots"] if s["as_of_date"] == day-timedelta(days=1)), None)
        repository.require(previous is not None, "DATE_NOT_FORWARD")
        prev = repository.stored_snapshot(previous)
        repository.require(repository.audit(db,key,prev).status == "VERIFIED", "SHADOW_HISTORY_INVALID")
        repository.require(prev.nav_rub > 0, "PREVIOUS_NAV_ZERO")
        positions_before = sorted((p for p in state["positions"] if p["shadow_daily_snapshot_id"] == previous["id"]), key=lambda p:p["bond_id"])
        quantities = {p["bond_id"]:p["quantity"] for p in positions_before}
        active = [i for i,q in quantities.items() if q > 0]
        with db.no_autoflush:
            columns = [c for c in BondCashflowEvent.__table__.columns if c.name != "created_at"]
            source = [dict(r) for r in db.execute(select(*columns).where(BondCashflowEvent.bond_id.in_(active),
                BondCashflowEvent.event_date == day, BondCashflowEvent.source == "moex")).mappings()]
        order = {"coupon":0,"amortization":1,"redemption":2,"offer_redemption":3,"other":4}
        source.sort(key=lambda r:(order.get(r["event_type"],5),r["bond_id"],r["id"]))
        validate_cashflows(source)
        events, marks, positions, diagnostics = [], [], [], set()
        before_entries = [e for e in state["ledger"] if e["event_date"] <= prev.as_of_date]
        cash = prev.cash_rub
        flows = {i:repository.ZERO for i in quantities}
        with localcontext(repository.DECIMAL_CONTEXT), db.no_autoflush:
            for row in source:
                kind, i = row["event_type"], row["bond_id"]
                if kind == "offer_redemption":
                    diagnostics.add("OFFER_REDEMPTION_NOT_AUTO_EXERCISED")
                    continue
                amount = row["amount"] * quantities[i]
                delta = -quantities[i] if kind == "redemption" else 0
                events.append(repository.event(key,event_date=day,sequence_number=len(before_entries)+len(events)+1,
                    event_type=kind.upper(),bond_id=i,quantity_delta=delta,cash_delta_rub=amount,unit_amount_rub=row["amount"],
                    source_cashflow_event_id=row["id"],source_contract_version="bond-cashflow-event-v1",
                    source_fingerprint_sha256=repository.digest(row)))
                cash += amount
                flows[i] += amount
                quantities[i] += delta
            loader = BondDv01Service(db)
            for i in sorted(quantities):
                if quantities[i] == 0:
                    positions.append(repository.position(bond_id=i,position_status="REDEEMED",quantity=0,
                        market_value_rub=repository.ZERO,cashflow_rub_on_date=flows[i]))
                    continue
                view = loader.build_for_bond(i,day,market_source="moex",max_market_age_days=7)
                validate_mark(view,i,day)
                marks.append(view)
                if view.status != "READY":
                    diagnostics.add("DV01_STATUS_"+view.status)
                positions.append(repository.position(bond_id=i,position_status="ACTIVE",quantity=quantities[i],
                    market_snapshot_id=view.market_snapshot_id,market_trade_date=view.market_trade_date,market_age_days=view.market_age_days,
                    price_basis=view.price_basis,dirty_value_rub_per_bond=view.dirty_value_currency,
                    market_value_rub=quantities[i]*view.dirty_value_currency,cashflow_rub_on_date=flows[i],
                    source_dv01_contract_version=view.contract_version,source_security_master_profile_id=view.provenance.security_master_profile_id))
            market = sum((p.market_value_rub for p in positions),repository.ZERO)
            nav = cash+market
            repository.require(cash >= 0 and nav >= 0, "SHADOW_HISTORY_INVALID")
            input_hash = repository.digest(dict(run_key_sha256=key,as_of_date=day,previous_snapshot_sha256=prev.snapshot_sha256,
                previous_ledger=tuple(e["event_key_sha256"] for e in before_entries),quantities_before={p["bond_id"]:p["quantity"] for p in positions_before},
                cashflows=source,valuation_evidence=marks))
            snap = repository.snapshot(as_of_date=day,previous_snapshot_sha256=prev.snapshot_sha256,cash_rub=cash,
                market_value_rub=market,nav_rub=nav,daily_return=nav/prev.nav_rub-1,
                cumulative_return=nav/run["initial_capital_rub"]-1,active_position_count=sum(q>0 for q in quantities.values()),
                redeemed_position_count=sum(q==0 for q in quantities.values()),ledger_entry_count_to_date=len(before_entries)+len(events),
                applied_cashflow_count_for_day=len(events),input_state_sha256=input_hash)
        status, blockers = "EXECUTABLE", ()
        if existing:
            if repository.stored_snapshot(existing) == snap and repository.audit(db,key,snap).status == "VERIFIED":
                status = "IDEMPOTENT_NOOP"
            else:
                status, blockers = "BLOCKED", ("HISTORICAL_SOURCE_DRIFT",)
        return repository.signed_plan(Plan(status=status,run_key_sha256=key,as_of_date=day,
            current_shadow_db_state_sha256=repository.digest(dict(shadow_state=repository.state_hash(state),input_state_sha256=input_hash)),
            input_state_sha256=input_hash,events=tuple(events),positions=tuple(positions),snapshot=snap,
            valuation_evidence=tuple(marks),blockers=blockers,diagnostics=tuple(sorted(diagnostics))))

    def plan(self, *, run_key_sha256, as_of_date):
        try:
            validate_context(run_key_sha256,as_of_date)
            db = repository.fresh(self.session_factory)
        except Exception as exc:
            return Plan(status="BLOCKED",blockers=(str(exc) if str(exc) in repository.Blocker.__args__ else "INPUT_INVALID",))
        try:
            return self._plan(db,run_key_sha256,as_of_date)
        except Exception as exc:
            return Plan(status="BLOCKED",run_key_sha256=run_key_sha256,as_of_date=as_of_date,
                blockers=(str(exc) if str(exc) in repository.Blocker.__args__ else "INPUT_INVALID",))
        finally:
            db.close()

    def apply(self, *, reviewed_plan, authorization):
        try:
            repository.strict(reviewed_plan,Plan)
            repository.strict(authorization,Authorization)
            validate_context(reviewed_plan.run_key_sha256,reviewed_plan.as_of_date)
        except Exception:
            return Receipt(status="BLOCKED",blockers=("INPUT_INVALID",))
        return repository.execute_apply(self.session_factory,reviewed_plan,authorization,
            lambda db:self._plan(db,reviewed_plan.run_key_sha256,reviewed_plan.as_of_date),Receipt,daily=True)
