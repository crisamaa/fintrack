"""End to end: one scheduled run over a temporary repository, with the network and the model mocked."""
import json
from datetime import timedelta

import httpx
import pytest

from conftest import NOW, FakeProvider, client, fixture_json
from tracker import db, run as runner
from tracker.config import Config

GH = fixture_json("greenhouse.json")


def repo(tmp_path):
    (tmp_path / "firms").mkdir()
    (tmp_path / "firms" / "firms.yaml").write_text(
        "- {id: acme, name: Acme Bank, sector: banking, country: CH, source: greenhouse, slug: acme, priority: 1}\n"
        "- {id: down, name: Down Bank, sector: banking, country: CH, source: lever, slug: down}\n")
    (tmp_path / "site").mkdir()
    return Config(root=tmp_path, digest_hour_utc=5)


def http():
    def handle(req):
        u = str(req.url).split("?")[0]
        if u == "https://boards-api.greenhouse.io/v1/boards/acme/jobs":
            return httpx.Response(200, json=GH)
        if u == "https://api.lever.co/v0/postings/down":
            return httpx.Response(503)
        return None
    return client(handle)


ENRICH = json.dumps({"is_internship": True, "countries": ["CA"], "city": "Toronto", "internship_type": "unknown",
                     "intake_year": None, "closes_on": None, "closes_quote": None, "languages": [],
                     "requirements": []})


def test_full_run_collects_enriches_exports_and_saves(tmp_path):
    cfg = repo(tmp_path)
    report = runner.run(cfg, now=NOW, http=http(), provider=FakeProvider(default=ENRICH), force_digest=True)
    assert report.status == "ok"  # a failed source is recorded on the source; it is not a failed run
    assert report.stats["checks_ok"] == 1 and report.stats["check_unavailable"] == 1
    assert not report.stats["step_errors"]
    conn = db.load(cfg.db_dir, NOW)
    assert conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 1
    assert conn.execute("SELECT enrichment FROM opportunities").fetchone()[0] == "done"
    assert conn.execute("SELECT status FROM runs").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM ai_calls WHERE outcome = 'ok'").fetchone()[0] == 1
    site = tmp_path / "site" / "data"
    opps = json.loads((site / "opportunities.json").read_text())["opportunities"]
    assert opps[0]["title"] == "Data Analyst, Intern" and opps[0]["sources"][0]["source_id"] == "greenhouse:acme"
    assert opps[0]["facts"] and opps[0]["events"]
    ops = json.loads((site / "ops.json").read_text())
    health = {s["id"]: s["health"] for s in ops["sources"]}
    assert health == {"greenhouse:acme": "ok", "lever:down": "degraded"}
    assert "GEMINI" not in (site / "ops.json").read_text() and "test-key" not in (site / "ops.json").read_text()
    market = json.loads((site / "market.json").read_text())
    assert {e["id"] for e in market["employers"]} == {"acme", "down"}
    digest = json.loads((site / "digest.json").read_text())
    assert any(i["kind"] == "new_opportunity" for i in digest["items"])


def test_second_run_is_quiet_and_lock_prevents_overlap(tmp_path):
    cfg = repo(tmp_path)
    runner.run(cfg, now=NOW, http=http(), provider=FakeProvider(default=ENRICH))
    report = runner.run(cfg, now=NOW + timedelta(hours=1), http=http(), provider=FakeProvider(default=ENRICH))
    assert report.stats.get("sources_checked", 0) == 0  # nothing due an hour later
    conn = db.load(cfg.db_dir, NOW)
    from tracker.db import iso, set_meta
    set_meta(conn, "run_lock", iso(NOW + timedelta(hours=5)))
    db.save(conn, cfg.db_dir)
    with pytest.raises(runner.RunLocked):
        runner.run(cfg, now=NOW + timedelta(hours=2), http=http())


def test_a_crashing_step_does_not_stop_the_others(tmp_path, monkeypatch):
    cfg = repo(tmp_path)
    from tracker import prospects
    monkeypatch.setattr(prospects, "score_all", lambda ctx: 1 / 0)
    report = runner.run(cfg, now=NOW, http=http(), provider=FakeProvider(default=ENRICH))
    assert "prospects" in report.stats["step_errors"]
    assert (tmp_path / "site" / "data" / "opportunities.json").exists()  # export still ran


def test_run_without_ai_key_still_collects(tmp_path):
    cfg = repo(tmp_path)
    report = runner.run(cfg, now=NOW, http=http(), provider=None)
    assert report.stats["checks_ok"] == 1 and report.stats["ai_blocked"] == "no_api_key"
    conn = db.load(cfg.db_dir, NOW)
    assert conn.execute("SELECT status FROM jobs WHERE kind = 'enrich'").fetchone()[0] == "pending"
