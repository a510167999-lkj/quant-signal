"""Independent official evidence for full-session A-share suspensions."""

from __future__ import annotations

import re
from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit

from pypdf import PdfReader


CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION = "cninfo-suspension-evidence/v1"
CNINFO_SUSPENSION_PARSER_VERSION_V1 = "cninfo-suspension-pdf/v1"
CNINFO_SUSPENSION_PARSER_VERSION = "cninfo-suspension-pdf/v2"
SUPPORTED_CNINFO_SUSPENSION_PARSER_VERSIONS = frozenset(
    {CNINFO_SUSPENSION_PARSER_VERSION_V1, CNINFO_SUSPENSION_PARSER_VERSION}
)
# Frozen source hash used by the v1 coverage contract.  Existing audited
# artifacts must continue to reproduce the verifier identity that signed them.
CNINFO_SUSPENSION_PARSER_V1_CODE_SHA256 = (
    "598d1c73b7b8e3cbf6c941b71e0a0aab64cb2188437421277e0694315c25be39"
)
CNINFO_SOURCE_PROFILE = "cninfo-official"
MAX_CNINFO_PDF_BYTES = 5 * 1024 * 1024
MAX_CNINFO_PDF_PAGES = 64
MAX_CNINFO_TEXT_CHARS = 2_000_000

_SOURCE_PATH_PATTERN = re.compile(
    r"^/finalpage/(?P<date>\d{4}-\d{2}-\d{2})/(?P<document>\d+)\.PDF$"
)
_TS_CODE_PATTERN = re.compile(r"^(?P<symbol>\d{6})\.(?:SH|SZ)$")
_DOCUMENT_SYMBOL_PATTERN = re.compile(r"证券代码\s*[:：]\s*(\d{6})")
_ANNOUNCEMENT_PATTERN = re.compile(
    r"公告编号\s*[:：]\s*(\d{4})\s*[-－—]\s*(\d{3})"
)
_DATE_PARTS = (
    r"(?P<year>\d{4})\s*年\s*(?P<month>\d{1,2})\s*月\s*"
    r"(?P<day>\d{1,2})\s*日"
)
_START_EFFECTIVE_PATTERN = re.compile(
    r"自\s*"
    + _DATE_PARTS
    + r"[^。；]{0,80}?开市(?:时)?\s*起\s*(?:开始)?\s*停牌"
)
_RESUME_EFFECTIVE_PATTERN = re.compile(
    r"(?:将)?于\s*" + _DATE_PARTS + r"[^。；]{0,80}?开市(?:时)?\s*起\s*复牌"
)
_SUSPENDED_LISTING_EFFECTIVE_PATTERN = re.compile(
    r"(?:将)?自\s*" + _DATE_PARTS + r"[^。；]{0,80}?起\s*暂停上市"
)
_RESTORED_LISTING_EFFECTIVE_PATTERN = re.compile(
    r"(?:将)?(?:自|于)\s*" + _DATE_PARTS + r"[^。；]{0,80}?起\s*恢复上市"
)
_DELISTING_PERIOD_EFFECTIVE_PATTERN = re.compile(
    r"(?:将)?于\s*" + _DATE_PARTS + r"[^。；]{0,80}?(?:起\s*)?进入退市整理期"
)
_SUSPENDED_LISTING_TITLE_PATTERN = re.compile(
    r"关于(?:公司)?股票暂停上市(?:的)?公告"
)
_RESTORED_LISTING_TITLE_PATTERN = re.compile(
    r"关于(?:公司)?股票恢复上市[^。；]{0,40}?公告"
)
_DELISTING_PERIOD_TITLE_PATTERN = re.compile(
    r"关于(?:公司)?股票进入退市整理期交易(?:的)?公告"
)
_ORDINARY_START_TITLE_PATTERN = re.compile(r"关于[^。；]{0,80}?停牌公告")
_ORDINARY_RESUME_TITLE_PATTERN = re.compile(
    r"关于[^。；]{0,100}?复牌[^。；]{0,40}?公告"
)
_COMPANY_NAME_END_PATTERN = re.compile(r"股\s*份\s*有\s*限\s*公\s*司\s*")


class SuspensionEvidenceError(ValueError):
    """Raised when an official suspension document cannot be proven."""


def _canonical_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SuspensionEvidenceError("published_at is not an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SuspensionEvidenceError("published_at must include a timezone")
    return parsed.isoformat()


def _canonical_source_url(source_url: Any, published_at: str) -> str:
    text = str(source_url or "").strip()
    parsed = urlsplit(text)
    match = _SOURCE_PATH_PATTERN.fullmatch(parsed.path)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "static.cninfo.com.cn"
        or parsed.netloc != "static.cninfo.com.cn"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or match is None
    ):
        raise SuspensionEvidenceError("CNINFO source URL is not pinned")
    publication_date = datetime.fromisoformat(published_at).date().isoformat()
    if match.group("date") != publication_date:
        raise SuspensionEvidenceError("source publication date does not match published_at")
    return text


def _effective_date(match: re.Match[str]) -> str:
    try:
        return date(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        ).isoformat()
    except ValueError as exc:
        raise SuspensionEvidenceError("notice effective date is invalid") from exc


def _unique_effective_match(
    pattern: re.Pattern[str], text: str
) -> re.Match[str] | None:
    matches = list(pattern.finditer(text))
    if not matches:
        return None
    if len({_effective_date(match) for match in matches}) != 1:
        raise SuspensionEvidenceError("official PDF effective date is ambiguous")
    return matches[0]


def _formal_title_text(text: str, announcement: re.Match[str]) -> str:
    company_end = _COMPANY_NAME_END_PATTERN.search(
        text, announcement.end(), min(len(text), announcement.end() + 200)
    )
    if company_end is None:
        return ""
    return text[company_end.end() : company_end.end() + 160].lstrip()


def _extract_pdf_text(raw_bytes: bytes) -> str:
    if not isinstance(raw_bytes, bytes) or not raw_bytes.startswith(b"%PDF-"):
        raise SuspensionEvidenceError("official evidence is not a PDF")
    if not raw_bytes or len(raw_bytes) > MAX_CNINFO_PDF_BYTES:
        raise SuspensionEvidenceError("official PDF size is outside the trusted bound")
    try:
        reader = PdfReader(BytesIO(raw_bytes), strict=False)
        if reader.is_encrypted:
            raise SuspensionEvidenceError("encrypted official PDF is not supported")
        if not reader.pages or len(reader.pages) > MAX_CNINFO_PDF_PAGES:
            raise SuspensionEvidenceError("official PDF page count is outside the trusted bound")
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except SuspensionEvidenceError:
        raise
    except Exception as exc:
        raise SuspensionEvidenceError("official PDF cannot be parsed") from exc
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > MAX_CNINFO_TEXT_CHARS:
        raise SuspensionEvidenceError("official PDF text is outside the trusted bound")
    return normalized


def parse_cninfo_suspension_pdf(
    raw_bytes: bytes,
    *,
    source_url: str,
    published_at: Any,
    expected_ts_code: str,
    notice_role: str,
    parser_version: str = CNINFO_SUSPENSION_PARSER_VERSION,
) -> dict[str, Any]:
    canonical_published_at = _canonical_timestamp(published_at)
    canonical_url = _canonical_source_url(source_url, canonical_published_at)
    expected = str(expected_ts_code or "").strip().upper()
    expected_match = _TS_CODE_PATTERN.fullmatch(expected)
    if expected_match is None:
        raise SuspensionEvidenceError("expected suspension symbol is invalid")
    role = str(notice_role or "").strip().lower()
    if role not in {"start", "resume"}:
        raise SuspensionEvidenceError("notice role must be start or resume")
    version = str(parser_version or "").strip()
    if version not in SUPPORTED_CNINFO_SUSPENSION_PARSER_VERSIONS:
        raise SuspensionEvidenceError("official PDF parser version is not supported")

    raw = bytes(raw_bytes)
    text = _extract_pdf_text(raw)
    document_symbols = set(_DOCUMENT_SYMBOL_PATTERN.findall(text))
    if document_symbols != {expected_match.group("symbol")}:
        raise SuspensionEvidenceError("official PDF symbol does not match expected symbol")
    announcement = _ANNOUNCEMENT_PATTERN.search(text)
    if announcement is None:
        raise SuspensionEvidenceError("official PDF announcement number is missing")

    if version == CNINFO_SUSPENSION_PARSER_VERSION_V1:
        if role == "start":
            if "停牌公告" not in text:
                raise SuspensionEvidenceError(
                    "official PDF is not a start suspension notice"
                )
            effective_match = _START_EFFECTIVE_PATTERN.search(text)
        else:
            if "复牌" not in text:
                raise SuspensionEvidenceError("official PDF is not a resume notice")
            effective_match = _RESUME_EFFECTIVE_PATTERN.search(text)
    else:
        formal_title = _formal_title_text(text, announcement)
        if role == "start" and _SUSPENDED_LISTING_TITLE_PATTERN.match(
            formal_title
        ):
            effective_match = _unique_effective_match(
                _SUSPENDED_LISTING_EFFECTIVE_PATTERN, text
            )
        elif role == "start" and _ORDINARY_START_TITLE_PATTERN.match(formal_title):
            effective_match = _unique_effective_match(
                _START_EFFECTIVE_PATTERN, text
            )
        elif role == "start":
            raise SuspensionEvidenceError(
                "official PDF is not a start suspension notice"
            )
        elif _RESTORED_LISTING_TITLE_PATTERN.match(formal_title):
            effective_match = _unique_effective_match(
                _RESTORED_LISTING_EFFECTIVE_PATTERN, text
            )
        elif _DELISTING_PERIOD_TITLE_PATTERN.match(formal_title):
            effective_match = _unique_effective_match(
                _DELISTING_PERIOD_EFFECTIVE_PATTERN, text
            )
        elif _ORDINARY_RESUME_TITLE_PATTERN.match(formal_title):
            effective_match = _unique_effective_match(
                _RESUME_EFFECTIVE_PATTERN, text
            )
        else:
            raise SuspensionEvidenceError("official PDF is not a resume notice")
    if effective_match is None:
        raise SuspensionEvidenceError("official PDF effective date is missing")

    return {
        "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
        "source_profile": CNINFO_SOURCE_PROFILE,
        "source_url": canonical_url,
        "published_at": canonical_published_at,
        "notice_role": role,
        "ts_code": expected,
        "announcement_number": f"{announcement.group(1)}-{announcement.group(2)}",
        "effective_date": _effective_date(effective_match),
        "raw_sha256": sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": version,
    }
