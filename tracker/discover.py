"""Monthly discovery: find FINMA-authorised Swiss banks that publish a job board we can read, and add them.

    python -m tracker.discover

Reads the current "Authorised banks and securities firms" list from FINMA, probes Greenhouse, Lever and Ashby for
a board that belongs to each firm not yet on our list, and adds the ones it finds to firms/firms.yaml. Names with
no board go to firms/unresolved.yaml. If the FINMA list cannot be read, it raises and changes nothing.
"""
import html
import io
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
import pypdf
import yaml
from bs4 import BeautifulSoup

from tracker.feeds import SourceError
from tracker.fetch import USER_AGENT, PoliteClient
from tracker.firms import fold, load_firms, make_id, name_key, name_tokens, same_firm, save_firms, write_yaml
from tracker.models import Firm

log = logging.getLogger(__name__)

FINMA_PAGE = "https://www.finma.ch/en/finma-public/authorised-institutions-individuals-and-products/"
MAX_PROBE_FIRMS = 150     # firms probed per run; the rest wait for next month
MAX_SLUGS = 5             # slug guesses per firm
SIMILARITY_THRESHOLD = 0.85

_GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards"
_LEVER = "https://api.lever.co/v0/postings"
_ASHBY = "https://api.ashbyhq.com/posting-api/job-board"

_CATEGORY_ROW_END = re.compile(r"[1-5]")
_FOOTER = re.compile(r"\d+/\d+|\d{2}\.\d{2}\.\d{4} /.*")
_BOARD_NOISE = re.compile(r"^(careers|jobs) at\s+|\s*[-|:]?\s*\b(jobs|careers|career|job board)$", re.I)


# --- FINMA list -----------------------------------------------------------------------------------------------

def find_finma_pdf_url(page_html: str, base_url: str) -> str:
    """The link to the PDF of the list of authorised banks and securities firms."""
    soup = BeautifulSoup(page_html, "html.parser")
    for a in soup.find_all("a", href=True):
        text = " ".join(a.get_text().split()).lower()
        if ("banks and securities firms" in text and "authorised" in text and "raiffeisen" not in text
                and urlsplit(a["href"]).path.lower().endswith(".pdf")):
            return urljoin(base_url, a["href"])
    raise SourceError("FINMA page: no PDF link for the authorised banks and securities firms list")


def parse_finma_names(pdf_bytes: bytes) -> list[str]:
    """Firm names from the list. Each entry is name lines, city, category, then marker lines ending in a lone
    licensing digit; branch names are cut at the first comma ("Bank X AG, Frankfurt, Zweigniederlassung Zürich")."""
    if not pdf_bytes.startswith(b"%PDF"):
        raise SourceError("FINMA list is not a PDF")
    try:
        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        pages = [p.extract_text() or "" for p in reader.pages]
    except Exception as e:  # pypdf raises many types on bad files
        raise SourceError(f"FINMA list: cannot read PDF ({e})") from e
    names: list[str] = []
    buf: list[str] = []
    for page in pages:
        lines = [ln.strip() for ln in page.splitlines() if ln.strip()]
        if "Category" in lines:
            lines = lines[lines.index("Category") + 1:]  # drop the repeated column header
        for ln in lines:
            if _FOOTER.fullmatch(ln):
                continue
            if _CATEGORY_ROW_END.fullmatch(ln):
                entry = [x for x in buf if x != "X"]
                buf = []
                if len(entry) >= 3:  # name line(s), city, category
                    name = " ".join(" ".join(entry[:-2]).split(",")[0].split())
                    if name and name not in names:
                        names.append(name)
            else:
                buf.append(ln)
    if not names:
        raise SourceError("FINMA list: no firm names found in the PDF")
    return names


# --- matching boards to names ---------------------------------------------------------------------------------

def slug_candidates(name: str) -> list[str]:
    """Likely job-board slugs for a firm name, best guess first."""
    head = name.split(",")[0]
    words = re.findall(r"[a-z0-9]+", fold(head))
    core = name_tokens(head)
    out = ["".join(core), "-".join(core), "".join(words), "-".join(words)]
    if len(core) > 1 and len(core[0]) >= 4:
        out.append(core[0])
    seen: list[str] = []
    for s in out:
        if s and s not in seen:
            seen.append(s)
    return seen[:MAX_SLUGS]


def name_similarity(firm_name: str, board_name: str) -> float:
    a, b = name_key(firm_name), name_key(board_name)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


class _Transient(Exception):
    """A request failed in a way that says nothing about whether the board exists."""


def _get(client: httpx.Client, url: str, **kwargs):
    try:
        resp = client.get(url, **kwargs)
    except SourceError:
        return None  # robots.txt says no: do not use this source
    except httpx.HTTPError as e:
        raise _Transient(f"{url}: {e}") from e
    if resp.status_code in (404, 410):
        return None
    if resp.status_code >= 400:
        raise _Transient(f"{url}: HTTP {resp.status_code}")
    return resp


def _json(client: httpx.Client, url: str, **kwargs):
    resp = _get(client, url, **kwargs)
    if resp is None:
        return None
    try:
        return resp.json()
    except ValueError:
        return None


def _page_title(client: httpx.Client, url: str) -> str | None:
    resp = _get(client, url)
    if resp is None:
        return None
    m = re.search(r"<title[^>]*>(.*?)</title>", resp.text, re.S | re.I)
    if not m:
        return None
    return _BOARD_NOISE.sub("", " ".join(html.unescape(m.group(1)).split())).strip() or None


def _greenhouse_name(client, slug):
    data = _json(client, f"{_GREENHOUSE}/{slug}")
    return _BOARD_NOISE.sub("", str(data["name"])).strip() if isinstance(data, dict) and data.get("name") else None


def _greenhouse_valid(client, slug):
    jobs = _json(client, f"{_GREENHOUSE}/{slug}/jobs")
    return isinstance(jobs, dict) and isinstance(jobs.get("jobs"), list)


def _lever_name(client, slug):
    data = _json(client, f"{_LEVER}/{slug}", params={"mode": "json", "limit": 1})
    return _page_title(client, f"https://jobs.lever.co/{slug}") if isinstance(data, list) else None


def _ashby_name(client, slug):
    data = _json(client, f"{_ASHBY}/{slug}")
    ok = isinstance(data, dict) and isinstance(data.get("jobs"), list)
    return _page_title(client, f"https://jobs.ashbyhq.com/{slug}") if ok else None


_PROBES = (("greenhouse", _greenhouse_name), ("lever", _lever_name), ("ashby", _ashby_name))


def probe_board(client: httpx.Client, name: str) -> tuple[str, str] | None:
    """(source, slug) of a job board whose company name resembles `name`, or None. Raises _Transient on trouble."""
    for slug in slug_candidates(name):
        for source, board_name in _PROBES:
            found = board_name(client, slug)
            if not found:
                continue
            if name_similarity(name, found) < SIMILARITY_THRESHOLD:
                log.info("ignoring %s board %r for %r: name does not match (%r)", source, slug, name, found)
                continue
            if source == "greenhouse" and not _greenhouse_valid(client, slug):
                continue
            return source, slug
    return None


# --- the monthly run ------------------------------------------------------------------------------------------

def _get_finma_names(client: httpx.Client) -> list[str]:
    try:
        page = client.get(FINMA_PAGE)
        page.raise_for_status()
        pdf = client.get(find_finma_pdf_url(page.text, str(page.url)))
        pdf.raise_for_status()
    except (httpx.HTTPError, httpx.InvalidURL) as e:
        raise SourceError(f"FINMA list: {e}") from e
    return parse_finma_names(pdf.content)


def _load_unresolved(path: Path) -> list[str]:
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text())
    return [str(n) for n in raw] if isinstance(raw, list) else []


def discover(firms_path: Path, unresolved_path: Path, client: httpx.Client) -> int:
    """Add FINMA-listed firms that have a readable job board. Returns the number of firms added.

    At most MAX_PROBE_FIRMS names are probed per run, in name order, names never probed before first; names
    still unmatched are recorded in `unresolved_path` (no duplicates) and probed again only with spare capacity.
    """
    names = _get_finma_names(client)  # raises before anything is read or written below
    firms = load_firms(firms_path)
    known = [f.name for f in firms] + [f.id for f in firms]
    previous = set(_load_unresolved(unresolved_path))
    todo = sorted({n for n in names if not any(same_firm(n, k) for k in known)}, key=lambda n: (n.casefold(), n))
    batch = ([n for n in todo if n not in previous] + [n for n in todo if n in previous])[:MAX_PROBE_FIRMS]

    added: list[Firm] = []
    unmatched: set[str] = set()
    used_ids = {f.id for f in firms}
    for name in batch:
        if any(same_firm(name, a.name) for a in added):
            continue
        try:
            found = probe_board(client, name)
        except _Transient as e:
            log.warning("discover: skipping %r for now: %s", name, e)
            continue
        if found is None:
            unmatched.add(name)
            continue
        source, slug = found
        base = make_id(name)
        fid, n = base, 2
        while fid in used_ids:
            fid, n = f"{base}-{n}", n + 1
        used_ids.add(fid)
        added.append(Firm(id=fid, name=name, sector="banking", country="CH", source=source, slug=slug))

    still_open = {n for n in todo if not any(same_firm(n, a.name) for a in added)}
    unresolved = sorted((previous | unmatched) & still_open, key=lambda n: (n.casefold(), n))
    if added:
        save_firms(firms_path, firms + added)
    if unresolved != _load_unresolved(unresolved_path) or not unresolved_path.exists():
        write_yaml(unresolved_path, unresolved)
    return len(added)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    polite = PoliteClient()
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30.0, follow_redirects=True,
                      event_hooks=polite.event_hooks()) as client:
        try:
            added = discover(Path("firms/firms.yaml"), Path("firms/unresolved.yaml"), client)
        except SourceError as e:
            log.error("discovery failed, nothing changed: %s", e)
            raise SystemExit(1) from e
    log.info("discovery added %d firms", added)


if __name__ == "__main__":
    main()
