"""Pure canonical freeze/replay/diff; acquisition and storage are external."""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence, Set
from datetime import datetime, timedelta

from app.schemas.tinvest_bond_identity_bridge import TInvestBondBridgeMatchState
from app.schemas.tinvest_frozen_admission_evidence import (
    FinalizedMoexResolution, FrozenAdmissionEvidence, FrozenAdmissionEvidenceDiff,
    FrozenAdmissionEvidenceError, FrozenEvidenceFieldChange, FrozenEvidenceResolutionChange,
)
from app.services.tinvest_bond_admission_manifest_service import TInvestBondAdmissionManifestService


def _error(code="INVALID_INPUT"):
    raise FrozenAdmissionEvidenceError(code) from None


def _canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _hash(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _sequence(value):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, memoryview, Mapping, Set)):
        _error()
    return tuple(value)


def _json_safe(value):
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str:
                _error()
            normalized = "".join(c for c in key.lower() if c.isalnum())
            if any(word in normalized for word in ("authorization", "password", "secret", "token", "cookie")) or normalized in {"headers", "apikey", "request", "response"}:
                _error("SECRET_MATERIAL_REJECTED")
            _json_safe(item)
    elif type(value) is list:
        for item in value:
            _json_safe(item)
    elif value is None or type(value) in (str, bool, int):
        return
    elif type(value) is float and math.isfinite(value):
        return
    else:
        _error()


def _strict(model):
    # Recheck originals because model_construct/copy can bypass validation.
    restored = type(model).model_validate(model.model_dump())
    if _canonical(restored.model_dump(mode="json")) != _canonical(model.model_dump(mode="json")):
        _error()


def _digest(artifact):
    return _hash(artifact.model_dump(mode="json", exclude={"captured_at", "artifact_sha256"}))


def _build(*, source_bonds, internal_bonds, core_m3_complete_bond_ids, identity_bridge,
           finalized_resolutions, captured_at):
    if type(captured_at) is not datetime or captured_at.tzinfo is None or captured_at.utcoffset() != timedelta(0):
        _error()
    sources, internal, core, resolutions = map(_sequence,
        (source_bonds, internal_bonds, core_m3_complete_bond_ids, finalized_resolutions))
    for row in sources:
        _json_safe(row.source_fields)
        _strict(row)
    for row in internal:
        _strict(row)
    _strict(identity_bridge)
    if any(type(i) is not int or i <= 0 for i in core) or len(set(core)) != len(core):
        _error()
    sources = tuple(sorted(sources, key=lambda r: r.uid))
    internal = tuple(sorted(internal, key=lambda r: r.bond_id))
    core = tuple(sorted(core))
    if len({r.uid for r in sources}) != len(sources) or len({r.bond_id for r in internal}) != len(internal):
        _error()
    queried = tuple(sorted({r.source_isin for r in identity_bridge.bridge_rows
        if r.match_state is not TInvestBondBridgeMatchState.MATCHED_EXACT_ISIN and
        r.source_isin is not None and r.source_isin.strip()}))
    for item in resolutions:
        if type(item) is not FinalizedMoexResolution:
            _error()
        _strict(item)
        d, r = item.acquisition, item.resolution
        if (d.final_status is not r.security_match_status or not 1 <= d.source_query_count <= 2 or
                not d.source_query_count <= d.attempt_count <= 3 * d.source_query_count or
                d.transient_failure_count > d.attempt_count or
                (d.transient_failure_count > 0 and d.last_failure_category is None) or
                (r.security_match_status.value != "SOURCE_ERROR" and
                 (d.transient_failure_count >= d.attempt_count or
                  d.last_failure_category not in {None, "TIMEOUT", "TRANSIENT_TRANSPORT", "TRANSIENT_HTTP"}))):
            _error()
        if r.security_match_status.value == "SOURCE_ERROR" and (
            r.candidate_count != 0 or r.matched_candidate_count != 0 or
            r.issuer_metadata_status != "ISSUER_MISSING" or d.last_failure_category is None or
            any(getattr(r, f) is not None for f in ("matched_secid", "matched_isin", "primary_board", "issuer_id", "issuer_title", "issuer_inn", "issuer_okpo"))):
            _error()
    resolutions = tuple(sorted(resolutions, key=lambda r: r.resolution.source_isin))
    if tuple(r.resolution.source_isin for r in resolutions) != queried:
        _error()
    manifest = TInvestBondAdmissionManifestService.build(source_bonds=sources,
        identity_bridge=identity_bridge, internal_bonds=internal,
        moex_resolutions=tuple(r.resolution for r in resolutions), core_m3_complete_bond_ids=core)
    candidates = manifest.import_candidate_manifest
    complete = all(r.resolution.security_match_status.value != "SOURCE_ERROR" for r in resolutions)
    artifact = FrozenAdmissionEvidence(captured_at=captured_at,
        source_bonds=sources, internal_bonds=internal, core_m3_complete_bond_ids=core,
        identity_bridge=identity_bridge, queried_unmatched_isins=queried, finalized_resolutions=resolutions,
        admission_manifest=manifest, candidate_count=len(candidates),
        candidate_isin_set_sha256=_hash([r.isin for r in candidates]),
        candidate_secid_set_sha256=_hash(sorted({r.matched_secid for r in candidates})),
        candidate_row_set_sha256=_hash([r.model_dump(mode="json") for r in candidates]),
        source_bonds_sha256=_hash([r.model_dump(mode="json") for r in sources]),
        internal_bonds_sha256=_hash([r.model_dump(mode="json") for r in internal]),
        core_m3_ids_sha256=_hash(list(core)), identity_bridge_sha256=_hash(identity_bridge.model_dump(mode="json")),
        acquisition_complete=complete, ready_for_production_freeze=complete, artifact_sha256="0" * 64)
    # Own the nested mutable source dictionaries; caller inputs remain untouched.
    artifact = artifact.model_copy(deep=True)
    return artifact.model_copy(update={"artifact_sha256": _digest(artifact)})


class TInvestFrozenAdmissionEvidenceService:
    @staticmethod
    def build(*, source_bonds, internal_bonds, core_m3_complete_bond_ids, identity_bridge,
              finalized_resolutions, captured_at) -> FrozenAdmissionEvidence:
        try:
            return _build(source_bonds=source_bonds, internal_bonds=internal_bonds,
                core_m3_complete_bond_ids=core_m3_complete_bond_ids, identity_bridge=identity_bridge,
                finalized_resolutions=finalized_resolutions, captured_at=captured_at)
        except FrozenAdmissionEvidenceError:
            raise
        except Exception:
            _error()

    @staticmethod
    def validate(artifact) -> None:
        try:
            if type(artifact) is not FrozenAdmissionEvidence or artifact.pit_ready is not False:
                _error("INVALID_ARTIFACT")
            _strict(artifact)
            rebuilt = _build(source_bonds=artifact.source_bonds, internal_bonds=artifact.internal_bonds,
                core_m3_complete_bond_ids=artifact.core_m3_complete_bond_ids, identity_bridge=artifact.identity_bridge,
                finalized_resolutions=artifact.finalized_resolutions, captured_at=artifact.captured_at)
            if _canonical(rebuilt.model_dump(mode="json")) != _canonical(artifact.model_dump(mode="json")):
                _error("INVALID_ARTIFACT")
        except FrozenAdmissionEvidenceError as exc:
            _error("SECRET_MATERIAL_REJECTED" if exc.code == "SECRET_MATERIAL_REJECTED" else "INVALID_ARTIFACT")
        except Exception:
            _error("INVALID_ARTIFACT")

    @staticmethod
    def serialize(artifact) -> bytes:
        TInvestFrozenAdmissionEvidenceService.validate(artifact)
        return _canonical(artifact.model_dump(mode="json"))

    @staticmethod
    def parse(serialized) -> FrozenAdmissionEvidence:
        def pairs(items):
            result = {}
            for key, value in items:
                if key in result:
                    _error("INVALID_ARTIFACT")
                result[key] = value
            return result
        try:
            if type(serialized) not in (bytes, str):
                _error("INVALID_ARTIFACT")
            data = json.loads(serialized, object_pairs_hook=pairs, parse_constant=lambda _: _error("INVALID_ARTIFACT"))
            _json_safe(data)
            # JSON-mode validation preserves strict tuple/enum/Decimal contracts.
            artifact = FrozenAdmissionEvidence.model_validate_json(_canonical(data))
            TInvestFrozenAdmissionEvidenceService.validate(artifact)
            return artifact
        except FrozenAdmissionEvidenceError:
            raise
        except Exception:
            _error("INVALID_ARTIFACT")

    @staticmethod
    def replay(artifact):
        TInvestFrozenAdmissionEvidenceService.validate(artifact)
        return TInvestBondAdmissionManifestService.build(source_bonds=artifact.source_bonds,
            identity_bridge=artifact.identity_bridge, internal_bonds=artifact.internal_bonds,
            moex_resolutions=tuple(r.resolution for r in artifact.finalized_resolutions),
            core_m3_complete_bond_ids=artifact.core_m3_complete_bond_ids)

    @staticmethod
    def diff(before, after) -> FrozenAdmissionEvidenceDiff:
        for artifact in (before, after):
            TInvestFrozenAdmissionEvidenceService.validate(artifact)
        left = {r.resolution.source_isin: r for r in before.finalized_resolutions}
        right = {r.resolution.source_isin: r for r in after.finalized_resolutions}
        semantic, acquisition = [], []
        for isin in sorted(left.keys() & right.keys()):
            for attribute, output in (("resolution", semantic), ("acquisition", acquisition)):
                old = getattr(left[isin], attribute).model_dump(mode="json")
                new = getattr(right[isin], attribute).model_dump(mode="json")
                changes = tuple(FrozenEvidenceFieldChange(field=f, before=old[f], after=new[f])
                    for f in sorted(old) if old[f] != new[f])
                if changes:
                    output.append(FrozenEvidenceResolutionChange(source_isin=isin, changes=changes))
        lc = {r.isin: r.model_dump(mode="json") for r in before.admission_manifest.import_candidate_manifest}
        rc = {r.isin: r.model_dump(mode="json") for r in after.admission_manifest.import_candidate_manifest}
        return FrozenAdmissionEvidenceDiff(before_sha256=before.artifact_sha256, after_sha256=after.artifact_sha256,
            added_isins=tuple(sorted(right.keys() - left.keys())), removed_isins=tuple(sorted(left.keys() - right.keys())),
            changed_resolution_isins=tuple(r.source_isin for r in semantic),
            unchanged_count=len(left.keys() & right.keys()) - len(semantic), resolution_changes=tuple(semantic),
            changed_acquisition_isins=tuple(r.source_isin for r in acquisition), acquisition_changes=tuple(acquisition),
            added_candidate_isins=tuple(sorted(rc.keys() - lc.keys())), removed_candidate_isins=tuple(sorted(lc.keys() - rc.keys())),
            changed_candidate_isins=tuple(sorted(i for i in lc.keys() & rc.keys() if lc[i] != rc[i])))
