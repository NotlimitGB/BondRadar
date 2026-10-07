import gc,hashlib,importlib.util,tracemalloc
from pathlib import Path
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine,inspect,text
from app.services.historical_audit_canonical_json import chunks


def migration():
    path=Path(__file__).resolve().parents[1]/"alembic"/"versions"/"202610070001_historical_evidence_foundation.py"
    spec=importlib.util.spec_from_file_location("historical_migration",path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_migration_upgrade_metadata_compatibility_and_nonempty_refusal():
    m=migration();engine=create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE bonds (id INTEGER PRIMARY KEY)")
        m.op=Operations(MigrationContext.configure(c));m.upgrade();m.upgrade()
        assert len([t for t in inspect(c).get_table_names() if t.startswith("historical_evidence_")])==6
        c.exec_driver_sql("INSERT INTO historical_evidence_runs (run_sha256,source_manifest_sha256,history_start,history_end,policy_json,scope_json,planned_work_count) VALUES ('x','y','2020-09-01','2020-09-02','{}','{}',0)")
        with pytest.raises(RuntimeError,match="NONEMPTY"):m.downgrade()
        c.exec_driver_sql("DELETE FROM historical_evidence_runs");m.downgrade();m.upgrade()
    from app.db.base import Base
    import app.models
    engine=create_engine("sqlite://");Base.metadata.create_all(engine)
    with engine.begin() as c:
        m.op=Operations(MigrationContext.configure(c));m.upgrade()


def test_partial_and_incompatible_schema_rejected_before_ddl():
    m=migration();engine=create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE historical_evidence_runs (id INTEGER PRIMARY KEY)")
        m.op=Operations(MigrationContext.configure(c))
        with pytest.raises(RuntimeError,match="PARTIAL"):m.upgrade()


def test_missing_index_incompatible_contract_and_restrict_fk():
    m=migration();engine=create_engine("sqlite://")
    with engine.begin() as c:
        c.exec_driver_sql("PRAGMA foreign_keys=ON")
        c.exec_driver_sql("CREATE TABLE bonds (id INTEGER PRIMARY KEY)")
        m.op=Operations(MigrationContext.configure(c));m.upgrade()
        c.exec_driver_sql("INSERT INTO historical_evidence_runs (id,run_sha256,source_manifest_sha256,history_start,history_end,policy_json,scope_json,planned_work_count) VALUES (1,'x','y','2020-09-01','2020-09-02','{}','{}',0)")
        c.exec_driver_sql("INSERT INTO historical_evidence_work_items (run_id,query_sha256,query_json,next_offset,status) VALUES (1,'q','{}',0,'PENDING')")
        from sqlalchemy.exc import IntegrityError
        with pytest.raises(IntegrityError):c.exec_driver_sql("DELETE FROM historical_evidence_runs WHERE id=1")
        with pytest.raises(IntegrityError):c.exec_driver_sql("UPDATE historical_evidence_work_items SET next_offset=-1")
        with pytest.raises(IntegrityError):c.exec_driver_sql("INSERT INTO historical_evidence_work_items (run_id,query_sha256,query_json,next_offset,status) VALUES (1,'q','{}',0,'PENDING')")
        c.exec_driver_sql("DROP INDEX ix_historical_evidence_source_pages_work_id")
        with pytest.raises(RuntimeError,match="INCOMPATIBLE"):m.upgrade()


def test_two_million_observations_bounded_streaming_hash_and_memory():
    # Processing/output envelope, not database throughput or production RSS.
    gc.collect();tracemalloc.start();hash_=hashlib.sha256();count=0
    template={"date":"2020-09-01","source":"moex","nkd":"0","price":"100","identity":"EXTINCT"}
    bytes_hashed=0
    for batch in range(2000):
        rows=[template.copy() for _ in range(1000)]
        for fragment in chunks(rows):
            encoded=fragment.encode("ascii");hash_.update(encoded);bytes_hashed+=len(encoded)
        count+=len(rows)
        del rows
    peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    print(f"A3_SCALE observations={count} peak={peak} bytes_hashed={bytes_hashed}")
    assert count==2_000_000 and peak<64*1024*1024 and len(hash_.hexdigest())==64


def test_static_boundaries_and_mapper_refactor():
    root=Path(__file__).resolve().parents[1]/"app"/"services"
    for name in ("normalization","plan_service","audit_service"):
        source=(root/f"historical_evidence_{name}.py").read_text()
        assert "MoexBondUniverseService" not in source and "InvestmentModel" not in source
        assert "requests.get" not in source and "os.environ" not in source
    client=(root/"historical_evidence_source_client.py").read_text()
    assert 'verify=False' not in client and 'marketprice_board' not in client
