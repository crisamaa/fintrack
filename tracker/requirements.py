"""Eligibility requirements stated in a posting, extracted deterministically (multilingual rules) with the sentence
that states each one as evidence. AI extraction adds to these (tracker.enrich); evaluation against the user's
private profile happens in the browser (site/eligibility.js), so nothing personal is ever needed here.

Value shapes by kind (JSON in opportunity_requirements.value):
  work_authorization  {"regions": ["CH", "EU", "EEA", "EFTA", "GB", ...]}  must already hold the right to work there
  sponsorship         {"available": true | false}
  degree_level        {"levels": ["bachelor", "master", "phd"]}
  graduation_year     {"years": [2027, 2028]} or {"penultimate": true}
  enrollment          {"required": true}
  duration_months     {"min": 3, "max": 6}
  language            {"code": "de"}
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from tracker.db import dumps, iso
from tracker.taxonomy import fold, required_languages

_REGION_WORDS = [
    (r"\beu\s*/\s*efta\b|\befta\s*/\s*eu\b", ["EU", "EFTA"]),
    (r"\beea\b|\bewr\b|european economic area", ["EEA"]),
    (r"\beu\b|european union|\bue\b|union europeenne|union europea", ["EU"]),
    (r"\befta\b", ["EFTA"]),
    (r"switzerland|swiss|schweiz|suisse|svizzera|\bch\b", ["CH"]),
    (r"united kingdom|\buk\b|britain", ["GB"]),
    (r"\bperu\b", ["PE"]),
    (r"united states|\bus\b|\busa\b", ["US"]),
]
_REGION_RES = [(re.compile(p), r) for p, r in _REGION_WORDS]

_AUTH = re.compile(
    r"(right|authori[sz]ation|authori[sz]ed|eligib\w*|permit|entitled)\s+(to\s+)?(work|employment)|work\s+permit|"
    r"work\s+authori[sz]ation|arbeitsbewilligung|arbeitserlaubnis|aufenthaltsbewilligung|permis\s+de\s+travail|"
    r"autorisation\s+de\s+travail|permiso\s+de\s+trabajo|permesso\s+di\s+lavoro|(eu|efta|swiss)[\s/-]+(citizen|"
    r"national|staatsangehoerig|nationalit)", re.I)
_NO_SPONSOR = re.compile(
    r"(no|not|unable\s+to|cannot|can\s*not|will\s+not|do\s+not|does\s+not)\s+(be\s+able\s+to\s+)?(offer|provide|"
    r"support|sponsor)\w*\s+(any\s+)?(visa|work\s+permit|sponsorship|immigration)|without\s+(visa\s+)?sponsorship|"
    r"keine\s+(arbeits|visa)\w*|sponsorship\s+(is\s+)?not\s+(available|possible|offered)", re.I)
_SPONSOR = re.compile(r"(visa|work\s+permit)\s+sponsorship\s+(is\s+|will\s+be\s+)?(available|provided|offered)|"
                      r"we\s+(can\s+|will\s+)?sponsor|sponsorship\s+(is\s+)?available", re.I)
_LEVELS = [(re.compile(r"bachelor|undergraduate|licence\b|grado\b|bsc\b|ba\b"), "bachelor"),
           (re.compile(r"master|msc\b|graduate\s+student|maestr[ií]a"), "master"),
           (re.compile(r"\bph\.?d|doctora"), "phd")]
_STUDENT_CUE = re.compile(r"student|studier|studium|etudiant|estudiante|degree|diplom|enrolled|pursuing|candidate",
                          re.I)
_GRAD = re.compile(r"(graduat\w*|abschluss|diplomation|egresad\w*|class\s+of)\D{0,40}?(20\d\d)"
                   r"(?:\s*(?:,|and|or|und|ou|o|-|–|/|to)\s*(20\d\d))?", re.I)
_PENULT = re.compile(r"penultimate\s+year|second[-\s]to[-\s]last\s+year|vorletzten", re.I)
_ENROLL = re.compile(r"(currently|must\s+be|still|be)\s+(enrolled|registered|matriculated)|immatrikuliert|"
                     r"eingeschrieben|inscrit|matriculad[oa]|student\s+status|statut\s+d.etudiant|"
                     r"studierendenstatus", re.I)
_MONTHS = re.compile(r"(\d{1,2})\s*(?:-|–|to|bis|a|à)\s*(\d{1,2})\s*(months?|monate?n?|mois|meses|mesi)|"
                     r"(\d{1,2})\s*(months?|monate?n?|mois|meses|mesi)", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;•\n])\s+|\s+[•·▪]\s+", text or "") if s.strip()]


def extract(text: str) -> list[tuple[str, dict, str]]:
    """(kind, value, evidence sentence) for every requirement the rules recognise."""
    out: list[tuple[str, dict, str]] = []
    seen: set[str] = set()

    def add(kind, value, sentence):
        key = kind + dumps(value)
        if key not in seen:
            seen.add(key)
            out.append((kind, value, sentence[:300]))

    for s in _sentences(text):
        f = fold(s)
        if _NO_SPONSOR.search(s):
            add("sponsorship", {"available": False}, s)
        elif _SPONSOR.search(s):
            add("sponsorship", {"available": True}, s)
        if _AUTH.search(s):
            regions: list[str] = []
            for rx, rs in _REGION_RES:
                if rx.search(f):
                    regions += [r for r in rs if r not in regions]
            if regions:
                add("work_authorization", {"regions": regions}, s)
        if _STUDENT_CUE.search(s):
            levels = [lvl for rx, lvl in _LEVELS if rx.search(f)]
            if levels:
                add("degree_level", {"levels": levels}, s)
        m = _GRAD.search(s)
        if m:
            years = sorted({int(y) for y in (m.group(2), m.group(3)) if y})
            add("graduation_year", {"years": years}, s)
        if _PENULT.search(s):
            add("graduation_year", {"penultimate": True}, s)
        if _ENROLL.search(s):
            add("enrollment", {"required": True}, s)
        m = _MONTHS.search(s)
        if m and re.search(r"intern|praktik|stage|duration|dauer|duree|duracion|period|length|for\s+\d", f):
            lo, hi = (int(m.group(1)), int(m.group(2))) if m.group(1) else (int(m.group(4)), int(m.group(4)))
            if 1 <= lo <= hi <= 24:
                add("duration_months", {"min": lo, "max": hi}, s)
    for code, sentence in required_languages(text):
        add("language", {"code": code}, sentence)
    return out


def store(conn: sqlite3.Connection, now: datetime, opp_id: str, reqs: list[tuple[str, dict, str]],
          method: str) -> int:
    n = 0
    for kind, value, evidence in reqs:
        cur = conn.execute("INSERT OR IGNORE INTO opportunity_requirements (opportunity_id, kind, value, method, "
                           "evidence, recorded_at) VALUES (?, ?, ?, ?, ?, ?)",
                           (opp_id, kind, dumps(value), method, evidence, iso(now)))
        n += cur.rowcount
    return n
