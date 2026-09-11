from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select

from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base


ROOT = Path(__file__).resolve().parents[2]
TASK255_TABLES = {
    "cbr_bank_reporting_subjects",
    "cbr_bank_source_artifacts",
    "cbr_bank_report_snapshots",
    "cbr_bank_raw_observations",
    "cbr_bank_subject_legal_issuer_evidence",
    "cbr_bank_subject_legal_issuer_profiles",
}


def test_task255_migration_upgrade_downgrade_reupgrade(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "task255.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    Base.metadata.tables["cbr_bank_artifact_availability_evidence"].drop(engine)
    for table_name in (
        "cbr_bank_subject_legal_issuer_profiles",
        "cbr_bank_subject_legal_issuer_evidence",
        "cbr_bank_raw_observations",
        "cbr_bank_report_snapshots",
        "cbr_bank_source_artifacts",
        "cbr_bank_reporting_subjects",
    ):
        Base.metadata.tables[table_name].drop(engine)
    command.stamp(config, "202608280002")
    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert TASK255_TABLES.issubset(set(inspector.get_table_names()))
    assert "financial_reports" in inspector.get_table_names()
    snapshot_fks = inspector.get_foreign_keys("cbr_bank_report_snapshots")
    assert snapshot_fks[0]["options"].get("ondelete") == "RESTRICT"
    identity_fks = inspector.get_foreign_keys(
        "cbr_bank_subject_legal_issuer_evidence"
    )
    assert {fk["options"].get("ondelete") for fk in identity_fks} == {
        "RESTRICT",
        "SET NULL",
    }
    assert inspector.get_unique_constraints("cbr_bank_source_artifacts")
    assert inspector.get_check_constraints("cbr_bank_raw_observations")

    command.downgrade(config, "202608280002")
    remaining = set(inspect(engine).get_table_names())
    assert not TASK255_TABLES.intersection(remaining)
    assert {"legal_issuers", "financial_reports", "companies", "bonds"}.issubset(
        remaining
    )
    command.upgrade(config, "head")
    assert TASK255_TABLES.issubset(set(inspect(engine).get_table_names()))
    engine.dispose()


def test_task260b_migration_bootstraps_availability_without_raw_rewrite(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "task260b.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    evidence_table = Base.metadata.tables["cbr_bank_artifact_availability_evidence"]
    evidence_table.drop(engine)
    command.stamp(config, "202609010001")

    observed = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)
    subjects = Base.metadata.tables["cbr_bank_reporting_subjects"]
    artifacts = Base.metadata.tables["cbr_bank_source_artifacts"]
    snapshots = Base.metadata.tables["cbr_bank_report_snapshots"]
    observations = Base.metadata.tables["cbr_bank_raw_observations"]
    with engine.begin() as connection:
        subject_id = connection.execute(
            subjects.insert().values(
                subject_regn="1",
                first_observed_at=observed,
                last_observed_at=observed,
            )
        ).inserted_primary_key[0]
        artifact_id = connection.execute(
            artifacts.insert().values(
                source_url="https://www.cbr.ru/vfs/credit/forms/102-20240401.rar",
                artifact_filename="102-20240401.rar",
                form="0409102",
                report_date=date(2024, 4, 1),
                content_bytes=b"abc",
                content_sha256="a" * 64,
                compressed_size=3,
                content_type="application/octet-stream",
                first_discovered_at=observed,
                first_retrieved_at=observed,
                ingested_at=observed,
                parser_contract_version="task251-test",
                archive_runtime_contract="test",
                artifact_fingerprint="b" * 64,
            )
        ).inserted_primary_key[0]
        snapshot_id = connection.execute(
            snapshots.insert().values(
                artifact_id=artifact_id,
                form="0409102",
                report_date=date(2024, 4, 1),
                value_member_name="VALUE.DBF",
                member_schema_inventory=[],
                form_schema_fingerprint="c" * 64,
                parser_contract_version="task251-test",
                observed_at=observed,
                retrieved_at=observed,
                ingested_at=observed,
                publication_status="UNKNOWN",
                publication_at=None,
                record_count=1,
                subject_count=1,
                subject_set_sha256="d" * 64,
                observation_set_sha256="e" * 64,
                snapshot_fingerprint="f" * 64,
            )
        ).inserted_primary_key[0]
        connection.execute(
            observations.insert().values(
                snapshot_id=snapshot_id,
                reporting_subject_id=subject_id,
                form="0409102",
                report_date=date(2024, 4, 1),
                subject_regn="1",
                archive_member_name="VALUE.DBF",
                source_row_number=1,
                source_row_fingerprint="1" * 64,
                source_value_field="SIM_ITOGO",
                source_code="1",
                source_dimensions={},
                source_fields_sha256="2" * 64,
                raw_value_text="1.2300",
                parsed_decimal_value=Decimal("1.2300"),
                disclosure_state="PUBLIC_VALUE",
                source_unit="RUB",
                source_currency="RUB",
                source_multiplier=1,
                source_date=date(2024, 4, 1),
                parser_contract_version="task251-test",
                ingested_at=observed,
                observation_fingerprint="3" * 64,
            )
        )

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert "cbr_bank_artifact_availability_evidence" in inspector.get_table_names()
    assert inspector.get_foreign_keys(
        "cbr_bank_artifact_availability_evidence"
    )[0]["options"].get("ondelete") == "RESTRICT"
    assert inspector.get_check_constraints("cbr_bank_artifact_availability_evidence")
    assert inspector.get_unique_constraints("cbr_bank_artifact_availability_evidence")
    assert inspector.get_indexes("cbr_bank_artifact_availability_evidence") == [
        {
            "name": "ix_cbr_artifact_availability_evidence_artifact_observed",
            "column_names": ["artifact_id", "observed_at"],
            "unique": 0,
            "dialect_options": {},
        }
    ]
    with engine.connect() as connection:
        row = connection.execute(select(evidence_table)).mappings().one()
        assert row["artifact_id"] == artifact_id
        assert row["evidence_source"] == "CBR_DIRECT"
        assert row["exact_payload_bound"] is True
        assert row["source_reference"].endswith("102-20240401.rar")
        assert row["observed_at"] == observed.replace(tzinfo=None)
        assert connection.scalar(select(func.count()).select_from(artifacts)) == 1
        assert connection.scalar(select(func.count()).select_from(snapshots)) == 1
        assert connection.scalar(select(func.count()).select_from(observations)) == 1
        snapshot = connection.execute(select(snapshots)).mappings().one()
        raw = connection.execute(select(observations)).mappings().one()
        assert snapshot["publication_at"] is None
        assert raw["raw_value_text"] == "1.2300"
        assert raw["parsed_decimal_value"] == Decimal("1.2300")

    command.downgrade(config, "202609010001")
    assert "cbr_bank_artifact_availability_evidence" not in inspect(engine).get_table_names()
    assert {
        "cbr_bank_source_artifacts",
        "cbr_bank_report_snapshots",
        "cbr_bank_raw_observations",
    }.issubset(set(inspect(engine).get_table_names()))
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(evidence_table)) == 1
    engine.dispose()


def test_task260b_sqlite_precreated_metadata_table_is_validated_and_bootstrapped(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "task260b-precreated.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    artifacts = Base.metadata.tables["cbr_bank_source_artifacts"]
    evidence = Base.metadata.tables["cbr_bank_artifact_availability_evidence"]
    observed = datetime(2026, 9, 3, 12, tzinfo=timezone.utc)

    with engine.begin() as connection:
        artifact_ids = []
        for index, digest in enumerate(("a" * 64, "b" * 64), start=1):
            artifact_ids.append(
                connection.execute(
                    artifacts.insert().values(
                        source_url=f"https://www.cbr.ru/{index}.rar",
                        artifact_filename=f"{index}.rar",
                        form="0409102",
                        report_date=date(2024, index, 1),
                        content_bytes=bytes([index]),
                        content_sha256=digest,
                        compressed_size=1,
                        content_type="application/octet-stream",
                        first_discovered_at=observed,
                        first_retrieved_at=observed,
                        ingested_at=observed,
                        parser_contract_version="task251-test",
                        archive_runtime_contract="test",
                        artifact_fingerprint=str(index) * 64,
                    )
                ).inserted_primary_key[0]
            )
        connection.execute(
            evidence.insert().values(
                artifact_id=artifact_ids[0],
                evidence_source="CBR_DIRECT",
                observed_at=observed,
                exact_payload_bound=True,
                source_reference="https://www.cbr.ru/1.rar",
            )
        )

    command.stamp(config, "202609010001")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        rows = connection.execute(
            select(evidence).order_by(evidence.c.artifact_id)
        ).mappings().all()
        assert len(rows) == 2
        assert [row["artifact_id"] for row in rows] == artifact_ids
        assert all(row["evidence_source"] == "CBR_DIRECT" for row in rows)
        assert all(row["exact_payload_bound"] is True for row in rows)

    command.downgrade(config, "202609010001")
    assert "cbr_bank_artifact_availability_evidence" not in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(evidence)) == 2
    engine.dispose()


def test_task260b_sqlite_precreated_partial_table_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    database_path = tmp_path / "task260b-partial.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", database_url)
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    engine = create_engine(database_url)
    Base.metadata.create_all(engine)
    Base.metadata.tables["cbr_bank_artifact_availability_evidence"].drop(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE cbr_bank_artifact_availability_evidence "
            "(id INTEGER PRIMARY KEY, artifact_id INTEGER NOT NULL)"
        )
    command.stamp(config, "202609010001")
    with pytest.raises(RuntimeError, match="Partial or incompatible"):
        command.upgrade(config, "head")
    engine.dispose()


def test_task260b_revision_is_non_destructive_bootstrap_only() -> None:
    migration = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "202609110001_cbr_bank_artifact_availability_evidence_v1.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "202609110001"' in migration
    assert 'down_revision = "202609010001"' in migration
    assert "first_retrieved_at" in migration
    assert "source_url" in migration
    assert "report_date" not in migration
    assert ".update(" not in migration.lower()
    assert ".delete(" not in migration.lower()
    assert "op.add_column" not in migration


def test_task255_revision_and_scope_are_schema_only() -> None:
    migration = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "202609010001_cbr_bank_raw_financial_evidence_store_v1.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "202609010001"' in migration
    assert 'down_revision = "202608280002"' in migration
    assert "op.create_table(" in migration
    assert "op.add_column" not in migration
    assert "op.alter_column" not in migration
    assert "op.execute" not in migration
    for table in TASK255_TABLES:
        assert table in migration
