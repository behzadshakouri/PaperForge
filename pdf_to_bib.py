#!/usr/bin/env python3
"""
pdf_to_bib.py

Recursively scan PDF files and create BibTeX entries for:
- journal articles
- conference papers
- books
- book chapters
- theses
- technical reports
- miscellaneous documents

Metadata priority:
1. DOI -> Crossref
2. arXiv ID -> arXiv Atom API
3. ISBN -> Open Library
4. Embedded PDF metadata + first-page text heuristics

Outputs:
- references.bib
- references_review.bib
- scan_report.csv
- unrecognized_pdfs.txt

Example:
    python pdf_to_bib.py "E:\\Behzad\\Research\\Papers" --output references.bib

Install:
    pip install pypdf requests

Optional OCR is intentionally not included because OCR often introduces errors.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

import bibtexparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import requests
from pypdf import PdfReader


USER_AGENT = (
    "PDF-to-BibTeX/1.0 "
    "(metadata extraction utility; contact: local-user@example.com)"
)

DOI_RE = re.compile(
    r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b",
    flags=re.IGNORECASE,
)

ARXIV_RE = re.compile(
    r"(?:arxiv\s*:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?",
    flags=re.IGNORECASE,
)

ISBN_RE = re.compile(
    r"\b(?:ISBN(?:-1[03])?\s*:?\s*)?"
    r"(?=[0-9Xx\-\s]{10,20}\b)"
    r"(97[89][0-9\-\s]{10,16}|[0-9][0-9Xx\-\s]{8,16})\b",
    flags=re.IGNORECASE,
)

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

ENTRY_TYPE_MAP = {
    "journal-article": "article",
    "proceedings-article": "inproceedings",
    "book": "book",
    "book-chapter": "incollection",
    "book-section": "incollection",
    "dissertation": "phdthesis",
    "report": "techreport",
    "posted-content": "misc",
    "dataset": "misc",
    "reference-entry": "misc",
    "monograph": "book",
    "edited-book": "book",
}


@dataclass
class Record:
    source_pdf: Path
    entry_type: str = "misc"
    title: str = ""
    authors: list[str] = field(default_factory=list)
    year: str = ""
    journal: str = ""
    booktitle: str = ""
    publisher: str = ""
    institution: str = ""
    school: str = ""
    volume: str = ""
    number: str = ""
    pages: str = ""
    doi: str = ""
    isbn: str = ""
    url: str = ""
    arxiv_id: str = ""
    abstract: str = ""
    note: str = ""
    confidence: str = "low"
    needs_review: bool = True
    metadata_source: str = "heuristic"
    raw: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        doi = normalize_doi(self.doi)
        if doi:
            return f"doi:{doi.lower()}"

        isbn = normalize_isbn(self.isbn)
        if isbn:
            return f"isbn:{isbn}"

        title_key = normalize_for_match(self.title)
        first_author = normalize_for_match(self.authors[0] if self.authors else "")
        year = self.year.strip()
        return f"meta:{title_key}|{first_author}|{year}"


def normalize_space(value: Any) -> str:
    if value is None:
        return ""
    text = html.unescape(str(value))
    text = text.replace("\x00", " ")
    return re.sub(r"\s+", " ", text).strip()


def normalize_for_match(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text


def normalize_doi(value: str) -> str:
    if not value:
        return ""
    value = urllib.parse.unquote(value)
    value = value.strip()
    value = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi\s*:\s*)", "", value, flags=re.I)
    value = value.rstrip(".,;:)]}>")
    match = DOI_RE.search(value)
    return match.group(0) if match else value


def normalize_isbn(value: str) -> str:
    return re.sub(r"[^0-9Xx]", "", value or "").upper()


def safe_get(seq: Any, index: int = 0, default: str = "") -> str:
    try:
        return normalize_space(seq[index])
    except (TypeError, IndexError, KeyError):
        return default


def request_json(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 25,
    retries: int = 3,
) -> Optional[dict[str, Any]]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, json.JSONDecodeError):
            if attempt == retries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def request_text(
    url: str,
    *,
    params: Optional[dict[str, Any]] = None,
    timeout: int = 25,
    retries: int = 3,
) -> Optional[str]:
    headers = {"User-Agent": USER_AGENT}
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=timeout)
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.text
        except requests.RequestException:
            if attempt == retries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def extract_pdf_data(pdf_path: Path, max_pages: int = 3) -> tuple[dict[str, str], str, Optional[str]]:
    metadata: dict[str, str] = {}
    text_parts: list[str] = []

    try:
        reader = PdfReader(str(pdf_path))
        raw_meta = reader.metadata or {}

        for key, value in raw_meta.items():
            clean_key = str(key).lstrip("/")
            metadata[clean_key] = normalize_space(value)

        for page in reader.pages[:max_pages]:
            try:
                extracted = page.extract_text() or ""
                text_parts.append(extracted)
            except Exception:
                continue

        return metadata, "\n".join(text_parts), None
    except Exception as exc:
        return metadata, "", f"{type(exc).__name__}: {exc}"


def find_doi(metadata: dict[str, str], text: str) -> str:
    candidates = [
        metadata.get("doi", ""),
        metadata.get("DOI", ""),
        metadata.get("Subject", ""),
        metadata.get("Keywords", ""),
        text,
    ]

    for candidate in candidates:
        if not candidate:
            continue
        match = DOI_RE.search(candidate)
        if match:
            return normalize_doi(match.group(0))
    return ""


def find_arxiv(text: str, metadata: dict[str, str]) -> str:
    combined = "\n".join([metadata.get("Subject", ""), metadata.get("Title", ""), text])
    match = ARXIV_RE.search(combined)
    return match.group(1) if match else ""


def isbn10_checksum(isbn: str) -> bool:
    if len(isbn) != 10:
        return False
    total = 0
    for i, char in enumerate(isbn):
        if char == "X":
            value = 10
        elif char.isdigit():
            value = int(char)
        else:
            return False
        total += (10 - i) * value
    return total % 11 == 0


def isbn13_checksum(isbn: str) -> bool:
    if len(isbn) != 13 or not isbn.isdigit():
        return False
    total = sum((1 if i % 2 == 0 else 3) * int(char) for i, char in enumerate(isbn[:12]))
    check = (10 - total % 10) % 10
    return check == int(isbn[12])


def is_valid_isbn(isbn: str) -> bool:
    normalized = normalize_isbn(isbn)
    return isbn10_checksum(normalized) or isbn13_checksum(normalized)


def find_isbn(text: str, metadata: dict[str, str]) -> str:
    combined = "\n".join(
        [
            metadata.get("Subject", ""),
            metadata.get("Keywords", ""),
            metadata.get("Title", ""),
            text,
        ]
    )

    for match in ISBN_RE.finditer(combined):
        candidate = normalize_isbn(match.group(1))
        if is_valid_isbn(candidate):
            return candidate
    return ""


def crossref_record(doi: str, pdf_path: Path) -> Optional[Record]:
    data = request_json(f"https://api.crossref.org/works/{urllib.parse.quote(doi)}")
    if not data or "message" not in data:
        return None

    item = data["message"]
    record = Record(source_pdf=pdf_path)
    record.raw = item
    record.metadata_source = "Crossref"
    record.confidence = "high"
    record.needs_review = False

    crossref_type = normalize_space(item.get("type", ""))
    record.entry_type = ENTRY_TYPE_MAP.get(crossref_type, "misc")
    record.title = safe_get(item.get("title", []))
    record.doi = normalize_doi(item.get("DOI", doi))
    record.url = normalize_space(item.get("URL", ""))

    authors = []
    for author in item.get("author", []) or []:
        family = normalize_space(author.get("family", ""))
        given = normalize_space(author.get("given", ""))
        name = ", ".join(part for part in [family, given] if part)
        if not name:
            name = normalize_space(author.get("name", ""))
        if name:
            authors.append(name)
    record.authors = authors

    date_parts = (
        item.get("published-print", {}).get("date-parts")
        or item.get("published-online", {}).get("date-parts")
        or item.get("issued", {}).get("date-parts")
        or []
    )
    if date_parts and date_parts[0]:
        record.year = str(date_parts[0][0])

    container = safe_get(item.get("container-title", []))
    record.journal = container if record.entry_type == "article" else ""
    record.booktitle = container if record.entry_type in {"inproceedings", "incollection"} else ""
    record.publisher = normalize_space(item.get("publisher", ""))
    record.volume = normalize_space(item.get("volume", ""))
    record.number = normalize_space(item.get("issue", ""))
    record.pages = normalize_space(item.get("page", ""))
    record.isbn = safe_get(item.get("ISBN", []))
    record.abstract = strip_jats(item.get("abstract", ""))

    if not record.title or not record.authors or not record.year:
        record.needs_review = True
        record.confidence = "medium"

    return record


def arxiv_record(arxiv_id: str, pdf_path: Path) -> Optional[Record]:
    xml_text = request_text(
        "https://export.arxiv.org/api/query",
        params={"id_list": arxiv_id},
    )
    if not xml_text:
        return None

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None

    ns = {"atom": "http://www.w3.org/2005/Atom"}
    entry = root.find("atom:entry", ns)
    if entry is None:
        return None

    title = normalize_space(entry.findtext("atom:title", default="", namespaces=ns))
    published = normalize_space(entry.findtext("atom:published", default="", namespaces=ns))
    summary = normalize_space(entry.findtext("atom:summary", default="", namespaces=ns))

    authors = []
    for author_node in entry.findall("atom:author", ns):
        name = normalize_space(author_node.findtext("atom:name", default="", namespaces=ns))
        if name:
            parts = name.split()
            if len(parts) > 1:
                name = f"{parts[-1]}, {' '.join(parts[:-1])}"
            authors.append(name)

    record = Record(source_pdf=pdf_path)
    record.entry_type = "misc"
    record.title = title
    record.authors = authors
    record.year = published[:4] if published else ""
    record.arxiv_id = arxiv_id
    record.url = f"https://arxiv.org/abs/{arxiv_id}"
    record.abstract = summary
    record.note = f"arXiv preprint arXiv:{arxiv_id}"
    record.metadata_source = "arXiv"
    record.confidence = "high"
    record.needs_review = not bool(title and authors and record.year)
    return record


def openlibrary_record(isbn: str, pdf_path: Path) -> Optional[Record]:
    normalized = normalize_isbn(isbn)
    data = request_json(
        "https://openlibrary.org/api/books",
        params={
            "bibkeys": f"ISBN:{normalized}",
            "format": "json",
            "jscmd": "data",
        },
    )
    if not data:
        return None

    item = data.get(f"ISBN:{normalized}")
    if not item:
        return None

    record = Record(source_pdf=pdf_path)
    record.entry_type = "book"
    record.title = normalize_space(item.get("title", ""))
    record.authors = [
        normalize_space(author.get("name", ""))
        for author in item.get("authors", []) or []
        if normalize_space(author.get("name", ""))
    ]
    record.publisher = safe_get([p.get("name", "") for p in item.get("publishers", []) or []])
    publish_date = normalize_space(item.get("publish_date", ""))
    year_match = YEAR_RE.search(publish_date)
    record.year = year_match.group(0) if year_match else ""
    record.isbn = normalized
    record.url = normalize_space(item.get("url", ""))
    record.metadata_source = "Open Library"
    record.confidence = "high"
    record.needs_review = not bool(record.title and record.authors and record.year)
    return record


def strip_jats(value: Any) -> str:
    text = normalize_space(value)
    text = re.sub(r"<[^>]+>", " ", text)
    return normalize_space(text)


def split_author_string(value: str) -> list[str]:
    value = normalize_space(value)
    if not value:
        return []

    value = re.sub(r"\s+(?:and|&)\s+", ";", value, flags=re.I)
    pieces = [normalize_space(p) for p in re.split(r";|\|", value) if normalize_space(p)]

    authors = []
    for piece in pieces:
        if "," in piece:
            authors.append(piece)
        else:
            words = piece.split()
            if len(words) >= 2:
                authors.append(f"{words[-1]}, {' '.join(words[:-1])}")
            else:
                authors.append(piece)
    return authors


def infer_title(metadata: dict[str, str], text: str, pdf_path: Path) -> str:
    metadata_title = normalize_space(metadata.get("Title", ""))
    if metadata_title and metadata_title.lower() not in {"untitled", "microsoft word"}:
        return metadata_title

    lines = [normalize_space(line) for line in text.splitlines()]
    lines = [
        line
        for line in lines[:80]
        if 5 <= len(line) <= 300
        and not DOI_RE.search(line)
        and not re.fullmatch(r"\d+", line)
    ]

    likely = []
    for line in lines:
        lower = line.lower()
        if any(
            phrase in lower
            for phrase in [
                "abstract",
                "introduction",
                "keywords",
                "copyright",
                "downloaded from",
                "available online",
                "received:",
                "accepted:",
            ]
        ):
            continue
        words = line.split()
        if 4 <= len(words) <= 30:
            likely.append(line)

    if likely:
        return max(likely[:12], key=len)

    return pdf_path.stem.replace("_", " ").replace("-", " ")


def infer_authors(metadata: dict[str, str], text: str, title: str) -> list[str]:
    embedded = normalize_space(metadata.get("Author", ""))
    if embedded and embedded.lower() not in {"anonymous", "unknown"}:
        return split_author_string(embedded)

    lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
    title_index = 0
    title_key = normalize_for_match(title)
    for idx, line in enumerate(lines[:100]):
        if title_key and normalize_for_match(line) == title_key:
            title_index = idx
            break

    candidate_lines = lines[title_index + 1 : title_index + 8]
    for line in candidate_lines:
        lower = line.lower()
        if any(token in lower for token in ["university", "department", "institute", "abstract", "@"]):
            continue
        if YEAR_RE.search(line):
            continue
        if 1 < len(line.split()) < 25 and re.search(r"[A-Za-z]", line):
            if "," in line or re.search(r"\band\b|&", line, flags=re.I):
                authors = split_author_string(line)
                if authors:
                    return authors

    return []


def infer_year(metadata: dict[str, str], text: str) -> str:
    for key in ("CreationDate", "ModDate", "Subject"):
        value = metadata.get(key, "")
        match = YEAR_RE.search(value)
        if match:
            return match.group(0)

    years = YEAR_RE.findall(text[:8000])
    # YEAR_RE.findall returns only the captured century due to the group,
    # so use finditer instead.
    candidates = [m.group(0) for m in YEAR_RE.finditer(text[:8000])]
    if candidates:
        plausible = [y for y in candidates if 1900 <= int(y) <= 2100]
        if plausible:
            return plausible[0]
    return ""


def infer_entry_type(text: str, isbn: str, arxiv_id: str) -> str:
    lower = text[:15000].lower()

    if isbn:
        return "book"
    if arxiv_id:
        return "misc"
    if "doctoral dissertation" in lower or "ph.d. dissertation" in lower or "phd thesis" in lower:
        return "phdthesis"
    if "master's thesis" in lower or "master thesis" in lower or "m.sc. thesis" in lower:
        return "mastersthesis"
    if "technical report" in lower or re.search(r"\breport\s+no\.", lower):
        return "techreport"
    if "proceedings of" in lower or "conference" in lower:
        return "inproceedings"
    if "journal" in lower or re.search(r"\bvol(?:ume)?\.?\s*\d+", lower):
        return "article"
    return "misc"


def heuristic_record(
    pdf_path: Path,
    metadata: dict[str, str],
    text: str,
    doi: str = "",
    isbn: str = "",
    arxiv_id: str = "",
) -> Record:
    record = Record(source_pdf=pdf_path)
    record.title = infer_title(metadata, text, pdf_path)
    record.authors = infer_authors(metadata, text, record.title)
    record.year = infer_year(metadata, text)
    record.doi = doi
    record.isbn = isbn
    record.arxiv_id = arxiv_id
    record.entry_type = infer_entry_type(text, isbn, arxiv_id)
    record.metadata_source = "PDF metadata/text heuristic"
    record.confidence = "low"
    record.needs_review = True

    subject = normalize_space(metadata.get("Subject", ""))
    if subject:
        record.note = f"PDF subject: {subject}"

    return record


def merge_missing(primary: Record, fallback: Record) -> Record:
    scalar_fields = [
        "title",
        "year",
        "journal",
        "booktitle",
        "publisher",
        "institution",
        "school",
        "volume",
        "number",
        "pages",
        "doi",
        "isbn",
        "url",
        "arxiv_id",
        "abstract",
        "note",
    ]

    for field_name in scalar_fields:
        if not getattr(primary, field_name):
            setattr(primary, field_name, getattr(fallback, field_name))

    if not primary.authors:
        primary.authors = fallback.authors

    if primary.entry_type == "misc" and fallback.entry_type != "misc":
        primary.entry_type = fallback.entry_type

    return primary


def sanitize_bibtex(value: str) -> str:
    value = normalize_space(value)
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
    }
    for old, new in replacements.items():
        value = value.replace(old, new)

    value = value.replace("{", r"\{").replace("}", r"\}")
    return value


def make_citation_key(record: Record, used_keys: set[str]) -> str:
    if record.authors:
        family = record.authors[0].split(",", 1)[0]
    else:
        family = "Unknown"

    family = re.sub(r"[^A-Za-z0-9]", "", family) or "Unknown"
    year = record.year or "n.d."

    title_words = re.findall(r"[A-Za-z0-9]+", record.title)
    ignored = {
        "a",
        "an",
        "the",
        "of",
        "on",
        "in",
        "for",
        "and",
        "to",
        "with",
        "using",
        "based",
        "from",
    }
    keyword = next(
        (word for word in title_words if word.lower() not in ignored and len(word) > 2),
        "Work",
    )
    keyword = keyword[:24]

    base = f"{family}{year}{keyword}"
    key = base
    suffix = ord("a")

    while key.lower() in {k.lower() for k in used_keys}:
        key = f"{base}{chr(suffix)}"
        suffix += 1

    used_keys.add(key)
    return key


def bibtex_entry(record: Record, citation_key: str) -> str:
    entry_type = record.entry_type or "misc"
    fields: list[tuple[str, str]] = []

    if record.authors:
        fields.append(("author", " and ".join(record.authors)))
    if record.title:
        fields.append(("title", record.title))
    if record.year:
        fields.append(("year", record.year))

    if entry_type == "article":
        if record.journal:
            fields.append(("journal", record.journal))
        if record.volume:
            fields.append(("volume", record.volume))
        if record.number:
            fields.append(("number", record.number))
        if record.pages:
            fields.append(("pages", record.pages))

    elif entry_type == "inproceedings":
        if record.booktitle:
            fields.append(("booktitle", record.booktitle))
        if record.publisher:
            fields.append(("publisher", record.publisher))
        if record.pages:
            fields.append(("pages", record.pages))

    elif entry_type == "incollection":
        if record.booktitle:
            fields.append(("booktitle", record.booktitle))
        if record.publisher:
            fields.append(("publisher", record.publisher))
        if record.pages:
            fields.append(("pages", record.pages))

    elif entry_type == "book":
        if record.publisher:
            fields.append(("publisher", record.publisher))
        if record.isbn:
            fields.append(("isbn", normalize_isbn(record.isbn)))

    elif entry_type in {"phdthesis", "mastersthesis"}:
        if record.school:
            fields.append(("school", record.school))
        elif record.institution:
            fields.append(("school", record.institution))

    elif entry_type == "techreport":
        if record.institution:
            fields.append(("institution", record.institution))
        if record.number:
            fields.append(("number", record.number))

    if record.doi:
        fields.append(("doi", normalize_doi(record.doi)))
    if record.url:
        fields.append(("url", record.url))
    if record.arxiv_id:
        fields.append(("eprint", record.arxiv_id))
        fields.append(("archivePrefix", "arXiv"))
    if record.note:
        fields.append(("note", record.note))

    fields.append(("file", str(record.source_pdf.resolve())))

    lines = [f"@{entry_type}{{{citation_key},"]
    for name, value in fields:
        if value:
            lines.append(f"  {name} = {{{sanitize_bibtex(value)}}},")
    lines.append("}")
    return "\n".join(lines)


def choose_better_record(a: Record, b: Record) -> Record:
    rank = {"high": 3, "medium": 2, "low": 1}
    score_a = rank.get(a.confidence, 0) + int(bool(a.doi)) + int(bool(a.isbn))
    score_b = rank.get(b.confidence, 0) + int(bool(b.doi)) + int(bool(b.isbn))
    return a if score_a >= score_b else b


def scan_pdf(pdf_path: Path, max_pages: int, offline: bool) -> tuple[Optional[Record], str]:
    metadata, text, error = extract_pdf_data(pdf_path, max_pages=max_pages)
    if error:
        return None, error

    doi = find_doi(metadata, text)
    arxiv_id = find_arxiv(text, metadata)
    isbn = find_isbn(text, metadata)

    fallback = heuristic_record(
        pdf_path,
        metadata,
        text,
        doi=doi,
        isbn=isbn,
        arxiv_id=arxiv_id,
    )

    if not offline and doi:
        record = crossref_record(doi, pdf_path)
        if record:
            return merge_missing(record, fallback), ""

    if not offline and arxiv_id:
        record = arxiv_record(arxiv_id, pdf_path)
        if record:
            return merge_missing(record, fallback), ""

    if not offline and isbn:
        record = openlibrary_record(isbn, pdf_path)
        if record:
            return merge_missing(record, fallback), ""

    return fallback, ""


def iter_pdfs(root: Path) -> Iterable[Path]:
    yield from sorted(
        (path for path in root.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf"),
        key=lambda p: str(p).lower(),
    )


def write_report(report_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "status",
        "pdf",
        "entry_type",
        "citation_key",
        "title",
        "authors",
        "year",
        "doi",
        "isbn",
        "metadata_source",
        "confidence",
        "needs_review",
        "message",
    ]

    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)



def parse_bibtex_file(path: Path) -> list[dict[str, str]]:
    """Read BibTeX records using bibtexparser."""
    with path.open("r", encoding="utf-8-sig") as handle:
        database = bibtexparser.load(handle)
    return database.entries


def clean_latex_text(value: str) -> str:
    """Convert common BibTeX/LaTeX markup to readable plain text."""
    if not value:
        return ""

    text = value
    replacements = {
        r"\&": "&",
        r"\%": "%",
        r"\_": "_",
        r"\#": "#",
        r"\$": "$",
        r"\textbackslash{}": "\\",
        r"~": " ",
        r"\textendash": "–",
        r"\textemdash": "—",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"\\(?:textit|emph|textbf|mathrm|textrm|mathbf)\s*\{([^{}]*)\}", r"\1", text)
    text = re.sub(r"\\[A-Za-z]+\s*", "", text)
    text = text.replace("{", "").replace("}", "")
    return normalize_space(text)


def split_bibtex_authors(author_field: str) -> list[str]:
    """Split a BibTeX author field while preserving corporate authors."""
    if not author_field:
        return []
    return [clean_latex_text(part) for part in re.split(r"\s+and\s+", author_field) if clean_latex_text(part)]


def apa_person_name(name: str) -> str:
    """
    Convert common BibTeX person formats to APA-style family name and initials.

    Examples:
        Shakouri, Behzad -> Shakouri, B.
        Behzad Shakouri -> Shakouri, B.
    """
    name = clean_latex_text(name)
    if not name:
        return ""

    # Corporate/group author enclosed in braces in source BibTeX.
    if len(name.split()) > 5 and "," not in name:
        return name

    if "," in name:
        family, given = [normalize_space(part) for part in name.split(",", 1)]
    else:
        parts = name.split()
        if len(parts) == 1:
            return parts[0]
        family = parts[-1]
        given = " ".join(parts[:-1])

    initials = []
    for token in re.split(r"[\s\-]+", given):
        token = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ]", "", token)
        if token:
            initials.append(f"{token[0].upper()}.")

    if initials:
        return f"{family}, {' '.join(initials)}"
    return family


def apa_authors(author_field: str) -> str:
    authors = [apa_person_name(name) for name in split_bibtex_authors(author_field)]
    authors = [name for name in authors if name]

    if not authors:
        return ""
    if len(authors) == 1:
        return authors[0]
    if len(authors) == 2:
        return f"{authors[0]}, & {authors[1]}"
    if len(authors) <= 20:
        return ", ".join(authors[:-1]) + f", & {authors[-1]}"

    # APA 7: list first 19, ellipsis, then final author.
    return ", ".join(authors[:19]) + f", … {authors[-1]}"


def sentence_case_title(title: str) -> str:
    """
    Apply conservative sentence case without aggressively lowercasing acronyms.
    BibTeX-protected capitalization is preserved only approximately after braces
    are removed, so words written fully in capitals remain unchanged.
    """
    title = clean_latex_text(title)
    if not title:
        return ""

    words = title.split()
    result = []
    capitalize_next = True

    for word in words:
        if word.isupper() and len(word) > 1:
            result.append(word)
        elif capitalize_next:
            result.append(word[:1].upper() + word[1:])
            capitalize_next = False
        else:
            result.append(word)

        if word.endswith((':', '?', '!')):
            capitalize_next = True

    return " ".join(result)


def ensure_terminal_period(text: str) -> str:
    text = normalize_space(text)
    if not text:
        return ""
    return text if text[-1] in ".?!" else text + "."


def apa_reference(entry: dict[str, str], include_doi_url: bool = True) -> str:
    """Create an APA 7-like plain-text reference for common BibTeX types."""
    entry_type = entry.get("ENTRYTYPE", "misc").lower()
    author_text = apa_authors(entry.get("author", ""))
    year = clean_latex_text(entry.get("year", "")) or "n.d."
    title = sentence_case_title(entry.get("title", ""))

    lead = f"{author_text} ({year})." if author_text else f"({year})."
    title_part = ensure_terminal_period(title)

    doi = normalize_doi(clean_latex_text(entry.get("doi", "")))
    url = clean_latex_text(entry.get("url", ""))
    doi_url = f"https://doi.org/{doi}" if doi else ""
    final_url = doi_url if doi_url and include_doi_url else url

    if entry_type == "article":
        journal = clean_latex_text(entry.get("journal", ""))
        volume = clean_latex_text(entry.get("volume", ""))
        number = clean_latex_text(entry.get("number", entry.get("issue", "")))
        pages = clean_latex_text(entry.get("pages", "")).replace("--", "–")

        source = journal
        if volume:
            source += f", {volume}"
        if number:
            source += f"({number})"
        if pages:
            source += f", {pages}"
        source = ensure_terminal_period(source)

        parts = [lead, title_part, source]

    elif entry_type in {"inproceedings", "conference"}:
        booktitle = clean_latex_text(entry.get("booktitle", ""))
        pages = clean_latex_text(entry.get("pages", "")).replace("--", "–")
        publisher = clean_latex_text(entry.get("publisher", ""))

        source = f"In {booktitle}" if booktitle else ""
        if pages:
            source += f" (pp. {pages})"
        source = ensure_terminal_period(source)
        publisher = ensure_terminal_period(publisher)

        parts = [lead, title_part, source, publisher]

    elif entry_type in {"incollection", "inbook"}:
        booktitle = sentence_case_title(entry.get("booktitle", ""))
        editor = apa_authors(entry.get("editor", ""))
        pages = clean_latex_text(entry.get("pages", "")).replace("--", "–")
        publisher = clean_latex_text(entry.get("publisher", ""))

        source = "In "
        if editor:
            source += f"{editor} (Ed.), "
        source += booktitle
        if pages:
            source += f" (pp. {pages})"
        source = ensure_terminal_period(source)
        publisher = ensure_terminal_period(publisher)

        parts = [lead, title_part, source, publisher]

    elif entry_type == "book":
        edition = clean_latex_text(entry.get("edition", ""))
        publisher = clean_latex_text(entry.get("publisher", ""))

        if edition and title_part:
            title_part = title_part[:-1] + f" ({edition} ed.)."

        parts = [lead, title_part, ensure_terminal_period(publisher)]

    elif entry_type in {"phdthesis", "mastersthesis"}:
        school = clean_latex_text(entry.get("school", entry.get("institution", "")))
        thesis_label = "Doctoral dissertation" if entry_type == "phdthesis" else "Master's thesis"
        descriptor = f"[{thesis_label}, {school}]" if school else f"[{thesis_label}]"
        parts = [lead, title_part, ensure_terminal_period(descriptor)]

    elif entry_type == "techreport":
        institution = clean_latex_text(entry.get("institution", ""))
        number = clean_latex_text(entry.get("number", ""))
        descriptor = f"Technical report"
        if number:
            descriptor += f" No. {number}"
        parts = [lead, title_part, ensure_terminal_period(descriptor), ensure_terminal_period(institution)]

    else:
        howpublished = clean_latex_text(entry.get("howpublished", ""))
        note = clean_latex_text(entry.get("note", ""))
        parts = [lead, title_part, ensure_terminal_period(howpublished), ensure_terminal_period(note)]

    reference = " ".join(part for part in parts if normalize_space(part))
    if final_url:
        reference += f" {final_url}"
    return normalize_space(reference)


def export_apa_references(
    bib_path: Path,
    output_path: Path,
    *,
    numbered: bool = False,
    sort_mode: str = "author",
    include_doi_url: bool = True,
) -> int:
    entries = parse_bibtex_file(bib_path)

    if sort_mode == "author":
        entries.sort(
            key=lambda entry: (
                normalize_for_match(entry.get("author", "")),
                entry.get("year", ""),
                normalize_for_match(entry.get("title", "")),
            )
        )
    elif sort_mode == "year":
        entries.sort(
            key=lambda entry: (
                entry.get("year", ""),
                normalize_for_match(entry.get("author", "")),
            )
        )
    elif sort_mode == "title":
        entries.sort(key=lambda entry: normalize_for_match(entry.get("title", "")))

    references = []
    for index, entry in enumerate(entries, start=1):
        text = apa_reference(entry, include_doi_url=include_doi_url)
        if numbered:
            text = f"{index}. {text}"
        references.append(text)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n\n".join(references) + ("\n" if references else ""), encoding="utf-8")
    return len(references)



def main() -> int:
    parser = argparse.ArgumentParser(
        description="Recursively scan PDFs and create BibTeX files."
    )
    parser.add_argument("folder", nargs="?", type=Path, help="Folder containing PDF files.")
    parser.add_argument(
        "--bib-input",
        type=Path,
        default=None,
        help="Read an existing .bib file instead of scanning PDFs.",
    )
    parser.add_argument(
        "--apa-output",
        type=Path,
        default=None,
        help="Export APA-style references to a plain-text file.",
    )
    parser.add_argument(
        "--apa-numbered",
        action="store_true",
        help="Number APA references.",
    )
    parser.add_argument(
        "--apa-sort",
        choices=["author", "year", "title", "none"],
        default="author",
        help="Sort order for APA references.",
    )
    parser.add_argument(
        "--no-doi-url",
        action="store_true",
        help="Do not append DOI URLs to APA references.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("references.bib"),
        help="Main BibTeX output file.",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=None,
        help="BibTeX file for uncertain records.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="CSV scan report.",
    )
    parser.add_argument(
        "--unrecognized",
        type=Path,
        default=None,
        help="Text file listing unreadable/unrecognized PDFs.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="Maximum number of PDF pages used for text extraction.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Do not query Crossref, arXiv, or Open Library.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.15,
        help="Delay between PDFs to avoid excessive API requests.",
    )

    args = parser.parse_args()

    if args.bib_input:
        bib_path = args.bib_input.expanduser().resolve()
        if not bib_path.exists():
            print(f"ERROR: BibTeX file not found: {bib_path}", file=sys.stderr)
            return 2

        apa_path = (
            args.apa_output.expanduser().resolve()
            if args.apa_output
            else bib_path.with_name(f"{bib_path.stem}_APA.txt")
        )

        count = export_apa_references(
            bib_path,
            apa_path,
            numbered=args.apa_numbered,
            sort_mode=args.apa_sort,
            include_doi_url=not args.no_doi_url,
        )
        print(f"Exported {count} APA-style reference(s): {apa_path}")
        return 0

    if args.folder is None:
        parser.error("Provide a PDF folder or use --bib-input FILE.bib")

    root = args.folder.expanduser().resolve()

    if not root.exists() or not root.is_dir():
        print(f"ERROR: Folder not found: {root}", file=sys.stderr)
        return 2

    output_path = args.output.expanduser().resolve()
    review_path = (
        args.review_output.expanduser().resolve()
        if args.review_output
        else output_path.with_name(f"{output_path.stem}_review.bib")
    )
    report_path = (
        args.report.expanduser().resolve()
        if args.report
        else output_path.with_name("scan_report.csv")
    )
    unrecognized_path = (
        args.unrecognized.expanduser().resolve()
        if args.unrecognized
        else output_path.with_name("unrecognized_pdfs.txt")
    )

    for path in [output_path, review_path, report_path, unrecognized_path]:
        path.parent.mkdir(parents=True, exist_ok=True)

    pdfs = list(iter_pdfs(root))
    if not pdfs:
        print(f"No PDF files found under: {root}")
        return 0

    records_by_fingerprint: dict[str, Record] = {}
    duplicate_paths: dict[str, list[Path]] = {}
    failures: list[tuple[Path, str]] = []
    report_rows: list[dict[str, str]] = []

    print(f"Found {len(pdfs)} PDF file(s).")

    for index, pdf_path in enumerate(pdfs, start=1):
        print(f"[{index}/{len(pdfs)}] {pdf_path}")

        record, error = scan_pdf(
            pdf_path,
            max_pages=max(1, args.max_pages),
            offline=args.offline,
        )

        if error or record is None:
            failures.append((pdf_path, error or "Unknown extraction error"))
            report_rows.append(
                {
                    "status": "failed",
                    "pdf": str(pdf_path),
                    "entry_type": "",
                    "citation_key": "",
                    "title": "",
                    "authors": "",
                    "year": "",
                    "doi": "",
                    "isbn": "",
                    "metadata_source": "",
                    "confidence": "",
                    "needs_review": "yes",
                    "message": error or "Unknown extraction error",
                }
            )
            continue

        fingerprint = record.fingerprint()
        if fingerprint in records_by_fingerprint:
            existing = records_by_fingerprint[fingerprint]
            winner = choose_better_record(existing, record)
            records_by_fingerprint[fingerprint] = winner
            duplicate_paths.setdefault(fingerprint, [existing.source_pdf]).append(pdf_path)

            report_rows.append(
                {
                    "status": "duplicate",
                    "pdf": str(pdf_path),
                    "entry_type": record.entry_type,
                    "citation_key": "",
                    "title": record.title,
                    "authors": "; ".join(record.authors),
                    "year": record.year,
                    "doi": record.doi,
                    "isbn": record.isbn,
                    "metadata_source": record.metadata_source,
                    "confidence": record.confidence,
                    "needs_review": "yes" if record.needs_review else "no",
                    "message": f"Duplicate of {winner.source_pdf}",
                }
            )
        else:
            records_by_fingerprint[fingerprint] = record

        if not args.offline:
            time.sleep(max(0.0, args.delay))

    used_keys: set[str] = set()
    accepted_entries: list[str] = []
    review_entries: list[str] = []

    for fingerprint, record in sorted(
        records_by_fingerprint.items(),
        key=lambda item: (
            item[1].authors[0].lower() if item[1].authors else "",
            item[1].year,
            item[1].title.lower(),
        ),
    ):
        key = make_citation_key(record, used_keys)
        entry = bibtex_entry(record, key)

        if record.needs_review:
            review_entries.append(entry)
            status = "review"
        else:
            accepted_entries.append(entry)
            status = "accepted"

        duplicate_note = ""
        if fingerprint in duplicate_paths:
            duplicate_note = "Duplicates: " + " | ".join(
                str(path) for path in duplicate_paths[fingerprint]
            )

        report_rows.append(
            {
                "status": status,
                "pdf": str(record.source_pdf),
                "entry_type": record.entry_type,
                "citation_key": key,
                "title": record.title,
                "authors": "; ".join(record.authors),
                "year": record.year,
                "doi": record.doi,
                "isbn": record.isbn,
                "metadata_source": record.metadata_source,
                "confidence": record.confidence,
                "needs_review": "yes" if record.needs_review else "no",
                "message": duplicate_note,
            }
        )

    header = (
        "% Generated by pdf_to_bib.py\n"
        f"% Source folder: {root}\n"
        f"% Total PDFs scanned: {len(pdfs)}\n\n"
    )

    output_path.write_text(
        header + "\n\n".join(accepted_entries) + ("\n" if accepted_entries else ""),
        encoding="utf-8",
    )
    review_path.write_text(
        header + "\n\n".join(review_entries) + ("\n" if review_entries else ""),
        encoding="utf-8",
    )

    write_report(report_path, report_rows)

    with unrecognized_path.open("w", encoding="utf-8") as handle:
        for pdf_path, message in failures:
            handle.write(f"{pdf_path}\n  {message}\n\n")

    print()
    print("Finished.")
    print(f"Accepted records : {len(accepted_entries)}")
    print(f"Review records   : {len(review_entries)}")
    print(f"Failed PDFs      : {len(failures)}")
    print(f"Main BibTeX      : {output_path}")
    print(f"Review BibTeX    : {review_path}")
    print(f"CSV report       : {report_path}")
    print(f"Unrecognized     : {unrecognized_path}")

    if args.apa_output:
        combined_path = output_path.with_name(f"{output_path.stem}_all_for_apa.bib")
        combined_content = output_path.read_text(encoding="utf-8") + "\n" + review_path.read_text(encoding="utf-8")
        combined_path.write_text(combined_content, encoding="utf-8")
        apa_count = export_apa_references(
            combined_path,
            args.apa_output.expanduser().resolve(),
            numbered=args.apa_numbered,
            sort_mode=args.apa_sort,
            include_doi_url=not args.no_doi_url,
        )
        combined_path.unlink(missing_ok=True)
        print(f"APA references   : {args.apa_output.expanduser().resolve()} ({apa_count})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
