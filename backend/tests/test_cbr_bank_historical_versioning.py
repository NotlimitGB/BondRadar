from __future__ import annotations

import ast
import random
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.cbr_bank_financial_evidence.historical_versioning import (
    ArtifactAvailabilityEvidence,
    ArtifactVersion,
    ArtifactVersionKey,
    EvidenceSource,
    PitSelectionQuality,
    PitSelectionResult,
    PitSelectionStatus,
    select_artifact_version_as_of,
)
from app.services.cbr_bank_reporting.contracts import CbrBankForm


REPORT_DATE = date(2024, 4, 1)
T1 = datetime(2024, 7, 31, 20, 20, 29, tzinfo=timezone.utc)
T2 = datetime(2024, 10, 15, 12, tzinfo=timezone.utc)
SHA_A = "a" * 64
SHA_B = "b" * 64


def evidence(
    digest: str,
    observed_at: datetime,
    *,
    exact: bool = True,
    source: EvidenceSource = EvidenceSource.WAYBACK,
    reference: str | None = None,
) -> ArtifactAvailabilityEvidence:
    return ArtifactAvailabilityEvidence(
        artifact_sha256=digest,
        observed_at=observed_at,
        source=source,
        exact_payload_bound=exact,
        source_reference=reference,
    )


def version(
    digest: str,
    *observations: ArtifactAvailabilityEvidence,
    form: CbrBankForm = CbrBankForm.FORM_102,
    report_date: date = REPORT_DATE,
) -> ArtifactVersion:
    return ArtifactVersion(
        key=ArtifactVersionKey(form, report_date, digest),
        availability_evidence=tuple(observations),
    )


def select(versions, as_of=T2):
    return select_artifact_version_as_of(
        form=CbrBankForm.FORM_102,
        report_date=REPORT_DATE,
        versions=versions,
        as_of=as_of,
    )


def test_one_exact_bound_version_is_selected_with_conservative_quality():
    result = select((version(SHA_A, evidence(SHA_A, T1)),))
    assert result.status is PitSelectionStatus.SELECTED
    assert result.selected_version.key.artifact_sha256 == SHA_A
    assert result.selected_safe_known_from == T1
    assert result.selection_quality is PitSelectionQuality.CONSERVATIVE_LAST_PROVEN
    assert result.eligible_version_count == 1
    assert result.candidate_artifact_sha256s == (SHA_A,)


def test_exact_boundary_is_eligible_and_future_version_is_excluded():
    assert select((version(SHA_A, evidence(SHA_A, T1)),), as_of=T1).status is (
        PitSelectionStatus.SELECTED
    )
    result = select((version(SHA_B, evidence(SHA_B, T2)),), as_of=T1)
    assert result.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF
    assert result.diagnostic_codes == ("NO_VERSION_OBSERVED_AT_OR_BEFORE_AS_OF",)


def test_later_revision_becomes_selected_only_at_its_evidence_boundary():
    versions = (
        version(SHA_A, evidence(SHA_A, T1)),
        version(SHA_B, evidence(SHA_B, T2)),
    )
    before_boundary = select(versions, as_of=T2 - timedelta(microseconds=1))
    assert before_boundary.selected_version.key.artifact_sha256 == SHA_A
    at_boundary = select(versions, as_of=T2)
    assert at_boundary.selected_version.key.artifact_sha256 == SHA_B
    assert at_boundary.selected_safe_known_from == T2


def test_multiple_observations_and_duplicate_version_objects_merge_deterministically():
    later = evidence(SHA_A, T2, source=EvidenceSource.CBR_DIRECT)
    earlier = evidence(SHA_A, T1, reference="capture-a")
    versions = (
        version(SHA_A, later),
        version(SHA_A, earlier, later, earlier),
    )
    result = select(versions)
    assert result.status is PitSelectionStatus.SELECTED
    assert result.selected_safe_known_from == T1
    assert len(result.selected_version.availability_evidence) == 2


def test_unbound_evidence_is_not_pit_eligible_and_no_fallback_occurs():
    current = version(SHA_B, evidence(SHA_B, T1, exact=False))
    result = select((current,), as_of=T2)
    assert current.safe_known_from is None
    assert result.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF
    assert result.selected_version is None
    assert result.diagnostic_codes == ("NO_EXACT_PAYLOAD_BOUND_EVIDENCE",)


def test_empty_target_version_set_fails_closed():
    unrelated = version(
        SHA_A,
        evidence(SHA_A, T1),
        form=CbrBankForm.FORM_101,
    )
    result = select((unrelated,))
    assert result.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF
    assert result.diagnostic_codes == ("NO_VERSION_FOR_FORM_REPORT_DATE",)


def test_different_shas_at_same_boundary_are_ambiguous_without_tiebreak():
    result = select(
        (
            version(SHA_B, evidence(SHA_B, T1)),
            version(SHA_A, evidence(SHA_A, T1)),
        )
    )
    assert result.status is PitSelectionStatus.AMBIGUOUS_VERSION_AT_BOUNDARY
    assert result.selected_version is None
    assert result.candidate_artifact_sha256s == (SHA_A, SHA_B)
    assert result.eligible_version_count == 2


def test_naive_datetime_and_noncanonical_identifiers_are_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        evidence(SHA_A, datetime(2024, 7, 31, 20, 20, 29))
    with pytest.raises(ValueError, match="timezone-aware"):
        select((), as_of=datetime(2024, 7, 31, 20, 20, 29))
    with pytest.raises(ValueError, match="canonical CbrBankForm"):
        ArtifactVersionKey("0409102", REPORT_DATE, SHA_A)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        ArtifactVersionKey(CbrBankForm.FORM_102, REPORT_DATE, "A" * 64)
    with pytest.raises(ValueError, match="must be a date"):
        ArtifactVersionKey(CbrBankForm.FORM_102, T1, SHA_A)  # type: ignore[arg-type]


def test_evidence_sha_must_match_version_sha():
    with pytest.raises(ValueError, match="evidence SHA"):
        version(SHA_A, evidence(SHA_B, T1))


def test_invalid_version_collection_returns_typed_failure():
    result = select((version(SHA_A, evidence(SHA_A, T1)), object()))
    assert result.status is PitSelectionStatus.INVALID_EVIDENCE
    assert result.selected_version is None
    assert result.diagnostic_codes == ("INVALID_VERSION_COLLECTION",)


def test_aware_offset_is_normalized_to_utc():
    offset = timezone(timedelta(hours=3))
    local_time = datetime(2024, 7, 31, 23, 20, 29, tzinfo=offset)
    item = evidence(SHA_A, local_time)
    result = select((version(SHA_A, item),), as_of=local_time)
    assert item.observed_at == T1
    assert item.observed_at.tzinfo is timezone.utc
    assert result.as_of == T1


def test_selection_is_order_independent_and_never_uses_future_boundary():
    versions = [
        version(SHA_A, evidence(SHA_A, T1)),
        version(SHA_B, evidence(SHA_B, T2)),
    ]
    expected = select(tuple(versions), as_of=T2)
    for seed in range(10):
        shuffled = list(versions)
        random.Random(seed).shuffle(shuffled)
        assert select(tuple(shuffled), as_of=T2) == expected
    assert expected.selected_safe_known_from <= expected.as_of


def test_current_restated_revision_cannot_leak_backward_or_fill_missing_history():
    current_only = version(SHA_B, evidence(SHA_B, T2))
    before = select((current_only,), as_of=T1)
    assert before.status is PitSelectionStatus.NO_KNOWN_VERSION_AS_OF
    assert before.selected_version is None


def test_a_to_b_to_a_reobservation_does_not_invent_active_intervals():
    t3 = T2 + timedelta(days=30)
    a = version(SHA_A, evidence(SHA_A, T1), evidence(SHA_A, t3))
    b = version(SHA_B, evidence(SHA_B, T2))
    result = select((a, b), as_of=t3)
    assert a.safe_known_from == T1
    assert result.selected_version.key.artifact_sha256 == SHA_B


@pytest.mark.parametrize(
    "form,report_date,archived_sha,current_sha,capture_at,raw_difference",
    (
        (
            CbrBankForm.FORM_102,
            date(2024, 4, 1),
            "52fb24529eff57e480148cbda074a1b7354d727d69c369ebef9723126d254dc7",
            "45512cfbf4050996f8f817e30e77d3fa7bf6a5eee3c6ec845ad1a876708af2ce",
            datetime(2024, 7, 31, 20, 20, 29, tzinfo=timezone.utc),
            "record_delta=-96",
        ),
        (
            CbrBankForm.FORM_135,
            date(2025, 6, 1),
            "293d3b1cf2c35281b8cdc6b32961cb2c925b1d178d5a91662f714252a31eb764",
            "9149ff890e1fba8bb2f09d4b2feb87c8cbddd4076527b8356eb6a2136235388e",
            datetime(2025, 7, 16, 1, 41, 11, tzinfo=timezone.utc),
            "changed_raw_values=3",
        ),
        (
            CbrBankForm.FORM_123,
            date(2025, 10, 1),
            "298ea0d19874b6ec290a59fa0663318c8c3f4809aaa63c2652ed96c26d3e568d",
            "33b40c5805e10e21b89b33d20ebdf5cbb9c2080e5d3a1df2bfbae2fded5c8c7c",
            datetime(2025, 10, 31, 19, 13, 11, tzinfo=timezone.utc),
            "changed_raw_values=6",
        ),
    ),
)
def test_task259_real_revision_cases_do_not_leak_current_frozen_version(
    form,
    report_date,
    archived_sha,
    current_sha,
    capture_at,
    raw_difference,
):
    del raw_difference
    archived = version(
        archived_sha,
        evidence(archived_sha, capture_at),
        form=form,
        report_date=report_date,
    )
    current_frozen = version(current_sha, form=form, report_date=report_date)
    result = select_artifact_version_as_of(
        form=form,
        report_date=report_date,
        versions=(current_frozen, archived),
        as_of=capture_at,
    )
    assert result.status is PitSelectionStatus.SELECTED
    assert result.selected_version.key.artifact_sha256 == archived_sha
    assert current_frozen.safe_known_from is None


def test_result_rejects_impossible_selected_future_boundary():
    selected = version(SHA_A, evidence(SHA_A, T2))
    with pytest.raises(ValueError, match="internally inconsistent"):
        PitSelectionResult(
            status=PitSelectionStatus.SELECTED,
            form=CbrBankForm.FORM_102,
            report_date=REPORT_DATE,
            as_of=T1,
            selected_version=selected,
            selected_safe_known_from=T2,
            selection_quality=PitSelectionQuality.CONSERVATIVE_LAST_PROVEN,
            eligible_version_count=1,
            candidate_artifact_sha256s=(SHA_A,),
            diagnostic_codes=(),
        )


def test_module_is_pure_and_has_no_database_network_or_persistence_surface():
    module_path = (
        Path(__file__).parents[1]
        / "app"
        / "services"
        / "cbr_bank_financial_evidence"
        / "historical_versioning.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imports.add(node.module)
    assert not any(
        name.startswith(("sqlalchemy", "alembic", "httpx", "requests", "app.models"))
        for name in imports
    )
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert calls.isdisjoint({"add", "flush", "commit", "execute", "delete", "post"})


def test_contract_document_locks_pit_semantics_and_only_next_task():
    document = (
        Path(__file__).parents[2]
        / "docs"
        / "audits"
        / "TASK260A_CBR_HISTORICAL_ARTIFACT_VERSIONING_AND_PIT_SELECTION_CONTRACT.md"
    ).read_text(encoding="utf-8")
    required = (
        "ArtifactVersionKey=(form, report_date, artifact_sha256)",
        "safe_known_from=min(observed_at where exact_payload_bound=true)",
        "AS_OF_SELECTION_POLICY=LAST_PROVEN_VERSION_AS_OF",
        "SELECTION_QUALITY=CONSERVATIVE_LAST_PROVEN",
        "LOOKAHEAD_INVARIANT=selected.safe_known_from <= as_of",
        "CURRENT_RESTATED_HISTORY",
        "PUBLICATION_TIME_PROVEN=false",
        "PIT_READY=false",
        "DATABASE_ACCESSED=false",
        "DATABASE_MUTATION_EXECUTED=false",
        "DATABASE_SCHEMA_CHANGED=false",
        "ALEMBIC_CHANGED=false",
        "TASK259_FILES_CHANGED=false",
        "PRODUCTION_ACTIONS=NONE",
        "NEXT_RECOMMENDED_TASK=260B_HISTORICAL_ARTIFACT_VERSION_PERSISTENCE_AND_FORWARD_OBSERVATION_FOUNDATION",
    )
    assert all(item in document for item in required)
    assert "publication_at = capture_at" not in document
    assert "publication_at = observed_at" not in document
