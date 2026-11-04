"""The seed firm list (firms/firms.yaml): hand-maintained starting points that discovery builds on.

The database is the system of record; this file is re-read every run and upserted, so adding a firm by hand still
works exactly as before. Removing a firm from the file does not delete it from the database (history is kept);
disable it with `enabled: false` instead.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

from tracker.taxonomy import COUNTRIES_OF_INTEREST, LEGACY_SECTORS, SECTORS

_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
Source = Literal["greenhouse", "lever", "ashby", "workday", "smartrecruiters", "recruitee", "personio", "page"]


class Firm(BaseModel):
    id: str
    name: str
    sector: str                       # legacy single sector, or any key of taxonomy.SECTORS
    sectors: list[str] = []           # extra overlapping sectors
    country: str
    source: Source
    slug: str | None = None
    site: str | None = None
    careers_url: str | None = None
    website: str | None = None
    priority: int = 2
    enabled: bool = True

    def all_sectors(self) -> list[str]:
        out = list(LEGACY_SECTORS.get(self.sector, [self.sector]))
        return out + [s for s in self.sectors if s not in out]


def _check(firm: Firm) -> None:
    if not _ID.fullmatch(firm.id):
        raise ValueError(f"{firm.id!r}: id must be a lowercase slug")
    if firm.country not in COUNTRIES_OF_INTEREST:
        raise ValueError(f"{firm.id}: country {firm.country!r} is not allowed")
    for s in firm.all_sectors():
        if s not in SECTORS:
            raise ValueError(f"{firm.id}: unknown sector {s!r}")
    if firm.priority not in (1, 2, 3):
        raise ValueError(f"{firm.id}: priority must be 1, 2 or 3")
    if firm.source == "page":
        if not firm.careers_url:
            raise ValueError(f"{firm.id}: page firm needs careers_url")
    elif not firm.slug:
        raise ValueError(f"{firm.id}: {firm.source} firm needs slug")
    elif firm.source == "workday" and (not firm.site or "/" not in firm.slug):
        raise ValueError(f"{firm.id}: workday firm needs site and slug 'tenant/board'")


def load_firms(path: Path) -> list[Firm]:
    raw = yaml.safe_load(Path(path).read_text())
    items = raw.get("firms") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        raise ValueError(f"{path}: expected a list of firms")
    firms, seen = [], set()
    for item in items:
        firm = Firm.model_validate(item)
        _check(firm)
        if firm.id in seen:
            raise ValueError(f"duplicate firm id {firm.id!r}")
        seen.add(firm.id)
        firms.append(firm)
    return firms


def load_names(path: Path) -> list[str]:
    """A plain YAML list of names (blocked.yaml, unresolved.yaml). A missing file means none."""
    if not Path(path).exists():
        return []
    names = yaml.safe_load(Path(path).read_text()) or []
    if not isinstance(names, list):
        raise ValueError(f"{path} must be a plain list of names")
    return [str(n) for n in names]


def write_yaml(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120))
    os.replace(tmp, path)
