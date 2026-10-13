"""Auto-extract paper metadata (title, authors, journal, year, DOI) from a PDF.

Used by the manual-paper form so uploading a PDF pre-fills the fields;
the user can still edit everything before saving.
"""
from __future__ import annotations

import re

import fitz  # PyMuPDF

_JOURNAL_PATTERNS = [
    (r"transportation research part f", "Transportation Research Part F"),
    (r"transportation research part a", "Transportation Research Part A"),
    (r"transport policy", "Transport Policy"),
    (r"travel behaviour and society", "Travel Behaviour and Society"),
    (r"accident analysis and prevention", "Accident Analysis and Prevention"),
    (r"journal of safety research", "Journal of Safety Research"),
]

_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"')\]]+", re.IGNORECASE)
_YEAR_AVAIL = re.compile(r"available online[^\d]*(\d{1,2}\s+\w+\s+)?(20\d{2})", re.IGNORECASE)
_YEAR_COPY = re.compile(r"©\s*(20\d{2})")
_AFFIL_RE = re.compile(
    r"university|institute|school of|department|college|laboratory|ltd|inc\.|"
    r"\bchina\b|\bviet\s?nam\b|\bdanang\b|\bchangsha\b|\bshenzhen\b|"
    r"\d{5,}|\bP\.?\s?R\.?\s?China\b",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^(a\s?r\s?t\s?i\s?c\s?l\s?e|article info|keywords|a\s?b\s?s\s?t\s?r\s?a\s?c\s?t|"
    r"abstract|received|to appear in|please cite|corresponding author|\*?\s*corresponding)",
    re.IGNORECASE,
)


def _first_page_lines(pdf_path: str) -> list[str]:
    doc = fitz.open(pdf_path)
    try:
        text = doc[0].get_text() or ""
    finally:
        doc.close()
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _is_header(ln: str) -> bool:
    l = ln.lower()
    if l.startswith(("journal pre-proof", "research article", "review article",
                     "short communication", "accepted manuscript")):
        return True
    if l.startswith("available online"):
        return True
    if re.match(r"^[\d\s\-–—:;,.()x/]+$", ln) and len(ln) < 90:
        return True
    if "©" in ln and ("elsevier" in l or "rights" in l):
        return True  # copyright/ISSN line, any length
    if any(p in l for p in ("all rights are reserved", "text and data mining",
                            "similar technologies", "no part of this publication")):
        return True  # wrapped continuation of the copyright line
    if re.search(r"\(\d{4}\)", ln) and re.search(r"\d{3,}", ln) and len(ln) < 110:
        return True  # journal header: "Travel Behaviour and Society 35 (2024) 100730"
    if _DOI_RE.search(ln) and len(ln) < 90:
        return True
    if l.startswith(("pii:", "doi:", "reference:")):
        return True
    if "elsevier" in l and len(ln) < 100:
        return True
    return False


def _is_affiliation_line(ln: str) -> bool:
    """True for real affiliation lines (addresses carry digits/postcodes).

    Title continuations like 'in China' / 'in Vietnam' must NOT match.
    """
    if not re.search(r"\d", ln):
        # No digits: only a marker-led line like "a School of ..." counts.
        return bool(re.match(r"^[a-z]\s", ln) and _AFFIL_RE.search(ln))
    return bool(_AFFIL_RE.search(ln))


def _looks_like_authors(ln: str) -> bool:
    # Names with affiliation markers: "Amjad Pervez a, Duy Quy Nguyen-Phuoc b,c,d,*, Jaeyoung Jay Lee e"
    if _AFFIL_RE.search(ln) or _STOP_RE.match(ln):
        return False
    if not ("," in ln or " and " in ln.lower()):
        return False
    tokens = [t for t in re.split(r"[,\s]+", ln) if t and not t.isdigit()]
    words = [t for t in tokens if re.match(r"^[A-Z][a-zA-Z\-.]*$", t)]
    # Most tokens should be capitalized name-words (markers like "a"/"b,c" excluded above).
    return len(words) >= 2 and len(words) / max(len(tokens), 1) >= 0.5


def extract_metadata(pdf_path: str) -> dict:
    """Return {title, authors, journal, year, doi} best-effort from a PDF."""
    lines = _first_page_lines(pdf_path)
    full = "\n".join(lines)

    doi = ""
    m = _DOI_RE.search(full)
    if m:
        doi = m.group(0).rstrip(".,;")

    journal = ""
    low = full.lower()
    for pat, canon in _JOURNAL_PATTERNS:
        if re.search(pat, low):
            journal = canon
            break
    m = re.search(r"to appear in:\s*(.+)", full, re.IGNORECASE)
    if m:
        journal = m.group(1).strip().rstrip(",")

    year = ""
    # Prefer the journal header's volume year ("... 35 (2024) 100730") over
    # "Available online" (which can be the year before).
    m = re.search(r"\(\s*(20\d{2})\s*\)\s*\d{4,}", full)
    if not m:
        m = _YEAR_AVAIL.search(full)
        if m and m.lastindex == 2:
            year = m.group(2)
        else:
            m = _YEAR_COPY.search(full)
    if m and not year:
        year = m.group(1) if m.lastindex == 1 else m.group(2)

    # Walk past headers; title = following text block (may wrap lines);
    # authors = following author block (may also wrap lines, continuations
    # often start with ",").
    title_parts: list[str] = []
    i = 0
    while i < len(lines) and _is_header(lines[i]):
        i += 1
    # Title block: join lines until an author-like line, affiliation, or stop marker.
    while i < len(lines):
        ln = lines[i]
        if (_STOP_RE.match(ln) or _is_affiliation_line(ln)
                or _looks_like_authors(ln) or ln.startswith(",")
                or re.match(r"^([A-Z][\w\-.]*\s+)+[A-Z][\w\-.]*\s+[a-z\d,*]+$",
                            ln.strip())):
            break  # author block starts (incl. bare "Wei Hao a" — marker required)
        if len(ln) < 4:
            i += 1
            continue
        title_parts.append(ln)
        i += 1
        if len(title_parts) >= 4:
            break
    # Join wrapped lines: hyphen-wrap joins directly, else with space.
    title = ""
    for part in title_parts:
        if title and title.endswith("-"):
            title = title[:-1] + part
        elif title:
            title += " " + part
        else:
            title = part
    title = re.sub(r"\s+", " ", title).strip()

    # Authors block: accumulate this line + wrapped continuation lines.
    auth_frags: list[str] = []
    while i < len(lines):
        ln = lines[i].strip()
        if _STOP_RE.match(ln) or _is_affiliation_line(ln):
            break
        frag = ln.lstrip(", ").strip()
        if not frag:
            i += 1
            continue
        is_cont = ln.startswith(",") or _looks_like_authors(ln)
        # A bare name-with-marker also continues/starts ("Wei Hao a", "Jinjun Tang d").
        if not is_cont and re.match(
                r"^([A-Z][\w\-.]*\s+)+[A-Z][\w\-.]*(\s+[a-z\d,*]+)?$", frag):
            is_cont = True
        if not is_cont:
            break
        auth_frags.append(frag)
        i += 1
        if len(auth_frags) >= 6:
            break
    authors: list[str] = []
    if auth_frags:
        joined = ", ".join(f for f in auth_frags if f)
        # Drop affiliation markers: " a", " b,c,d,*", " a,1", trailing "*".
        joined = re.sub(r"\s+[a-z](?:,[a-z\d]+)*\*?", "", joined)
        joined = re.sub(r"\s*\*+", "", joined)
        joined = re.sub(r"\s+", " ", joined).strip(" ,")
        authors = [a.strip() for a in re.split(r",\s*|\s+and\s+", joined) if a.strip()]

    return {
        "title": title[:500],
        "authors": ", ".join(authors)[:500],
        "journal": journal[:200],
        "year": year,
        "doi": doi[:200],
    }
