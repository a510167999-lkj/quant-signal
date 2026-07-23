"""Fail-closed evidence and replay support for security-code transitions."""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
from io import BytesIO
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

import pandas as pd


SECURITY_CODE_TRANSITION_EVIDENCE_SCHEMA_VERSION = (
    "cninfo-security-code-transition-evidence/v1"
)
SECURITY_CODE_TRANSITION_PARSER_VERSION = (
    "cninfo-security-code-transition-pdf/v1"
)
SECURITY_CODE_TRANSITION_CONTRACT_SCHEMA_VERSION = (
    "security-code-transition-contract/v1"
)
SECURITY_CODE_TRANSITION_APPLICATION_SCHEMA_VERSION = (
    "security-code-transition-application/v1"
)
MAX_CNINFO_PDF_BYTES = 5 * 1024 * 1024
MAX_CNINFO_PDF_PAGES = 64
MAX_CNINFO_TEXT_CHARS = 2_000_000

_TS_CODE_PATTERN = re.compile(
    r"^(?P<symbol>\d{6})\.(?P<exchange>SH|SZ|BJ)$"
)
_SOURCE_PATH_PATTERN = re.compile(
    r"^/finalpage/(?P<date>\d{4}-\d{2}-\d{2})/(?P<document>\d+)\.PDF$"
)
_ANNOUNCEMENT_PATTERN = re.compile(
    r"公告编号\s*[:：]\s*(\d{4})\s*[-－—]\s*(\d{3})"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_ANNOUNCEMENT_NUMBER_PATTERN = re.compile(r"^\d{4}-\d{3}$")
_EVIDENCE_KEYS = (
    "schema_version",
    "source_profile",
    "source_url",
    "published_at",
    "publication_time_precision",
    "announcement_number",
    "predecessor_ts_code",
    "successor_ts_code",
    "effective_date",
    "share_quantity_ratio",
    "issuer_continuity",
    "raw_sha256",
    "raw_bytes",
    "text_sha256",
    "parser_version",
)


class SecurityCodeTransitionEvidenceError(ValueError):
    """Raised when transition evidence or its application is not provable."""


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_sha256(value: Any) -> str:
    return sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_timestamp(value: Any) -> str:
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SecurityCodeTransitionEvidenceError(
            "published_at is not an ISO timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SecurityCodeTransitionEvidenceError(
            "published_at must include a timezone"
        )
    return parsed.isoformat()


def _canonical_ts_code(value: Any, *, field: str) -> str:
    text = str(value or "").strip().upper()
    if _TS_CODE_PATTERN.fullmatch(text) is None:
        raise SecurityCodeTransitionEvidenceError(
            f"{field} is not a canonical A-share ts_code"
        )
    return text


def _symbol_ts_code(symbol: str) -> str:
    if not re.fullmatch(r"\d{6}", symbol):
        raise SecurityCodeTransitionEvidenceError(
            "replay symbol is not a six-digit A-share symbol"
        )
    exchange = "SH" if symbol[0] in {"5", "6", "9"} else "SZ"
    return f"{symbol}.{exchange}"


def _canonical_date(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise SecurityCodeTransitionEvidenceError(
            f"{field} is not an ISO date"
        ) from exc


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
        raise SecurityCodeTransitionEvidenceError(
            "CNINFO source URL is not pinned"
        )
    if match.group("date") != datetime.fromisoformat(published_at).date().isoformat():
        raise SecurityCodeTransitionEvidenceError(
            "source publication date does not match published_at"
        )
    return text


def _extract_pdf_text(raw_bytes: bytes) -> str:
    if not isinstance(raw_bytes, bytes) or not raw_bytes.startswith(b"%PDF-"):
        raise SecurityCodeTransitionEvidenceError(
            "official evidence is not a PDF"
        )
    if not raw_bytes or len(raw_bytes) > MAX_CNINFO_PDF_BYTES:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF size is outside the trusted bound"
        )
    try:
        from pypdf import PdfReader

        reader = PdfReader(BytesIO(raw_bytes), strict=False)
        if reader.is_encrypted:
            raise SecurityCodeTransitionEvidenceError(
                "encrypted official PDF is not supported"
            )
        if not reader.pages or len(reader.pages) > MAX_CNINFO_PDF_PAGES:
            raise SecurityCodeTransitionEvidenceError(
                "official PDF page count is outside the trusted bound"
            )
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
    except SecurityCodeTransitionEvidenceError:
        raise
    except Exception as exc:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF cannot be parsed"
        ) from exc
    normalized = " ".join(text.split())
    if not normalized or len(normalized) > MAX_CNINFO_TEXT_CHARS:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF text is outside the trusted bound"
        )
    return normalized


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _contains_code_relation(
    compact_text: str,
    predecessor_symbol: str,
    successor_symbol: str,
) -> bool:
    relations = (
        f"原证券代码“{predecessor_symbol}”变更为“{successor_symbol}”",
        (
            f"证券代码“{predecessor_symbol}”股份变更为证券代码"
            f"“{successor_symbol}”股份"
        ),
        f"证券代码{predecessor_symbol}变更为{successor_symbol}",
    )
    return any(value in compact_text for value in relations)


def parse_cninfo_security_code_transition_pdf(
    raw_bytes: bytes,
    *,
    source_url: str,
    published_at: Any,
    predecessor_ts_code: str,
    successor_ts_code: str,
    effective_date: Any,
) -> dict[str, Any]:
    canonical_published_at = _canonical_timestamp(published_at)
    canonical_url = _canonical_source_url(
        source_url, canonical_published_at
    )
    predecessor = _canonical_ts_code(
        predecessor_ts_code, field="predecessor_ts_code"
    )
    successor = _canonical_ts_code(
        successor_ts_code, field="successor_ts_code"
    )
    if predecessor == successor:
        raise SecurityCodeTransitionEvidenceError(
            "predecessor and successor codes must differ"
        )
    predecessor_match = _TS_CODE_PATTERN.fullmatch(predecessor)
    successor_match = _TS_CODE_PATTERN.fullmatch(successor)
    assert predecessor_match is not None
    assert successor_match is not None
    if predecessor_match.group("exchange") != successor_match.group("exchange"):
        raise SecurityCodeTransitionEvidenceError(
            "security-code transition cannot cross exchanges"
        )
    effective = _canonical_date(effective_date, field="effective_date")
    if datetime.fromisoformat(canonical_published_at).date() > date.fromisoformat(
        effective
    ):
        raise SecurityCodeTransitionEvidenceError(
            "transition evidence was published after its effective date"
        )

    raw = bytes(raw_bytes)
    text = _extract_pdf_text(raw)
    compact = _compact_text(text)
    predecessor_symbol = predecessor_match.group("symbol")
    successor_symbol = successor_match.group("symbol")
    announcement = _ANNOUNCEMENT_PATTERN.search(text)
    if announcement is None:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF announcement number is missing"
        )
    if "关于变更公司证券简称及证券代码的实施公告" not in compact:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF is not a security-code transition notice"
        )
    if (
        f"证券代码：{predecessor_symbol}" not in compact
        and f"证券代码:{predecessor_symbol}" not in compact
    ):
        raise SecurityCodeTransitionEvidenceError(
            "official PDF predecessor code is not bound"
        )
    if (
        f"变更后的证券代码：{successor_symbol}" not in compact
        and f"变更后的证券代码:{successor_symbol}" not in compact
        and f"变更后的证券代码{successor_symbol}" not in compact
        and not _contains_code_relation(
            compact, predecessor_symbol, successor_symbol
        )
    ):
        raise SecurityCodeTransitionEvidenceError(
            "official PDF successor code is not bound"
        )
    effective_parts = date.fromisoformat(effective)
    chinese_effective = (
        f"{effective_parts.year}年{effective_parts.month}月"
        f"{effective_parts.day}日"
    )
    if (
        chinese_effective not in compact
        or (
            "开市起启用变更后的证券简称及证券代码" not in compact
            and "开市起启用变更后的证券代码" not in compact
            and "证券代码启用日期" not in compact
        )
    ):
        raise SecurityCodeTransitionEvidenceError(
            "official PDF effective date is not bound"
        )
    if "持有公司股份数量不变" not in compact:
        raise SecurityCodeTransitionEvidenceError(
            "official PDF does not prove unchanged share quantity"
        )
    if (
        "公司法人主体存续" not in compact
        or "上市主体没有发生实质变化" not in compact
    ):
        raise SecurityCodeTransitionEvidenceError(
            "official PDF does not prove issuer continuity"
        )
    predecessor_boundary = (
        f"T-1日前（含T-1日）证券代码为“{predecessor_symbol}”"
    )
    successor_boundary = (
        f"T日后（含T日）证券代码变更为“{successor_symbol}”"
    )
    if (
        predecessor_boundary not in compact
        or successor_boundary not in compact
        or not _contains_code_relation(
            compact, predecessor_symbol, successor_symbol
        )
    ):
        raise SecurityCodeTransitionEvidenceError(
            "official PDF does not prove the T-1/T code boundary"
        )

    return {
        "schema_version": SECURITY_CODE_TRANSITION_EVIDENCE_SCHEMA_VERSION,
        "source_profile": "cninfo-official",
        "source_url": canonical_url,
        "published_at": canonical_published_at,
        "publication_time_precision": (
            "source_partition_date_normalized_midnight"
        ),
        "announcement_number": (
            f"{announcement.group(1)}-{announcement.group(2)}"
        ),
        "predecessor_ts_code": predecessor,
        "successor_ts_code": successor,
        "effective_date": effective,
        "share_quantity_ratio": 1.0,
        "issuer_continuity": True,
        "raw_sha256": sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "text_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "parser_version": SECURITY_CODE_TRANSITION_PARSER_VERSION,
    }


def build_security_code_transition_contract(
    evidence_receipts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not evidence_receipts:
        raise SecurityCodeTransitionEvidenceError(
            "transition contract requires at least one evidence receipt"
        )
    transitions: list[dict[str, Any]] = []
    claimed_codes: set[str] = set()
    for raw_receipt in evidence_receipts:
        receipt = dict(raw_receipt)
        if (
            receipt.get("schema_version")
            != SECURITY_CODE_TRANSITION_EVIDENCE_SCHEMA_VERSION
            or receipt.get("source_profile") != "cninfo-official"
            or receipt.get("publication_time_precision")
            != "source_partition_date_normalized_midnight"
            or receipt.get("parser_version")
            != SECURITY_CODE_TRANSITION_PARSER_VERSION
            or receipt.get("share_quantity_ratio") != 1.0
            or receipt.get("issuer_continuity") is not True
        ):
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence receipt contract is invalid"
            )
        predecessor = _canonical_ts_code(
            receipt.get("predecessor_ts_code"),
            field="predecessor_ts_code",
        )
        successor = _canonical_ts_code(
            receipt.get("successor_ts_code"),
            field="successor_ts_code",
        )
        predecessor_match = _TS_CODE_PATTERN.fullmatch(predecessor)
        successor_match = _TS_CODE_PATTERN.fullmatch(successor)
        assert predecessor_match is not None
        assert successor_match is not None
        if (
            predecessor == successor
            or predecessor_match.group("exchange")
            != successor_match.group("exchange")
        ):
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence codes are not a same-exchange change"
            )
        effective = _canonical_date(
            receipt.get("effective_date"), field="effective_date"
        )
        published_at = _canonical_timestamp(receipt.get("published_at"))
        source_url = _canonical_source_url(
            receipt.get("source_url"), published_at
        )
        if datetime.fromisoformat(published_at).date() > date.fromisoformat(
            effective
        ):
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence was published after its effective date"
            )
        announcement_number = str(
            receipt.get("announcement_number") or ""
        ).strip()
        raw_sha = str(receipt.get("raw_sha256") or "").strip().lower()
        text_sha = str(receipt.get("text_sha256") or "").strip().lower()
        raw_bytes = receipt.get("raw_bytes")
        if (
            _ANNOUNCEMENT_NUMBER_PATTERN.fullmatch(
                announcement_number
            )
            is None
            or _SHA256_PATTERN.fullmatch(raw_sha) is None
            or _SHA256_PATTERN.fullmatch(text_sha) is None
            or not isinstance(raw_bytes, int)
            or isinstance(raw_bytes, bool)
            or raw_bytes <= 0
            or raw_bytes > MAX_CNINFO_PDF_BYTES
        ):
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence receipt provenance is invalid"
            )
        normalized_receipt = {
            key: receipt.get(key) for key in _EVIDENCE_KEYS
        }
        normalized_receipt.update(
            {
                "source_url": source_url,
                "published_at": published_at,
                "announcement_number": announcement_number,
                "predecessor_ts_code": predecessor,
                "successor_ts_code": successor,
                "effective_date": effective,
                "raw_sha256": raw_sha,
                "raw_bytes": raw_bytes,
                "text_sha256": text_sha,
            }
        )
        if {predecessor, successor} & claimed_codes:
            raise SecurityCodeTransitionEvidenceError(
                "transition contract contains ambiguous code identities"
            )
        claimed_codes.update({predecessor, successor})
        identity = {
            "predecessor_ts_code": predecessor,
            "successor_ts_code": successor,
            "effective_date": effective,
            "share_quantity_ratio": 1.0,
        }
        transition_id = canonical_sha256(identity)
        transitions.append(
            {
                **normalized_receipt,
                **identity,
                "canonical_ts_code": predecessor,
                "security_id": f"cn-a-share:{predecessor}",
                "transition_id": transition_id,
            }
        )
    transitions.sort(
        key=lambda value: (
            value["effective_date"],
            value["predecessor_ts_code"],
            value["successor_ts_code"],
        )
    )
    return {
        "schema_version": SECURITY_CODE_TRANSITION_CONTRACT_SCHEMA_VERSION,
        "identity_policy": (
            "official_issuer_continuity_and_unchanged_share_quantity"
        ),
        "pre_effective_successor_policy": "exclude_provider_backfill",
        "effective_boundary_policy": "predecessor_before_successor_on_or_after",
        "transitions": transitions,
    }


_OFFICIAL_300114_302132_EVIDENCE = {
    "schema_version": SECURITY_CODE_TRANSITION_EVIDENCE_SCHEMA_VERSION,
    "source_profile": "cninfo-official",
    "source_url": (
        "https://static.cninfo.com.cn/finalpage/2025-02-15/"
        "1222544408.PDF"
    ),
    "published_at": "2025-02-15T00:00:00+08:00",
    "publication_time_precision": (
        "source_partition_date_normalized_midnight"
    ),
    "announcement_number": "2025-028",
    "predecessor_ts_code": "300114.SZ",
    "successor_ts_code": "302132.SZ",
    "effective_date": "2025-02-17",
    "share_quantity_ratio": 1.0,
    "issuer_continuity": True,
    "raw_sha256": (
        "dd68049c48df826848f361fd9e7b23dd20b6805144a2e5bc36e54db638611488"
    ),
    "raw_bytes": 370156,
    "text_sha256": (
        "d63b8c08c7fdd6d8a1b15579b623edd6f6f07d585493c90391fc228319bb8a3f"
    ),
    "parser_version": SECURITY_CODE_TRANSITION_PARSER_VERSION,
}

SECURITY_CODE_TRANSITION_CONTRACT = build_security_code_transition_contract(
    [_OFFICIAL_300114_302132_EVIDENCE]
)
SECURITY_CODE_TRANSITION_CONTRACT_SHA256 = canonical_sha256(
    SECURITY_CODE_TRANSITION_CONTRACT
)


def _validated_contract(contract: Mapping[str, Any]) -> dict[str, Any]:
    value = json.loads(_canonical_json(dict(contract)))
    if (
        value.get("schema_version")
        != SECURITY_CODE_TRANSITION_CONTRACT_SCHEMA_VERSION
        or not isinstance(value.get("transitions"), list)
        or not value["transitions"]
    ):
        raise SecurityCodeTransitionEvidenceError(
            "security-code transition contract is invalid"
        )
    receipts = [
        {key: item.get(key) for key in _EVIDENCE_KEYS}
        for item in value["transitions"]
        if isinstance(item, Mapping)
    ]
    if len(receipts) != len(value["transitions"]):
        raise SecurityCodeTransitionEvidenceError(
            "security-code transition contract is invalid"
        )
    rebuilt = build_security_code_transition_contract(receipts)
    if rebuilt != value:
        raise SecurityCodeTransitionEvidenceError(
            "security-code transition contract derivation mismatch"
        )
    return value


def load_security_code_transition_evidence(
    evidence_root: str | Path,
    *,
    expected_contract_sha256: str,
) -> dict[str, Any]:
    expected = str(expected_contract_sha256 or "").strip().lower()
    if (
        expected != SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        or len(expected) != 64
    ):
        raise SecurityCodeTransitionEvidenceError(
            "security-code transition contract hash mismatch"
        )
    root = Path(evidence_root)
    if not root.is_dir() or root.is_symlink():
        raise SecurityCodeTransitionEvidenceError(
            "transition evidence root is not a trusted directory"
        )
    frozen_contract = _validated_contract(
        SECURITY_CODE_TRANSITION_CONTRACT
    )
    transitions = frozen_contract["transitions"]
    expected_names = {
        f"{item['raw_sha256']}.pdf" for item in transitions
    }
    actual_names = {
        item.name
        for item in root.iterdir()
        if item.is_file() and item.suffix.lower() == ".pdf"
    }
    if actual_names != expected_names:
        raise SecurityCodeTransitionEvidenceError(
            "transition evidence PDF set does not match the contract hash set"
        )

    receipts: list[dict[str, Any]] = []
    for item in transitions:
        path = root / f"{item['raw_sha256']}.pdf"
        if not path.is_file() or path.is_symlink():
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence PDF is missing or is a symlink"
            )
        raw = path.read_bytes()
        if sha256(raw).hexdigest() != item["raw_sha256"]:
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence PDF hash mismatch"
            )
        parsed = parse_cninfo_security_code_transition_pdf(
            raw,
            source_url=item["source_url"],
            published_at=item["published_at"],
            predecessor_ts_code=item["predecessor_ts_code"],
            successor_ts_code=item["successor_ts_code"],
            effective_date=item["effective_date"],
        )
        if any(parsed[key] != item[key] for key in _EVIDENCE_KEYS):
            raise SecurityCodeTransitionEvidenceError(
                "transition evidence does not reproduce the frozen contract"
            )
        receipts.append(
            {
                **parsed,
                "evidence_file_name": path.name,
                "transition_id": item["transition_id"],
            }
        )
    receipt_identity = {
        "schema_version": "security-code-transition-evidence-load/v1",
        "contract_sha256": expected,
        "evidence_receipts": receipts,
    }
    return {
        **receipt_identity,
        "contract": json.loads(
            _canonical_json(frozen_contract)
        ),
        "receipt_sha256": canonical_sha256(receipt_identity),
    }


def _normalized_sessions(sessions: Sequence[Any]) -> list[str]:
    normalized = [
        _canonical_date(value, field="session") for value in sessions
    ]
    if (
        not normalized
        or normalized != sorted(normalized)
        or len(normalized) != len(set(normalized))
    ):
        raise SecurityCodeTransitionEvidenceError(
            "transition application sessions are not strict and ordered"
        )
    return normalized


def _frame_content_sha256(frame: pd.DataFrame) -> str:
    if frame.columns.duplicated().any():
        raise SecurityCodeTransitionEvidenceError(
            "transition bars contain duplicate columns"
        )
    columns = sorted(str(column) for column in frame.columns)
    ordered = frame.loc[:, columns].copy()
    sort_columns = [
        column
        for column in ("ts_code", "source_ts_code", "date")
        if column in ordered.columns
    ]
    if sort_columns:
        ordered = ordered.sort_values(
            sort_columns, kind="mergesort", na_position="first"
        ).reset_index(drop=True)
    try:
        row_hashes = pd.util.hash_pandas_object(
            ordered,
            index=False,
            categorize=False,
        ).to_numpy(dtype="uint64", copy=False)
    except (TypeError, ValueError) as exc:
        raise SecurityCodeTransitionEvidenceError(
            "transition bars contain non-hashable values"
        ) from exc
    digest = sha256(
        _canonical_json(
            {
                "columns": columns,
                "dtypes": [str(ordered[column].dtype) for column in columns],
                "row_count": len(ordered),
            }
        ).encode("utf-8")
    )
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def _same_market_value(left: Any, right: Any) -> bool:
    if pd.isna(left) and pd.isna(right):
        return True
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(
            float(left),
            float(right),
            rel_tol=1e-12,
            abs_tol=1e-12,
        )
    return left == right


def apply_security_code_transition_contract(
    bars: pd.DataFrame,
    *,
    sessions: Sequence[Any],
    contract: Mapping[str, Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    if not {"date", "ts_code"}.issubset(bars.columns):
        raise SecurityCodeTransitionEvidenceError(
            "transition application bars are missing identity columns"
        )
    contract_value = _validated_contract(contract)
    contract_sha = canonical_sha256(contract_value)
    ordered_sessions = _normalized_sessions(sessions)
    result = bars.copy()
    result["date"] = result["date"].astype(str)
    result["ts_code"] = result["ts_code"].astype(str).str.upper()
    if not set(result["date"]).issubset(ordered_sessions):
        raise SecurityCodeTransitionEvidenceError(
            "transition bars contain dates outside the audited sessions"
        )
    if result.duplicated(subset=["ts_code", "date"]).any():
        raise SecurityCodeTransitionEvidenceError(
            "transition source bars contain duplicate code dates"
        )
    source_bars_sha = _frame_content_sha256(result)
    result["source_ts_code"] = result["ts_code"]
    result["security_id"] = result["ts_code"].map(
        lambda value: f"cn-a-share:{value}"
    )
    result["security_code_transition_id"] = None
    result["security_code_transition_contract_sha256"] = contract_sha

    keep = pd.Series(True, index=result.index, dtype=bool)
    excluded_successor_backfill = 0
    excluded_predecessor_after_effective = 0
    transition_receipts: list[dict[str, Any]] = []
    excluded_row_keys: list[str] = []
    boundary_count = 0
    for item in contract_value["transitions"]:
        predecessor = _canonical_ts_code(
            item.get("predecessor_ts_code"),
            field="predecessor_ts_code",
        )
        successor = _canonical_ts_code(
            item.get("successor_ts_code"),
            field="successor_ts_code",
        )
        canonical = _canonical_ts_code(
            item.get("canonical_ts_code"),
            field="canonical_ts_code",
        )
        effective = _canonical_date(
            item.get("effective_date"), field="effective_date"
        )
        security_id = str(item.get("security_id") or "").strip()
        transition_id = str(item.get("transition_id") or "").strip()
        if (
            canonical != predecessor
            or not security_id
            or len(transition_id) != 64
        ):
            raise SecurityCodeTransitionEvidenceError(
                "transition application identity is invalid"
            )
        target = result["source_ts_code"].isin([predecessor, successor])
        target_count = int(target.sum())
        if target_count == 0:
            continue

        predecessor_before = result[
            target
            & (result["source_ts_code"] == predecessor)
            & (result["date"] < effective)
        ].set_index("date", drop=False)
        successor_before = result[
            target
            & (result["source_ts_code"] == successor)
            & (result["date"] < effective)
        ].set_index("date", drop=False)
        overlap_dates = sorted(
            set(predecessor_before.index) & set(successor_before.index)
        )
        comparison_fields = [
            field
            for field in (
                "open",
                "high",
                "low",
                "close",
                "pre_close",
                "amount",
                "adj_factor",
                "suspended",
            )
            if field in result.columns
        ]
        for overlap_date in overlap_dates:
            if any(
                not _same_market_value(
                    predecessor_before.at[overlap_date, field],
                    successor_before.at[overlap_date, field],
                )
                for field in comparison_fields
            ):
                raise SecurityCodeTransitionEvidenceError(
                    "successor backfill conflicts with predecessor market bars"
                )

        if ordered_sessions[0] < effective <= ordered_sessions[-1]:
            if effective not in ordered_sessions:
                raise SecurityCodeTransitionEvidenceError(
                    "security-code transition boundary session is missing"
                )
            predecessor_sessions = [
                value for value in ordered_sessions if value < effective
            ]
            predecessor_boundary = (
                predecessor_sessions[-1] if predecessor_sessions else None
            )
            if predecessor_boundary is not None:
                predecessor_rows = result[
                    target
                    & (result["source_ts_code"] == predecessor)
                    & (result["date"] == predecessor_boundary)
                ]
                if len(predecessor_rows) != 1:
                    raise SecurityCodeTransitionEvidenceError(
                        "predecessor transition boundary bar is missing or ambiguous"
                    )
            successor_rows = result[
                target
                & (result["source_ts_code"] == successor)
                & (result["date"] == effective)
            ]
            if len(successor_rows) != 1:
                raise SecurityCodeTransitionEvidenceError(
                    "successor transition boundary bar is missing or ambiguous"
                )
            if predecessor_boundary is not None:
                predecessor_row = predecessor_rows.iloc[0]
                successor_row = successor_rows.iloc[0]
                if (
                    "pre_close" in result.columns
                    and not _same_market_value(
                        predecessor_row["close"],
                        successor_row["pre_close"],
                    )
                ):
                    raise SecurityCodeTransitionEvidenceError(
                        "transition boundary close continuity is invalid"
                    )
                if (
                    "adj_factor" in result.columns
                    and not _same_market_value(
                        predecessor_row["adj_factor"],
                        successor_row["adj_factor"],
                    )
                ):
                    raise SecurityCodeTransitionEvidenceError(
                        "transition boundary adjustment continuity is invalid"
                    )
            boundary_count += 1
        elif ordered_sessions[0] == effective:
            successor_rows = result[
                target
                & (result["source_ts_code"] == successor)
                & (result["date"] == effective)
            ]
            if len(successor_rows) != 1:
                raise SecurityCodeTransitionEvidenceError(
                    "successor transition boundary bar is missing or ambiguous"
                )
            boundary_count += 1

        successor_backfill = (
            target
            & (result["source_ts_code"] == successor)
            & (result["date"] < effective)
        )
        predecessor_after = (
            target
            & (result["source_ts_code"] == predecessor)
            & (result["date"] >= effective)
        )
        excluded_successor_backfill += int(successor_backfill.sum())
        excluded_predecessor_after_effective += int(predecessor_after.sum())
        excluded_row_keys.extend(
            f"{row.source_ts_code}|{row.date}"
            for row in result.loc[
                successor_backfill | predecessor_after,
                ["source_ts_code", "date"],
            ].itertuples(index=False)
        )
        keep &= ~successor_backfill & ~predecessor_after
        result.loc[target, "ts_code"] = canonical
        result.loc[target, "security_id"] = security_id
        result.loc[target, "security_code_transition_id"] = transition_id
        transition_receipts.append(
            {
                "transition_id": transition_id,
                "security_id": security_id,
                "canonical_ts_code": canonical,
                "predecessor_ts_code": predecessor,
                "successor_ts_code": successor,
                "effective_date": effective,
                "source_row_count": target_count,
                "excluded_successor_backfill_row_count": int(
                    successor_backfill.sum()
                ),
                "excluded_predecessor_after_effective_row_count": int(
                    predecessor_after.sum()
                ),
            }
        )

    canonical_bars = result.loc[keep].copy()
    if canonical_bars.duplicated(subset=["ts_code", "date"]).any():
        raise SecurityCodeTransitionEvidenceError(
            "transition application created duplicate canonical market bars"
        )
    canonical_bars = canonical_bars.sort_values(
        ["ts_code", "date"], kind="mergesort"
    ).reset_index(drop=True)
    receipt_identity = {
        "schema_version": SECURITY_CODE_TRANSITION_APPLICATION_SCHEMA_VERSION,
        "contract_sha256": contract_sha,
        "sessions_sha256": canonical_sha256(ordered_sessions),
        "source_bars_sha256": source_bars_sha,
        "canonical_bars_sha256": _frame_content_sha256(canonical_bars),
        "source_row_count": len(bars),
        "canonical_row_count": len(canonical_bars),
        "excluded_successor_backfill_row_count": (
            excluded_successor_backfill
        ),
        "excluded_predecessor_after_effective_row_count": (
            excluded_predecessor_after_effective
        ),
        "transition_boundary_count": boundary_count,
        "excluded_row_keys_sha256": canonical_sha256(
            sorted(excluded_row_keys)
        ),
        "transition_receipts": transition_receipts,
    }
    return canonical_bars, {
        **receipt_identity,
        "receipt_sha256": canonical_sha256(receipt_identity),
    }


def _transition_code_lookup(
    contract: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], str]:
    value = _validated_contract(contract)
    lookup: dict[str, dict[str, Any]] = {}
    for item in value["transitions"]:
        predecessor = _canonical_ts_code(
            item.get("predecessor_ts_code"),
            field="predecessor_ts_code",
        )
        successor = _canonical_ts_code(
            item.get("successor_ts_code"),
            field="successor_ts_code",
        )
        normalized = {
            **item,
            "predecessor_ts_code": predecessor,
            "successor_ts_code": successor,
            "canonical_ts_code": _canonical_ts_code(
                item.get("canonical_ts_code"),
                field="canonical_ts_code",
            ),
            "effective_date": _canonical_date(
                item.get("effective_date"), field="effective_date"
            ),
        }
        for code in (predecessor, successor):
            if code in lookup:
                raise SecurityCodeTransitionEvidenceError(
                    "transition evidence remap identity is ambiguous"
                )
            lookup[code] = normalized
    return lookup, canonical_sha256(value)


def _evidence_ts_code(
    symbol: Any,
    proof: Mapping[str, Any],
) -> str:
    raw_symbol = str(symbol or "").strip().upper()
    key_symbol = (
        raw_symbol[:6]
        if _TS_CODE_PATTERN.fullmatch(raw_symbol)
        else raw_symbol
    )
    if not re.fullmatch(r"\d{6}", key_symbol):
        raise SecurityCodeTransitionEvidenceError(
            "evidence symbol is not a six-digit A-share symbol"
        )
    explicit = str(proof.get("ts_code") or "").strip().upper()
    if explicit:
        ts_code = _canonical_ts_code(explicit, field="evidence ts_code")
        if ts_code[:6] != key_symbol:
            raise SecurityCodeTransitionEvidenceError(
                "evidence ts_code conflicts with its symbol"
            )
        return ts_code
    if _TS_CODE_PATTERN.fullmatch(raw_symbol):
        return raw_symbol
    return _symbol_ts_code(key_symbol)


def _stable_evidence_identity(
    *,
    ts_code: str,
    trade_date: str,
    lookup: Mapping[str, Mapping[str, Any]],
) -> tuple[str, str, str | None]:
    item = lookup.get(ts_code)
    if item is None:
        return ts_code[:6], f"cn-a-share:{ts_code}", None
    effective = str(item["effective_date"])
    expected = (
        str(item["predecessor_ts_code"])
        if trade_date < effective
        else str(item["successor_ts_code"])
    )
    if ts_code != expected:
        raise SecurityCodeTransitionEvidenceError(
            "transition evidence code is outside its effective interval"
        )
    return (
        str(item["canonical_ts_code"])[:6],
        str(item["security_id"]),
        str(item["transition_id"]),
    )


def remap_security_code_transition_suspension_evidence(
    evidence: Mapping[
        tuple[str, str], Sequence[Mapping[str, Any]]
    ],
    *,
    contract: Mapping[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    lookup, contract_sha = _transition_code_lookup(contract)
    remapped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for raw_key, raw_proofs in sorted(evidence.items()):
        if not isinstance(raw_key, tuple) or len(raw_key) != 2:
            raise SecurityCodeTransitionEvidenceError(
                "suspension evidence key is invalid"
            )
        source_symbol = str(raw_key[0] or "").strip().upper()
        trade_date = _canonical_date(
            raw_key[1], field="suspension trade_date"
        )
        if not isinstance(raw_proofs, Sequence) or isinstance(
            raw_proofs, (str, bytes)
        ):
            raise SecurityCodeTransitionEvidenceError(
                "suspension evidence proofs are invalid"
            )
        for raw_proof in raw_proofs:
            if not isinstance(raw_proof, Mapping):
                raise SecurityCodeTransitionEvidenceError(
                    "suspension evidence proof is invalid"
                )
            proof = dict(raw_proof)
            if (
                str(proof.get("symbol") or "").strip().upper()
                != source_symbol
                or _canonical_date(
                    proof.get("trade_date"),
                    field="suspension proof trade_date",
                )
                != trade_date
            ):
                raise SecurityCodeTransitionEvidenceError(
                    "suspension evidence proof identity conflicts with its key"
                )
            source_ts_code = _evidence_ts_code(source_symbol, proof)
            stable_symbol, security_id, transition_id = (
                _stable_evidence_identity(
                    ts_code=source_ts_code,
                    trade_date=trade_date,
                    lookup=lookup,
                )
            )
            remapped.setdefault((stable_symbol, trade_date), []).append(
                {
                    **proof,
                    "symbol": stable_symbol,
                    "source_symbol": source_symbol,
                    "source_ts_code": source_ts_code,
                    "security_id": security_id,
                    "security_code_transition_id": transition_id,
                    "security_code_transition_contract_sha256": contract_sha,
                }
            )
    return {
        key: sorted(
            proofs,
            key=lambda proof: canonical_sha256(proof),
        )
        for key, proofs in sorted(remapped.items())
    }


def remap_security_code_transition_terminal_evidence(
    evidence: Mapping[str, Mapping[str, Any]],
    *,
    contract: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    lookup, contract_sha = _transition_code_lookup(contract)
    remapped: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_proof in sorted(evidence.items()):
        if not isinstance(raw_proof, Mapping):
            raise SecurityCodeTransitionEvidenceError(
                "terminal evidence proof is invalid"
            )
        source_symbol = str(raw_symbol or "").strip().upper()
        proof = dict(raw_proof)
        if str(proof.get("symbol") or "").strip().upper() != source_symbol:
            raise SecurityCodeTransitionEvidenceError(
                "terminal evidence proof identity conflicts with its key"
            )
        terminal_date = _canonical_date(
            proof.get("delist_date"), field="terminal delist_date"
        )
        source_ts_code = _evidence_ts_code(source_symbol, proof)
        stable_symbol, security_id, transition_id = (
            _stable_evidence_identity(
                ts_code=source_ts_code,
                trade_date=terminal_date,
                lookup=lookup,
            )
        )
        mapped = {
            **proof,
            "symbol": stable_symbol,
            "ts_code": str(
                lookup.get(source_ts_code, {}).get(
                    "canonical_ts_code", source_ts_code
                )
            ),
            "source_symbol": source_symbol,
            "source_ts_code": source_ts_code,
            "security_id": security_id,
            "security_code_transition_id": transition_id,
            "security_code_transition_contract_sha256": contract_sha,
        }
        previous = remapped.get(stable_symbol)
        if previous is not None and previous != mapped:
            raise SecurityCodeTransitionEvidenceError(
                "terminal evidence conflicts for one stable security"
            )
        remapped[stable_symbol] = mapped
    return remapped


class SecurityCodeTransitionReplayAdapter:
    """Resolve a stable security identity to the actual code on each date."""

    def __init__(
        self,
        base_adapter: Any,
        *,
        contract: Mapping[str, Any],
    ) -> None:
        if not hasattr(base_adapter, "next_open"):
            raise SecurityCodeTransitionEvidenceError(
                "base replay adapter has no next_open boundary"
            )
        self._base_adapter = base_adapter
        self._contract = _validated_contract(contract)
        self._transition_contract_sha256 = canonical_sha256(self._contract)
        self._by_code: dict[str, dict[str, Any]] = {}
        for item in self._contract.get("transitions", []):
            predecessor = _canonical_ts_code(
                item.get("predecessor_ts_code"),
                field="predecessor_ts_code",
            )
            successor = _canonical_ts_code(
                item.get("successor_ts_code"),
                field="successor_ts_code",
            )
            normalized = {
                **item,
                "predecessor_ts_code": predecessor,
                "successor_ts_code": successor,
                "effective_date": _canonical_date(
                    item.get("effective_date"), field="effective_date"
                ),
            }
            for code in (predecessor, successor):
                if code in self._by_code:
                    raise SecurityCodeTransitionEvidenceError(
                        "replay transition code identity is ambiguous"
                    )
                self._by_code[code] = normalized
        base_contract = str(
            getattr(base_adapter, "contract_sha256", "") or ""
        ).strip().lower()
        artifact_root = str(
            getattr(base_adapter, "artifact_root_sha256", "") or ""
        ).strip().lower()
        if (
            _SHA256_PATTERN.fullmatch(base_contract) is None
            or _SHA256_PATTERN.fullmatch(artifact_root) is None
        ):
            raise SecurityCodeTransitionEvidenceError(
                "base replay adapter audit hashes are invalid"
            )
        self._contract_sha256 = canonical_sha256(
            {
                "schema_version": (
                    "security-code-transition-replay-adapter/v1"
                ),
                "base_replay_contract_sha256": base_contract,
                "security_code_transition_contract_sha256": (
                    self._transition_contract_sha256
                ),
            }
        )

    @property
    def artifact_root_sha256(self) -> Any:
        return getattr(self._base_adapter, "artifact_root_sha256", None)

    @property
    def contract_sha256(self) -> str:
        return self._contract_sha256

    @property
    def security_code_transition_contract_sha256(self) -> str:
        return self._transition_contract_sha256

    def baseline_scenarios(self) -> Any:
        return self._base_adapter.baseline_scenarios()

    def resolve_ts_code(
        self, symbol: Any, trade_date: Any
    ) -> tuple[str, str, str | None]:
        raw_symbol = str(symbol or "").strip().upper()
        if re.fullmatch(r"\d{6}", raw_symbol):
            inferred_ts_code = _symbol_ts_code(raw_symbol)
            candidates = [
                code for code in self._by_code if code.startswith(raw_symbol)
            ]
            if len(candidates) > 1:
                transition_ids = {
                    self._by_code[code]["transition_id"]
                    for code in candidates
                }
                if len(transition_ids) != 1:
                    raise SecurityCodeTransitionEvidenceError(
                        "replay symbol identity is ambiguous"
                    )
            ts_code = candidates[0] if candidates else inferred_ts_code
        elif _TS_CODE_PATTERN.fullmatch(raw_symbol):
            ts_code = raw_symbol
            if (
                ts_code not in self._by_code
                and any(
                    code[:6] == ts_code[:6] for code in self._by_code
                )
            ):
                raise SecurityCodeTransitionEvidenceError(
                    "replay symbol exchange conflicts with transition contract"
                )
        else:
            raise SecurityCodeTransitionEvidenceError(
                "replay symbol is not a six-digit symbol or ts_code"
            )
        transition = self._by_code.get(ts_code)
        if transition is None:
            return ts_code[:6], ts_code, None
        effective = transition["effective_date"]
        requested_date = _canonical_date(
            trade_date, field="trade_date"
        )
        resolved = (
            transition["predecessor_ts_code"]
            if requested_date < effective
            else transition["successor_ts_code"]
        )
        return (
            resolved[:6],
            resolved,
            str(transition["transition_id"]),
        )

    def next_open(
        self, symbol: Any, trade_date: Any, side: str = "buy"
    ) -> dict[str, Any]:
        resolved_symbol, resolved_ts_code, transition_id = (
            self.resolve_ts_code(symbol, trade_date)
        )
        verdict = self._base_adapter.next_open(
            resolved_symbol, trade_date, side
        )
        if not isinstance(verdict, Mapping):
            raise SecurityCodeTransitionEvidenceError(
                "base replay verdict is not a mapping"
            )
        result = dict(verdict)
        proof = result.get("generation_proof")
        if not isinstance(proof, Mapping):
            raise SecurityCodeTransitionEvidenceError(
                "base replay verdict generation proof is missing"
            )
        canonical_input = str(symbol or "").strip().upper()
        transition = self._by_code.get(resolved_ts_code)
        stable_symbol = (
            str(transition["canonical_ts_code"])[:6]
            if transition is not None
            else canonical_input[:6]
        )
        result["generation_proof"] = {
            **dict(proof),
            "requested_stable_symbol": stable_symbol,
            "resolved_ts_code": resolved_ts_code,
            "security_code_transition_id": transition_id,
            "security_code_transition_contract_sha256": (
                self._transition_contract_sha256
            ),
        }
        return result
