"""Historical discovery never uses the current Bond table as its population."""
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import sqlite3
from app.schemas.historical_evidence_foundation import (
    HistoricalAuthorization, HistoricalDiscovery, HistoricalEvidencePolicy, HistoricalSecurity, HistoricalSourceQuery,
)
from app.services.historical_evidence_source_client import HistoricalSourceError
from app.services.historical_evidence_normalization import normalize, sha, signed, checked
from app.services.ofz_identity import is_ofz_instrument


def discovery_queries(policy):
    yield HistoricalSourceQuery(family="DATES",table="dates")
    yield HistoricalSourceQuery(family="COLUMNS",table="history")
    yield HistoricalSourceQuery(family="LISTING",table="securities")
    day=policy.history_start
    while day<=policy.cutoff:
        yield HistoricalSourceQuery(family="MARKET",table="history",trade_date=day)
        day+=timedelta(days=1)


def read_scope(policy):return sha({"operation":"READ_SOURCE","policy":policy})


def pages(client,query,*,max_pages=100000):
    # Scratch uniqueness index is private and deleted even on source failure.
    # Retaining millions of row hashes in a Python set would violate the budget.
    with TemporaryDirectory(prefix="bondradar-a3-pages-") as scratch:
        index=sqlite3.connect(str(Path(scratch)/"seen.sqlite"))
        try:
            index.execute("PRAGMA cache_size=-1024")
            index.execute("CREATE TABLE seen (kind TEXT, hash TEXT, PRIMARY KEY(kind,hash)) WITHOUT ROWID")
            total=None;cursor_started=False;page_size=None
            for _ in range(max_pages):
                try:page=client.fetch_page(query);checked(page,"page_sha256")
                except (ValueError,TypeError):raise HistoricalSourceError("SOURCE_PAGE_CONTRACT_INVALID") from None
                if page.query!=query:raise HistoricalSourceError("SOURCE_QUERY_BINDING_CONFLICT")
                content=sha(tuple(sorted((sha(row) for row in page.rows))))
                if page.rows:
                    try:index.execute("INSERT INTO seen VALUES ('PAGE',?)",(content,))
                    except sqlite3.IntegrityError:raise HistoricalSourceError("REPEATED_SOURCE_PAGE") from None
                if cursor_started and page.cursor_total is None:raise HistoricalSourceError("SOURCE_CURSOR_DISAPPEARED")
                if page.cursor_total is not None:
                    if total is not None and total!=page.cursor_total:raise HistoricalSourceError("SOURCE_TOTAL_CHANGED")
                    if page_size is not None and page_size!=page.cursor_page_size:raise HistoricalSourceError("SOURCE_PAGE_SIZE_CHANGED")
                    page_size=page.cursor_page_size
                    total=page.cursor_total;cursor_started=True
                try:index.executemany("INSERT INTO seen VALUES ('ROW',?)",((sha(row),) for row in page.rows))
                except sqlite3.IntegrityError:raise HistoricalSourceError("OVERLAPPING_SOURCE_ROWS") from None
                index.commit()
                yield page
                if page.complete:return
                query=query.model_copy(update={"offset":page.next_offset})
            raise HistoricalSourceError("SOURCE_PAGINATION_EXHAUSTED")
        finally:index.close()


class HistoricalEvidenceDiscoveryService:
    def __init__(self,source_client):self.source_client=source_client

    def discover_bounded(self, *, policy, evidence_root, authorization, limits=None):
        from app.services.historical_discovery_checkpoint import run_discovery
        return run_discovery(source_client=self.source_client, policy=policy, evidence_root=evidence_root,
                             authorization=authorization, limits=limits)

    def discover(self,*,policy,authorization,page_sink=None):
        if type(policy) is not HistoricalEvidencePolicy or type(authorization) is not HistoricalAuthorization:
            raise ValueError("DISCOVERY_REQUEST_REQUIRED")
        HistoricalEvidencePolicy.model_validate({k:getattr(policy,k) for k in type(policy).model_fields})
        if (policy.cutoff-policy.history_start).days>31:raise ValueError("DURABLE_DISCOVERY_REQUIRED")
        scope=read_scope(policy)
        if authorization.operation!="READ_SOURCE" or authorization.plan_sha256!=scope or authorization.scope_sha256!=scope or authorization.source_manifest_sha256!=scope or authorization.current_db_sha256!=sha({}):
            raise ValueError("SOURCE_READ_AUTHORIZATION_MISMATCH")
        securities={};dates=set();hashes=[];errors=set()
        for query in discovery_queries(policy):
            try:
                for page in pages(self.source_client,query):
                    if page_sink:page_sink(page)
                    hashes.append(page.page_sha256)
                    if query.family not in ("MARKET","LISTING"):continue
                    for row in page.rows:
                        evidence=normalize(query,row)
                        day=evidence["event_date"]
                        if day:dates.add(day)
                        fields=evidence["values"]
                        identity=HistoricalSecurity(secid=evidence["secid"],isin=evidence["isin"],board=evidence["board"],
                            interval_start=__import__("datetime").date.fromisoformat(fields["start"]) if query.family=="LISTING" and fields["start"] else None,
                            interval_end=__import__("datetime").date.fromisoformat(fields["end"]) if query.family=="LISTING" and fields["end"] else None,
                            authority="SOURCE_LISTING" if query.family=="LISTING" else "OBSERVED_HISTORY")
                        securities[sha(identity)]=identity
            except HistoricalSourceError as exc:errors.add(query.family+":"+exc.code)
            except (ValueError,TypeError):errors.add(query.family+":SOURCE_BINDING_INVALID")
        return signed(HistoricalDiscovery,"source_manifest_sha256",policy=policy,
            securities=tuple(securities[k] for k in sorted(securities)),observed_dates=tuple(sorted(dates)),source_page_hashes=tuple(hashes),
            status="PARTIAL" if errors else "COMPLETE",blockers=tuple(sorted(errors)))


def acquisition_queries(discovery,representatives=()):
    from app.services.historical_discovery_checkpoint import VerifiedDiscoveryIndex
    if type(discovery) is not VerifiedDiscoveryIndex:
        checked(discovery,"source_manifest_sha256")
    if discovery.status!="COMPLETE":raise ValueError("DISCOVERY_INCOMPLETE")
    # Calendar requests include absent days; a short source tail never shortens policy.
    yield from discovery_queries(discovery.policy)
    exact={}
    if type(discovery) is VerifiedDiscoveryIndex:
        from app.services.historical_discovery_checkpoint import iterate_index
        bindings=((item["secid"],item["isin"]) for item in iterate_index(discovery.path("bindings")))
    else:
        for security in discovery.securities:
            key=(security.secid,security.isin)
            if key not in exact:exact[key]=security
        bindings=sorted(exact,key=lambda item:(item[0],item[1] or ""))
    selected={(r.secid,r.isin) for r in representatives}
    for secid,isin in bindings:
        structural=not is_ofz_instrument(isin=isin,secid=secid) or (secid,isin) in selected
        # Exact reference may resolve a SECID observed in history without ISIN;
        # its result remains current observation, not historical linkage proof.
        yield HistoricalSourceQuery(family="REFERENCE",table="securities",secid=secid,expected_isin=isin)
        if structural:
            yield HistoricalSourceQuery(family="DESCRIPTION",table="description",secid=secid,expected_isin=isin)
        for table in ("coupons","amortizations","redemptions","offers"):
            yield HistoricalSourceQuery(family="CASHFLOWS",table=table,secid=secid,expected_isin=isin)
