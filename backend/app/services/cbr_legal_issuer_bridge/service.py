from __future__ import annotations

from collections import Counter
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.legal_issuer import LegalIssuer

from .contracts import (
    CbrBridgeState,
    CbrLegalIssuerBridgeResult,
    CbrLegalIssuerBridgeSnapshot,
    LegalIssuerCandidate,
    canonical_inn,
    canonical_regn,
    identifier_set_sha256,
    utc_datetime,
)
from .finorg import CbrFinOrgClient
from .fullcolist import CbrFullCoListClient
from .transport import CbrIdentityHttpTransport


class LegalIssuerResolver(Protocol):
    def resolve(self, inns: tuple[str, ...]) -> dict[str, tuple[LegalIssuerCandidate, ...]]:
        ...


class LegalIssuerInnResolver:
    def __init__(self, session: Session, *, batch_size: int = 500) -> None:
        if batch_size < 1 or batch_size > 1000:
            raise ValueError("LegalIssuer resolver batch size is invalid")
        self.session = session
        self.batch_size = batch_size

    def resolve(self, inns: tuple[str, ...]) -> dict[str, tuple[LegalIssuerCandidate, ...]]:
        canonical = tuple(sorted({canonical_inn(value) for value in inns}))
        result: dict[str, list[LegalIssuerCandidate]] = {value: [] for value in canonical}
        with self.session.no_autoflush:
            for start in range(0, len(canonical), self.batch_size):
                batch = canonical[start : start + self.batch_size]
                if not batch:
                    continue
                rows = self.session.execute(
                    select(
                        LegalIssuer.id,
                        LegalIssuer.issuer_inn,
                        LegalIssuer.resolution_state,
                        LegalIssuer.source_issuer_id,
                        LegalIssuer.issuer_title,
                    )
                    .where(LegalIssuer.issuer_inn.in_(batch))
                    .order_by(LegalIssuer.issuer_inn, LegalIssuer.id)
                ).all()
                for row in rows:
                    if row.issuer_inn is None:
                        continue
                    result[row.issuer_inn].append(
                        LegalIssuerCandidate(
                            legal_issuer_id=row.id,
                            issuer_inn=row.issuer_inn,
                            resolution_state=row.resolution_state,
                            source_issuer_id=row.source_issuer_id,
                            issuer_title=row.issuer_title,
                        )
                    )
        return {key: tuple(value) for key, value in result.items()}


class CbrLegalIssuerBridgeService:
    def __init__(
        self,
        *,
        fullcolist_client: CbrFullCoListClient | None = None,
        finorg_client: CbrFinOrgClient | None = None,
    ) -> None:
        if fullcolist_client is None and finorg_client is None:
            transport = CbrIdentityHttpTransport()
            fullcolist_client = CbrFullCoListClient(transport)
            finorg_client = CbrFinOrgClient(transport)
        self.fullcolist_client = fullcolist_client or CbrFullCoListClient()
        self.finorg_client = finorg_client or CbrFinOrgClient()

    def bridge_regns(
        self,
        regns,
        *,
        retrieved_at: datetime,
        legal_issuer_resolver: LegalIssuerResolver | None = None,
    ) -> CbrLegalIssuerBridgeSnapshot:
        requested = tuple(sorted({canonical_regn(value) for value in regns}, key=int))
        if not requested or len(requested) > 1000:
            raise ValueError("bridge requires 1..1,000 unique REGNs")
        observed = utc_datetime(retrieved_at, field_name="retrieved_at")
        registry = self.fullcolist_client.fetch(retrieved_at=observed)

        rows_by_regn: dict[str, list] = {}
        for record in registry.records:
            rows_by_regn.setdefault(record.regn, []).append(record)
        registry_ready: dict[str, tuple[str, str | None, tuple[str, ...]]] = {}
        preliminary: dict[str, CbrLegalIssuerBridgeResult] = {}
        for regn in requested:
            rows = rows_by_regn.get(regn, [])
            names = {row.name for row in rows}
            registry_name = next(iter(names)) if len(names) == 1 else None
            diagnostic_warnings = ("cbr_registry_name_variants",) if len(names) > 1 else ()
            if not rows:
                preliminary[regn] = self._result(
                    regn, CbrBridgeState.CBR_REGN_NOT_FOUND
                )
                continue
            if regn in registry.ambiguous_regns:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.CBR_REGN_AMBIGUOUS,
                    registry_name=registry_name,
                    warnings=diagnostic_warnings,
                )
                continue
            ogrns = {row.ogrn for row in rows if row.ogrn is not None}
            if not ogrns:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.CBR_OGRN_MISSING,
                    registry_name=registry_name,
                    warnings=diagnostic_warnings,
                )
                continue
            ogrn = next(iter(ogrns))
            if ogrn in registry.conflicting_ogrns:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.CBR_OGRN_CONFLICT,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    warnings=diagnostic_warnings,
                )
                continue
            registry_ready[regn] = (ogrn, registry_name, diagnostic_warnings)

        requested_ogrns = tuple(sorted({value[0] for value in registry_ready.values()}))
        finorg_last_update = self.finorg_client.get_last_update()
        finorg = self.finorg_client.search_by_ogrns(requested_ogrns)
        finorg_by_ogrn: dict[str, list] = {}
        for record in finorg.records:
            finorg_by_ogrn.setdefault(record.ogrn, []).append(record)

        source_resolved: dict[str, tuple[str, str, str | None, str | None, tuple[str, ...]]] = {}
        for regn, (ogrn, registry_name, warnings) in registry_ready.items():
            rows = finorg_by_ogrn.get(ogrn, [])
            finorg_names = {row.name for row in rows if row.name is not None}
            finorg_name = next(iter(finorg_names)) if len(finorg_names) == 1 else None
            local_warnings = list(warnings)
            if len(finorg_names) > 1:
                local_warnings.append("finorg_name_variants")
            if finorg.source_error or any(row.error_text for row in rows):
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_SOURCE_ERROR,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=tuple(local_warnings),
                )
                continue
            if not rows:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_NOT_FOUND,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    warnings=tuple(local_warnings),
                )
                continue
            if any(row.ogrn != ogrn for row in rows):
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_OGRN_MISMATCH,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=tuple(local_warnings),
                )
                continue
            if any(row.inn_status == "INVALID" for row in rows):
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_INN_INVALID,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=tuple(local_warnings),
                )
                continue
            inns = {row.inn for row in rows if row.inn is not None}
            if len(inns) > 1:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_INN_CONFLICT,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=tuple(local_warnings),
                )
                continue
            if not inns:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.FINORG_INN_MISSING,
                    ogrn=ogrn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=tuple(local_warnings),
                )
                continue
            inn = next(iter(inns))
            if any(row.inn is None for row in rows):
                local_warnings.append("finorg_duplicate_row_missing_inn")
            source_resolved[regn] = (
                ogrn,
                inn,
                registry_name,
                finorg_name,
                tuple(local_warnings),
            )

        candidates = (
            legal_issuer_resolver.resolve(
                tuple(sorted({value[1] for value in source_resolved.values()}))
            )
            if legal_issuer_resolver is not None
            else {}
        )
        for regn, (ogrn, inn, registry_name, finorg_name, warnings) in source_resolved.items():
            if legal_issuer_resolver is None:
                preliminary[regn] = self._result(
                    regn,
                    CbrBridgeState.LEGAL_ISSUER_NOT_EVALUATED,
                    ogrn=ogrn,
                    inn=inn,
                    registry_name=registry_name,
                    finorg_name=finorg_name,
                    warnings=warnings,
                )
                continue
            matches = candidates.get(inn, ())
            if not matches:
                state = CbrBridgeState.LEGAL_ISSUER_NOT_FOUND
                issuer = None
            elif len(matches) > 1:
                state = CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS
                issuer = None
            else:
                issuer = matches[0]
                state = (
                    CbrBridgeState.VERIFIED
                    if issuer.resolution_state == "verified"
                    else CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED
                )
            local_warnings = list(warnings)
            if (
                issuer is not None
                and registry_name
                and issuer.issuer_title
                and registry_name != issuer.issuer_title
            ):
                local_warnings.append("title_mismatch_warning_only")
            preliminary[regn] = self._result(
                regn,
                state,
                ogrn=ogrn,
                inn=inn,
                registry_name=registry_name,
                finorg_name=finorg_name,
                issuer=issuer,
                warnings=tuple(local_warnings),
            )

        results = tuple(
            replace(
                preliminary[regn],
                registry_as_of=registry.registry_as_of,
                finorg_last_update=finorg_last_update,
                retrieved_at=observed,
            )
            for regn in requested
        )
        counts = Counter(item.bridge_state.value for item in results)
        source_resolved_regns = tuple(
            item.regn for item in results if item.ogrn is not None and item.inn is not None
        )
        verified_regns = tuple(
            item.regn for item in results if item.bridge_state == CbrBridgeState.VERIFIED
        )
        return CbrLegalIssuerBridgeSnapshot(
            requested_regns=requested,
            registry_as_of=registry.registry_as_of,
            finorg_last_update=finorg_last_update,
            retrieved_at=observed,
            registry_records=registry.records,
            finorg_records=finorg.records,
            bridge_results=results,
            state_counts=tuple(sorted(counts.items())),
            regn_set_hash=identifier_set_sha256(requested),
            source_resolved_regn_set_hash=identifier_set_sha256(source_resolved_regns),
            legal_issuer_verified_regn_set_hash=identifier_set_sha256(verified_regns),
            warnings=(
                "current_identity_sources_only",
                "historical_identity_backcast_forbidden",
            ),
            legal_issuer_evaluation_performed=legal_issuer_resolver is not None,
        )

    @staticmethod
    def _result(
        regn: str,
        state: CbrBridgeState,
        *,
        ogrn: str | None = None,
        inn: str | None = None,
        registry_name: str | None = None,
        finorg_name: str | None = None,
        issuer: LegalIssuerCandidate | None = None,
        warnings: tuple[str, ...] = (),
    ) -> CbrLegalIssuerBridgeResult:
        return CbrLegalIssuerBridgeResult(
            regn=regn,
            bridge_state=state,
            ogrn=ogrn,
            inn=inn,
            legal_issuer_id=issuer.legal_issuer_id if issuer is not None else None,
            legal_issuer_source_issuer_id=(
                issuer.source_issuer_id if issuer is not None else None
            ),
            cbr_registry_name=registry_name,
            finorg_name=finorg_name,
            legal_issuer_title=issuer.issuer_title if issuer is not None else None,
            warnings=tuple(sorted(set(warnings))),
        )
