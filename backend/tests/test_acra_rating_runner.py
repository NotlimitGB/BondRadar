from datetime import datetime, timezone
from pathlib import Path
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.models.company import Company
from app.models.bond import Bond
from app.models.legal_issuer import LegalIssuer
from app.models.credit_risk_evidence import CreditRatingEvent, CreditDefaultEvent
from app.services.credit_risk_evidence.acra import runner
from app.services.credit_risk_evidence.acra.contracts import FetchedPage, AcraError
from test_acra_rating_parser import HTML, URL


NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)


@pytest.fixture
def setup(db_session):
    db_session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
    db_session.execute(text("INSERT INTO alembic_version VALUES ('202609150003')"))
    company = Company(name="Issuer", ticker="TASK264")
    db_session.add(company)
    db_session.flush()
    db_session.add_all([
        LegalIssuer(source_issuer_id="test", issuer_title="Issuer", issuer_inn="7701234567", resolution_state="verified", last_resolved_at=NOW),
        Bond(company_id=company.id, isin="RU000A123456", name="Bond"),
    ])
    db_session.commit()
    source = SimpleNamespace(discover=lambda: (URL,), fetch_issuer=lambda url: FetchedPage(url, HTML, "text/html", NOW), list_pages=1)
    adapter = SimpleNamespace(readonly=lambda s: None, locks=lambda s: None)
    return db_session.get_bind(), source, adapter


def plan(tmp_path, setup):
    engine, source, adapter = setup
    directory = tmp_path / "bundle"
    result = runner.execute("plan", bundle_dir=directory, engine=engine, client=source, clock=lambda: NOW, _adapter=adapter)
    assert result["ready"], result
    return directory, result


def test_frozen_plan_preflight_apply_idempotency_and_no_defaults(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, source, adapter = setup
    assert planned["database_mutation_executed"] is False
    assert planned["counts"]["issuer_rating_events_candidate"] == 3
    assert planned["counts"]["bond_rating_events_candidate"] == 1
    preflight = runner.execute("preflight", bundle_dir=directory, engine=engine, expected_plan_hash=planned["plan_hash"], _adapter=adapter)
    assert preflight["ready"] and preflight["network_accessed"] is False
    first = runner.execute("apply", bundle_dir=directory, engine=engine, expected_plan_hash=planned["plan_hash"], _adapter=adapter)
    assert first["ready"], first
    assert first["credit_rating_events_inserted"] == 4
    assert first["production_actions"] == "ACRA_RATING_INGESTION_APPLY"
    replay = runner.execute("apply", bundle_dir=directory, engine=engine, expected_plan_hash=planned["plan_hash"], _adapter=adapter)
    assert replay["ready"], replay
    assert replay["credit_rating_events_inserted"] == 0 and replay["already_materialized"] == 4
    assert replay["production_actions"] == "NONE"
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(CreditDefaultEvent)) == 0
        assert s.scalar(select(func.count()).select_from(CreditRatingEvent)) == 4
        assert {e.publication_at for e in s.execute(select(CreditRatingEvent)).scalars()} == {None}


def test_tampered_bytes_hash_and_universe_change(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    payload = next((directory / "pages").glob("*.html"))
    original = payload.read_bytes()
    payload.write_bytes(original + b"tamper")
    with pytest.raises(AcraError):
        runner.load_bundle(directory)
    payload.write_bytes(original)
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.add(LegalIssuer(source_issuer_id="new", issuer_title="New", issuer_inn="7707654321", resolution_state="verified", last_resolved_at=NOW))
        s.commit()
    result = runner.execute("preflight", bundle_dir=directory, engine=engine, expected_plan_hash=planned["plan_hash"], _adapter=adapter)
    assert result["error_code"] == "UNIVERSE_CHANGED"
    assert result["database_mutation_executed"] is False


def test_offline_hash_rejected_before_database(tmp_path, setup):
    directory, _ = plan(tmp_path, setup)
    result = runner.execute("apply", bundle_dir=directory, expected_plan_hash="0"*64, database_url="postgresql://secret:password@host/db")
    assert result["error_code"] == "EXPECTED_PLAN_HASH_MISMATCH"
    assert result["database_accessed"] is False
    assert "password" not in json.dumps(result)


def test_commit_uncertainty(tmp_path, setup, monkeypatch):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    def fail(s):
        raise RuntimeError("secret")
    monkeypatch.setattr(runner.Session, "commit", fail)
    result = runner.execute("apply", bundle_dir=directory, engine=engine, expected_plan_hash=planned["plan_hash"], _adapter=adapter)
    assert result["commit_outcome_unknown"] and result["reconciliation_required"]
    assert result["database_mutation_executed"] is None
    assert result["production_actions"] == "ACRA_RATING_INGESTION_APPLY_OUTCOME_UNKNOWN"
    assert "secret" not in json.dumps(result)


def test_cli_matrix_and_safety(capsys):
    assert runner.main(["--mode", "apply", "--database-url-env", "DATABASE_URL", "--bundle-dir", "bad", "--confirm-read-only"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["database_accessed"] is False and value["pit_ready"] is False
    assert value["default_ingestion"] is False


def test_deterministic_hash_and_strict_manifest(tmp_path, setup):
    directory, first = plan(tmp_path, setup)
    engine, source, adapter = setup
    second = runner.execute("plan", bundle_dir=tmp_path / "second", engine=engine, client=source, clock=lambda: NOW, _adapter=adapter)
    assert second["plan_hash"] == first["plan_hash"]
    manifest, *_ = runner.load_bundle(directory)
    manifest["extra"] = "tamper"
    (directory / "manifest.json").write_text(json.dumps(manifest))
    assert runner.execute("preflight", bundle_dir=directory, engine=engine, expected_plan_hash=first["plan_hash"], _adapter=adapter)["database_accessed"] is False


def test_unsupported_in_universe_structure_blocks_plan_without_bundle(tmp_path, setup):
    engine, source, adapter = setup
    broken = HTML.replace(b"<h2>Credit rating</h2>", b"<h2>Unsupported credit layout</h2>")
    source.fetch_issuer = lambda url: FetchedPage(url, broken, "text/html", NOW)
    directory = tmp_path / "unsupported"
    result = runner.execute("plan", bundle_dir=directory, engine=engine, client=source,
                            clock=lambda: NOW, _adapter=adapter)
    assert result["ready"] is False
    assert result["error_code"] == "UNSUPPORTED_ISSUER_PAGE_STRUCTURE"
    assert result["database_mutation_executed"] is False
    assert result["source_bundle_written"] is False
    assert result["production_actions"] == "NONE"
    assert not (directory / "manifest.json").exists()
