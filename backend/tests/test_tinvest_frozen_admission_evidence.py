"""Hermetic replay and integrity regression over synthetic source contracts."""

import ast
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal, getcontext
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.schemas.tinvest_bond_admission_manifest import MoexSecurityMatchStatus
from app.schemas.tinvest_frozen_admission_evidence import (
    FinalizedMoexResolution, FrozenAdmissionEvidenceError, MoexResolutionAcquisitionDiagnostics,
)
from app.services.tinvest_bond_identity_bridge_service import TInvestBondIdentityBridgeService
from app.services.tinvest_frozen_admission_evidence_service import TInvestFrozenAdmissionEvidenceService as Service
from test_tinvest_bond_admission_manifest import source, resolution


NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def finalized(isin, *, error=False, retry=False, inn="7700000000"):
    row = resolution(isin, secid=isin).model_copy(update={"issuer_inn": inn})
    if error:
        row = row.model_copy(update={"security_match_status": MoexSecurityMatchStatus.SOURCE_ERROR,
            "matched_secid": None, "matched_isin": None, "candidate_count": 0,
            "matched_candidate_count": 0, "primary_board": None, "issuer_metadata_status": "ISSUER_MISSING",
            "issuer_id": None, "issuer_title": None, "issuer_inn": None, "issuer_okpo": None})
    return FinalizedMoexResolution(resolution=row, acquisition=MoexResolutionAcquisitionDiagnostics(
        attempt_count=3 if error else 2 if retry else 1,
        transient_failure_count=3 if error else 1 if retry else 0,
        final_status=row.security_match_status, source_query_count=1,
        last_failure_category="TIMEOUT" if error or retry else None))


def inputs(isins=("RU000A10FWR5", "RU000A100AA1"), *, error=False, retry=False):
    sources = [source(f"UID-{isin}", isin) for isin in isins]
    bridge = TInvestBondIdentityBridgeService.build(sources, [], [])
    return dict(source_bonds=sources, internal_bonds=[], core_m3_complete_bond_ids=[],
        identity_bridge=bridge, finalized_resolutions=[finalized(i, error=error, retry=retry) for i in isins],
        captured_at=NOW)


def test_roundtrip_replay_order_timestamp_and_copy_ownership():
    values = inputs()
    artifact = Service.build(**values)
    parsed = Service.parse(Service.serialize(artifact))
    assert parsed.model_dump() == artifact.model_dump()
    assert Service.replay(parsed).model_dump() == artifact.admission_manifest.model_dump()
    assert artifact.candidate_count == 2 and artifact.acquisition_complete and artifact.ready_for_production_freeze
    permuted = {**values, "source_bonds": values["source_bonds"][::-1],
        "finalized_resolutions": values["finalized_resolutions"][::-1], "captured_at": NOW + timedelta(days=1)}
    second = Service.build(**permuted)
    assert second.artifact_sha256 == artifact.artifact_sha256
    assert second.candidate_row_set_sha256 == artifact.candidate_row_set_sha256
    assert second.candidate_isin_set_sha256 == artifact.candidate_isin_set_sha256
    assert second.candidate_secid_set_sha256 == artifact.candidate_secid_set_sha256
    assert Service.serialize(Service.parse(Service.serialize(artifact))) == Service.serialize(artifact)
    values["source_bonds"][0].source_fields["sector"] = "changed caller dict"
    Service.validate(artifact)
    assert artifact.source_bonds[1].source_fields["sector"] == "financial"
    with pytest.raises(ValidationError):
        artifact.candidate_count = 0


def test_source_error_is_replayable_but_not_ready():
    artifact = Service.build(**inputs(error=True))
    assert artifact.candidate_count == 0
    assert artifact.acquisition_complete is artifact.ready_for_production_freeze is False
    parsed = Service.parse(Service.serialize(artifact))
    assert parsed.artifact_sha256 == artifact.artifact_sha256
    assert Service.replay(parsed).provenance == artifact.admission_manifest.provenance
    assert all(r.resolution.security_match_status is MoexSecurityMatchStatus.SOURCE_ERROR for r in parsed.finalized_resolutions)


@pytest.mark.parametrize("field,value", [("candidate_count", 999), ("artifact_sha256", "0" * 64),
    ("candidate_row_set_sha256", "0" * 64), ("pit_ready", True), ("acquisition_complete", False),
    ("source_bonds_sha256", "0" * 64), ("schema_version", "wrong-version")])
def test_tamper_rejected(field, value):
    artifact = Service.build(**inputs())
    with pytest.raises(FrozenAdmissionEvidenceError) as exc:
        Service.validate(artifact.model_copy(update={field: value}))
    assert str(exc.value) == "INVALID_ARTIFACT"


def test_nested_tamper_and_duplicate_keys_rejected():
    artifact = Service.build(**inputs())
    data = json.loads(Service.serialize(artifact))
    data["finalized_resolutions"][0]["resolution"]["issuer_inn"] = "OTHER"
    with pytest.raises(FrozenAdmissionEvidenceError):
        Service.parse(json.dumps(data))
    data = json.loads(Service.serialize(artifact))
    data["admission_manifest"]["import_candidate_manifest"][0]["matched_secid"] = "OTHER"
    with pytest.raises(FrozenAdmissionEvidenceError):
        Service.parse(json.dumps(data))
    for invalid in ('{"schema_version":"a","schema_version":"b"}', '{"x":NaN}', '{}', b'not-json',
                    Service.serialize(artifact)[:-1] + b',"extra":1}'):
        with pytest.raises(FrozenAdmissionEvidenceError):
            Service.parse(invalid)


def test_exact_diff_semantic_and_acquisition_changes_separate():
    before = Service.build(**inputs(("RU000A100AA1", "RU000A100AA2", "RU000A100AA3")))
    args = inputs(("RU000A100AA2", "RU000A100AA3", "RU000A100AA4"))
    args["finalized_resolutions"][0] = finalized("RU000A100AA2", inn="NEW-INN")
    args["finalized_resolutions"][1] = finalized("RU000A100AA3", retry=True)
    after = Service.build(**args)
    delta = Service.diff(before, after)
    assert delta.added_isins == ("RU000A100AA4",) and delta.removed_isins == ("RU000A100AA1",)
    assert delta.changed_resolution_isins == ("RU000A100AA2",) and delta.unchanged_count == 1
    assert delta.resolution_changes[0].changes[0].field == "issuer_inn"
    assert delta.changed_acquisition_isins == ("RU000A100AA3",)
    assert delta.added_candidate_isins == ("RU000A100AA4",)
    assert delta.removed_candidate_isins == ("RU000A100AA1",)
    # Task296B candidate aggregates do not contain issuer INN. Keep their exact
    # existing content rather than inventing a candidate change from source drift.
    assert delta.changed_candidate_isins == ()
    row = args["finalized_resolutions"][0]
    args["finalized_resolutions"][0] = row.model_copy(update={"resolution": row.resolution.model_copy(update={"matched_secid": "NEW-SECID"})})
    assert Service.diff(before, Service.build(**args)).changed_candidate_isins == ("RU000A100AA2",)
    retry_only = Service.build(**inputs(retry=True))
    direct = Service.build(**inputs())
    assert retry_only.artifact_sha256 != direct.artifact_sha256
    assert retry_only.candidate_row_set_sha256 == direct.candidate_row_set_sha256
    assert not Service.diff(direct, retry_only).changed_resolution_isins


@pytest.mark.parametrize("change", ["duplicate_uid", "duplicate_resolution", "missing_resolution", "extra_resolution",
    "generator", "core_bool", "timestamp_naive", "timestamp_non_utc", "diagnostics"])
def test_invalid_inputs_fail_closed(change):
    args = inputs()
    if change == "duplicate_uid":
        args["source_bonds"].append(args["source_bonds"][0])
    elif change == "duplicate_resolution":
        args["finalized_resolutions"].append(args["finalized_resolutions"][0])
    elif change == "missing_resolution":
        args["finalized_resolutions"].pop()
    elif change == "extra_resolution":
        args["finalized_resolutions"].append(finalized("RU000A100ZZ1"))
    elif change == "generator":
        args["source_bonds"] = iter(args["source_bonds"])
    elif change == "core_bool":
        args["core_m3_complete_bond_ids"] = [True]
    elif change == "timestamp_naive":
        args["captured_at"] = NOW.replace(tzinfo=None)
    elif change == "timestamp_non_utc":
        args["captured_at"] = NOW.astimezone(timezone(timedelta(hours=3)))
    else:
        item = args["finalized_resolutions"][0]
        args["finalized_resolutions"][0] = item.model_copy(update={"acquisition": item.acquisition.model_copy(update={"attempt_count": 6})})
    with pytest.raises(FrozenAdmissionEvidenceError):
        Service.build(**args)


def test_secret_keys_json_types_context_and_static_pure_boundary():
    for key in ("authorization", "access_token", "cookie", "headers", "api_key", "clientSecret"):
        args = inputs()
        args["source_bonds"][0].source_fields[key] = "VERY_SECRET_VALUE"
        with pytest.raises(FrozenAdmissionEvidenceError) as exc:
            Service.build(**args)
        assert exc.value.code == "SECRET_MATERIAL_REJECTED" and "VERY_SECRET" not in repr(exc.value)
    for value in (float("nan"), Decimal("2"), b"bytes", object()):
        args = inputs()
        args["source_bonds"][0].source_fields["extra"] = value
        with pytest.raises(FrozenAdmissionEvidenceError):
            Service.build(**args)
    context = getcontext().copy()
    try:
        normal = Service.build(**inputs())
        getcontext().prec = 3
        altered = Service.build(**inputs())
        assert normal.artifact_sha256 == altered.artifact_sha256 and getcontext().prec == 3
    finally:
        getcontext().prec = context.prec
    module = Path(__file__).parents[1] / "app/services/tinvest_frozen_admission_evidence_service.py"
    tree = ast.parse(module.read_text(encoding="utf-8"))
    forbidden = {"sqlalchemy", "httpx", "requests", "os", "pathlib", "open", "lookup", "Session", "sync", "apply"}
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    imports = {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert not (names | imports) & forbidden


def test_existing_missing_isin_and_empty_resolution_set():
    from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection
    rows = [source("MATCHED", "RU000A100AA1"), source("NO_ISIN", None)]
    internal = [BondIdentityProjection(bond_id=7, isin="RU000A100AA1", secid="EXISTING")]
    bridge = TInvestBondIdentityBridgeService.build(rows, internal, [7])
    artifact = Service.build(source_bonds=rows, internal_bonds=internal,
        core_m3_complete_bond_ids=[7], identity_bridge=bridge,
        finalized_resolutions=[], captured_at=NOW)
    assert artifact.queried_unmatched_isins == () and artifact.candidate_count == 0
    assert artifact.acquisition_complete and artifact.ready_for_production_freeze
    assert Service.replay(Service.parse(Service.serialize(artifact))).model_dump() == artifact.admission_manifest.model_dump()
