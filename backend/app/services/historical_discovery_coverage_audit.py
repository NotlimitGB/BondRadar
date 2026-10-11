"""Task307 coverage analytics. No source requests or application database."""
from collections import Counter
from datetime import date, timedelta
from decimal import Decimal, Context, localcontext, ROUND_HALF_EVEN, InvalidOperation
import sqlite3
from app.schemas.historical_discovery_coverage_audit import (
    AuditLimits, Metric, Integrity, DiscoveryCoverage, UniverseCoverage, IdentityDiagnostic,
    IdentityCoverage, PeriodCoverage, HorizonCoverage, EntryCoverage, DateCoverage,
    BoardCoverage, PitEvidence, Remediation, Capabilities, HistoricalDiscoveryCoverageAudit,
)
from app.services.historical_discovery_coverage_reader import workspace, CoverageReader, AuditFailure
from app.services.historical_evidence_normalization import normalize
from app.services.historical_audit_canonical_json import digest, chunks
from app.services.ofz_identity import is_ofz_instrument


NAMES = ("total","actual","zero","missing","malformed","negative","fractional","nonfinite",
         "clean","nkd","usable","joint","usablezero","usableunknown","ofz","ofzactual","ofzusable",
         "cleanmissing","cleaninvalid","nkdmissing","nkdinvalid")


def metric(name, count, denominator):
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        pct = Decimal(count) * Decimal("100") / denominator if denominator else Decimal("0")
    return Metric(name=name,count=count,denominator=denominator,percentage=pct)


def signed_report(**values):
    report = HistoricalDiscoveryCoverageAudit(**values)
    return report.model_copy(update={"audit_sha256":digest(report)})


def blocked(code, manifest=None):
    values = {}
    if manifest is not None:
        values = {k:getattr(manifest,k) for k in ("source_manifest_sha256","source_content_sha256","scope_sha256","implementation_sha256")}
    return signed_report(status="BLOCKED",readiness_blockers=(code,),
        remediation_priorities=(Remediation(priority="P0",reason=code,affected_count=None,
            missing_proof="Validated source integrity or approved resource capacity",source_family="DISCOVERY",
            required_step="Review the failure; do not repair or continue acquisition automatically",
            validation="Re-run source-only audit after separately reviewed resolution",dependency="Task306A4"),),**values)


def positive(value):
    return value is not None and Decimal(str(value)).is_finite() and Decimal(str(value)) > 0


def nonnegative(value):
    return value is not None and Decimal(str(value)).is_finite() and Decimal(str(value)) >= 0


def trade_class(raw):
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return _trade_class(raw)


def _trade_class(raw):
    if raw is None or (type(raw) is str and not raw.strip()):
        return "missing"
    if type(raw) is bool or type(raw) not in (str,int,float,Decimal):
        return "malformed"
    try:
        number = Decimal(str(raw))
    except InvalidOperation:
        return "malformed"
    if not number.is_finite(): return "nonfinite"
    if number < 0: return "negative"
    if number != number.to_integral_value(): return "fractional"
    return "actual" if number > 0 else "zero"


class Aggregation:
    def __init__(self, db):
        self.db = db
        self.counts = Counter()

    def market(self, query, row, item, key):
        with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
            return self._market(query,row,item,key)

    def _market(self, query, row, item, key):
        native = {k.upper():v for k,v in row.items()}
        trade = trade_class(native.get("NUMTRADES"))
        values = normalize(query,row)["values"]
        clean = positive(values.get("clean_price"))
        nkd = nonnegative(values.get("nkd"))
        chosen = values.get("clean_price") if values.get("clean_price") is not None else values.get("price")
        usable = positive(chosen) and nkd
        ofz = is_ofz_instrument(isin=item.isin,secid=item.secid)
        clean_present = any(v is not None and str(v).strip() for k,v in row.items() if k.upper()=="LEGALCLOSEPRICE")
        nkd_present = any(v is not None and str(v).strip() for k,v in row.items() if k.upper() in ("ACCINT","ACCRUEDINT"))
        c = dict.fromkeys(NAMES,0)
        c.update(total=1,clean=int(clean),nkd=int(nkd),usable=int(usable),joint=int(usable and trade=="actual"),
                 usablezero=int(usable and trade=="zero"),usableunknown=int(usable and trade not in ("actual","zero")),
                 ofz=int(ofz),ofzactual=int(ofz and trade=="actual"),ofzusable=int(ofz and usable),
                 cleanmissing=int(not clean_present),cleaninvalid=int(clean_present and not clean),
                 nkdmissing=int(not nkd_present),nkdinvalid=int(nkd_present and not nkd))
        c[trade] = 1
        self.counts.update(c)
        day, board = str(query.trade_date), item.board
        columns = ",".join(NAMES)
        updates = ",".join(f"{name}={name}+excluded.{name}" for name in NAMES)
        self.db.execute(f"INSERT INTO stats(day,board,{columns}) VALUES ({','.join('?' for _ in range(len(NAMES)+2))}) "
                        f"ON CONFLICT(day,board) DO UPDATE SET {updates}", (day,board,*(c[k] for k in NAMES)))
        self.db.execute("""INSERT INTO daily VALUES (?,?,?,?,?) ON CONFLICT(day,key) DO UPDATE SET
            actual=max(actual,excluded.actual),usable=max(usable,excluded.usable),joint=max(joint,excluded.joint)""",
            (day,key,c["actual"],c["usable"],c["joint"]))
        self.db.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?)", (key,day,board))
        self.db.execute("INSERT OR IGNORE INTO board_identity VALUES (?,?)", (board,key))
        self.db.execute("INSERT OR IGNORE INTO board_date VALUES (?,?)", (board,day))
        if ofz:self.db.execute("INSERT OR IGNORE INTO ofz_identity VALUES (?)", (key,))

    def count(self, sql, args=()):
        return self.db.execute(sql,args).fetchone()[0]

    def identities(self):
        result = []
        for key,secid,isin,listing,observed,first,last,start,end in self.db.execute("SELECT * FROM binding ORDER BY secid,isin"):
            boards = lambda authority: tuple(r[0] for r in self.db.execute(
                "SELECT board FROM identity_board WHERE key=? AND authority=? ORDER BY board",(key,authority)))
            # Deduplicate days through the key-date-board PK, then indexed day/key join.
            day_count,actual,usable = self.db.execute("""SELECT count(*),coalesce(sum(d.actual),0),coalesce(sum(d.usable),0)
                FROM (SELECT DISTINCT day FROM observations WHERE key=?) o
                JOIN daily d ON d.day=o.day AND d.key=?""",(key,key)).fetchone()
            candidates = tuple(r[0] for r in self.db.execute("SELECT isin FROM binding WHERE secid=? AND isin IS NOT NULL ORDER BY isin",(secid,)))
            result.append(IdentityDiagnostic(secid=secid,isin=isin,listing=bool(listing),observed=bool(observed),
                listing_boards=boards("SOURCE_LISTING"),observed_boards=boards("OBSERVED_HISTORY"),
                first_observed_date=date.fromisoformat(first) if first else None,
                last_observed_date=date.fromisoformat(last) if last else None,
                first_listing_interval=date.fromisoformat(start) if start else None,
                last_listing_interval=date.fromisoformat(end) if end else None,observed_date_count=day_count,
                actual_trade_date_count=actual,usable_price_nkd_date_count=usable,
                conflicting_isin_candidates=candidates if len(candidates)>1 else ()))
        return tuple(result)

    def periods(self, width):
        result = []
        sums = ",".join(f"sum({k})" for k in ("total","actual","zero","usable","joint"))
        for period,total,actual,zero,usable,joint,dates in self.db.execute(
            f"SELECT substr(day,1,{width}),{sums},count(DISTINCT day) FROM stats GROUP BY substr(day,1,{width}) ORDER BY 1"):
            identities = self.count("SELECT count(DISTINCT key) FROM daily WHERE substr(day,1,?)=?",(width,period))
            result.append(PeriodCoverage(period=period,market_rows=total,observed_dates=dates,
                actual_trade_rows=actual,zero_trade_rows=zero,unknown_trade_rows=total-actual-zero,
                usable_price_nkd_rows=usable,actual_and_usable_rows=joint,distinct_identities=identities))
        return tuple(result)

    def dates(self, condition="total>0"):
        return tuple(date.fromisoformat(r[0]) for r in self.db.execute(
            f"SELECT DISTINCT day FROM stats WHERE {condition} ORDER BY day"))

    def entries(self, policy, observed):
        result = []
        current, end = policy.entry_span
        while current <= end:
            month = current.strftime("%Y-%m")
            row = self.db.execute("SELECT min(day) FROM stats WHERE substr(day,1,7)=?",(month,)).fetchone()
            entry = date.fromisoformat(row[0]) if row[0] else None
            n = a = u = j = rows = 0
            if entry:
                n,a,u,j = self.db.execute("SELECT count(*),coalesce(sum(actual),0),coalesce(sum(usable),0),coalesce(sum(joint),0) FROM daily WHERE day=?",(str(entry),)).fetchone()
                rows = self.count("SELECT sum(total) FROM stats WHERE day=?",(str(entry),))
            horizons = []
            for h in policy.horizons:
                target = entry+timedelta(days=h) if entry else None
                status = "NO_ENTRY_DATE" if not entry else "BEYOND_CUTOFF" if target>policy.cutoff else "OBSERVED_RANGE_REACHES_TARGET" if observed and observed[-1]>=target else "OBSERVED_RANGE_SHORT"
                count = None
                if entry and target<=policy.cutoff:
                    count = self.count("""SELECT count(*) FROM daily e WHERE e.day=? AND EXISTS
                        (SELECT 1 FROM observations o WHERE o.key=e.key AND o.day>? AND o.day<=?)""",
                        (str(entry),str(entry),str(target)))
                horizons.append(HorizonCoverage(horizon_days=h,target_date=target,status=status,
                    entry_identities_with_subsequent_observation=count))
            reasons = {"DECISION_PUBLICATION_UNPROVEN"}
            if not entry: reasons.add("NO_OBSERVED_ENTRY_DATE")
            if entry and not j: reasons.add("NO_ACTUAL_TRADE_AND_USABLE_PRICE_INTERSECTION")
            result.append(EntryCoverage(month=month,entry_date=entry,source_rows=rows,identities=n,
                actual_trade_identities=a,usable_price_nkd_identities=u,jointly_covered_identities=j,
                diagnostics=tuple(sorted(reasons)),horizons=tuple(horizons)))
            current = date(current.year+1,1,1) if current.month==12 else date(current.year,current.month+1,1)
        return tuple(result)

    def boards(self):
        result = []
        for (board,) in self.db.execute("SELECT DISTINCT board FROM identity_board ORDER BY board"):
            total,actual,zero,usable = self.db.execute("SELECT coalesce(sum(total),0),coalesce(sum(actual),0),coalesce(sum(zero),0),coalesce(sum(usable),0) FROM stats WHERE board=?",(board,)).fetchone()
            n,first,last = self.db.execute("SELECT count(*),min(day),max(day) FROM board_date WHERE board=?",(board,)).fetchone()
            identities = self.count("SELECT count(*) FROM board_identity WHERE board=?",(board,))
            result.append(BoardCoverage(board=board,market_rows=total,distinct_identities=identities,observed_dates=n,
                first_date=date.fromisoformat(first) if first else None,last_date=date.fromisoformat(last) if last else None,
                actual_trade_rows=actual,zero_trade_rows=zero,unknown_trade_rows=total-actual-zero,usable_price_nkd_rows=usable))
        return tuple(result)


def pit_matrix():
    families = ("market_history","historical_universe_membership","security_identifiers","issuer_linkage",
                "contractual_cashflows","coupons","amortizations","redemptions","offers","ratings",
                "financial_statements","default_credit_events","bid_ask_execution_costs")
    return tuple(PitEvidence(family=f,
        available_evidence="Trade-date observations" if f=="market_history" else "Source listing intervals and observed identifiers" if f in ("historical_universe_membership","security_identifiers") else "NOT_ACQUIRED",
        unavailable_evidence="Historical publication/version completeness; decision cutoff authority",
        provenance="TRADE_DATE_NOT_PUBLICATION; RETRIEVAL_NOT_HISTORICAL_AVAILABILITY" if f=="market_history" else "LISTING_EFFECTIVE_INTERVAL_NOT_PUBLICATION" if f in ("historical_universe_membership","security_identifiers") else "NO_ACCEPTED_DISCOVERY_FAMILY",
        decision_time_usability="UNPROVEN" if f in ("market_history","historical_universe_membership","security_identifiers") else "NOT_ACQUIRED",
        blocker="HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN" if f=="historical_universe_membership" else "HISTORICAL_PUBLICATION_UNPROVEN" if f in ("market_history","security_identifiers") else "SOURCE_FAMILY_NOT_ACQUIRED") for f in families)


class HistoricalDiscoveryCoverageAuditService:
    def build(self, *, evidence_root, scratch_dir, expected_manifest_sha256=None, limits=None):
        limits = AuditLimits() if limits is None else limits
        if type(limits) is not AuditLimits: raise ValueError("AUDIT_LIMITS_REQUIRED")
        limits = AuditLimits.model_validate({k:getattr(limits,k) for k in AuditLimits.model_fields})
        reader = None
        try:
            with workspace(evidence_root,scratch_dir,limits) as (root,db,budget):
                reader = CoverageReader(root,db,budget,expected_manifest_sha256=expected_manifest_sha256)
                a = Aggregation(db)
                reader.scan(a.market)
                m, c = reader.manifest, a.counts
                observed = a.dates()
                records = a.count("SELECT count(*) FROM identity")
                listing_records = a.count("SELECT count(*) FROM identity WHERE authority='SOURCE_LISTING'")
                bindings = a.count("SELECT count(*) FROM binding")
                missing = a.count("SELECT count(*) FROM binding WHERE isin IS NULL")
                conflicts = a.count("SELECT count(*) FROM (SELECT secid FROM binding WHERE isin IS NOT NULL GROUP BY secid HAVING count(*)>1)")
                boards = a.boards()
                checks = {"identity_count":records,"exact_binding_count":bindings,"listing_identity_count":listing_records,
                    "observed_identity_count":records-listing_records,"board_count":len(boards),"unresolved_binding_count":missing,
                    "identity_conflict_secid_count":conflicts,"market_row_count":c["total"],"actual_trade_row_count":c["actual"],
                    "zero_trade_row_count":c["zero"],"unknown_trade_row_count":c["total"]-c["actual"]-c["zero"],
                    "usable_price_row_count":c["usable"],"source_page_count":reader.pages,
                    "completed_partition_count":len(reader.states),"absent_partition_count":len(reader.empty_dates)}
                if any(getattr(m,k)!=v for k,v in checks.items()) or m.observed_dates!=observed:
                    raise AuditFailure("SOURCE_MANIFEST_METRIC_RECONCILIATION_FAILED")
                identities = a.identities()
                budget.check()
                listing = sum(i.listing for i in identities); history = sum(i.observed for i in identities)
                shared = sum(i.listing and i.observed for i in identities)
                affected_dates = tuple(date.fromisoformat(r[0]) for r in db.execute("SELECT DISTINCT o.day FROM observations o JOIN binding b ON b.key=o.key WHERE b.isin IS NULL ORDER BY o.day"))
                discrepancies = tuple(r[0] for r in db.execute("SELECT secid FROM binding GROUP BY secid HAVING max(listing)>0 AND max(observed)>0 AND min(listing+observed)<2 ORDER BY secid"))
                universe = UniverseCoverage(identity_records=records,distinct_secids=a.count("SELECT count(DISTINCT secid) FROM binding"),
                    distinct_isins=a.count("SELECT count(DISTINCT isin) FROM binding WHERE isin IS NOT NULL"),exact_pairs=bindings,
                    secid_board_pairs=a.count("SELECT count(*) FROM (SELECT DISTINCT b.secid,i.board FROM identity_board i JOIN binding b ON b.key=i.key)"),
                    listing_pairs=listing,observed_pairs=history,listing_only_pairs=listing-shared,observed_only_pairs=history-shared,
                    shared_pairs=shared,missing_isin_pairs=missing,affected_missing_isin_secids=a.count("SELECT count(DISTINCT secid) FROM binding WHERE isin IS NULL"),
                    conflicting_secids=conflicts,multi_board_pairs=a.count("SELECT count(*) FROM (SELECT key FROM identity_board GROUP BY key HAVING count(DISTINCT board)>1)"),
                    not_observed_on_last_market_date_pairs=sum(i.observed and i.last_observed_date!=observed[-1] for i in identities) if observed else 0)
                blockers = {"HISTORICAL_UNIVERSE_MEMBERSHIP_UNPROVEN","SURVIVORSHIP_BIAS_RISK","HISTORICAL_PUBLICATION_UNPROVEN",
                    "REFERENCE_AND_CASHFLOWS_NOT_ACQUIRED","EXECUTION_FEASIBILITY_UNPROVEN","OFZ_DURATION_MATCH_NOT_EVALUATED"}
                if missing: blockers.add("SOURCE_ISIN_MISSING")
                if conflicts: blockers.add("SOURCE_EXACT_BINDING_CONFLICT")
                remediation = [Remediation(priority="P1",reason=r,affected_count=missing if r=="SOURCE_ISIN_MISSING" else conflicts if r=="SOURCE_EXACT_BINDING_CONFLICT" else None,
                    missing_proof="Historical completeness, exact binding or publication authority",source_family="REFERENCE/LISTING/CASHFLOWS",
                    required_step="Specify a reviewed evidence request from audit diagnostics before acquisition",
                    validation="Exact source binding, dated provenance and completeness review",dependency="Task307 review")
                    for r in sorted(blockers) if r not in ("EXECUTION_FEASIBILITY_UNPROVEN","OFZ_DURATION_MATCH_NOT_EVALUATED")]
                remediation += [Remediation(priority="P2",reason=r,affected_count=c["ofz"] if r.startswith("OFZ") else None,
                    missing_proof="Verified terms/duration eligibility or execution-cost evidence",source_family="DESCRIPTION/EXECUTION_MARKET",
                    required_step="Review missing source contracts; no automatic acquisition",validation="Historical terms and availability authority",dependency="P1 evidence qualification")
                    for r in ("EXECUTION_FEASIBILITY_UNPROVEN","OFZ_DURATION_MATCH_NOT_EVALUATED")]
                report = signed_report(status="COMPLETE",source_manifest_sha256=m.source_manifest_sha256,source_content_sha256=m.source_content_sha256,
                    scope_sha256=m.scope_sha256,implementation_sha256=m.implementation_sha256,
                    evidence_integrity=Integrity(status="VERIFIED",manifest_verified=True,indexes_verified=True,receipt_pages_verified=True,
                        index_replay_verified=True,reconciliation_verified=True,verification_level="FULL_ACCEPTED_GENERATION_REPLAY"),
                    discovery_coverage=DiscoveryCoverage(history_start=m.policy.history_start,cutoff=m.policy.cutoff,requested_partitions=len(reader.states),
                        completed_partitions=len(reader.states),accepted_pages=reader.pages,http_attempts=reader.http_attempts,retries=reader.retries,
                        empty_market_dates=tuple(reader.empty_dates),source_families=("COLUMNS","DATES","LISTING","MARKET"),
                        source_observation_first=reader.observation_first,source_observation_last=reader.observation_last),
                    universe_coverage=universe,identity_coverage=IdentityCoverage(missing_isin_listing_records=a.count("SELECT count(*) FROM identity WHERE isin IS NULL AND authority='SOURCE_LISTING'"),
                        missing_isin_history_records=a.count("SELECT count(*) FROM identity WHERE isin IS NULL AND authority='OBSERVED_HISTORY'"),
                        affected_market_dates=affected_dates,listing_market_discrepancy_secids=discrepancies,identities=identities),
                    market_quality=tuple(metric(k,c[k],c["total"]) for k in ("total","clean","nkd","usable","joint","cleanmissing","cleaninvalid","nkdmissing","nkdinvalid")),
                    trading_activity=tuple(metric(k,c[k],c["total"]) for k in ("actual","zero","missing","malformed","negative","fractional","nonfinite")),
                    price_nkd_coverage=tuple(metric(k,c[k],c["total"]) for k in ("clean","nkd","usable","joint","usablezero","usableunknown")),
                    date_coverage=DateCoverage(observed_dates=observed,actual_trade_dates=a.dates("actual>0"),usable_price_nkd_dates=a.dates("usable>0"),
                        actual_and_usable_dates=a.dates("joint>0"),unknown_trade_dates=a.dates("total>actual+zero"),annual=a.periods(4),monthly=a.periods(7),research_entries=a.entries(m.policy,observed)),
                    board_coverage=boards,ofz_coverage=tuple(metric(k,c[k],c["total"]) for k in ("ofz","ofzactual","ofzusable"))+
                        (metric("canonical_ofz_pairs",sum(is_ofz_instrument(isin=i.isin,secid=i.secid) for i in identities),bindings),
                         metric("observed_ofz_pairs",a.count("SELECT count(*) FROM ofz_identity"),bindings),
                         metric("observed_ofz_dates",a.count("SELECT count(DISTINCT o.day) FROM observations o JOIN ofz_identity f ON f.key=o.key"),len(observed))),
                    pit_evidence=pit_matrix(),readiness_blockers=tuple(sorted(blockers)),
                    remediation_priorities=tuple(sorted(remediation,key=lambda r:(r.priority,r.reason))),capabilities=Capabilities(deep_accepted_source_integrity=True))
                if sum(len(p.encode("ascii")) for p in chunks(report)) > limits.output_bytes:
                    raise AuditFailure("OUTPUT_BUDGET_EXCEEDED")
                budget.check()
                reader.finish()
                return report
        except AuditFailure as exc:
            return blocked(exc.code,reader.manifest if reader else None)
        except sqlite3.Error as exc:
            code="SCRATCH_BUDGET_EXCEEDED" if getattr(exc,"sqlite_errorcode",None)==sqlite3.SQLITE_FULL else "SCRATCH_STORAGE_FAILURE"
            return blocked(code,reader.manifest if reader else None)
        except (ValueError,TypeError,KeyError,OSError,InvalidOperation,OverflowError):
            return blocked("SOURCE_EVIDENCE_INVALID_OR_UNREADABLE",reader.manifest if reader else None)
