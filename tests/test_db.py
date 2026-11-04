import json
import shutil
from pathlib import Path

import pytest

from conftest import NOW, make_ctx
from helpers import employer, source
from tracker import db, legacy, seeds


def test_empty_load_applies_all_migrations(ctx):
    assert db.schema_version(ctx.conn) == len(db.migrations())
    assert "opportunities" in db.tables(ctx.conn)


def test_round_trip_is_lossless_and_sorted(tmp_path, ctx):
    employer(ctx, "b-bank", "B Bank")
    employer(ctx, "a-bank", "A Bank")
    source(ctx, "a-bank", board="abank")
    db.save(ctx.conn, tmp_path)
    lines = (tmp_path / "employers.jsonl").read_text().splitlines()
    assert [json.loads(x)["id"] for x in lines] == ["a-bank", "b-bank"]
    again = db.load(tmp_path, NOW)
    assert db.counts(again) == db.counts(ctx.conn)
    db.save(again, tmp_path / "second")
    for f in tmp_path.glob("*.jsonl"):
        assert f.read_text() == (tmp_path / "second" / f.name).read_text()


def test_save_refuses_broken_foreign_keys(tmp_path, ctx):
    ctx.conn.execute("PRAGMA foreign_keys = OFF")
    ctx.conn.execute("INSERT INTO employer_sectors VALUES ('ghost', 'banking', 'seed')")
    with pytest.raises(db.IntegrityError):
        db.save(ctx.conn, tmp_path)
    assert not (tmp_path / "employers.jsonl").exists()


def test_old_dump_is_backed_up_then_migrated(tmp_path, monkeypatch):
    """A dump at schema v1 loads at v1, is snapshotted, then gets the newer migration."""
    mig = tmp_path / "migrations"
    mig.mkdir()
    shutil.copy(db.MIGRATIONS_DIR / "0001_schema.sql", mig)
    monkeypatch.setattr(db, "MIGRATIONS_DIR", mig)
    ctx = make_ctx()
    employer(ctx)
    db.save(ctx.conn, tmp_path / "dump")
    (mig / "0002_add_note.sql").write_text("ALTER TABLE employers ADD COLUMN note TEXT;")
    conn = db.load(tmp_path / "dump", NOW, backup_dir=tmp_path / "backups")
    assert db.schema_version(conn) == 2
    assert conn.execute("SELECT note FROM employers WHERE id = 'acme'").fetchone()[0] is None
    backups = list((tmp_path / "backups").iterdir())
    assert len(backups) == 1 and (backups[0] / "employers.jsonl").exists()


def test_restore_from_sqlite_backup(tmp_path, ctx):
    employer(ctx)
    path = db.backup_sqlite(ctx.conn, tmp_path / "b.sqlite")
    counts = db.restore(path, tmp_path / "restored", NOW)
    assert counts["employers"] == 1
    assert db.counts(db.load(tmp_path / "restored", NOW))["employers"] == 1


def test_seed_sync_is_idempotent_and_keeps_removed_firms(tmp_path, ctx):
    firms = tmp_path / "firms"
    firms.mkdir()
    (firms / "firms.yaml").write_text(
        "- {id: acme, name: Acme, sector: banking, country: CH, source: greenhouse, slug: acme}\n"
        "- {id: beta, name: Beta Capital, sector: private_equity, country: CH, source: page, "
        "careers_url: 'https://beta.example/careers'}\n")
    (firms / "blocked.yaml").write_text("- Gamma Bank\n")
    (firms / "candidates.yaml").write_text("- {name: Banco Ejemplo, website: 'https://www.bancoejemplo.pe', "
                                           "country: PE, sector: banking}\n")
    first = seeds.sync(ctx, firms)
    assert first == {"firms": 2, "sources_new": 2, "blocked": 1, "candidates": 1}
    second = seeds.sync(ctx, firms)
    assert second["sources_new"] == 0 and second["candidates"] == 0
    (firms / "firms.yaml").write_text(
        "- {id: acme, name: Acme, sector: banking, country: CH, source: greenhouse, slug: acme, enabled: false}\n")
    seeds.sync(ctx, firms)
    assert ctx.conn.execute("SELECT COUNT(*) FROM employers WHERE id = 'beta'").fetchone()[0] == 1
    assert ctx.conn.execute("SELECT enabled, health FROM sources WHERE id = 'greenhouse:acme'").fetchone()[:] == \
        (0, "disabled")
    note = ctx.conn.execute("SELECT access_note FROM employers WHERE name = 'Gamma Bank'").fetchone()[0]
    assert "by hand" in note


V1_ROLE = {"id": "abc123", "firm_id": "acme", "firm_name": "Acme", "sector": "banking",
           "title": "Summer Intern 2027", "url": "https://boards.greenhouse.io/acme/jobs/1?gh_src=x", "city": "Zurich",
           "countries": ["CH"], "season": "summer", "year": 2027, "closes": "2026-12-01", "languages_required": ["de"],
           "status": "open", "first_seen": "2026-11-01", "last_seen": "2026-11-04", "last_checked": "2026-11-04",
           "closed_on": None}


def test_legacy_import_keeps_ids_and_maps_duplicates(tmp_path, ctx):
    employer(ctx, "acme", "Acme")
    source(ctx)
    dup = dict(V1_ROLE, id="dup999", url="https://boards.greenhouse.io/acme/jobs/1")
    closed = dict(V1_ROLE, id="old111", url="https://boards.greenhouse.io/acme/jobs/2", status="closed",
                  closed_on="2026-10-01")
    (tmp_path / "roles.json").write_text(json.dumps({r["id"]: r for r in (V1_ROLE, dup, closed)}))
    (tmp_path / "firm_state.json").write_text(json.dumps({"acme": {"last_checked": "2026-11-04", "hash": "h",
                                                                    "zero_streak": 1}}))
    report = legacy.import_v1(ctx, tmp_path)
    assert report["imported"] == 2 and report["mapped"] == {"dup999": "abc123"}
    o = dict(ctx.conn.execute("SELECT * FROM opportunities WHERE id = 'abc123'").fetchone())
    assert o["status"] == "open" and o["internship_type"] == "summer" and o["closes_on"] == "2026-12-01"
    assert json.loads(o["countries"]) == ["CH"]
    assert ctx.conn.execute("SELECT status FROM opportunities WHERE id = 'old111'").fetchone()[0] == "closed"
    method = ctx.conn.execute("SELECT method FROM opportunity_facts WHERE opportunity_id = 'abc123' AND "
                              "field = 'closes_on'").fetchone()[0]
    assert method == "ai"  # v1 got it from the model, and the record says so
    src = ctx.conn.execute("SELECT zero_streak, content_hash FROM sources WHERE id = 'greenhouse:acme'").fetchone()
    assert tuple(src) == (1, "h")
    assert legacy.import_v1(ctx, tmp_path) is None  # runs once


def test_committed_legacy_data_imports_cleanly(ctx):
    seeds.sync(ctx, Path("firms"))
    report = legacy.import_v1(ctx, Path("data/legacy"))
    assert report is not None and report["imported"] + len(report["mapped"]) == report["roles"]
