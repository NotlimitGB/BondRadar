"""Task306A observed-data coverage, never replay, scoring or profitability."""
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Context, Decimal, ROUND_HALF_EVEN, localcontext
import hashlib
import json
from sqlalchemy.exc import SQLAlchemyError
from app.schemas.modern_historical_replay_readiness import (
    HistoricalReplayReadinessAuditV1 as Audit, HistoricalSourceInventoryV1 as Inventory,
    HistoricalReplayDateReadinessV1 as DateReadiness, HistoricalEvidenceFamilyAuditV1 as Family,
    HistoricalReplayWindowSummaryV1 as Window, FieldCoverage, CountEntry, EvidenceTimeRange, EvidenceDateRange,
    ObservedBondHistory, MarketDateCoverage, CreditTargetInventory,
)
from app.services.modern_historical_replay_evidence_reader import read_evidence, MARKET_FIELDS
from app.services.bond_market_feature_service import _liquidity
from app.services.moex_duration_semantics import normalize_moex_duration
from app.services.ofz_identity import is_ofz_instrument


P0 = tuple(sorted(("HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN", "SURVIVORSHIP_BIAS_RISK",
    "CURRENT_BOND_METADATA_LOOKAHEAD_RISK", "CURRENT_SECURITY_MASTER_LOOKAHEAD_RISK",
    "SECURITY_MASTER_CURRENT_PROFILE_ONLY", "SECURITY_MASTER_HISTORICAL_VALUE_UNPROVEN",
    "SECURITY_MASTER_HISTORICAL_AVAILABILITY_UNPROVEN", "CURRENT_ISSUER_IDENTITY_LOOKAHEAD_RISK",
    "MARKET_HISTORICAL_AVAILABILITY_UNPROVEN", "CASHFLOW_HISTORY_COMPLETENESS_UNPROVEN",
    "OFZ_STRUCTURAL_HISTORY_UNPROVEN")))
REMEDIATION = tuple(sorted(("CAPTURE_AUTHORITATIVE_HISTORICAL_LISTING_UNIVERSE",
    "VERSION_SECURITY_MASTER_VALUES_AND_PROVE_AVAILABILITY", "VERSION_EXACT_ISSUER_BINDINGS",
    "PROVE_MARKET_PAYLOAD_HISTORICAL_AVAILABILITY", "PROVE_CONTRACTUAL_CASHFLOW_HISTORY_COMPLETENESS",
    "PROVE_RATING_PUBLICATION_AND_TARGET_IDENTITY", "VERSION_OFZ_ELIGIBILITY_EVIDENCE")))
PROFILE_FIELDS = ("currency_code", "nominal_value", "coupon_structure", "amortization_structure",
    "perpetual_structure", "lot_size", "trading_board", "coupon_frequency_per_year", "maturity_date", "listing_status", "outstanding_nominal")
SCALAR_STATES = {"currency_code": "currency_state", "nominal_value": "nominal_state",
    "lot_size": "lot_size_state", "trading_board": "trading_board_state",
    "coupon_frequency_per_year": "coupon_frequency_state", "maturity_date": "maturity_state", "outstanding_nominal": "outstanding_nominal_state"}


def finite(value):
    return type(value) is Decimal and value.is_finite()


def present(value):
    return type(value) is str and bool(value.strip())


def pct(count, denominator):
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return Decimal(count) * Decimal("100") / Decimal(denominator) if denominator else Decimal("0")


def utc(value):
    # SQLite DateTime loses tzinfo; persisted timestamps in this project are UTC.
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def require(condition):
    if not condition:
        raise ValueError("INVALID_DB_EVIDENCE")


def signed(report):
    content = report.model_dump(mode="json", exclude={"audit_sha256"})
    encoded = json.dumps(content, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("ascii")
    return report.model_copy(update={"audit_sha256": hashlib.sha256(encoded).hexdigest()})


def publication_gate(row, cutoff):
    precision = row["publication_precision"]
    require(precision in ("DATE", "TIMESTAMP", "UNKNOWN"))
    day, timestamp = row["publication_date"], row["publication_at"]
    require((precision == "DATE" and type(day) is date and timestamp is None) or
        (precision == "TIMESTAMP" and day is None and type(timestamp) is datetime) or
        (precision == "UNKNOWN" and day is None and timestamp is None))
    if precision == "UNKNOWN":
        return "UNPROVEN"
    available = (datetime.combine(day, time.min, timezone.utc) + timedelta(days=1)
        if precision == "DATE" else utc(timestamp))
    return "PROVEN" if available <= cutoff else "FUTURE"


def classify_date(*, joint_bond_ids, peer_bond_ids, ofz_nodes, proofs):
    """Pure rule: proof inputs are private audit facts, not public overrides."""
    usable = len(set(joint_bond_ids) & set(peer_bond_ids)) >= 3 and ofz_nodes >= 2
    if not usable:
        return "UNUSABLE"
    needed = ("universe", "market", "terms", "issuer", "rating", "liquidity", "duration",
              "ofz", "outcome", "cashflow")
    return "PIT_SAFE" if all(proofs.get(key) is True for key in needed) else "DIAGNOSTIC_ONLY"


def _coverage(rows, field, *, verified=None):
    supplied = [r for r in rows if r.get(field) is not None]
    usable = [r for r in supplied if (finite(r[field]) if isinstance(r[field], Decimal)
              else present(r[field]) if isinstance(r[field], str) else type(r[field]) in (int, bool, date))]
    if field in ("price", "clean_price", "dirty_price", "nominal_value", "lot_size", "coupon_frequency_per_year", "outstanding_nominal"):
        usable = [r for r in usable if type(r[field]) in (Decimal, int) and r[field] > 0]
    elif field in ("nkd", "duration_years", "volume"):
        usable = [r for r in usable if finite(r[field]) and r[field] >= 0]
    elif field == "liquidity_score":
        usable = [r for r in usable if type(r[field]) is int and 0 <= r[field] <= 100]
    return FieldCoverage(field=field, row_count=len(rows), nonnull_count=len(supplied),
        nonnull_pct=pct(len(supplied), len(rows)), usable_count=len(usable),
        bond_count=len({r["bond_id"] for r in supplied if r.get("bond_id") is not None}),
        date_count=len({r["trade_date"] for r in supplied if r.get("trade_date") is not None}),
        verified_count=sum(verified(r) for r in supplied) if verified else 0)


def _inventory(table, rows, labels):
    dates = [r[n] for r in rows for n in ("trade_date", "event_date", "report_date", "as_of_date") if type(r.get(n)) is date]
    times = []
    date_ranges = []
    for field in ("trade_date", "event_date", "report_date", "as_of_date", "publication_date", "maturity_date"):
        if any(field in r for r in rows):
            values = [r[field] for r in rows if type(r.get(field)) is date]
            date_ranges.append(EvidenceDateRange(field=field, nonnull_count=len(values), null_count=len(rows)-len(values),
                minimum=min(values) if values else None, maximum=max(values) if values else None))
    for field in ("effective_at", "observed_at", "ingestion_at", "ingested_at", "retrieved_at", "publication_at", "created_at", "updated_at", "first_discovered_at", "first_retrieved_at", "last_observed_at", "last_resolved_at"):
        if any(field in r for r in rows):
            values = [utc(r[field]) for r in rows if r.get(field) is not None]
            times.append(EvidenceTimeRange(field=field, nonnull_count=len(values), null_count=len(rows)-len(values),
                minimum=min(values) if values else None, maximum=max(values) if values else None))
    groups = Counter()
    for field in ("source", "source_provider", "source_kind", "event_type", "currency", "publication_precision",
                  "publication_status", "resolution_state", "field_name", "form", "source_form", "evidence_source",
                  "exact_payload_bound", "horizon_days", "return_method", "report_year", "mapping_state", "listing_status",
                  "currency_state", "nominal_state", "lot_size_state", "trading_board_state", "coupon_frequency_state",
                  "maturity_state", "outstanding_nominal_state", "coupon_structure", "perpetual_structure", "amortization_structure"):
        for row in rows:
            if field in row:
                value = row[field]
                groups[f"{field}={value if value is not None else 'NOT_SUPPLIED'}"] += 1
    if table == "bond_cashflow_events":
        groups["amount_missing"] = sum(r["amount"] is None for r in rows)
        groups["amount_present"] = sum(r["amount"] is not None for r in rows)
        groups["amount_invalid"] = sum(r["amount"] is not None and (not finite(r["amount"]) or r["amount"] < 0) for r in rows)
        groups["non_RUB"] = sum(r["currency"] != "RUB" for r in rows)
    if table == "credit_rating_events":
        groups["rated_target_count"] = len({(r["target_kind"], r["bond_id"], r["legal_issuer_id"]) for r in rows})
        groups["RATING_PUBLICATION_UNKNOWN_COUNT"] = sum(r["publication_precision"] == "UNKNOWN" for r in rows)
        groups["RATING_IDENTITY_UNPROVEN_COUNT"] = sum(r["resolution_state"] != "RESOLVED" for r in rows)
        groups["RATING_PUBLICATION_RESOLVED_TARGET_COUNT"] = sum(r["resolution_state"] == "RESOLVED" and r["publication_precision"] != "UNKNOWN" for r in rows)
        groups["RATING_PIT_PROVEN_COUNT"] = 0
        # Publication/current target evidence does not prove complete historical linkage.
    if table == "bond_market_snapshots":
        groups["trade_date_after_created_date"] = sum(r["trade_date"] > utc(r["created_at"]).date() for r in rows)
    if table == "bond_return_labels":
        groups["evaluable_bond_date_count"] = len({(r["bond_id"], r["as_of_date"]) for r in rows
            if r["start_market_snapshot_id"] is not None and r["end_market_snapshot_id"] is not None})
        groups["90d_total_return_count"] = sum(r["horizon_days"] == 90 and r["return_method"] == "total_return" for r in rows)
    fields = ()
    if table == "bond_market_snapshots":
        fields = tuple(_coverage(rows, f) for f in MARKET_FIELDS)
    elif table == "bond_security_master_profiles":
        fields = tuple(_coverage(rows, f, verified=(lambda r, f=f: r.get(SCALAR_STATES[f]) == "verified")
            if f in SCALAR_STATES else (lambda r, f=f: r[f] not in ("unknown", "conflict"))) for f in PROFILE_FIELDS)
    elif table == "bond_return_labels":
        fields = tuple(FieldCoverage(field=f, row_count=len(rows), nonnull_count=n,
            nonnull_pct=pct(n, len(rows)), usable_count=0) for f, n in sorted(labels.items()))
    diagnostics = ("LEGACY_OUTCOME_EVIDENCE", "NOT_MODERN_OUTCOME_AUTHORITY") if table == "bond_return_labels" else ()
    if table.startswith("cbr_bank_") or table == "controlled_financial_statement_values":
        diagnostics = ("NOT_CURRENT_TASK299_INPUT",)
        if table == "controlled_financial_statement_values":
            diagnostics += ("FINANCIAL_PUBLICATION_UNKNOWN",)
    if table == "credit_rating_events":
        diagnostics = ("PUBLICATION_PLUS_CURRENT_TARGET_NOT_COMPLETE_HISTORICAL_IDENTITY_PROOF",)
    return Inventory(source_table=table, row_count=len(rows),
        bond_count=len({r["bond_id"] for r in rows if r.get("bond_id") is not None}),
        date_count=len(set(dates)), min_date=min(dates) if dates else None, max_date=max(dates) if dates else None,
        fields=fields, breakdowns=tuple(CountEntry(key=k, count=n, pct=pct(n, len(rows))) for k,n in sorted(groups.items())),
        time_ranges=tuple(times), date_ranges=tuple(date_ranges), diagnostics=diagnostics)


def _profile_ready(p):
    return bool(p and p["contract_version"] == "bond-security-master-v2" and
        p["currency_state"] == "verified" and p["currency_code"] == "RUB" and
        p["nominal_state"] == "verified" and finite(p["nominal_value"]) and p["nominal_value"] > 0 and
        p["coupon_structure"] == "fixed" and p["perpetual_structure"] == "dated" and
        p["coupon_frequency_state"] == "verified" and type(p["coupon_frequency_per_year"]) is int and
        p["coupon_frequency_per_year"] > 0 and p["lot_size_state"] == "verified" and
        type(p["lot_size"]) is int and p["lot_size"] > 0 and p["trading_board_state"] == "verified" and
        present(p["trading_board"]))


def _dirty_inputs(snapshot, p):
    if not p or p["currency_state"] != "verified" or p["currency_code"] != "RUB" or p["nominal_state"] != "verified":
        return False
    quote = snapshot["clean_price"] if snapshot["clean_price"] is not None else snapshot["price"]
    return bool(finite(p["nominal_value"]) and p["nominal_value"] > 0 and finite(quote) and quote > 0 and
        finite(snapshot["nkd"]) and snapshot["nkd"] >= 0)


def _market_ready(snapshot, p):
    duration = normalize_moex_duration(snapshot["raw_payload"], stored_duration_years=snapshot["duration_years"])
    frequency = p["coupon_frequency_per_year"] if p else None
    valid_yield = finite(snapshot["yield_to_maturity"])
    return bool(_dirty_inputs(snapshot, p) and duration.status == "READY" and
        finite(duration.duration_years) and duration.duration_years >= 0 and valid_yield and
        type(frequency) is int and frequency > 0 and snapshot["yield_to_maturity"] > Decimal(-100 * frequency))


def _market_inputs(snapshot):
    """Market-only prerequisites, independent of present-day canonical terms."""
    duration = normalize_moex_duration(snapshot["raw_payload"],stored_duration_years=snapshot["duration_years"])
    quote = snapshot["clean_price"] if snapshot["clean_price"] is not None else snapshot["price"]
    return bool(duration.status == "READY" and finite(duration.duration_years) and duration.duration_years >= 0 and
        finite(snapshot["yield_to_maturity"]) and finite(quote) and quote > 0 and
        finite(snapshot["nkd"]) and snapshot["nkd"] >= 0)


def _ofz_structure(bond, p, day):
    text = " ".join((bond["name"], bond["isin"] or "", bond["secid"] or "")).upper()
    return bool(p and p["currency_state"] == "verified" and p["currency_code"] == "RUB" and
        p["coupon_structure"] == "fixed" and p["perpetual_structure"] == "dated" and
        p["coupon_frequency_state"] == "verified" and type(p["coupon_frequency_per_year"]) is int and
        p["coupon_frequency_per_year"] > 0 and p["amortization_structure"] == "bullet" and
        p["maturity_state"] == "verified" and type(p["maturity_date"]) is date and p["maturity_date"] >= day and
        not any(marker in text for marker in ("ОФЗ-ПК", "ОФЗ-ИН", "ОФЗ-АД", "OFZ-PK", "OFZ-IN", "OFZ-AD")))


def _ofz_market_inputs(snapshot):
    # Curve observations need YTM and authoritative duration, not dirty pricing.
    duration = normalize_moex_duration(snapshot["raw_payload"], stored_duration_years=snapshot["duration_years"])
    return bool(duration.status == "READY" and finite(duration.duration_years) and duration.duration_years > 0 and
        finite(snapshot["yield_to_maturity"]))


def _intervals(snapshots, profile):
    intervals = []
    for row in snapshots:
        if _dirty_inputs(row, profile):
            start, end = row["trade_date"], row["trade_date"] + timedelta(days=7)
            if intervals and start <= intervals[-1][1] + timedelta(days=1):
                intervals[-1] = (intervals[-1][0], max(end, intervals[-1][1]))
            else:
                intervals.append((start, end))
    return tuple(intervals)


def outcome_coverage(day, intervals, events, maturity):
    """Merged quote intervals avoid a 91-day valuation loop for every Bond/date."""
    end = day + timedelta(days=90)
    dates = [r["event_date"] for r in events]
    if any(r["event_type"] == "redemption" for r in events[:bisect_right(dates,day)]):
        return "NONE", False, ("REDEMPTION_ON_OR_BEFORE_ENTRY",), ()
    relevant = events[bisect_right(dates, day):bisect_right(dates, end)]
    blockers, diagnostics = set(), set()
    redeemed = None
    seen = set()
    for row in relevant:
        kind = row["event_type"]
        if kind == "offer_redemption":
            diagnostics.add("OFFER_NOT_AUTOMATIC_CASH")
            continue
        identity = (row["event_date"], kind)
        if identity in seen:
            blockers.add("DUPLICATE_CASHFLOW_EVENT")
        seen.add(identity)
        if redeemed is not None:
            blockers.add("CASHFLOW_AFTER_REDEMPTION")
        if kind not in ("coupon", "amortization", "redemption"):
            blockers.add("UNSUPPORTED_CASHFLOW_TYPE")
        if row["currency"] != "RUB" or not finite(row["amount"]) or row["amount"] < 0:
            blockers.add("CASHFLOW_AMOUNT_OR_CURRENCY_INVALID")
        if kind == "redemption" and row["currency"] == "RUB" and finite(row["amount"]) and row["amount"] >= 0:
            redeemed = row["event_date"]
    if type(maturity) is date and day < maturity <= end and redeemed is None:
        blockers.add("MATURITY_REDEMPTION_EVIDENCE_MISSING")
    required_end = redeemed - timedelta(days=1) if redeemed else end
    starts = [a for a,b in intervals]
    idx = bisect_right(starts, day)-1
    full = idx >= 0 and intervals[idx][1] >= required_end
    overlap = any(a <= required_end and b >= day for a,b in intervals)
    state = "FULL" if full and not blockers else "PARTIAL" if overlap or relevant else "NONE"
    if not full:
        blockers.add("OUTCOME_DAILY_MARKET_COVERAGE_INCOMPLETE")
    coupon = any(r["event_type"] == "coupon" and r["currency"] == "RUB" and finite(r["amount"]) and r["amount"] >= 0 for r in relevant)
    return state, coupon, tuple(sorted(blockers)), tuple(sorted(diagnostics))


def _liquidity_prefix(rows):
    # Parse each raw row once; window component presence/median-positive gates
    # then need only prefix differences, without recomputing Task270 statistics.
    prefix = [(0,0,0)]
    for row in rows:
        _, turnover, trades = _liquidity(row["raw_payload"],set())
        val, num, positive = prefix[-1]
        prefix.append((val + (turnover is not None), num + (trades is not None),
                       positive + (turnover is not None and turnover > 0)))
    return tuple(prefix)


def _matrix(tables, ofz_rows):
    # No completeness authority or versioned resolver contract exists in this schema.
    definitions = (
        ("historical_universe", "bonds", False, True, "HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN"),
        ("market", "bond_market_snapshots", True, False, "MARKET_HISTORICAL_AVAILABILITY_UNPROVEN"),
        ("liquidity", "bond_market_snapshots", True, False, "MARKET_HISTORICAL_AVAILABILITY_UNPROVEN"),
        ("security_master", "bond_security_master_evidence", True, True, "SECURITY_MASTER_HISTORICAL_VALUE_UNPROVEN"),
        ("issuer_identity", "bond_legal_issuer_evidence", True, True, "CURRENT_ISSUER_IDENTITY_LOOKAHEAD_RISK"),
        ("credit_rating", "credit_rating_events", True, True, "RATING_IDENTITY_HISTORY_UNPROVEN"),
        ("rating_publication", "credit_rating_events", True, False, "RATING_PUBLICATION_UNKNOWN"),
        ("financial_publication", "cbr_bank_report_snapshots", True, False, "FINANCIAL_PUBLICATION_UNKNOWN"),
        ("modified_duration", "bond_market_snapshots", True, True, "SECURITY_MASTER_HISTORICAL_VALUE_UNPROVEN"),
        ("dv01", "bond_market_snapshots", True, True, "SECURITY_MASTER_HISTORICAL_VALUE_UNPROVEN"),
        ("ofz_market", "bond_market_snapshots", True, True, "OFZ_STRUCTURAL_HISTORY_UNPROVEN"),
        ("ofz_structural_eligibility", "bond_security_master_evidence", True, True, "OFZ_STRUCTURAL_HISTORY_UNPROVEN"),
        ("cashflows", "bond_cashflow_events", True, False, "CASHFLOW_HISTORY_COMPLETENESS_UNPROVEN"),
        ("future_outcome_90d", "bond_market_snapshots", True, True, "OUTCOME_EXHAUSTIVE_EVIDENCE_UNPROVEN"),
    )
    result = []
    ofz_ids = {r["bond_id"] for r in ofz_rows}
    for family, table, dated, current, blocker in definitions:
        rows = tables[table]
        current_available = bool(rows)
        if family == "security_master":
            current_available = bool(tables["bond_security_master_profiles"])
        elif family == "issuer_identity":
            current_available = bool(tables["bond_legal_issuer_profiles"])
        if family == "ofz_market":
            rows = ofz_rows
            current_available = bool(rows)
        elif family == "ofz_structural_eligibility":
            rows = [r for r in tables["bond_security_master_evidence"] if r["bond_id"] in ofz_ids]
            current_available = bool(ofz_ids)
        status = "PARTIAL" if rows and dated else "UNPROVEN"
        availability = False
        if family == "rating_publication":
            known = sum(r["publication_precision"] in ("DATE", "TIMESTAMP") for r in rows)
            status = "PROVEN" if rows and known == len(rows) else "PARTIAL" if known else "UNPROVEN"
            availability = status == "PROVEN"
        if family == "financial_publication":
            status = "NOT_APPLICABLE"
        result.append(Family(family=family, available_now=current_available, historically_dated=dated and bool(rows),
            historical_availability_proven=availability, historical_value_versioned=False,
            current_state_dependency=current, pit_status=status, current_model_input=family != "financial_publication",
            blockers=() if status in ("PROVEN", "NOT_APPLICABLE") else (blocker,)))
    return tuple(sorted(result, key=lambda f:f.family))


def _build(data):
    tables = data.tables
    bonds = {r["id"]:r for r in tables["bonds"]}
    require(all(type(bid) is int and bid > 0 for bid in bonds))
    profiles = {r["bond_id"]:r for r in tables["bond_security_master_profiles"]}
    issuers = {(r["identity_source"],r["source_issuer_id"]):r for r in tables["legal_issuers"] if r["resolution_state"] == "verified"}
    current_issuer = {}
    for p in tables["bond_legal_issuer_profiles"]:
        if p["mapping_state"] == "verified":
            issuer = issuers.get((p["mapping_source"],p["source_issuer_id"]))
            if issuer:
                current_issuer[p["bond_id"]] = issuer
    histories, flows, quotes = defaultdict(list), defaultdict(list), {}
    for table in ("bond_market_snapshots", "bond_cashflow_events", "bond_security_master_profiles",
                  "bond_security_master_evidence", "bond_legal_issuer_profiles", "bond_legal_issuer_evidence"):
        require(all(r["bond_id"] in bonds for r in tables[table]))
    for r in tables["bond_market_snapshots"]:
        require(type(r["trade_date"]) is date)
        require((r["bond_id"],r["trade_date"]) not in quotes)
        quotes[r["bond_id"],r["trade_date"]] = r
        histories[r["bond_id"]].append(r)
    order = {"coupon":0, "amortization":1, "redemption":2, "offer_redemption":3, "other":4}
    for r in tables["bond_cashflow_events"]:
        if r["source"] == "moex":
            flows[r["bond_id"]].append(r)
    for rows in histories.values():
        rows.sort(key=lambda r:(r["trade_date"],r["id"]))
    for rows in flows.values():
        rows.sort(key=lambda r:(r["event_date"],order.get(r["event_type"],5),r["id"]))
    intervals = {bid:_intervals(rows, profiles.get(bid)) for bid,rows in histories.items()}
    history_dates = {bid:tuple(r["trade_date"] for r in rows) for bid,rows in histories.items()}
    liquidity_prefix = {bid:_liquidity_prefix(rows) for bid,rows in histories.items()}
    ofz = {bid for bid,b in bonds.items() if is_ofz_instrument(isin=b["isin"],secid=b["secid"])}
    artifacts = {r["id"]:r for r in tables["credit_risk_source_artifacts"]}
    ratings = defaultdict(list)
    for r in tables["credit_rating_events"]:
        require(r["artifact_id"] in artifacts)
        require(r["resolution_state"] in ("RESOLVED", "UNRESOLVED", "AMBIGUOUS"))
        if r["resolution_state"] == "RESOLVED":
            require(r["bond_id"] in bonds if r["target_kind"] == "BOND" else
                any(i["id"] == r["legal_issuer_id"] for i in tables["legal_issuers"]))
        publication_gate(r, datetime.min.replace(tzinfo=timezone.utc))
        ratings[(r["target_kind"], r["bond_id"] if r["target_kind"] == "BOND" else r["legal_issuer_id"])].append(r)
    windows = defaultdict(list)
    for r in data.decision_windows:
        windows[r["entry_date"]].append(r)
    dates = tuple(r["trade_date"] for r in data.market_date_counts)
    inventories = [_inventory(t,rows,data.label_nonnull_counts) for t,rows in sorted(tables.items())]
    ofz_rows = [r for r in tables["bond_market_snapshots"] if r["bond_id"] in ofz and
        _ofz_market_inputs(r) and _ofz_structure(bonds[r["bond_id"]],profiles.get(r["bond_id"]),r["trade_date"])]
    ofz_dates = defaultdict(list)
    for r in ofz_rows:ofz_dates[r["trade_date"]].append(r)
    ofz_date_coverage = tuple(MarketDateCoverage(trade_date=day,row_count=len(rows),bond_count=len({r["bond_id"] for r in rows}),
        positive_duration_node_count=len({normalize_moex_duration(r["raw_payload"],stored_duration_years=r["duration_years"]).duration_years for r in rows}))
        for day,rows in sorted(ofz_dates.items()))
    inventories.append(_inventory("ofz_current_eligibility_market_subset",ofz_rows,{}))
    extra_counts = {"bonds_with_MOEX_history":len(histories), "bonds_without_MOEX_history":len(set(bonds)-set(histories)),
        "historical_bonds_without_cashflows":len(set(histories)-set(flows)),
        "canonical_OFZ_identity_count":len(ofz),
        "historical_bonds_current_nonactive":sum(profiles.get(bid,{}).get("listing_status") in ("inactive","delisted","defaulted") for bid in histories)}
    for n,item in enumerate(inventories):
        if item.source_table == "bonds":
            inventories[n] = item.model_copy(update={"bond_count":len(bonds), "breakdowns":tuple(sorted((*item.breakdowns,
                *(CountEntry(key=k,count=v,pct=pct(v,len(bonds))) for k,v in extra_counts.items())), key=lambda e:e.key))})
    # Field-specific timestamp coverage must remain visible rather than one global range.
    evidence = tables["bond_security_master_evidence"]
    for field in sorted({r["field_name"] for r in evidence}):
        inventories.append(_inventory("bond_security_master_evidence:"+field,
            [r for r in evidence if r["field_name"] == field], {}))
    per_date = []
    for day in dates:
        require(day > date.min + timedelta(days=30) and day <= date.max - timedelta(days=97))
        cutoff = datetime.combine(day,time.min,timezone.utc)
        observed = {bid for bid, ds in history_dates.items() if bisect_left(ds,day) > 0}
        entry, liquid, credit, pub, terms, duration, outcomes, coupon = set(),set(),set(),set(),set(),set(),set(),set()
        joint, peer, components, capacity, numeric_inputs = set(),set(),set(),set(),set()
        cohorts = defaultdict(set)
        flags = set(P0)
        diagnostics = {"CURRENT_TERMS_USED_FOR_DIAGNOSTIC_ONLY", "DIRTY_VALUE_INPUT_COVERAGE_NOT_PRICING",
            "OFZ_NODES_ARE_MACAULAY_INPUT_COVERAGE_NOT_DURATION_MATCH", "CASHFLOW_ABSENCE_NOT_PROOF"}
        states = Counter()
        nodes = set()
        for w in windows[day]:
            bid = w["bond_id"]
            if w["observation_days"] >= 5:
                liquid.add(bid)
            ds = history_dates[bid]; prefix = liquidity_prefix[bid]
            left, right = bisect_left(ds,day-timedelta(days=30)), bisect_left(ds,day)
            val, num, positive = tuple(b-a for a,b in zip(prefix[left],prefix[right]))
            if val and num:
                components.add(bid)
                if positive >= (val+1)//2:
                    capacity.add(bid)
            p = profiles.get(bid)
            if _profile_ready(p):
                terms.add(bid)
            snap = quotes.get((bid,w["decision_trade_date"]))
            if snap:
                require(snap["trade_date"] < day and (day-snap["trade_date"]).days <= 7)
            if snap:
                d = normalize_moex_duration(snap["raw_payload"],stored_duration_years=snap["duration_years"])
                if d.status == "READY" and finite(d.duration_years) and d.duration_years >= 0:
                    duration.add(bid)
                if _market_inputs(snap):entry.add(bid)
            if snap and _market_ready(snap,p):
                numeric_inputs.add(bid)
            if snap and bid in ofz and _ofz_market_inputs(snap) and _ofz_structure(bonds[bid],p,day):
                d = normalize_moex_duration(snap["raw_payload"], stored_duration_years=snap["duration_years"])
                nodes.add(d.duration_years)
            selected = defaultdict(list)
            target_ids = [("BOND", bid)]
            if bid in current_issuer:
                target_ids.append(("LEGAL_ISSUER", current_issuer[bid]["id"]))
            for target in target_ids:
                for r in ratings[target]:
                    if r["resolution_state"] != "RESOLVED" or r["event_date"] >= day:
                        continue
                    if target[0] == "BOND" and r["source_bond_isin"] != bonds[bid]["isin"]:
                        continue
                    if target[0] == "LEGAL_ISSUER" and r["source_issuer_inn"] != current_issuer[bid]["issuer_inn"]:
                        continue
                    gate = publication_gate(r,cutoff)
                    if gate == "FUTURE":
                        continue
                    selected[(target[0],r["rating_agency"])].append((r,gate))
            keys = []
            for selector, group in selected.items():
                latest = max(r["event_date"] for r,g in group)
                winners = [(r,g) for r,g in group if r["event_date"] == latest]
                if len(winners) != 1:
                    diagnostics.add("MULTIPLE_LATEST_RATING_EVENTS")
                    continue
                r, gate = winners[0]; artifact = artifacts[r["artifact_id"]]
                if (r["contract_version"] != "credit-rating-event-v1" or artifact["contract_version"] != "credit-risk-source-artifact-v1"
                    or not present(artifact["content_sha256"]) or not present(artifact["source_provider"])
                    or not present(r["rating_value_raw"])):
                    continue
                key = (*selector,artifact["source_provider"],r["rating_scale_raw"],r["rating_value_raw"])
                keys.append(key); credit.add(bid)
                if gate == "PROVEN":
                    pub.add(bid)
                else:
                    flags.add("RATING_PUBLICATION_UNKNOWN")
            state, has_coupon, blockers, notes = outcome_coverage(day,intervals.get(bid,()),flows[bid],bonds[bid]["maturity_date"])
            states[state] += 1; diagnostics.update(notes)
            if state == "FULL":
                outcomes.add(bid)
            else:
                diagnostics.update(blockers)
            if has_coupon:
                coupon.add(bid)
            # Task300 requires a verified LegalIssuer for a prospective strategy position.
            if bid in entry & numeric_inputs & liquid & components & capacity & credit & terms & outcomes and bid in current_issuer:
                joint.add(bid)
                for key in keys:
                    cohorts[key].add(bid)
        # Windows omit Bonds with no observations in 30d; count their outcomes independently.
        evaluated = {w["bond_id"] for w in windows[day]}
        for bid in observed - evaluated:
            state, has_coupon, blockers, notes = outcome_coverage(day,intervals.get(bid,()),flows[bid],bonds[bid]["maturity_date"])
            states[state] += 1
            if state == "FULL":outcomes.add(bid)
            if has_coupon:coupon.add(bid)
        for ids in cohorts.values():
            if len(ids) >= 3:
                peer.update(ids)
        if len(liquid & components) < 10:
            diagnostics.add("TASK270_BENCHMARK_UNIVERSE_INSUFFICIENT")
        diagnostics.add("DATA_COVERAGE_NOT_FULL_MODERN_CHAIN_READY")
        classification = classify_date(joint_bond_ids=joint,peer_bond_ids=peer,ofz_nodes=len(nodes),proofs={})
        if len(joint & peer) < 3:flags.add("JOINT_MODERN_INPUT_OUTCOME_SUBSET_INSUFFICIENT")
        if len(nodes) < 2:flags.add("OFZ_TWO_DURATION_NODES_UNAVAILABLE")
        per_date.append(DateReadiness(as_of_date=day,classification=classification,
            observed_universe_bond_count=len(observed),entry_market_ready_bond_count=len(entry),
            liquidity_history_ready_bond_count=len(liquid),credit_evidence_ready_bond_count=len(credit),
            liquidity_component_ready_bond_count=len(components),liquidity_benchmark_eligible_bond_count=len(liquid & components),
            liquidity_benchmark_size_ready=len(liquid & components)>=10,
            rating_publication_proven_bond_count=len(pub),security_master_current_ready_bond_count=len(terms),
            issuer_current_verified_bond_count=len(observed & set(current_issuer)), duration_market_history_bond_count=len(duration),
            ofz_market_ready=len(nodes)>=2,ofz_duration_node_count=len(nodes),outcome_90d_ready_bond_count=len(outcomes),
            outcome_full_count=states["FULL"],outcome_partial_count=states["PARTIAL"],outcome_none_count=states["NONE"],
            coupon_history_ready_bond_count=len(coupon),joint_qualifying_bond_ids=tuple(sorted(joint)),
            credit_peer_qualified_bond_ids=tuple(sorted(peer)),
            first_observation_after_entry_bond_count=sum(ds[0]>day for ds in history_dates.values()),
            blockers=tuple(sorted(flags)),diagnostics=tuple(sorted(diagnostics))))
    monthly = {}
    for r in per_date:
        if r.classification != "UNUSABLE":monthly.setdefault((r.as_of_date.year,r.as_of_date.month),r.as_of_date)
    counts = sorted(r["bond_count"] for r in data.market_date_counts)
    middle = len(counts)//2
    median = (Decimal(counts[middle]) if len(counts)%2 else (Decimal(counts[middle-1])+Decimal(counts[middle]))/2) if counts else Decimal("0")
    ranges = tuple(ObservedBondHistory(bond_id=bid,snapshot_count=len(histories[bid]),
        first_trade_date=history_dates[bid][0] if bid in history_dates else None,
        last_trade_date=history_dates[bid][-1] if bid in history_dates else None,
        current_listing_status=profiles[bid]["listing_status"] if bid in profiles else None,
        canonical_ofz=bid in ofz,cashflow_count=len(flows[bid])) for bid in sorted(bonds))
    diagnostic = [r.as_of_date for r in per_date if r.classification == "DIAGNOSTIC_ONLY"]
    blockers = set(P0)
    if any(r["publication_precision"] == "UNKNOWN" for r in tables["credit_rating_events"]):blockers.add("RATING_PUBLICATION_UNKNOWN")
    if tables["controlled_financial_statement_values"] or any(r["publication_status"] == "UNKNOWN" for r in tables["cbr_bank_report_snapshots"]):
        blockers.add("FINANCIAL_PUBLICATION_UNKNOWN")
    credit_groups = defaultdict(list)
    for r in tables["credit_rating_events"]:
        key = (r["target_kind"],r["bond_id"],r["legal_issuer_id"],r["rating_agency"],
            artifacts[r["artifact_id"]]["source_provider"],r["rating_scale_raw"],r["rating_value_raw"])
        credit_groups[key].append(r)
    credit_inventory = []
    for key, rows in sorted(credit_groups.items(),key=lambda item:json.dumps(item[0],ensure_ascii=True,separators=(",",":"))):
        target,bid,iid,agency,provider,scale,value = key
        publications = Counter(r["publication_precision"] for r in rows)
        credit_inventory.append(CreditTargetInventory(target_kind=target,bond_id=bid,legal_issuer_id=iid,
            rating_agency=agency,source_provider=provider,rating_scale_raw=scale,rating_value_raw=value,
            event_count=len(rows),first_event_date=min(r["event_date"] for r in rows),last_event_date=max(r["event_date"] for r in rows),
            publication_breakdown=tuple(CountEntry(key=k,count=v,pct=pct(v,len(rows))) for k,v in sorted(publications.items()))))
    return signed(Audit(status="COMPLETE",audit_classification="DIAGNOSTIC_REPLAY_ONLY" if diagnostic else "INSUFFICIENT_FOR_REPLAY",
        source_inventories=tuple(sorted(inventories,key=lambda i:i.source_table)),evidence_matrix=_matrix(tables,ofz_rows),
        credit_target_inventory=tuple(credit_inventory),
        bond_histories=ranges,market_date_coverage=tuple(MarketDateCoverage(**dict(r)) for r in data.market_date_counts),
        ofz_market_date_coverage=ofz_date_coverage,
        per_date_readiness=tuple(per_date),all_candidate_date_count=len(dates),monthly_candidate_date_count=len(monthly),
        diagnostic_only_date_count=len(diagnostic),unusable_date_count=len(dates)-len(diagnostic),
        earliest_candidate_date=dates[0] if dates else None, latest_candidate_date=dates[-1] if dates else None,
        earliest_diagnostic_date=min(diagnostic) if diagnostic else None,latest_diagnostic_date=max(diagnostic) if diagnostic else None,
        window_summary=Window(all_observed_candidate_dates=dates,monthly_candidate_dates=tuple(monthly.values()),
            minimum_bonds_per_trade_date=min(counts) if counts else 0, median_bonds_per_trade_date=median,
            maximum_bonds_per_trade_date=max(counts) if counts else 0,
            missing_calendar_date_count=(dates[-1]-dates[0]).days+1-len(dates) if dates else 0),
        known_p0_blockers=tuple(sorted(blockers)),remediation_requirements=REMEDIATION))


class ModernHistoricalReplayReadinessAuditService:
    def __init__(self, db):
        self.db = db

    def build(self, *, market_source="moex", horizon_days=90,
              liquidity_lookback_calendar_days=30, liquidity_min_observation_days=5):
        if type(market_source) is not str or market_source != "moex" or any(type(v) is not int or v != expected
            for v,expected in ((horizon_days,90),(liquidity_lookback_calendar_days,30),(liquidity_min_observation_days,5))):
            raise ValueError("INVALID_AUDIT_CONFIGURATION")
        try:
            with self.db.no_autoflush, localcontext(Context(prec=28,rounding=ROUND_HALF_EVEN)):
                return _build(read_evidence(self.db))
        except SQLAlchemyError:
            return signed(Audit(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY",
                known_p0_blockers=("REQUIRED_SCHEMA_OR_DB_STATE_UNAVAILABLE",)))
        except (ValueError, TypeError, KeyError, OverflowError):
            return signed(Audit(status="BLOCKED",audit_classification="INSUFFICIENT_FOR_REPLAY",
                known_p0_blockers=("INVALID_DB_EVIDENCE",)))
