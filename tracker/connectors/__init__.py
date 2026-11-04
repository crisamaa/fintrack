"""Source connectors: one per recruitment platform, all behind the same small interface.

A connector knows three things about its platform:
  detect(url)            -> SourceRef | None    recognise a board URL (no network)
  validate(ref, http)    -> str | None          confirm the board exists; returns its display name if known
  fetch(source, http)    -> FetchResult         read the postings (raises SourceFailure when it cannot)

The scheduler and the ingestion pipeline only ever see these types, so adding a platform means adding one module
and registering it in CONNECTORS (docs/connectors.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from tracker.http import SafeClient, SourceFailure  # noqa: F401  (re-exported for connectors)


@dataclass(frozen=True)
class SourceRef:
    kind: str
    url: str                    # canonical URL for the source
    board: str | None = None
    site: str | None = None

    @property
    def id(self) -> str:
        if self.board:
            return f"{self.kind}:{self.board}" + (f"@{self.site}" if self.site and self.kind != "workday" else "")
        import hashlib
        return f"{self.kind}:{hashlib.sha1(self.url.encode()).hexdigest()[:10]}"


@dataclass
class RawPosting:
    external_id: str
    title: str
    url: str
    location: str = ""
    description: str = ""
    published_at: str | None = None        # as stated by the source (ISO date or datetime)
    structured: dict[str, Any] = field(default_factory=dict)
    extraction: str = "structured"         # structured | jsonld | ai | import


@dataclass
class FetchResult:
    postings: list[RawPosting]
    total_listed: int = 0                  # all jobs on the board, before the internship filter
    complete: bool = True                  # every page was read: absence of a posting is evidence it is gone
    not_modified: bool = False             # 304 or identical content hash: nothing to do
    etag: str | None = None
    last_modified: str | None = None
    content_hash: str | None = None
    page_text: str | None = None           # set when the page needs AI extraction
    needs_ai: bool = False
    found_sources: list[SourceRef] = field(default_factory=list)  # other boards linked from this source
    http_status: int | None = None


class Connector(Protocol):
    kind: str

    def detect(self, url: str) -> SourceRef | None: ...

    def validate(self, ref: SourceRef, http: SafeClient) -> str | None: ...

    def fetch(self, source: dict, http: SafeClient) -> FetchResult: ...


def _registry() -> dict[str, Connector]:
    from tracker.connectors import ats, page
    items: list[Connector] = [ats.Greenhouse(), ats.Lever(), ats.Ashby(), ats.Workday(), ats.SmartRecruiters(),
                              ats.Recruitee(), ats.Personio(), page.CareersPage()]
    return {c.kind: c for c in items}


CONNECTORS: dict[str, Connector] = {}


def get(kind: str) -> Connector:
    if not CONNECTORS:
        CONNECTORS.update(_registry())
    try:
        return CONNECTORS[kind]
    except KeyError:
        raise SourceFailure("format_changed", f"no connector for source kind {kind!r}") from None


def detect(url: str) -> SourceRef | None:
    """The ATS board a URL belongs to, if any (careers pages are not detected here)."""
    get("page")  # populate
    for kind, c in CONNECTORS.items():
        if kind == "page":
            continue
        ref = c.detect(url)
        if ref:
            return ref
    return None
