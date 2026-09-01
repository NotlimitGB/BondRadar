from __future__ import annotations

import hashlib
from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.cbr_bank_financial_evidence import (
    CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
    CBR_BANK_SOURCE,
    CBR_REPORTING_SUBJECT_SOURCE,
    CBR_REPORTING_SUBJECT_TYPE,
    CbrBankRawObservation,
    CbrBankReportSnapshot,
    CbrBankReportingSubject,
    CbrBankSourceArtifact,
    CbrBankSubjectLegalIssuerEvidence,
    CbrBankSubjectLegalIssuerProfile,
)
from app.models.legal_issuer import LegalIssuer
from app.services.cbr_bank_reporting.bundle import subject_set_sha256
from app.services.cbr_bank_reporting.contracts import (
    CbrBankRegulatoryBundleSnapshot,
    CbrFormResult,
)
from app.services.cbr_legal_issuer_bridge.contracts import (
    CONTRACT_VERSION as TASK252_CONTRACT_VERSION,
    CbrBridgeState,
    CbrLegalIssuerBridgeResult,
    CbrLegalIssuerBridgeSnapshot,
    canonical_regn,
    identifier_set_sha256,
)

from .contracts import (
    ARCHIVE_RUNTIME_CONTRACT,
    TASK251_PARSER_CONTRACT_VERSION,
    CbrIdentityLinkState,
    EntityWriteCounts,
    ExactFormEvidence,
    PersistBundleResult,
)
from .fingerprints import (
    canonical_value,
    ordered_fingerprints_sha256,
    sha256_canonical,
    utc_datetime,
)
from .lexical import extract_exact_form_evidence


_TASK252_STATE_PROJECTION = {
    CbrBridgeState.VERIFIED: CbrIdentityLinkState.VERIFIED,
    CbrBridgeState.LEGAL_ISSUER_NOT_FOUND: CbrIdentityLinkState.NOT_FOUND,
    CbrBridgeState.LEGAL_ISSUER_INN_AMBIGUOUS: CbrIdentityLinkState.AMBIGUOUS,
    CbrBridgeState.LEGAL_ISSUER_NOT_VERIFIED: CbrIdentityLinkState.NOT_VERIFIED,
    CbrBridgeState.LEGAL_ISSUER_NOT_EVALUATED: CbrIdentityLinkState.NOT_EVALUATED,
}


def _chunks(values: list[str], size: int = 500) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _as_utc(value: datetime) -> datetime:
    return utc_datetime(value, field_name="persisted datetime")


def _json_equal(left: Any, right: Any) -> bool:
    return canonical_value(left) == canonical_value(right)


class CbrBankRawFinancialEvidenceStore:
    def __init__(
        self,
        session: Session,
        *,
        archive_executable: str | None = None,
    ) -> None:
        self.session = session
        self.archive_executable = archive_executable

    def _checkpoint(self, name: str) -> None:
        """Test seam for proving caller-owned rollback; production path is a no-op."""
        del name

    def _concurrency_savepoint(self):
        bind = self.session.get_bind()
        return nullcontext() if bind.dialect.name == "sqlite" else self.session.begin_nested()

    def _recoverable_integrity_error(self) -> bool:
        return self.session.get_bind().dialect.name != "sqlite"

    def persist_bundle(
        self,
        bundle: CbrBankRegulatoryBundleSnapshot,
        *,
        observed_at: datetime,
        ingested_at: datetime,
        publication_status: str = "UNKNOWN",
        publication_at: datetime | None = None,
        identity_snapshot: CbrLegalIssuerBridgeSnapshot | None = None,
    ) -> PersistBundleResult:
        if not isinstance(bundle, CbrBankRegulatoryBundleSnapshot):
            raise TypeError("bundle must be a Task251 snapshot")
        observed = utc_datetime(observed_at, field_name="observed_at")
        ingested = utc_datetime(ingested_at, field_name="ingested_at")
        publication = (
            utc_datetime(publication_at, field_name="publication_at")
            if publication_at is not None
            else None
        )
        if publication_status not in {"KNOWN", "UNKNOWN"}:
            raise ValueError("publication_status is invalid")
        if (publication_status == "KNOWN") != (publication is not None):
            raise ValueError("publication status and timestamp disagree")
        if not bundle.forms or len({item.form for item in bundle.forms}) != len(bundle.forms):
            raise ValueError("Task251 bundle forms must be unique and nonempty")
        if any(
            item.artifact.reference.report_date != bundle.report_date
            or item.artifact.reference.form != item.form
            for item in bundle.forms
        ):
            raise ValueError("Task251 bundle lineage is inconsistent")
        reported_subject_hashes = dict(bundle.subject_set_hashes)
        for item in bundle.forms:
            expected_hash = subject_set_sha256(set(item.subjects))
            if reported_subject_hashes.get(item.form.value) != expected_hash:
                raise ValueError("Task251 bundle subject hash mismatch")

        exact_forms = tuple(
            extract_exact_form_evidence(
                item, archive_executable=self.archive_executable
            )
            for item in sorted(bundle.forms, key=lambda value: value.form.value)
        )
        exact_by_form = {item.form: item for item in exact_forms}
        all_regns = {
            canonical_regn(observation.record.regn)
            for form in exact_forms
            for observation in form.observations
        }
        subjects_by_regn, subject_counts = self._persist_subjects(
            all_regns, observed_at=observed
        )
        self._checkpoint("subjects")

        artifact_count = EntityWriteCounts()
        snapshot_count = EntityWriteCounts()
        observation_count = EntityWriteCounts()
        persisted_snapshots: list[CbrBankReportSnapshot] = []
        for form_result in sorted(bundle.forms, key=lambda item: item.form.value):
            exact = exact_by_form[form_result.form.value]
            artifact, inserted = self._persist_artifact(
                form_result, ingested_at=ingested
            )
            artifact_count = replace(
                artifact_count,
                inserted=artifact_count.inserted + int(inserted),
                reused=artifact_count.reused + int(not inserted),
            )
            snapshot, observations, inserted_snapshot = self._persist_snapshot(
                form_result,
                exact,
                artifact,
                subjects_by_regn,
                observed_at=observed,
                ingested_at=ingested,
                publication_status=publication_status,
                publication_at=publication,
            )
            snapshot_count = replace(
                snapshot_count,
                inserted=snapshot_count.inserted + int(inserted_snapshot),
                reused=snapshot_count.reused + int(not inserted_snapshot),
            )
            inserted_observations, reused_observations = self._persist_observations(
                snapshot, observations, subjects_by_regn, ingested_at=ingested
            )
            observation_count = replace(
                observation_count,
                inserted=observation_count.inserted + inserted_observations,
                reused=observation_count.reused + reused_observations,
            )
            self._verify_snapshot(snapshot, observations)
            persisted_snapshots.append(snapshot)
        self._checkpoint("raw_evidence")

        identity_evidence_count = EntityWriteCounts()
        identity_profile_count = EntityWriteCounts()
        if identity_snapshot is not None:
            identity_evidence_count, identity_profile_count = self._persist_identity(
                identity_snapshot,
                subjects_by_regn,
                ingested_at=ingested,
            )
        self._checkpoint("identity")
        self.session.flush()
        return PersistBundleResult(
            subjects=subject_counts,
            artifacts=artifact_count,
            snapshots=snapshot_count,
            observations=observation_count,
            identity_evidence=identity_evidence_count,
            identity_profiles=identity_profile_count,
            forms=tuple(item.form for item in exact_forms),
            subject_count=len(subjects_by_regn),
            observation_count=sum(len(item.observations) for item in exact_forms),
        )

    def _persist_subjects(
        self, regns: set[str], *, observed_at: datetime
    ) -> tuple[dict[str, CbrBankReportingSubject], EntityWriteCounts]:
        canonical = sorted({canonical_regn(value) for value in regns}, key=int)
        existing: dict[str, CbrBankReportingSubject] = {}
        for batch in _chunks(canonical):
            rows = self.session.execute(
                select(CbrBankReportingSubject).where(
                    CbrBankReportingSubject.source == CBR_REPORTING_SUBJECT_SOURCE,
                    CbrBankReportingSubject.subject_type == CBR_REPORTING_SUBJECT_TYPE,
                    CbrBankReportingSubject.subject_regn.in_(batch),
                )
            ).scalars()
            existing.update({row.subject_regn: row for row in rows})
        inserted = updated = reused = 0
        for regn in canonical:
            row = existing.get(regn)
            if row is None:
                row = CbrBankReportingSubject(
                    contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                    source=CBR_REPORTING_SUBJECT_SOURCE,
                    subject_type=CBR_REPORTING_SUBJECT_TYPE,
                    subject_regn=regn,
                    first_observed_at=observed_at,
                    last_observed_at=observed_at,
                )
                try:
                    with self._concurrency_savepoint():
                        self.session.add(row)
                        self.session.flush()
                except IntegrityError:
                    if not self._recoverable_integrity_error():
                        raise
                    row = self.session.execute(
                        select(CbrBankReportingSubject).where(
                            CbrBankReportingSubject.source
                            == CBR_REPORTING_SUBJECT_SOURCE,
                            CbrBankReportingSubject.subject_type
                            == CBR_REPORTING_SUBJECT_TYPE,
                            CbrBankReportingSubject.subject_regn == regn,
                        )
                    ).scalar_one_or_none()
                    if row is None:
                        raise
                    reused += 1
                else:
                    inserted += 1
                if (
                    row.contract_version != CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION
                    or row.source != CBR_REPORTING_SUBJECT_SOURCE
                    or row.subject_type != CBR_REPORTING_SUBJECT_TYPE
                    or row.subject_regn != regn
                ):
                    raise ValueError(
                        "persisted CBR reporting subject contract mismatch"
                    )
                existing[regn] = row
                continue
            if (
                row.contract_version != CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION
                or row.source != CBR_REPORTING_SUBJECT_SOURCE
                or row.subject_type != CBR_REPORTING_SUBJECT_TYPE
            ):
                raise ValueError("persisted CBR reporting subject contract mismatch")
            first = _as_utc(row.first_observed_at)
            last = _as_utc(row.last_observed_at)
            new_first = min(first, observed_at)
            new_last = max(last, observed_at)
            if new_first != first or new_last != last:
                row.first_observed_at = new_first
                row.last_observed_at = new_last
                updated += 1
            else:
                reused += 1
        self.session.flush()
        return existing, EntityWriteCounts(
            inserted=inserted, reused=reused, updated=updated
        )

    def _persist_artifact(
        self, form_result: CbrFormResult, *, ingested_at: datetime
    ) -> tuple[CbrBankSourceArtifact, bool]:
        source = form_result.artifact
        content_hash = hashlib.sha256(source.content).hexdigest()
        content_size = len(source.content)
        if content_hash != source.content_sha256 or content_size != source.compressed_size:
            raise ValueError("Task251 artifact hash or size mismatch")
        if not source.content or not source.content_type:
            raise ValueError("Task251 artifact content metadata is incomplete")
        artifact_fingerprint = sha256_canonical(
            {
                "contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                "source": CBR_BANK_SOURCE,
                "content_sha256": content_hash,
                "compressed_size": content_size,
            }
        )
        row = self.session.execute(
            select(CbrBankSourceArtifact).where(
                CbrBankSourceArtifact.artifact_fingerprint == artifact_fingerprint
            )
        ).scalar_one_or_none()
        if row is not None:
            if (
                row.source != CBR_BANK_SOURCE
                or row.content_sha256 != content_hash
                or row.compressed_size != content_size
                or row.content_bytes != source.content
                or row.form != form_result.form.value
                or row.report_date != source.reference.report_date
            ):
                raise ValueError("artifact fingerprint semantic collision")
            return row, False
        row = CbrBankSourceArtifact(
            contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
            source=CBR_BANK_SOURCE,
            source_url=source.reference.source_url,
            artifact_filename=source.reference.artifact_filename,
            form=form_result.form.value,
            report_date=source.reference.report_date,
            content_bytes=source.content,
            content_sha256=content_hash,
            compressed_size=content_size,
            content_type=source.content_type,
            first_discovered_at=utc_datetime(
                source.reference.discovered_at, field_name="first_discovered_at"
            ),
            first_retrieved_at=utc_datetime(
                source.retrieved_at, field_name="first_retrieved_at"
            ),
            ingested_at=ingested_at,
            parser_contract_version=TASK251_PARSER_CONTRACT_VERSION,
            archive_runtime_contract=ARCHIVE_RUNTIME_CONTRACT,
            artifact_fingerprint=artifact_fingerprint,
        )
        try:
            with self._concurrency_savepoint():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            if not self._recoverable_integrity_error():
                raise
            existing = self.session.execute(
                select(CbrBankSourceArtifact).where(
                    CbrBankSourceArtifact.artifact_fingerprint
                    == artifact_fingerprint
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            if (
                existing.source != CBR_BANK_SOURCE
                or existing.content_sha256 != content_hash
                or existing.compressed_size != content_size
                or existing.content_bytes != source.content
                or existing.form != form_result.form.value
                or existing.report_date != source.reference.report_date
            ):
                raise ValueError("artifact fingerprint semantic collision")
            return existing, False
        return row, True

    def _planned_observations(
        self,
        exact: ExactFormEvidence,
        snapshot_fingerprint: str,
    ) -> list[dict[str, Any]]:
        planned = []
        for item in exact.observations:
            record = item.record
            regn = canonical_regn(record.regn)
            dimensions = [[name, value] for name, value in item.source_dimensions]
            observation_fingerprint = sha256_canonical(
                {
                    "contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                    "snapshot_fingerprint": snapshot_fingerprint,
                    "subject_regn": regn,
                    "archive_member_name": record.source_member.upper(),
                    "source_row_number": record.source_row_index,
                    "source_row_fingerprint": item.source_row_fingerprint,
                    "source_value_field": item.source_value_field,
                    "source_code": record.source_code,
                    "source_subcode": None,
                    "source_dimensions": dimensions,
                    "raw_value_text": item.raw_value_text,
                    "parsed_decimal_value": item.parsed_decimal_value,
                    "disclosure_state": record.disclosure_state.value,
                    "source_unit": record.raw_unit,
                    "source_currency": record.raw_currency,
                    "source_multiplier": record.raw_multiplier,
                    "source_date": record.source_date,
                }
            )
            planned.append(
                {
                    "form": exact.form,
                    "report_date": exact.report_date,
                    "subject_regn": regn,
                    "archive_member_name": record.source_member.upper(),
                    "source_row_number": record.source_row_index,
                    "source_row_fingerprint": item.source_row_fingerprint,
                    "source_value_field": item.source_value_field,
                    "source_code": record.source_code,
                    "source_subcode": None,
                    "source_dimensions": dimensions,
                    "source_fields_sha256": item.source_fields_sha256,
                    "raw_value_text": item.raw_value_text,
                    "parsed_decimal_value": item.parsed_decimal_value,
                    "disclosure_state": record.disclosure_state.value,
                    "source_unit": record.raw_unit,
                    "source_currency": record.raw_currency,
                    "source_multiplier": record.raw_multiplier,
                    "source_date": record.source_date,
                    "observation_fingerprint": observation_fingerprint,
                }
            )
        return planned

    def _persist_snapshot(
        self,
        form_result: CbrFormResult,
        exact: ExactFormEvidence,
        artifact: CbrBankSourceArtifact,
        subjects: dict[str, CbrBankReportingSubject],
        *,
        observed_at: datetime,
        ingested_at: datetime,
        publication_status: str,
        publication_at: datetime | None,
    ) -> tuple[CbrBankReportSnapshot, list[dict[str, Any]], bool]:
        del subjects
        member_inventory = [list(item) for item in form_result.member_schema_fingerprints]
        expected_subject_hash = subject_set_sha256(set(form_result.subjects))
        snapshot_fingerprint = sha256_canonical(
            {
                "contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                "artifact_fingerprint": artifact.artifact_fingerprint,
                "form": form_result.form.value,
                "report_date": form_result.artifact.reference.report_date,
                "value_member_name": exact.value_member_name,
                "member_schema_inventory": member_inventory,
                "form_schema_fingerprint": form_result.form_schema_fingerprint,
                "parser_contract_version": TASK251_PARSER_CONTRACT_VERSION,
                "observed_at": observed_at,
                "retrieved_at": form_result.artifact.retrieved_at,
                "publication_status": publication_status,
                "publication_at": publication_at,
            }
        )
        observations = self._planned_observations(exact, snapshot_fingerprint)
        observation_set = ordered_fingerprints_sha256(
            [item["observation_fingerprint"] for item in observations]
        )
        values = {
            "artifact_id": artifact.id,
            "form": form_result.form.value,
            "report_date": form_result.artifact.reference.report_date,
            "value_member_name": exact.value_member_name,
            "member_schema_inventory": member_inventory,
            "form_schema_fingerprint": form_result.form_schema_fingerprint,
            "parser_contract_version": TASK251_PARSER_CONTRACT_VERSION,
            "observed_at": observed_at,
            "retrieved_at": utc_datetime(
                form_result.artifact.retrieved_at, field_name="retrieved_at"
            ),
            "publication_status": publication_status,
            "publication_at": publication_at,
            "record_count": len(observations),
            "subject_count": len(form_result.subjects),
            "subject_set_sha256": expected_subject_hash,
            "observation_set_sha256": observation_set,
            "snapshot_fingerprint": snapshot_fingerprint,
        }
        row = self.session.execute(
            select(CbrBankReportSnapshot).where(
                CbrBankReportSnapshot.snapshot_fingerprint == snapshot_fingerprint
            )
        ).scalar_one_or_none()
        if row is not None:
            self._assert_snapshot_equal(row, values)
            return row, observations, False
        row = CbrBankReportSnapshot(
            contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
            ingested_at=ingested_at,
            **values,
        )
        try:
            with self._concurrency_savepoint():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            if not self._recoverable_integrity_error():
                raise
            existing = self.session.execute(
                select(CbrBankReportSnapshot).where(
                    CbrBankReportSnapshot.snapshot_fingerprint
                    == snapshot_fingerprint
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            self._assert_snapshot_equal(existing, values)
            return existing, observations, False
        return row, observations, True

    def _assert_snapshot_equal(
        self, row: CbrBankReportSnapshot, values: dict[str, Any]
    ) -> None:
        for name, expected in values.items():
            actual = getattr(row, name)
            if isinstance(expected, datetime):
                equal = _as_utc(actual) == _as_utc(expected)
            elif name == "member_schema_inventory":
                equal = _json_equal(actual, expected)
            else:
                equal = actual == expected
            if not equal:
                raise ValueError("snapshot fingerprint semantic collision")

    def _persist_observations(
        self,
        snapshot: CbrBankReportSnapshot,
        planned: list[dict[str, Any]],
        subjects: dict[str, CbrBankReportingSubject],
        *,
        ingested_at: datetime,
    ) -> tuple[int, int]:
        fingerprints = [item["observation_fingerprint"] for item in planned]
        existing: dict[str, CbrBankRawObservation] = {}
        for batch in _chunks(fingerprints):
            rows = self.session.execute(
                select(CbrBankRawObservation).where(
                    CbrBankRawObservation.observation_fingerprint.in_(batch)
                )
            ).scalars()
            existing.update({row.observation_fingerprint: row for row in rows})
        inserted = reused = 0
        additions = []
        for item in planned:
            fingerprint = item["observation_fingerprint"]
            row = existing.get(fingerprint)
            if row is not None:
                self._assert_observation_equal(row, snapshot, item, subjects)
                reused += 1
                continue
            subject = subjects[item["subject_regn"]]
            additions.append(
                CbrBankRawObservation(
                    snapshot_id=snapshot.id,
                    reporting_subject_id=subject.id,
                    contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                    parser_contract_version=TASK251_PARSER_CONTRACT_VERSION,
                    ingested_at=ingested_at,
                    **item,
                )
            )
            inserted += 1
        if additions:
            try:
                with self._concurrency_savepoint():
                    self.session.add_all(additions)
                    self.session.flush()
            except IntegrityError:
                if not self._recoverable_integrity_error():
                    raise
                inserted = 0
                raced: dict[str, CbrBankRawObservation] = {}
                addition_fingerprints = [
                    item.observation_fingerprint for item in additions
                ]
                for batch in _chunks(addition_fingerprints):
                    rows = self.session.execute(
                        select(CbrBankRawObservation).where(
                            CbrBankRawObservation.observation_fingerprint.in_(batch)
                        )
                    ).scalars()
                    raced.update(
                        {row.observation_fingerprint: row for row in rows}
                    )
                planned_by_fingerprint = {
                    item["observation_fingerprint"]: item for item in planned
                }
                for addition in additions:
                    existing_row = raced.get(addition.observation_fingerprint)
                    if existing_row is None:
                        raise
                    self._assert_observation_equal(
                        existing_row,
                        snapshot,
                        planned_by_fingerprint[addition.observation_fingerprint],
                        subjects,
                    )
                    reused += 1
        return inserted, reused

    def _assert_observation_equal(
        self,
        row: CbrBankRawObservation,
        snapshot: CbrBankReportSnapshot,
        values: dict[str, Any],
        subjects: dict[str, CbrBankReportingSubject],
    ) -> None:
        expected = dict(values)
        expected.update(
            snapshot_id=snapshot.id,
            reporting_subject_id=subjects[values["subject_regn"]].id,
        )
        for name, value in expected.items():
            actual = getattr(row, name)
            if name == "source_dimensions":
                equal = _json_equal(actual, value)
            else:
                equal = actual == value
            if not equal:
                raise ValueError("observation fingerprint semantic collision")

    def _verify_snapshot(
        self, snapshot: CbrBankReportSnapshot, planned: list[dict[str, Any]]
    ) -> None:
        rows = list(
            self.session.execute(
                select(CbrBankRawObservation)
                .where(CbrBankRawObservation.snapshot_id == snapshot.id)
                .order_by(CbrBankRawObservation.source_row_number)
            ).scalars()
        )
        if len(rows) != snapshot.record_count or len(rows) != len(planned):
            raise ValueError("persisted observation count mismatch")
        checksum = ordered_fingerprints_sha256(
            [row.observation_fingerprint for row in rows]
        )
        if checksum != snapshot.observation_set_sha256:
            raise ValueError("persisted observation checksum mismatch")

    def _persist_identity(
        self,
        identity_snapshot: CbrLegalIssuerBridgeSnapshot,
        subjects: dict[str, CbrBankReportingSubject],
        *,
        ingested_at: datetime,
    ) -> tuple[EntityWriteCounts, EntityWriteCounts]:
        if identity_snapshot.historical_backcast_allowed:
            raise ValueError("historical Task252 identity backcast is forbidden")
        results = sorted(identity_snapshot.bridge_results, key=lambda item: int(item.regn))
        result_regns = tuple(canonical_regn(item.regn) for item in results)
        requested_regns = tuple(
            sorted(
                {canonical_regn(item) for item in identity_snapshot.requested_regns},
                key=int,
            )
        )
        if (
            identity_snapshot.pit_status != "CURRENT_ONLY"
            or tuple(result_regns) != requested_regns
            or identity_snapshot.regn_set_hash
            != identifier_set_sha256(requested_regns)
        ):
            raise ValueError("Task252 bridge snapshot contract mismatch")
        if len(set(result_regns)) != len(results):
            raise ValueError("Task252 bridge results contain duplicate REGNs")
        evidence_inserted = evidence_reused = 0
        profile_inserted = profile_updated = profile_noop = 0
        for result in results:
            regn = canonical_regn(result.regn)
            if (
                result.registry_as_of != identity_snapshot.registry_as_of
                or result.finorg_last_update is None
                or _as_utc(result.finorg_last_update)
                != _as_utc(identity_snapshot.finorg_last_update)
                or result.retrieved_at is None
                or _as_utc(result.retrieved_at)
                != _as_utc(identity_snapshot.retrieved_at)
            ):
                raise ValueError("Task252 result timestamp lineage mismatch")
            subject = subjects.get(regn)
            if subject is None:
                raise ValueError("Task252 bridge result is outside the Task251 bundle")
            evidence, inserted = self._persist_identity_evidence(
                subject, result, ingested_at=ingested_at
            )
            evidence_inserted += int(inserted)
            evidence_reused += int(not inserted)
            profile_action = self._resolve_identity_profile(
                subject, evidence, ingested_at=ingested_at
            )
            profile_inserted += int(profile_action == "inserted")
            profile_updated += int(profile_action == "updated")
            profile_noop += int(profile_action == "noop")
        return (
            EntityWriteCounts(inserted=evidence_inserted, reused=evidence_reused),
            EntityWriteCounts(
                inserted=profile_inserted,
                updated=profile_updated,
                noop=profile_noop,
            ),
        )

    def _project_identity(
        self, result: CbrLegalIssuerBridgeResult
    ) -> tuple[CbrIdentityLinkState, LegalIssuer | None, str | None, str | None]:
        state = _TASK252_STATE_PROJECTION.get(
            result.bridge_state, CbrIdentityLinkState.SOURCE_IDENTITY_BLOCKED
        )
        if state != CbrIdentityLinkState.VERIFIED:
            return state, None, None, None
        if result.legal_issuer_id is None or result.legal_issuer_source_issuer_id is None:
            raise ValueError("verified Task252 result is missing LegalIssuer identity")
        issuer = self.session.get(LegalIssuer, result.legal_issuer_id)
        if (
            issuer is None
            or issuer.resolution_state != "verified"
            or issuer.source_issuer_id != result.legal_issuer_source_issuer_id
        ):
            raise ValueError("verified Task252 LegalIssuer lineage mismatch")
        return state, issuer, issuer.identity_source, issuer.source_issuer_id

    def _persist_identity_evidence(
        self,
        subject: CbrBankReportingSubject,
        result: CbrLegalIssuerBridgeResult,
        *,
        ingested_at: datetime,
    ) -> tuple[CbrBankSubjectLegalIssuerEvidence, bool]:
        if (
            result.registry_as_of is None
            or result.finorg_last_update is None
            or result.retrieved_at is None
        ):
            raise ValueError("Task252 bridge result lacks required source timestamps")
        state, issuer, identity_source, source_issuer_id = self._project_identity(result)
        observed = utc_datetime(result.retrieved_at, field_name="identity observed_at")
        finorg_last_update = utc_datetime(
            result.finorg_last_update, field_name="finorg_last_update"
        )
        diagnostics = sorted(
            {f"TASK252_STATE:{result.bridge_state.value}", *result.warnings}
        )
        values = {
            "reporting_subject_id": subject.id,
            "subject_regn": subject.subject_regn,
            "bridge_contract_version": TASK252_CONTRACT_VERSION,
            "bridge_state": state.value,
            "observed_ogrn": result.ogrn,
            "observed_inn": result.inn,
            "observed_cbr_name": result.cbr_registry_name,
            "legal_issuer_id": issuer.id if issuer is not None else None,
            "legal_issuer_identity_source": identity_source,
            "legal_issuer_source_issuer_id": source_issuer_id,
            "registry_as_of": result.registry_as_of,
            "finorg_last_update": finorg_last_update,
            "observed_at": observed,
            "retrieved_at": observed,
            "diagnostic_codes": diagnostics,
        }
        fingerprint = sha256_canonical(
            {
                "contract_version": CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                "subject_regn": subject.subject_regn,
                **{key: value for key, value in values.items() if key != "reporting_subject_id"},
            }
        )
        row = self.session.execute(
            select(CbrBankSubjectLegalIssuerEvidence).where(
                CbrBankSubjectLegalIssuerEvidence.evidence_fingerprint == fingerprint
            )
        ).scalar_one_or_none()
        if row is not None:
            self._assert_identity_evidence_equal(row, values)
            return row, False
        row = CbrBankSubjectLegalIssuerEvidence(
            contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
            ingested_at=ingested_at,
            evidence_fingerprint=fingerprint,
            **values,
        )
        try:
            with self._concurrency_savepoint():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            if not self._recoverable_integrity_error():
                raise
            existing = self.session.execute(
                select(CbrBankSubjectLegalIssuerEvidence).where(
                    CbrBankSubjectLegalIssuerEvidence.evidence_fingerprint
                    == fingerprint
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            self._assert_identity_evidence_equal(existing, values)
            return existing, False
        return row, True

    def _assert_identity_evidence_equal(
        self, row: CbrBankSubjectLegalIssuerEvidence, values: dict[str, Any]
    ) -> None:
        for name, expected in values.items():
            actual = getattr(row, name)
            if isinstance(expected, datetime):
                equal = _as_utc(actual) == _as_utc(expected)
            elif name == "diagnostic_codes":
                equal = _json_equal(actual, expected)
            else:
                equal = actual == expected
            if not equal:
                raise ValueError("identity evidence fingerprint semantic collision")

    def _resolve_identity_profile(
        self,
        subject: CbrBankReportingSubject,
        evidence: CbrBankSubjectLegalIssuerEvidence,
        *,
        ingested_at: datetime,
    ) -> str:
        profile = self.session.get(CbrBankSubjectLegalIssuerProfile, subject.id)
        if profile is None:
            profile = CbrBankSubjectLegalIssuerProfile(
                reporting_subject_id=subject.id,
                contract_version=CBR_BANK_RAW_EVIDENCE_CONTRACT_VERSION,
                current_evidence_id=evidence.id,
                bridge_state=evidence.bridge_state,
                legal_issuer_id=evidence.legal_issuer_id,
                legal_issuer_identity_source=evidence.legal_issuer_identity_source,
                legal_issuer_source_issuer_id=evidence.legal_issuer_source_issuer_id,
                current_ogrn=evidence.observed_ogrn,
                current_inn=evidence.observed_inn,
                current_cbr_name=evidence.observed_cbr_name,
                last_observed_at=evidence.observed_at,
                last_resolved_at=ingested_at,
            )
            self.session.add(profile)
            self.session.flush()
            return "inserted"
        current = (
            self.session.get(CbrBankSubjectLegalIssuerEvidence, profile.current_evidence_id)
            if profile.current_evidence_id is not None
            else None
        )
        if current is not None:
            current_time = _as_utc(current.observed_at)
            candidate_time = _as_utc(evidence.observed_at)
            if candidate_time < current_time:
                return "noop"
            if candidate_time == current_time and current.id != evidence.id:
                raise ValueError("tied current Task252 evidence is ambiguous")
            if current.id == evidence.id:
                return "noop"
        profile.current_evidence_id = evidence.id
        profile.bridge_state = evidence.bridge_state
        profile.legal_issuer_id = evidence.legal_issuer_id
        profile.legal_issuer_identity_source = evidence.legal_issuer_identity_source
        profile.legal_issuer_source_issuer_id = evidence.legal_issuer_source_issuer_id
        profile.current_ogrn = evidence.observed_ogrn
        profile.current_inn = evidence.observed_inn
        profile.current_cbr_name = evidence.observed_cbr_name
        profile.last_observed_at = evidence.observed_at
        profile.last_resolved_at = max(_as_utc(profile.last_resolved_at), ingested_at)
        self.session.flush()
        return "updated"
