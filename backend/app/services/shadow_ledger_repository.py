"""Shadow-only transaction ownership, canonical evidence and read-only audits."""
from datetime import date, datetime
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import re
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.models.shadow_test_run import ShadowTestRun
from app.models.shadow_ledger_entry import ShadowLedgerEntry
from app.models.shadow_daily_snapshot import ShadowDailySnapshot
from app.models.shadow_daily_position_snapshot import ShadowDailyPositionSnapshot
from app.schemas.shadow_ledger import Blocker, LedgerEvent, DailyPosition, DailySnapshot, ShadowAudit

TABLES = (ShadowTestRun, ShadowLedgerEntry, ShadowDailySnapshot, ShadowDailyPositionSnapshot)
DECIMAL_CONTEXT = Context(prec=28, rounding=ROUND_HALF_EVEN)
ZERO = Decimal("0")
PROVENANCE_VERSIONS = (
    "unified-candidate-v1", "investment-evaluation-batch-v1", "risk-engine-policy-v1",
    "portfolio-strategy-v1", "portfolio-strategy-policy-v1", "shadow-execution-plan-v1",
    "shadow-execution-policy-v1", "bond-dv01-v1", "bond-security-master-v2",
    "shadow-ledger-v1", "shadow-daily-mark-v1",
)


def canonical(value):
    if isinstance(value, BaseModel):
        return canonical(value.model_dump())
    if type(value) is Decimal:
        require(value.is_finite(), "INPUT_INVALID")
        # Numeric equality, including trailing zero representations, is canonical.
        if not value:
            return "0"
        return format(value, "f").rstrip("0").rstrip(".") if value.as_tuple().exponent < 0 else format(value, "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        forbidden = {"password", "token", "access_token", "refresh_token", "api_key", "authorization", "headers", "cookies", "secret", "connection_string"}
        require(not any(str(k).lower() in forbidden for k in value), "INPUT_INVALID")
        technical = {"created_at", "updated_at", "ingested_at", "ingestion_timestamp"}
        return {str(k): canonical(v) for k, v in value.items() if str(k).lower() not in technical}
    if isinstance(value, (tuple, list)):
        return [canonical(v) for v in value]
    return value


def digest(value):
    encoded = json.dumps(canonical(value), sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("ascii")).hexdigest()


def require(condition, code):
    if not condition:
        raise ValueError(code)


def strict(value, cls):
    require(type(value) is cls, "INPUT_INVALID")
    try:
        cls.model_validate(value.model_dump(), strict=True)
        rebuilt = cls.model_validate_json(value.model_dump_json())
        require(rebuilt.model_dump() == value.model_dump(), "INPUT_INVALID")
        # Revalidation must not conceal coercion introduced by model_copy.
        def original(obj):
            if isinstance(obj, BaseModel):
                fields = type(obj).model_fields
                require(set(obj.__dict__) <= set(fields), "INPUT_INVALID")
                for name in fields:
                    if name == "pit_ready":
                        require(getattr(obj, name) is False, "INPUT_INVALID")
                    original(getattr(obj, name))
            elif isinstance(obj, (tuple, list)):
                for item in obj:
                    original(item)
        original(value)
    except Exception:
        raise ValueError("INPUT_INVALID") from None


def finite(value, *, positive=False):
    return type(value) is Decimal and value.is_finite() and (value > 0 if positive else value >= 0)


def signed_plan(plan):
    return plan.model_copy(update={"plan_sha256": digest(plan.model_dump(exclude={"plan_sha256"}))})


def event(run_key_sha256, **fields):
    obj = LedgerEvent(event_key_sha256="", **fields)
    key = digest({"run_key_sha256": run_key_sha256, **obj.model_dump(exclude={"event_key_sha256", "pit_ready"})})
    return obj.model_copy(update={"event_key_sha256": key})


def position(**fields):
    obj = DailyPosition(**fields)
    return obj.model_copy(update={"position_state_sha256": digest(obj.model_dump(exclude={"position_state_sha256"}))})


def snapshot(**fields):
    obj = DailySnapshot(**fields)
    return obj.model_copy(update={"snapshot_sha256": digest(obj.model_dump(exclude={"snapshot_sha256"}))})


def fresh(factory):
    db = factory()
    if not isinstance(db, Session):
        raise ValueError("SESSION_NOT_FRESH")
    bind = db.get_bind()
    if db.in_transaction() or db.new or db.dirty or db.deleted or (hasattr(bind,"in_transaction") and bind.in_transaction()):
        raise ValueError("SESSION_NOT_FRESH")
    return db


def rows(db, model, predicate, order):
    columns = [c for c in model.__table__.columns if c.name not in ("created_at", "activated_at", "observation_completed_at")]
    return [dict(r) for r in db.execute(select(*columns).where(predicate).order_by(*order)).mappings()]


def read_state(db, key):
    with db.no_autoflush:
        runs = rows(db, ShadowTestRun, ShadowTestRun.run_key_sha256 == key, (ShadowTestRun.id,))
        if not runs:
            return dict(run=None, ledger=[], snapshots=[], positions=[])
        run = runs[0]
        return dict(run=run,
            ledger=rows(db, ShadowLedgerEntry, ShadowLedgerEntry.shadow_run_id == run["id"], (ShadowLedgerEntry.sequence_number,)),
            snapshots=rows(db, ShadowDailySnapshot, ShadowDailySnapshot.shadow_run_id == run["id"], (ShadowDailySnapshot.as_of_date,)),
            positions=rows(db, ShadowDailyPositionSnapshot, ShadowDailyPositionSnapshot.shadow_run_id == run["id"], (ShadowDailyPositionSnapshot.shadow_daily_snapshot_id, ShadowDailyPositionSnapshot.bond_id)))


def state_hash(state):
    snapshots = {s["id"]: s["snapshot_sha256"] for s in state["snapshots"]}
    def clean(row):
        row = {k: v for k, v in row.items() if k not in ("id", "shadow_run_id")}
        for name in ("previous_snapshot_id", "shadow_daily_snapshot_id"):
            if name in row:
                row[name] = snapshots.get(row[name])
        return row
    return digest({k: clean(v) if k == "run" and v else [clean(r) for r in v] if k != "run" else None for k, v in state.items()})


def stored_snapshot(row):
    return DailySnapshot.model_validate_json(json.dumps(row["details_json"]["snapshot"]))


def audit(db, key, expected):
    state = read_state(db, key)
    found = [s for s in state["snapshots"] if s["as_of_date"] == expected.as_of_date]
    errors = set()
    ledger = [e for e in state["ledger"] if e["event_date"] <= expected.as_of_date]
    positions = []
    try:
        require(state["run"] is not None and len(found) == 1, "SHADOW_HISTORY_INVALID")
        s = found[0]
        require(tuple(s["details_json"]["provenance_versions"]) == PROVENANCE_VERSIONS, "SHADOW_HISTORY_INVALID")
        actual = stored_snapshot(s)
        require(actual == expected and actual.snapshot_sha256 == digest(actual.model_dump(exclude={"snapshot_sha256"})), "SHADOW_HISTORY_INVALID")
        for field in DailySnapshot.model_fields:
            if field in s:
                require(s[field] == getattr(actual, field), "SHADOW_HISTORY_INVALID")
        previous = next((r for r in state["snapshots"] if r["id"] == s["previous_snapshot_id"]), None)
        require((previous["snapshot_sha256"] if previous else None) == actual.previous_snapshot_sha256, "SHADOW_HISTORY_INVALID")
        quantities, cash = {}, ZERO
        with localcontext(DECIMAL_CONTEXT):
            for n, entry in enumerate(ledger, 1):
                e = LedgerEvent.model_validate_json(json.dumps(entry["details_json"]))
                require(e.sequence_number == n and e.event_key_sha256 == digest({"run_key_sha256": key, **e.model_dump(exclude={"event_key_sha256", "pit_ready"})}), "SHADOW_HISTORY_INVALID")
                for field in LedgerEvent.model_fields:
                    if field in entry:
                        require(entry[field] == getattr(e, field), "SHADOW_HISTORY_INVALID")
                if e.event_type == "INITIAL_CAPITAL":
                    require(n == 1 and e.bond_id is None and e.quantity_delta == 0 and
                        e.cash_delta_rub == state["run"]["initial_capital_rub"], "SHADOW_HISTORY_INVALID")
                elif e.event_type == "GENESIS_PURCHASE":
                    require(e.quantity_delta > 0 and finite(e.unit_amount_rub,positive=True) and
                        e.cash_delta_rub == -(e.quantity_delta*e.unit_amount_rub), "SHADOW_HISTORY_INVALID")
                else:
                    before = quantities.get(e.bond_id,0)
                    require(before > 0 and finite(e.unit_amount_rub) and e.cash_delta_rub == before*e.unit_amount_rub and
                        e.quantity_delta == (-before if e.event_type == "REDEMPTION" else 0), "SHADOW_HISTORY_INVALID")
                cash += e.cash_delta_rub
                if e.bond_id is not None:
                    quantities[e.bond_id] = quantities.get(e.bond_id, 0) + e.quantity_delta
            positions = [p for p in state["positions"] if p["shadow_daily_snapshot_id"] == s["id"]]
            require(len(positions) == len(quantities), "SHADOW_HISTORY_INVALID")
            market = ZERO
            stored = {p["bond_id"]: p for p in s["details_json"]["positions"]}
            for p in positions:
                evidence = DailyPosition.model_validate_json(json.dumps(stored[p["bond_id"]]))
                require(evidence.position_state_sha256 == digest(evidence.model_dump(exclude={"position_state_sha256"})), "SHADOW_HISTORY_INVALID")
                for field in DailyPosition.model_fields:
                    if field in p:
                        require(p[field] == getattr(evidence, field), "SHADOW_HISTORY_INVALID")
                require(p["quantity"] == quantities[p["bond_id"]] and p["quantity"] >= 0, "SHADOW_HISTORY_INVALID")
                value = p["quantity"] * p["dirty_value_rub_per_bond"] if p["quantity"] else ZERO
                require(value == p["market_value_rub"], "SHADOW_HISTORY_INVALID")
                market += value
            require(cash == actual.cash_rub and market == actual.market_value_rub and cash + market == actual.nav_rub, "SHADOW_HISTORY_INVALID")
            require(len(ledger) == actual.ledger_entry_count_to_date, "SHADOW_HISTORY_INVALID")
            require(sum(p["quantity"] > 0 for p in positions) == actual.active_position_count and
                sum(p["quantity"] == 0 for p in positions) == actual.redeemed_position_count, "SHADOW_HISTORY_INVALID")
            today = [e for e in ledger if e["event_date"] == actual.as_of_date and e["event_type"] in ("COUPON","AMORTIZATION","REDEMPTION")]
            require(len(today) == actual.applied_cashflow_count_for_day, "SHADOW_HISTORY_INVALID")
            for p in positions:
                require(p["cashflow_rub_on_date"] == sum((e["cash_delta_rub"] for e in today if e["bond_id"] == p["bond_id"]), ZERO), "SHADOW_HISTORY_INVALID")
            if previous:
                prior = stored_snapshot(previous)
                require(prior.nav_rub > 0 and actual.daily_return == actual.nav_rub/prior.nav_rub-1 and
                    actual.cumulative_return == actual.nav_rub/state["run"]["initial_capital_rub"]-1, "SHADOW_HISTORY_INVALID")
            else:
                require(actual.nav_rub == state["run"]["initial_capital_rub"] and actual.daily_return == actual.cumulative_return == 0, "SHADOW_HISTORY_INVALID")
    except Exception:
        errors.add("SHADOW_HISTORY_INVALID")
    return ShadowAudit(status="FAILED" if errors else "VERIFIED", run_key_sha256=key,
        snapshot_sha256=expected.snapshot_sha256, ledger_entry_count=len(ledger), position_count=len(positions), blockers=tuple(sorted(errors)))


def audit_genesis(db, reviewed):
    """Check persisted original Genesis provenance in addition to accounting."""
    result = audit(db, reviewed.run_key_sha256, reviewed.snapshot)
    state = read_state(db, reviewed.run_key_sha256)
    expected = {
        "run_key_sha256": reviewed.run_key_sha256,
        "shadow_execution_sha256": reviewed.shadow_execution_sha256,
        "genesis_plan_sha256": reviewed.plan_sha256,
        "source_universe_sha256": reviewed.source_universe_sha256,
        "source_code_sha": reviewed.source_code_sha,
    }
    valid = state["run"] is not None
    for name, value in expected.items():
        width = 40 if name == "source_code_sha" else 64
        valid = valid and type(value) is str and re.fullmatch(r"[0-9a-f]{%d}" % width, value) is not None
        valid = valid and state["run"].get(name) == value
    if not valid:
        return result.model_copy(update={"status": "FAILED", "blockers": ("SHADOW_HISTORY_INVALID",)})
    return result


def persist(db, plan, run_fields=None):
    state = read_state(db, plan.run_key_sha256)
    if state["run"] is None:
        require(run_fields is not None, "RUN_NOT_FOUND")
        run = ShadowTestRun(**run_fields)
        db.add(run)
        db.flush()
        run_id = run.id
    else:
        run_id = state["run"]["id"]
    for e in plan.events:
        fields = e.model_dump(exclude={"pit_ready"})
        db.add(ShadowLedgerEntry(shadow_run_id=run_id, details_json=e.model_dump(mode="json"), **fields))
    previous = next((s for s in state["snapshots"] if s["snapshot_sha256"] == plan.snapshot.previous_snapshot_sha256), None)
    fields = plan.snapshot.model_dump(exclude={"pit_ready", "return_formula_version", "previous_snapshot_sha256"})
    s = ShadowDailySnapshot(shadow_run_id=run_id, previous_snapshot_id=previous["id"] if previous else None,
        details_json={"snapshot": plan.snapshot.model_dump(mode="json"), "positions": [p.model_dump(mode="json") for p in plan.positions],
            "provenance_versions": list(PROVENANCE_VERSIONS), "calculation_context": "DECIMAL_28_ROUND_HALF_EVEN",
            "hash_method": "SORTED_CANONICAL_JSON_SHA256_V1", "diagnostics": list(plan.diagnostics),
            "valuation_evidence": [v.model_dump(mode="json") for v in plan.valuation_evidence]}, **fields)
    db.add(s)
    db.flush()
    for p in plan.positions:
        db.add(ShadowDailyPositionSnapshot(shadow_run_id=run_id, shadow_daily_snapshot_id=s.id, **p.model_dump(exclude={"pit_ready"})))
    if state["run"] and plan.as_of_date == state["run"]["planned_end_date"]:
        run = db.get(ShadowTestRun, run_id)
        run.status = "OBSERVATION_COMPLETE"
        # Operational timestamp is excluded from all deterministic hashes.
        from sqlalchemy import func
        run.observation_completed_at = func.now()
    db.flush()


def execute_apply(factory, reviewed, authorization, rebuild, receipt_type, run_fields=None, daily=False, genesis_audit=None):
    base = dict(plan_sha256=reviewed.plan_sha256, run_key_sha256=reviewed.run_key_sha256)
    try:
        require(reviewed.status in ("EXECUTABLE", "IDEMPOTENT_NOOP") and not reviewed.blockers, "AUTHORIZATION_MISMATCH")
        require(reviewed.plan_sha256 == digest(reviewed.model_dump(exclude={"plan_sha256"})), "PLAN_SHA_MISMATCH")
        for field in type(authorization).model_fields:
            if field not in ("explicit_apply", "contract_version", "pit_ready"):
                require(getattr(authorization, field) == getattr(reviewed, field), "AUTHORIZATION_MISMATCH")
        require(authorization.explicit_apply is True, "AUTHORIZATION_MISMATCH")
        db = fresh(factory)
    except Exception as exc:
        code = str(exc) if str(exc) in Blocker.__args__ else "AUTHORIZATION_MISMATCH"
        return receipt_type(**base, status="BLOCKED", blockers=(code,))
    attempted = 0
    committing = committed = False
    pre = None
    try:
        dialect = db.get_bind().dialect.name
        if dialect == "sqlite":
            db.execute(text("BEGIN IMMEDIATE"))
        elif dialect == "postgresql":
            db.begin()
            for model in TABLES:
                db.execute(text(f"LOCK TABLE {model.__tablename__} IN SHARE ROW EXCLUSIVE MODE NOWAIT"))
            if daily:
                for table in ("bonds", "bond_cashflow_events", "bond_market_snapshots", "bond_security_master_profiles"):
                    db.execute(text(f"LOCK TABLE {table} IN SHARE MODE NOWAIT"))
        else:
            raise ValueError("APPLY_DIALECT_UNSUPPORTED")
        current = rebuild(db)
        require(current == reviewed, "CURRENT_SHADOW_STATE_DRIFT")
        if current.status == "IDEMPOTENT_NOOP":
            pre = genesis_audit(db, current) if genesis_audit else audit(db, current.run_key_sha256, current.snapshot)
            require(pre.status == "VERIFIED", "PRE_COMMIT_AUDIT_FAILED")
            return receipt_type(**base, status="IDEMPOTENT_NOOP", pre_commit_audit=pre)
        attempted = len(current.events) + len(current.positions) + 1 + int(run_fields is not None)
        persist(db, current, run_fields)
        pre = genesis_audit(db, current) if genesis_audit else audit(db, current.run_key_sha256, current.snapshot)
        require(pre.status == "VERIFIED", "PRE_COMMIT_AUDIT_FAILED")
        committing = True
        db.commit()
        committed = True
        verification = fresh(factory)
        try:
            post = genesis_audit(verification, current) if genesis_audit else audit(verification, current.run_key_sha256, current.snapshot)
        finally:
            verification.close()
        if post.status != "VERIFIED":
            return receipt_type(**base, status="POST_COMMIT_AUDIT_FAILED", attempted_rows=attempted,
                committed_rows=attempted, pre_commit_audit=pre, post_commit_audit=post, blockers=("POST_COMMIT_AUDIT_FAILED",))
        return receipt_type(**base, status="APPLIED", attempted_rows=attempted, committed_rows=attempted,
            pre_commit_audit=pre, post_commit_audit=post)
    except Exception as exc:
        if committed:
            status, count, code = "POST_COMMIT_AUDIT_FAILED", attempted, "POST_COMMIT_AUDIT_FAILED"
        elif committing:
            status, count, code = "COMMIT_OUTCOME_UNKNOWN", None, "COMMIT_OUTCOME_UNKNOWN"
        else:
            try:
                db.rollback()
            except Exception:
                return receipt_type(**base, status="ROLLBACK_FAILED", attempted_rows=attempted,
                    committed_rows=None, pre_commit_audit=pre, blockers=("ROLLBACK_FAILED",))
            status, count = "ROLLED_BACK", 0
            code = str(exc) if str(exc) in Blocker.__args__ else "LOCK_OR_TRANSACTION_ERROR"
        return receipt_type(**base, status=status, attempted_rows=attempted, committed_rows=count,
            pre_commit_audit=pre, blockers=(code,))
    finally:
        db.close()
