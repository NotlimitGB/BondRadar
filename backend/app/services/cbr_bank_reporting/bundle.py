from __future__ import annotations

import hashlib
import json
from datetime import date
from itertools import combinations

from .archive import (
    MAX_MEMBER_BYTES,
    MAX_TOTAL_UNCOMPRESSED_BYTES,
    extract_archive_members,
)
from .client import CbrBankRegulatoryClient
from .contracts import (
    CbrBankArtifact,
    CbrBankForm,
    CbrBankRegulatoryBundleSnapshot,
    CbrSourceError,
    CbrSourceStatus,
)
from .dbf import read_dbf_member
from .parsers import MAX_BUNDLE_RECORDS, parse_form


def _regn_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdecimal() else (1, value)


def subject_set_sha256(subjects: set[str]) -> str:
    projection = sorted(subjects, key=_regn_sort_key)
    payload = json.dumps(projection, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def _exclusive_membership_counts(
    forms: tuple[CbrBankForm, ...], subject_sets: dict[CbrBankForm, set[str]]
) -> tuple[tuple[str, int], ...]:
    universe = set().union(*(subject_sets[form] for form in forms))
    counts: list[tuple[str, int]] = []
    for size in range(1, len(forms) + 1):
        for group in combinations(forms, size):
            selected = set(group)
            count = sum(
                1
                for regn in universe
                if {form for form in forms if regn in subject_sets[form]} == selected
            )
            counts.append(("_".join(form.short_code for form in group), count))
    return tuple(counts)


class CbrBankRegulatoryBundleService:
    def __init__(
        self,
        *,
        client: CbrBankRegulatoryClient | None = None,
        archive_executable: str | None = None,
    ) -> None:
        self.client = client or CbrBankRegulatoryClient()
        self.archive_executable = archive_executable

    def fetch_bundle(
        self,
        *,
        report_date: date,
        forms: tuple[CbrBankForm, ...],
    ) -> CbrBankRegulatoryBundleSnapshot:
        if not forms or len(forms) > 4 or len(set(forms)) != len(forms):
            raise ValueError("forms must contain one to four unique values")
        references = self.client.discover_requested(forms=forms, report_date=report_date)
        artifacts = tuple(self.client.fetch_artifact(reference) for reference in references)
        return self.build_snapshot(report_date=report_date, artifacts=artifacts)

    def build_snapshot(
        self,
        *,
        report_date: date,
        artifacts: tuple[CbrBankArtifact, ...],
        enforce_approved_schema: bool = True,
        allow_dynamic_value_member: bool = False,
        max_archive_member_bytes: int = MAX_MEMBER_BYTES,
        max_archive_total_uncompressed_bytes: int = MAX_TOTAL_UNCOMPRESSED_BYTES,
    ) -> CbrBankRegulatoryBundleSnapshot:
        if not artifacts or len(artifacts) > 4:
            raise ValueError("one to four artifacts are required")
        by_form = {artifact.reference.form: artifact for artifact in artifacts}
        if len(by_form) != len(artifacts):
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT, "duplicate form artifact"
            )
        if any(artifact.reference.report_date != report_date for artifact in artifacts):
            raise CbrSourceError(
                CbrSourceStatus.INVALID_CONTENT, "mixed report dates"
            )
        parsed = []
        record_count = 0
        for form in sorted(by_form, key=lambda item: item.value):
            artifact = by_form[form]
            extracted = extract_archive_members(
                artifact,
                executable=self.archive_executable,
                max_member_bytes=max_archive_member_bytes,
                max_total_uncompressed_bytes=max_archive_total_uncompressed_bytes,
            )
            dbfs = tuple(read_dbf_member(member, content) for member, content in extracted)
            result = parse_form(
                form,
                artifact,
                dbfs,
                enforce_approved_schema=enforce_approved_schema,
                allow_dynamic_value_member=allow_dynamic_value_member,
            )
            record_count += len(result.records)
            if record_count > MAX_BUNDLE_RECORDS:
                raise CbrSourceError(
                    CbrSourceStatus.INVALID_DBF, "bundle record limit exceeded"
                )
            parsed.append(result)
        subject_sets = {item.form: set(item.subjects) for item in parsed}
        ordered_forms = tuple(item.form for item in parsed)
        overlaps: list[tuple[str, int]] = []
        for size in range(2, len(parsed) + 1):
            for group in combinations((item.form for item in parsed), size):
                intersection = set.intersection(*(subject_sets[form] for form in group))
                key = "_".join(form.short_code for form in group)
                overlaps.append((key, len(intersection)))
        return CbrBankRegulatoryBundleSnapshot(
            report_date=report_date,
            forms=tuple(parsed),
            subjects_by_form=tuple(
                (item.form.value, len(item.subjects)) for item in parsed
            ),
            records_by_form=tuple(
                (item.form.value, len(item.records)) for item in parsed
            ),
            subject_set_hashes=tuple(
                (item.form.value, subject_set_sha256(subject_sets[item.form]))
                for item in parsed
            ),
            cross_form_overlap=tuple(overlaps),
            exclusive_membership_counts=_exclusive_membership_counts(
                ordered_forms, subject_sets
            ),
            warnings=(
                "publication_timestamp_not_proven",
                "current_disclosure_is_regulatorily_reduced",
            ),
        )
