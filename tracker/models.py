import hashlib
from datetime import date
from typing import Literal

from pydantic import BaseModel

ALLOWED_COUNTRIES: frozenset[str] = frozenset(
    "CH AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO".split()
)

Sector = Literal["banking", "private_equity", "finance", "fintech"]
Source = Literal["greenhouse", "lever", "ashby", "workday", "page"]
Season = Literal["summer", "spring", "winter", "off_cycle", "unknown"]


class Firm(BaseModel):
    id: str
    name: str
    sector: Sector
    country: str
    source: Source
    slug: str | None = None
    site: str | None = None
    careers_url: str | None = None


class Posting(BaseModel):
    firm_id: str
    title: str
    url: str
    location: str
    description: str


class Enrichment(BaseModel):
    is_internship: bool
    countries: list[str]
    city: str | None
    season: Season
    year: int | None
    closes: date | None
    languages_required: list[str]


class Role(BaseModel):
    id: str
    firm_id: str
    firm_name: str
    sector: Sector
    title: str
    url: str
    city: str | None
    countries: list[str]
    season: Season
    year: int | None
    closes: date | None
    languages_required: list[str]
    status: Literal["open", "closed"]
    first_seen: date
    last_seen: date
    last_checked: date
    closed_on: date | None = None


def role_id(firm_id: str, url: str) -> str:
    return hashlib.sha1(f"{firm_id}|{url}".encode()).hexdigest()[:16]
