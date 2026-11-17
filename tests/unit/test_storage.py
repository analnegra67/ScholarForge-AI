import sqlite3
import threading

import pytest

from scholarforge.errors import InvalidInput, MigrationError, StorageError
from scholarforge.models.enums import NodeKind, Severity, SourceStatus, TaskActor, TaskStatus
from scholarforge.storage.database import Database
from scholarforge.storage.migrate import Migration, latest_version, load_migrations, migrate, open_database
from scholarforge.storage.repo import ProjectDB, normalize_symbol


@pytest.fixture
def pdb(tmp_path):
    db = open_database(tmp_path / "project.db")
    yield ProjectDB(db)
    db.close()


def test_migrations_apply_once(tmp_path):
    db = Database(tmp_path / "p.db")
    applied = migrate(db)
    assert applied == [m.version for m in load_migrations()]
    assert db.user_version() == latest_version()
    assert migrate(db) == []
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"nodes", "tasks", "sources", "claims", "terms", "notation", "labels", "issues", "figures"} <= tables
    assert db.integrity_check() == []
    db.close()


def test_migration_failure_rolls_back_and_backs_up(tmp_path):
    db = Database(tmp_path / "p.db")
    base = load_migrations()
    migrate(db, base)
    good = Migration(len(base) + 1, "add_col", "ALTER TABLE meta ADD COLUMN extra TEXT;")
    bad = Migration(len(base) + 2, "broken", "CREATE TABLE ok_table(x INTEGER);\nTHIS IS NOT SQL;")
    with pytest.raises(MigrationError):
        migrate(db, [*base, good, bad])
    # the good migration committed, the broken one left no trace
    assert db.user_version() == len(base) + 1
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "ok_table" not in tables
    assert (tmp_path / f"p.db.bak-v{len(base)}").exists()
    db.close()


def test_refuses_newer_schema(tmp_path):
    db = Database(tmp_path / "p.db")
    migrate(db)
    db.execute(f"PRAGMA user_version = {latest_version() + 5}")
    with pytest.raises(MigrationError):
        migrate(db)
    db.close()


def test_transaction_rollback_on_error(pdb):
    with pytest.raises(RuntimeError):
        with pdb.db.transaction() as c:
            c.execute("INSERT INTO meta(key, value) VALUES ('a', '1')")
            raise RuntimeError("boom")
    assert pdb.meta.get("a") is None
    with pdb.db.transaction():
        with pdb.db.transaction():  # nested joins the outer one
            pdb.meta.set("b", "2")
    assert pdb.meta.get("b") == "2"


def test_node_tree_and_walk_order(pdb):
    ch1 = pdb.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="Calculus", slug="calculus")
    ch2 = pdb.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=1, title="Algebra", slug="algebra")
    s1 = pdb.nodes.create(kind=NodeKind.SECTION, parent_id=ch1.id, position=0, title="Limits", slug="limits")
    b1 = pdb.nodes.create(kind=NodeKind.BLOCK, parent_id=s1.id, position=0, title="Def", slug="def",
                          plan={"target_words": 500})
    b2 = pdb.nodes.create(kind=NodeKind.BLOCK, parent_id=s1.id, position=1, title="Thm", slug="thm")
    order = [n.id for n in pdb.nodes.walk()]
    assert order == [ch1.id, s1.id, b1.id, b2.id, ch2.id]
    assert pdb.nodes.ancestor_of_kind(b2.id, NodeKind.CHAPTER).id == ch1.id
    assert pdb.nodes.get(b1.id).plan == {"target_words": 500}
    with pytest.raises(InvalidInput):
        pdb.nodes.update(b1.id, kind="chapter")
    pdb.nodes.delete(ch1.id)  # cascades
    assert pdb.nodes.find(b1.id) is None


def test_sources_terms_labels_issues(pdb):
    src = pdb.sources.add(citekey="rumelhart1986", title="Learning representations", authors=["D. Rumelhart"],
                          year=1986, doi="10.1038/323533a0")
    assert pdb.sources.by_identifier(doi="10.1038/323533A0").id == src.id
    pdb.sources.update(src.id, status=SourceStatus.VERIFIED, verification={"crossref": {"ok": True}})
    assert pdb.sources.get(src.id).status == SourceStatus.VERIFIED

    ch = pdb.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=0, title="C", slug="c")
    t = pdb.terms.add("Gradient", "vector of partial derivatives", ch.id, ["grad"])
    assert pdb.terms.get("gradient").id == t.id
    t2 = pdb.terms.redefine(t.id, "the vector of all first partial derivatives", ch.id)
    assert t2.version == 2 and len(pdb.terms.history(t.id)) == 2

    pdb.labels.define("thm-chain", "thm", ch.id, "Chain rule")
    with pytest.raises(InvalidInput):
        other = pdb.nodes.create(kind=NodeKind.CHAPTER, parent_id=None, position=1, title="D", slug="d")
        pdb.labels.define("thm-chain", "thm", other.id, "dup")
    pdb.labels.set_refs(ch.id, ["thm-chain", "eq-missing"])
    assert pdb.labels.unresolved() == [(ch.id, "eq-missing")]

    i1 = pdb.issues.add(severity=Severity.ERROR, category="xref", code="XREF", message="m", node_id=ch.id)
    i2 = pdb.issues.add(severity=Severity.ERROR, category="xref", code="XREF", message="m", node_id=ch.id)
    assert i1.id == i2.id  # de-duplicated
    assert pdb.issues.counts() == {"error": 1}
    assert pdb.issues.resolve_for_node(ch.id) == 1
    assert pdb.issues.counts() == {}


def test_symbol_normalisation():
    assert normalize_symbol("$\\boldsymbol{x}$") == normalize_symbol("\\mathbf{x}")
    assert normalize_symbol("\\nabla f") == "\\nablaf"


def test_concurrent_leases_are_exclusive(tmp_path):
    path = tmp_path / "p.db"
    setup = ProjectDB(open_database(path))
    task = setup.tasks.create(kind="x", actor=TaskActor.HOST)
    setup.close()

    winners: list[str] = []
    errors: list[Exception] = []
    barrier = threading.Barrier(6)

    def worker():
        try:
            local = ProjectDB(Database(path))
            barrier.wait()
            leased = local.tasks.lease_next(TaskActor.HOST, 60)
            if leased:
                winners.append(leased.lease_token)
            local.close()
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(winners) == 1
    check = ProjectDB(Database(path))
    assert check.tasks.get(task.id).status == TaskStatus.LEASED
    check.close()


def test_backup_api_produces_consistent_copy(pdb, tmp_path):
    pdb.meta.set("k", "v")
    target = pdb.db.backup_to(tmp_path / "copy.db")
    conn = sqlite3.connect(str(target))
    assert conn.execute("SELECT value FROM meta WHERE key='k'").fetchone()[0] == "v"
    conn.close()


def test_open_database_error_is_wrapped(tmp_path):
    folder = tmp_path / "dir.db"
    folder.mkdir()
    with pytest.raises((StorageError, MigrationError, sqlite3.Error)):
        open_database(folder)
