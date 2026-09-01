from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect

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
