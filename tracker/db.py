"""The database: SQLite in memory while a run works, persisted as one JSONL file per table under data/db/.

Why JSONL in git rather than a database server or a committed .sqlite file: the tracker runs on free GitHub Actions
with no server. A text dump sorted by primary key gives line-level diffs, so every run's commit is small and the
git history doubles as a complete, restorable backup. The schema comes from the versioned migrations in
tracker/migrations; the dump records which ones were applied (schema_migrations.jsonl), so loading an old dump
replays it at its own version and then applies the newer migrations.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class IntegrityError(Exception):
    """The database failed an integrity or foreign-key check; nothing is written."""


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    if len(s) == 10:
        return datetime.fromisoformat(s).replace(tzinfo=UTC)
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def loads(text: str | None, default: Any = None) -> Any:
    if text is None or text == "":
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def migrations() -> list[tuple[int, str, str]]:
    """(version, name, sql) for every migration file, in order. Files are named NNNN_name.sql."""
    out = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version = int(path.name.split("_", 1)[0])
        out.append((version, path.stem.split("_", 1)[1], path.read_text()))
    versions = [v for v, _, _ in out]
    if versions != list(range(1, len(versions) + 1)):
        raise RuntimeError(f"migrations must be numbered 1..n without gaps, got {versions}")
    return out


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)  # autocommit; explicit transactions via tx()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, "
        "applied_at TEXT NOT NULL)")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """A transaction; nested use joins the outer one (SAVEPOINTs keep inner failures contained)."""
    if conn.in_transaction:
        name = f"sp{id(conn) % 100000}_{os.urandom(3).hex()}"
        conn.execute(f"SAVEPOINT {name}")
        try:
            yield conn
        except BaseException:
            conn.execute(f"ROLLBACK TO {name}")
            conn.execute(f"RELEASE {name}")
            raise
        conn.execute(f"RELEASE {name}")
        return
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def migrate(conn: sqlite3.Connection, now: datetime, up_to: int | None = None) -> list[int]:
    """Apply pending migrations (each in its own transaction). Returns the versions applied."""
    applied = []
    current = schema_version(conn)
    for version, name, sql in migrations():
        if version <= current or (up_to is not None and version > up_to):
            continue
        conn.execute("BEGIN")
        try:
            # executescript would commit implicitly; run statements one by one inside the transaction instead.
            for stmt in _statements(sql):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (version, name, iso(now)))
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        applied.append(version)
        log.info("applied migration %04d_%s", version, name)
    return applied


def _statements(sql: str) -> list[str]:
    """Split a migration into statements. Rule for migration files: no "--" or ";" inside string literals, and
    no triggers (so comments can be stripped and statements split on ";" safely)."""
    lines = [ln.split("--", 1)[0] for ln in sql.splitlines()]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
                        "ORDER BY name").fetchall()
    return [r[0] for r in rows]


def _pk(conn: sqlite3.Connection, table: str) -> list[str]:
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk = [r["name"] for r in sorted(info, key=lambda r: r["pk"]) if r["pk"]]
    return pk or ["rowid"]


def check_integrity(conn: sqlite3.Connection) -> None:
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        raise IntegrityError(f"integrity_check: {ok}")
    bad = conn.execute("PRAGMA foreign_key_check").fetchall()
    if bad:
        sample = [tuple(r) for r in bad[:5]]
        raise IntegrityError(f"{len(bad)} foreign-key violations, e.g. {sample}")


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables(conn)}


# --- persistence ----------------------------------------------------------------------------------------------------

def load(dump_dir: Path, now: datetime, backup_dir: Path | None = None) -> sqlite3.Connection:
    """Open the database from its JSONL dump (or empty when there is none) and bring it to the latest schema.

    When migrations are pending for an existing dump, the dump directory is copied to `backup_dir` first.
    """
    dump_dir = Path(dump_dir)
    conn = connect()
    versions_file = dump_dir / "schema_migrations.jsonl"
    dumped = [json.loads(ln) for ln in versions_file.read_text().splitlines() if ln.strip()] \
        if versions_file.exists() else []
    dumped_version = max((r["version"] for r in dumped), default=0)
    migrate(conn, now, up_to=dumped_version)
    conn.execute("DELETE FROM schema_migrations")
    for r in dumped:
        conn.execute("INSERT INTO schema_migrations VALUES (?, ?, ?)", (r["version"], r["name"], r["applied_at"]))
    conn.execute("PRAGMA foreign_keys = OFF")  # rows load table by table; checked as a whole below
    conn.execute("BEGIN")
    try:
        known = set(tables(conn))
        for path in sorted(dump_dir.glob("*.jsonl")) if dump_dir.exists() else []:
            table = path.stem
            if table == "schema_migrations":
                continue
            if table not in known:
                raise IntegrityError(f"{path}: no table {table!r} at schema version {dumped_version}")
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                cols = list(row)
                conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [row[c] for c in cols])
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    conn.execute("PRAGMA foreign_keys = ON")
    check_integrity(conn)
    pending = [v for v, _, _ in migrations() if v > dumped_version]
    if pending and dumped and backup_dir is not None:
        snapshot(dump_dir, Path(backup_dir), f"pre-migration-v{pending[0]:04d}", now)
    migrate(conn, now)
    check_integrity(conn)
    return conn


def save(conn: sqlite3.Connection, dump_dir: Path) -> dict[str, int]:
    """Write every table as JSONL sorted by primary key. Checks integrity first; writes each file atomically."""
    check_integrity(conn)
    dump_dir = Path(dump_dir)
    dump_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, int] = {}
    for table in tables(conn):
        order = ", ".join(_pk(conn, table))
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order}").fetchall()
        path = dump_dir / f"{table}.jsonl"
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for r in rows:
                f.write(dumps(dict(r)) + "\n")
        os.replace(tmp, path)
        written[table] = len(rows)
    for stale in dump_dir.glob("*.jsonl"):
        if stale.stem not in written:
            stale.unlink()
    return written


def snapshot(dump_dir: Path, backup_dir: Path, label: str, now: datetime) -> Path:
    """Copy the dump directory to backup_dir/<timestamp>-<label>/ and return that path."""
    dest = Path(backup_dir) / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{label}"
    shutil.copytree(dump_dir, dest, dirs_exist_ok=True)
    log.info("backed up %s to %s", dump_dir, dest)
    return dest


def backup_sqlite(conn: sqlite3.Connection, path: Path) -> Path:
    """Write a standalone SQLite file (for inspection with any SQLite tool, or as an extra backup)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    out = sqlite3.connect(path)
    with out:
        conn.backup(out)
    out.close()
    return path


def restore(source: Path, dump_dir: Path, now: datetime) -> dict[str, int]:
    """Restore the dump directory from a backup: a snapshot directory or a .sqlite file. Validated before writing."""
    source = Path(source)
    if source.is_dir():
        conn = load(source, now)
    else:
        src = sqlite3.connect(source)
        conn = connect()
        conn.execute("DROP TABLE schema_migrations")
        src.backup(conn)
        src.close()
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn, now)
        check_integrity(conn)
    return save(conn, dump_dir)


# --- small helpers used everywhere -------------------------------------------------------------------------------

def one(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return conn.execute(sql, tuple(params)).fetchone()


def scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return None if row is None else row[0]


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    v = scalar(conn, "SELECT value FROM meta WHERE key = ?", (key,))
    return default if v is None else v


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                 (key, value))


def audit(conn: sqlite3.Connection, now: datetime, action: str, entity: str, entity_id: str | None = None,
          detail: dict | None = None, actor: str = "system") -> None:
    conn.execute("INSERT INTO audit_events (at, actor, action, entity, entity_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
                 (iso(now), actor, action, entity, entity_id, dumps(detail or {})))
