"""Replayable Task296B evidence, with no authorization or persistence."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.tinvest_bond_admission_manifest import MoexBondResolutionProjection, MoexSecurityMatchStatus, TInvestBondAdmissionManifestView
from app.schemas.tinvest_bond_identity_bridge import BondIdentityProjection, TInvestBondIdentityBridgeView
from app.schemas.tinvest_instrument_universe import TInvestBondUniverseInstrument


class Frozen(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class MoexResolutionAcquisitionDiagnostics(Frozen):
    attempt_count: int = Field(ge=0, le=6)
    transient_failure_count: int = Field(ge=0, le=6)
    final_status: MoexSecurityMatchStatus
    source_query_count: int = Field(ge=0, le=2)
    last_failure_category: Literal["TIMEOUT", "TRANSIENT_TRANSPORT", "TRANSIENT_HTTP", "HTTP_REJECTED", "TRANSPORT_REJECTED", "INVALID_RESPONSE", "UNKNOWN_SOURCE_ERROR"] | None = None


class FinalizedMoexResolution(Frozen):
    resolution: MoexBondResolutionProjection
    acquisition: MoexResolutionAcquisitionDiagnostics


class FrozenAdmissionEvidence(Frozen):
    schema_version: Literal["tinvest-frozen-admission-evidence-v1"] = "tinvest-frozen-admission-evidence-v1"
    captured_at: datetime
    source_bonds: tuple[TInvestBondUniverseInstrument, ...]
    internal_bonds: tuple[BondIdentityProjection, ...]
    core_m3_complete_bond_ids: tuple[int, ...]
    identity_bridge: TInvestBondIdentityBridgeView
    queried_unmatched_isins: tuple[str, ...]
    finalized_resolutions: tuple[FinalizedMoexResolution, ...]
    admission_manifest: TInvestBondAdmissionManifestView
    candidate_count: int = Field(ge=0)
    candidate_isin_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_secid_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    candidate_row_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bonds_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    internal_bonds_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    core_m3_ids_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    identity_bridge_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    acquisition_complete: bool
    ready_for_production_freeze: bool
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    hash_method: Literal["CANONICAL_JSON_SHA256_EXCLUDING_CAPTURED_AT_AND_SELF_V1"] = "CANONICAL_JSON_SHA256_EXCLUDING_CAPTURED_AT_AND_SELF_V1"
    captured_at_hashed: Literal[False] = False
    database_persistence: Literal[False] = False
    production_execution_authorized: Literal[False] = False
    pit_ready: Literal[False] = False


class FrozenEvidenceFieldChange(Frozen):
    field: str
    before: Any
    after: Any


class FrozenEvidenceResolutionChange(Frozen):
    source_isin: str
    changes: tuple[FrozenEvidenceFieldChange, ...]


class FrozenAdmissionEvidenceDiff(Frozen):
    before_sha256: str
    after_sha256: str
    added_isins: tuple[str, ...]
    removed_isins: tuple[str, ...]
    changed_resolution_isins: tuple[str, ...]
    unchanged_count: int = Field(ge=0)
    resolution_changes: tuple[FrozenEvidenceResolutionChange, ...]
    changed_acquisition_isins: tuple[str, ...]
    acquisition_changes: tuple[FrozenEvidenceResolutionChange, ...]
    added_candidate_isins: tuple[str, ...]
    removed_candidate_isins: tuple[str, ...]
    changed_candidate_isins: tuple[str, ...]
    pit_ready: Literal[False] = False


class FrozenAdmissionEvidenceError(ValueError):
    """Only fixed codes are exposed, never failed input or exceptions."""

    def __init__(self, code: Literal["INVALID_INPUT", "INVALID_ARTIFACT", "SECRET_MATERIAL_REJECTED"]):
        self.code = code
        super().__init__(code)
