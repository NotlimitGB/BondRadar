from types import SimpleNamespace
from datetime import timedelta
from pathlib import Path
import json

import pytest
from sqlalchemy import select, text, func
from app.models.credit_risk_evidence import CreditRatingEvent, CreditRiskSourceArtifact, CreditDefaultEvent
from app.services.credit_risk_evidence.cbr_ratings import runner
from app.services.credit_risk_evidence.cbr_ratings.contracts import RepositoryError
from test_credit_risk_evidence import _seed_issuer
from test_cbr_rating_repository import NOW, UNIVERSE, fixture_responses, json_bytes, row, response, search_fields, INN


@pytest.fixture
def setup(db_session):
    _seed_issuer(db_session).issuer_inn = INN
    db_session.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32))"))
    db_session.execute(text("INSERT INTO alembic_version VALUES ('202609160001')"))
    db_session.commit()
    adapter = SimpleNamespace(readonly=lambda s: None, locks=lambda s: None)
    source = SimpleNamespace(collect=lambda u: fixture_responses(), requests=4)
    return db_session.get_bind(), source, adapter


def plan(tmp_path, setup, suffix="bundle"):
    engine, source, adapter = setup
    directory = tmp_path / suffix
    result = runner.execute("plan", bundle_dir=directory, engine=engine, client=source, clock=lambda: NOW, _adapter=adapter)
    assert result["ready"], result
    return directory, result


def test_plan_preflight_apply_replay_and_full_bytes_readback(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    assert planned["network_accessed"] and planned["database_mutation_executed"] is False
    assert planned["counts"]["agency_acra_events"] == 2
    pre = runner.execute("preflight", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])
    assert pre["ready"] and not pre["network_accessed"]
    first = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])
    assert first["ready"], first
    assert first["rating_events_inserted"] == 2 and first["source_artifacts_inserted"] == 2
    assert first["production_actions"] == "CBR_RATING_REPOSITORY_APPLY"
    replay = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])
    assert replay["ready"] and replay["rating_events_inserted"] == replay["source_artifacts_inserted"] == 0
    assert replay["production_actions"] == "NONE"
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(CreditDefaultEvent)) == 0
        assert {a.content_bytes for a in s.scalars(select(CreditRiskSourceArtifact))} == {r.content for r in fixture_responses()}
        assert all(e.rating_scale_raw is None and e.publication_at is None for e in s.scalars(select(CreditRatingEvent)))


def test_changed_response_reuses_logical_events_and_new_facts_insert(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, source, adapter = setup
    assert runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])["ready"]
    old = fixture_responses()
    # Different exact JSON bytes with the same historical/current facts.
    from dataclasses import replace
    source.collect = lambda u: tuple(replace(r, content=r.content+b" ", retrieved_at=NOW+timedelta(days=1)) for r in old)
    directory, second = plan(tmp_path, setup, "later")
    assert second["counts"]["semantic_reobservations_existing"] == 2
    result = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=second["plan_hash"])
    assert result["ready"] and result["source_artifacts_inserted"] == 2 and result["rating_events_inserted"] == 0


def test_tamper_and_hash_precede_environment_and_engine(tmp_path, setup, monkeypatch):
    directory, planned = plan(tmp_path, setup)
    def forbidden(*a, **k):
        raise AssertionError("DB environment/engine touched")
    monkeypatch.setattr(runner, "create_engine", forbidden)
    monkeypatch.setattr(runner.os.environ, "get", forbidden)
    result = runner.execute("apply", bundle_dir=directory, database_url="postgresql://secret", expected_plan_hash="0"*64)
    assert not result["database_accessed"] and result["error_code"] == "EXPECTED_PLAN_HASH_MISMATCH"
    file = next((directory/"responses").glob("*.json")); file.write_bytes(b"tampered")
    result = runner.execute("preflight", bundle_dir=directory, expected_plan_hash=planned["plan_hash"])
    assert not result["database_accessed"] and not result["ready"]


def test_universe_and_revision_guard(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.execute(text("UPDATE alembic_version SET version_num='202609150003'")); s.commit()
    result = runner.execute("preflight", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])
    assert result["error_code"] == "UNEXPECTED_SCHEMA_REVISION" and not result["database_mutation_executed"]


def test_commit_uncertainty_and_atomic_rollback(tmp_path, setup, monkeypatch):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    def fail(s):
        raise RuntimeError("secret password")
    monkeypatch.setattr(runner.Session, "commit", fail)
    result = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter, expected_plan_hash=planned["plan_hash"])
    assert result["error_code"] == "COMMIT_OUTCOME_UNKNOWN"
    assert result["commit_outcome_unknown"] and result["reconciliation_required"]
    assert result["database_mutation_executed"] is None
    assert result["production_actions"] == "CBR_RATING_REPOSITORY_APPLY_OUTCOME_UNKNOWN"
    assert "password" not in json.dumps(result)


def test_cli_no_network_offline_modes_and_confirmations(capsys):
    assert runner.main(["--mode","apply","--database-url-env","DATABASE_URL","--bundle-dir","bad","--confirm-read-only"]) == 2
    value = json.loads(capsys.readouterr().out)
    assert value["network_accessed"] is False and value["database_accessed"] is False and value["pit_ready"] is False


def test_deterministic_hash_and_manifest_tampering(tmp_path, setup):
    directory, first = plan(tmp_path, setup)
    _, second = plan(tmp_path, setup, "second")
    assert first["plan_hash"] == second["plan_hash"]
    manifest, *_ = runner.load_bundle(directory)
    manifest["universe"]["issuer_inns"] = []
    (directory/"manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(RepositoryError, match="BUNDLE_HASH_MISMATCH"):
        runner.load_bundle(directory)


@pytest.mark.parametrize("with_eligible", [True, False])
def test_mixed_full_universe_bundle_counts_coverage_and_offline_preflight(tmp_path, setup, with_eligible):
    from sqlalchemy.orm import Session
    from app.models import LegalIssuer
    engine, source, adapter = setup
    with Session(engine) as session:
        if with_eligible:
            _seed_issuer(session, source_id="foreign", state="verified").issuer_inn = "0010000025"
        else:
            session.scalar(select(LegalIssuer)).issuer_inn = "0010000025"
            source.collect = lambda u: ()
            source.requests = 2
        session.commit()
    directory, planned = plan(tmp_path, setup)
    manifest, responses, candidates, counts = runner.load_bundle(directory)
    expected_inns = sorted(["0010000025"] + ([INN] if with_eligible else []))
    assert manifest["universe"]["issuer_inns"] == expected_inns
    assert manifest["universe_sha256"] == runner.canonical_json_sha256(manifest["universe"])
    assert counts["issuer_universe_count"] == len(expected_inns)
    assert counts["issuer_query_eligible_count"] == int(with_eligible)
    assert counts["issuer_query_ineligible_count"] == 1
    assert counts["issuer_queries_attempted"] == counts["issuer_queries_succeeded"] == int(with_eligible)
    assert counts["issuer_query_eligible_count"] + counts["issuer_query_ineligible_count"] == counts["issuer_universe_count"]
    assert planned["coverage"]["issuer_coverage_pct"] == ("50" if with_eligible else "0")
    assert planned["network_accessed"] and planned["transaction_read_only"]
    assert not planned["database_mutation_executed"] and not planned["database_persistence"]
    assert planned["production_actions"] == "NONE" and not planned["pit_ready"]
    _, repeated = plan(tmp_path, setup, "repeated")
    assert repeated["plan_hash"] == planned["plan_hash"]
    source.collect = lambda u: pytest.fail("offline preflight attempted source access")
    preflight = runner.execute("preflight", bundle_dir=directory, engine=engine, _adapter=adapter,
                               expected_plan_hash=planned["plan_hash"])
    assert preflight["ready"] and preflight["transaction_read_only"] and not preflight["network_accessed"]
    assert preflight["counts"] == planned["counts"] and not preflight["database_mutation_executed"]
    assert preflight["production_actions"] == "NONE" and not preflight["pit_ready"]
    # Rehash the tampered body to prove derivation, not merely the outer hash, rejects it.
    for key in ("issuer_query_eligible_count", "issuer_query_ineligible_count"):
        tampered = json.loads(json.dumps(manifest))
        tampered["counts"][key] += 1
        tampered["plan_hash"] = runner.canonical_json_sha256({k: v for k, v in tampered.items() if k != "plan_hash"})
        (directory/"manifest.json").write_text(json.dumps(tampered))
        with pytest.raises(RepositoryError, match="DERIVED_COUNT_MISMATCH"):
            runner.load_bundle(directory)


def test_universe_change_blocks_offline_apply(tmp_path, setup):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        _seed_issuer(session, source_id="another", state="verified").issuer_inn = "7707654321"
        session.commit()
    result = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter,
                            expected_plan_hash=planned["plan_hash"])
    assert result["error_code"] == "UNIVERSE_CHANGED" and not result["database_mutation_executed"]
    assert result["production_actions"] == "NONE"


def test_readonly_and_lock_statement_protocol():
    statements = []
    session = SimpleNamespace(execute=lambda sql: (statements.append(str(sql)) or SimpleNamespace(scalar_one=lambda: "on")))
    runner._readonly(session, None)
    assert statements == ["SET TRANSACTION READ ONLY", "SHOW transaction_read_only"]
    statements.clear(); runner._locks(session, None)
    assert statements == ["SET LOCAL lock_timeout = '5s'",
        "LOCK TABLE legal_issuers IN SHARE MODE", "LOCK TABLE bonds IN SHARE MODE",
        "LOCK TABLE credit_risk_source_artifacts IN SHARE ROW EXCLUSIVE MODE",
        "LOCK TABLE credit_rating_events IN SHARE ROW EXCLUSIVE MODE"]
    session.execute = lambda sql: SimpleNamespace(scalar_one=lambda: "off")
    with pytest.raises(RepositoryError, match="READ_ONLY_NOT_VERIFIED"):
        runner._readonly(session, None)


@pytest.mark.parametrize("failure_stage", ["before_commit", "after_commit"])
def test_readback_failure_preserves_known_mutation_truth(tmp_path, setup, monkeypatch, failure_stage):
    directory, planned = plan(tmp_path, setup)
    engine, _, adapter = setup
    original, calls = runner._reconcile, []
    def broken(*args):
        calls.append(1)
        if len(calls) == (2 if failure_stage == "before_commit" else 3):
            raise RepositoryError("INJECTED_READBACK_FAILURE")
        return original(*args)
    monkeypatch.setattr(runner, "_reconcile", broken)
    result = runner.execute("apply", bundle_dir=directory, engine=engine, _adapter=adapter,
                            expected_plan_hash=planned["plan_hash"])
    committed = failure_stage == "after_commit"
    assert not result["ready"] and result["commit_completed"] == committed
    assert result["database_mutation_executed"] == committed
    assert result["reconciliation_required"] == committed
    assert result["production_actions"] == ("CBR_RATING_REPOSITORY_APPLY" if committed else "NONE")
    from sqlalchemy.orm import Session
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(CreditRatingEvent)) == (2 if committed else 0)


def test_unsupported_source_never_publishes_bundle(tmp_path, setup):
    from dataclasses import replace
    engine, source, adapter = setup
    responses = fixture_responses()
    source.collect = lambda universe: (replace(responses[0], content=responses[0].content.replace(
        'АКРА (АО)'.encode(), b'UNKNOWN')), *responses[1:])
    directory = tmp_path/"blocked"
    result = runner.execute("plan", bundle_dir=directory, engine=engine, client=source,
                            clock=lambda: NOW, _adapter=adapter)
    assert not result["ready"] and not directory.exists() and not result["database_mutation_executed"]
