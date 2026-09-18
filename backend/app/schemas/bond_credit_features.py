"""Source-native credit evidence views; no economic credit interpretation."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BOND_CREDIT_FEATURE_CONTRACT_VERSION = "bond-credit-feature-v1"
RatingAgency = Literal["ACRA", "EXPERT_RA", "NRA", "NKR"]
IssuerLinkStatus = Literal[
    "VERIFIED", "MAPPING_MISSING", "MAPPING_NOT_VERIFIED", "MAPPING_CONFLICT",
    "LEGAL_ISSUER_NOT_VERIFIED",
]
BankSubjectStatus = Literal[
    "VERIFIED", "NO_VERIFIED_SUBJECT", "AMBIGUOUS_VERIFIED_SUBJECT", "ISSUER_NOT_VERIFIED",
]


class CreditFeatureModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CreditRatingEventEvidence(CreditFeatureModel):
    event_id: int
    artifact_id: int
    source_provider: str
    source_artifact_sha256: str
    artifact_retrieved_at: datetime
    source_object_id: str
    rating_agency: RatingAgency
    target_kind: Literal["BOND", "LEGAL_ISSUER"]
    event_date: date
    publication_precision: Literal["DATE", "TIMESTAMP", "UNKNOWN"]
    publication_date: date | None
    publication_at: datetime | None
    rating_scale_raw: str | None
    rating_value_raw: str | None
    rating_outlook_raw: str | None
    rating_watch_raw: str | None
    rating_action_raw: str | None
    source_issuer_inn: str | None
    source_bond_isin: str | None
    event_fingerprint: str


class RatingAgencyLatestEvidence(CreditFeatureModel):
    rating_agency: RatingAgency
    latest_event_date: date
    event_count: int
    has_rating_value: bool
    events: list[CreditRatingEventEvidence]


class BankCreditMetricEvidence(CreditFeatureModel):
    metric_id: int
    metric_key: str
    metric_family: Literal["REGULATORY_CAPITAL", "REGULATORY_RATIO"]
    metric_value: Decimal
    metric_unit: Literal["RUB", "PERCENT"]
    metric_fingerprint: str
    source_form: str
    source_code: str
    report_date: date
    subject_regn: str
    normalized_observation_id: int
    raw_observation_id: int
    report_snapshot_id: int
    source_artifact_id: int
    source_artifact_sha256: str
    source_dimensions: list[Any]
    source_disclosure_state: str
    normalization_fingerprint: str
    snapshot_publication_status: Literal["KNOWN", "UNKNOWN"]
    snapshot_publication_at: datetime | None
    snapshot_retrieved_at: datetime


class BondCreditFeatureAvailability(CreditFeatureModel):
    has_verified_legal_issuer: bool
    has_bond_rating_event: bool
    has_bond_rating_value: bool
    has_issuer_rating_event: bool
    has_issuer_rating_value: bool
    has_verified_bank_subject: bool
    has_bank_credit_metrics: bool
    bond_rating_agencies: list[RatingAgency]
    issuer_rating_agencies: list[RatingAgency]


class BondCreditFeatureProvenance(CreditFeatureModel):
    bond_legal_issuer_profile_id: int | None
    mapping_state: str | None
    mapping_source: str | None
    mapping_source_issuer_id: str | None
    legal_issuer_identity_source: str | None
    verified_bank_reporting_subject_ids: list[int]
    bank_bridge_current_evidence_id: int | None


class BondCreditFeatureCapabilities(CreditFeatureModel):
    credit_feature_join_ready: Literal[True] = True
    bond_rating_evidence_ready: Literal[True] = True
    issuer_rating_evidence_ready: Literal[True] = True
    bank_credit_metric_join_ready: Literal[True] = True
    issuer_to_bond_rating_inheritance: Literal[False] = False
    bond_to_issuer_rating_inheritance: Literal[False] = False
    cross_agency_normalization_ready: Literal[False] = False
    unified_credit_score_ready: Literal[False] = False
    pd_model_ready: Literal[False] = False
    default_evidence_schema_ready: Literal[True] = True
    default_ingestion_ready: Literal[False] = False
    default_feature_join_ready: Literal[False] = False
    credit_adjusted_spread_ready: Literal[False] = False
    liquidity_adjusted_spread_ready: Literal[False] = False


class BondCreditFeatureView(CreditFeatureModel):
    contract_version: Literal["bond-credit-feature-v1"] = BOND_CREDIT_FEATURE_CONTRACT_VERSION
    bond_id: int
    isin: str | None
    secid: str | None
    as_of_date: date
    issuer_link_status: IssuerLinkStatus
    legal_issuer_id: int | None
    legal_issuer_source_issuer_id: str | None
    legal_issuer_inn: str | None
    bond_ratings: list[RatingAgencyLatestEvidence]
    issuer_ratings: list[RatingAgencyLatestEvidence]
    bank_subject_status: BankSubjectStatus
    bank_reporting_subject_id: int | None
    bank_subject_regn: str | None
    bank_report_date: date | None
    bank_report_age_days: int | None
    bank_metrics: list[BankCreditMetricEvidence]
    availability: BondCreditFeatureAvailability
    provenance: BondCreditFeatureProvenance
    quality_flags: list[str]
    capabilities: BondCreditFeatureCapabilities = Field(default_factory=BondCreditFeatureCapabilities)
    pit_ready: Literal[False] = False
