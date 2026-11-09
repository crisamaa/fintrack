"""Build a local copy of the site with freshly classified data, for trying the UI before the next tracker run.

Reads data/db into memory and writes only to the output directory; data/db and site/data are left unchanged (checked).
Usage: PYTHONPATH=. .venv/bin/python tests/e2e/build_site.py . /tmp/fintrack-site
"""
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from tracker import db, export, roles
from tracker.config import Config
from tracker.context import Ctx

repo, out = Path(sys.argv[1]), Path(sys.argv[2])
before = {p.name: p.stat().st_mtime for p in (repo / "data" / "db").glob("*.jsonl")}
now = datetime.now(UTC)
conn = db.load(repo / "data" / "db", now)          # in memory; backup_dir None: nothing written
n = roles.refresh(conn, now)
ctx = Ctx(conn=conn, now=now, config=Config(root=repo), http=None, ai=None)
if out.exists(): shutil.rmtree(out)
shutil.copytree(repo / "site", out)
(out / "data" / "opportunities.json").write_text(json.dumps(export.opportunities(ctx)))
(out / "data" / "market.json").write_text(json.dumps(export.market(ctx)))
after = {p.name: p.stat().st_mtime for p in (repo / "data" / "db").glob("*.jsonl")}
assert before == after, "data/db changed"
print("classified", n, "opportunities; site copy at", out)
