from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, func, inspect, select

from app import models  # noqa: F401
from app.core.config import settings
from app.db.base import Base


ROOT = Path(__file__).resolve().parents[2]
TABLES = (
    "credit_risk_source_artifacts",
    "credit_rating_events",
    "credit_default_events",
)


def _config(url: str) -> Config:
    config = Config(str(ROOT / "backend" / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_task263_migration_upgrade_downgrade_reupgrade(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'task263.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = _config(url)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(engine)
    command.stamp(config, "202609150002")

    command.upgrade(config, "head")
    inspector = inspect(engine)
    assert set(TABLES).issubset(inspector.get_table_names())
    assert {
        fk["options"].get("ondelete")
        for table in TABLES[1:]
        for fk in inspector.get_foreign_keys(table)
    } == {"RESTRICT"}
    for table in TABLES:
        with engine.connect() as connection:
            assert connection.scalar(
                select(func.count()).select_from(Base.metadata.tables[table])
            ) == 0

    command.downgrade(config, "202609150002")
    assert not set(TABLES).intersection(inspect(engine).get_table_names())
    assert "cbr_bank_credit_metrics" in inspect(engine).get_table_names()
    command.upgrade(config, "head")
    assert set(TABLES).issubset(inspect(engine).get_table_names())
    engine.dispose()


def test_task263_sqlite_accepts_exact_precreated_schema(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'precreated.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = _config(url)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    # Validate the Task263 pre-created contract, not today's additive metadata.
    for table in reversed(TABLES):
        Base.metadata.tables[table].drop(engine)
    command.stamp(config, "202609150002")
    command.upgrade(config, "202609150003")
    command.stamp(config, "202609150002")
    command.upgrade(config, "head")
    assert set(TABLES).issubset(inspect(engine).get_table_names())
    engine.dispose()


def test_task263_sqlite_rejects_partial_precreated_schema(tmp_path, monkeypatch) -> None:
    url = f"sqlite:///{(tmp_path / 'partial.db').as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    config = _config(url)
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Base.metadata.tables["credit_default_events"].drop(engine)
    Base.metadata.tables["credit_rating_events"].drop(engine)
    command.stamp(config, "202609150002")
    with pytest.raises(RuntimeError, match="Partial Task263"):
        command.upgrade(config, "head")
    engine.dispose()


def test_task263_revision_is_schema_only() -> None:
    text = (
        ROOT
        / "backend"
        / "alembic"
        / "versions"
        / "202609150003_credit_rating_default_evidence_v1.py"
    ).read_text(encoding="utf-8")
    assert 'revision = "202609150003"' in text
    assert 'down_revision = "202609150002"' in text
    for token in ("INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert token not in text.upper()
