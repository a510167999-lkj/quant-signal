"""Append-only raw receipt storage for point-in-time universe research.

This module captures native Tushare response bytes before DataFrame conversion,
normalizes one bounded response at a time, and stores multi-year rows in SQLite.
It proves receipt and normalization lineage; it deliberately does not grant
final-validation eligibility to any research strategy.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from contextlib import contextmanager, nullcontext
from datetime import date, datetime, timezone
from pathlib import Path
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import quote, urlsplit

from app.durable_io import fsync_directory, fsync_file
from app.research_pit_contracts import STOCK_BASIC_FIELDS
from app.research_membership import (
    MEMBERSHIP_POLICY_VERSION,
    MembershipContractError,
    build_membership_manifest,
    classify_membership_snapshot_body,
    derive_quarantined_membership,
    verify_membership_manifest,
)
from app.research_proxy_data import (
    ETF_PROXY_REQUIRED_SYMBOLS,
    EtfProxyError,
    FUND_DAILY_FIELDS,
    FUND_DAILY_ROW_CAP,
    normalize_etf_proxy_rows,
)
from app.research_pit_sources import JIAOCH_ROW_CAP_OVERRIDES


CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION = "cninfo-suspension-evidence/v1"
CNINFO_SUSPENSION_PARSER_VERSION_V1 = "cninfo-suspension-pdf/v1"
CNINFO_SUSPENSION_PARSER_VERSION = "cninfo-suspension-pdf/v2"
SUPPORTED_CNINFO_SUSPENSION_PARSER_VERSIONS = frozenset(
    {CNINFO_SUSPENSION_PARSER_VERSION_V1, CNINFO_SUSPENSION_PARSER_VERSION}
)
CNINFO_SUSPENSION_PARSER_V1_CODE_SHA256 = (
    "598d1c73b7b8e3cbf6c941b71e0a0aab64cb2188437421277e0694315c25be39"
)
CNINFO_SOURCE_PROFILE = "cninfo-official"
MAX_CNINFO_PDF_BYTES = 5 * 1024 * 1024


class SuspensionEvidenceError(ValueError):
    """Lazy compatibility boundary for optional CNINFO PDF support."""


def _load_suspension_evidence_module():
    try:
        return importlib.import_module("app.research_suspension_evidence")
    except ModuleNotFoundError as exc:
        if exc.name != "pypdf":
            raise
        raise SuspensionEvidenceError(
            "CNINFO suspension PDF support requires optional dependency 'pypdf'"
        ) from exc


def _lazy_parse_cninfo_suspension_pdf(*args, **kwargs):
    module = _load_suspension_evidence_module()
    try:
        return module.parse_cninfo_suspension_pdf(*args, **kwargs)
    except module.SuspensionEvidenceError as exc:
        raise SuspensionEvidenceError(str(exc)) from exc


# Preserve the existing monkeypatch/public seam while deferring the PDF parser
# and its optional pypdf dependency until an official notice is actually used.
parse_cninfo_suspension_pdf = _lazy_parse_cninfo_suspension_pdf


def _cninfo_parser_code_path() -> Path:
    parser = parse_cninfo_suspension_pdf
    if parser is _lazy_parse_cninfo_suspension_pdf:
        parser = _load_suspension_evidence_module().parse_cninfo_suspension_pdf
    return Path(parser.__code__.co_filename)


STORE_SCHEMA_VERSION = "pit-receipt-store/v4"
LEGACY_STORE_SCHEMA_VERSIONS = {"pit-receipt-store/v2", "pit-receipt-store/v3"}
PARSER_VERSION = "tushare-native-v2"
COVERAGE_AUDIT_SCHEMA_VERSION = "pit-coverage-audit/v4"
UNIVERSE_ARTIFACT_SCHEMA_VERSION = "audited-pit-universe/v5"
DATASET_ENDPOINTS = {
    "stock_basic": "stock_basic",
    "trade_cal": "trade_cal",
    "bak_basic": "bak_basic",
    "daily": "daily",
    "adj_factor": "adj_factor",
    "stk_limit": "stk_limit",
    "suspend_d": "suspend_d",
    "fund_daily": "fund_daily",
}
OFFICIAL_ROW_CAPS = {
    "stock_basic": 6000,
    "bak_basic": 7000,
    "daily": 6000,
    "stk_limit": 5800,
    "fund_daily": FUND_DAILY_ROW_CAP,
}
MAX_RAW_BYTES = {
    "stock_basic": 50 * 1024 * 1024,
    "bak_basic": 50 * 1024 * 1024,
    "trade_cal": 20 * 1024 * 1024,
    "daily": 50 * 1024 * 1024,
    "adj_factor": 50 * 1024 * 1024,
    "stk_limit": 50 * 1024 * 1024,
    "suspend_d": 20 * 1024 * 1024,
    "fund_daily": 50 * 1024 * 1024,
}
SUPPORTED_A_SHARE_MARKETS = ("主板", "创业板", "科创板")
SUPPORTED_A_SHARE_EXCHANGES = ("SSE", "SZSE")
# trade_cal 数据源覆盖范围。jiaoch 中转的 tushare trade_cal 只维护 SSE
# (SZSE/CFFEX/SHFE/DCE 全部空响应,已实测),但 A 股 SSE/SZSE 交易日历自 2010 年起
# 完全一致(证监会统一安排节假日)。calendar audit 只要求 SSE,避免 jiaoch 数据
# 缺失阻断大池 PIT 抓取。SUPPORTED_A_SHARE_EXCHANGES 仍用于 universe/stock_basic
# 审计(那个接口对两市都工作)。
CALENDAR_SOURCE_EXCHANGES = ("SSE",)
REQUIRED_STOCK_STATUSES = ("L", "D", "P", "G")
STOCK_GENERATION_SCHEMA_VERSION = "stock-basic-generations/v1"
STOCK_GENERATION_SCOPE = "SSE,SZSE:L,D,P,G"
STOCK_GENERATION_WINDOW_SECONDS = 3600
REQUIRED_STOCK_PARTITIONS = tuple(
    f"{exchange}:{status}"
    for exchange in SUPPORTED_A_SHARE_EXCHANGES
    for status in REQUIRED_STOCK_STATUSES
)


def _is_supported_a_share_snapshot_code(ts_code: Any, exchange: Any) -> bool:
    """Classify a stock-only bak_basic row without current-code backfill."""

    code = str(ts_code or "").strip().upper()
    venue = str(exchange or "").strip().upper()
    symbol, separator, suffix = code.partition(".")
    if len(symbol) != 6 or not symbol.isdigit() or not separator:
        return False
    if venue == "SSE" and suffix == "SH":
        return not symbol.startswith("900")
    if venue == "SZSE" and suffix == "SZ":
        return not symbol.startswith("200")
    return False


MARKET_SESSION_GENERATION_SCHEMA_VERSION = "market-session-generations/v1"
MARKET_SESSION_GENERATION_WINDOW_SECONDS = 3600
MARKET_SESSION_DATASETS = ("daily", "adj_factor", "stk_limit", "suspend_d")
NONEMPTY_MARKET_SESSION_DATASETS = ("daily", "adj_factor", "stk_limit")
# Frozen vintage enum: classifies how a generation was produced and feeds the
# immutable lineage. The begin API and the collector share one default; any
# value outside this tuple is rejected so an unbounded string can never become
# part of an immutable receipt.
MARKET_SESSION_VINTAGES = ("live_forward", "historical_backfill")
DEFAULT_MARKET_SESSION_VINTAGE = "live_forward"
# WHY: the destination artifact built by publish_universe_artifact must carry
# the market-session generation tables column/constraint identical to the
# source _initialize schema so audit_coverage can re-bind to the same proof
# offline. Both the source and publish paths executescript this single DDL so
# there is exactly one definition (no second hand-written copy to drift).
_MARKET_SESSION_GENERATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_session_generations (
    generation_sequence INTEGER PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    trade_date TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('collecting', 'published', 'abandoned')
    ),
    vintage TEXT NOT NULL,
    final_oos_eligible INTEGER NOT NULL CHECK (final_oos_eligible = 0),
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    terminal_reason TEXT,
    manifest_sha256 TEXT,
    lineage_sha256 TEXT
    ,expected_request_semantics_json TEXT
    ,expected_request_semantics_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS market_session_generation_shards (
    generation_id TEXT NOT NULL,
    dataset TEXT NOT NULL,
    attempt_id TEXT NOT NULL UNIQUE,
    request_semantics_sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    PRIMARY KEY (generation_id, dataset),
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id),
    FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS market_session_generation_rows_daily (
    generation_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    pre_close REAL NOT NULL,
    change REAL NOT NULL,
    pct_chg REAL NOT NULL,
    vol REAL NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (generation_id, trade_date, ts_code),
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id)
);
CREATE TABLE IF NOT EXISTS market_session_generation_rows_adj_factor (
    generation_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    adj_factor REAL NOT NULL,
    PRIMARY KEY (generation_id, trade_date, ts_code),
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id)
);
CREATE TABLE IF NOT EXISTS market_session_generation_rows_stk_limit (
    generation_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    pre_close REAL,
    up_limit REAL NOT NULL CHECK (up_limit >= 0),
    down_limit REAL NOT NULL CHECK (down_limit >= 0),
    PRIMARY KEY (generation_id, trade_date, ts_code),
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id)
);
CREATE TABLE IF NOT EXISTS market_session_generation_rows_suspend_d (
    generation_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    suspend_timing TEXT NOT NULL,
    suspend_type TEXT NOT NULL CHECK (suspend_type IN ('S', 'R')),
    PRIMARY KEY (generation_id, trade_date, ts_code, suspend_timing, suspend_type),
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id)
);
CREATE TABLE IF NOT EXISTS market_session_generation_head (
    trade_date TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (generation_id)
        REFERENCES market_session_generations(generation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_collecting_market_session_per_trade_date
    ON market_session_generations(trade_date)
    WHERE status = 'collecting';
"""
_OFFICIAL_SUSPENSION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS official_notice_receipts (
    receipt_id TEXT PRIMARY KEY,
    source_profile TEXT NOT NULL CHECK (source_profile = 'cninfo-official'),
    source_url TEXT NOT NULL UNIQUE,
    published_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    notice_role TEXT NOT NULL CHECK (notice_role IN ('start', 'resume')),
    ts_code TEXT NOT NULL,
    announcement_number TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    raw_path TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL CHECK (raw_bytes > 0),
    text_sha256 TEXT NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    parser_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS official_suspension_intervals (
    interval_id TEXT PRIMARY KEY,
    ts_code TEXT NOT NULL,
    start_date TEXT NOT NULL,
    resume_date TEXT NOT NULL CHECK (resume_date > start_date),
    start_receipt_id TEXT NOT NULL UNIQUE,
    resume_receipt_id TEXT NOT NULL UNIQUE,
    evidence_root_sha256 TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    UNIQUE (ts_code, start_date, resume_date),
    FOREIGN KEY (start_receipt_id)
        REFERENCES official_notice_receipts(receipt_id),
    FOREIGN KEY (resume_receipt_id)
        REFERENCES official_notice_receipts(receipt_id)
);
CREATE INDEX IF NOT EXISTS idx_official_suspension_interval_dates
    ON official_suspension_intervals(start_date, resume_date, ts_code);
"""
_MEMBERSHIP_GENERATION_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS membership_session_generations (
    generation_sequence INTEGER PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    trade_date TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('collecting', 'published', 'abandoned')
    ),
    source_kind TEXT CHECK (
        source_kind IS NULL OR source_kind IN (
            'causal_carry_forward', 'pre_anchor_quarantine'
        )
    ),
    signal_session_eligible INTEGER NOT NULL CHECK (signal_session_eligible = 0),
    temporal_role TEXT NOT NULL,
    temporal_contract_sha256 TEXT NOT NULL CHECK (
        length(temporal_contract_sha256) = 64
        AND temporal_contract_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    source_authority_json TEXT NOT NULL,
    source_authority_sha256 TEXT NOT NULL CHECK (
        length(source_authority_sha256) = 64
        AND source_authority_sha256 NOT GLOB '*[^0-9a-f]*'
    ),
    market_generation_id TEXT NOT NULL,
    market_manifest_sha256 TEXT NOT NULL,
    market_lineage_sha256 TEXT NOT NULL,
    anchor_trade_date TEXT,
    anchor_receipt_dataset TEXT CHECK (
        anchor_receipt_dataset IS NULL OR anchor_receipt_dataset = 'bak_basic'
    ),
    anchor_receipt_partition TEXT,
    anchor_receipt_raw_sha256 TEXT,
    anchor_normalized_sha256 TEXT,
    anchor_row_count INTEGER CHECK (anchor_row_count IS NULL OR anchor_row_count > 0),
    anchor_in_scope_row_count INTEGER CHECK (
        anchor_in_scope_row_count IS NULL OR anchor_in_scope_row_count > 0
    ),
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    terminal_reason TEXT,
    manifest_json TEXT,
    manifest_sha256 TEXT,
    lineage_sha256 TEXT,
    FOREIGN KEY (market_generation_id)
        REFERENCES market_session_generations(generation_id),
    FOREIGN KEY (anchor_receipt_dataset, anchor_receipt_partition)
        REFERENCES receipts(dataset, partition_key),
    CHECK (
        (source_kind IS NULL AND anchor_trade_date IS NULL
            AND anchor_receipt_dataset IS NULL AND anchor_receipt_partition IS NULL
            AND anchor_receipt_raw_sha256 IS NULL AND anchor_normalized_sha256 IS NULL
            AND anchor_row_count IS NULL AND anchor_in_scope_row_count IS NULL)
        OR
        (source_kind = 'pre_anchor_quarantine' AND anchor_trade_date IS NULL
            AND anchor_receipt_dataset IS NULL AND anchor_receipt_partition IS NULL
            AND anchor_receipt_raw_sha256 IS NULL AND anchor_normalized_sha256 IS NULL
            AND anchor_row_count IS NULL AND anchor_in_scope_row_count IS NULL)
        OR
        (source_kind = 'causal_carry_forward' AND anchor_trade_date IS NOT NULL
            AND anchor_receipt_dataset = 'bak_basic'
            AND anchor_receipt_partition = anchor_trade_date
            AND anchor_receipt_raw_sha256 IS NOT NULL
            AND anchor_normalized_sha256 IS NOT NULL
            AND anchor_row_count IS NOT NULL AND anchor_in_scope_row_count IS NOT NULL
            AND anchor_in_scope_row_count <= anchor_row_count)
    )
);
CREATE TABLE IF NOT EXISTS membership_generation_rows (
    generation_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    ts_code TEXT NOT NULL,
    exchange TEXT,
    name TEXT,
    industry TEXT,
    list_date TEXT,
    membership_present INTEGER NOT NULL CHECK (membership_present IN (0, 1)),
    metadata_stale_possible INTEGER NOT NULL CHECK (metadata_stale_possible IN (0, 1)),
    unknown_metadata INTEGER NOT NULL CHECK (unknown_metadata IN (0, 1)),
    observed_in_daily INTEGER NOT NULL CHECK (observed_in_daily IN (0, 1)),
    signal_session_eligible INTEGER NOT NULL CHECK (signal_session_eligible = 0),
    signal_eligible INTEGER NOT NULL CHECK (signal_eligible = 0),
    source_kind TEXT NOT NULL CHECK (
        source_kind IN ('causal_carry_forward', 'pre_anchor_quarantine')
    ),
    metadata_anchor_date TEXT,
    PRIMARY KEY (generation_id, ts_code),
    FOREIGN KEY (generation_id)
        REFERENCES membership_session_generations(generation_id)
);
CREATE TABLE IF NOT EXISTS membership_generation_evidence (
    generation_id TEXT NOT NULL,
    evidence_kind TEXT NOT NULL CHECK (
        evidence_kind IN ('target_semantic_empty', 'anchor_snapshot_attempt')
    ),
    attempt_id TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    request_semantics_sha256 TEXT NOT NULL,
    terminal_status TEXT NOT NULL,
    classification_kind TEXT NOT NULL CHECK (
        classification_kind IN ('semantic_empty', 'nonempty_snapshot')
    ),
    PRIMARY KEY (generation_id, evidence_kind, attempt_id),
    FOREIGN KEY (generation_id)
        REFERENCES membership_session_generations(generation_id),
    FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id),
    CHECK (
        (evidence_kind = 'target_semantic_empty'
            AND terminal_status = 'invalid_json'
            AND classification_kind = 'semantic_empty')
        OR
        (evidence_kind = 'anchor_snapshot_attempt'
            AND terminal_status IN ('stored', 'reused')
            AND classification_kind = 'nonempty_snapshot')
    )
);
CREATE TABLE IF NOT EXISTS membership_session_head (
    trade_date TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    manifest_sha256 TEXT NOT NULL,
    lineage_sha256 TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (generation_id)
        REFERENCES membership_session_generations(generation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_collecting_membership_generation_per_trade_date
    ON membership_session_generations(trade_date)
    WHERE status = 'collecting';
"""
ETF_PROXY_GENERATION_SCHEMA_VERSION = "etf-proxy-generations/v1"
ETF_PROXY_GENERATION_WINDOW_SECONDS = 3600
ETF_PROXY_GENERATION_VINTAGES = ("historical_backfill", "live_forward")
# Keep source-store and audited-artifact constraints byte-for-byte identical.
# Later generation methods may rely on these database constraints, but this
# schema alone does not make captured fund_daily attempts publishable.
_ETF_PROXY_GENERATION_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS etf_proxy_generations (
    generation_sequence INTEGER PRIMARY KEY,
    generation_id TEXT NOT NULL UNIQUE,
    scope_key TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    contract_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('collecting', 'published', 'abandoned')
    ),
    vintage TEXT NOT NULL CHECK (
        vintage IN ('historical_backfill', 'live_forward')
    ),
    final_oos_eligible INTEGER NOT NULL CHECK (final_oos_eligible = 0),
    started_at TEXT NOT NULL,
    terminal_at TEXT,
    terminal_reason TEXT,
    manifest_sha256 TEXT,
    lineage_sha256 TEXT
);
CREATE TABLE IF NOT EXISTS etf_proxy_generation_shards (
    generation_id TEXT NOT NULL,
    symbol TEXT NOT NULL CHECK (
        symbol IN ('{ETF_PROXY_REQUIRED_SYMBOLS[0]}', '{ETF_PROXY_REQUIRED_SYMBOLS[1]}')
    ),
    attempt_id TEXT NOT NULL UNIQUE,
    request_semantics_sha256 TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    raw_bytes INTEGER NOT NULL,
    normalized_sha256 TEXT NOT NULL,
    row_count INTEGER NOT NULL,
    PRIMARY KEY (generation_id, symbol),
    FOREIGN KEY (generation_id)
        REFERENCES etf_proxy_generations(generation_id),
    FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
);
CREATE TABLE IF NOT EXISTS etf_proxy_generation_rows (
    generation_id TEXT NOT NULL,
    ts_code TEXT NOT NULL CHECK (
        ts_code IN ('{ETF_PROXY_REQUIRED_SYMBOLS[0]}', '{ETF_PROXY_REQUIRED_SYMBOLS[1]}')
    ),
    trade_date TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    pre_close REAL NOT NULL,
    change REAL NOT NULL,
    pct_chg REAL NOT NULL,
    vol REAL NOT NULL,
    amount REAL NOT NULL,
    PRIMARY KEY (generation_id, ts_code, trade_date),
    FOREIGN KEY (generation_id, ts_code)
        REFERENCES etf_proxy_generation_shards(generation_id, symbol)
);
CREATE TABLE IF NOT EXISTS etf_proxy_generation_head (
    scope_key TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL,
    lineage_sha256 TEXT NOT NULL,
    published_at TEXT NOT NULL,
    FOREIGN KEY (generation_id)
        REFERENCES etf_proxy_generations(generation_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS one_collecting_etf_proxy_generation_per_scope
    ON etf_proxy_generations(scope_key)
    WHERE status = 'collecting';
"""
MARKET_SESSION_ROW_CAPS = {
    "daily": 6000,
    "adj_factor": 6000,
    "stk_limit": 5800,
    "suspend_d": 5000,
}
KNOWN_MARKET_EXCHANGES = {
    "主板": {"SSE", "SZSE"},
    "创业板": {"SZSE"},
    "科创板": {"SSE"},
    "CDR": {"SSE", "SZSE"},
    "北交所": {"BSE"},
}
NORMALIZED_FIELDS = {
    "stock_basic": STOCK_BASIC_FIELDS,
    "trade_cal": ("exchange", "cal_date", "is_open", "pretrade_date"),
    "bak_basic": ("trade_date", "ts_code", "name", "industry", "list_date"),
    "daily": (
        "ts_code",
        "trade_date",
        "open",
        "high",
        "low",
        "close",
        "pre_close",
        "change",
        "pct_chg",
        "vol",
        "amount",
    ),
    "adj_factor": ("ts_code", "trade_date", "adj_factor"),
    "stk_limit": (
        "trade_date",
        "ts_code",
        "pre_close",
        "up_limit",
        "down_limit",
    ),
    "suspend_d": ("ts_code", "trade_date", "suspend_timing", "suspend_type"),
    "fund_daily": FUND_DAILY_FIELDS,
}
PRIMARY_KEYS = {
    "stock_basic": ("ts_code",),
    "trade_cal": ("exchange", "cal_date"),
    "bak_basic": ("trade_date", "ts_code"),
    "daily": ("trade_date", "ts_code"),
    "adj_factor": ("trade_date", "ts_code"),
    "stk_limit": ("trade_date", "ts_code"),
    "suspend_d": ("trade_date", "ts_code", "suspend_timing", "suspend_type"),
    "fund_daily": ("ts_code", "trade_date"),
}
FULL_SNAPSHOT_PARAM_KEYS = {
    "stock_basic": {"exchange", "list_status"},
    "trade_cal": {"exchange", "start_date", "end_date"},
    "bak_basic": {"trade_date"},
    "daily": {"trade_date"},
    "adj_factor": {"trade_date"},
    "stk_limit": {"trade_date"},
    "suspend_d": {"trade_date"},
    "fund_daily": {"ts_code", "start_date", "end_date"},
}


class PITReceiptError(ValueError):
    pass


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


_CANONICAL_INT_STRING = re.compile(r"^[+-]?[0-9]+$")


def _require_int(value: Any, label: str) -> int:
    """Lossless integer validation of a stored column, fail-closed on corruption.

    WHY not bare ``int()``: ``int()`` silently truncates a type-corrupted column
    (``1.5`` -> ``1``, ``200.9`` -> ``200``, ``5000.9`` -> ``5000``), so a torn
    write that drifts an INTEGER-affinity column to a fractional REAL would pass
    every downstream equality / range check after truncation. sqlite INTEGER
    affinity stores ``1.5`` as REAL; it does not round it away, so we cannot
    rely on the schema. We accept only lossless integers -- true ``int``; a
    finite, integral ``float``; or a canonical digit string (optional sign +
    ASCII digits, nothing else) -- and reject ``bool``, non-finite / fractional
    floats, and any string carrying whitespace, a decimal point, or exponent
    notation. Any other shape (including ``"not-int"``) surfaces as a
    :class:`PITReceiptError`, never a leaked traceback or a silent truncation.
    """

    if isinstance(value, bool):
        raise PITReceiptError(f"{label} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or value != int(value):
            raise PITReceiptError(f"{label} must be an integer")
        return int(value)
    if isinstance(value, str):
        if not _CANONICAL_INT_STRING.match(value):
            raise PITReceiptError(f"{label} must be an integer")
        return int(value)
    raise PITReceiptError(f"{label} must be an integer")


def _coverage_verifier_contract_sha256(
    official_parser_versions: Iterable[str] = (),
) -> str:
    """Stable semantic identity; bump the audit schema when these rules change."""

    parser_versions = frozenset(str(value) for value in official_parser_versions)
    if not parser_versions or parser_versions == {CNINFO_SUSPENSION_PARSER_VERSION_V1}:
        official_parser_contract = {
            "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
            "parser_version": CNINFO_SUSPENSION_PARSER_VERSION_V1,
            "parser_code_sha256": CNINFO_SUSPENSION_PARSER_V1_CODE_SHA256,
            "source_profile": CNINFO_SOURCE_PROFILE,
            "interval_policy": "full_session_half_open_start_inclusive_resume_exclusive_v1",
            "use_policy": "explain_missing_daily_only_never_synthesize_market_rows_v1",
        }
    else:
        if not parser_versions.issubset(
            SUPPORTED_CNINFO_SUSPENSION_PARSER_VERSIONS
        ):
            raise PITReceiptError(
                "official suspension coverage uses an unsupported parser version"
            )
        official_parser_contract = {
            "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
            "parser_version": CNINFO_SUSPENSION_PARSER_VERSION,
            "supported_parser_versions": sorted(parser_versions),
            "parser_code_sha256": hashlib.sha256(
                _cninfo_parser_code_path().read_bytes()
            ).hexdigest(),
            "source_profile": CNINFO_SOURCE_PROFILE,
            "interval_policy": "full_session_half_open_start_inclusive_resume_exclusive_v1",
            "use_policy": "explain_missing_daily_only_never_synthesize_market_rows_v1",
        }

    return _sha256(
        {
            "audit_schema_version": COVERAGE_AUDIT_SCHEMA_VERSION,
            "store_schema_version": STORE_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "dataset_endpoints": DATASET_ENDPOINTS,
            "official_row_caps": OFFICIAL_ROW_CAPS,
            "normalized_fields": NORMALIZED_FIELDS,
            "primary_keys": PRIMARY_KEYS,
            "full_snapshot_param_keys": {
                key: sorted(value) for key, value in FULL_SNAPSHOT_PARAM_KEYS.items()
            },
            "exchanges": SUPPORTED_A_SHARE_EXCHANGES,
            "markets": SUPPORTED_A_SHARE_MARKETS,
            "statuses": REQUIRED_STOCK_STATUSES,
            "stock_generation_schema_version": STOCK_GENERATION_SCHEMA_VERSION,
            "stock_generation_contract_sha256": _stock_generation_contract_sha256(),
            "stock_generation_lineage_policy": (
                "exact_generation_head_shards_rows_attempts_events_v1"
            ),
            "lifecycle_policy": "list_date_inclusive_delist_date_exclusive_v1",
            "completeness_scope": "tushare_internal_lifecycle_reconciliation",
            "receipt_verification_scope": (
                "whole_store_plus_active_stock_per_session_market_and_official_suspension_fail_closed"
            ),
            "official_suspension_evidence": official_parser_contract,
        }
    )


def _stock_generation_contract_sha256() -> str:
    return _sha256(
        {
            "schema_version": STOCK_GENERATION_SCHEMA_VERSION,
            "scope": STOCK_GENERATION_SCOPE,
            "partitions": list(REQUIRED_STOCK_PARTITIONS),
            "fields": list(NORMALIZED_FIELDS["stock_basic"]),
            "row_cap": OFFICIAL_ROW_CAPS["stock_basic"],
            "window_seconds": STOCK_GENERATION_WINDOW_SECONDS,
            "parser_version": PARSER_VERSION,
        }
    )


def _market_session_contract_sha256() -> str:
    return _sha256(
        {
            "schema_version": MARKET_SESSION_GENERATION_SCHEMA_VERSION,
            "datasets": list(MARKET_SESSION_DATASETS),
            "nonempty_datasets": list(NONEMPTY_MARKET_SESSION_DATASETS),
            "fields": {
                dataset: list(NORMALIZED_FIELDS[dataset]) for dataset in MARKET_SESSION_DATASETS
            },
            "row_caps": dict(MARKET_SESSION_ROW_CAPS),
            "window_seconds": MARKET_SESSION_GENERATION_WINDOW_SECONDS,
            "parser_version": PARSER_VERSION,
        }
    )


def _etf_proxy_generation_scope_key(start_date: str, end_date: str) -> str:
    """Deterministic per-date-range scope; callers cannot supply a scope/symbol.

    WHY: the partial unique index ``one_collecting_etf_proxy_generation_per_scope``
    enforces one collecting generation per scope. Binding the scope to the frozen
    schema version, the two required proxy symbols (in fixed order), and the
    caller's date range makes each date range an independent scope while keeping
    identical ranges identical across processes.
    """

    return _sha256(
        {
            "schema_version": ETF_PROXY_GENERATION_SCHEMA_VERSION,
            "symbols": list(ETF_PROXY_REQUIRED_SYMBOLS),
            "start_date": start_date,
            "end_date": end_date,
        }
    )


def _etf_proxy_generation_contract_sha256(start_date: str, end_date: str) -> str:
    """Stable semantic identity for one ETF proxy date range.

    Same range -> same digest; a different range -> a different digest. Bumping
    the schema version, the required symbols, the fund_daily field set or row
    cap, or the collecting window changes every contract, so stale receipts can
    never be re-bound to a mutated lineage.
    """

    return _sha256(
        {
            "schema_version": ETF_PROXY_GENERATION_SCHEMA_VERSION,
            "symbols": list(ETF_PROXY_REQUIRED_SYMBOLS),
            "fields": list(FUND_DAILY_FIELDS),
            "row_cap": FUND_DAILY_ROW_CAP,
            "window_seconds": ETF_PROXY_GENERATION_WINDOW_SECONDS,
            "start_date": start_date,
            "end_date": end_date,
            "parser_version": PARSER_VERSION,
        }
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_sha256(value: Any, field: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 64 or any(character not in "0123456789abcdef" for character in text):
        raise PITReceiptError(f"{field} is not a valid SHA-256 digest")
    return text


def _stream_rows_sha256(rows: Iterable[Mapping[str, Any]]) -> Tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for row in rows:
        digest.update(_canonical_json(dict(row)).encode("utf-8"))
        digest.update(b"\n")
        count += 1
    return digest.hexdigest(), count


def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError as exc:
        raise PITReceiptError(f"invalid {field}: {value!r}") from exc


def _optional_iso_date(value: Any, field: str) -> Any:
    if value is None or str(value).strip() in {"", "None", "NaT", "nan"}:
        return None
    return _iso_date(value, field)


def _finite_number(
    value: Any, field: str, *, positive: bool = False, nonnegative: bool = False
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise PITReceiptError(f"{field} is not numeric") from exc
    if not math.isfinite(number):
        raise PITReceiptError(f"{field} is not finite")
    if positive and number <= 0:
        raise PITReceiptError(f"{field} must be positive")
    if nonnegative and number < 0:
        raise PITReceiptError(f"{field} must be nonnegative")
    return number


def _has_valid_expected_limit_interval(
    up_limit: Any, down_limit: Any, reference_close: Any
) -> bool:
    """Fail closed for executable-market evidence read from SQLite.

    SQLite accepts IEEE infinity as REAL and legacy schemas may have weaker
    constraints, so normalization and DDL checks are not sufficient proof at
    audit time.
    """

    try:
        up = float(up_limit)
        down = float(down_limit)
        reference = float(reference_close)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(up)
        and math.isfinite(down)
        and math.isfinite(reference)
        and up > 0
        and down > 0
        and down < reference < up
    )


def _market_ts_code(value: Any, dataset: str) -> str:
    ts_code = str(value or "").strip().upper()
    symbol, separator, suffix = ts_code.partition(".")
    if (
        len(symbol) != 6
        or not symbol.isdigit()
        or separator != "."
        or suffix not in {"SH", "SZ", "BJ"}
    ):
        raise PITReceiptError(f"invalid {dataset} ts_code: {ts_code!r}")
    return ts_code


def _aware_timestamp(value: Any) -> str:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise PITReceiptError(f"invalid retrieved_at: {value!r}") from exc
    if parsed.tzinfo is None:
        raise PITReceiptError("retrieved_at must include a timezone")
    return parsed.isoformat()


def _normalize_request_params(dataset: str, params: Mapping[str, Any]) -> Dict[str, Any]:
    if set(params) != FULL_SNAPSHOT_PARAM_KEYS[dataset]:
        raise PITReceiptError(
            f"{dataset} requires exact full-snapshot parameters: "
            + ", ".join(sorted(FULL_SNAPSHOT_PARAM_KEYS[dataset]))
        )
    if dataset == "stock_basic":
        return {
            "exchange": str(params["exchange"]).strip().upper(),
            "list_status": str(params["list_status"]).strip().upper(),
        }
    if dataset == "trade_cal":
        return {
            "exchange": str(params["exchange"]).strip().upper(),
            "start_date": _iso_date(params["start_date"], "trade_cal start_date"),
            "end_date": _iso_date(params["end_date"], "trade_cal end_date"),
        }
    if dataset == "fund_daily":
        ts_code = str(params["ts_code"]).strip().upper()
        if not ts_code:
            raise PITReceiptError("fund_daily ts_code is missing")
        # Pin the symbol to the frozen ETF proxy contract BEFORE any wire call:
        # a format-valid but out-of-contract ETF (e.g. 510050.SH) must never
        # reach the network — it is rejected at param normalisation, not later
        # at response normalisation, so no stray symbol can be fetched.
        if ts_code not in ETF_PROXY_REQUIRED_SYMBOLS:
            raise PITReceiptError(
                f"fund_daily ts_code {ts_code!r} is not a required ETF proxy "
                f"symbol {list(ETF_PROXY_REQUIRED_SYMBOLS)}"
            )
        start = _iso_date(params["start_date"], "fund_daily start_date")
        end = _iso_date(params["end_date"], "fund_daily end_date")
        if end < start:
            raise PITReceiptError("fund_daily end_date precedes start_date")
        return {"ts_code": ts_code, "start_date": start, "end_date": end}
    if dataset in {"bak_basic", "daily", "adj_factor", "stk_limit", "suspend_d"}:
        return {"trade_date": _iso_date(params["trade_date"], f"{dataset} trade_date")}
    raise PITReceiptError(f"unsupported dataset: {dataset}")


def _canonical_wire_params(dataset: str, params: Mapping[str, Any]) -> Dict[str, str]:
    """Return the only wire query that can represent a full receipt partition."""

    normalized = _normalize_request_params(dataset, params)
    if dataset == "stock_basic":
        return {
            "exchange": normalized["exchange"],
            "list_status": normalized["list_status"],
        }
    if dataset == "trade_cal":
        return {
            "exchange": normalized["exchange"],
            "start_date": normalized["start_date"].replace("-", ""),
            "end_date": normalized["end_date"].replace("-", ""),
        }
    if dataset == "fund_daily":
        return {
            "ts_code": normalized["ts_code"],
            "start_date": normalized["start_date"].replace("-", ""),
            "end_date": normalized["end_date"].replace("-", ""),
        }
    return {"trade_date": normalized["trade_date"].replace("-", "")}


def _canonical_partition_key(dataset: str, params: Mapping[str, Any]) -> str:
    normalized = _normalize_request_params(dataset, params)
    if dataset == "stock_basic":
        return f"{normalized['exchange']}:{normalized['list_status']}"
    if dataset == "trade_cal":
        return f"{normalized['exchange']}:{normalized['start_date']}:{normalized['end_date']}"
    if dataset == "fund_daily":
        return f"{normalized['ts_code']}:{normalized['start_date']}:{normalized['end_date']}"
    return normalized["trade_date"]


def _reject_duplicate_object_keys(pairs: Sequence[Tuple[str, Any]]) -> Dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PITReceiptError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _parse_native_envelope(raw_bytes: bytes) -> Tuple[List[str], List[List[Any]], int, str]:
    try:
        payload = json.loads(
            raw_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_object_keys
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PITReceiptError("invalid native JSON response") from exc
    if not isinstance(payload, dict):
        raise PITReceiptError("native response must be an object")
    try:
        code = int(payload.get("code"))
    except (TypeError, ValueError) as exc:
        raise PITReceiptError("native response code is invalid") from exc
    message = str(payload.get("msg") or "")
    if code != 0:
        raise PITReceiptError(f"native response code {code}: {message}")
    if message.strip() not in {"", "success"}:
        raise PITReceiptError(f"native response message is not empty: {message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise PITReceiptError("native response data is missing")
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise PITReceiptError("native response fields are invalid")
    if len(fields) != len(set(fields)):
        raise PITReceiptError("native response has duplicate fields")
    if not isinstance(items, list):
        raise PITReceiptError("native response items are invalid")
    for item in items:
        if not isinstance(item, list) or len(item) != len(fields):
            raise PITReceiptError("native response item field width mismatch")
    return fields, items, code, message


def validate_fund_daily_attempt(
    raw_bytes: bytes,
    params: Mapping[str, Any],
    row_cap: int,
) -> Dict[str, Any]:
    """Read-only body classification for a captured ``fund_daily`` attempt.

    Pure: no store, no DB, no mutation. The future generation stage calls this
    to decide whether a captured raw body is a complete, in-contract ETF proxy
    session (``valid``), reached the single-call row cap and is therefore
    non-promotable as a standalone session (``cap_reached``), or is malformed
    (``invalid_body``). It deliberately mirrors the receipt-side
    ``_normalize_rows`` contract so the generation stage and the receipt
    boundary cannot drift apart. ``record_fetch_attempt`` itself only captures
    the body and must NOT parse it — this helper is the only authority on body
    shape until the generation stage exists.
    """

    try:
        fields, items, _code, _message = _parse_native_envelope(raw_bytes)
    except PITReceiptError as exc:
        return {"status": "invalid_body", "reason": str(exc), "row_count": 0}
    if tuple(fields) != FUND_DAILY_FIELDS:
        return {
            "status": "invalid_body",
            "reason": (
                "fund_daily fields must exactly match the frozen contract "
                "order: " + ", ".join(FUND_DAILY_FIELDS)
            ),
            "row_count": len(items),
        }
    row_count = len(items)
    # A full page hit the tushare single-call cap: the session cannot be trusted
    # to be complete, so it is non-promotable. Checked BEFORE row validation to
    # match the receipt-side ordering in ingest_tushare_response.
    if row_count >= int(row_cap):
        return {"status": "cap_reached", "row_count": row_count}
    partition_key = _canonical_partition_key("fund_daily", params)
    try:
        _normalize_rows("fund_daily", partition_key, params, fields, items)
    except PITReceiptError as exc:
        return {"status": "invalid_body", "reason": str(exc), "row_count": row_count}
    return {"status": "valid", "row_count": row_count}


def _normalize_rows(
    dataset: str,
    partition_key: str,
    params: Mapping[str, Any],
    fields: Sequence[str],
    items: Sequence[Sequence[Any]],
) -> List[Dict[str, Any]]:
    if dataset not in NORMALIZED_FIELDS:
        raise PITReceiptError(f"unsupported normalized dataset: {dataset}")
    required = NORMALIZED_FIELDS[dataset]
    missing = [field for field in required if field not in fields]
    if missing:
        raise PITReceiptError("native response missing required fields: " + ", ".join(missing))
    if dataset == "fund_daily":
        # fund_daily reuses the frozen ETF proxy contract: one declared symbol
        # per receipt, raw OHLC only, exact field set, window-bounded, deduped.
        # Validation is cross-row (sort + dedup), so it cannot share the
        # per-item loop below; EtfProxyError is translated to keep the receipt
        # boundary error type stable.
        #
        # The native response field header must declare fields in the EXACT
        # frozen contract order. Items are positional, so a reordered-but-
        # complete header would silently relabel columns if only the set
        # matched (the generic missing-field check above is set-based); reject
        # any order drift here.
        if tuple(fields) != FUND_DAILY_FIELDS:
            raise PITReceiptError(
                "fund_daily fields must exactly match the frozen contract "
                "order: " + ", ".join(FUND_DAILY_FIELDS)
            )
        # An empty items payload is never an acceptable proxy session: returning
        # [] would mask a missing session as "legitimately empty" and let a
        # coverage gap slip through. Reject explicitly — the coverage audit is
        # the only authority that may reason about session absence.
        if not items:
            raise PITReceiptError("fund_daily items must be non-empty")
        expected_symbol = str(params.get("ts_code") or "").strip().upper()
        window_start = _iso_date(params.get("start_date"), "fund_daily start_date")
        window_end = _iso_date(params.get("end_date"), "fund_daily end_date")
        proxy_input = [dict(zip(fields, values)) for values in items]
        try:
            return normalize_etf_proxy_rows(proxy_input, expected_symbol, window_start, window_end)
        except EtfProxyError as exc:
            raise PITReceiptError(f"fund_daily normalisation failed: {exc}") from exc
    normalized = []
    keys = set()
    for values in items:
        raw = dict(zip(fields, values))
        if dataset == "stock_basic":
            ts_code = str(raw.get("ts_code") or "").strip().upper()
            symbol = str(raw.get("symbol") or "").strip()
            exchange = str(raw.get("exchange") or "").strip().upper()
            status = str(raw.get("list_status") or "").strip().upper()
            normal_identifier = len(symbol) == 6 and symbol.isdigit()
            legacy_identifier = bool(re.fullmatch(r"T(?:\d{6}|S\d{4})", symbol))
            if (not normal_identifier and not legacy_identifier) or "." not in ts_code:
                raise PITReceiptError(f"invalid stock_basic identifier: {ts_code!r}")
            if symbol != ts_code.split(".", 1)[0]:
                raise PITReceiptError(f"stock_basic symbol conflicts with ts_code: {ts_code}")
            suffix_exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(
                ts_code.rsplit(".", 1)[-1]
            )
            if exchange != suffix_exchange:
                raise PITReceiptError(f"stock_basic exchange conflicts with ts_code: {ts_code}")
            requested_status = str(params.get("list_status") or "").strip().upper()
            requested_exchange = str(params.get("exchange") or "").strip().upper()
            if status != requested_status or exchange != requested_exchange:
                raise PITReceiptError("stock_basic row does not match request shard")
            market = str(raw.get("market") or "").strip()
            if legacy_identifier and not market:
                market = "历史遗留"
            if not legacy_identifier and (
                market not in KNOWN_MARKET_EXCHANGES
                or exchange not in KNOWN_MARKET_EXCHANGES[market]
            ):
                raise PITReceiptError(
                    f"stock_basic market/exchange combination is unsupported: {market}/{exchange}"
                )
            listed_on = (
                _optional_iso_date(raw.get("list_date"), "list_date")
                if status == "G"
                else _iso_date(raw.get("list_date"), "list_date")
            )
            delisted_on = _optional_iso_date(raw.get("delist_date"), "delist_date")
            if legacy_identifier and (
                status != "D" or not delisted_on or delisted_on >= "2016-01-01"
            ):
                raise PITReceiptError(
                    f"legacy stock_basic identifier is outside frozen history: {ts_code}"
                )
            if status == "D" and not delisted_on:
                raise PITReceiptError(f"delisted security requires delist_date: {ts_code}")
            if status in {"P", "G"} and delisted_on:
                raise PITReceiptError(
                    f"non-delisted security may not declare delist_date: {ts_code}"
                )
            if delisted_on and listed_on and delisted_on < listed_on:
                raise PITReceiptError(f"delist_date precedes list_date: {ts_code}")
            row = {
                "ts_code": ts_code,
                "symbol": symbol,
                "name": str(raw.get("name") or "").strip(),
                "exchange": exchange,
                "market": market,
                "list_status": status,
                "list_date": listed_on,
                "delist_date": delisted_on,
            }
            if not row["name"] or not row["market"]:
                raise PITReceiptError(f"stock_basic name/market is missing: {ts_code}")
        elif dataset == "trade_cal":
            exchange = str(raw.get("exchange") or "").strip().upper()
            requested_exchange = str(params.get("exchange") or "").strip().upper()
            if exchange != requested_exchange:
                raise PITReceiptError("trade_cal row does not match requested exchange")
            try:
                is_open = int(raw.get("is_open"))
            except (TypeError, ValueError) as exc:
                raise PITReceiptError("trade_cal is_open is invalid") from exc
            if is_open not in {0, 1}:
                raise PITReceiptError("trade_cal is_open must be 0 or 1")
            calendar_date = _iso_date(raw.get("cal_date"), "cal_date")
            requested_start = _iso_date(params.get("start_date"), "trade_cal start_date")
            requested_end = _iso_date(params.get("end_date"), "trade_cal end_date")
            if not requested_start <= calendar_date <= requested_end:
                raise PITReceiptError("trade_cal row is outside requested date range")
            row = {
                "exchange": exchange,
                "cal_date": calendar_date,
                "is_open": is_open,
                "pretrade_date": _optional_iso_date(raw.get("pretrade_date"), "pretrade_date"),
            }
        elif dataset == "bak_basic":
            session = _iso_date(raw.get("trade_date"), "trade_date")
            expected_session = _iso_date(partition_key, "partition_key")
            requested_session = _iso_date(params.get("trade_date"), "trade_date param")
            if session != expected_session or session != requested_session:
                raise PITReceiptError("bak_basic row trade_date does not match request")
            ts_code = str(raw.get("ts_code") or "").strip().upper()
            name = str(raw.get("name") or "").strip()
            symbol, separator, suffix = ts_code.partition(".")
            exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}.get(suffix)
            if (
                len(symbol) != 6
                or not symbol.isdigit()
                or not separator
                or not exchange
                or not name
            ):
                raise PITReceiptError("bak_basic identifier/name is invalid")
            row = {
                "trade_date": session,
                "ts_code": ts_code,
                "exchange": exchange,
                "name": name,
                "industry": str(raw.get("industry") or "").strip() or None,
                "list_date": (
                    None
                    if str(raw.get("list_date") or "").strip() in {"", "0"}
                    else _iso_date(raw.get("list_date"), "list_date")
                ),
            }
        elif dataset in {"daily", "adj_factor", "stk_limit", "suspend_d"}:
            session = _iso_date(raw.get("trade_date"), f"{dataset} trade_date")
            expected_session = _iso_date(partition_key, "partition_key")
            requested_session = _iso_date(params.get("trade_date"), f"{dataset} trade_date param")
            if session != expected_session or session != requested_session:
                raise PITReceiptError(f"{dataset} row trade_date does not match request")
            ts_code = _market_ts_code(raw.get("ts_code"), dataset)
            suffix_exchange = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}[
                ts_code.rsplit(".", 1)[-1]
            ]
            in_scope_market_code = _is_supported_a_share_snapshot_code(ts_code, suffix_exchange)
            if dataset == "daily":
                try:
                    prices = {
                        field: _finite_number(raw.get(field), f"daily {field}", positive=True)
                        for field in ("open", "high", "low", "close", "pre_close")
                    }
                    if prices["high"] < max(prices["open"], prices["close"]):
                        raise PITReceiptError("daily high is below open or close")
                    if prices["low"] > min(prices["open"], prices["close"]):
                        raise PITReceiptError("daily low is above open or close")
                    row = {
                        "trade_date": session,
                        "ts_code": ts_code,
                        **prices,
                        "change": _finite_number(raw.get("change"), "daily change"),
                        "pct_chg": _finite_number(raw.get("pct_chg"), "daily pct_chg"),
                        "vol": _finite_number(raw.get("vol"), "daily vol", nonnegative=True),
                        "amount": _finite_number(
                            raw.get("amount"), "daily amount", nonnegative=True
                        ),
                    }
                except PITReceiptError:
                    if not in_scope_market_code:
                        continue
                    raise
            elif dataset == "adj_factor":
                row = {
                    "trade_date": session,
                    "ts_code": ts_code,
                    "adj_factor": _finite_number(
                        raw.get("adj_factor"), "adj_factor", positive=True
                    ),
                }
            elif dataset == "stk_limit":
                # pre_close 是涨跌停参考价,jiaoch 对新股首日/复牌等场景可能返回 NaN/inf。
                # 缺失或非有限时降级为 None(涨跌停区间验证 _has_valid_expected_limit_interval
                # 会返回 False,下游保守处理),不 fail-closed。
                _raw_pre_close = raw.get("pre_close")
                try:
                    pre_close = (
                        None
                        if _raw_pre_close in {None, ""}
                        or not math.isfinite(float(_raw_pre_close))
                        else float(_raw_pre_close)
                    )
                except (TypeError, ValueError):
                    pre_close = None
                up_limit = _finite_number(
                    raw.get("up_limit"), "stk_limit up_limit", nonnegative=True
                )
                down_limit = _finite_number(
                    raw.get("down_limit"), "stk_limit down_limit", nonnegative=True
                )
                # Provider bulk responses can contain out-of-scope instruments
                # whose sentinel limit is zero. Preserve those rows so the PIT
                # scope reconciliation can exclude them, while retaining the
                # stronger interval check for ordinary positive-limit rows.
                if (
                    up_limit > 0
                    and down_limit > 0
                    and (
                        not down_limit < up_limit
                        or (pre_close is not None and not down_limit < pre_close < up_limit)
                    )
                ):
                    raise PITReceiptError("stk_limit limit interval is invalid")
                row = {
                    "trade_date": session,
                    "ts_code": ts_code,
                    "pre_close": pre_close,
                    "up_limit": up_limit,
                    "down_limit": down_limit,
                }
            else:
                suspend_type = str(raw.get("suspend_type") or "").strip().upper()
                if suspend_type not in {"S", "R"}:
                    raise PITReceiptError("suspend_d suspend_type must be S or R")
                row = {
                    "trade_date": session,
                    "ts_code": ts_code,
                    "suspend_timing": str(raw.get("suspend_timing") or "").strip(),
                    "suspend_type": suspend_type,
                }
        else:
            raise PITReceiptError(f"unsupported normalized dataset: {dataset}")
        key = tuple(row[field] for field in PRIMARY_KEYS[dataset])
        if key in keys:
            raise PITReceiptError(f"duplicate normalized key for {dataset}: {key}")
        keys.add(key)
        normalized.append(row)
    if dataset in {"daily", "adj_factor", "stk_limit"} and not normalized:
        raise PITReceiptError(f"{dataset} full-session response is empty")
    return sorted(normalized, key=lambda row: tuple(row[field] for field in PRIMARY_KEYS[dataset]))


class PITReceiptStore:
    def __init__(self, root: str) -> None:
        self.root = Path(root).resolve()
        self.raw_root = self.root / "raw"
        self.database_path = self.root / "metadata.sqlite3"
        self.raw_root.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(str(self.database_path), timeout=30)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS store_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fetch_attempts (
                    attempt_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL UNIQUE,
                    dataset TEXT NOT NULL,
                    partition_key TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    fields_json TEXT NOT NULL,
                    request_semantics_json TEXT NOT NULL,
                    request_semantics_sha256 TEXT NOT NULL,
                    wire_request_sha256 TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    started_at TEXT,
                    retrieved_at TEXT,
                    elapsed_ns INTEGER NOT NULL,
                    row_cap INTEGER NOT NULL,
                    http_status INTEGER,
                    response_headers_json TEXT NOT NULL,
                    clock_attestation_json TEXT NOT NULL,
                    body_complete INTEGER NOT NULL,
                    raw_path TEXT,
                    raw_sha256 TEXT,
                    raw_bytes INTEGER NOT NULL,
                    outcome TEXT NOT NULL CHECK (outcome = 'captured'),
                    error_kind TEXT,
                    error_message TEXT,
                    recorded_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fetch_promotion_events (
                    event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_id TEXT NOT NULL UNIQUE,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
                );
                CREATE TABLE IF NOT EXISTS receipts (
                    dataset TEXT NOT NULL,
                    partition_key TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    raw_path TEXT NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    response_code INTEGER NOT NULL,
                    response_message TEXT NOT NULL,
                    row_cap INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    normalized_sha256 TEXT NOT NULL,
                    parser_version TEXT NOT NULL,
                    PRIMARY KEY (dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS security_master (
                    ts_code TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    market TEXT NOT NULL,
                    list_status TEXT NOT NULL,
                    list_date TEXT,
                    delist_date TEXT,
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'stock_basic'),
                    receipt_partition TEXT NOT NULL,
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS trade_sessions (
                    exchange TEXT NOT NULL,
                    cal_date TEXT NOT NULL,
                    is_open INTEGER NOT NULL,
                    pretrade_date TEXT,
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'trade_cal'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (exchange, cal_date),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS daily_universe (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    name TEXT NOT NULL,
                    industry TEXT,
                    list_date TEXT,
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'bak_basic'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (trade_date, ts_code),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS raw_daily_bars (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    open REAL NOT NULL,
                    high REAL NOT NULL,
                    low REAL NOT NULL,
                    close REAL NOT NULL,
                    pre_close REAL,
                    change REAL NOT NULL,
                    pct_chg REAL NOT NULL,
                    vol REAL NOT NULL,
                    amount REAL NOT NULL,
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'daily'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (trade_date, ts_code),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS adjustment_factors (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    adj_factor REAL NOT NULL,
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'adj_factor'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (trade_date, ts_code),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS daily_price_limits (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    pre_close REAL,
                    up_limit REAL NOT NULL CHECK (up_limit >= 0),
                    down_limit REAL NOT NULL CHECK (down_limit >= 0),
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'stk_limit'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (trade_date, ts_code),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS suspension_events (
                    trade_date TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    suspend_timing TEXT NOT NULL,
                    suspend_type TEXT NOT NULL CHECK (suspend_type IN ('S', 'R')),
                    receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'suspend_d'),
                    receipt_partition TEXT NOT NULL,
                    PRIMARY KEY (
                        trade_date, ts_code, suspend_type, suspend_timing
                    ),
                    FOREIGN KEY (receipt_dataset, receipt_partition)
                        REFERENCES receipts(dataset, partition_key)
                );
                CREATE TABLE IF NOT EXISTS stock_basic_generations (
                    generation_sequence INTEGER PRIMARY KEY,
                    generation_id TEXT NOT NULL UNIQUE,
                    scope_key TEXT NOT NULL,
                    contract_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (
                        status IN ('collecting', 'published', 'abandoned')
                    ),
                    started_at TEXT NOT NULL,
                    terminal_at TEXT,
                    terminal_reason TEXT,
                    manifest_sha256 TEXT,
                    expected_request_semantics_json TEXT,
                    expected_request_semantics_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS stock_basic_generation_shards (
                    generation_id TEXT NOT NULL,
                    logical_partition_key TEXT NOT NULL,
                    attempt_id TEXT NOT NULL UNIQUE,
                    request_semantics_sha256 TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    raw_sha256 TEXT NOT NULL,
                    raw_bytes INTEGER NOT NULL,
                    normalized_sha256 TEXT NOT NULL,
                    row_count INTEGER NOT NULL,
                    PRIMARY KEY (generation_id, logical_partition_key),
                    FOREIGN KEY (generation_id)
                        REFERENCES stock_basic_generations(generation_id),
                    FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
                );
                CREATE TABLE IF NOT EXISTS stock_basic_generation_rows (
                    generation_id TEXT NOT NULL,
                    logical_partition_key TEXT NOT NULL,
                    ts_code TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    name TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    market TEXT NOT NULL,
                    list_status TEXT NOT NULL,
                    list_date TEXT,
                    delist_date TEXT,
                    PRIMARY KEY (generation_id, logical_partition_key, ts_code),
                    FOREIGN KEY (generation_id, logical_partition_key)
                        REFERENCES stock_basic_generation_shards(
                            generation_id, logical_partition_key
                        )
                );
                CREATE TABLE IF NOT EXISTS stock_basic_generation_head (
                    scope_key TEXT PRIMARY KEY,
                    generation_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    FOREIGN KEY (generation_id)
                        REFERENCES stock_basic_generations(generation_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_collecting_stock_generation_per_scope
                    ON stock_basic_generations(scope_key)
                    WHERE status = 'collecting';
                """
                + _MARKET_SESSION_GENERATION_SCHEMA_SQL
                + _OFFICIAL_SUSPENSION_SCHEMA_SQL
                + _MEMBERSHIP_GENERATION_SCHEMA_SQL
                + _ETF_PROXY_GENERATION_SCHEMA_SQL
                + """
                CREATE INDEX IF NOT EXISTS idx_security_master_receipt
                    ON security_master(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_trade_sessions_receipt
                    ON trade_sessions(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_daily_universe_receipt
                    ON daily_universe(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_raw_daily_bars_receipt
                    ON raw_daily_bars(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_adjustment_factors_receipt
                    ON adjustment_factors(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_daily_price_limits_receipt
                    ON daily_price_limits(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_suspension_events_receipt
                    ON suspension_events(receipt_partition);
                CREATE INDEX IF NOT EXISTS idx_fetch_attempt_partition
                    ON fetch_attempts(dataset, partition_key, attempt_sequence);
                CREATE INDEX IF NOT EXISTS idx_fetch_attempt_request
                    ON fetch_attempts(request_semantics_sha256, wire_request_sha256);
                """
            )
            for table in ("stock_basic_generations", "market_session_generations"):
                available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "expected_request_semantics_json" not in available:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN expected_request_semantics_json TEXT"
                    )
                if "expected_request_semantics_sha256" not in available:
                    connection.execute(
                        f"ALTER TABLE {table} ADD COLUMN expected_request_semantics_sha256 TEXT"
                    )
            required_columns = {
                "receipts": {"http_status", "normalized_sha256", "parser_version"},
                "security_master": {"receipt_dataset", "receipt_partition", "list_date"},
                "trade_sessions": {"receipt_dataset", "receipt_partition"},
                "daily_universe": {
                    "exchange",
                    "list_date",
                    "receipt_dataset",
                    "receipt_partition",
                },
                "raw_daily_bars": {
                    "trade_date",
                    "ts_code",
                    "open",
                    "close",
                    "receipt_dataset",
                    "receipt_partition",
                },
                "adjustment_factors": {
                    "trade_date",
                    "ts_code",
                    "adj_factor",
                    "receipt_dataset",
                    "receipt_partition",
                },
                "daily_price_limits": {
                    "trade_date",
                    "ts_code",
                    "up_limit",
                    "down_limit",
                    "receipt_dataset",
                    "receipt_partition",
                },
                "suspension_events": {
                    "trade_date",
                    "ts_code",
                    "suspend_type",
                    "suspend_timing",
                    "receipt_dataset",
                    "receipt_partition",
                },
                "fetch_attempts": {
                    "attempt_id",
                    "request_semantics_sha256",
                    "wire_request_sha256",
                    "raw_sha256",
                    "body_complete",
                },
                "fetch_promotion_events": {"attempt_id", "status", "details_json"},
                "stock_basic_generations": {
                    "generation_id",
                    "scope_key",
                    "status",
                    "manifest_sha256",
                    "expected_request_semantics_json",
                    "expected_request_semantics_sha256",
                },
                "stock_basic_generation_shards": {
                    "generation_id",
                    "logical_partition_key",
                    "attempt_id",
                    "normalized_sha256",
                },
                "stock_basic_generation_rows": {
                    "generation_id",
                    "logical_partition_key",
                    "ts_code",
                },
                "stock_basic_generation_head": {
                    "scope_key",
                    "generation_id",
                    "manifest_sha256",
                },
                "market_session_generations": {
                    "generation_id",
                    "trade_date",
                    "status",
                    "vintage",
                    "final_oos_eligible",
                    "manifest_sha256",
                    "lineage_sha256",
                    "expected_request_semantics_json",
                    "expected_request_semantics_sha256",
                },
                "market_session_generation_shards": {
                    "generation_id",
                    "dataset",
                    "attempt_id",
                    "normalized_sha256",
                },
                "market_session_generation_rows_daily": {
                    "generation_id",
                    "trade_date",
                    "ts_code",
                },
                "market_session_generation_rows_adj_factor": {
                    "generation_id",
                    "trade_date",
                    "ts_code",
                },
                "market_session_generation_rows_stk_limit": {
                    "generation_id",
                    "trade_date",
                    "ts_code",
                },
                "market_session_generation_rows_suspend_d": {
                    "generation_id",
                    "trade_date",
                    "ts_code",
                },
                "market_session_generation_head": {
                    "trade_date",
                    "generation_id",
                    "manifest_sha256",
                },
                "official_notice_receipts": {
                    "receipt_id",
                    "source_profile",
                    "source_url",
                    "published_at",
                    "retrieved_at",
                    "notice_role",
                    "ts_code",
                    "effective_date",
                    "raw_path",
                    "raw_sha256",
                    "raw_bytes",
                    "text_sha256",
                    "normalized_sha256",
                    "parser_version",
                },
                "official_suspension_intervals": {
                    "interval_id",
                    "ts_code",
                    "start_date",
                    "resume_date",
                    "start_receipt_id",
                    "resume_receipt_id",
                    "evidence_root_sha256",
                    "recorded_at",
                },
                "membership_session_generations": {
                    "generation_id",
                    "trade_date",
                    "policy_version",
                    "status",
                    "source_kind",
                    "signal_session_eligible",
                    "temporal_role",
                    "temporal_contract_sha256",
                    "source_authority_json",
                    "source_authority_sha256",
                    "market_generation_id",
                    "market_manifest_sha256",
                    "market_lineage_sha256",
                    "manifest_json",
                    "manifest_sha256",
                    "lineage_sha256",
                },
                "membership_generation_rows": {
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "membership_present",
                    "unknown_metadata",
                    "signal_eligible",
                },
                "membership_generation_evidence": {
                    "generation_id",
                    "evidence_kind",
                    "attempt_id",
                    "raw_sha256",
                    "request_semantics_sha256",
                    "terminal_status",
                },
                "membership_session_head": {
                    "trade_date",
                    "generation_id",
                    "manifest_sha256",
                    "lineage_sha256",
                },
            }
            for table, required in required_columns.items():
                available = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                missing = sorted(required - available)
                if missing:
                    raise PITReceiptError(
                        f"incompatible receipt store schema for {table}; rebuild store: {missing}"
                    )
            limit_columns = {
                row[1]: row
                for row in connection.execute(
                    "PRAGMA table_info(market_session_generation_rows_stk_limit)"
                )
            }
            if int(limit_columns["pre_close"][3]) != 0:
                raise PITReceiptError(
                    "incompatible market_session_generation_rows_stk_limit "
                    "pre_close nullability; rebuild store"
                )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            self._migrate_published_generation_pins(connection)
            stored_version = connection.execute(
                "SELECT value FROM store_metadata WHERE key = 'schema_version'"
            ).fetchone()
            if stored_version is not None and stored_version[0] not in {
                STORE_SCHEMA_VERSION,
                *LEGACY_STORE_SCHEMA_VERSIONS,
            }:
                raise PITReceiptError(
                    f"unsupported receipt store schema version: {stored_version[0]}"
                )
            connection.execute(
                """
                INSERT INTO store_metadata (key, value) VALUES ('schema_version', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (STORE_SCHEMA_VERSION,),
            )
            connection.execute("PRAGMA user_version = 4")
            self._recover_orphan_promotions_on_connection(connection)

    def _migrate_published_generation_pins(self, connection: sqlite3.Connection) -> None:
        records = []
        for row in connection.execute(
            "SELECT * FROM stock_basic_generations "
            "WHERE status = 'published' AND expected_request_semantics_sha256 IS NULL"
        ):
            generation = dict(row)
            shards = list(
                connection.execute(
                    "SELECT logical_partition_key, request_semantics_sha256 "
                    "FROM stock_basic_generation_shards WHERE generation_id = ?",
                    (generation["generation_id"],),
                )
            )
            pin = {str(shard[0]): str(shard[1]) for shard in shards}
            encoded, digest = self._expected_semantics_pin(
                pin, _sha256(pin), REQUIRED_STOCK_PARTITIONS
            )
            verified = self._stock_generation_manifest_on_connection(
                connection,
                generation["generation_id"],
                expected_semantics_override=pin,
            )
            legacy_payload = dict(verified["manifest"])
            legacy_payload.pop("expected_request_semantics", None)
            legacy_payload.pop("expected_request_semantics_sha256", None)
            legacy_manifest_sha256 = _sha256(legacy_payload)
            head = connection.execute(
                "SELECT generation_id, manifest_sha256 "
                "FROM stock_basic_generation_head WHERE scope_key = ?",
                (generation["scope_key"],),
            ).fetchone()
            if not hmac.compare_digest(
                str(generation["manifest_sha256"]), legacy_manifest_sha256
            ) or (
                head is not None
                and str(head["generation_id"]) == generation["generation_id"]
                and not hmac.compare_digest(str(head["manifest_sha256"]), legacy_manifest_sha256)
            ):
                raise PITReceiptError("legacy stock generation manifest mismatch")
            connection.execute(
                "UPDATE stock_basic_generations SET "
                "expected_request_semantics_json = ?, "
                "expected_request_semantics_sha256 = ? WHERE generation_id = ?",
                (encoded, digest, generation["generation_id"]),
            )
            verified = self._stock_generation_manifest_on_connection(
                connection, generation["generation_id"]
            )
            connection.execute(
                "UPDATE stock_basic_generations SET manifest_sha256 = ? WHERE generation_id = ?",
                (verified["manifest_sha256"], generation["generation_id"]),
            )
            if head is not None and str(head["generation_id"]) == generation["generation_id"]:
                connection.execute(
                    "UPDATE stock_basic_generation_head SET manifest_sha256 = ? "
                    "WHERE generation_id = ?",
                    (verified["manifest_sha256"], generation["generation_id"]),
                )
            records.append(
                {
                    "kind": "stock_basic",
                    "generation_id": generation["generation_id"],
                    "old_manifest_sha256": generation["manifest_sha256"],
                    "new_manifest_sha256": verified["manifest_sha256"],
                    "expected_request_semantics_sha256": digest,
                }
            )
        for row in connection.execute(
            "SELECT * FROM market_session_generations "
            "WHERE status = 'published' AND expected_request_semantics_sha256 IS NULL"
        ):
            generation = dict(row)
            shards = list(
                connection.execute(
                    "SELECT dataset, request_semantics_sha256 "
                    "FROM market_session_generation_shards WHERE generation_id = ?",
                    (generation["generation_id"],),
                )
            )
            pin = {str(shard[0]): str(shard[1]) for shard in shards}
            encoded, digest = self._expected_semantics_pin(
                pin, _sha256(pin), MARKET_SESSION_DATASETS
            )
            verified = self._market_session_manifest_on_connection(
                connection,
                generation["generation_id"],
                expected_semantics_override=pin,
            )
            legacy_payload = dict(verified["manifest"])
            legacy_payload.pop("expected_request_semantics", None)
            legacy_payload.pop("expected_request_semantics_sha256", None)
            legacy_manifest_sha256 = _sha256(legacy_payload)
            legacy_lineage_sha256 = self._market_session_lineage_sha256_on_connection(
                connection, generation["generation_id"], legacy=True
            )
            head = connection.execute(
                "SELECT generation_id, manifest_sha256 "
                "FROM market_session_generation_head WHERE trade_date = ?",
                (generation["trade_date"],),
            ).fetchone()
            if (
                not hmac.compare_digest(str(generation["manifest_sha256"]), legacy_manifest_sha256)
                or (
                    head is not None
                    and str(head["generation_id"]) == generation["generation_id"]
                    and not hmac.compare_digest(
                        str(head["manifest_sha256"]), legacy_manifest_sha256
                    )
                )
                or not hmac.compare_digest(str(generation["lineage_sha256"]), legacy_lineage_sha256)
            ):
                raise PITReceiptError("legacy market generation identity mismatch")
            connection.execute(
                "UPDATE market_session_generations SET "
                "expected_request_semantics_json = ?, "
                "expected_request_semantics_sha256 = ? WHERE generation_id = ?",
                (encoded, digest, generation["generation_id"]),
            )
            verified = self._market_session_manifest_on_connection(
                connection, generation["generation_id"]
            )
            connection.execute(
                "UPDATE market_session_generations SET manifest_sha256 = ?, "
                "lineage_sha256 = ? WHERE generation_id = ?",
                (
                    verified["manifest_sha256"],
                    verified["lineage_sha256"],
                    generation["generation_id"],
                ),
            )
            if head is not None and str(head["generation_id"]) == generation["generation_id"]:
                connection.execute(
                    "UPDATE market_session_generation_head SET manifest_sha256 = ? "
                    "WHERE generation_id = ?",
                    (verified["manifest_sha256"], generation["generation_id"]),
                )
            records.append(
                {
                    "kind": "market_session",
                    "generation_id": generation["generation_id"],
                    "old_manifest_sha256": generation["manifest_sha256"],
                    "new_manifest_sha256": verified["manifest_sha256"],
                    "old_lineage_sha256": generation["lineage_sha256"],
                    "new_lineage_sha256": verified["lineage_sha256"],
                    "expected_request_semantics_sha256": digest,
                }
            )
        if records:
            stored = connection.execute(
                "SELECT value FROM store_metadata WHERE key = ?",
                ("generation_pin_migration/v1",),
            ).fetchone()
            history = json.loads(stored[0]) if stored is not None else []
            merged = {
                (str(record["kind"]), str(record["generation_id"])): record for record in history
            }
            for record in records:
                key = (str(record["kind"]), str(record["generation_id"]))
                if key in merged and merged[key] != record:
                    raise PITReceiptError(
                        "generation pin migration history conflicts with prior record"
                    )
                merged[key] = record
            history = [merged[key] for key in sorted(merged)]
            connection.execute(
                "INSERT INTO store_metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                ("generation_pin_migration/v1", _canonical_json(history)),
            )

    def _raw_path(self, dataset: str, digest: str, *, suffix: str = ".json") -> Path:
        return self.raw_root / dataset / digest[:2] / f"{digest}{suffix}"

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        fsync_directory(path)

    def _publish_raw(
        self, dataset: str, raw_bytes: bytes, digest: str, *, suffix: str = ".json"
    ) -> str:
        destination = self._raw_path(dataset, digest, suffix=suffix)
        missing_directories = []
        cursor = destination.parent
        while not cursor.exists() and cursor != self.root:
            missing_directories.append(cursor)
            cursor = cursor.parent
        destination.parent.mkdir(parents=True, exist_ok=True)
        for created in reversed(missing_directories):
            self._fsync_directory(created.parent)
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != raw_bytes:
                raise PITReceiptError("content-addressed raw receipt mismatch")
        else:
            descriptor, temp_name = tempfile.mkstemp(
                prefix=f"{digest}.", suffix=".tmp", dir=str(destination.parent)
            )
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(raw_bytes)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temp_name, destination)
                except FileExistsError:
                    if destination.is_symlink() or destination.read_bytes() != raw_bytes:
                        raise PITReceiptError("content-addressed raw receipt race mismatch")
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        self._fsync_directory(destination.parent)
        return destination.relative_to(self.root).as_posix()

    @staticmethod
    def _official_notice_ref(receipt: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "receipt_id": str(receipt["receipt_id"]),
            "source_profile": str(receipt["source_profile"]),
            "source_url": str(receipt["source_url"]),
            "published_at": str(receipt["published_at"]),
            "retrieved_at": str(receipt["retrieved_at"]),
            "notice_role": str(receipt["notice_role"]),
            "ts_code": str(receipt["ts_code"]),
            "announcement_number": str(receipt["announcement_number"]),
            "effective_date": str(receipt["effective_date"]),
            "raw_path": str(receipt["raw_path"]),
            "raw_sha256": str(receipt["raw_sha256"]),
            "raw_bytes": _require_int(receipt["raw_bytes"], "official suspension raw bytes"),
            "text_sha256": str(receipt["text_sha256"]),
            "normalized_sha256": str(receipt["normalized_sha256"]),
            "parser_version": str(receipt["parser_version"]),
        }

    @staticmethod
    def _official_interval_root(
        *,
        ts_code: str,
        start_date: str,
        resume_date: str,
        receipt_refs: Sequence[Mapping[str, Any]],
    ) -> str:
        return _sha256(
            {
                "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
                "ts_code": ts_code,
                "start_date": start_date,
                "resume_date": resume_date,
                "receipts": [dict(ref) for ref in receipt_refs],
            }
        )

    def ingest_cninfo_suspension_interval(
        self,
        *,
        ts_code: str,
        start_raw_bytes: bytes,
        start_source_url: str,
        start_published_at: Any,
        start_retrieved_at: Any,
        resume_raw_bytes: bytes,
        resume_source_url: str,
        resume_published_at: Any,
        resume_retrieved_at: Any,
    ) -> Dict[str, Any]:
        """Append a closed, official full-session suspension interval.

        The interval is independent of the market provider's ``suspend_d``
        endpoint. It may explain a missing daily bar, but it never creates or
        mutates a provider row.
        """

        symbol = _market_ts_code(ts_code, "official suspension")

        def prepare(
            *,
            raw_bytes: bytes,
            source_url: str,
            published_at: Any,
            retrieved_at: Any,
            notice_role: str,
        ) -> Dict[str, Any]:
            try:
                evidence = parse_cninfo_suspension_pdf(
                    raw_bytes,
                    source_url=source_url,
                    published_at=published_at,
                    expected_ts_code=symbol,
                    notice_role=notice_role,
                )
            except SuspensionEvidenceError as exc:
                raise PITReceiptError(f"invalid official suspension evidence: {exc}") from exc
            retrieved = _aware_timestamp(retrieved_at)
            if datetime.fromisoformat(retrieved) < datetime.fromisoformat(
                evidence["published_at"]
            ):
                raise PITReceiptError("official suspension retrieval precedes publication")
            normalized_sha256 = _sha256(evidence)
            receipt_id = _sha256(
                {
                    "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
                    "source_url": evidence["source_url"],
                    "raw_sha256": evidence["raw_sha256"],
                    "normalized_sha256": normalized_sha256,
                }
            )
            raw_path = self._publish_raw(
                "cninfo_suspension",
                bytes(raw_bytes),
                evidence["raw_sha256"],
                suffix=".pdf",
            )
            return {
                "receipt_id": receipt_id,
                "source_profile": evidence["source_profile"],
                "source_url": evidence["source_url"],
                "published_at": evidence["published_at"],
                "retrieved_at": retrieved,
                "notice_role": evidence["notice_role"],
                "ts_code": evidence["ts_code"],
                "announcement_number": evidence["announcement_number"],
                "effective_date": evidence["effective_date"],
                "raw_path": raw_path,
                "raw_sha256": evidence["raw_sha256"],
                "raw_bytes": evidence["raw_bytes"],
                "text_sha256": evidence["text_sha256"],
                "normalized_sha256": normalized_sha256,
                "parser_version": evidence["parser_version"],
            }

        start_receipt = prepare(
            raw_bytes=start_raw_bytes,
            source_url=start_source_url,
            published_at=start_published_at,
            retrieved_at=start_retrieved_at,
            notice_role="start",
        )
        resume_receipt = prepare(
            raw_bytes=resume_raw_bytes,
            source_url=resume_source_url,
            published_at=resume_published_at,
            retrieved_at=resume_retrieved_at,
            notice_role="resume",
        )
        start_date = str(start_receipt["effective_date"])
        resume_date = str(resume_receipt["effective_date"])
        if resume_date <= start_date:
            raise PITReceiptError("official suspension resume date must follow start date")
        receipt_refs = [
            self._official_notice_ref(start_receipt),
            self._official_notice_ref(resume_receipt),
        ]
        evidence_root = self._official_interval_root(
            ts_code=symbol,
            start_date=start_date,
            resume_date=resume_date,
            receipt_refs=receipt_refs,
        )
        interval_id = _sha256(
            {
                "kind": "official_suspension_interval",
                "evidence_root_sha256": evidence_root,
            }
        )
        recorded_at = max(
            str(start_receipt["retrieved_at"]), str(resume_receipt["retrieved_at"])
        )
        receipt_columns = tuple(start_receipt)
        placeholders = ", ".join("?" for _ in receipt_columns)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for receipt in (start_receipt, resume_receipt):
                existing = connection.execute(
                    "SELECT * FROM official_notice_receipts WHERE source_url = ?",
                    (receipt["source_url"],),
                ).fetchone()
                if existing is not None:
                    if self._official_notice_ref(existing) != self._official_notice_ref(receipt):
                        raise PITReceiptError("official suspension source URL conflicts with receipt")
                    continue
                connection.execute(
                    "INSERT INTO official_notice_receipts ("
                    + ", ".join(receipt_columns)
                    + ") VALUES ("
                    + placeholders
                    + ")",
                    tuple(receipt[column] for column in receipt_columns),
                )
            overlap = connection.execute(
                """
                SELECT interval_id FROM official_suspension_intervals
                WHERE ts_code = ? AND start_date < ? AND resume_date > ?
                """,
                (symbol, resume_date, start_date),
            ).fetchone()
            if overlap is not None and str(overlap["interval_id"]) != interval_id:
                raise PITReceiptError("official suspension intervals overlap")
            existing_interval = connection.execute(
                "SELECT * FROM official_suspension_intervals WHERE interval_id = ?",
                (interval_id,),
            ).fetchone()
            interval = {
                "interval_id": interval_id,
                "ts_code": symbol,
                "start_date": start_date,
                "resume_date": resume_date,
                "start_receipt_id": start_receipt["receipt_id"],
                "resume_receipt_id": resume_receipt["receipt_id"],
                "evidence_root_sha256": evidence_root,
                "recorded_at": recorded_at,
            }
            if existing_interval is None:
                connection.execute(
                    """
                    INSERT INTO official_suspension_intervals (
                        interval_id, ts_code, start_date, resume_date,
                        start_receipt_id, resume_receipt_id,
                        evidence_root_sha256, recorded_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(interval.values()),
                )
            elif dict(existing_interval) != interval:
                raise PITReceiptError("official suspension interval conflicts with prior record")
        return interval

    @staticmethod
    def _credential_text_is_present(value: Any, configured_token: str) -> bool:
        credential_keys = {"token", "api_key", "apikey", "secret", "authorization"}

        def walk(item: Any) -> bool:
            if isinstance(item, Mapping):
                for key, nested in item.items():
                    if str(key).strip().lower() in credential_keys or walk(nested):
                        return True
                return False
            if isinstance(item, (list, tuple, set)):
                return any(walk(nested) for nested in item)
            if str(item).strip().lower() in credential_keys:
                return True
            if configured_token and configured_token in str(item):
                return True
            return False

        return walk(value)

    def record_fetch_attempt(
        self,
        *,
        dataset: str,
        partition_key: str,
        endpoint: str,
        params: Mapping[str, Any],
        fields: Sequence[str],
        wire_request_sha256: str,
        raw_bytes: bytes = None,
        http_status: int = None,
        started_at: str = None,
        retrieved_at: str = None,
        elapsed_ns: int = 0,
        row_cap: int,
        body_complete: bool,
        attempt_no: int = None,
        response_headers: Mapping[str, Any] = None,
        clock_attestation: Mapping[str, Any] = None,
        error_kind: str = None,
        error_message: str = None,
        request_body_sha256: str = None,
        request_semantics_sha256: str = None,
        request_semantics: Mapping[str, Any] = None,
    ) -> Dict[str, Any]:
        """Append one transport attempt without accepting it as a receipt."""

        if dataset not in DATASET_ENDPOINTS or endpoint != DATASET_ENDPOINTS[dataset]:
            raise PITReceiptError("unsupported dataset/endpoint")
        configured_tokens = tuple(
            token
            for token in (
                str(os.getenv("TUSHARE_TOKEN") or ""),
                str(os.getenv("JIAOCH_TOKEN") or ""),
            )
            if token
        )
        credential_surface = {
            "params": dict(params),
            "fields": list(fields),
            "response_headers": dict(response_headers or {}),
            "clock_attestation": dict(clock_attestation or {}),
            "error_kind": error_kind,
            "error_message": error_message,
            "request_semantics": dict(request_semantics or {}),
        }
        if any(
            self._credential_text_is_present(credential_surface, token)
            for token in configured_tokens or ("",)
        ):
            raise PITReceiptError("request attempt contains a credential or token")
        if raw_bytes is not None:
            if not isinstance(raw_bytes, bytes):
                raise PITReceiptError("attempt response body must be bytes")
            if any(token.encode("utf-8") in raw_bytes for token in configured_tokens):
                raise PITReceiptError("attempt response body contains a credential or token")
            if len(raw_bytes) > MAX_RAW_BYTES[dataset]:
                raise PITReceiptError("attempt response body exceeds the dataset byte limit")
        normalized_params = _normalize_request_params(dataset, params)
        if partition_key != _canonical_partition_key(dataset, normalized_params):
            raise PITReceiptError("attempt partition key does not match canonical request")
        normalized_fields = tuple(str(field).strip() for field in fields)
        if normalized_fields != tuple(NORMALIZED_FIELDS[dataset]):
            raise PITReceiptError("attempt request fields do not match the frozen dataset contract")
        wire_digest = _require_sha256(wire_request_sha256, "wire request hash")
        if request_body_sha256 is not None and not hmac.compare_digest(
            wire_digest, _require_sha256(request_body_sha256, "request body hash")
        ):
            raise PITReceiptError("request body and wire request hashes disagree")
        if int(row_cap) <= 0:
            raise PITReceiptError("row_cap must be positive")
        source_profile = str((request_semantics or {}).get("source_profile") or "official")
        expected_cap = OFFICIAL_ROW_CAPS.get(dataset)
        if source_profile == "jiaoch":
            expected_cap = dict(JIAOCH_ROW_CAP_OVERRIDES).get(dataset, expected_cap)
        if expected_cap is not None and int(row_cap) != expected_cap:
            raise PITReceiptError(
                f"{dataset} must use source row cap {expected_cap}, got {row_cap}"
            )
        started = _aware_timestamp(started_at) if started_at is not None else None
        retrieved = _aware_timestamp(retrieved_at) if retrieved_at is not None else None
        if int(elapsed_ns) < 0:
            raise PITReceiptError("attempt elapsed_ns must not be negative")
        if body_complete and raw_bytes is None:
            raise PITReceiptError("complete attempt response body is missing")
        if http_status is not None and not 100 <= int(http_status) <= 599:
            raise PITReceiptError("attempt HTTP status is invalid")

        receipt_semantics = {
            "dataset": dataset,
            "endpoint": endpoint,
            "params": normalized_params,
            "fields": list(normalized_fields),
        }
        persisted_semantics = (
            dict(request_semantics) if request_semantics is not None else receipt_semantics
        )
        if request_semantics is not None:
            try:
                semantic_receipt_params = _normalize_request_params(
                    dataset, persisted_semantics.get("receipt_params") or {}
                )
            except (KeyError, PITReceiptError):
                semantic_receipt_params = {}
            if (
                persisted_semantics.get("schema_version") != "tushare-wire-request/v1"
                or persisted_semantics.get("dataset") != dataset
                or persisted_semantics.get("partition_key") != partition_key
                or persisted_semantics.get("api_name") != endpoint
                or persisted_semantics.get("method") != "POST"
                or semantic_receipt_params != normalized_params
                or persisted_semantics.get("wire_params")
                != _canonical_wire_params(dataset, normalized_params)
                or persisted_semantics.get("fields") != list(normalized_fields)
                or int(persisted_semantics.get("row_cap") or 0) != int(row_cap)
                or partition_key != _canonical_partition_key(dataset, normalized_params)
            ):
                raise PITReceiptError(
                    "wire request semantics disagree with receipt promotion contract"
                )
        semantics_digest = _sha256(persisted_semantics)
        if request_semantics_sha256 is not None and not hmac.compare_digest(
            semantics_digest,
            _require_sha256(request_semantics_sha256, "request semantics hash"),
        ):
            raise PITReceiptError("request semantics hash mismatch")

        raw_path = None
        raw_digest = None
        raw_size = 0
        if raw_bytes is not None:
            raw_digest = hashlib.sha256(raw_bytes).hexdigest()
            raw_size = len(raw_bytes)
            raw_path = self._publish_raw(dataset, raw_bytes, raw_digest)
        headers_json = _canonical_json(dict(response_headers or {}))
        clock_json = _canonical_json(dict(clock_attestation or {}))
        recorded_at = retrieved or datetime.now(timezone.utc).isoformat()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(attempt_sequence), 0) + 1 FROM fetch_attempts"
                ).fetchone()[0]
            )
            resolved_attempt_no = (
                int(attempt_no)
                if attempt_no is not None
                else int(
                    connection.execute(
                        """
                        SELECT COALESCE(MAX(attempt_no), 0) + 1 FROM fetch_attempts
                        WHERE dataset = ? AND partition_key = ?
                        """,
                        (dataset, partition_key),
                    ).fetchone()[0]
                )
            )
            if resolved_attempt_no <= 0:
                raise PITReceiptError("attempt_no must be positive")
            attempt_id = f"attempt-{next_sequence:020d}"
            connection.execute(
                """
                INSERT INTO fetch_attempts (
                    attempt_sequence, attempt_id, dataset, partition_key, endpoint,
                    params_json, fields_json, request_semantics_json,
                    request_semantics_sha256, wire_request_sha256, attempt_no,
                    started_at, retrieved_at, elapsed_ns, row_cap, http_status,
                    response_headers_json, clock_attestation_json, body_complete,
                    raw_path, raw_sha256, raw_bytes, outcome, error_kind,
                    error_message, recorded_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          'captured', ?, ?, ?)
                """,
                (
                    next_sequence,
                    attempt_id,
                    dataset,
                    partition_key,
                    endpoint,
                    _canonical_json(normalized_params),
                    _canonical_json(list(normalized_fields)),
                    _canonical_json(persisted_semantics),
                    semantics_digest,
                    wire_digest,
                    resolved_attempt_no,
                    started,
                    retrieved,
                    int(elapsed_ns),
                    int(row_cap),
                    int(http_status) if http_status is not None else None,
                    headers_json,
                    clock_json,
                    1 if body_complete else 0,
                    raw_path,
                    raw_digest,
                    raw_size,
                    str(error_kind) if error_kind else None,
                    str(error_message) if error_message else None,
                    recorded_at,
                ),
            )
            connection.commit()
        terminal_status = {
            "transport_error": "transport_error",
            "http_retryable": "http_error",
            "http_error": "http_error",
            "api_error": "api_error",
            "invalid_json": "invalid_json",
            "incomplete_response": "invalid_json",
            "unsupported_content_encoding": "invalid_json",
            "credential_echo": "invalid_json",
            "clock_attestation_failed": "clock_error",
        }.get(str(error_kind or ""))
        if terminal_status:
            self._append_promotion_event(
                attempt_id,
                terminal_status,
                {"error_kind": str(error_kind)},
            )
        return self._fetch_attempt(attempt_id)

    @staticmethod
    def _promotion_result(
        attempt_id: str, status: str, details: Mapping[str, Any]
    ) -> Dict[str, Any]:
        return {"attempt_id": attempt_id, "status": status, **dict(details)}

    def _append_promotion_event(
        self, attempt_id: str, status: str, details: Mapping[str, Any]
    ) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT status, details_json FROM fetch_promotion_events WHERE attempt_id = ?",
                (attempt_id,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO fetch_promotion_events (
                        attempt_id, status, details_json, recorded_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        status,
                        _canonical_json(dict(details)),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                return self._promotion_result(attempt_id, status, details)
            return self._promotion_result(
                attempt_id, existing["status"], json.loads(existing["details_json"])
            )

    def _fetch_attempt(self, attempt_id: str) -> Dict[str, Any]:
        rows = self.fetch_attempts(attempt_id=attempt_id)
        if not rows:
            raise PITReceiptError(f"fetch attempt does not exist: {attempt_id}")
        return rows[0]

    def fetch_attempts(
        self, *, partition_key: str = None, attempt_id: str = None
    ) -> List[Dict[str, Any]]:
        clauses = []
        params: List[Any] = []
        if partition_key is not None:
            clauses.append("partition_key = ?")
            params.append(str(partition_key))
        if attempt_id is not None:
            clauses.append("attempt_id = ?")
            params.append(str(attempt_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as connection:
            rows = list(
                connection.execute(
                    "SELECT * FROM fetch_attempts" + where + " ORDER BY attempt_sequence",
                    tuple(params),
                )
            )
            output = []
            for stored in rows:
                row = dict(stored)
                events = [
                    {
                        "status": event["status"],
                        "details": json.loads(event["details_json"]),
                        "recorded_at": event["recorded_at"],
                    }
                    for event in connection.execute(
                        """
                        SELECT status, details_json, recorded_at
                        FROM fetch_promotion_events
                        WHERE attempt_id = ? ORDER BY event_sequence
                        """,
                        (row["attempt_id"],),
                    )
                ]
                row.update(
                    {
                        "params": json.loads(row.pop("params_json")),
                        "fields": json.loads(row.pop("fields_json")),
                        "request_semantics": json.loads(row.pop("request_semantics_json")),
                        "response_headers": json.loads(row.pop("response_headers_json")),
                        "clock_attestation": json.loads(row.pop("clock_attestation_json")),
                        "body_complete": bool(row["body_complete"]),
                        "promotion_events": events,
                        "terminal_status": events[-1]["status"] if events else None,
                    }
                )
                output.append(row)
            return output

    @staticmethod
    def _generation_timestamp(value: Any, field: str) -> Tuple[str, datetime]:
        text = value.isoformat() if isinstance(value, datetime) else str(value)
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise PITReceiptError(f"invalid {field}") from exc
        if parsed.tzinfo is None:
            raise PITReceiptError(f"{field} must include a timezone")
        return parsed.isoformat(), parsed.astimezone(timezone.utc)

    @staticmethod
    def _stock_generation_view(
        connection: sqlite3.Connection, generation_id: str
    ) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM stock_basic_generations WHERE generation_id = ?",
            (str(generation_id),),
        ).fetchone()
        if row is None:
            raise PITReceiptError(f"stock generation does not exist: {generation_id}")
        result = dict(row)
        staged = [
            item[0]
            for item in connection.execute(
                """
                SELECT logical_partition_key
                FROM stock_basic_generation_shards
                WHERE generation_id = ? ORDER BY logical_partition_key
                """,
                (str(generation_id),),
            )
        ]
        partition_order = {key: index for index, key in enumerate(REQUIRED_STOCK_PARTITIONS)}
        result["staged_partitions"] = sorted(
            staged, key=lambda key: partition_order.get(key, len(partition_order))
        )
        return result

    @staticmethod
    def _expected_semantics_pin(
        manifest: Mapping[str, str] | None,
        claimed_sha256: str | None,
        required_keys: Sequence[str] | None = None,
    ) -> tuple[str | None, str | None]:
        if manifest is None and claimed_sha256 is None:
            return None, None
        if manifest is None or claimed_sha256 is None:
            raise PITReceiptError("expected request semantics pin is incomplete")
        normalized = {
            str(key): _require_sha256(value, "expected semantics")
            for key, value in sorted(manifest.items())
        }
        if required_keys is not None and set(normalized) != set(required_keys):
            raise PITReceiptError("expected request semantics manifest keyset mismatch")
        encoded = _canonical_json(normalized)
        digest = _sha256(normalized)
        if not hmac.compare_digest(
            digest, _require_sha256(claimed_sha256, "expected semantics manifest")
        ):
            raise PITReceiptError("expected request semantics manifest hash mismatch")
        return encoded, digest

    @classmethod
    def _verified_generation_pin(
        cls, generation: Mapping[str, Any], required_keys: Sequence[str]
    ) -> dict[str, str]:
        try:
            manifest = json.loads(generation["expected_request_semantics_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PITReceiptError("generation expected request semantics pin is invalid") from exc
        encoded, digest = cls._expected_semantics_pin(
            manifest,
            generation.get("expected_request_semantics_sha256"),
            required_keys,
        )
        if encoded != generation["expected_request_semantics_json"]:
            raise PITReceiptError("generation expected request semantics pin is not canonical")
        return json.loads(encoded)

    def begin_or_resume_stock_basic_generation(
        self,
        now: Any,
        force_new: bool = False,
        *,
        expected_request_semantics: Mapping[str, str] | None = None,
        expected_request_semantics_sha256: str | None = None,
    ) -> Dict[str, Any]:
        now_iso, now_utc = self._generation_timestamp(now, "generation now")
        expected_json, expected_sha256 = self._expected_semantics_pin(
            expected_request_semantics,
            expected_request_semantics_sha256,
            REQUIRED_STOCK_PARTITIONS,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT generation_id FROM stock_basic_generations
                WHERE scope_key = ? AND status = 'collecting'
                """,
                (STOCK_GENERATION_SCOPE,),
            ).fetchone()
            if current is not None:
                view = self._stock_generation_view(connection, current[0])
                _started_iso, started_utc = self._generation_timestamp(
                    view["started_at"], "generation started_at"
                )
                if now_utc < started_utc:
                    raise PITReceiptError("generation clock moved backwards")
                complete = set(view["staged_partitions"]) == set(REQUIRED_STOCK_PARTITIONS)
                expired = (
                    not complete
                    and (now_utc - started_utc).total_seconds() > STOCK_GENERATION_WINDOW_SECONDS
                )
                pin_matches = expected_sha256 is None or (
                    view.get("expected_request_semantics_sha256") == expected_sha256
                    and view.get("expected_request_semantics_json") == expected_json
                )
                if not force_new and not expired and pin_matches:
                    return view
                reason = (
                    "force_new"
                    if force_new
                    else ("request_semantics_changed" if not pin_matches else "stale_incomplete")
                )
                connection.execute(
                    """
                    UPDATE stock_basic_generations
                    SET status = 'abandoned', terminal_at = ?, terminal_reason = ?
                    WHERE generation_id = ? AND status = 'collecting'
                    """,
                    (now_iso, reason, view["generation_id"]),
                )
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation_sequence), 0) + 1 FROM stock_basic_generations"
                ).fetchone()[0]
            )
            generation_id = f"stock-master-{next_sequence:020d}"
            connection.execute(
                """
                INSERT INTO stock_basic_generations (
                    generation_sequence, generation_id, scope_key,
                    contract_sha256, status, started_at,
                    expected_request_semantics_json,
                    expected_request_semantics_sha256
                ) VALUES (?, ?, ?, ?, 'collecting', ?, ?, ?)
                """,
                (
                    next_sequence,
                    generation_id,
                    STOCK_GENERATION_SCOPE,
                    _stock_generation_contract_sha256(),
                    now_iso,
                    expected_json,
                    expected_sha256,
                ),
            )
            return self._stock_generation_view(connection, generation_id)

    def _validated_stock_attempt_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        logical_partition_key: str,
        attempt_id: str,
        allow_staged_event: bool = False,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
        if logical_partition_key not in REQUIRED_STOCK_PARTITIONS:
            raise PITReceiptError("stock generation partition is invalid")
        stored = connection.execute(
            "SELECT * FROM fetch_attempts WHERE attempt_id = ?", (str(attempt_id),)
        ).fetchone()
        if stored is None:
            raise PITReceiptError(f"fetch attempt does not exist: {attempt_id}")
        attempt = dict(stored)
        if (
            attempt["dataset"] != "stock_basic"
            or attempt["partition_key"] != logical_partition_key
            or attempt["endpoint"] != "stock_basic"
            or attempt["error_kind"] is not None
            or int(attempt["body_complete"]) != 1
            or attempt["http_status"] is None
            or not 200 <= int(attempt["http_status"]) < 300
            or not attempt["raw_path"]
        ):
            raise PITReceiptError("stock generation attempt is not promotable")
        event = connection.execute(
            "SELECT status, details_json FROM fetch_promotion_events WHERE attempt_id = ?",
            (str(attempt_id),),
        ).fetchone()
        if event is not None:
            details = json.loads(event["details_json"])
            if not (
                allow_staged_event
                and event["status"] == "generation_staged"
                and details.get("logical_partition_key") == logical_partition_key
                and details.get("raw_sha256") == attempt["raw_sha256"]
            ):
                raise PITReceiptError("stock generation attempt already has a terminal event")
        params = json.loads(attempt["params_json"])
        if logical_partition_key != _canonical_partition_key("stock_basic", params):
            raise PITReceiptError("stock generation attempt partition lineage mismatch")
        semantics = json.loads(attempt["request_semantics_json"])
        if (
            _sha256(semantics) != attempt["request_semantics_sha256"]
            or semantics.get("wire_params") != _canonical_wire_params("stock_basic", params)
            or semantics.get("fields") != list(NORMALIZED_FIELDS["stock_basic"])
        ):
            raise PITReceiptError("stock generation request lineage mismatch")
        raw_bytes = self._verify_raw_file(attempt)
        fields, items, _code, _message = _parse_native_envelope(raw_bytes)
        if len(items) >= int(attempt["row_cap"]):
            raise PITReceiptError("stock generation attempt reaches row cap")
        normalized = _normalize_rows("stock_basic", logical_partition_key, params, fields, items)
        return attempt, normalized, _sha256(normalized)

    def stage_stock_basic_attempt(
        self, generation_id: str, logical_partition_key: str, attempt_id: str
    ) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._stock_generation_view(connection, generation_id)
            if generation["status"] != "collecting":
                raise PITReceiptError("stock generation is abandoned or inactive")
            existing = connection.execute(
                """
                SELECT attempt_id FROM stock_basic_generation_shards
                WHERE generation_id = ? AND logical_partition_key = ?
                """,
                (str(generation_id), str(logical_partition_key)),
            ).fetchone()
            if existing is not None and existing["attempt_id"] != str(attempt_id):
                raise PITReceiptError("stock generation partition is already staged; conflict")
            if existing is not None:
                stored_attempt = connection.execute(
                    "SELECT * FROM fetch_attempts WHERE attempt_id = ?",
                    (str(attempt_id),),
                ).fetchone()
                if stored_attempt is None:
                    raise PITReceiptError("staged stock generation attempt is missing")
                self._verify_raw_file(dict(stored_attempt))
                return {**generation, "status": "reused"}
            attempt, normalized, normalized_sha256 = self._validated_stock_attempt_on_connection(
                connection,
                logical_partition_key=str(logical_partition_key),
                attempt_id=str(attempt_id),
            )
            if generation.get("expected_request_semantics_sha256"):
                expected = json.loads(generation["expected_request_semantics_json"])
                if attempt["request_semantics_sha256"] != expected.get(str(logical_partition_key)):
                    raise PITReceiptError(
                        "stock generation attempt request semantics do not match pinned manifest"
                    )
            _started_iso, started_utc = self._generation_timestamp(
                generation["started_at"], "generation started_at"
            )
            _retrieved_iso, retrieved_utc = self._generation_timestamp(
                attempt["retrieved_at"], "attempt retrieved_at"
            )
            span_seconds = (retrieved_utc - started_utc).total_seconds()
            if not 0 <= span_seconds <= STOCK_GENERATION_WINDOW_SECONDS:
                raise PITReceiptError("stock generation staging window expired")
            connection.execute(
                """
                INSERT INTO stock_basic_generation_shards (
                    generation_id, logical_partition_key, attempt_id,
                    request_semantics_sha256, retrieved_at, raw_sha256,
                    raw_bytes, normalized_sha256, row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(generation_id),
                    str(logical_partition_key),
                    str(attempt_id),
                    attempt["request_semantics_sha256"],
                    attempt["retrieved_at"],
                    attempt["raw_sha256"],
                    int(attempt["raw_bytes"]),
                    normalized_sha256,
                    len(normalized),
                ),
            )
            connection.executemany(
                """
                INSERT INTO stock_basic_generation_rows (
                    generation_id, logical_partition_key, ts_code, symbol,
                    name, exchange, market, list_status, list_date, delist_date
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(generation_id),
                        str(logical_partition_key),
                        row["ts_code"],
                        row["symbol"],
                        row["name"],
                        row["exchange"],
                        row["market"],
                        row["list_status"],
                        row["list_date"],
                        row["delist_date"],
                    )
                    for row in normalized
                ],
            )
            connection.execute(
                """
                INSERT INTO fetch_promotion_events (
                    attempt_id, status, details_json, recorded_at
                ) VALUES (?, 'generation_staged', ?, ?)
                """,
                (
                    str(attempt_id),
                    _canonical_json(
                        {
                            "generation_id": str(generation_id),
                            "logical_partition_key": str(logical_partition_key),
                            "raw_sha256": attempt["raw_sha256"],
                        }
                    ),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            staged = self._stock_generation_view(connection, generation_id)
            return {**staged, "status": "staged"}

    def _stock_generation_manifest_on_connection(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        *,
        expected_semantics_override: Mapping[str, str] | None = None,
    ) -> Dict[str, Any]:
        generation = self._stock_generation_view(connection, generation_id)
        if generation["contract_sha256"] != _stock_generation_contract_sha256():
            raise PITReceiptError("stock generation contract hash mismatch")
        shards = list(
            connection.execute(
                """
                SELECT * FROM stock_basic_generation_shards
                WHERE generation_id = ? ORDER BY logical_partition_key
                """,
                (str(generation_id),),
            )
        )
        expected_semantics = (
            dict(expected_semantics_override)
            if expected_semantics_override is not None
            else self._verified_generation_pin(generation, REQUIRED_STOCK_PARTITIONS)
        )
        partition_keys = [row["logical_partition_key"] for row in shards]
        if set(partition_keys) != set(REQUIRED_STOCK_PARTITIONS) or len(shards) != len(
            REQUIRED_STOCK_PARTITIONS
        ):
            raise PITReceiptError("stock generation is incomplete; all eight shards are required")
        duplicate = connection.execute(
            """
            SELECT ts_code FROM stock_basic_generation_rows
            WHERE generation_id = ? GROUP BY ts_code HAVING COUNT(*) > 1 LIMIT 1
            """,
            (str(generation_id),),
        ).fetchone()
        if duplicate is not None:
            raise PITReceiptError("stock generation contains duplicate security rows")
        generation_rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT logical_partition_key, ts_code, symbol, name, exchange,
                       market, list_status, list_date, delist_date
                FROM stock_basic_generation_rows
                WHERE generation_id = ?
                ORDER BY logical_partition_key, ts_code
                """,
                (str(generation_id),),
            )
        ]
        rows_sha256, generation_row_count = _stream_rows_sha256(generation_rows)
        _started_iso, started_utc = self._generation_timestamp(
            generation["started_at"], "generation started_at"
        )
        retrieved_values = []
        shard_manifest = []
        for stored_shard in shards:
            shard = dict(stored_shard)
            if shard["request_semantics_sha256"] != expected_semantics.get(
                shard["logical_partition_key"]
            ):
                raise PITReceiptError("stock generation pinned semantics mismatch")
            attempt, normalized, normalized_sha256 = self._validated_stock_attempt_on_connection(
                connection,
                logical_partition_key=shard["logical_partition_key"],
                attempt_id=shard["attempt_id"],
                allow_staged_event=True,
            )
            event = connection.execute(
                """
                SELECT details_json FROM fetch_promotion_events
                WHERE attempt_id = ? AND status = 'generation_staged'
                """,
                (shard["attempt_id"],),
            ).fetchone()
            event_details = json.loads(event["details_json"]) if event else {}
            if event_details.get("generation_id") != str(generation_id):
                raise PITReceiptError("stock generation promotion binding mismatch")
            if (
                attempt["request_semantics_sha256"] != shard["request_semantics_sha256"]
                or attempt["retrieved_at"] != shard["retrieved_at"]
                or attempt["raw_sha256"] != shard["raw_sha256"]
                or int(attempt["raw_bytes"]) != int(shard["raw_bytes"])
                or normalized_sha256 != shard["normalized_sha256"]
                or len(normalized) != int(shard["row_count"])
            ):
                raise PITReceiptError("stock generation shard lineage mismatch")
            stored_rows = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT ts_code, symbol, name, exchange, market, list_status,
                           list_date, delist_date
                    FROM stock_basic_generation_rows
                    WHERE generation_id = ? AND logical_partition_key = ?
                    ORDER BY ts_code
                    """,
                    (str(generation_id), shard["logical_partition_key"]),
                )
            ]
            if stored_rows != normalized:
                raise PITReceiptError("stock generation normalized rows mismatch")
            _retrieved_iso, retrieved_utc = self._generation_timestamp(
                shard["retrieved_at"], "generation shard retrieved_at"
            )
            if retrieved_utc < started_utc:
                raise PITReceiptError("stock generation shard predates generation")
            retrieved_values.append(retrieved_utc)
            shard_manifest.append(
                {
                    "logical_partition_key": shard["logical_partition_key"],
                    "attempt_id": shard["attempt_id"],
                    "request_semantics_sha256": shard["request_semantics_sha256"],
                    "retrieved_at": shard["retrieved_at"],
                    "raw_sha256": shard["raw_sha256"],
                    "raw_bytes": int(shard["raw_bytes"]),
                    "normalized_sha256": shard["normalized_sha256"],
                    "row_count": int(shard["row_count"]),
                }
            )
        if (
            max(retrieved_values) - min(retrieved_values)
        ).total_seconds() > STOCK_GENERATION_WINDOW_SECONDS or (
            max(retrieved_values) - started_utc
        ).total_seconds() > STOCK_GENERATION_WINDOW_SECONDS:
            raise PITReceiptError("stock generation shards exceed the one-hour window")
        payload = {
            "schema_version": STOCK_GENERATION_SCHEMA_VERSION,
            "generation_id": str(generation_id),
            "scope_key": STOCK_GENERATION_SCOPE,
            "contract_sha256": generation["contract_sha256"],
            "started_at": generation["started_at"],
            "rows_sha256": rows_sha256,
            "row_count": generation_row_count,
            "expected_request_semantics": expected_semantics,
            "expected_request_semantics_sha256": _sha256(expected_semantics),
            "shards": shard_manifest,
        }
        manifest_sha256 = _sha256(payload)
        audit_identity_sha256 = _sha256(
            {
                "schema_version": "stock-basic-generation-audit/v1",
                "generation_id": str(generation_id),
                "manifest_sha256": manifest_sha256,
                "rows_sha256": rows_sha256,
            }
        )
        return {
            "manifest": payload,
            "manifest_sha256": manifest_sha256,
            "rows_sha256": rows_sha256,
            "audit_identity_sha256": audit_identity_sha256,
        }

    @staticmethod
    def _stock_generation_lineage_sha256_on_connection(
        connection: sqlite3.Connection, generation_id: str
    ) -> str:
        generation = connection.execute(
            """
            SELECT generation_sequence, generation_id, scope_key,
                   contract_sha256, status, started_at, terminal_at,
                   terminal_reason, manifest_sha256,
                   expected_request_semantics_json,
                   expected_request_semantics_sha256
            FROM stock_basic_generations WHERE generation_id = ?
            """,
            (str(generation_id),),
        ).fetchone()
        head = connection.execute(
            """
            SELECT scope_key, generation_id, manifest_sha256, published_at
            FROM stock_basic_generation_head
            WHERE scope_key = ? AND generation_id = ?
            """,
            (STOCK_GENERATION_SCOPE, str(generation_id)),
        ).fetchone()
        if generation is None or head is None:
            raise PITReceiptError("stock generation lineage head is incomplete")
        shards = [
            dict(row)
            for row in connection.execute(
                """
                SELECT generation_id, logical_partition_key, attempt_id,
                       request_semantics_sha256, retrieved_at, raw_sha256,
                       raw_bytes, normalized_sha256, row_count
                FROM stock_basic_generation_shards
                WHERE generation_id = ? ORDER BY logical_partition_key
                """,
                (str(generation_id),),
            )
        ]
        attempts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT attempt.* FROM fetch_attempts AS attempt
                JOIN stock_basic_generation_shards AS shard
                  ON shard.attempt_id = attempt.attempt_id
                WHERE shard.generation_id = ?
                ORDER BY shard.logical_partition_key
                """,
                (str(generation_id),),
            )
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT event.* FROM fetch_promotion_events AS event
                JOIN stock_basic_generation_shards AS shard
                  ON shard.attempt_id = event.attempt_id
                WHERE shard.generation_id = ?
                ORDER BY shard.logical_partition_key
                """,
                (str(generation_id),),
            )
        ]
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT generation_id, logical_partition_key, ts_code, symbol,
                       name, exchange, market, list_status, list_date, delist_date
                FROM stock_basic_generation_rows
                WHERE generation_id = ?
                ORDER BY logical_partition_key, ts_code
                """,
                (str(generation_id),),
            )
        ]
        if (
            len(shards) != len(REQUIRED_STOCK_PARTITIONS)
            or len(attempts) != len(REQUIRED_STOCK_PARTITIONS)
            or len(events) != len(REQUIRED_STOCK_PARTITIONS)
        ):
            raise PITReceiptError("stock generation exact lineage is incomplete")
        return _sha256(
            {
                "schema_version": "stock-basic-generation-lineage/v1",
                "generation_id": str(generation_id),
                "generation": dict(generation),
                "head": dict(head),
                "shards": shards,
                "rows": rows,
                "attempts": attempts,
                "promotion_events": events,
            }
        )

    def publish_stock_basic_generation(self, generation_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._stock_generation_view(connection, generation_id)
            if generation["status"] == "abandoned":
                raise PITReceiptError("stock generation is abandoned or inactive")
            if generation.get("expected_request_semantics_sha256") is None:
                rows = connection.execute(
                    "SELECT logical_partition_key, request_semantics_sha256 "
                    "FROM stock_basic_generation_shards WHERE generation_id = ?",
                    (str(generation_id),),
                )
                pin = {str(row[0]): str(row[1]) for row in rows}
                if set(pin) != set(REQUIRED_STOCK_PARTITIONS):
                    raise PITReceiptError(
                        "stock generation is incomplete; all eight shards are required"
                    )
                encoded, digest = self._expected_semantics_pin(
                    pin, _sha256(pin), REQUIRED_STOCK_PARTITIONS
                )
                connection.execute(
                    "UPDATE stock_basic_generations SET "
                    "expected_request_semantics_json = ?, "
                    "expected_request_semantics_sha256 = ? WHERE generation_id = ?",
                    (encoded, digest, str(generation_id)),
                )
                generation = self._stock_generation_view(connection, generation_id)
            verified = self._stock_generation_manifest_on_connection(connection, str(generation_id))
            if generation["status"] == "published":
                if generation["manifest_sha256"] != verified["manifest_sha256"]:
                    raise PITReceiptError("published stock generation manifest mismatch")
                return generation
            published_at = max(
                self._generation_timestamp(row[0], "generation shard retrieved_at")[1]
                for row in connection.execute(
                    """
                    SELECT retrieved_at FROM stock_basic_generation_shards
                    WHERE generation_id = ?
                    """,
                    (str(generation_id),),
                )
            ).isoformat()
            connection.execute(
                """
                UPDATE stock_basic_generations
                SET status = 'published', terminal_at = ?, manifest_sha256 = ?
                WHERE generation_id = ? AND status = 'collecting'
                """,
                (published_at, verified["manifest_sha256"], str(generation_id)),
            )
            connection.execute(
                """
                INSERT INTO stock_basic_generation_head (
                    scope_key, generation_id, manifest_sha256, published_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(scope_key) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    manifest_sha256 = excluded.manifest_sha256,
                    published_at = excluded.published_at
                """,
                (
                    STOCK_GENERATION_SCOPE,
                    str(generation_id),
                    verified["manifest_sha256"],
                    published_at,
                ),
            )
            return self._stock_generation_view(connection, generation_id)

    def active_stock_basic_generation(self) -> Dict[str, Any] | None:
        with self._connect() as connection:
            connection.execute("BEGIN")
            active = self._active_stock_generation_on_connection(connection)
            if active is None:
                return None
            generation, _verified = active
            return generation

    def _active_stock_generation_on_connection(
        self, connection: sqlite3.Connection
    ) -> Tuple[Dict[str, Any], Dict[str, Any]] | None:
        head = connection.execute(
            """
            SELECT generation_id, manifest_sha256
            FROM stock_basic_generation_head
            WHERE scope_key = ?
            """,
            (STOCK_GENERATION_SCOPE,),
        ).fetchone()
        if head is None:
            return None
        generation = self._stock_generation_view(connection, head["generation_id"])
        if generation["scope_key"] != STOCK_GENERATION_SCOPE:
            raise PITReceiptError("active stock generation scope mismatch")
        if generation["status"] != "published":
            raise PITReceiptError("active stock generation is not published")
        if head["manifest_sha256"] != generation["manifest_sha256"]:
            raise PITReceiptError("active stock generation head manifest mismatch")
        latest = connection.execute(
            """
            SELECT MAX(generation_sequence)
            FROM stock_basic_generations
            WHERE scope_key = ? AND status = 'published'
            """,
            (STOCK_GENERATION_SCOPE,),
        ).fetchone()[0]
        if latest is None or int(generation["generation_sequence"]) != int(latest):
            raise PITReceiptError("active stock generation head rollback detected")
        verified = self._stock_generation_manifest_on_connection(
            connection, str(generation["generation_id"])
        )
        if head["manifest_sha256"] != verified["manifest_sha256"]:
            raise PITReceiptError("active stock generation head hash mismatch")
        return generation, verified

    def verify_stock_basic_generation(self, generation_id: str = None) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            resolved = str(generation_id) if generation_id is not None else None
            active_verified = None
            if resolved is None:
                active = self._active_stock_generation_on_connection(connection)
                if active is None:
                    raise PITReceiptError("active stock generation is missing")
                generation, active_verified = active
                resolved = str(generation["generation_id"])
            else:
                generation = self._stock_generation_view(connection, resolved)
            if generation["status"] != "published":
                raise PITReceiptError("stock generation is not published")
            verified = active_verified or self._stock_generation_manifest_on_connection(
                connection, resolved
            )
            if generation["manifest_sha256"] != verified["manifest_sha256"]:
                raise PITReceiptError("stock generation manifest mismatch")
            return {
                **generation,
                "verification_status": "passed",
                **verified,
            }

    @staticmethod
    def _market_session_view(connection: sqlite3.Connection, generation_id: str) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM market_session_generations WHERE generation_id = ?",
            (str(generation_id),),
        ).fetchone()
        if row is None:
            raise PITReceiptError(f"market session generation does not exist: {generation_id}")
        result = dict(row)
        staged = [
            item[0]
            for item in connection.execute(
                """
                SELECT dataset FROM market_session_generation_shards
                WHERE generation_id = ? ORDER BY dataset
                """,
                (str(generation_id),),
            )
        ]
        dataset_order = {key: index for index, key in enumerate(MARKET_SESSION_DATASETS)}
        result["staged_datasets"] = sorted(
            staged, key=lambda key: dataset_order.get(key, len(dataset_order))
        )
        result["final_oos_eligible"] = bool(result["final_oos_eligible"])
        return result

    def begin_or_resume_market_session_generation(
        self,
        now: Any,
        trade_date: Any,
        *,
        vintage: str = DEFAULT_MARKET_SESSION_VINTAGE,
        force_new: bool = False,
        expected_request_semantics: Mapping[str, str] | None = None,
        expected_request_semantics_sha256: str | None = None,
    ) -> Dict[str, Any]:
        now_iso, now_utc = self._generation_timestamp(now, "generation now")
        session = _iso_date(trade_date, "trade_date")
        expected_json, expected_sha256 = self._expected_semantics_pin(
            expected_request_semantics,
            expected_request_semantics_sha256,
            MARKET_SESSION_DATASETS,
        )
        if vintage not in MARKET_SESSION_VINTAGES:
            raise PITReceiptError(
                f"market generation vintage must be one of {MARKET_SESSION_VINTAGES}"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT generation_id FROM market_session_generations
                WHERE trade_date = ? AND status = 'collecting'
                """,
                (session,),
            ).fetchone()
            if current is not None:
                view = self._market_session_view(connection, current[0])
                _started_iso, started_utc = self._generation_timestamp(
                    view["started_at"], "generation started_at"
                )
                if now_utc < started_utc:
                    raise PITReceiptError("generation clock moved backwards")
                complete = set(view["staged_datasets"]) == set(MARKET_SESSION_DATASETS)
                expired = (
                    not complete
                    and (now_utc - started_utc).total_seconds()
                    > MARKET_SESSION_GENERATION_WINDOW_SECONDS
                )
                pin_matches = expected_sha256 is None or (
                    view.get("expected_request_semantics_sha256") == expected_sha256
                    and view.get("expected_request_semantics_json") == expected_json
                )
                if not force_new and not expired and pin_matches:
                    return view
                reason = (
                    "force_new"
                    if force_new
                    else ("request_semantics_changed" if not pin_matches else "stale_incomplete")
                )
                connection.execute(
                    """
                    UPDATE market_session_generations
                    SET status = 'abandoned', terminal_at = ?, terminal_reason = ?
                    WHERE generation_id = ? AND status = 'collecting'
                    """,
                    (now_iso, reason, view["generation_id"]),
                )
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation_sequence), 0) + 1 "
                    "FROM market_session_generations"
                ).fetchone()[0]
            )
            generation_id = f"market-session-{next_sequence:020d}"
            connection.execute(
                """
                INSERT INTO market_session_generations (
                    generation_sequence, generation_id, trade_date,
                    contract_sha256, status, vintage, final_oos_eligible,
                    started_at, expected_request_semantics_json,
                    expected_request_semantics_sha256
                ) VALUES (?, ?, ?, ?, 'collecting', ?, 0, ?, ?, ?)
                """,
                (
                    next_sequence,
                    generation_id,
                    session,
                    _market_session_contract_sha256(),
                    str(vintage),
                    now_iso,
                    expected_json,
                    expected_sha256,
                ),
            )
            return self._market_session_view(connection, generation_id)

    @staticmethod
    def _membership_url_origin(value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise PITReceiptError(f"membership {field} is not canonical")
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise PITReceiptError(f"membership {field} is invalid") from exc
        scheme = parsed.scheme.lower()
        host = (parsed.hostname or "").lower()
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise PITReceiptError(f"membership {field} origin is invalid")
        effective_port = port or (443 if scheme == "https" else 80)
        rendered_host = f"[{host}]" if ":" in host else host
        return f"{scheme}://{rendered_host}:{effective_port}"

    @classmethod
    def _membership_source_authority(cls, semantics: Mapping[str, Any]) -> Dict[str, Any]:
        authority: Dict[str, Any] = {}
        for field in ("source_profile", "request_protocol", "network_route"):
            value = semantics.get(field)
            if not isinstance(value, str) or not value or value != value.strip():
                raise PITReceiptError(f"membership source authority {field} is invalid")
            authority[field] = value
        proxy = semantics.get("proxy_endpoint")
        if proxy is None:
            authority["proxy_endpoint"] = None
        else:
            if not isinstance(proxy, str) or not proxy or proxy != proxy.strip():
                raise PITReceiptError("membership source authority proxy_endpoint is invalid")
            parsed = urlsplit(proxy)
            if parsed.query or parsed.fragment or parsed.username or parsed.password:
                raise PITReceiptError(
                    "membership source authority proxy_endpoint may not contain credentials"
                )
            origin = cls._membership_url_origin(proxy, "proxy_endpoint")
            path = parsed.path.rstrip("/")
            authority["proxy_endpoint"] = origin + path
        authority["base_origin"] = cls._membership_url_origin(semantics.get("url"), "request URL")
        return authority

    @classmethod
    def _membership_attempt_authority(
        cls,
        attempt: Mapping[str, Any],
        *,
        dataset: str,
        partition_key: str,
    ) -> Tuple[Dict[str, Any], str, str]:
        try:
            semantics = json.loads(attempt["request_semantics_json"])
            params = json.loads(attempt["params_json"])
            fields = json.loads(attempt["fields_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise PITReceiptError("membership attempt request semantics are malformed") from exc
        if (
            not isinstance(semantics, dict)
            or _canonical_json(semantics) != attempt["request_semantics_json"]
        ):
            raise PITReceiptError("membership attempt request semantics are not canonical")
        if not hmac.compare_digest(
            _sha256(semantics),
            _require_sha256(
                attempt["request_semantics_sha256"],
                "membership attempt request semantics hash",
            ),
        ):
            raise PITReceiptError("membership attempt request semantics hash mismatch")
        try:
            normalized_params = _normalize_request_params(dataset, params)
        except (KeyError, PITReceiptError) as exc:
            raise PITReceiptError("membership attempt params are invalid") from exc
        if (
            attempt["dataset"] != dataset
            or attempt["partition_key"] != partition_key
            or attempt["endpoint"] != dataset
            or semantics.get("schema_version") != "tushare-wire-request/v1"
            or semantics.get("dataset") != dataset
            or semantics.get("partition_key") != partition_key
            or semantics.get("api_name") != dataset
            or semantics.get("method") != "POST"
            or semantics.get("receipt_params") != normalized_params
            or semantics.get("wire_params") != _canonical_wire_params(dataset, normalized_params)
            or semantics.get("fields") != fields
            or fields != list(NORMALIZED_FIELDS[dataset])
            or int(semantics.get("row_cap") or 0) != int(attempt["row_cap"])
        ):
            raise PITReceiptError("membership attempt request lineage mismatch")
        parsed_url = urlsplit(str(semantics.get("url") or ""))
        protocol = semantics.get("request_protocol")
        if parsed_url.query or parsed_url.fragment:
            raise PITReceiptError("membership request URL path is not canonical")
        if protocol == "tushare-path-per-interface/v1":
            if parsed_url.path.rstrip("/") != f"/{dataset}":
                raise PITReceiptError("membership request URL path differs from interface")
        elif protocol == "tushare-root-post/v1":
            if parsed_url.path not in {"", "/"}:
                raise PITReceiptError("membership request URL path must be root")
        else:
            raise PITReceiptError("membership request protocol is unsupported")
        temporal_role = semantics.get("temporal_role")
        if (
            not isinstance(temporal_role, str)
            or not temporal_role
            or temporal_role != temporal_role.strip()
        ):
            raise PITReceiptError("membership attempt temporal role is invalid")
        temporal_contract = _require_sha256(
            semantics.get("temporal_contract_sha256"),
            "membership attempt temporal contract hash",
        )
        return (
            cls._membership_source_authority(semantics),
            temporal_role,
            temporal_contract,
        )

    def _membership_market_authority_on_connection(
        self, connection: sqlite3.Connection, trade_date: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str, str]:
        active = self._active_market_session_on_connection(connection, trade_date)
        if active is None:
            raise PITReceiptError("active market generation is missing")
        generation, verified = active
        rows = list(
            connection.execute(
                """
                SELECT attempt.*
                FROM market_session_generation_shards AS shard
                JOIN fetch_attempts AS attempt ON attempt.attempt_id = shard.attempt_id
                WHERE shard.generation_id = ? ORDER BY shard.dataset
                """,
                (generation["generation_id"],),
            )
        )
        if len(rows) != len(MARKET_SESSION_DATASETS):
            raise PITReceiptError("membership market authority is incomplete")
        authorities = []
        for stored in rows:
            attempt = dict(stored)
            authorities.append(
                self._membership_attempt_authority(
                    attempt,
                    dataset=attempt["dataset"],
                    partition_key=trade_date,
                )
            )
        source, role, contract = authorities[0]
        if any(item != authorities[0] for item in authorities[1:]):
            raise PITReceiptError("membership market source/temporal authority mismatch")
        return generation, verified, source, role, contract

    @staticmethod
    def _membership_generation_view(
        connection: sqlite3.Connection, generation_id: str
    ) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM membership_session_generations WHERE generation_id = ?",
            (str(generation_id),),
        ).fetchone()
        if row is None:
            raise PITReceiptError(f"membership generation does not exist: {generation_id}")
        result = dict(row)
        result["signal_session_eligible"] = bool(result["signal_session_eligible"])
        result.pop("source_authority_json", None)
        manifest_json = result.pop("manifest_json", None)
        if manifest_json:
            try:
                manifest = json.loads(manifest_json)
            except json.JSONDecodeError as exc:
                raise PITReceiptError("membership generation manifest is malformed") from exc
            result.update(
                {
                    "manifest": manifest,
                    "anchor": manifest["anchor"],
                    "rows": manifest["rows"],
                    "summary": manifest["summary"],
                    "empty_evidence": manifest["empty_evidence"],
                    "market_generation": manifest["market_generation"],
                }
            )
        return result

    @staticmethod
    def _verify_membership_generation_identity(
        generation: Mapping[str, Any], *, collecting_unstaged: bool = False
    ) -> None:
        if generation.get("policy_version") != MEMBERSHIP_POLICY_VERSION:
            raise PITReceiptError("membership generation policy identity mismatch")
        try:
            sequence = int(generation["generation_sequence"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PITReceiptError("membership generation sequence identity is invalid") from exc
        if sequence <= 0 or generation.get("generation_id") != (
            f"membership-session-{sequence:020d}"
        ):
            raise PITReceiptError("membership generation sequence identity mismatch")
        trade_date = str(generation.get("trade_date") or "")
        try:
            if date.fromisoformat(trade_date).isoformat() != trade_date:
                raise ValueError
        except ValueError as exc:
            raise PITReceiptError("membership generation trade_date identity is invalid") from exc
        if bool(generation.get("signal_session_eligible")):
            raise PITReceiptError("membership generation eligibility identity mismatch")
        status = generation.get("status")
        if status == "published" and generation.get("terminal_reason") is not None:
            raise PITReceiptError("published membership generation lifecycle identity mismatch")
        if (
            status == "collecting"
            and not collecting_unstaged
            and any(
                generation.get(field) is not None for field in ("terminal_at", "terminal_reason")
            )
        ):
            raise PITReceiptError("staged membership generation lifecycle identity mismatch")
        if collecting_unstaged:
            nullable_fields = (
                "source_kind",
                "anchor_trade_date",
                "anchor_receipt_dataset",
                "anchor_receipt_partition",
                "anchor_receipt_raw_sha256",
                "anchor_normalized_sha256",
                "anchor_row_count",
                "anchor_in_scope_row_count",
                "manifest_json",
                "manifest_sha256",
                "lineage_sha256",
                "terminal_at",
                "terminal_reason",
            )
            if any(generation.get(field) is not None for field in nullable_fields):
                raise PITReceiptError("unstaged membership generation identity is not pristine")

    def begin_or_resume_membership_generation(
        self,
        now: Any,
        trade_date: Any,
        *,
        temporal_role: str,
        temporal_contract_sha256: str,
        force_new: bool = False,
    ) -> Dict[str, Any]:
        _now_iso, now_utc = self._generation_timestamp(now, "membership generation now")
        now_iso = now_utc.isoformat()
        session = _iso_date(trade_date, "trade_date")
        if (
            not isinstance(temporal_role, str)
            or not temporal_role
            or temporal_role != temporal_role.strip()
        ):
            raise PITReceiptError("membership temporal role is missing")
        temporal_contract = _require_sha256(
            temporal_contract_sha256, "membership temporal contract hash"
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exact = connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (session,),
            ).fetchone()
            if exact is not None:
                raise PITReceiptError(
                    "same-day bak_basic receipt exists; derived membership is forbidden"
                )
            market, verified, source, market_role, market_contract = (
                self._membership_market_authority_on_connection(connection, session)
            )
            if market_role != temporal_role or market_contract != temporal_contract:
                raise PITReceiptError("membership temporal authority differs from market")
            source_json = _canonical_json(source)
            source_sha256 = _sha256(source)
            current = connection.execute(
                """
                SELECT * FROM membership_session_generations
                WHERE trade_date = ? AND status = 'collecting'
                """,
                (session,),
            ).fetchone()
            if current is not None:
                current_generation = dict(current)
                self._verify_membership_generation_identity(
                    current_generation,
                    collecting_unstaged=current_generation["manifest_json"] is None,
                )
                view = self._membership_generation_view(
                    connection, current_generation["generation_id"]
                )
                _started_iso, started_utc = self._generation_timestamp(
                    view["started_at"], "membership generation started_at"
                )
                if now_utc < started_utc:
                    raise PITReceiptError("membership generation clock moved backwards")
                source_matches = (
                    view["source_authority_sha256"] == source_sha256
                    and connection.execute(
                        "SELECT source_authority_json FROM membership_session_generations "
                        "WHERE generation_id=?",
                        (view["generation_id"],),
                    ).fetchone()[0]
                    == source_json
                )
                temporal_matches = (
                    view["temporal_role"] == temporal_role
                    and view["temporal_contract_sha256"] == temporal_contract
                )
                market_matches = (
                    view["market_generation_id"] == market["generation_id"]
                    and view["market_manifest_sha256"] == verified["manifest_sha256"]
                    and view["market_lineage_sha256"] == verified["lineage_sha256"]
                )
                dependencies_match = source_matches and temporal_matches and market_matches
                if dependencies_match and current_generation["manifest_json"] is not None:
                    self._verify_staged_membership_on_connection(connection, current_generation)
                if not force_new and dependencies_match:
                    return view
                if force_new:
                    reason = "force_new"
                elif not market_matches:
                    reason = "market_head_changed"
                else:
                    reason = "authority_changed"
                connection.execute(
                    """
                    UPDATE membership_session_generations
                    SET status='abandoned', terminal_at=?, terminal_reason=?
                    WHERE generation_id=? AND status='collecting'
                    """,
                    (now_iso, reason, view["generation_id"]),
                )
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation_sequence), 0) + 1 "
                    "FROM membership_session_generations"
                ).fetchone()[0]
            )
            generation_id = f"membership-session-{next_sequence:020d}"
            connection.execute(
                """
                INSERT INTO membership_session_generations (
                    generation_sequence, generation_id, trade_date,
                    policy_version, status, source_kind,
                    signal_session_eligible, temporal_role,
                    temporal_contract_sha256, source_authority_json,
                    source_authority_sha256, market_generation_id,
                    market_manifest_sha256, market_lineage_sha256, started_at
                ) VALUES (?, ?, ?, ?, 'collecting', NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    next_sequence,
                    generation_id,
                    session,
                    MEMBERSHIP_POLICY_VERSION,
                    temporal_role,
                    temporal_contract,
                    source_json,
                    source_sha256,
                    market["generation_id"],
                    verified["manifest_sha256"],
                    verified["lineage_sha256"],
                    now_iso,
                ),
            )
            return self._membership_generation_view(connection, generation_id)

    def _membership_empty_evidence_on_connection(
        self,
        connection: sqlite3.Connection,
        generation: Mapping[str, Any],
        expected_source: Mapping[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        trade_date = str(generation["trade_date"])
        rows = list(
            connection.execute(
                """
                SELECT attempt.*, event.status AS terminal_status,
                       event.details_json AS terminal_details_json,
                       event.recorded_at AS terminal_recorded_at
                FROM fetch_attempts AS attempt
                LEFT JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                WHERE attempt.dataset='bak_basic'
                  AND attempt.partition_key=?
                ORDER BY attempt.attempt_sequence
                """,
                (trade_date,),
            )
        )
        if not rows:
            raise PITReceiptError("membership semantic-empty terminal evidence is missing")
        descriptors: List[Dict[str, Any]] = []
        links: List[Dict[str, Any]] = []
        for stored in rows:
            attempt = dict(stored)
            source, role, contract = self._membership_attempt_authority(
                attempt, dataset="bak_basic", partition_key=trade_date
            )
            # A different frozen temporal contract is a prior collection cohort
            # and cannot justify this generation. Within the same temporal
            # authority, however, a route/source change is ambiguous and poisons
            # the target cohort rather than being silently ignored.
            if (
                role != generation["temporal_role"]
                or contract != generation["temporal_contract_sha256"]
            ):
                continue
            if source != dict(expected_source):
                raise PITReceiptError("membership target cohort source authority mismatch")
            if attempt["terminal_status"] != "invalid_json":
                raise PITReceiptError(
                    "membership target cohort semantic-empty terminal evidence "
                    "is missing or conflicting"
                )
            if (
                attempt["http_status"] is None
                or not 200 <= int(attempt["http_status"]) < 300
                or int(attempt["body_complete"]) != 1
                or not attempt["raw_path"]
                or attempt["retrieved_at"] is None
            ):
                raise PITReceiptError(
                    "membership target cohort semantic-empty attempt is not a complete HTTP success"
                )
            # The controlled collector may label the planned empty response at
            # attempt-record time. That label is accepted only in its exact known
            # shape; classification still comes exclusively from re-parsing raw.
            if attempt["error_kind"] not in {None, "invalid_json"} or (
                attempt["error_kind"] == "invalid_json"
                and attempt["error_message"] != "planned response is empty"
            ):
                raise PITReceiptError(
                    "membership target cohort semantic-empty attempt has conflicting "
                    "transport evidence"
                )
            try:
                event_details = json.loads(attempt["terminal_details_json"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise PITReceiptError(
                    "membership semantic-empty terminal event is malformed"
                ) from exc
            if _canonical_json(event_details) != attempt["terminal_details_json"]:
                raise PITReceiptError("membership semantic-empty terminal event is not canonical")
            raw = self._verify_raw_file(attempt)
            try:
                classification = classify_membership_snapshot_body(
                    raw, required_fields=NORMALIZED_FIELDS["bak_basic"]
                )
            except MembershipContractError as exc:
                raise PITReceiptError(
                    "membership semantic-empty raw body failed classification"
                ) from exc
            if classification.kind != "semantic_empty":
                raise PITReceiptError(
                    "membership semantic-empty raw body is not semantically empty"
                )
            descriptor = {
                "trade_date": trade_date,
                "attempt_id": attempt["attempt_id"],
                "raw_sha256": attempt["raw_sha256"],
                "request_semantics_sha256": attempt["request_semantics_sha256"],
                "terminal_status": "invalid_json",
                "classification_kind": "semantic_empty",
                "temporal_role": generation["temporal_role"],
                "temporal_contract_sha256": generation["temporal_contract_sha256"],
            }
            descriptors.append(descriptor)
            links.append(
                {
                    "generation_id": generation["generation_id"],
                    "evidence_kind": "target_semantic_empty",
                    "attempt_id": attempt["attempt_id"],
                    "trade_date": trade_date,
                    "raw_sha256": attempt["raw_sha256"],
                    "request_semantics_sha256": attempt["request_semantics_sha256"],
                    "terminal_status": "invalid_json",
                    "classification_kind": "semantic_empty",
                }
            )
        if not descriptors:
            raise PITReceiptError(
                "membership target cohort semantic-empty terminal evidence is missing"
            )
        return descriptors, links

    def _membership_anchor_candidate_on_connection(
        self,
        connection: sqlite3.Connection,
        generation: Mapping[str, Any],
        expected_source: Mapping[str, Any],
        stored_receipt: Mapping[str, Any],
    ) -> Tuple[
        Dict[str, Any] | None,
        List[Dict[str, Any]],
        Dict[str, Any] | None,
    ]:
        receipt = dict(stored_receipt)
        anchor_date = str(receipt["partition_key"])
        if (
            receipt["dataset"] != "bak_basic"
            or receipt["endpoint"] != "bak_basic"
            or receipt["http_status"] is None
            or not 200 <= int(receipt["http_status"]) < 300
            or int(receipt["response_code"]) != 0
            or int(receipt["row_count"]) <= 0
            or receipt["parser_version"] != PARSER_VERSION
        ):
            raise PITReceiptError("membership anchor receipt metadata is invalid")
        self._generation_timestamp(
            receipt["retrieved_at"], "membership anchor receipt retrieved_at"
        )
        raw = self._verify_raw_file(receipt)
        try:
            fields, items, response_code, _message = _parse_native_envelope(raw)
            params = json.loads(receipt["params_json"])
            if (
                _canonical_json(params) != receipt["params_json"]
                or _canonical_partition_key("bak_basic", params) != anchor_date
            ):
                raise PITReceiptError("membership anchor receipt params are not canonical")
            normalized = _normalize_rows("bak_basic", anchor_date, params, fields, items)
        except (json.JSONDecodeError, PITReceiptError) as exc:
            raise PITReceiptError("membership anchor receipt cannot be rebuilt") from exc
        stored_rows = self._normalized_rows_from_db(connection, "bak_basic", anchor_date)
        if (
            response_code != 0
            or len(normalized) != int(receipt["row_count"])
            or _sha256(normalized) != receipt["normalized_sha256"]
            or stored_rows != normalized
        ):
            raise PITReceiptError("membership anchor receipt lineage mismatch")

        stored_attempts = list(
            connection.execute(
                """
                SELECT attempt.*, event.status AS terminal_status,
                       event.details_json AS terminal_details_json,
                       event.recorded_at AS terminal_recorded_at
                FROM fetch_attempts AS attempt
                LEFT JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                WHERE attempt.dataset='bak_basic'
                  AND attempt.partition_key=?
                ORDER BY attempt.attempt_sequence
                """,
                (anchor_date,),
            )
        )
        linked_attempts: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for stored_attempt in stored_attempts:
            candidate = dict(stored_attempt)
            raw_link = candidate["raw_sha256"] == receipt["raw_sha256"]
            details = None
            if candidate["terminal_details_json"] is not None:
                try:
                    parsed_details = json.loads(candidate["terminal_details_json"])
                except (TypeError, json.JSONDecodeError) as exc:
                    if raw_link:
                        raise PITReceiptError(
                            "membership anchor terminal event is malformed"
                        ) from exc
                else:
                    if (
                        not isinstance(parsed_details, dict)
                        or _canonical_json(parsed_details) != candidate["terminal_details_json"]
                    ):
                        if raw_link or (
                            isinstance(parsed_details, dict)
                            and parsed_details.get("receipt_raw_sha256") == receipt["raw_sha256"]
                        ):
                            raise PITReceiptError("membership anchor terminal event is malformed")
                    else:
                        details = parsed_details
            event_link = (
                details is not None and details.get("receipt_raw_sha256") == receipt["raw_sha256"]
            )
            if raw_link or event_link:
                if details is None:
                    raise PITReceiptError("membership anchor terminal event is missing")
                linked_attempts.append((candidate, details))
        if not linked_attempts:
            raise PITReceiptError("membership anchor successful attempt evidence is missing")
        anchor_attempt = None
        for candidate, details in linked_attempts:
            if (
                candidate["terminal_status"] not in {"stored", "reused"}
                or details.get("receipt_raw_sha256") != receipt["raw_sha256"]
                or candidate["raw_sha256"] != receipt["raw_sha256"]
                or candidate["error_kind"] is not None
                or candidate["http_status"] is None
                or not 200 <= int(candidate["http_status"]) < 300
                or int(candidate["body_complete"]) != 1
                or not candidate["raw_path"]
                or candidate["raw_bytes"] != receipt["raw_bytes"]
                or candidate["started_at"] is None
                or candidate["retrieved_at"] is None
                or candidate["terminal_recorded_at"] is None
            ):
                raise PITReceiptError("membership anchor attempt lineage mismatch")
            if self._verify_raw_file(candidate) != raw:
                raise PITReceiptError("membership anchor attempt raw body mismatch")
            _attempt_started_iso, attempt_started = self._generation_timestamp(
                candidate["started_at"], "membership anchor attempt started_at"
            )
            _retrieved_iso, retrieved = self._generation_timestamp(
                candidate["retrieved_at"], "membership anchor attempt retrieved_at"
            )
            _terminal_iso, terminal = self._generation_timestamp(
                candidate["terminal_recorded_at"],
                "membership anchor terminal recorded_at",
            )
            if retrieved < attempt_started or terminal < retrieved:
                raise PITReceiptError("membership anchor attempt chronology mismatch")
            source, role, contract = self._membership_attempt_authority(
                candidate, dataset="bak_basic", partition_key=anchor_date
            )
            if (
                source == dict(expected_source)
                and role == generation["temporal_role"]
                and contract == generation["temporal_contract_sha256"]
                and anchor_attempt is None
            ):
                anchor_attempt = candidate
        if anchor_attempt is None:
            return None, [], None
        anchor_rows = []
        for row in stored_rows:
            if not _is_supported_a_share_snapshot_code(row["ts_code"], row["exchange"]):
                continue
            if row["list_date"] is None or row["list_date"] > anchor_date:
                raise PITReceiptError(
                    "membership anchor supported A-share list_date is missing or after anchor"
                )
            anchor_rows.append(
                {
                    "trade_date": anchor_date,
                    "ts_code": row["ts_code"],
                    "exchange": row["exchange"],
                    "name": row["name"],
                    "industry": row["industry"],
                    "list_date": row["list_date"],
                }
            )
        if not anchor_rows:
            raise PITReceiptError("membership anchor contains no in-scope listed A-share rows")
        descriptor = {
            "trade_date": anchor_date,
            "receipt_dataset": "bak_basic",
            "receipt_partition": anchor_date,
            "receipt_raw_sha256": receipt["raw_sha256"],
            "normalized_sha256": receipt["normalized_sha256"],
            "row_count": int(receipt["row_count"]),
            "in_scope_row_count": len(anchor_rows),
            "temporal_role": generation["temporal_role"],
            "temporal_contract_sha256": generation["temporal_contract_sha256"],
        }
        link = {
            "generation_id": generation["generation_id"],
            "evidence_kind": "anchor_snapshot_attempt",
            "attempt_id": anchor_attempt["attempt_id"],
            "trade_date": anchor_date,
            "raw_sha256": anchor_attempt["raw_sha256"],
            "request_semantics_sha256": anchor_attempt["request_semantics_sha256"],
            "terminal_status": anchor_attempt["terminal_status"],
            "classification_kind": "nonempty_snapshot",
        }
        return descriptor, anchor_rows, link

    def _membership_anchor_on_connection(
        self,
        connection: sqlite3.Connection,
        generation: Mapping[str, Any],
        expected_source: Mapping[str, Any],
    ) -> Tuple[
        Dict[str, Any] | None,
        List[Dict[str, Any]],
        Dict[str, Any] | None,
    ]:
        receipts = connection.execute(
            """
            SELECT * FROM receipts
            WHERE dataset='bak_basic' AND partition_key < ?
            ORDER BY partition_key DESC
            """,
            (str(generation["trade_date"]),),
        )
        for receipt in receipts:
            candidate = self._membership_anchor_candidate_on_connection(
                connection, generation, expected_source, receipt
            )
            if candidate[0] is not None:
                return candidate
        return None, [], None

    def _membership_live_materialization_on_connection(
        self, connection: sqlite3.Connection, generation: Mapping[str, Any]
    ) -> Dict[str, Any]:
        if (
            connection.execute(
                "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (generation["trade_date"],),
            ).fetchone()
            is not None
        ):
            raise PITReceiptError("same-day bak_basic receipt exists; derived membership is stale")
        market, market_verified, source, role, contract = (
            self._membership_market_authority_on_connection(connection, generation["trade_date"])
        )
        if (
            market["generation_id"] != generation["market_generation_id"]
            or market_verified["manifest_sha256"] != generation["market_manifest_sha256"]
            or market_verified["lineage_sha256"] != generation["market_lineage_sha256"]
        ):
            raise PITReceiptError("membership bound market generation is stale")
        if (
            role != generation["temporal_role"]
            or contract != generation["temporal_contract_sha256"]
        ):
            raise PITReceiptError("membership market temporal authority mismatch")
        source_json = _canonical_json(source)
        if (
            _sha256(source) != generation["source_authority_sha256"]
            or source_json != generation["source_authority_json"]
        ):
            raise PITReceiptError("membership market source authority mismatch")

        empty_descriptors, evidence_links = self._membership_empty_evidence_on_connection(
            connection, generation, source
        )
        anchor, anchor_rows, anchor_link = self._membership_anchor_on_connection(
            connection, generation, source
        )
        if anchor_link is not None:
            evidence_links.append(anchor_link)
        daily_codes = set()
        for row in connection.execute(
            """
            SELECT ts_code FROM market_session_generation_rows_daily
            WHERE generation_id=? ORDER BY ts_code
            """,
            (generation["market_generation_id"],),
        ):
            code = str(row["ts_code"])
            suffix = code.rsplit(".", 1)[-1] if "." in code else ""
            exchange = {"SH": "SSE", "SZ": "SZSE"}.get(suffix)
            if exchange and _is_supported_a_share_snapshot_code(code, exchange):
                daily_codes.add(code)
        try:
            projection = derive_quarantined_membership(
                trade_date=generation["trade_date"],
                anchor_trade_date=anchor["trade_date"] if anchor else None,
                anchor_rows=anchor_rows,
                daily_codes=daily_codes,
            )
            manifest = build_membership_manifest(
                trade_date=generation["trade_date"],
                source_kind=projection.source_kind,
                anchor=anchor,
                empty_evidence=empty_descriptors,
                market_generation={
                    "trade_date": generation["trade_date"],
                    "generation_id": market["generation_id"],
                    "manifest_sha256": market_verified["manifest_sha256"],
                    "lineage_sha256": market_verified["lineage_sha256"],
                    "temporal_role": generation["temporal_role"],
                    "temporal_contract_sha256": generation["temporal_contract_sha256"],
                },
                temporal_authority={
                    "temporal_role": generation["temporal_role"],
                    "temporal_contract_sha256": generation["temporal_contract_sha256"],
                },
                rows=projection.rows,
            )
            manifest = verify_membership_manifest(manifest)
        except MembershipContractError as exc:
            raise PITReceiptError("membership manifest contract failed") from exc
        evidence_links.sort(key=lambda item: (item["evidence_kind"], item["attempt_id"]))
        return {
            "manifest": manifest,
            "manifest_sha256": manifest["manifest_sha256"],
            "rows": manifest["rows"],
            "evidence_links": evidence_links,
            "source_kind": projection.source_kind,
            "anchor": anchor,
        }

    @staticmethod
    def _membership_rows_on_connection(
        connection: sqlite3.Connection, generation_id: str
    ) -> List[Dict[str, Any]]:
        output = []
        for stored in connection.execute(
            """
            SELECT trade_date, ts_code, exchange, name, industry, list_date,
                   membership_present, metadata_stale_possible, unknown_metadata,
                   observed_in_daily, signal_session_eligible, signal_eligible,
                   source_kind, metadata_anchor_date
            FROM membership_generation_rows
            WHERE generation_id=? ORDER BY ts_code
            """,
            (str(generation_id),),
        ):
            row = dict(stored)
            for field in (
                "membership_present",
                "metadata_stale_possible",
                "unknown_metadata",
                "observed_in_daily",
                "signal_session_eligible",
                "signal_eligible",
            ):
                row[field] = bool(row[field])
            output.append(row)
        return output

    @staticmethod
    def _membership_evidence_on_connection(
        connection: sqlite3.Connection, generation_id: str
    ) -> List[Dict[str, Any]]:
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT generation_id, evidence_kind, attempt_id, trade_date,
                       raw_sha256, request_semantics_sha256, terminal_status,
                       classification_kind
                FROM membership_generation_evidence
                WHERE generation_id=? ORDER BY evidence_kind, attempt_id
                """,
                (str(generation_id),),
            )
        ]

    @staticmethod
    def _membership_lineage_sha256_on_connection(
        connection: sqlite3.Connection, generation_id: str
    ) -> str:
        generation = connection.execute(
            """
            SELECT generation_sequence, generation_id, trade_date, policy_version,
                   source_kind, signal_session_eligible, temporal_role,
                   temporal_contract_sha256, source_authority_json,
                   source_authority_sha256, market_generation_id,
                   market_manifest_sha256, market_lineage_sha256,
                   anchor_trade_date, anchor_receipt_dataset,
                   anchor_receipt_partition, anchor_receipt_raw_sha256,
                   anchor_normalized_sha256, anchor_row_count,
                   anchor_in_scope_row_count, started_at, manifest_json,
                   manifest_sha256
            FROM membership_session_generations WHERE generation_id=?
            """,
            (str(generation_id),),
        ).fetchone()
        if generation is None:
            raise PITReceiptError("membership generation lineage is incomplete")
        evidence = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM membership_generation_evidence "
                "WHERE generation_id=? ORDER BY evidence_kind, attempt_id",
                (str(generation_id),),
            )
        ]
        attempts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT attempt.* FROM fetch_attempts AS attempt
                JOIN membership_generation_evidence AS evidence
                  ON evidence.attempt_id=attempt.attempt_id
                WHERE evidence.generation_id=?
                ORDER BY evidence.evidence_kind, evidence.attempt_id
                """,
                (str(generation_id),),
            )
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT event.* FROM fetch_promotion_events AS event
                JOIN membership_generation_evidence AS evidence
                  ON evidence.attempt_id=event.attempt_id
                WHERE evidence.generation_id=?
                ORDER BY evidence.evidence_kind, evidence.attempt_id
                """,
                (str(generation_id),),
            )
        ]
        if len(attempts) != len(evidence) or len(events) != len(evidence):
            raise PITReceiptError("membership generation evidence lineage is incomplete")
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM membership_generation_rows WHERE generation_id=? ORDER BY ts_code",
                (str(generation_id),),
            )
        ]
        anchor_receipt = None
        if generation["anchor_receipt_partition"] is not None:
            stored_anchor = connection.execute(
                "SELECT * FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                (generation["anchor_receipt_partition"],),
            ).fetchone()
            if stored_anchor is None:
                raise PITReceiptError("membership anchor receipt lineage is incomplete")
            anchor_receipt = dict(stored_anchor)
        market_head = connection.execute(
            "SELECT * FROM market_session_generation_head WHERE trade_date=?",
            (generation["trade_date"],),
        ).fetchone()
        if market_head is None:
            raise PITReceiptError("membership market head lineage is incomplete")
        return _sha256(
            {
                "schema_version": "membership-session-generation-lineage/v1",
                "generation": dict(generation),
                "rows": rows,
                "evidence": evidence,
                "attempts": attempts,
                "promotion_events": events,
                "anchor_receipt": anchor_receipt,
                "market_head": dict(market_head),
            }
        )

    def _verify_staged_membership_on_connection(
        self, connection: sqlite3.Connection, generation: Mapping[str, Any]
    ) -> Dict[str, Any]:
        self._verify_membership_generation_identity(generation)
        live = self._membership_live_materialization_on_connection(connection, generation)
        stored_manifest_json = generation.get("manifest_json")
        if not stored_manifest_json:
            raise PITReceiptError("membership generation has not been staged")
        try:
            stored_manifest = json.loads(stored_manifest_json)
            stored_manifest = verify_membership_manifest(stored_manifest)
        except (json.JSONDecodeError, MembershipContractError) as exc:
            raise PITReceiptError("membership staged manifest is invalid") from exc
        if (
            _canonical_json(stored_manifest) != stored_manifest_json
            or stored_manifest != live["manifest"]
            or generation["manifest_sha256"] != live["manifest_sha256"]
            or generation["source_kind"] != live["source_kind"]
            or bool(generation["signal_session_eligible"])
        ):
            raise PITReceiptError("membership staged manifest immutable conflict")
        anchor = live["anchor"]
        expected_anchor = (
            (
                anchor["trade_date"],
                anchor["receipt_dataset"],
                anchor["receipt_partition"],
                anchor["receipt_raw_sha256"],
                anchor["normalized_sha256"],
                int(anchor["row_count"]),
                int(anchor["in_scope_row_count"]),
            )
            if anchor
            else (None, None, None, None, None, None, None)
        )
        actual_anchor = (
            generation["anchor_trade_date"],
            generation["anchor_receipt_dataset"],
            generation["anchor_receipt_partition"],
            generation["anchor_receipt_raw_sha256"],
            generation["anchor_normalized_sha256"],
            generation["anchor_row_count"],
            generation["anchor_in_scope_row_count"],
        )
        if actual_anchor != expected_anchor:
            raise PITReceiptError("membership staged anchor immutable conflict")
        if (
            self._membership_rows_on_connection(connection, generation["generation_id"])
            != live["rows"]
        ):
            raise PITReceiptError("membership staged rows immutable conflict")
        if (
            self._membership_evidence_on_connection(connection, generation["generation_id"])
            != live["evidence_links"]
        ):
            raise PITReceiptError("membership staged evidence immutable conflict")
        lineage = self._membership_lineage_sha256_on_connection(
            connection, generation["generation_id"]
        )
        if not generation.get("lineage_sha256") or not hmac.compare_digest(
            str(generation["lineage_sha256"]), lineage
        ):
            raise PITReceiptError("membership generation lineage mismatch")
        return {**live, "lineage_sha256": lineage}

    def stage_membership_generation(self, generation_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._membership_generation_view(connection, generation_id)
            stored_generation = dict(
                connection.execute(
                    "SELECT * FROM membership_session_generations WHERE generation_id=?",
                    (str(generation_id),),
                ).fetchone()
            )
            self._verify_membership_generation_identity(
                stored_generation,
                collecting_unstaged=stored_generation["manifest_json"] is None,
            )
            if generation["status"] == "abandoned":
                raise PITReceiptError("membership generation is abandoned or inactive")
            if stored_generation["manifest_json"]:
                verified = self._verify_staged_membership_on_connection(
                    connection, stored_generation
                )
                if stored_generation["status"] == "published":
                    active = self._active_membership_on_connection(
                        connection, stored_generation["trade_date"]
                    )
                    if active is None or active[0]["generation_id"] != generation_id:
                        raise PITReceiptError("published membership head mismatch")
                return {
                    **self._membership_generation_view(connection, generation_id),
                    **verified,
                    "reused": True,
                }
            if generation["status"] != "collecting":
                raise PITReceiptError("membership generation is not collectable")
            live = self._membership_live_materialization_on_connection(
                connection, stored_generation
            )
            anchor = live["anchor"]
            connection.execute(
                """
                UPDATE membership_session_generations SET
                    source_kind=?, anchor_trade_date=?, anchor_receipt_dataset=?,
                    anchor_receipt_partition=?, anchor_receipt_raw_sha256=?,
                    anchor_normalized_sha256=?, anchor_row_count=?,
                    anchor_in_scope_row_count=?, manifest_json=?, manifest_sha256=?
                WHERE generation_id=? AND status='collecting' AND manifest_json IS NULL
                """,
                (
                    live["source_kind"],
                    anchor["trade_date"] if anchor else None,
                    anchor["receipt_dataset"] if anchor else None,
                    anchor["receipt_partition"] if anchor else None,
                    anchor["receipt_raw_sha256"] if anchor else None,
                    anchor["normalized_sha256"] if anchor else None,
                    anchor["row_count"] if anchor else None,
                    anchor["in_scope_row_count"] if anchor else None,
                    _canonical_json(live["manifest"]),
                    live["manifest_sha256"],
                    str(generation_id),
                ),
            )
            for row in live["rows"]:
                connection.execute(
                    """
                    INSERT INTO membership_generation_rows (
                        generation_id, trade_date, ts_code, exchange, name,
                        industry, list_date, membership_present,
                        metadata_stale_possible, unknown_metadata,
                        observed_in_daily, signal_session_eligible,
                        signal_eligible, source_kind, metadata_anchor_date
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?)
                    """,
                    (
                        str(generation_id),
                        row["trade_date"],
                        row["ts_code"],
                        row["exchange"],
                        row["name"],
                        row["industry"],
                        row["list_date"],
                        int(row["membership_present"]),
                        int(row["metadata_stale_possible"]),
                        int(row["unknown_metadata"]),
                        int(row["observed_in_daily"]),
                        row["source_kind"],
                        row["metadata_anchor_date"],
                    ),
                )
            for evidence in live["evidence_links"]:
                connection.execute(
                    """
                    INSERT INTO membership_generation_evidence (
                        generation_id, evidence_kind, attempt_id, trade_date,
                        raw_sha256, request_semantics_sha256, terminal_status,
                        classification_kind
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    tuple(
                        evidence[field]
                        for field in (
                            "generation_id",
                            "evidence_kind",
                            "attempt_id",
                            "trade_date",
                            "raw_sha256",
                            "request_semantics_sha256",
                            "terminal_status",
                            "classification_kind",
                        )
                    ),
                )
            lineage = self._membership_lineage_sha256_on_connection(connection, str(generation_id))
            connection.execute(
                "UPDATE membership_session_generations SET lineage_sha256=? WHERE generation_id=?",
                (lineage, str(generation_id)),
            )
            return {
                **self._membership_generation_view(connection, generation_id),
                "lineage_sha256": lineage,
                "reused": False,
            }

    def _membership_causal_terminal_at_on_connection(
        self, connection: sqlite3.Connection, generation: Mapping[str, Any]
    ) -> str:
        _started_iso, generation_started = self._generation_timestamp(
            generation["started_at"], "membership generation started_at"
        )
        causal_times = [generation_started]
        market = connection.execute(
            """
            SELECT generation.started_at AS market_started_at,
                   generation.terminal_at AS market_terminal_at,
                   head.published_at AS market_published_at
            FROM market_session_generations AS generation
            JOIN market_session_generation_head AS head
              ON head.generation_id=generation.generation_id
            WHERE generation.generation_id=? AND head.trade_date=?
            """,
            (generation["market_generation_id"], generation["trade_date"]),
        ).fetchone()
        if (
            market is None
            or market["market_terminal_at"] is None
            or market["market_published_at"] is None
        ):
            raise PITReceiptError("membership publication chronology lacks market time")
        _market_started_iso, market_started = self._generation_timestamp(
            market["market_started_at"], "membership bound market started_at"
        )
        shard_times = []
        shard_datasets = []
        for shard in connection.execute(
            """
            SELECT dataset, retrieved_at
            FROM market_session_generation_shards
            WHERE generation_id=?
            ORDER BY dataset
            """,
            (generation["market_generation_id"],),
        ):
            shard_datasets.append(str(shard["dataset"]))
            _shard_iso, shard_utc = self._generation_timestamp(
                shard["retrieved_at"], "membership bound market shard retrieved_at"
            )
            if shard_utc < market_started:
                raise PITReceiptError("membership publication chronology reverses market time")
            shard_times.append(shard_utc)
        if len(shard_datasets) != len(MARKET_SESSION_DATASETS) or set(shard_datasets) != set(
            MARKET_SESSION_DATASETS
        ):
            raise PITReceiptError("membership bound market chronology lacks shard evidence")
        expected_market_terminal = max(shard_times).isoformat()
        for field in ("market_terminal_at", "market_published_at"):
            value_iso, value_utc = self._generation_timestamp(
                market[field], f"membership bound {field}"
            )
            if value_utc < market_started:
                raise PITReceiptError("membership publication chronology reverses market time")
            if value_iso != expected_market_terminal:
                raise PITReceiptError("membership bound market publication chronology mismatch")
            causal_times.append(value_utc)

        evidence = list(
            connection.execute(
                """
                SELECT attempt.started_at, attempt.retrieved_at,
                       event.recorded_at AS event_recorded_at
                FROM membership_generation_evidence AS evidence
                JOIN fetch_attempts AS attempt
                  ON attempt.attempt_id=evidence.attempt_id
                JOIN fetch_promotion_events AS event
                  ON event.attempt_id=evidence.attempt_id
                WHERE evidence.generation_id=?
                ORDER BY evidence.evidence_kind, evidence.attempt_id
                """,
                (generation["generation_id"],),
            )
        )
        expected_count = int(
            connection.execute(
                "SELECT COUNT(*) FROM membership_generation_evidence WHERE generation_id=?",
                (generation["generation_id"],),
            ).fetchone()[0]
        )
        if not evidence or len(evidence) != expected_count:
            raise PITReceiptError("membership publication chronology lacks evidence time")
        for row in evidence:
            if row["started_at"] is None or row["retrieved_at"] is None:
                raise PITReceiptError(
                    "membership publication chronology has incomplete attempt time"
                )
            _attempt_started_iso, attempt_started = self._generation_timestamp(
                row["started_at"], "membership evidence attempt started_at"
            )
            _retrieved_iso, retrieved = self._generation_timestamp(
                row["retrieved_at"], "membership evidence attempt retrieved_at"
            )
            _event_iso, event_recorded = self._generation_timestamp(
                row["event_recorded_at"], "membership evidence terminal recorded_at"
            )
            if retrieved < attempt_started or event_recorded < retrieved:
                raise PITReceiptError("membership publication chronology reverses evidence time")
            causal_times.extend((retrieved, event_recorded))
        return max(causal_times).isoformat()

    def publish_membership_generation(self, generation_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stored = connection.execute(
                "SELECT * FROM membership_session_generations WHERE generation_id=?",
                (str(generation_id),),
            ).fetchone()
            if stored is None:
                raise PITReceiptError(f"membership generation does not exist: {generation_id}")
            generation = dict(stored)
            if generation["status"] == "abandoned":
                raise PITReceiptError("membership generation is abandoned or inactive")
            verified = self._verify_staged_membership_on_connection(connection, generation)
            if generation["status"] == "published":
                active = self._active_membership_on_connection(connection, generation["trade_date"])
                if active is None or active[0]["generation_id"] != generation["generation_id"]:
                    raise PITReceiptError("published membership head mismatch")
                return {
                    **self._membership_generation_view(connection, generation_id),
                    **verified,
                    "reused": True,
                }
            if generation["status"] != "collecting":
                raise PITReceiptError("membership generation is not publishable")
            prior_published_exists = connection.execute(
                "SELECT 1 FROM membership_session_generations "
                "WHERE trade_date=? AND status='published' LIMIT 1",
                (generation["trade_date"],),
            ).fetchone()
            prior_head_exists = connection.execute(
                "SELECT 1 FROM membership_session_head WHERE trade_date=?",
                (generation["trade_date"],),
            ).fetchone()
            if prior_published_exists is None:
                if prior_head_exists is not None:
                    raise PITReceiptError("membership head exists without a published generation")
            else:
                if prior_head_exists is None:
                    raise PITReceiptError("published membership generation lacks active head")
                prior_active = self._active_membership_on_connection(
                    connection, generation["trade_date"]
                )
                if prior_active is None or int(prior_active[0]["generation_sequence"]) >= int(
                    generation["generation_sequence"]
                ):
                    raise PITReceiptError("membership generation head rollback detected")
            published_at = self._membership_causal_terminal_at_on_connection(connection, generation)
            connection.execute(
                """
                UPDATE membership_session_generations
                SET status='published', terminal_at=?, terminal_reason=NULL
                WHERE generation_id=? AND status='collecting'
                """,
                (published_at, str(generation_id)),
            )
            connection.execute(
                """
                INSERT INTO membership_session_head (
                    trade_date, generation_id, manifest_sha256,
                    lineage_sha256, published_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    generation_id=excluded.generation_id,
                    manifest_sha256=excluded.manifest_sha256,
                    lineage_sha256=excluded.lineage_sha256,
                    published_at=excluded.published_at
                """,
                (
                    generation["trade_date"],
                    str(generation_id),
                    verified["manifest_sha256"],
                    verified["lineage_sha256"],
                    published_at,
                ),
            )
            return {
                **self._membership_generation_view(connection, generation_id),
                **verified,
                "reused": False,
            }

    def _active_membership_on_connection(
        self, connection: sqlite3.Connection, trade_date: str
    ) -> Tuple[Dict[str, Any], Dict[str, Any]] | None:
        head = connection.execute(
            "SELECT * FROM membership_session_head WHERE trade_date=?",
            (trade_date,),
        ).fetchone()
        if head is None:
            return None
        stored = connection.execute(
            "SELECT * FROM membership_session_generations WHERE generation_id=?",
            (head["generation_id"],),
        ).fetchone()
        if stored is None:
            raise PITReceiptError("active membership generation is missing")
        generation = dict(stored)
        self._verify_membership_generation_identity(generation)
        if generation["trade_date"] != trade_date or generation["status"] != "published":
            raise PITReceiptError("active membership generation is invalid")
        latest = connection.execute(
            "SELECT MAX(generation_sequence) FROM membership_session_generations "
            "WHERE trade_date=? AND status='published'",
            (trade_date,),
        ).fetchone()[0]
        if latest is None or int(latest) != int(generation["generation_sequence"]):
            raise PITReceiptError("active membership generation head rollback detected")
        verified = self._verify_staged_membership_on_connection(connection, generation)
        if (
            head["manifest_sha256"] != verified["manifest_sha256"]
            or head["lineage_sha256"] != verified["lineage_sha256"]
            or generation["manifest_sha256"] != verified["manifest_sha256"]
            or generation["lineage_sha256"] != verified["lineage_sha256"]
        ):
            raise PITReceiptError("active membership generation head hash mismatch")
        expected_terminal = self._membership_causal_terminal_at_on_connection(
            connection, generation
        )
        if generation["terminal_at"] is None or head["published_at"] is None:
            raise PITReceiptError("active membership publication chronology is incomplete")
        try:
            terminal_iso, _terminal_utc = self._generation_timestamp(
                generation["terminal_at"], "membership generation terminal_at"
            )
            head_iso, _head_utc = self._generation_timestamp(
                head["published_at"], "membership head published_at"
            )
        except PITReceiptError as exc:
            raise PITReceiptError("active membership publication chronology is invalid") from exc
        if terminal_iso != expected_terminal or head_iso != expected_terminal:
            raise PITReceiptError("active membership publication chronology mismatch")
        return generation, verified

    def active_membership_generation(self, trade_date: Any) -> Dict[str, Any] | None:
        session = _iso_date(trade_date, "trade_date")
        with self._connect() as connection:
            connection.execute("BEGIN")
            active = self._active_membership_on_connection(connection, session)
            if active is None:
                return None
            generation, verified = active
            return {
                **self._membership_generation_view(connection, generation["generation_id"]),
                **verified,
            }

    def verify_membership_generation(self, trade_date: Any) -> Dict[str, Any]:
        session = _iso_date(trade_date, "trade_date")
        with self._connect() as connection:
            connection.execute("BEGIN")
            active = self._active_membership_on_connection(connection, session)
            if active is None:
                raise PITReceiptError("active membership generation is missing")
            generation, verified = active
            return {
                **self._membership_generation_view(connection, generation["generation_id"]),
                **verified,
                "verification_status": "passed",
            }

    @staticmethod
    def _etf_proxy_generation_view(
        connection: sqlite3.Connection, generation_id: str
    ) -> Dict[str, Any]:
        row = connection.execute(
            "SELECT * FROM etf_proxy_generations WHERE generation_id = ?",
            (str(generation_id),),
        ).fetchone()
        if row is None:
            raise PITReceiptError(f"etf proxy generation does not exist: {generation_id}")
        result = dict(row)
        staged = [
            item[0]
            for item in connection.execute(
                "SELECT symbol FROM etf_proxy_generation_shards WHERE generation_id = ?",
                (str(generation_id),),
            )
        ]
        symbol_order = {symbol: index for index, symbol in enumerate(ETF_PROXY_REQUIRED_SYMBOLS)}
        # WHY fixed order: downstream publish/audit consumes the two proxy
        # symbols in one canonical order regardless of insertion order.
        result["staged_symbols"] = sorted(
            staged, key=lambda symbol: symbol_order.get(symbol, len(symbol_order))
        )
        result["final_oos_eligible"] = bool(result["final_oos_eligible"])
        return result

    def begin_or_resume_etf_proxy_generation(
        self,
        now: Any,
        start_date: Any,
        end_date: Any,
        *,
        vintage: str = "historical_backfill",
        force_new: bool = False,
    ) -> Dict[str, Any]:
        """Open or resume the collecting ETF proxy generation for one date range.

        The scope and contract are derived solely from the frozen schema, the two
        required proxy symbols, and the caller's date range; callers cannot pass
        a scope or symbol. A generation is complete once exactly the two required
        symbol shards are staged. A complete generation is never timed out -- it
        waits for publish -- while an incomplete one past the collecting window is
        atomically abandoned as ``stale_incomplete`` and a fresh one starts.
        ``final_oos_eligible`` always stays False and no network is touched here.
        """

        now_iso, now_utc = self._generation_timestamp(now, "generation now")
        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if end < start:
            raise PITReceiptError("end_date must not precede start_date")
        if vintage not in ETF_PROXY_GENERATION_VINTAGES:
            raise PITReceiptError(
                f"etf proxy generation vintage must be one of {ETF_PROXY_GENERATION_VINTAGES}"
            )
        scope_key = _etf_proxy_generation_scope_key(start, end)
        contract_sha256 = _etf_proxy_generation_contract_sha256(start, end)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT generation_id FROM etf_proxy_generations
                WHERE scope_key = ? AND status = 'collecting'
                """,
                (scope_key,),
            ).fetchone()
            if current is not None:
                view = self._etf_proxy_generation_view(connection, current[0])
                _started_iso, started_utc = self._generation_timestamp(
                    view["started_at"], "generation started_at"
                )
                if now_utc < started_utc:
                    raise PITReceiptError("generation clock moved backwards")
                complete = set(view["staged_symbols"]) == set(ETF_PROXY_REQUIRED_SYMBOLS)
                # Only an incomplete generation can time out; the boundary
                # ``elapsed == window`` does NOT abandon (strictly greater does).
                expired = (
                    not complete
                    and (now_utc - started_utc).total_seconds()
                    > ETF_PROXY_GENERATION_WINDOW_SECONDS
                )
                if not force_new and not expired:
                    return view
                reason = "force_new" if force_new else "stale_incomplete"
                connection.execute(
                    """
                    UPDATE etf_proxy_generations
                    SET status = 'abandoned', terminal_at = ?, terminal_reason = ?
                    WHERE generation_id = ? AND status = 'collecting'
                    """,
                    (now_iso, reason, view["generation_id"]),
                )
            next_sequence = int(
                connection.execute(
                    "SELECT COALESCE(MAX(generation_sequence), 0) + 1 FROM etf_proxy_generations"
                ).fetchone()[0]
            )
            generation_id = f"etf-proxy-{next_sequence:020d}"
            connection.execute(
                """
                INSERT INTO etf_proxy_generations (
                    generation_sequence, generation_id, scope_key, start_date,
                    end_date, contract_sha256, status, vintage,
                    final_oos_eligible, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'collecting', ?, 0, ?)
                """,
                (
                    next_sequence,
                    generation_id,
                    scope_key,
                    start,
                    end,
                    contract_sha256,
                    str(vintage),
                    now_iso,
                ),
            )
            return self._etf_proxy_generation_view(connection, generation_id)

    def _validated_market_attempt_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        trade_date: str,
        dataset: str,
        attempt_id: str,
        allow_staged_event: bool = False,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
        if dataset not in MARKET_SESSION_DATASETS:
            raise PITReceiptError("unsupported market dataset")
        stored = connection.execute(
            "SELECT * FROM fetch_attempts WHERE attempt_id = ?", (str(attempt_id),)
        ).fetchone()
        if stored is None:
            raise PITReceiptError(f"fetch attempt does not exist: {attempt_id}")
        attempt = dict(stored)
        if (
            attempt["dataset"] != dataset
            or attempt["partition_key"] != trade_date
            or attempt["endpoint"] != dataset
            or attempt["error_kind"] is not None
            or int(attempt["body_complete"]) != 1
            or attempt["http_status"] is None
            or not 200 <= int(attempt["http_status"]) < 300
            or not attempt["raw_path"]
        ):
            raise PITReceiptError("market generation attempt is not promotable")
        semantics = json.loads(attempt["request_semantics_json"])
        expected_row_cap = int(MARKET_SESSION_ROW_CAPS[dataset])
        if str(semantics.get("source_profile") or "official") == "jiaoch":
            expected_row_cap = int(dict(JIAOCH_ROW_CAP_OVERRIDES).get(dataset, expected_row_cap))
        if int(attempt["row_cap"]) != expected_row_cap:
            raise PITReceiptError("market generation attempt row cap is not canonical")
        event = connection.execute(
            "SELECT status, details_json FROM fetch_promotion_events WHERE attempt_id = ?",
            (str(attempt_id),),
        ).fetchone()
        if event is not None:
            details = json.loads(event["details_json"])
            if not (
                allow_staged_event
                and event["status"] == "market_session_staged"
                and details.get("generation_id")
                and details.get("dataset") == dataset
                and details.get("raw_sha256") == attempt["raw_sha256"]
            ):
                raise PITReceiptError("market generation attempt already has a terminal event")
        params = json.loads(attempt["params_json"])
        if trade_date != _canonical_partition_key(dataset, params):
            raise PITReceiptError("market generation attempt partition lineage mismatch")
        if (
            _sha256(semantics) != attempt["request_semantics_sha256"]
            or semantics.get("wire_params") != _canonical_wire_params(dataset, params)
            or semantics.get("fields") != list(NORMALIZED_FIELDS[dataset])
        ):
            raise PITReceiptError("market generation request lineage mismatch")
        raw_bytes = self._verify_raw_file(attempt)
        fields, items, _code, _message = _parse_native_envelope(raw_bytes)
        normalized = _normalize_rows(dataset, trade_date, params, fields, items)
        if len(normalized) >= int(attempt["row_cap"]):
            raise PITReceiptError("market generation attempt reaches row cap")
        return attempt, normalized, _sha256(normalized)

    def stage_market_session_attempt(
        self, generation_id: str, dataset: str, attempt_id: str
    ) -> Dict[str, Any]:
        if dataset not in MARKET_SESSION_DATASETS:
            raise PITReceiptError("unsupported market dataset")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._market_session_view(connection, generation_id)
            if generation["status"] != "collecting":
                raise PITReceiptError("market generation is abandoned or inactive")
            trade_date = generation["trade_date"]
            existing = connection.execute(
                """
                SELECT attempt_id FROM market_session_generation_shards
                WHERE generation_id = ? AND dataset = ?
                """,
                (str(generation_id), str(dataset)),
            ).fetchone()
            if existing is not None and existing["attempt_id"] != str(attempt_id):
                raise PITReceiptError("market generation dataset is already staged; conflict")
            if existing is not None:
                stored_attempt = connection.execute(
                    "SELECT * FROM fetch_attempts WHERE attempt_id = ?",
                    (str(attempt_id),),
                ).fetchone()
                if stored_attempt is None:
                    raise PITReceiptError("staged market generation attempt is missing")
                self._verify_raw_file(dict(stored_attempt))
                return {**generation, "status": "reused"}
            attempt, normalized, normalized_sha256 = self._validated_market_attempt_on_connection(
                connection,
                trade_date=trade_date,
                dataset=str(dataset),
                attempt_id=str(attempt_id),
            )
            if generation.get("expected_request_semantics_sha256"):
                expected = json.loads(generation["expected_request_semantics_json"])
                if attempt["request_semantics_sha256"] != expected.get(str(dataset)):
                    raise PITReceiptError(
                        "market generation attempt request semantics do not match pinned manifest"
                    )
            _started_iso, started_utc = self._generation_timestamp(
                generation["started_at"], "generation started_at"
            )
            _retrieved_iso, retrieved_utc = self._generation_timestamp(
                attempt["retrieved_at"], "attempt retrieved_at"
            )
            span_seconds = (retrieved_utc - started_utc).total_seconds()
            if not 0 <= span_seconds <= MARKET_SESSION_GENERATION_WINDOW_SECONDS:
                raise PITReceiptError("market generation staging window expired")
            connection.execute(
                """
                INSERT INTO market_session_generation_shards (
                    generation_id, dataset, attempt_id,
                    request_semantics_sha256, retrieved_at, raw_sha256,
                    raw_bytes, normalized_sha256, row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(generation_id),
                    str(dataset),
                    str(attempt_id),
                    attempt["request_semantics_sha256"],
                    attempt["retrieved_at"],
                    attempt["raw_sha256"],
                    int(attempt["raw_bytes"]),
                    normalized_sha256,
                    len(normalized),
                ),
            )
            self._insert_market_session_rows(connection, generation_id, dataset, normalized)
            connection.execute(
                """
                INSERT INTO fetch_promotion_events (
                    attempt_id, status, details_json, recorded_at
                ) VALUES (?, 'market_session_staged', ?, ?)
                """,
                (
                    str(attempt_id),
                    _canonical_json(
                        {
                            "generation_id": str(generation_id),
                            "dataset": str(dataset),
                            "raw_sha256": attempt["raw_sha256"],
                        }
                    ),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            staged = self._market_session_view(connection, generation_id)
            return {**staged, "status": "staged"}

    def _verify_etf_proxy_generation_contract_on_connection(
        self,
        connection: sqlite3.Connection,
        generation: Mapping[str, Any],
    ) -> None:
        """Re-derive the frozen scope_key + contract_sha256 and constant-time
        compare to the stored generation row.

        WHY: staging must never trust the persisted contract columns. Both
        digests are a pure function of the frozen schema version, the required
        proxy symbols, the field set/row cap/window, and the generation's
        start/end. ``hmac.compare_digest`` keeps the comparison constant-time so
        the failure mode is always a hard reject, never a silent re-bind to a
        mutated lineage.
        """

        expected_scope = _etf_proxy_generation_scope_key(
            generation["start_date"], generation["end_date"]
        )
        expected_contract = _etf_proxy_generation_contract_sha256(
            generation["start_date"], generation["end_date"]
        )
        if not hmac.compare_digest(expected_scope, str(generation["scope_key"])):
            raise PITReceiptError("etf proxy generation scope lineage mismatch")
        if not hmac.compare_digest(expected_contract, str(generation["contract_sha256"])):
            raise PITReceiptError("etf proxy generation contract lineage mismatch")

    def _validated_etf_proxy_attempt_on_connection(
        self,
        connection: sqlite3.Connection,
        *,
        generation: Mapping[str, Any],
        symbol: str,
        attempt_id: str,
        allow_staged_event: bool,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], str]:
        """Re-derive every canonical invariant for one ``fund_daily`` attempt.

        WHY this exists: a captured attempt is never trusted on its stored
        flags alone. The generation stage re-derives the partition, the wire
        semantics, the frozen contract fields and the normalized rows from the
        raw CAS bytes, and rejects anything that drifts. The semantics check
        mirrors ``record_fetch_attempt``'s own canonical contract (not just a
        hash) so a self-consistent-but-mutated semantics cannot slip through.
        """

        stored = connection.execute(
            "SELECT * FROM fetch_attempts WHERE attempt_id = ?", (str(attempt_id),)
        ).fetchone()
        if stored is None:
            raise PITReceiptError(f"fetch attempt does not exist: {attempt_id}")
        attempt = dict(stored)
        if (
            attempt["dataset"] != "fund_daily"
            or attempt["endpoint"] != "fund_daily"
            or attempt["error_kind"] is not None
            or _require_int(attempt["body_complete"], "etf proxy attempt body_complete") != 1
            or attempt["http_status"] is None
            or not 200
            <= _require_int(attempt["http_status"], "etf proxy attempt http_status")
            < 300
            or not attempt["raw_path"]
        ):
            raise PITReceiptError("etf proxy attempt is not promotable")
        if _require_int(attempt["row_cap"], "etf proxy attempt row_cap") != int(FUND_DAILY_ROW_CAP):
            raise PITReceiptError("etf proxy attempt row cap is not canonical")
        try:
            params = json.loads(attempt["params_json"])
        except (ValueError, TypeError) as exc:
            raise PITReceiptError("etf proxy attempt params_json is malformed") from exc
        if not isinstance(params, dict):
            raise PITReceiptError("etf proxy attempt params_json is not an object")
        normalized_params = _normalize_request_params("fund_daily", params)
        canonical_partition = _canonical_partition_key("fund_daily", normalized_params)
        # The attempt must bind to THIS symbol and THIS generation's date range;
        # an attempt captured for the other symbol or a stale range is rejected
        # before any raw parsing, so it can never populate the wrong shard.
        if normalized_params["ts_code"] != symbol:
            raise PITReceiptError("etf proxy attempt symbol does not match shard")
        if (
            normalized_params["start_date"] != generation["start_date"]
            or normalized_params["end_date"] != generation["end_date"]
        ):
            raise PITReceiptError("etf proxy attempt date range does not match generation")
        if attempt["partition_key"] != canonical_partition:
            raise PITReceiptError("etf proxy attempt partition lineage mismatch")
        event = connection.execute(
            "SELECT status, details_json FROM fetch_promotion_events WHERE attempt_id = ?",
            (str(attempt_id),),
        ).fetchone()
        pending_event_details: Optional[str] = None
        if event is not None:
            # A first stage requires a clean attempt with no prior terminal event
            # (generation_required, transport_error, ...). Only the reuse path --
            # which has already bound the shard -- tolerates its own staged event.
            if not (allow_staged_event and event["status"] == "etf_proxy_staged"):
                raise PITReceiptError("etf proxy attempt already has a terminal event")
            # WHY defer the details check: the canonical details include
            # ``normalized_sha256``, which is only known after the raw bytes are
            # re-parsed below. We carry the stored details forward and compare it
            # to the exact canonical dict once normalized is recomputed, so a
            # staged event whose details were mutated (value OR extra key) is
            # rejected rather than silently reused.
            pending_event_details = event["details_json"]
        try:
            semantics = json.loads(attempt["request_semantics_json"])
        except (ValueError, TypeError) as exc:
            raise PITReceiptError("etf proxy attempt request_semantics_json is malformed") from exc
        if not isinstance(semantics, dict):
            raise PITReceiptError("etf proxy attempt request_semantics_json is not an object")
        try:
            semantic_receipt_params = _normalize_request_params(
                "fund_daily", semantics.get("receipt_params") or {}
            )
        except (KeyError, PITReceiptError):
            semantic_receipt_params = {}
        if (
            _sha256(semantics) != attempt["request_semantics_sha256"]
            or semantics.get("schema_version") != "tushare-wire-request/v1"
            or semantics.get("dataset") != "fund_daily"
            or semantics.get("partition_key") != canonical_partition
            or semantics.get("api_name") != "fund_daily"
            or semantics.get("method") != "POST"
            or semantic_receipt_params != normalized_params
            or semantics.get("wire_params")
            != _canonical_wire_params("fund_daily", normalized_params)
            or semantics.get("fields") != list(FUND_DAILY_FIELDS)
            or _require_int(semantics.get("row_cap") or 0, "etf proxy semantics row_cap")
            != int(FUND_DAILY_ROW_CAP)
        ):
            raise PITReceiptError("etf proxy request lineage mismatch")
        # WHY strictly validate raw_bytes before _verify_raw_file: that helper
        # compares ``int(raw_bytes)`` to the real file size, so a torn write that
        # drifts the column to ``N + 0.9`` would truncate to ``N`` and match the
        # file exactly -- passing the byte-count receipt for a corrupted column.
        # The ETF path validates losslessly first; every downstream use of this
        # shard's raw_bytes (the INSERT and the reuse lineage compare) reuses the
        # same strict helper rather than a bare ``int()`` that could truncate.
        _require_int(attempt["raw_bytes"], "etf proxy attempt raw_bytes")
        raw_bytes = self._verify_raw_file(attempt)
        fields, items, _code, _message = _parse_native_envelope(raw_bytes)
        normalized = _normalize_rows("fund_daily", canonical_partition, params, fields, items)
        if len(normalized) >= int(FUND_DAILY_ROW_CAP):
            raise PITReceiptError("etf proxy attempt reaches row cap")
        if pending_event_details is not None:
            # The staged event must bind to THIS generation/symbol/raw/normalized
            # exactly. Equality (not a partial .get() of a few keys) means a
            # mutated value OR any extra key is a tamper that fails closed.
            expected_details = {
                "generation_id": str(generation["generation_id"]),
                "symbol": str(symbol),
                "raw_sha256": attempt["raw_sha256"],
                "normalized_sha256": _sha256(normalized),
            }
            try:
                details = json.loads(pending_event_details)
            except (ValueError, TypeError) as exc:
                raise PITReceiptError("etf proxy staged event details_json is malformed") from exc
            if details != expected_details:
                raise PITReceiptError("etf proxy staged event details were tampered")
        # WHY the window lives in the shared validator, not the first-stage
        # caller: both the first stage and the reuse path run this validator, so
        # a torn write that drifts ``retrieved_at`` on BOTH the attempt and the
        # shard (mutually self-consistent, so the shard-lineage equality check on
        # reuse passes) is still caught here. One window check, shared by both
        # paths -- never two copies that can drift apart.
        _started_iso, started_utc = self._generation_timestamp(
            generation["started_at"], "generation started_at"
        )
        _retrieved_iso, retrieved_utc = self._generation_timestamp(
            attempt["retrieved_at"], "attempt retrieved_at"
        )
        span_seconds = (retrieved_utc - started_utc).total_seconds()
        if not 0 <= span_seconds <= ETF_PROXY_GENERATION_WINDOW_SECONDS:
            raise PITReceiptError("etf proxy generation staging window expired")
        return attempt, normalized, _sha256(normalized)

    def _verify_etf_proxy_reuse(
        self,
        connection: sqlite3.Connection,
        *,
        generation: Mapping[str, Any],
        symbol: str,
        attempt_id: str,
    ) -> None:
        """Fail-closed deep re-verification for an already-staged shard.

        Idempotency is not "return cached": reuse re-runs the SAME full
        canonical validator as the first stage (``allow_staged_event=True``) so a
        self-consistent-but-mutated semantics, an un-promotable attempt flag, or
        a stale params range is caught by the per-field canonical contract -- not
        by a second, weaker raw-only check. Only AFTER that passes do we compare
        every shard lineage field (``retrieved_at`` included, not just
        raw/hash/count) and the persisted rows against the recomputed normalized
        hash. The staged event details are verified for exact equality inside the
        validator (raw + normalized are both known there).
        """

        attempt, normalized, normalized_sha256 = self._validated_etf_proxy_attempt_on_connection(
            connection,
            generation=generation,
            symbol=str(symbol),
            attempt_id=str(attempt_id),
            allow_staged_event=True,
        )
        shard = connection.execute(
            "SELECT * FROM etf_proxy_generation_shards WHERE generation_id = ? AND symbol = ?",
            (str(generation["generation_id"]), str(symbol)),
        ).fetchone()
        if shard is None:
            raise PITReceiptError("etf proxy shard missing on reuse")
        shard = dict(shard)
        # WHY every lineage field: a torn write that drifts only ``retrieved_at``
        # (or any single lineage column) must surface, so each stored column is
        # bound to the freshly re-derived attempt value, not just raw/hash/count.
        expected_lineage = {
            "attempt_id": str(attempt_id),
            "request_semantics_sha256": attempt["request_semantics_sha256"],
            "retrieved_at": attempt["retrieved_at"],
            "raw_sha256": attempt["raw_sha256"],
            "raw_bytes": _require_int(attempt["raw_bytes"], "etf proxy attempt raw_bytes"),
            "normalized_sha256": normalized_sha256,
            "row_count": len(normalized),
        }
        for field, expected in expected_lineage.items():
            if field != "raw_bytes":
                stored = shard[field]
            else:
                # WHY strict on the shard column too: a torn write that drifts
                # ONLY the shard's raw_bytes to ``N + 0.9`` must fail closed --
                # bare ``int()`` on both sides would truncate to the same N and
                # silently reuse the corrupted shard.
                stored = _require_int(shard[field], "etf proxy shard raw_bytes")
            if stored != expected:
                raise PITReceiptError("etf proxy shard no longer matches its attempt")
        persisted_rows = [
            dict(row)
            for row in connection.execute(
                "SELECT ts_code, trade_date, open, high, low, close, pre_close, "
                "change, pct_chg, vol, amount "
                "FROM etf_proxy_generation_rows "
                "WHERE generation_id = ? AND ts_code = ? ORDER BY trade_date",
                (str(generation["generation_id"]), str(symbol)),
            )
        ]
        if _sha256(persisted_rows) != normalized_sha256:
            raise PITReceiptError("etf proxy persisted rows no longer match shard")

    def _insert_etf_proxy_rows(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        symbol: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        connection.executemany(
            """
            INSERT INTO etf_proxy_generation_rows (
                generation_id, ts_code, trade_date, open, high, low, close,
                pre_close, change, pct_chg, vol, amount
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    str(generation_id),
                    str(symbol),
                    row["trade_date"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["pre_close"],
                    row["change"],
                    row["pct_chg"],
                    row["vol"],
                    row["amount"],
                )
                for row in rows
            ],
        )

    def stage_etf_proxy_generation_attempt(
        self, generation_id: str, symbol: str, attempt_id: str
    ) -> Dict[str, Any]:
        """Atomically promote one ``fund_daily`` attempt into an ETF proxy shard.

        The whole flow runs under ``BEGIN IMMEDIATE``: validate the generation is
        collecting, bind the attempt to this symbol+slot (conflict if the slot is
        already held by another attempt, reuse if held by the same attempt), then
        re-derive every canonical invariant, enforce the staging window, and
        write exactly one shard row, its normalized rows and one
        ``etf_proxy_staged`` event. Legacy receipts/fund tables are never touched
        and ``final_oos_eligible`` never flips.
        """

        if symbol not in ETF_PROXY_REQUIRED_SYMBOLS:
            raise PITReceiptError(
                f"etf proxy generation symbol is not a required proxy symbol: {symbol!r}"
            )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._etf_proxy_generation_view(connection, str(generation_id))
            if generation["status"] != "collecting":
                raise PITReceiptError("etf proxy generation is abandoned or inactive")
            # WHY recompute on every stage (first AND reuse): the frozen scope_key
            # and contract_sha256 are derived purely from start/end + the frozen
            # schema. A mutated generation row must not be trusted as stored; we
            # re-derive both and constant-time compare before any attempt work.
            self._verify_etf_proxy_generation_contract_on_connection(connection, generation)
            existing = connection.execute(
                """
                SELECT attempt_id FROM etf_proxy_generation_shards
                WHERE generation_id = ? AND symbol = ?
                """,
                (str(generation_id), str(symbol)),
            ).fetchone()
            if existing is not None and existing["attempt_id"] != str(attempt_id):
                raise PITReceiptError("etf proxy generation symbol is already staged; conflict")
            if existing is not None:
                self._verify_etf_proxy_reuse(
                    connection,
                    generation=generation,
                    symbol=str(symbol),
                    attempt_id=str(attempt_id),
                )
                return {**generation, "status": "reused"}
            attempt, normalized, normalized_sha256 = (
                self._validated_etf_proxy_attempt_on_connection(
                    connection,
                    generation=generation,
                    symbol=str(symbol),
                    attempt_id=str(attempt_id),
                    allow_staged_event=False,
                )
            )
            connection.execute(
                """
                INSERT INTO etf_proxy_generation_shards (
                    generation_id, symbol, attempt_id, request_semantics_sha256,
                    retrieved_at, raw_sha256, raw_bytes, normalized_sha256,
                    row_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(generation_id),
                    str(symbol),
                    str(attempt_id),
                    attempt["request_semantics_sha256"],
                    attempt["retrieved_at"],
                    attempt["raw_sha256"],
                    _require_int(attempt["raw_bytes"], "etf proxy attempt raw_bytes"),
                    normalized_sha256,
                    len(normalized),
                ),
            )
            self._insert_etf_proxy_rows(connection, str(generation_id), str(symbol), normalized)
            connection.execute(
                """
                INSERT INTO fetch_promotion_events (
                    attempt_id, status, details_json, recorded_at
                ) VALUES (?, 'etf_proxy_staged', ?, ?)
                """,
                (
                    str(attempt_id),
                    _canonical_json(
                        {
                            "generation_id": str(generation_id),
                            "symbol": str(symbol),
                            "raw_sha256": attempt["raw_sha256"],
                            "normalized_sha256": normalized_sha256,
                        }
                    ),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            staged = self._etf_proxy_generation_view(connection, str(generation_id))
            return {**staged, "status": "staged"}

    def _etf_proxy_generation_rows_from_db(
        self, connection: sqlite3.Connection, generation_id: str, symbol: str
    ) -> List[Dict[str, Any]]:
        """Canonical normalized rows for one shard, in ``trade_date`` order.

        The column set and ordering exactly match the rows the stager wrote (and
        the reuse path re-hashes), so a manifest can compare these DB rows to a
        freshly re-normalized list with plain equality and a lineage can hash
        them deterministically.
        """

        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT ts_code, trade_date, open, high, low, close, pre_close,
                       change, pct_chg, vol, amount
                FROM etf_proxy_generation_rows
                WHERE generation_id = ? AND ts_code = ?
                ORDER BY trade_date
                """,
                (str(generation_id), str(symbol)),
            )
        ]

    def _etf_proxy_generation_manifest_on_connection(
        self, connection: sqlite3.Connection, generation_id: str
    ) -> Dict[str, Any]:
        """Recompute the freeze manifest for one ETF proxy generation.

        WHY recompute everything: publishing (and verifying) never trusts stored
        flags or stored hashes. The manifest re-derives the frozen scope/contract,
        enforces exactly the two required symbols in canonical order, re-runs the
        full per-shard canonical validator (raw CAS + complete semantics + exact
        staged event + staging window), binds every shard lineage field losslessly
        to the freshly re-derived attempt, and requires the persisted rows to
        equal the freshly normalized rows. Only then is a deterministic payload
        hashed. ``final_oos_eligible`` must be False; the vintage is frozen.
        """

        generation = self._etf_proxy_generation_view(connection, generation_id)
        # WHY recompute before trust: the frozen scope_key + contract_sha256 are
        # a pure function of start/end + the frozen schema; a mutated generation
        # row must not be re-bound to a stale manifest.
        self._verify_etf_proxy_generation_contract_on_connection(connection, generation)
        if generation["final_oos_eligible"] is not False:
            raise PITReceiptError("etf proxy generation final_oos_eligible must be false")
        shards = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM etf_proxy_generation_shards WHERE generation_id = ?",
                (str(generation_id),),
            )
        ]
        shards_by_symbol = {shard["symbol"]: shard for shard in shards}
        # WHY exact two-symbol set in canonical order: a publishable generation
        # freezes exactly the two required proxy symbols. A missing, extra, or
        # duplicate symbol is incomplete and must fail closed -- never silently
        # publish a partial scope.
        if set(shards_by_symbol.keys()) != set(ETF_PROXY_REQUIRED_SYMBOLS) or len(shards) != len(
            ETF_PROXY_REQUIRED_SYMBOLS
        ):
            raise PITReceiptError(
                "etf proxy generation is incomplete; exactly two symbol shards are required"
            )
        _started_iso, started_utc = self._generation_timestamp(
            generation["started_at"], "generation started_at"
        )
        retrieved_values = []
        symbol_roots: Dict[str, str] = {}
        shard_manifest = []
        for symbol in ETF_PROXY_REQUIRED_SYMBOLS:
            shard = shards_by_symbol[symbol]
            attempt, normalized, normalized_sha256 = (
                self._validated_etf_proxy_attempt_on_connection(
                    connection,
                    generation=generation,
                    symbol=symbol,
                    attempt_id=shard["attempt_id"],
                    allow_staged_event=True,
                )
            )
            # The validator tolerates a staged event with exact details, but a
            # publishable shard MUST carry exactly that one staged event -- a
            # missing event (or a second terminal event) is a tamper.
            event = connection.execute(
                """
                SELECT status FROM fetch_promotion_events
                WHERE attempt_id = ? AND status = 'etf_proxy_staged'
                """,
                (shard["attempt_id"],),
            ).fetchone()
            if event is None:
                raise PITReceiptError("etf proxy generation promotion event is missing")
            # WHY every lineage field, lossless int: a torn write that drifts a
            # single column (retrieved_at, raw_bytes, ...) must surface. Bare
            # int() on raw_bytes would truncate N+0.9 -> N and silently pass.
            expected_lineage = {
                "attempt_id": str(shard["attempt_id"]),
                "request_semantics_sha256": attempt["request_semantics_sha256"],
                "retrieved_at": attempt["retrieved_at"],
                "raw_sha256": attempt["raw_sha256"],
                "raw_bytes": _require_int(attempt["raw_bytes"], "etf proxy attempt raw_bytes"),
                "normalized_sha256": normalized_sha256,
                "row_count": len(normalized),
            }
            for field, expected in expected_lineage.items():
                if field != "raw_bytes":
                    stored = shard[field]
                else:
                    stored = _require_int(shard[field], "etf proxy shard raw_bytes")
                if stored != expected:
                    raise PITReceiptError("etf proxy generation shard lineage mismatch")
            stored_rows = self._etf_proxy_generation_rows_from_db(
                connection, str(generation_id), symbol
            )
            if stored_rows != normalized:
                raise PITReceiptError("etf proxy generation normalized rows mismatch")
            symbol_roots[symbol] = _stream_rows_sha256(normalized)[0]
            _retrieved_iso, retrieved_utc = self._generation_timestamp(
                shard["retrieved_at"], "generation shard retrieved_at"
            )
            if retrieved_utc < started_utc:
                raise PITReceiptError("etf proxy generation shard predates generation")
            retrieved_values.append(retrieved_utc)
            shard_manifest.append(
                {
                    "symbol": symbol,
                    "attempt_id": str(shard["attempt_id"]),
                    "request_semantics_sha256": shard["request_semantics_sha256"],
                    "retrieved_at": shard["retrieved_at"],
                    "raw_sha256": shard["raw_sha256"],
                    "raw_bytes": _require_int(shard["raw_bytes"], "etf proxy shard raw_bytes"),
                    "normalized_sha256": shard["normalized_sha256"],
                    "row_count": _require_int(shard["row_count"], "etf proxy shard row_count"),
                }
            )
        if (
            max(retrieved_values) - min(retrieved_values)
        ).total_seconds() > ETF_PROXY_GENERATION_WINDOW_SECONDS or (
            max(retrieved_values) - started_utc
        ).total_seconds() > ETF_PROXY_GENERATION_WINDOW_SECONDS:
            raise PITReceiptError("etf proxy generation shards exceed the freeze window")
        retrieved_at_min = min(retrieved_values).isoformat()
        retrieved_at_max = max(retrieved_values).isoformat()
        rows_root = _sha256({symbol: symbol_roots[symbol] for symbol in ETF_PROXY_REQUIRED_SYMBOLS})
        payload = {
            "schema_version": ETF_PROXY_GENERATION_SCHEMA_VERSION,
            "generation_id": str(generation_id),
            "scope_key": generation["scope_key"],
            "start_date": generation["start_date"],
            "end_date": generation["end_date"],
            "contract_sha256": generation["contract_sha256"],
            "vintage": generation["vintage"],
            "final_oos_eligible": False,
            "started_at": generation["started_at"],
            "retrieved_at_min": retrieved_at_min,
            "retrieved_at_max": retrieved_at_max,
            "symbol_roots": symbol_roots,
            "rows_root": rows_root,
            "shards": shard_manifest,
        }
        manifest_sha256 = _sha256(payload)
        lineage_sha256 = self._etf_proxy_generation_lineage_sha256_on_connection(
            connection, str(generation_id)
        )
        return {
            "manifest": payload,
            "manifest_sha256": manifest_sha256,
            "lineage_sha256": lineage_sha256,
        }

    @staticmethod
    def _etf_proxy_generation_lineage_sha256_on_connection(
        connection: sqlite3.Connection, generation_id: str
    ) -> str:
        # WHY a separate immutable hash: the lineage is an immutable receipt over
        # exactly what was frozen. It is a pure function of the immutable
        # generation semantics (identity + scope + contract + vintage +
        # final_oos=False + started_at), the two shards, their underlying
        # attempts and promotion events, and every normalized row, all in
        # canonical order. Lifecycle fields that mutate across the
        # collecting -> published transition (status, terminal_at,
        # terminal_reason, manifest_sha256, lineage_sha256) are deliberately
        # excluded so the hash is identical before and after publish -- it must
        # NOT be a hash of the manifest alone.
        generation = connection.execute(
            """
            SELECT generation_sequence, generation_id, scope_key, start_date,
                   end_date, contract_sha256, vintage, final_oos_eligible,
                   started_at
            FROM etf_proxy_generations WHERE generation_id = ?
            """,
            (str(generation_id),),
        ).fetchone()
        if generation is None:
            raise PITReceiptError("etf proxy generation lineage is incomplete")
        symbol_order = {symbol: index for index, symbol in enumerate(ETF_PROXY_REQUIRED_SYMBOLS)}

        def _by_canonical_symbol(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            return sorted(rows, key=lambda row: symbol_order.get(row["symbol"], len(symbol_order)))

        shards = _by_canonical_symbol(
            [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT generation_id, symbol, attempt_id,
                           request_semantics_sha256, retrieved_at, raw_sha256,
                           raw_bytes, normalized_sha256, row_count
                    FROM etf_proxy_generation_shards
                    WHERE generation_id = ?
                    """,
                    (str(generation_id),),
                )
            ]
        )
        ordered_symbols = [shard["symbol"] for shard in shards]
        attempts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT attempt.* FROM fetch_attempts AS attempt
                JOIN etf_proxy_generation_shards AS shard
                  ON shard.attempt_id = attempt.attempt_id
                WHERE shard.generation_id = ?
                """,
                (str(generation_id),),
            )
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT event.* FROM fetch_promotion_events AS event
                JOIN etf_proxy_generation_shards AS shard
                  ON shard.attempt_id = event.attempt_id
                WHERE shard.generation_id = ?
                """,
                (str(generation_id),),
            )
        ]
        if (
            len(shards) != len(ETF_PROXY_REQUIRED_SYMBOLS)
            or len(attempts) != len(ETF_PROXY_REQUIRED_SYMBOLS)
            or len(events) != len(ETF_PROXY_REQUIRED_SYMBOLS)
            or ordered_symbols != list(ETF_PROXY_REQUIRED_SYMBOLS)
        ):
            raise PITReceiptError("etf proxy generation exact lineage is incomplete")
        attempt_by_id = {attempt["attempt_id"]: attempt for attempt in attempts}
        event_by_id = {event["attempt_id"]: event for event in events}
        ordered_attempts = [attempt_by_id[shard["attempt_id"]] for shard in shards]
        ordered_events = [event_by_id[shard["attempt_id"]] for shard in shards]
        rows = {
            symbol: connection.execute(
                """
                SELECT ts_code, trade_date, open, high, low, close, pre_close,
                       change, pct_chg, vol, amount
                FROM etf_proxy_generation_rows
                WHERE generation_id = ? AND ts_code = ?
                ORDER BY trade_date
                """,
                (str(generation_id), symbol),
            ).fetchall()
            for symbol in ETF_PROXY_REQUIRED_SYMBOLS
        }
        rows = {
            symbol: [dict(row) for row in rows[symbol]] for symbol in ETF_PROXY_REQUIRED_SYMBOLS
        }
        return _sha256(
            {
                "schema_version": "etf-proxy-generation-lineage/v1",
                "generation_id": str(generation_id),
                "generation": dict(generation),
                "shards": shards,
                "rows": rows,
                "attempts": ordered_attempts,
                "promotion_events": ordered_events,
            }
        )

    def _etf_proxy_assert_head_consistent_on_connection(
        self, connection: sqlite3.Connection, scope_key: str
    ) -> None:
        """Read-only scope-head consistency check, run inside the caller's
        ``BEGIN IMMEDIATE`` before publish writes the head.

        WHY read-only + pre-write: the head-update path must NOT silently repair
        a torn / rolled-back / spurious head by overwriting it. This surfaces
        such corruption so a failure rolls back and leaves the bad state exactly
        as found, instead of certifying it as clean.

        - No published generation yet: the head must NOT exist.
        - Otherwise: the head must exist, reference the latest published
          generation of this scope, carry that generation's stored
          manifest+lineage, and match a fresh deep verification. The collecting
          generation being published is never a legitimate head.
        """

        latest = connection.execute(
            "SELECT generation_sequence, manifest_sha256, lineage_sha256 "
            "FROM etf_proxy_generations "
            "WHERE scope_key = ? AND status = 'published' "
            "ORDER BY generation_sequence DESC LIMIT 1",
            (scope_key,),
        ).fetchone()
        head = connection.execute(
            "SELECT generation_id, manifest_sha256, lineage_sha256 "
            "FROM etf_proxy_generation_head WHERE scope_key = ?",
            (scope_key,),
        ).fetchone()
        if latest is None:
            if head is not None:
                raise PITReceiptError(
                    "etf proxy generation head exists before any published generation"
                )
            return
        if head is None:
            raise PITReceiptError(
                "etf proxy generation head missing for an already-published scope"
            )
        head_generation = self._etf_proxy_generation_view(connection, head["generation_id"])
        if head_generation["status"] != "published":
            raise PITReceiptError("etf proxy generation head points at a non-published generation")
        if head_generation["scope_key"] != scope_key:
            raise PITReceiptError("etf proxy generation head scope mismatch")
        if int(head_generation["generation_sequence"]) != int(latest["generation_sequence"]):
            raise PITReceiptError("etf proxy generation head rollback detected")
        if not hmac.compare_digest(str(head["manifest_sha256"]), str(latest["manifest_sha256"])):
            raise PITReceiptError("etf proxy generation head manifest mismatch before publish")
        if not hmac.compare_digest(str(head["lineage_sha256"]), str(latest["lineage_sha256"])):
            raise PITReceiptError("etf proxy generation head lineage mismatch before publish")
        verified = self._etf_proxy_generation_manifest_on_connection(
            connection, str(head["generation_id"])
        )
        if not hmac.compare_digest(str(head["manifest_sha256"]), verified["manifest_sha256"]):
            raise PITReceiptError("etf proxy generation head manifest hash mismatch before publish")
        if not hmac.compare_digest(str(head["lineage_sha256"]), verified["lineage_sha256"]):
            raise PITReceiptError("etf proxy generation head lineage hash mismatch before publish")

    def publish_etf_proxy_generation(self, generation_id: str) -> Dict[str, Any]:
        """Atomically freeze one complete ETF proxy generation as published.

        ``BEGIN IMMEDIATE``: re-derive the full manifest, then either (a) flip a
        collecting generation to published, stamp ``terminal_at`` at the later of
        the two shards' ``retrieved_at``, persist manifest+lineage, and advance
        the scope head -- or (b) for an already-published generation, deep-verify
        the stored manifest AND lineage against a fresh recomputation and return
        idempotently without writing. Abandoned generations are rejected. Legacy
        receipts/fund tables are never touched and ``final_oos_eligible`` never
        flips. The head only ever advances to a newer sequence; re-publishing an
        older generation never rolls the head back.
        """

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._etf_proxy_generation_view(connection, str(generation_id))
            if generation["status"] == "abandoned":
                raise PITReceiptError("etf proxy generation is abandoned or inactive")
            # WHY head consistency before any write: the republish branch and the
            # collecting->published branch both must refuse to overwrite a torn /
            # rolled-back / spurious head. Run read-only inside this transaction so
            # a failure rolls back and leaves the corruption exactly as found.
            self._etf_proxy_assert_head_consistent_on_connection(
                connection, generation["scope_key"]
            )
            verified = self._etf_proxy_generation_manifest_on_connection(
                connection, str(generation_id)
            )
            if generation["status"] == "published":
                # WHY deep-verify both hashes: idempotent re-publish is not
                # "return cached". Any tamper of raw/attempt/event/shard/row that
                # changes the recomputed manifest or lineage must reject here --
                # never silently re-freeze a corrupted generation.
                if not hmac.compare_digest(
                    str(generation["manifest_sha256"]), verified["manifest_sha256"]
                ):
                    raise PITReceiptError("published etf proxy generation manifest mismatch")
                if not hmac.compare_digest(
                    str(generation["lineage_sha256"]), verified["lineage_sha256"]
                ):
                    raise PITReceiptError("published etf proxy generation lineage mismatch")
                published_view = self._etf_proxy_generation_view(connection, str(generation_id))
                return {
                    **published_view,
                    "manifest": verified["manifest"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "lineage_sha256": verified["lineage_sha256"],
                }
            published_at = max(
                self._generation_timestamp(row[0], "generation shard retrieved_at")[1]
                for row in connection.execute(
                    """
                    SELECT retrieved_at FROM etf_proxy_generation_shards
                    WHERE generation_id = ?
                    """,
                    (str(generation_id),),
                )
            ).isoformat()
            connection.execute(
                """
                UPDATE etf_proxy_generations
                SET status = 'published', terminal_at = ?, manifest_sha256 = ?,
                    lineage_sha256 = ?
                WHERE generation_id = ? AND status = 'collecting'
                """,
                (
                    published_at,
                    verified["manifest_sha256"],
                    verified["lineage_sha256"],
                    str(generation_id),
                ),
            )
            # WHY conditional head advance: the head must always point at the
            # latest published sequence for this scope. Re-publishing an older
            # generation (which can only reach this branch as collecting->published)
            # must never overwrite a head that already points at a newer sequence.
            existing_head = connection.execute(
                "SELECT generation_id FROM etf_proxy_generation_head WHERE scope_key = ?",
                (generation["scope_key"],),
            ).fetchone()
            if existing_head is None:
                connection.execute(
                    """
                    INSERT INTO etf_proxy_generation_head (
                        scope_key, generation_id, manifest_sha256, lineage_sha256,
                        published_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        generation["scope_key"],
                        str(generation_id),
                        verified["manifest_sha256"],
                        verified["lineage_sha256"],
                        published_at,
                    ),
                )
            else:
                existing_sequence = int(
                    connection.execute(
                        "SELECT generation_sequence FROM etf_proxy_generations "
                        "WHERE generation_id = ?",
                        (existing_head["generation_id"],),
                    ).fetchone()[0]
                )
                if int(generation["generation_sequence"]) >= existing_sequence:
                    connection.execute(
                        """
                        UPDATE etf_proxy_generation_head
                        SET generation_id = ?, manifest_sha256 = ?,
                            lineage_sha256 = ?, published_at = ?
                        WHERE scope_key = ?
                        """,
                        (
                            str(generation_id),
                            verified["manifest_sha256"],
                            verified["lineage_sha256"],
                            published_at,
                            generation["scope_key"],
                        ),
                    )
            published_view = self._etf_proxy_generation_view(connection, str(generation_id))
            return {
                **published_view,
                "manifest": verified["manifest"],
                "manifest_sha256": verified["manifest_sha256"],
                "lineage_sha256": verified["lineage_sha256"],
            }

    def _active_etf_proxy_on_connection(
        self, connection: sqlite3.Connection, start_date: str, end_date: str
    ) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
        scope_key = _etf_proxy_generation_scope_key(start_date, end_date)
        head = connection.execute(
            """
            SELECT generation_id, manifest_sha256, lineage_sha256
            FROM etf_proxy_generation_head
            WHERE scope_key = ?
            """,
            (scope_key,),
        ).fetchone()
        if head is None:
            return None
        generation = self._etf_proxy_generation_view(connection, head["generation_id"])
        if generation["start_date"] != start_date or generation["end_date"] != end_date:
            raise PITReceiptError("active etf proxy generation scope mismatch")
        if generation["status"] != "published":
            raise PITReceiptError("active etf proxy generation is not published")
        if not hmac.compare_digest(
            str(head["manifest_sha256"]), str(generation["manifest_sha256"])
        ):
            raise PITReceiptError("active etf proxy generation head manifest mismatch")
        if not hmac.compare_digest(str(head["lineage_sha256"]), str(generation["lineage_sha256"])):
            raise PITReceiptError("active etf proxy generation head lineage mismatch")
        latest = connection.execute(
            """
            SELECT MAX(generation_sequence)
            FROM etf_proxy_generations
            WHERE scope_key = ? AND status = 'published'
            """,
            (scope_key,),
        ).fetchone()[0]
        if latest is None or int(generation["generation_sequence"]) != int(latest):
            raise PITReceiptError("active etf proxy generation head rollback detected")
        verified = self._etf_proxy_generation_manifest_on_connection(
            connection, str(generation["generation_id"])
        )
        if not hmac.compare_digest(str(head["manifest_sha256"]), verified["manifest_sha256"]):
            raise PITReceiptError("active etf proxy generation head manifest hash mismatch")
        if not hmac.compare_digest(str(head["lineage_sha256"]), verified["lineage_sha256"]):
            raise PITReceiptError("active etf proxy generation head lineage hash mismatch")
        return generation, verified

    def active_etf_proxy_generation(
        self, start_date: str, end_date: str
    ) -> Optional[Dict[str, Any]]:
        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if end < start:
            raise PITReceiptError("end_date must not precede start_date")
        with self._connect() as connection:
            connection.execute("BEGIN")
            active = self._active_etf_proxy_on_connection(connection, start, end)
            if active is None:
                return None
            generation, _verified = active
            return generation

    def verify_etf_proxy_generation(
        self,
        generation_id: str = None,
        *,
        start_date: str = None,
        end_date: str = None,
    ) -> Dict[str, Any]:
        """Deep-verify a published ETF proxy generation.

        The identifier is either an explicit ``generation_id`` OR a complete
        ``start_date``/``end_date`` range -- never both, and never half a range.
        The explicit path re-derives the manifest+lineage and constant-time
        compares them to the stored receipt (an older generation stays verifiable
        by id even after the head advances). The range path additionally resolves
        the active head and checks for rollback. Only published generations verify.
        """

        with self._connect() as connection:
            connection.execute("BEGIN")
            if generation_id is not None and (start_date is not None or end_date is not None):
                raise PITReceiptError("specify generation_id or start_date/end_date, not both")
            active_verified: Optional[Dict[str, Any]] = None
            if generation_id is not None:
                resolved = str(generation_id)
                generation = self._etf_proxy_generation_view(connection, resolved)
            else:
                if start_date is None or end_date is None:
                    raise PITReceiptError(
                        "etf proxy generation range requires both start_date and end_date"
                    )
                start = _iso_date(start_date, "start_date")
                end = _iso_date(end_date, "end_date")
                if end < start:
                    raise PITReceiptError("end_date must not precede start_date")
                active = self._active_etf_proxy_on_connection(connection, start, end)
                if active is None:
                    raise PITReceiptError("active etf proxy generation is missing")
                generation, active_verified = active
                resolved = str(generation["generation_id"])
            if generation["status"] != "published":
                raise PITReceiptError("etf proxy generation is not published")
            verified = active_verified or self._etf_proxy_generation_manifest_on_connection(
                connection, resolved
            )
            if not hmac.compare_digest(
                str(generation["manifest_sha256"]), verified["manifest_sha256"]
            ):
                raise PITReceiptError("etf proxy generation manifest mismatch")
            stored_lineage = connection.execute(
                "SELECT lineage_sha256 FROM etf_proxy_generations WHERE generation_id = ?",
                (resolved,),
            ).fetchone()
            # WHY constant-time: the persisted lineage is the immutable proof of
            # what was frozen; compare it to the freshly recomputed lineage so any
            # tamper (stored value OR any bound row/attempt/event/shard) fails
            # closed on both the explicit-id and range verify paths.
            if (
                stored_lineage is None
                or not stored_lineage[0]
                or not hmac.compare_digest(str(stored_lineage[0]), str(verified["lineage_sha256"]))
            ):
                raise PITReceiptError("etf proxy generation lineage mismatch")
            return {
                **generation,
                "verification_status": "passed",
                **verified,
            }

    def _insert_market_session_rows(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        dataset: str,
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        if dataset == "daily":
            connection.executemany(
                """
                INSERT INTO market_session_generation_rows_daily (
                    generation_id, trade_date, ts_code, open, high, low, close,
                    pre_close, change, pct_chg, vol, amount
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(generation_id),
                        row["trade_date"],
                        row["ts_code"],
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["pre_close"],
                        row["change"],
                        row["pct_chg"],
                        row["vol"],
                        row["amount"],
                    )
                    for row in rows
                ],
            )
            return
        if dataset == "adj_factor":
            connection.executemany(
                """
                INSERT INTO market_session_generation_rows_adj_factor (
                    generation_id, trade_date, ts_code, adj_factor
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (str(generation_id), row["trade_date"], row["ts_code"], row["adj_factor"])
                    for row in rows
                ],
            )
            return
        if dataset == "stk_limit":
            connection.executemany(
                """
                INSERT INTO market_session_generation_rows_stk_limit (
                    generation_id, trade_date, ts_code, pre_close, up_limit, down_limit
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(generation_id),
                        row["trade_date"],
                        row["ts_code"],
                        row["pre_close"],
                        row["up_limit"],
                        row["down_limit"],
                    )
                    for row in rows
                ],
            )
            return
        if dataset == "suspend_d":
            connection.executemany(
                """
                INSERT INTO market_session_generation_rows_suspend_d (
                    generation_id, trade_date, ts_code, suspend_timing, suspend_type
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        str(generation_id),
                        row["trade_date"],
                        row["ts_code"],
                        row["suspend_timing"],
                        row["suspend_type"],
                    )
                    for row in rows
                ],
            )
            return
        raise PITReceiptError(f"unsupported market dataset: {dataset}")

    def _market_session_rows_from_db(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        dataset: str,
        *,
        include_generation_id: bool = False,
    ) -> List[Dict[str, Any]]:
        generation_field = "generation_id, " if include_generation_id else ""
        if dataset == "daily":
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT {generation_field}trade_date, ts_code, open, high, low, close, pre_close,
                           change, pct_chg, vol, amount
                    FROM market_session_generation_rows_daily
                    WHERE generation_id = ? ORDER BY trade_date, ts_code
                    """,
                    (str(generation_id),),
                )
            ]
        if dataset == "adj_factor":
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT {generation_field}trade_date, ts_code, adj_factor
                    FROM market_session_generation_rows_adj_factor
                    WHERE generation_id = ? ORDER BY trade_date, ts_code
                    """,
                    (str(generation_id),),
                )
            ]
        if dataset == "stk_limit":
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT {generation_field}trade_date, ts_code, pre_close, up_limit, down_limit
                    FROM market_session_generation_rows_stk_limit
                    WHERE generation_id = ? ORDER BY trade_date, ts_code
                    """,
                    (str(generation_id),),
                )
            ]
        if dataset == "suspend_d":
            return [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT {generation_field}trade_date, ts_code, suspend_timing, suspend_type
                    FROM market_session_generation_rows_suspend_d
                    WHERE generation_id = ? ORDER BY trade_date, ts_code, suspend_timing, suspend_type
                    """,
                    (str(generation_id),),
                )
            ]
        raise PITReceiptError(f"unsupported market dataset: {dataset}")

    def _market_session_manifest_on_connection(
        self,
        connection: sqlite3.Connection,
        generation_id: str,
        *,
        expected_semantics_override: Mapping[str, str] | None = None,
        verified_rows_out: Dict[str, List[Dict[str, Any]]] | None = None,
    ) -> Dict[str, Any]:
        generation = self._market_session_view(connection, generation_id)
        if generation["contract_sha256"] != _market_session_contract_sha256():
            raise PITReceiptError("market generation contract hash mismatch")
        shards = list(
            connection.execute(
                """
                SELECT * FROM market_session_generation_shards
                WHERE generation_id = ? ORDER BY dataset
                """,
                (str(generation_id),),
            )
        )
        expected_semantics = (
            dict(expected_semantics_override)
            if expected_semantics_override is not None
            else self._verified_generation_pin(generation, MARKET_SESSION_DATASETS)
        )
        dataset_keys = [row["dataset"] for row in shards]
        if set(dataset_keys) != set(MARKET_SESSION_DATASETS) or len(shards) != len(
            MARKET_SESSION_DATASETS
        ):
            raise PITReceiptError("market generation is incomplete; all four shards are required")
        trade_date = generation["trade_date"]
        _started_iso, started_utc = self._generation_timestamp(
            generation["started_at"], "generation started_at"
        )
        retrieved_values = []
        dataset_roots: Dict[str, str] = {}
        verified_rows_by_dataset: Dict[str, List[Dict[str, Any]]] = {}
        shard_manifest = []
        for stored_shard in shards:
            shard = dict(stored_shard)
            dataset = shard["dataset"]
            if shard["request_semantics_sha256"] != expected_semantics.get(dataset):
                raise PITReceiptError("market generation pinned semantics mismatch")
            attempt, normalized, normalized_sha256 = self._validated_market_attempt_on_connection(
                connection,
                trade_date=trade_date,
                dataset=dataset,
                attempt_id=shard["attempt_id"],
                allow_staged_event=True,
            )
            event = connection.execute(
                """
                SELECT details_json FROM fetch_promotion_events
                WHERE attempt_id = ? AND status = 'market_session_staged'
                """,
                (shard["attempt_id"],),
            ).fetchone()
            event_details = json.loads(event["details_json"]) if event else {}
            if event_details.get("generation_id") != str(generation_id):
                raise PITReceiptError("market generation promotion binding mismatch")
            if (
                attempt["request_semantics_sha256"] != shard["request_semantics_sha256"]
                or attempt["retrieved_at"] != shard["retrieved_at"]
                or attempt["raw_sha256"] != shard["raw_sha256"]
                or int(attempt["raw_bytes"]) != int(shard["raw_bytes"])
                or normalized_sha256 != shard["normalized_sha256"]
                or len(normalized) != int(shard["row_count"])
            ):
                raise PITReceiptError("market generation shard lineage mismatch")
            # Fetch the exact lineage row shape once. The same bounded,
            # single-session objects prove normalized equality, feed the
            # lineage hash, and may be consumed by coverage reconciliation.
            stored_rows = self._market_session_rows_from_db(
                connection,
                generation_id,
                dataset,
                include_generation_id=True,
            )
            if len(stored_rows) != len(normalized) or any(
                stored_row["generation_id"] != str(generation_id)
                or any(stored_row[field] != value for field, value in normalized_row.items())
                for stored_row, normalized_row in zip(stored_rows, normalized)
            ):
                raise PITReceiptError("market generation normalized rows mismatch")
            verified_rows_by_dataset[dataset] = stored_rows
            dataset_roots[dataset] = _stream_rows_sha256(normalized)[0]
            _retrieved_iso, retrieved_utc = self._generation_timestamp(
                shard["retrieved_at"], "generation shard retrieved_at"
            )
            if retrieved_utc < started_utc:
                raise PITReceiptError("market generation shard predates generation")
            retrieved_values.append(retrieved_utc)
            shard_manifest.append(
                {
                    "dataset": dataset,
                    "attempt_id": shard["attempt_id"],
                    "request_semantics_sha256": shard["request_semantics_sha256"],
                    "retrieved_at": shard["retrieved_at"],
                    "raw_sha256": shard["raw_sha256"],
                    "raw_bytes": int(shard["raw_bytes"]),
                    "normalized_sha256": shard["normalized_sha256"],
                    "row_count": int(shard["row_count"]),
                }
            )
        duplicate = connection.execute(
            """
            SELECT ts_code FROM market_session_generation_rows_daily
            WHERE generation_id = ? GROUP BY ts_code HAVING COUNT(*) > 1 LIMIT 1
            """,
            (str(generation_id),),
        ).fetchone()
        if duplicate is not None:
            raise PITReceiptError("market generation contains duplicate security rows")
        if (
            max(retrieved_values) - min(retrieved_values)
        ).total_seconds() > MARKET_SESSION_GENERATION_WINDOW_SECONDS or (
            max(retrieved_values) - started_utc
        ).total_seconds() > MARKET_SESSION_GENERATION_WINDOW_SECONDS:
            raise PITReceiptError("market generation shards exceed the freeze window")
        retrieved_at_min = min(retrieved_values).isoformat()
        retrieved_at_max = max(retrieved_values).isoformat()
        rows_root = _sha256(
            {dataset: dataset_roots[dataset] for dataset in MARKET_SESSION_DATASETS}
        )
        payload = {
            "schema_version": MARKET_SESSION_GENERATION_SCHEMA_VERSION,
            "generation_id": str(generation_id),
            "trade_date": trade_date,
            "contract_sha256": generation["contract_sha256"],
            "vintage": generation["vintage"],
            "final_oos_eligible": False,
            "started_at": generation["started_at"],
            "retrieved_at_min": retrieved_at_min,
            "retrieved_at_max": retrieved_at_max,
            "rows_root": rows_root,
            "dataset_roots": dataset_roots,
            "expected_request_semantics": expected_semantics,
            "expected_request_semantics_sha256": _sha256(expected_semantics),
            "shards": shard_manifest,
        }
        manifest_sha256 = _sha256(payload)
        lineage_sha256 = self._market_session_lineage_sha256_on_connection(
            connection,
            generation_id,
            verified_rows_by_dataset=verified_rows_by_dataset,
        )
        if verified_rows_out is not None:
            if verified_rows_out:
                raise PITReceiptError("verified market row capsule output is not empty")
            verified_rows_out.update(verified_rows_by_dataset)
        return {
            "manifest": payload,
            "manifest_sha256": manifest_sha256,
            "lineage_sha256": lineage_sha256,
        }

    @staticmethod
    def _market_session_lineage_sha256_on_connection(
        connection: sqlite3.Connection,
        generation_id: str,
        *,
        legacy: bool = False,
        verified_rows_by_dataset: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> str:
        # WHY: the lineage is an immutable receipt over what was frozen. It must
        # be a pure function of immutable generation semantics (identity +
        # contract + vintage + started_at), the four shards, their underlying
        # attempts and promotion events, and the normalized rows. Lifecycle
        # fields that mutate across the collecting -> published transition
        # (status, terminal_at, terminal_reason, manifest_sha256, lineage_sha256)
        # are deliberately excluded: otherwise the hash drifts between the
        # publish-time computation and any later verify re-computation, and the
        # stored receipt no longer proves what was frozen.
        generation_columns = (
            "generation_sequence, generation_id, trade_date, contract_sha256, "
            "vintage, final_oos_eligible, started_at"
        )
        if not legacy:
            generation_columns += (
                ", expected_request_semantics_json, expected_request_semantics_sha256"
            )
        generation = connection.execute(
            f"SELECT {generation_columns} FROM market_session_generations WHERE generation_id = ?",
            (str(generation_id),),
        ).fetchone()
        if generation is None:
            raise PITReceiptError("market generation lineage is incomplete")
        shards = [
            dict(row)
            for row in connection.execute(
                """
                SELECT generation_id, dataset, attempt_id, request_semantics_sha256,
                       retrieved_at, raw_sha256, raw_bytes, normalized_sha256, row_count
                FROM market_session_generation_shards
                WHERE generation_id = ? ORDER BY dataset
                """,
                (str(generation_id),),
            )
        ]
        attempts = [
            dict(row)
            for row in connection.execute(
                """
                SELECT attempt.* FROM fetch_attempts AS attempt
                JOIN market_session_generation_shards AS shard
                  ON shard.attempt_id = attempt.attempt_id
                WHERE shard.generation_id = ?
                ORDER BY shard.dataset
                """,
                (str(generation_id),),
            )
        ]
        events = [
            dict(row)
            for row in connection.execute(
                """
                SELECT event.* FROM fetch_promotion_events AS event
                JOIN market_session_generation_shards AS shard
                  ON shard.attempt_id = event.attempt_id
                WHERE shard.generation_id = ?
                ORDER BY shard.dataset
                """,
                (str(generation_id),),
            )
        ]
        if (
            len(shards) != len(MARKET_SESSION_DATASETS)
            or len(attempts) != len(MARKET_SESSION_DATASETS)
            or len(events) != len(MARKET_SESSION_DATASETS)
        ):
            raise PITReceiptError("market generation exact lineage is incomplete")
        # suspend_d carries two legitimate rows per (ts_code, suspend_type) when
        # the AM and PM sessions are both suspended; the ordering key must span
        # suspend_timing so the lineage bytes are deterministic in that case.
        rows_order = {
            "suspend_d": "trade_date, ts_code, suspend_timing, suspend_type",
        }
        if verified_rows_by_dataset is None:
            rows = {
                dataset: [
                    dict(row)
                    for row in connection.execute(
                        f"SELECT * FROM market_session_generation_rows_{dataset} "
                        f"WHERE generation_id = ? ORDER BY "
                        f"{rows_order.get(dataset, 'trade_date, ts_code')}",
                        (str(generation_id),),
                    )
                ]
                for dataset in MARKET_SESSION_DATASETS
            }
        else:
            if set(verified_rows_by_dataset) != set(MARKET_SESSION_DATASETS):
                raise PITReceiptError("verified market row capsule is incomplete")
            rows = {
                dataset: verified_rows_by_dataset[dataset]
                for dataset in MARKET_SESSION_DATASETS
            }
            if any(
                str(row.get("generation_id")) != str(generation_id)
                for dataset_rows in rows.values()
                for row in dataset_rows
            ):
                raise PITReceiptError("verified market row capsule generation mismatch")
        return _sha256(
            {
                "schema_version": "market-session-generation-lineage/v1",
                "generation_id": str(generation_id),
                "generation": dict(generation),
                "shards": shards,
                "rows": rows,
                "attempts": attempts,
                "promotion_events": events,
            }
        )

    def publish_market_session_generation(self, generation_id: str) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            generation = self._market_session_view(connection, generation_id)
            if generation["status"] == "abandoned":
                raise PITReceiptError("market generation is abandoned or inactive")
            if generation.get("expected_request_semantics_sha256") is None:
                rows = connection.execute(
                    "SELECT dataset, request_semantics_sha256 "
                    "FROM market_session_generation_shards WHERE generation_id = ?",
                    (str(generation_id),),
                )
                pin = {str(row[0]): str(row[1]) for row in rows}
                if set(pin) != set(MARKET_SESSION_DATASETS):
                    raise PITReceiptError(
                        "market generation is incomplete; all four shards are required"
                    )
                encoded, digest = self._expected_semantics_pin(
                    pin, _sha256(pin), MARKET_SESSION_DATASETS
                )
                connection.execute(
                    "UPDATE market_session_generations SET "
                    "expected_request_semantics_json = ?, "
                    "expected_request_semantics_sha256 = ? WHERE generation_id = ?",
                    (encoded, digest, str(generation_id)),
                )
                generation = self._market_session_view(connection, generation_id)
            verified = self._market_session_manifest_on_connection(connection, str(generation_id))
            if generation["status"] == "published":
                if generation["manifest_sha256"] != verified["manifest_sha256"]:
                    raise PITReceiptError("published market generation manifest mismatch")
                return {
                    **generation,
                    "manifest": verified["manifest"],
                    "manifest_sha256": verified["manifest_sha256"],
                    "lineage_sha256": verified["lineage_sha256"],
                }
            published_at = max(
                self._generation_timestamp(row[0], "generation shard retrieved_at")[1]
                for row in connection.execute(
                    """
                    SELECT retrieved_at FROM market_session_generation_shards
                    WHERE generation_id = ?
                    """,
                    (str(generation_id),),
                )
            ).isoformat()
            connection.execute(
                """
                UPDATE market_session_generations
                SET status = 'published', terminal_at = ?, manifest_sha256 = ?,
                    lineage_sha256 = ?
                WHERE generation_id = ? AND status = 'collecting'
                """,
                (
                    published_at,
                    verified["manifest_sha256"],
                    verified["lineage_sha256"],
                    str(generation_id),
                ),
            )
            connection.execute(
                """
                INSERT INTO market_session_generation_head (
                    trade_date, generation_id, manifest_sha256, published_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(trade_date) DO UPDATE SET
                    generation_id = excluded.generation_id,
                    manifest_sha256 = excluded.manifest_sha256,
                    published_at = excluded.published_at
                """,
                (
                    generation["trade_date"],
                    str(generation_id),
                    verified["manifest_sha256"],
                    published_at,
                ),
            )
            published_view = self._market_session_view(connection, generation_id)
            return {
                **published_view,
                "manifest": verified["manifest"],
                "manifest_sha256": verified["manifest_sha256"],
                "lineage_sha256": verified["lineage_sha256"],
            }

    def _active_market_session_on_connection(
        self,
        connection: sqlite3.Connection,
        trade_date: str,
        *,
        verified_rows_out: Dict[str, List[Dict[str, Any]]] | None = None,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]] | None:
        head = connection.execute(
            """
            SELECT generation_id, manifest_sha256
            FROM market_session_generation_head
            WHERE trade_date = ?
            """,
            (trade_date,),
        ).fetchone()
        if head is None:
            return None
        generation = self._market_session_view(connection, head["generation_id"])
        if generation["trade_date"] != trade_date:
            raise PITReceiptError("active market generation trade_date mismatch")
        if generation["status"] != "published":
            raise PITReceiptError("active market generation is not published")
        if head["manifest_sha256"] != generation["manifest_sha256"]:
            raise PITReceiptError("active market generation head manifest mismatch")
        latest = connection.execute(
            """
            SELECT MAX(generation_sequence)
            FROM market_session_generations
            WHERE trade_date = ? AND status = 'published'
            """,
            (trade_date,),
        ).fetchone()[0]
        if latest is None or int(generation["generation_sequence"]) != int(latest):
            raise PITReceiptError("active market generation head rollback detected")
        verified = self._market_session_manifest_on_connection(
            connection,
            str(generation["generation_id"]),
            verified_rows_out=verified_rows_out,
        )
        if head["manifest_sha256"] != verified["manifest_sha256"]:
            raise PITReceiptError("active market generation head hash mismatch")
        return generation, verified

    def active_market_session_generation(self, trade_date: str) -> Dict[str, Any] | None:
        session = _iso_date(trade_date, "trade_date")
        with self._connect() as connection:
            connection.execute("BEGIN")
            active = self._active_market_session_on_connection(connection, session)
            if active is None:
                return None
            generation, _verified = active
            return generation

    def verify_market_session_generation(
        self, generation_id: str = None, *, trade_date: str = None
    ) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            if generation_id is not None and trade_date is not None:
                raise PITReceiptError("specify generation_id or trade_date, not both")
            resolved = str(generation_id) if generation_id is not None else None
            active_verified = None
            if resolved is None:
                session = _iso_date(trade_date, "trade_date") if trade_date is not None else None
                if session is None:
                    raise PITReceiptError("market generation identifier is missing")
                active = self._active_market_session_on_connection(connection, session)
                if active is None:
                    raise PITReceiptError("active market generation is missing")
                generation, active_verified = active
                resolved = str(generation["generation_id"])
            else:
                generation = self._market_session_view(connection, resolved)
            if generation["status"] != "published":
                raise PITReceiptError("market generation is not published")
            verified = active_verified or self._market_session_manifest_on_connection(
                connection, resolved
            )
            if generation["manifest_sha256"] != verified["manifest_sha256"]:
                raise PITReceiptError("market generation manifest mismatch")
            stored_lineage = connection.execute(
                "SELECT lineage_sha256 FROM market_session_generations WHERE generation_id = ?",
                (resolved,),
            ).fetchone()
            # WHY: the persisted lineage is the immutable proof of what was
            # frozen. Constant-time compare it against the freshly recomputed
            # lineage so tampering with the stored value fails closed on both
            # the explicit (generation_id) and active (trade_date) verify paths.
            if (
                stored_lineage is None
                or not stored_lineage[0]
                or not hmac.compare_digest(str(stored_lineage[0]), str(verified["lineage_sha256"]))
            ):
                raise PITReceiptError("market generation lineage mismatch")
            return {
                **generation,
                "verification_status": "passed",
                **verified,
            }

    def _market_generation_coverage_on_connection(
        self,
        connection: sqlite3.Connection,
        sessions: Sequence[Any],
        *,
        on_verified_session: Optional[
            Callable[
                [str, Mapping[str, Any], Mapping[str, Sequence[Mapping[str, Any]]]],
                None,
            ]
        ] = None,
    ) -> Dict[str, Any]:
        """Prove every session in ``sessions`` has a published, active generation.

        WHY: coverage is the read-only aggregate receipt that each open-market
        day in a sorted trade-date list is frozen at its latest published head.
        It reuses the per-day active-head + manifest deep verification, then
        constant-time compares the stored lineage against the freshly recomputed
        lineage, so any missing / collecting / rolled-back / tampered (manifest,
        lineage, row, attempt, event) day fails closed. It only reads on the
        passed connection (no second connection, no legacy receipt writes) and
        never touches final_oos eligibility.
        """

        # WHY: coverage proves a frozen snapshot of the store; running it
        # outside an explicit transaction could observe a half-applied write,
        # so fail closed before touching any table.
        if not connection.in_transaction:
            raise PITReceiptError("market generation coverage requires an active transaction")
        session_list = list(sessions)
        if not session_list:
            raise PITReceiptError("market generation coverage requires at least one session")
        refs: List[Dict[str, Any]] = []
        for raw_session in session_list:
            session = _iso_date(raw_session, "trade_date")
            verified_rows: Dict[str, List[Dict[str, Any]]] = {}
            active = self._active_market_session_on_connection(
                connection,
                session,
                verified_rows_out=(
                    verified_rows if on_verified_session is not None else None
                ),
            )
            if active is None:
                raise PITReceiptError(
                    f"market generation coverage is missing a published generation for {session}"
                )
            generation, verified = active
            stored_lineage = generation.get("lineage_sha256")
            # WHY: the persisted lineage is the immutable proof of what was
            # frozen; compare it constant-time against the recomputed lineage so
            # tampering with the stored column fails closed exactly like the
            # explicit/active verify paths.
            if not stored_lineage or not hmac.compare_digest(
                str(stored_lineage), str(verified["lineage_sha256"])
            ):
                raise PITReceiptError("market generation coverage stored lineage mismatch")
            if on_verified_session is not None:
                # Consume before advancing to the next session; coverage never
                # accumulates row capsules or includes them in its receipt.
                on_verified_session(session, generation, verified_rows)
            refs.append(
                {
                    "trade_date": session,
                    "generation_id": str(generation["generation_id"]),
                    "manifest_sha256": str(verified["manifest_sha256"]),
                    "lineage_sha256": str(stored_lineage),
                    "vintage": str(generation["vintage"]),
                }
            )
        refs.sort(key=lambda ref: ref["trade_date"])
        return {"refs": refs, "count": len(refs), "root": _sha256(refs)}

    def current_pool_complete_bar_coverage(
        self, *, sessions: Sequence[Any], ts_codes: Sequence[str]
    ) -> Dict[str, Any]:
        """Count complete current-pool bars from active verified generations.

        A complete bar is the same-symbol, same-session intersection of
        ``daily``, ``adj_factor``, and ``stk_limit``.  ``suspend_d`` is sparse
        evidence, so an absent suspension row is not missing market history.
        The entire read uses one SQLite snapshot and streams one verified
        generation at a time instead of materializing the full market history.
        """

        normalized_sessions = [_iso_date(value, "trade_date") for value in sessions]
        if not normalized_sessions or normalized_sessions != sorted(
            set(normalized_sessions)
        ):
            raise PITReceiptError(
                "current-pool history sessions must be sorted, unique, and non-empty"
            )
        normalized_symbols = [str(value).strip().upper() for value in ts_codes]
        if (
            normalized_symbols != sorted(set(normalized_symbols))
            or any(
                len(symbol) != 9
                or symbol[6:] not in {".SH", ".SZ"}
                or not symbol[:6].isdigit()
                for symbol in normalized_symbols
            )
        ):
            raise PITReceiptError(
                "current-pool history symbols must be sorted, unique canonical ts_codes"
            )

        coverage_by_symbol: Dict[str, Dict[str, Any]] = {
            symbol: {
                "ts_code": symbol,
                "bar_count": 0,
                "daily_row_count": 0,
                "adj_factor_row_count": 0,
                "stk_limit_row_count": 0,
                "first_complete_trade_date": None,
                "last_complete_trade_date": None,
            }
            for symbol in normalized_symbols
        }
        symbol_set = set(normalized_symbols)

        def consume_verified_session(
            session: str,
            generation: Mapping[str, Any],
            rows_by_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        ) -> None:
            symbols_by_dataset: Dict[str, set[str]] = {}
            for dataset in NONEMPTY_MARKET_SESSION_DATASETS:
                rows = rows_by_dataset.get(dataset)
                if rows is None:
                    raise PITReceiptError(
                        f"verified market generation omitted {dataset}: {session}"
                    )
                current_symbols: set[str] = set()
                for row in rows:
                    if (
                        row.get("trade_date") != session
                        or row.get("generation_id") != generation["generation_id"]
                    ):
                        raise PITReceiptError(
                            f"verified {dataset} row generation binding mismatch: {session}"
                        )
                    symbol = str(row.get("ts_code") or "").strip().upper()
                    if symbol in current_symbols:
                        raise PITReceiptError(
                            f"duplicate verified {dataset} symbol: {symbol}/{session}"
                        )
                    current_symbols.add(symbol)
                    if symbol in symbol_set:
                        coverage_by_symbol[symbol][f"{dataset}_row_count"] += 1
                symbols_by_dataset[dataset] = current_symbols

            complete_symbols = symbol_set.intersection(
                *(symbols_by_dataset[dataset] for dataset in NONEMPTY_MARKET_SESSION_DATASETS)
            )
            for symbol in complete_symbols:
                item = coverage_by_symbol[symbol]
                item["bar_count"] += 1
                if item["first_complete_trade_date"] is None:
                    item["first_complete_trade_date"] = session
                item["last_complete_trade_date"] = session

        with self._connect() as connection:
            connection.execute("BEGIN")
            coverage = self._market_generation_coverage_on_connection(
                connection,
                normalized_sessions,
                on_verified_session=consume_verified_session,
            )
        return {
            "items": [coverage_by_symbol[symbol] for symbol in normalized_symbols],
            "market_generation_refs": coverage["refs"],
            "market_generation_count": coverage["count"],
            "market_generation_root_sha256": coverage["root"],
        }

    def _recover_orphan_promotions_on_connection(self, connection: sqlite3.Connection) -> int:
        orphan_rows = list(
            connection.execute(
                """
                SELECT attempt.attempt_id, attempt.dataset, attempt.partition_key,
                       attempt.endpoint, attempt.params_json, attempt.fields_json,
                       attempt.request_semantics_json,
                       attempt.request_semantics_sha256,
                       attempt.http_status, attempt.row_cap,
                       attempt.body_complete, attempt.raw_path,
                       attempt.raw_sha256, attempt.raw_bytes,
                       attempt.error_kind, attempt.recorded_at,
                       CASE WHEN receipt.dataset IS NULL THEN 0 ELSE 1 END
                           AS receipt_committed,
                       stock_shard.generation_id AS stock_generation_id,
                       stock_shard.logical_partition_key AS stock_partition_key,
                       market_shard.generation_id AS market_generation_id,
                       market_shard.dataset AS market_dataset,
                       etf_shard.generation_id AS etf_generation_id,
                       etf_shard.symbol AS etf_symbol,
                       etf_shard.normalized_sha256 AS etf_normalized_sha256
                FROM fetch_attempts AS attempt
                LEFT JOIN receipts AS receipt
                  ON receipt.dataset = attempt.dataset
                 AND receipt.partition_key = attempt.partition_key
                 AND receipt.endpoint = attempt.endpoint
                 AND receipt.params_json = attempt.params_json
                 AND receipt.http_status = attempt.http_status
                 AND receipt.row_cap = attempt.row_cap
                 AND receipt.raw_path = attempt.raw_path
                 AND receipt.raw_sha256 = attempt.raw_sha256
                 AND receipt.raw_bytes = attempt.raw_bytes
                LEFT JOIN stock_basic_generation_shards AS stock_shard
                  ON stock_shard.attempt_id = attempt.attempt_id
                LEFT JOIN market_session_generation_shards AS market_shard
                  ON market_shard.attempt_id = attempt.attempt_id
                LEFT JOIN etf_proxy_generation_shards AS etf_shard
                  ON etf_shard.attempt_id = attempt.attempt_id
                LEFT JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                WHERE event.attempt_id IS NULL
                ORDER BY attempt.attempt_sequence
                """
            )
        )
        for row in orphan_rows:
            semantics = json.loads(row["request_semantics_json"])
            params = json.loads(row["params_json"])
            fields = json.loads(row["fields_json"])
            try:
                semantic_receipt_params = _normalize_request_params(
                    row["dataset"], semantics.get("receipt_params") or {}
                )
            except (KeyError, PITReceiptError):
                semantic_receipt_params = {}
            semantics_recoverable = not (
                _sha256(semantics) != row["request_semantics_sha256"]
                or semantics.get("schema_version") != "tushare-wire-request/v1"
                or semantics.get("dataset") != row["dataset"]
                or semantics.get("partition_key") != row["partition_key"]
                or semantics.get("api_name") != row["endpoint"]
                or semantics.get("method") != "POST"
                or semantic_receipt_params != params
                or semantics.get("wire_params") != _canonical_wire_params(row["dataset"], params)
                or semantics.get("fields") != fields
                or int(semantics.get("row_cap") or 0) != int(row["row_cap"])
                or row["partition_key"] != _canonical_partition_key(row["dataset"], params)
            )
            committed = bool(
                row["receipt_committed"]
                or row["stock_generation_id"]
                or row["market_generation_id"]
                or row["etf_generation_id"]
            )
            if committed and not semantics_recoverable:
                raise PITReceiptError("orphan attempt request semantics are not recoverable")
            if row["receipt_committed"]:
                status = "reused"
                details = {
                    "receipt_raw_sha256": row["raw_sha256"],
                    "recovery": "receipt_committed_before_event",
                }
            elif row["stock_generation_id"]:
                status = "generation_staged"
                details = {
                    "generation_id": row["stock_generation_id"],
                    "logical_partition_key": row["stock_partition_key"],
                    "raw_sha256": row["raw_sha256"],
                    "recovery": "generation_shard_committed_before_event",
                }
            elif row["market_generation_id"]:
                status = "market_session_staged"
                details = {
                    "generation_id": row["market_generation_id"],
                    "dataset": row["market_dataset"],
                    "raw_sha256": row["raw_sha256"],
                    "recovery": "generation_shard_committed_before_event",
                }
            elif row["etf_generation_id"]:
                status = "etf_proxy_staged"
                details = {
                    "generation_id": row["etf_generation_id"],
                    "symbol": row["etf_symbol"],
                    "raw_sha256": row["raw_sha256"],
                    "normalized_sha256": row["etf_normalized_sha256"],
                    "recovery": "generation_shard_committed_before_event",
                }
            else:
                error_kind = str(row["error_kind"] or "")
                status = {
                    "transport_error": "transport_error",
                    "http_retryable": "http_error",
                    "http_error": "http_error",
                    "api_error": "api_error",
                    "invalid_json": "invalid_json",
                    "incomplete_response": "invalid_json",
                    "unsupported_content_encoding": "invalid_json",
                    "credential_echo": "invalid_json",
                    "clock_attestation_failed": "clock_error",
                }.get(error_kind)
                reason = error_kind or "uncommitted_before_crash"
                if status is None:
                    if row["http_status"] is None:
                        status, reason = "transport_error", "missing_http_response"
                    elif not 200 <= int(row["http_status"]) < 300:
                        status, reason = "http_error", "unsuccessful_http_response"
                    elif not row["body_complete"] or not row["raw_path"]:
                        status, reason = "invalid_json", "incomplete_response"
                    else:
                        # A captured 2xx body without a committed receipt or shard is
                        # evidence of an interrupted validation/staging path.  Never
                        # replay it as success during recovery: doing so would turn a
                        # crash gap into acceptance after the fact.
                        status, reason = "rejected", "uncommitted_before_crash"
                evidence = {
                    "attempt_id": row["attempt_id"],
                    "http_status": row["http_status"],
                    "body_complete": bool(row["body_complete"]),
                    "raw_sha256": row["raw_sha256"],
                    "error_kind": error_kind or None,
                    "reason": reason,
                }
                details = {
                    "recovery": "recorded_attempt_without_terminal_event",
                    "reason": reason,
                    "evidence_sha256": _sha256(evidence),
                    "attempt_recorded_at": row["recorded_at"],
                }
            connection.execute(
                """
                INSERT INTO fetch_promotion_events (
                    attempt_id, status, details_json, recorded_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    row["attempt_id"],
                    status,
                    _canonical_json(details),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        return len(orphan_rows)

    def controlled_receipt_index(self) -> Dict[Tuple[str, str, str], Dict[str, Any]]:
        """Deep-verify receipts once, then index collector-backed resume identities."""

        output: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._verify_receipts_on_connection(connection)
            self._recover_orphan_promotions_on_connection(connection)
            for row in connection.execute(
                """
                SELECT attempt.dataset, attempt.partition_key,
                       attempt.endpoint, attempt.params_json, attempt.fields_json,
                       attempt.request_semantics_json,
                       attempt.request_semantics_sha256,
                       attempt.row_cap, attempt.raw_sha256, event.status
                FROM fetch_attempts AS attempt
                JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                JOIN receipts AS receipt
                  ON receipt.dataset = attempt.dataset
                 AND receipt.partition_key = attempt.partition_key
                 AND receipt.raw_sha256 = attempt.raw_sha256
                WHERE event.status IN ('stored', 'reused')
                ORDER BY event.event_sequence
                """
            ):
                semantics = json.loads(row["request_semantics_json"])
                if _sha256(semantics) != row["request_semantics_sha256"]:
                    raise PITReceiptError("attempt request semantics hash mismatch")
                if semantics.get("schema_version") != "tushare-wire-request/v1":
                    continue
                params = json.loads(row["params_json"])
                try:
                    semantic_receipt_params = _normalize_request_params(
                        row["dataset"], semantics.get("receipt_params") or {}
                    )
                except (KeyError, PITReceiptError) as exc:
                    raise PITReceiptError("attempt receipt params lineage mismatch") from exc
                if semantic_receipt_params != params:
                    raise PITReceiptError("attempt receipt params lineage mismatch")
                fields = json.loads(row["fields_json"])
                if (
                    semantics.get("dataset") != row["dataset"]
                    or semantics.get("partition_key") != row["partition_key"]
                    or semantics.get("api_name") != row["endpoint"]
                    or semantics.get("method") != "POST"
                    or semantics.get("wire_params")
                    != _canonical_wire_params(row["dataset"], params)
                    or int(semantics.get("row_cap") or 0) != int(row["row_cap"])
                    or row["partition_key"] != _canonical_partition_key(row["dataset"], params)
                ):
                    raise PITReceiptError("attempt wire request lineage mismatch")
                if semantics.get("fields") != fields:
                    raise PITReceiptError("attempt request fields lineage mismatch")
                key = (
                    row["dataset"],
                    row["partition_key"],
                    row["request_semantics_sha256"],
                )
                output[key] = {
                    "dataset": row["dataset"],
                    "partition_key": row["partition_key"],
                    "request_semantics_sha256": row["request_semantics_sha256"],
                    "receipt_raw_sha256": row["raw_sha256"],
                    "promotion_status": row["status"],
                }
        return output

    def promote_fetch_attempt(self, attempt_id: str) -> Dict[str, Any]:
        attempt = self._fetch_attempt(str(attempt_id))
        if attempt["promotion_events"]:
            event = attempt["promotion_events"][-1]
            return self._promotion_result(attempt["attempt_id"], event["status"], event["details"])
        if attempt["dataset"] == "fund_daily":
            # fund_daily assembles a multi-symbol ETF proxy session in a dedicated
            # generation stage; a single raw attempt is never a promotable receipt.
            # Fail closed BEFORE any receipt/source write so no legacy table can
            # capture a half-assembled session. The terminal event is durable and
            # idempotent: a repeat call returns the existing status unchanged.
            return self._append_promotion_event(
                attempt["attempt_id"],
                "generation_required",
                {"reason": "fund_daily generation required"},
            )
        if attempt["http_status"] is None or not 200 <= int(attempt["http_status"]) < 300:
            return self._append_promotion_event(attempt["attempt_id"], "http_error", {})
        if not attempt["body_complete"] or not attempt["raw_path"]:
            return self._append_promotion_event(attempt["attempt_id"], "invalid_json", {})
        raw_bytes = self._verify_raw_file(attempt)
        try:
            payload = json.loads(
                raw_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_object_keys
            )
            response_code = int(payload.get("code")) if isinstance(payload, dict) else None
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
            return self._append_promotion_event(attempt["attempt_id"], "invalid_json", {})
        if response_code != 0:
            return self._append_promotion_event(attempt["attempt_id"], "api_error", {})
        try:
            response_fields, _items, _code, _message = _parse_native_envelope(raw_bytes)
            if any(field not in response_fields for field in attempt["fields"]):
                raise PITReceiptError("native response missing requested fields")
            receipt = self.ingest_tushare_response(
                dataset=attempt["dataset"],
                partition_key=attempt["partition_key"],
                endpoint=attempt["endpoint"],
                params=attempt["params"],
                raw_bytes=raw_bytes,
                http_status=int(attempt["http_status"]),
                retrieved_at=attempt["retrieved_at"],
                row_cap=int(attempt["row_cap"]),
            )
        except PITReceiptError as exc:
            if "immutable partition conflict" in str(exc):
                with self._connect() as connection:
                    existing = connection.execute(
                        """
                        SELECT raw_sha256 FROM receipts
                        WHERE dataset = ? AND partition_key = ?
                        """,
                        (attempt["dataset"], attempt["partition_key"]),
                    ).fetchone()
                details = {
                    "existing_raw_sha256": existing["raw_sha256"] if existing else None,
                    "candidate_raw_sha256": attempt["raw_sha256"],
                }
                return self._append_promotion_event(
                    attempt["attempt_id"], "immutable_conflict", details
                )
            return self._append_promotion_event(attempt["attempt_id"], "invalid_json", {})
        details = {"receipt_raw_sha256": receipt["raw_sha256"]}
        return self._append_promotion_event(attempt["attempt_id"], receipt["status"], details)

    def ingest_tushare_response(
        self,
        *,
        dataset: str,
        partition_key: str,
        endpoint: str,
        params: Mapping[str, Any],
        raw_bytes: bytes,
        http_status: int,
        retrieved_at: str,
        row_cap: int,
    ) -> Dict[str, Any]:
        if dataset not in DATASET_ENDPOINTS or endpoint != DATASET_ENDPOINTS[dataset]:
            raise PITReceiptError("unsupported dataset/endpoint")
        if not isinstance(raw_bytes, bytes):
            raise PITReceiptError("raw response must be bytes")
        if len(raw_bytes) > MAX_RAW_BYTES[dataset]:
            raise PITReceiptError(f"raw response exceeds safe byte limit for {dataset}")
        if not 200 <= int(http_status) < 300:
            raise PITReceiptError(f"HTTP status is not successful: {http_status}")
        if int(row_cap) <= 0:
            raise PITReceiptError("row_cap must be positive")
        if {str(key).lower() for key in params} & {"token", "api_key", "apikey", "secret"}:
            raise PITReceiptError("request params may not persist credentials")
        official_cap = OFFICIAL_ROW_CAPS.get(dataset)
        if official_cap is not None and int(row_cap) != official_cap:
            raise PITReceiptError(
                f"{dataset} must use official row cap {official_cap}, got {row_cap}"
            )
        params = _normalize_request_params(dataset, params)
        if dataset == "stock_basic":
            requested_exchange = str(params.get("exchange") or "").strip().upper()
            requested_status = str(params.get("list_status") or "").strip().upper()
            if requested_exchange not in {"SSE", "SZSE", "BSE"} or requested_status not in {
                "L",
                "D",
                "P",
                "G",
            }:
                raise PITReceiptError("stock_basic request shard is incomplete")
            if partition_key != f"{requested_exchange}:{requested_status}":
                raise PITReceiptError("stock_basic partition key does not match request")
        if dataset == "trade_cal":
            requested_exchange = str(params.get("exchange") or "").strip().upper()
            requested_start = _iso_date(params.get("start_date"), "trade_cal start_date")
            requested_end = _iso_date(params.get("end_date"), "trade_cal end_date")
            if requested_exchange not in {"SSE", "SZSE", "BSE"}:
                raise PITReceiptError("trade_cal exchange is invalid")
            if requested_end < requested_start:
                raise PITReceiptError("trade_cal end_date precedes start_date")
            expected_partition = f"{requested_exchange}:{requested_start}:{requested_end}"
            if partition_key != expected_partition:
                raise PITReceiptError("trade_cal partition key does not match request")
        timestamp = _aware_timestamp(retrieved_at)
        fields, items, response_code, response_message = _parse_native_envelope(raw_bytes)
        if len(items) >= int(row_cap):
            raise PITReceiptError(f"native response reached row cap ({len(items)}/{int(row_cap)})")
        if dataset in {"bak_basic", "trade_cal"} and not items:
            raise PITReceiptError(f"{dataset} response is empty")
        normalized = _normalize_rows(dataset, partition_key, params, fields, items)
        raw_digest = hashlib.sha256(raw_bytes).hexdigest()
        normalized_digest = _sha256(normalized)
        params_json = _canonical_json(dict(params))

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM receipts WHERE dataset = ? AND partition_key = ?",
                (dataset, partition_key),
            ).fetchone()
            if existing is not None:
                return self._reuse_existing(
                    connection,
                    existing,
                    dataset=dataset,
                    partition_key=partition_key,
                    endpoint=endpoint,
                    params_json=params_json,
                    http_status=http_status,
                    row_cap=row_cap,
                    raw_digest=raw_digest,
                    normalized_digest=normalized_digest,
                    normalized_count=len(normalized),
                )

        raw_path = self._publish_raw(dataset, raw_bytes, raw_digest)
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM receipts WHERE dataset = ? AND partition_key = ?",
                    (dataset, partition_key),
                ).fetchone()
                if existing is not None:
                    return self._reuse_existing(
                        connection,
                        existing,
                        dataset=dataset,
                        partition_key=partition_key,
                        endpoint=endpoint,
                        params_json=params_json,
                        http_status=http_status,
                        row_cap=row_cap,
                        raw_digest=raw_digest,
                        normalized_digest=normalized_digest,
                        normalized_count=len(normalized),
                    )
                connection.execute(
                    """
                    INSERT INTO receipts (
                        dataset, partition_key, endpoint, params_json, retrieved_at,
                        http_status, raw_path, raw_sha256, raw_bytes, response_code, response_message,
                        row_cap, row_count, normalized_sha256, parser_version
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        dataset,
                        partition_key,
                        endpoint,
                        params_json,
                        timestamp,
                        int(http_status),
                        raw_path,
                        raw_digest,
                        len(raw_bytes),
                        response_code,
                        response_message,
                        int(row_cap),
                        len(normalized),
                        normalized_digest,
                        PARSER_VERSION,
                    ),
                )
                self._insert_normalized(connection, dataset, partition_key, normalized)
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise PITReceiptError(f"duplicate normalized key: {exc}") from exc
        return {
            "dataset": dataset,
            "partition_key": partition_key,
            "endpoint": endpoint,
            "params_json": params_json,
            "retrieved_at": timestamp,
            "http_status": int(http_status),
            "raw_path": raw_path,
            "raw_sha256": raw_digest,
            "raw_bytes": len(raw_bytes),
            "response_code": response_code,
            "response_message": response_message,
            "row_cap": int(row_cap),
            "row_count": len(normalized),
            "normalized_sha256": normalized_digest,
            "parser_version": PARSER_VERSION,
            "status": "stored",
        }

    def _reuse_existing(
        self,
        connection: sqlite3.Connection,
        existing: sqlite3.Row,
        *,
        dataset: str,
        partition_key: str,
        endpoint: str,
        params_json: str,
        http_status: int,
        row_cap: int,
        raw_digest: str,
        normalized_digest: str,
        normalized_count: int,
    ) -> Dict[str, Any]:
        same_request = (
            existing["raw_sha256"] == raw_digest
            and existing["endpoint"] == endpoint
            and existing["params_json"] == params_json
            and int(existing["http_status"]) == int(http_status)
            and int(existing["row_cap"]) == int(row_cap)
            and existing["parser_version"] == PARSER_VERSION
        )
        if not same_request:
            raise PITReceiptError(f"immutable partition conflict: {dataset}/{partition_key}")
        self._verify_raw_file(dict(existing))
        if existing["normalized_sha256"] != normalized_digest or int(existing["row_count"]) != int(
            normalized_count
        ):
            raise PITReceiptError("normalized source hash mismatch")
        stored_rows = self._normalized_rows_from_db(connection, dataset, partition_key)
        if _sha256(stored_rows) != normalized_digest:
            raise PITReceiptError("normalized rows hash mismatch")
        return {**dict(existing), "status": "reused"}

    @staticmethod
    def _insert_normalized(
        connection: sqlite3.Connection,
        dataset: str,
        partition_key: str,
        rows: Iterable[Mapping[str, Any]],
    ) -> None:
        if dataset == "stock_basic":
            connection.executemany(
                """
                INSERT INTO security_master (
                    ts_code, symbol, name, exchange, market, list_status,
                    list_date, delist_date, receipt_dataset, receipt_partition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["ts_code"],
                        row["symbol"],
                        row["name"],
                        row["exchange"],
                        row["market"],
                        row["list_status"],
                        row["list_date"],
                        row["delist_date"],
                        "stock_basic",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        elif dataset == "trade_cal":
            connection.executemany(
                """
                INSERT INTO trade_sessions (
                    exchange, cal_date, is_open, pretrade_date, receipt_partition
                    , receipt_dataset
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["exchange"],
                        row["cal_date"],
                        row["is_open"],
                        row["pretrade_date"],
                        partition_key,
                        "trade_cal",
                    )
                    for row in rows
                ],
            )
        elif dataset == "bak_basic":
            connection.executemany(
                """
                INSERT INTO daily_universe (
                    trade_date, ts_code, exchange, name, industry, list_date,
                    receipt_dataset, receipt_partition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_date"],
                        row["ts_code"],
                        row["exchange"],
                        row["name"],
                        row["industry"],
                        row["list_date"],
                        "bak_basic",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        elif dataset == "daily":
            connection.executemany(
                """
                INSERT INTO raw_daily_bars (
                    trade_date, ts_code, open, high, low, close, pre_close,
                    change, pct_chg, vol, amount, receipt_dataset,
                    receipt_partition
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_date"],
                        row["ts_code"],
                        row["open"],
                        row["high"],
                        row["low"],
                        row["close"],
                        row["pre_close"],
                        row["change"],
                        row["pct_chg"],
                        row["vol"],
                        row["amount"],
                        "daily",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        elif dataset == "adj_factor":
            connection.executemany(
                """
                INSERT INTO adjustment_factors (
                    trade_date, ts_code, adj_factor, receipt_dataset,
                    receipt_partition
                ) VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_date"],
                        row["ts_code"],
                        row["adj_factor"],
                        "adj_factor",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        elif dataset == "stk_limit":
            connection.executemany(
                """
                INSERT INTO daily_price_limits (
                    trade_date, ts_code, pre_close, up_limit, down_limit,
                    receipt_dataset, receipt_partition
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_date"],
                        row["ts_code"],
                        row["pre_close"],
                        row["up_limit"],
                        row["down_limit"],
                        "stk_limit",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        elif dataset == "suspend_d":
            connection.executemany(
                """
                INSERT INTO suspension_events (
                    trade_date, ts_code, suspend_timing, suspend_type,
                    receipt_dataset, receipt_partition
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        row["trade_date"],
                        row["ts_code"],
                        row["suspend_timing"],
                        row["suspend_type"],
                        "suspend_d",
                        partition_key,
                    )
                    for row in rows
                ],
            )
        else:
            raise PITReceiptError(f"unsupported normalized dataset: {dataset}")

    def _safe_raw_path(self, relative_value: str) -> Path:
        relative = Path(str(relative_value))
        if relative.is_absolute() or ".." in relative.parts:
            raise PITReceiptError("unsafe raw receipt path")
        current = self.root
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                raise PITReceiptError("raw receipt path contains a symlink")
        try:
            current.resolve().relative_to(self.root)
        except ValueError as exc:
            raise PITReceiptError("raw receipt path escapes store") from exc
        if not current.is_file():
            raise PITReceiptError("raw receipt file is missing")
        return current

    def _verify_raw_file(self, receipt: Mapping[str, Any]) -> bytes:
        path = self._safe_raw_path(str(receipt["raw_path"]))
        size = path.stat().st_size
        if size != int(receipt["raw_bytes"]):
            raise PITReceiptError("raw receipt byte count mismatch")
        if size > MAX_RAW_BYTES[str(receipt["dataset"])]:
            raise PITReceiptError("raw receipt exceeds safe byte limit")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != receipt["raw_sha256"]:
            raise PITReceiptError("raw receipt hash mismatch")
        if path.stem != receipt["raw_sha256"]:
            raise PITReceiptError("raw receipt content-addressed path mismatch")
        return content

    def _normalized_rows_from_db(
        self, connection: sqlite3.Connection, dataset: str, partition_key: str
    ) -> List[Dict[str, Any]]:
        if dataset == "stock_basic":
            cursor = connection.execute(
                """
                SELECT ts_code, symbol, name, exchange, market, list_status,
                       list_date, delist_date
                FROM security_master WHERE receipt_partition = ? ORDER BY ts_code
                """,
                (partition_key,),
            )
        elif dataset == "trade_cal":
            cursor = connection.execute(
                """
                SELECT exchange, cal_date, is_open, pretrade_date
                FROM trade_sessions WHERE receipt_partition = ? ORDER BY exchange, cal_date
                """,
                (partition_key,),
            )
        elif dataset == "bak_basic":
            cursor = connection.execute(
                """
                SELECT trade_date, ts_code, exchange, name, industry, list_date
                FROM daily_universe WHERE receipt_partition = ? ORDER BY trade_date, ts_code
                """,
                (partition_key,),
            )
        elif dataset == "daily":
            cursor = connection.execute(
                """
                SELECT trade_date, ts_code, open, high, low, close, pre_close,
                       change, pct_chg, vol, amount
                FROM raw_daily_bars
                WHERE receipt_partition = ? ORDER BY trade_date, ts_code
                """,
                (partition_key,),
            )
        elif dataset == "adj_factor":
            cursor = connection.execute(
                """
                SELECT trade_date, ts_code, adj_factor
                FROM adjustment_factors
                WHERE receipt_partition = ? ORDER BY trade_date, ts_code
                """,
                (partition_key,),
            )
        elif dataset == "stk_limit":
            cursor = connection.execute(
                """
                SELECT trade_date, ts_code, pre_close, up_limit, down_limit
                FROM daily_price_limits
                WHERE receipt_partition = ? ORDER BY trade_date, ts_code
                """,
                (partition_key,),
            )
        elif dataset == "suspend_d":
            cursor = connection.execute(
                """
                SELECT trade_date, ts_code, suspend_timing, suspend_type
                FROM suspension_events
                WHERE receipt_partition = ?
                ORDER BY trade_date, ts_code, suspend_type, suspend_timing
                """,
                (partition_key,),
            )
        else:
            raise PITReceiptError(f"unsupported normalized dataset: {dataset}")
        return [dict(row) for row in cursor]

    def verify_receipts(self) -> Dict[str, Any]:
        with self._connect() as connection:
            connection.execute("BEGIN")
            result = self._verify_receipts_on_connection(connection)
            return {key: value for key, value in result.items() if key != "receipt_refs"}

    def common_open_sessions(
        self,
        *,
        start_date: str,
        end_date: str,
        exchanges: Sequence[str] = CALENDAR_SOURCE_EXCHANGES,
    ) -> List[str]:
        """Verify complete calendars for the requested exchanges and return common open sessions.

        Defaults to CALENDAR_SOURCE_EXCHANGES (SSE only) because jiaoch trade_cal only
        maintains SSE; A-share SSE/SZSE calendars are identical since 2010.
        """

        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if end < start:
            raise PITReceiptError("end_date precedes start_date")
        expected_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
        open_sets = []
        with self._connect() as connection:
            connection.execute("BEGIN")
            self._verify_receipts_on_connection(connection)
            for exchange in exchanges:
                count = int(
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM trade_sessions
                        WHERE exchange = ? AND cal_date BETWEEN ? AND ?
                        """,
                        (exchange, start, end),
                    ).fetchone()[0]
                )
                if count != expected_days:
                    raise PITReceiptError(f"calendar date coverage is incomplete for {exchange}")
                rows = list(
                    connection.execute(
                        """
                        SELECT cal_date, pretrade_date FROM trade_sessions
                        WHERE exchange = ? AND is_open = 1 AND cal_date BETWEEN ? AND ?
                        ORDER BY cal_date
                        """,
                        (exchange, start, end),
                    )
                )
                for index, row in enumerate(rows):
                    previous = row["pretrade_date"]
                    if not previous or previous >= row["cal_date"]:
                        raise PITReceiptError(
                            f"trade calendar pretrade chain is invalid for {exchange}"
                        )
                    if index > 0 and previous != rows[index - 1]["cal_date"]:
                        raise PITReceiptError(
                            f"trade calendar pretrade chain is broken for {exchange}"
                        )
                open_sets.append({row["cal_date"] for row in rows})
        if not open_sets[0]:
            raise PITReceiptError("calendar coverage has no open sessions")
        if any(sessions != open_sets[0] for sessions in open_sets[1:]):
            raise PITReceiptError("exchange open-session calendars disagree")
        return sorted(open_sets[0])

    def _verify_receipts_on_connection(self, connection: sqlite3.Connection) -> Dict[str, Any]:
        manifest_rows = []
        verified_count = 0
        integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
        if integrity != "ok":
            raise PITReceiptError(f"SQLite integrity check failed: {integrity}")
        foreign_key_errors = list(connection.execute("PRAGMA foreign_key_check"))
        if foreign_key_errors:
            raise PITReceiptError("normalized rows contain broken receipt foreign keys")
        cursor = connection.execute("SELECT * FROM receipts ORDER BY dataset, partition_key")
        for stored in cursor:
            receipt = dict(stored)
            if receipt["endpoint"] != DATASET_ENDPOINTS.get(receipt["dataset"]):
                raise PITReceiptError("stored endpoint does not match dataset")
            if receipt["parser_version"] != PARSER_VERSION:
                raise PITReceiptError("stored parser version is not trusted")
            _aware_timestamp(receipt["retrieved_at"])
            official_cap = OFFICIAL_ROW_CAPS.get(receipt["dataset"])
            if official_cap is not None and int(receipt["row_cap"]) != official_cap:
                raise PITReceiptError("stored receipt does not use official row cap")
            raw_bytes = self._verify_raw_file(receipt)
            if not 200 <= int(receipt["http_status"]) < 300:
                raise PITReceiptError("stored HTTP status is not successful")
            fields, items, response_code, response_message = _parse_native_envelope(raw_bytes)
            params = json.loads(receipt["params_json"])
            if params != _normalize_request_params(receipt["dataset"], params):
                raise PITReceiptError("stored request params are not canonical")
            normalized = _normalize_rows(
                receipt["dataset"], receipt["partition_key"], params, fields, items
            )
            if len(items) >= int(receipt["row_cap"]):
                raise PITReceiptError("stored receipt reaches row cap")
            if (
                response_code != receipt["response_code"]
                or response_message != receipt["response_message"]
            ):
                raise PITReceiptError("stored response metadata mismatch")
            if len(normalized) != int(receipt["row_count"]):
                raise PITReceiptError("normalized row count mismatch")
            if _sha256(normalized) != receipt["normalized_sha256"]:
                raise PITReceiptError("normalized source hash mismatch")
            stored_rows = self._normalized_rows_from_db(
                connection, receipt["dataset"], receipt["partition_key"]
            )
            if _sha256(stored_rows) != receipt["normalized_sha256"]:
                raise PITReceiptError("normalized rows hash mismatch")
            receipt_semantics = {
                "dataset": receipt["dataset"],
                "partition_key": receipt["partition_key"],
                "endpoint": receipt["endpoint"],
                "params": params,
                "retrieved_at": receipt["retrieved_at"],
                "http_status": receipt["http_status"],
                "raw_path": receipt["raw_path"],
                "raw_sha256": receipt["raw_sha256"],
                "raw_bytes": receipt["raw_bytes"],
                "response_code": receipt["response_code"],
                "response_message": receipt["response_message"],
                "row_cap": receipt["row_cap"],
                "row_count": receipt["row_count"],
                "normalized_sha256": receipt["normalized_sha256"],
                "parser_version": receipt["parser_version"],
            }
            manifest_rows.append(
                {
                    "dataset": receipt["dataset"],
                    "partition_key": receipt["partition_key"],
                    "receipt_sha256": _sha256(receipt_semantics),
                }
            )
            verified_count += 1
        return {
            "verified_receipt_count": verified_count,
            "receipt_manifest_sha256": _sha256(manifest_rows),
            "receipt_refs": manifest_rows,
        }

    def _verify_official_notice_receipt_on_connection(
        self, connection: sqlite3.Connection, receipt_id: str
    ) -> Dict[str, Any]:
        stored = connection.execute(
            "SELECT * FROM official_notice_receipts WHERE receipt_id = ?",
            (str(receipt_id),),
        ).fetchone()
        if stored is None:
            raise PITReceiptError("official suspension notice receipt is missing")
        receipt = dict(stored)
        if receipt["source_profile"] != CNINFO_SOURCE_PROFILE:
            raise PITReceiptError("official suspension source profile is not trusted")
        parser_version = str(receipt["parser_version"])
        if parser_version not in SUPPORTED_CNINFO_SUSPENSION_PARSER_VERSIONS:
            raise PITReceiptError("official suspension parser version is not trusted")
        published_at = _aware_timestamp(receipt["published_at"])
        retrieved_at = _aware_timestamp(receipt["retrieved_at"])
        if datetime.fromisoformat(retrieved_at) < datetime.fromisoformat(published_at):
            raise PITReceiptError("official suspension retrieval precedes publication")
        raw_sha256 = _require_sha256(
            receipt["raw_sha256"], "official suspension raw hash"
        )
        raw_bytes = _require_int(
            receipt["raw_bytes"], "official suspension raw bytes"
        )
        expected_path = (
            Path("raw")
            / "cninfo_suspension"
            / raw_sha256[:2]
            / f"{raw_sha256}.pdf"
        ).as_posix()
        if str(receipt["raw_path"]) != expected_path:
            raise PITReceiptError("official suspension raw path is not content-addressed")
        path = self._safe_raw_path(str(receipt["raw_path"]))
        if raw_bytes <= 0 or raw_bytes > MAX_CNINFO_PDF_BYTES:
            raise PITReceiptError("official suspension raw byte count is outside policy")
        if path.stat().st_size != raw_bytes:
            raise PITReceiptError("official suspension raw byte count mismatch")
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != raw_sha256:
            raise PITReceiptError("official suspension raw hash mismatch")
        try:
            evidence = parse_cninfo_suspension_pdf(
                content,
                source_url=receipt["source_url"],
                published_at=published_at,
                expected_ts_code=receipt["ts_code"],
                notice_role=receipt["notice_role"],
                parser_version=parser_version,
            )
        except SuspensionEvidenceError as exc:
            raise PITReceiptError(
                f"official suspension PDF verification failed: {exc}"
            ) from exc
        normalized_sha256 = _require_sha256(
            receipt["normalized_sha256"], "official suspension normalized hash"
        )
        if _sha256(evidence) != normalized_sha256:
            raise PITReceiptError("official suspension normalized hash mismatch")
        expected_columns = {
            "source_profile": evidence["source_profile"],
            "source_url": evidence["source_url"],
            "published_at": evidence["published_at"],
            "notice_role": evidence["notice_role"],
            "ts_code": evidence["ts_code"],
            "announcement_number": evidence["announcement_number"],
            "effective_date": evidence["effective_date"],
            "raw_sha256": evidence["raw_sha256"],
            "raw_bytes": evidence["raw_bytes"],
            "text_sha256": evidence["text_sha256"],
            "parser_version": evidence["parser_version"],
        }
        for field, expected in expected_columns.items():
            actual = raw_bytes if field == "raw_bytes" else receipt[field]
            if actual != expected:
                raise PITReceiptError(
                    f"official suspension receipt {field} does not match PDF"
                )
        expected_receipt_id = _sha256(
            {
                "schema_version": CNINFO_SUSPENSION_EVIDENCE_SCHEMA_VERSION,
                "source_url": evidence["source_url"],
                "raw_sha256": evidence["raw_sha256"],
                "normalized_sha256": normalized_sha256,
            }
        )
        if not hmac.compare_digest(str(receipt["receipt_id"]), expected_receipt_id):
            raise PITReceiptError("official suspension receipt identity mismatch")
        return self._official_notice_ref(receipt)

    def _verify_official_suspension_evidence_on_connection(
        self,
        connection: sqlite3.Connection,
        sessions: Sequence[str],
    ) -> Dict[str, Any]:
        if not sessions:
            return {
                "count": 0,
                "refs": [],
                "root": _sha256([]),
                "codes_by_session": {},
                "parser_versions": [],
            }
        rows = list(
            connection.execute(
                """
                SELECT * FROM official_suspension_intervals
                WHERE start_date <= ? AND resume_date > ?
                ORDER BY ts_code, start_date, resume_date, interval_id
                """,
                (sessions[-1], sessions[0]),
            )
        )
        refs = []
        parser_versions = set()
        codes_by_session: Dict[str, set[str]] = {session: set() for session in sessions}
        prior_by_symbol: Dict[str, Tuple[str, str]] = {}
        for stored in rows:
            interval = dict(stored)
            interval_id = _require_sha256(
                interval["interval_id"], "official suspension interval identity"
            )
            symbol = _market_ts_code(interval["ts_code"], "official suspension")
            start_date = _iso_date(interval["start_date"], "official suspension start date")
            resume_date = _iso_date(
                interval["resume_date"], "official suspension resume date"
            )
            if resume_date <= start_date:
                raise PITReceiptError("official suspension interval is not half-open")
            prior = prior_by_symbol.get(symbol)
            if prior is not None and start_date < prior[1]:
                raise PITReceiptError("official suspension intervals overlap")
            prior_by_symbol[symbol] = (start_date, resume_date)
            start_receipt = self._verify_official_notice_receipt_on_connection(
                connection, interval["start_receipt_id"]
            )
            resume_receipt = self._verify_official_notice_receipt_on_connection(
                connection, interval["resume_receipt_id"]
            )
            if (
                start_receipt["notice_role"] != "start"
                or start_receipt["ts_code"] != symbol
                or start_receipt["effective_date"] != start_date
            ):
                raise PITReceiptError("official suspension start receipt mismatch")
            if (
                resume_receipt["notice_role"] != "resume"
                or resume_receipt["ts_code"] != symbol
                or resume_receipt["effective_date"] != resume_date
            ):
                raise PITReceiptError("official suspension resume receipt mismatch")
            receipt_refs = [start_receipt, resume_receipt]
            parser_versions.update(
                receipt["parser_version"] for receipt in receipt_refs
            )
            evidence_root = self._official_interval_root(
                ts_code=symbol,
                start_date=start_date,
                resume_date=resume_date,
                receipt_refs=receipt_refs,
            )
            if not hmac.compare_digest(
                _require_sha256(
                    interval["evidence_root_sha256"],
                    "official suspension evidence root",
                ),
                evidence_root,
            ):
                raise PITReceiptError("official suspension evidence root mismatch")
            expected_interval_id = _sha256(
                {
                    "kind": "official_suspension_interval",
                    "evidence_root_sha256": evidence_root,
                }
            )
            if not hmac.compare_digest(interval_id, expected_interval_id):
                raise PITReceiptError("official suspension interval identity mismatch")
            _aware_timestamp(interval["recorded_at"])
            ref = {
                "interval_id": interval_id,
                "ts_code": symbol,
                "start_date": start_date,
                "resume_date": resume_date,
                "start_receipt_id": start_receipt["receipt_id"],
                "resume_receipt_id": resume_receipt["receipt_id"],
                "evidence_root_sha256": evidence_root,
            }
            refs.append(ref)
            start_publication_date = str(start_receipt["published_at"])[:10]
            for session in sessions:
                if start_date <= session < resume_date:
                    if start_publication_date > session:
                        raise PITReceiptError(
                            "official suspension start notice was published after session"
                        )
                    codes_by_session[session].add(symbol)
        refs.sort(key=lambda row: (row["ts_code"], row["start_date"], row["interval_id"]))
        return {
            "count": len(refs),
            "refs": refs,
            "root": _sha256(refs),
            "codes_by_session": codes_by_session,
            "parser_versions": sorted(parser_versions),
        }

    def audit_coverage(
        self,
        *,
        start_date: str,
        end_date: str,
        calendar_exchanges: Sequence[str] = CALENDAR_SOURCE_EXCHANGES,
        required_stock_statuses: Sequence[str] = REQUIRED_STOCK_STATUSES,
        allowed_markets: Sequence[str] = SUPPORTED_A_SHARE_MARKETS,
        _connection: sqlite3.Connection = None,
    ) -> Dict[str, Any]:
        start = _iso_date(start_date, "start_date")
        end = _iso_date(end_date, "end_date")
        if end < start:
            raise PITReceiptError("end_date precedes start_date")
        maximum_loaded = 0
        connection_manager = self._connect() if _connection is None else nullcontext(_connection)
        with connection_manager as connection:
            if _connection is None:
                connection.execute("BEGIN IMMEDIATE")
            elif not connection.in_transaction:
                raise PITReceiptError(
                    "external coverage audit connection must already be in a transaction"
                )
            normalized_exchanges = tuple(
                str(exchange).strip().upper() for exchange in calendar_exchanges
            )
            if not normalized_exchanges or len(set(normalized_exchanges)) != len(
                normalized_exchanges
            ):
                raise PITReceiptError("calendar exchange scope is invalid")
            if set(normalized_exchanges) - {"SSE", "SZSE"}:
                raise PITReceiptError(
                    "BSE scope is not supported until a BSE-specific market policy is frozen"
                )
            # calendar audit 只要求 SSE(jiaoch trade_cal 数据源限制,A 股两市日历同步)。
            # universe 审计仍要求两市(SUPPORTED_A_SHARE_EXCHANGES,在别处校验)。
            normalized_markets = tuple(
                str(market).strip() for market in allowed_markets if str(market).strip()
            )
            if not normalized_markets or len(set(normalized_markets)) != len(normalized_markets):
                raise PITReceiptError("market scope is invalid")
            if normalized_markets != SUPPORTED_A_SHARE_MARKETS:
                raise PITReceiptError(
                    "market scope policy is not supported; use the frozen A-share scope"
                )
            normalized_statuses = tuple(
                str(status).strip().upper() for status in required_stock_statuses
            )
            if normalized_statuses != REQUIRED_STOCK_STATUSES:
                raise PITReceiptError(
                    "stock status policy is not supported; L/D/P/G receipts are mandatory"
                )
            active_generation = self._active_stock_generation_on_connection(connection)
            if active_generation is None:
                raise PITReceiptError("active stock_basic generation is missing")
            generation, generation_verification = active_generation
            generation_id = str(generation["generation_id"])
            generation_lineage_sha256 = self._stock_generation_lineage_sha256_on_connection(
                connection, generation_id
            )
            verification = self._verify_receipts_on_connection(connection)
            expected_calendar_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
            open_sets = []
            for exchange in normalized_exchanges:
                calendar_count = connection.execute(
                    """
                    SELECT COUNT(*) FROM trade_sessions
                    WHERE exchange = ? AND cal_date BETWEEN ? AND ?
                    """,
                    (exchange, start, end),
                ).fetchone()[0]
                if calendar_count != expected_calendar_days:
                    raise PITReceiptError(f"calendar date coverage is incomplete for {exchange}")
                open_rows = list(
                    connection.execute(
                        """
                        SELECT cal_date, pretrade_date FROM trade_sessions
                        WHERE exchange = ? AND is_open = 1 AND cal_date BETWEEN ? AND ?
                        ORDER BY cal_date
                        """,
                        (exchange, start, end),
                    )
                )
                for index, row in enumerate(open_rows):
                    previous = row["pretrade_date"]
                    if not previous or previous >= row["cal_date"]:
                        raise PITReceiptError(
                            f"trade calendar pretrade chain is invalid for {exchange}"
                        )
                    if index > 0 and previous != open_rows[index - 1]["cal_date"]:
                        raise PITReceiptError(
                            f"trade calendar pretrade chain is broken for {exchange}"
                        )
                open_sets.append({row["cal_date"] for row in open_rows})
            if any(sessions != open_sets[0] for sessions in open_sets[1:]):
                raise PITReceiptError("exchange open-session calendars disagree")
            exchange_placeholders = ",".join("?" for _ in normalized_exchanges)
            market_placeholders = ",".join("?" for _ in normalized_markets)
            ambiguous = connection.execute(
                """
                SELECT COUNT(*) FROM stock_basic_generation_rows
                WHERE generation_id = ?
                  AND list_status = 'P'
                  AND exchange IN ("""
                + exchange_placeholders
                + """)
                  AND market IN ("""
                + market_placeholders
                + """)
                  AND list_date <= ?
                  AND (delist_date IS NULL OR delist_date > ?)
                """,
                (generation_id, *normalized_exchanges, *normalized_markets, end, start),
            ).fetchone()[0]
            if ambiguous:
                raise PITReceiptError("unresolved P security lifecycle state")
            sessions = sorted(open_sets[0])
            derived_memberships: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
            exact_membership_sessions = set()
            for session in sessions:
                receipt = connection.execute(
                    "SELECT 1 FROM receipts WHERE dataset='bak_basic' AND partition_key=?",
                    (session,),
                ).fetchone()
                if receipt is not None:
                    exact_membership_sessions.add(session)
                    continue
                active_membership = self._active_membership_on_connection(connection, session)
                if active_membership is None:
                    raise PITReceiptError(f"missing complete daily receipt: {session}")
                derived_memberships[session] = active_membership
            selected_receipt_keys = set()
            selected_receipt_keys.update(
                {
                    ("trade_cal", row["receipt_partition"])
                    for row in connection.execute(
                        """
                        SELECT DISTINCT receipt_partition FROM trade_sessions
                        WHERE exchange IN ("""
                        + exchange_placeholders
                        + ") AND cal_date BETWEEN ? AND ?",
                        (*normalized_exchanges, start, end),
                    )
                }
            )
            selected_receipt_keys.update(
                ("bak_basic", session) for session in exact_membership_sessions
            )
            all_receipt_refs = {
                (row["dataset"], row["partition_key"]): row for row in verification["receipt_refs"]
            }
            missing_refs = sorted(selected_receipt_keys - set(all_receipt_refs))
            if missing_refs:
                missing_daily = [key for dataset, key in missing_refs if dataset == "bak_basic"]
                if missing_daily:
                    raise PITReceiptError(f"missing complete daily receipt: {missing_daily[0]}")
                raise PITReceiptError(
                    f"coverage receipt selection is incomplete: {missing_refs[:5]}"
                )
            official_suspension_evidence = (
                self._verify_official_suspension_evidence_on_connection(
                    connection, sessions
                )
            )
            session_count = 0
            excluded_rows = 0
            excluded_market_rows = 0
            market_reconciliations = []

            def reconcile_verified_session(
                session: str,
                _generation: Mapping[str, Any],
                rows_by_dataset: Mapping[
                    str, Sequence[Mapping[str, Any]]
                ],
            ) -> None:
                nonlocal maximum_loaded
                nonlocal session_count
                nonlocal excluded_rows
                nonlocal excluded_market_rows

                receipt = connection.execute(
                    """
                    SELECT row_count FROM receipts
                    WHERE dataset = 'bak_basic' AND partition_key = ?
                    """,
                    (session,),
                ).fetchone()
                derived = derived_memberships.get(session)
                if receipt is None and derived is None:
                    raise PITReceiptError(f"missing complete daily receipt: {session}")
                if receipt is None:
                    membership_generation, membership_verified = derived
                    if membership_generation["signal_session_eligible"]:
                        raise PITReceiptError("derived membership session is BUY eligible")
                    snapshot_rows = [
                        dict(row) for row in membership_verified.get("rows", [])
                    ]
                    expected = {
                        row["ts_code"]
                        for row in snapshot_rows
                        if _is_supported_a_share_snapshot_code(
                            row["ts_code"], row["exchange"]
                        )
                    }
                else:
                    snapshot_rows = [
                        dict(row)
                        for row in connection.execute(
                            """
                            SELECT ts_code, exchange, list_date
                            FROM daily_universe
                            WHERE trade_date = ?
                            ORDER BY ts_code
                            """,
                            (session,),
                        )
                    ]
                    expected = {
                        row["ts_code"]
                        for row in snapshot_rows
                        if row["list_date"] is not None
                        and row["list_date"] <= session
                        and _is_supported_a_share_snapshot_code(
                            row["ts_code"], row["exchange"]
                        )
                    }
                if not expected:
                    raise PITReceiptError(
                        f"expected in-scope universe is empty for open session: {session}"
                    )
                daily_total = len(snapshot_rows)
                excluded_rows += daily_total - len(expected)
                maximum_loaded = max(maximum_loaded, len(expected), daily_total)
                if receipt is not None and int(receipt["row_count"]) != daily_total:
                    raise PITReceiptError(
                        f"daily membership receipt count mismatch for {session}"
                    )

                daily_rows = {
                    str(row["ts_code"]): row for row in rows_by_dataset["daily"]
                }
                daily_codes = set(daily_rows)
                market_expected = expected & daily_codes if derived is not None else expected
                factor_codes = {
                    str(row["ts_code"])
                    for row in rows_by_dataset["adj_factor"]
                }
                limit_rows = {
                    str(row["ts_code"]): row
                    for row in rows_by_dataset["stk_limit"]
                }
                limit_codes = set(limit_rows)
                suspended_codes = {
                    str(row["ts_code"])
                    for row in rows_by_dataset["suspend_d"]
                    if row["suspend_type"] == "S"
                }
                official_suspended_codes = set(
                    official_suspension_evidence["codes_by_session"].get(
                        session, set()
                    )
                )
                official_daily_conflicts = official_suspended_codes & daily_codes
                if official_daily_conflicts:
                    raise PITReceiptError(
                        f"official suspension conflicts with daily market row for {session}: "
                        f"{sorted(official_daily_conflicts)[:5]}"
                    )
                missing_daily = market_expected - daily_codes
                provider_explained_daily = missing_daily & suspended_codes
                official_explained_daily = (
                    missing_daily - provider_explained_daily
                ) & official_suspended_codes
                unexplained_daily = (
                    missing_daily
                    - provider_explained_daily
                    - official_explained_daily
                )
                traded_expected = market_expected & daily_codes
                missing_factors = traded_expected - factor_codes
                missing_limits = traded_expected - limit_codes
                invalid_limits = set()
                for ts_code in traded_expected & limit_codes:
                    limit_row = limit_rows[ts_code]
                    up_limit = float(limit_row["up_limit"])
                    down_limit = float(limit_row["down_limit"])
                    reference_close = limit_row["pre_close"]
                    if reference_close is None:
                        reference_close = daily_rows[ts_code]["pre_close"]
                    if not _has_valid_expected_limit_interval(
                        up_limit, down_limit, reference_close
                    ):
                        invalid_limits.add(ts_code)
                bad_limits = missing_limits | invalid_limits
                if unexplained_daily or missing_factors or bad_limits:
                    raise PITReceiptError(
                        f"market evidence mismatch for {session}: "
                        f"daily={sorted(unexplained_daily)[:5]}, "
                        f"adj_factor={sorted(missing_factors)[:5]}, "
                        f"stk_limit={sorted(bad_limits)[:5]}"
                    )
                excluded_market_count = (
                    len(daily_codes - expected)
                    + len(factor_codes - expected)
                    + len(limit_codes - expected)
                    + len(suspended_codes - expected)
                )
                excluded_market_rows += excluded_market_count
                market_reconciliations.append(
                    {
                        "trade_date": session,
                        "expected_count": len(expected),
                        "expected_sha256": _sha256(sorted(expected)),
                        "provider_suspension_count": len(suspended_codes),
                        "official_suspension_count": len(official_suspended_codes),
                        "missing_daily_explained_by_provider_count": len(
                            provider_explained_daily
                        ),
                        "missing_daily_explained_by_official_count": len(
                            official_explained_daily
                        ),
                        "unexplained_missing_daily_count": len(unexplained_daily),
                        "excluded_market_row_count": excluded_market_count,
                    }
                )
                session_count += 1

            market_generation_coverage = self._market_generation_coverage_on_connection(
                connection,
                sessions,
                on_verified_session=reconcile_verified_session,
            )
            selected_refs = [all_receipt_refs[key] for key in sorted(selected_receipt_keys)]
            selected_refs.append(
                {
                    "dataset": "stock_basic_generation",
                    "partition_key": generation_id,
                    "receipt_sha256": _sha256(
                        {
                            "audit_identity_sha256": generation_verification[
                                "audit_identity_sha256"
                            ],
                            "lineage_sha256": generation_lineage_sha256,
                        }
                    ),
                }
            )
            for ref in market_generation_coverage["refs"]:
                selected_refs.append(
                    {
                        "dataset": "market_session_generation",
                        "partition_key": ref["trade_date"],
                        "receipt_sha256": _sha256(
                            {
                                "generation_id": ref["generation_id"],
                                "manifest_sha256": ref["manifest_sha256"],
                                "lineage_sha256": ref["lineage_sha256"],
                                "vintage": ref["vintage"],
                            }
                        ),
                    }
                )
            for ref in official_suspension_evidence["refs"]:
                selected_refs.append(
                    {
                        "dataset": "official_suspension_interval",
                        "partition_key": ref["interval_id"],
                        "receipt_sha256": ref["evidence_root_sha256"],
                    }
                )
            for session, (membership_generation, membership_verified) in sorted(
                derived_memberships.items()
            ):
                selected_refs.append(
                    {
                        "dataset": "membership_generation",
                        "partition_key": session,
                        "receipt_sha256": _sha256(
                            {
                                "generation_id": membership_generation["generation_id"],
                                "manifest_sha256": membership_verified["manifest_sha256"],
                                "lineage_sha256": membership_verified["lineage_sha256"],
                            }
                        ),
                    }
                )
            selected_refs.sort(key=lambda row: (row["dataset"], row["partition_key"]))
            verification = {
                "verified_receipt_count": len(selected_refs),
                "receipt_manifest_sha256": _sha256(selected_refs),
            }
        if session_count <= 0:
            raise PITReceiptError("coverage has no open sessions")
        membership_statuses = [session in derived_memberships for session in sessions]
        maximum_consecutive_quarantined = 0
        current_consecutive = 0
        for quarantined in membership_statuses:
            current_consecutive = current_consecutive + 1 if quarantined else 0
            maximum_consecutive_quarantined = max(
                maximum_consecutive_quarantined, current_consecutive
            )
        membership_generation_refs = [
            {
                "trade_date": session,
                "generation_id": generation["generation_id"],
                "manifest_sha256": verified["manifest_sha256"],
                "lineage_sha256": verified["lineage_sha256"],
            }
            for session, (generation, verified) in sorted(derived_memberships.items())
        ]
        membership_generation_root = _sha256(membership_generation_refs)
        audit = {
            "schema_version": COVERAGE_AUDIT_SCHEMA_VERSION,
            "store_schema_version": STORE_SCHEMA_VERSION,
            "parser_version": PARSER_VERSION,
            "verifier_code_sha256": _coverage_verifier_contract_sha256(
                official_suspension_evidence["parser_versions"]
            ),
            "status": "passed",
            "start_date": start,
            "end_date": end,
            "calendar_exchanges": list(normalized_exchanges),
            "allowed_markets": list(normalized_markets),
            "required_stock_statuses": list(normalized_statuses),
            "stock_generation_id": generation_id,
            "stock_generation_manifest_sha256": generation_verification["manifest_sha256"],
            "stock_generation_rows_sha256": generation_verification["rows_sha256"],
            "stock_generation_audit_identity_sha256": generation_verification[
                "audit_identity_sha256"
            ],
            "stock_generation_lineage_sha256": generation_lineage_sha256,
            "session_count": session_count,
            "exact_membership_session_count": len(exact_membership_sessions),
            "quarantined_membership_session_count": len(derived_memberships),
            "pre_anchor_session_count": sum(
                generation["source_kind"] == "pre_anchor_quarantine"
                for generation, _verified in derived_memberships.values()
            ),
            "maximum_consecutive_quarantined_sessions": maximum_consecutive_quarantined,
            "membership_generation_refs": membership_generation_refs,
            "membership_generation_root_sha256": membership_generation_root,
            "maximum_session_rows_loaded": maximum_loaded,
            "excluded_out_of_scope_daily_rows": excluded_rows,
            "excluded_out_of_scope_market_rows": excluded_market_rows,
            "market_reconciliations": market_reconciliations,
            "market_reconciliation_root_sha256": _sha256(market_reconciliations),
            "completeness_scope": "bak_basic_point_in_time_market_set_reconciliation",
            "market_generation_count": market_generation_coverage["count"],
            "market_generation_refs": market_generation_coverage["refs"],
            "market_generation_root_sha256": market_generation_coverage["root"],
            "official_suspension_interval_count": official_suspension_evidence[
                "count"
            ],
            "official_suspension_refs": official_suspension_evidence["refs"],
            "official_suspension_evidence_root_sha256": official_suspension_evidence[
                "root"
            ],
            "receipt_verification_scope": (
                "whole_store_plus_active_stock_per_session_market_and_official_suspension_fail_closed"
            ),
            "lifecycle_policy": "bak_basic_snapshot_list_date_inclusive_v2",
            **verification,
            "final_oos_eligible": False,
        }
        return {**audit, "coverage_audit_sha256": _sha256(audit)}

    @staticmethod
    def _copy_artifact_query(
        source: sqlite3.Connection,
        destination: sqlite3.Connection,
        *,
        select_sql: str,
        select_params: Sequence[Any],
        insert_sql: str,
        fields: Sequence[str],
        chunk_size: int = 1000,
    ) -> Dict[str, Any]:
        cursor = source.execute(select_sql, tuple(select_params))
        digest = hashlib.sha256()
        count = 0
        maximum_buffered = 0
        while True:
            rows = cursor.fetchmany(chunk_size)
            if not rows:
                break
            maximum_buffered = max(maximum_buffered, len(rows))
            values = []
            for row in rows:
                normalized = {field: row[field] for field in fields}
                digest.update(_canonical_json(normalized).encode("utf-8"))
                digest.update(b"\n")
                values.append(tuple(normalized[field] for field in fields))
            destination.executemany(insert_sql, values)
            count += len(values)
        return {
            "sha256": digest.hexdigest(),
            "rows": count,
            "maximum_rows_buffered": maximum_buffered,
        }

    def _copy_selected_raw(
        self,
        receipt: Mapping[str, Any],
        staging_root: Path,
        *,
        max_bytes: int = None,
    ) -> Dict[str, Any]:
        source_path = self._safe_raw_path(str(receipt["raw_path"]))
        relative = Path(str(receipt["raw_path"]))
        destination = staging_root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        source_descriptor = os.open(str(source_path), flags)
        temp_name = None
        try:
            source_stat = os.fstat(source_descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise PITReceiptError("raw receipt is not a regular file")
            expected_bytes = _require_int(receipt["raw_bytes"], "raw receipt bytes")
            if source_stat.st_size != expected_bytes:
                raise PITReceiptError("raw receipt byte count changed during export")
            safe_limit = (
                _require_int(max_bytes, "raw receipt safe limit")
                if max_bytes is not None
                else MAX_RAW_BYTES[str(receipt["dataset"])]
            )
            if source_stat.st_size > safe_limit:
                raise PITReceiptError("raw receipt exceeds safe byte limit during export")
            output_descriptor, temp_name = tempfile.mkstemp(
                prefix=f"{receipt['raw_sha256']}.", suffix=".tmp", dir=str(destination.parent)
            )
            digest = hashlib.sha256()
            copied = 0
            with (
                os.fdopen(source_descriptor, "rb") as source_handle,
                os.fdopen(output_descriptor, "wb") as output_handle,
            ):
                source_descriptor = -1
                while True:
                    chunk = source_handle.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    copied += len(chunk)
                    output_handle.write(chunk)
                output_handle.flush()
                os.fsync(output_handle.fileno())
            if copied != expected_bytes:
                raise PITReceiptError("raw receipt byte count mismatch during export")
            if not hmac.compare_digest(digest.hexdigest(), str(receipt["raw_sha256"])):
                raise PITReceiptError("raw receipt hash mismatch during export")
            if destination.exists():
                if destination.is_symlink() or _file_sha256(destination) != digest.hexdigest():
                    raise PITReceiptError("duplicate raw artifact path mismatch")
            else:
                os.replace(temp_name, destination)
                temp_name = None
            self._fsync_directory(destination.parent)
            return {
                "path": relative.as_posix(),
                "sha256": digest.hexdigest(),
                "bytes": copied,
            }
        finally:
            if source_descriptor >= 0:
                os.close(source_descriptor)
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def _artifact_descriptor(
        database_path: Path, manifest_path: Path, manifest: Mapping[str, Any], status: str
    ) -> Dict[str, Any]:
        return {
            "path": str(database_path),
            "manifest_path": str(manifest_path),
            "artifact_root_sha256": manifest["artifact_root_sha256"],
            "bundle_sha256": manifest["bundle_sha256"],
            "artifact_file_sha256": manifest["sqlite"]["sha256"],
            "coverage_audit_sha256": manifest["coverage_audit_sha256"],
            "receipt_manifest_sha256": manifest["receipt_manifest_sha256"],
            "stock_generation": manifest["stock_generation"],
            "market_generations": manifest["market_generations"],
            "official_suspensions": manifest["official_suspensions"],
            "membership_generations": manifest["membership_generations"],
            "tables": manifest["tables"],
            "maximum_rows_buffered": manifest["maximum_rows_buffered"],
            "final_oos_eligible": False,
            "temporal_role": (
                manifest["temporal_binding"].get("role")
                if manifest.get("temporal_binding", {}).get("schema_version")
                == "research-artifact-temporal-binding/v1"
                else "legacy_development_unbound"
            ),
            "temporal_contract_sha256": manifest.get("temporal_binding", {}).get("contract_sha256"),
            "status": status,
        }

    def publish_universe_artifact(
        self,
        directory: str,
        *,
        start_date: str,
        end_date: str,
        expected_coverage_audit_sha256: str,
        temporal_contract_sha256: str = None,
        temporal_role: str = None,
        permitted_operation: str = "publish",
        promotion_eligible: bool = False,
    ) -> Dict[str, Any]:
        """Publish a pruned receipt-store snapshot with complete raw lineage."""

        if (temporal_contract_sha256 is None) != (temporal_role is None):
            raise PITReceiptError("temporal binding requires both contract hash and role")
        if promotion_eligible is not False:
            raise PITReceiptError("audited artifacts are never promotion eligible")
        if permitted_operation != "publish":
            raise PITReceiptError("audited artifact operation must be publish")
        temporal_binding = None
        if temporal_contract_sha256 is not None:
            temporal_binding = {
                "schema_version": "research-artifact-temporal-binding/v1",
                "contract_sha256": _require_sha256(
                    temporal_contract_sha256, "temporal contract hash"
                ),
                "role": str(temporal_role),
                "start_date": _iso_date(start_date, "start_date"),
                "end_date": _iso_date(end_date, "end_date"),
                "permitted_operation": "publish",
                "promotion_eligible": False,
            }
            if temporal_binding["role"] not in {"development", "contaminated_diagnostic"}:
                raise PITReceiptError("temporal role cannot publish artifacts")
        expected_audit = _require_sha256(
            expected_coverage_audit_sha256, "expected coverage audit hash"
        )
        output_dir = Path(directory).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        staging_parent = Path(tempfile.mkdtemp(prefix=".audited-universe.", dir=str(output_dir)))
        staging_root = staging_parent / "payload"
        staging_root.mkdir()
        database_path = staging_root / "metadata.sqlite3"
        try:
            with self._connect() as source:
                source.execute("BEGIN IMMEDIATE")
                self._recover_orphan_promotions_on_connection(source)
                audit = self.audit_coverage(
                    start_date=start_date,
                    end_date=end_date,
                    _connection=source,
                )
                if not hmac.compare_digest(audit["coverage_audit_sha256"], expected_audit):
                    raise PITReceiptError("coverage audit hash mismatch; refusing stale export")

                source.execute(
                    """
                    CREATE TEMP TABLE artifact_selected_receipts (
                        dataset TEXT NOT NULL,
                        partition_key TEXT NOT NULL,
                        PRIMARY KEY (dataset, partition_key)
                    ) WITHOUT ROWID
                    """
                )
                generation_id = str(audit["stock_generation_id"])
                selected_keys = set()
                selected_keys.update(
                    ("trade_cal", row["receipt_partition"])
                    for row in source.execute(
                        """
                        SELECT DISTINCT receipt_partition FROM trade_sessions
                        WHERE exchange IN ('SSE', 'SZSE') AND cal_date BETWEEN ? AND ?
                        """,
                        (audit["start_date"], audit["end_date"]),
                    )
                )
                selected_keys.update(
                    ("bak_basic", row["cal_date"])
                    for row in source.execute(
                        """
                        SELECT session.cal_date
                        FROM trade_sessions AS session
                        JOIN receipts AS receipt
                          ON receipt.dataset = 'bak_basic'
                         AND receipt.partition_key = session.cal_date
                        WHERE session.exchange = 'SSE'
                          AND session.is_open = 1
                          AND session.cal_date BETWEEN ? AND ?
                        ORDER BY cal_date
                        """,
                        (audit["start_date"], audit["end_date"]),
                    )
                )
                source.executemany(
                    "INSERT INTO artifact_selected_receipts VALUES (?, ?)",
                    sorted(selected_keys),
                )
                # WHY: the source audit fail-closed every open session to a
                # published market generation. The snapshot must carry exactly
                # those generations (and their shards / rows / attempts / events
                # / raw) so audit_coverage re-binds to the same proof offline.
                source.execute(
                    """
                    CREATE TEMP TABLE artifact_selected_market_generations (
                        generation_id TEXT NOT NULL PRIMARY KEY
                    ) WITHOUT ROWID
                    """
                )
                source.executemany(
                    "INSERT INTO artifact_selected_market_generations VALUES (?)",
                    [(str(ref["generation_id"]),) for ref in audit["market_generation_refs"]],
                )
                source.execute(
                    """
                    CREATE TEMP TABLE artifact_selected_membership_generations (
                        generation_id TEXT NOT NULL PRIMARY KEY
                    ) WITHOUT ROWID
                    """
                )
                source.executemany(
                    "INSERT INTO artifact_selected_membership_generations VALUES (?)",
                    [
                        (str(ref["generation_id"]),)
                        for ref in audit["membership_generation_refs"]
                    ],
                )
                source.execute(
                    """
                    CREATE TEMP TABLE artifact_selected_attempts (
                        attempt_id TEXT NOT NULL PRIMARY KEY
                    ) WITHOUT ROWID
                    """
                )
                source.execute(
                    """
                    INSERT INTO artifact_selected_attempts
                    SELECT attempt.attempt_id
                    FROM fetch_attempts AS attempt
                    JOIN artifact_selected_receipts AS selected
                      ON selected.dataset = attempt.dataset
                     AND selected.partition_key = attempt.partition_key
                    """
                )
                source.execute(
                    """
                    INSERT OR IGNORE INTO artifact_selected_attempts
                    SELECT attempt.attempt_id
                    FROM fetch_attempts AS attempt
                    JOIN stock_basic_generation_shards AS template
                      ON template.generation_id = ?
                     AND template.logical_partition_key = attempt.partition_key
                     AND template.request_semantics_sha256 =
                         attempt.request_semantics_sha256
                    JOIN stock_basic_generations AS generation
                      ON generation.generation_id = template.generation_id
                    WHERE attempt.dataset = 'stock_basic'
                    """,
                    (generation_id,),
                )
                source.execute(
                    """
                    INSERT OR IGNORE INTO artifact_selected_attempts
                    SELECT attempt.attempt_id
                    FROM fetch_attempts AS attempt
                    JOIN market_session_generations AS generation
                      ON generation.trade_date = attempt.partition_key
                    JOIN artifact_selected_market_generations AS selected_generation
                      ON selected_generation.generation_id = generation.generation_id
                    JOIN market_session_generation_shards AS template
                      ON template.generation_id = generation.generation_id
                     AND template.dataset = attempt.dataset
                     AND template.request_semantics_sha256 =
                         attempt.request_semantics_sha256
                    """
                )
                source.execute(
                    """
                    INSERT OR IGNORE INTO artifact_selected_attempts
                    SELECT evidence.attempt_id
                    FROM membership_generation_evidence AS evidence
                    JOIN artifact_selected_membership_generations AS selected
                      ON selected.generation_id = evidence.generation_id
                    """
                )

                if temporal_binding is not None:
                    selected_attempts = source.execute(
                        """
                        SELECT request_semantics_json FROM fetch_attempts
                        WHERE attempt_id IN (SELECT attempt_id FROM artifact_selected_attempts)
                        """
                    )
                    for selected_attempt in selected_attempts:
                        semantics = json.loads(selected_attempt["request_semantics_json"])
                        if (
                            semantics.get("temporal_contract_sha256")
                            != temporal_binding["contract_sha256"]
                            or semantics.get("temporal_role") != temporal_binding["role"]
                        ):
                            raise PITReceiptError(
                                "selected attempts do not share artifact temporal binding"
                            )

                destination = sqlite3.connect(str(database_path))
                destination.row_factory = sqlite3.Row
                try:
                    destination.execute("PRAGMA journal_mode=DELETE")
                    destination.execute("PRAGMA synchronous=FULL")
                    destination.execute("PRAGMA foreign_keys=ON")
                    destination.executescript(
                        """
                        CREATE TABLE store_metadata (
                            key TEXT PRIMARY KEY,
                            value TEXT NOT NULL
                        );
                        CREATE TABLE fetch_attempts (
                            attempt_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            attempt_id TEXT NOT NULL UNIQUE,
                            dataset TEXT NOT NULL,
                            partition_key TEXT NOT NULL,
                            endpoint TEXT NOT NULL,
                            params_json TEXT NOT NULL,
                            fields_json TEXT NOT NULL,
                            request_semantics_json TEXT NOT NULL,
                            request_semantics_sha256 TEXT NOT NULL,
                            wire_request_sha256 TEXT NOT NULL,
                            attempt_no INTEGER NOT NULL,
                            started_at TEXT,
                            retrieved_at TEXT,
                            elapsed_ns INTEGER NOT NULL,
                            row_cap INTEGER NOT NULL,
                            http_status INTEGER,
                            response_headers_json TEXT NOT NULL,
                            clock_attestation_json TEXT NOT NULL,
                            body_complete INTEGER NOT NULL,
                            raw_path TEXT,
                            raw_sha256 TEXT,
                            raw_bytes INTEGER NOT NULL,
                            outcome TEXT NOT NULL CHECK (outcome = 'captured'),
                            error_kind TEXT,
                            error_message TEXT,
                            recorded_at TEXT NOT NULL
                        );
                        CREATE TABLE fetch_promotion_events (
                            event_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                            attempt_id TEXT NOT NULL UNIQUE,
                            status TEXT NOT NULL,
                            details_json TEXT NOT NULL,
                            recorded_at TEXT NOT NULL,
                            FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
                        );
                        CREATE TABLE receipts (
                            dataset TEXT NOT NULL,
                            partition_key TEXT NOT NULL,
                            endpoint TEXT NOT NULL,
                            params_json TEXT NOT NULL,
                            retrieved_at TEXT NOT NULL,
                            http_status INTEGER NOT NULL,
                            raw_path TEXT NOT NULL,
                            raw_sha256 TEXT NOT NULL,
                            raw_bytes INTEGER NOT NULL,
                            response_code INTEGER NOT NULL,
                            response_message TEXT NOT NULL,
                            row_cap INTEGER NOT NULL,
                            row_count INTEGER NOT NULL,
                            normalized_sha256 TEXT NOT NULL,
                            parser_version TEXT NOT NULL,
                            PRIMARY KEY (dataset, partition_key)
                        );
                        CREATE TABLE stock_basic_generations (
                            generation_sequence INTEGER PRIMARY KEY,
                            generation_id TEXT NOT NULL UNIQUE,
                            scope_key TEXT NOT NULL,
                            contract_sha256 TEXT NOT NULL,
                            status TEXT NOT NULL CHECK (status = 'published'),
                            started_at TEXT NOT NULL,
                            terminal_at TEXT NOT NULL,
                            terminal_reason TEXT,
                            manifest_sha256 TEXT NOT NULL,
                            expected_request_semantics_json TEXT,
                            expected_request_semantics_sha256 TEXT
                        );
                        CREATE TABLE stock_basic_generation_shards (
                            generation_id TEXT NOT NULL,
                            logical_partition_key TEXT NOT NULL,
                            attempt_id TEXT NOT NULL UNIQUE,
                            request_semantics_sha256 TEXT NOT NULL,
                            retrieved_at TEXT NOT NULL,
                            raw_sha256 TEXT NOT NULL,
                            raw_bytes INTEGER NOT NULL,
                            normalized_sha256 TEXT NOT NULL,
                            row_count INTEGER NOT NULL,
                            PRIMARY KEY (generation_id, logical_partition_key),
                            FOREIGN KEY (generation_id)
                                REFERENCES stock_basic_generations(generation_id),
                            FOREIGN KEY (attempt_id) REFERENCES fetch_attempts(attempt_id)
                        );
                        CREATE TABLE stock_basic_generation_rows (
                            generation_id TEXT NOT NULL,
                            logical_partition_key TEXT NOT NULL,
                            ts_code TEXT NOT NULL,
                            symbol TEXT NOT NULL,
                            name TEXT NOT NULL,
                            exchange TEXT NOT NULL,
                            market TEXT NOT NULL,
                            list_status TEXT NOT NULL,
                            list_date TEXT,
                            delist_date TEXT,
                            PRIMARY KEY (generation_id, logical_partition_key, ts_code),
                            FOREIGN KEY (generation_id, logical_partition_key)
                                REFERENCES stock_basic_generation_shards(
                                    generation_id, logical_partition_key
                                )
                        );
                        CREATE TABLE stock_basic_generation_head (
                            scope_key TEXT PRIMARY KEY,
                            generation_id TEXT NOT NULL,
                            manifest_sha256 TEXT NOT NULL,
                            published_at TEXT NOT NULL,
                            FOREIGN KEY (generation_id)
                                REFERENCES stock_basic_generations(generation_id)
                        );
                        CREATE TABLE security_master (
                            ts_code TEXT PRIMARY KEY,
                            symbol TEXT NOT NULL,
                            name TEXT NOT NULL,
                            exchange TEXT NOT NULL,
                            market TEXT NOT NULL,
                            list_status TEXT NOT NULL,
                            list_date TEXT,
                            delist_date TEXT,
                            generation_id TEXT NOT NULL,
                            logical_partition_key TEXT NOT NULL,
                            FOREIGN KEY (generation_id, logical_partition_key, ts_code)
                                REFERENCES stock_basic_generation_rows(
                                    generation_id, logical_partition_key, ts_code
                                )
                        );
                        CREATE TABLE trade_sessions (
                            exchange TEXT NOT NULL,
                            cal_date TEXT NOT NULL,
                            is_open INTEGER NOT NULL,
                            pretrade_date TEXT,
                            receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'trade_cal'),
                            receipt_partition TEXT NOT NULL,
                            PRIMARY KEY (exchange, cal_date),
                            FOREIGN KEY (receipt_dataset, receipt_partition)
                                REFERENCES receipts(dataset, partition_key)
                        );
                        CREATE TABLE daily_universe (
                            trade_date TEXT NOT NULL,
                            ts_code TEXT NOT NULL,
                            exchange TEXT NOT NULL,
                            name TEXT NOT NULL,
                            industry TEXT,
                            list_date TEXT,
                            receipt_dataset TEXT NOT NULL CHECK (receipt_dataset = 'bak_basic'),
                            receipt_partition TEXT NOT NULL,
                            PRIMARY KEY (trade_date, ts_code),
                            FOREIGN KEY (receipt_dataset, receipt_partition)
                                REFERENCES receipts(dataset, partition_key)
                        );
                        CREATE TABLE artifact_metadata (
                            key TEXT PRIMARY KEY,
                            value_json TEXT NOT NULL
                        );
                        """
                        + _MARKET_SESSION_GENERATION_SCHEMA_SQL
                        + _OFFICIAL_SUSPENSION_SCHEMA_SQL
                        + _MEMBERSHIP_GENERATION_SCHEMA_SQL
                        + _ETF_PROXY_GENERATION_SCHEMA_SQL
                        + """
                        CREATE INDEX idx_security_master_generation
                            ON security_master(generation_id, logical_partition_key);
                        CREATE INDEX idx_trade_sessions_receipt
                            ON trade_sessions(receipt_partition);
                        CREATE INDEX idx_daily_universe_receipt
                            ON daily_universe(receipt_partition);
                        CREATE INDEX idx_artifact_daily_code_date
                            ON daily_universe(ts_code, trade_date);
                        CREATE INDEX idx_artifact_daily_date_code
                            ON daily_universe(trade_date, ts_code);
                        CREATE INDEX idx_fetch_attempt_partition
                            ON fetch_attempts(dataset, partition_key, attempt_sequence);
                        CREATE INDEX idx_fetch_attempt_request
                            ON fetch_attempts(request_semantics_sha256, wire_request_sha256);
                        CREATE INDEX idx_stock_generation_rows_code
                            ON stock_basic_generation_rows(generation_id, ts_code);
                        """
                    )
                    destination.execute(
                        "INSERT INTO store_metadata VALUES ('schema_version', ?)",
                        (STORE_SCHEMA_VERSION,),
                    )
                    destination.execute("PRAGMA user_version = 4")

                    receipt_fields = (
                        "dataset",
                        "partition_key",
                        "endpoint",
                        "params_json",
                        "retrieved_at",
                        "http_status",
                        "raw_path",
                        "raw_sha256",
                        "raw_bytes",
                        "response_code",
                        "response_message",
                        "row_cap",
                        "row_count",
                        "normalized_sha256",
                        "parser_version",
                    )
                    receipt_digest = hashlib.sha256()
                    raw_objects: Dict[str, Dict[str, Any]] = {}
                    receipt_count = 0
                    receipt_cursor = source.execute(
                        """
                        SELECT receipt.* FROM receipts AS receipt
                        JOIN artifact_selected_receipts AS selected
                          ON selected.dataset = receipt.dataset
                         AND selected.partition_key = receipt.partition_key
                        ORDER BY receipt.dataset, receipt.partition_key
                        """
                    )
                    for stored_receipt in receipt_cursor:
                        receipt = dict(stored_receipt)
                        raw = self._copy_selected_raw(receipt, staging_root)
                        previous = raw_objects.setdefault(raw["path"], raw)
                        if previous != raw:
                            raise PITReceiptError("raw object path collision during export")
                        receipt_digest.update(
                            _canonical_json(
                                {field: receipt[field] for field in receipt_fields}
                            ).encode("utf-8")
                        )
                        receipt_digest.update(b"\n")
                        destination.execute(
                            "INSERT INTO receipts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            tuple(receipt[field] for field in receipt_fields),
                        )
                        receipt_count += 1
                    if receipt_count != len(selected_keys):
                        raise PITReceiptError("selected receipt snapshot is incomplete")

                    table_results: Dict[str, Dict[str, Any]] = {
                        "receipts": {
                            "sha256": receipt_digest.hexdigest(),
                            "rows": receipt_count,
                            "maximum_rows_buffered": 1,
                        }
                    }
                    official_receipt_fields = (
                        "receipt_id",
                        "source_profile",
                        "source_url",
                        "published_at",
                        "retrieved_at",
                        "notice_role",
                        "ts_code",
                        "announcement_number",
                        "effective_date",
                        "raw_path",
                        "raw_sha256",
                        "raw_bytes",
                        "text_sha256",
                        "normalized_sha256",
                        "parser_version",
                    )
                    official_interval_fields = (
                        "interval_id",
                        "ts_code",
                        "start_date",
                        "resume_date",
                        "start_receipt_id",
                        "resume_receipt_id",
                        "evidence_root_sha256",
                        "recorded_at",
                    )
                    official_interval_ids = [
                        str(ref["interval_id"])
                        for ref in audit["official_suspension_refs"]
                    ]
                    official_receipt_ids = sorted(
                        {
                            str(ref[key])
                            for ref in audit["official_suspension_refs"]
                            for key in ("start_receipt_id", "resume_receipt_id")
                        }
                    )
                    official_receipt_digest = hashlib.sha256()
                    official_receipt_count = 0
                    if official_receipt_ids:
                        placeholders = ",".join("?" for _ in official_receipt_ids)
                        cursor = source.execute(
                            "SELECT * FROM official_notice_receipts "
                            f"WHERE receipt_id IN ({placeholders}) ORDER BY receipt_id",
                            tuple(official_receipt_ids),
                        )
                        for stored_official_receipt in cursor:
                            official_receipt = dict(stored_official_receipt)
                            raw = self._copy_selected_raw(
                                official_receipt,
                                staging_root,
                                max_bytes=MAX_CNINFO_PDF_BYTES,
                            )
                            previous = raw_objects.setdefault(raw["path"], raw)
                            if previous != raw:
                                raise PITReceiptError(
                                    "official raw object path collision during export"
                                )
                            normalized = {
                                field: official_receipt[field]
                                for field in official_receipt_fields
                            }
                            official_receipt_digest.update(
                                _canonical_json(normalized).encode("utf-8")
                            )
                            official_receipt_digest.update(b"\n")
                            destination.execute(
                                "INSERT INTO official_notice_receipts ("
                                + ",".join(official_receipt_fields)
                                + ") VALUES ("
                                + ",".join("?" for _ in official_receipt_fields)
                                + ")",
                                tuple(
                                    official_receipt[field]
                                    for field in official_receipt_fields
                                ),
                            )
                            official_receipt_count += 1
                    if official_receipt_count != len(official_receipt_ids):
                        raise PITReceiptError(
                            "selected official suspension receipts are incomplete"
                        )
                    table_results["official_notice_receipts"] = {
                        "sha256": official_receipt_digest.hexdigest(),
                        "rows": official_receipt_count,
                        "maximum_rows_buffered": 1 if official_receipt_count else 0,
                    }
                    official_interval_digest = hashlib.sha256()
                    official_interval_count = 0
                    if official_interval_ids:
                        placeholders = ",".join("?" for _ in official_interval_ids)
                        cursor = source.execute(
                            "SELECT * FROM official_suspension_intervals "
                            f"WHERE interval_id IN ({placeholders}) "
                            "ORDER BY ts_code, start_date, resume_date, interval_id",
                            tuple(official_interval_ids),
                        )
                        for stored_interval in cursor:
                            interval = dict(stored_interval)
                            normalized = {
                                field: interval[field]
                                for field in official_interval_fields
                            }
                            official_interval_digest.update(
                                _canonical_json(normalized).encode("utf-8")
                            )
                            official_interval_digest.update(b"\n")
                            destination.execute(
                                "INSERT INTO official_suspension_intervals ("
                                + ",".join(official_interval_fields)
                                + ") VALUES ("
                                + ",".join("?" for _ in official_interval_fields)
                                + ")",
                                tuple(interval[field] for field in official_interval_fields),
                            )
                            official_interval_count += 1
                    if official_interval_count != len(official_interval_ids):
                        raise PITReceiptError(
                            "selected official suspension intervals are incomplete"
                        )
                    table_results["official_suspension_intervals"] = {
                        "sha256": official_interval_digest.hexdigest(),
                        "rows": official_interval_count,
                        "maximum_rows_buffered": 1 if official_interval_count else 0,
                    }
                    attempt_fields = (
                        "attempt_sequence",
                        "attempt_id",
                        "dataset",
                        "partition_key",
                        "endpoint",
                        "params_json",
                        "fields_json",
                        "request_semantics_json",
                        "request_semantics_sha256",
                        "wire_request_sha256",
                        "attempt_no",
                        "started_at",
                        "retrieved_at",
                        "elapsed_ns",
                        "row_cap",
                        "http_status",
                        "response_headers_json",
                        "clock_attestation_json",
                        "body_complete",
                        "raw_path",
                        "raw_sha256",
                        "raw_bytes",
                        "outcome",
                        "error_kind",
                        "error_message",
                        "recorded_at",
                    )
                    attempt_digest = hashlib.sha256()
                    attempt_count = 0
                    for stored_attempt in source.execute(
                        """
                        SELECT attempt.* FROM fetch_attempts AS attempt
                        JOIN artifact_selected_attempts AS selected
                          ON selected.attempt_id = attempt.attempt_id
                        ORDER BY attempt.attempt_sequence
                        """,
                    ):
                        attempt = dict(stored_attempt)
                        if attempt["raw_path"]:
                            raw = self._copy_selected_raw(attempt, staging_root)
                            previous = raw_objects.setdefault(raw["path"], raw)
                            if previous != raw:
                                raise PITReceiptError("raw object path collision during export")
                        attempt_digest.update(
                            _canonical_json(
                                {field: attempt[field] for field in attempt_fields}
                            ).encode("utf-8")
                        )
                        attempt_digest.update(b"\n")
                        destination.execute(
                            "INSERT INTO fetch_attempts VALUES ("
                            + ",".join("?" for _ in attempt_fields)
                            + ")",
                            tuple(attempt[field] for field in attempt_fields),
                        )
                        attempt_count += 1
                    table_results["fetch_attempts"] = {
                        "sha256": attempt_digest.hexdigest(),
                        "rows": attempt_count,
                        "maximum_rows_buffered": 1,
                    }
                    table_results["fetch_promotion_events"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT event.event_sequence, event.attempt_id, event.status,
                                   event.details_json, event.recorded_at
                            FROM fetch_promotion_events AS event
                            JOIN artifact_selected_attempts AS selected
                              ON selected.attempt_id = event.attempt_id
                            ORDER BY event.event_sequence
                        """,
                        select_params=(),
                        insert_sql="INSERT INTO fetch_promotion_events VALUES (?, ?, ?, ?, ?)",
                        fields=(
                            "event_sequence",
                            "attempt_id",
                            "status",
                            "details_json",
                            "recorded_at",
                        ),
                    )
                    table_results["stock_basic_generations"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_sequence, generation_id, scope_key,
                                   contract_sha256, status, started_at, terminal_at,
                                   terminal_reason, manifest_sha256,
                                   expected_request_semantics_json,
                                   expected_request_semantics_sha256
                            FROM stock_basic_generations
                            WHERE generation_id = ?
                            ORDER BY generation_sequence
                        """,
                        select_params=(generation_id,),
                        insert_sql=(
                            "INSERT INTO stock_basic_generations "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        fields=(
                            "generation_sequence",
                            "generation_id",
                            "scope_key",
                            "contract_sha256",
                            "status",
                            "started_at",
                            "terminal_at",
                            "terminal_reason",
                            "manifest_sha256",
                            "expected_request_semantics_json",
                            "expected_request_semantics_sha256",
                        ),
                    )
                    table_results["stock_basic_generation_shards"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_id, logical_partition_key, attempt_id,
                                   request_semantics_sha256, retrieved_at, raw_sha256,
                                   raw_bytes, normalized_sha256, row_count
                            FROM stock_basic_generation_shards
                            WHERE generation_id = ?
                            ORDER BY logical_partition_key
                        """,
                        select_params=(generation_id,),
                        insert_sql=(
                            "INSERT INTO stock_basic_generation_shards "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        fields=(
                            "generation_id",
                            "logical_partition_key",
                            "attempt_id",
                            "request_semantics_sha256",
                            "retrieved_at",
                            "raw_sha256",
                            "raw_bytes",
                            "normalized_sha256",
                            "row_count",
                        ),
                    )
                    table_results["stock_basic_generation_rows"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                                SELECT generation_id, logical_partition_key, ts_code,
                                       symbol, name, exchange, market, list_status,
                                       list_date, delist_date
                                FROM stock_basic_generation_rows
                                WHERE generation_id = ?
                                ORDER BY logical_partition_key, ts_code
                            """,
                        select_params=(generation_id,),
                        insert_sql=(
                            "INSERT INTO stock_basic_generation_rows "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        fields=(
                            "generation_id",
                            "logical_partition_key",
                            "ts_code",
                            "symbol",
                            "name",
                            "exchange",
                            "market",
                            "list_status",
                            "list_date",
                            "delist_date",
                        ),
                    )
                    table_results["stock_basic_generation_head"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                                SELECT scope_key, generation_id, manifest_sha256,
                                       published_at
                                FROM stock_basic_generation_head
                                WHERE scope_key = ? AND generation_id = ?
                            """,
                        select_params=(STOCK_GENERATION_SCOPE, generation_id),
                        insert_sql=("INSERT INTO stock_basic_generation_head VALUES (?, ?, ?, ?)"),
                        fields=(
                            "scope_key",
                            "generation_id",
                            "manifest_sha256",
                            "published_at",
                        ),
                    )
                    table_results["security_master"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT row.ts_code, row.symbol, row.name, row.exchange,
                                   row.market, row.list_status, row.list_date,
                                   row.delist_date, row.generation_id,
                                   row.logical_partition_key
                            FROM stock_basic_generation_rows AS row
                            WHERE row.generation_id = ?
                            ORDER BY row.ts_code
                        """,
                        select_params=(generation_id,),
                        insert_sql="INSERT INTO security_master VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        fields=(
                            "ts_code",
                            "symbol",
                            "name",
                            "exchange",
                            "market",
                            "list_status",
                            "list_date",
                            "delist_date",
                            "generation_id",
                            "logical_partition_key",
                        ),
                    )
                    table_results["trade_sessions"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT session.exchange, session.cal_date, session.is_open,
                                   session.pretrade_date, session.receipt_dataset,
                                   session.receipt_partition
                            FROM trade_sessions AS session
                            JOIN artifact_selected_receipts AS selected
                              ON selected.dataset = session.receipt_dataset
                             AND selected.partition_key = session.receipt_partition
                            ORDER BY session.exchange, session.cal_date
                        """,
                        select_params=(),
                        insert_sql="INSERT INTO trade_sessions VALUES (?, ?, ?, ?, ?, ?)",
                        fields=(
                            "exchange",
                            "cal_date",
                            "is_open",
                            "pretrade_date",
                            "receipt_dataset",
                            "receipt_partition",
                        ),
                    )
                    table_results["daily_universe"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT daily.trade_date, daily.ts_code, daily.exchange, daily.name,
                                   daily.industry, daily.list_date, daily.receipt_dataset,
                                   daily.receipt_partition
                            FROM daily_universe AS daily
                            JOIN artifact_selected_receipts AS selected
                              ON selected.dataset = daily.receipt_dataset
                             AND selected.partition_key = daily.receipt_partition
                            ORDER BY daily.trade_date, daily.ts_code
                        """,
                        select_params=(),
                        insert_sql="INSERT INTO daily_universe VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        fields=(
                            "trade_date",
                            "ts_code",
                            "exchange",
                            "name",
                            "industry",
                            "list_date",
                            "receipt_dataset",
                            "receipt_partition",
                        ),
                    )
                    # WHY: replay the source audit's per-session market generation
                    # proof. Copied in FK dependency order (generations, then
                    # shards whose attempts are already copied, then rows, then
                    # head) so PRAGMA foreign_key_check stays clean. Each market
                    # table is captured into table_results so the manifest
                    # table-root proof (_verify_table_roots, exact expected set)
                    # binds these bytes too — no market table can be added,
                    # dropped, or mutated without breaking the artifact root.
                    table_results["market_session_generations"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_sequence, generation_id, trade_date,
                                   contract_sha256, status, vintage, final_oos_eligible,
                                   started_at, terminal_at, terminal_reason,
                                   manifest_sha256, lineage_sha256,
                                   expected_request_semantics_json,
                                   expected_request_semantics_sha256
                            FROM market_session_generations
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_market_generations
                            )
                            ORDER BY generation_sequence
                        """,
                        select_params=(),
                        insert_sql=(
                            "INSERT INTO market_session_generations ("
                            "generation_sequence, generation_id, trade_date, "
                            "contract_sha256, status, vintage, final_oos_eligible, "
                            "started_at, terminal_at, terminal_reason, "
                            "manifest_sha256, lineage_sha256, "
                            "expected_request_semantics_json, "
                            "expected_request_semantics_sha256) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        fields=(
                            "generation_sequence",
                            "generation_id",
                            "trade_date",
                            "contract_sha256",
                            "status",
                            "vintage",
                            "final_oos_eligible",
                            "started_at",
                            "terminal_at",
                            "terminal_reason",
                            "manifest_sha256",
                            "lineage_sha256",
                            "expected_request_semantics_json",
                            "expected_request_semantics_sha256",
                        ),
                    )
                    table_results["market_session_generation_shards"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_id, dataset, attempt_id,
                                   request_semantics_sha256, retrieved_at, raw_sha256,
                                   raw_bytes, normalized_sha256, row_count
                            FROM market_session_generation_shards
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_market_generations
                            )
                            ORDER BY generation_id, dataset
                        """,
                        select_params=(),
                        insert_sql=(
                            "INSERT INTO market_session_generation_shards "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                        ),
                        fields=(
                            "generation_id",
                            "dataset",
                            "attempt_id",
                            "request_semantics_sha256",
                            "retrieved_at",
                            "raw_sha256",
                            "raw_bytes",
                            "normalized_sha256",
                            "row_count",
                        ),
                    )
                    for table_name, row_fields, order_key in (
                        (
                            "market_session_generation_rows_daily",
                            (
                                "generation_id",
                                "trade_date",
                                "ts_code",
                                "open",
                                "high",
                                "low",
                                "close",
                                "pre_close",
                                "change",
                                "pct_chg",
                                "vol",
                                "amount",
                            ),
                            "trade_date, ts_code",
                        ),
                        (
                            "market_session_generation_rows_adj_factor",
                            (
                                "generation_id",
                                "trade_date",
                                "ts_code",
                                "adj_factor",
                            ),
                            "trade_date, ts_code",
                        ),
                        (
                            "market_session_generation_rows_stk_limit",
                            (
                                "generation_id",
                                "trade_date",
                                "ts_code",
                                "pre_close",
                                "up_limit",
                                "down_limit",
                            ),
                            "trade_date, ts_code",
                        ),
                        (
                            "market_session_generation_rows_suspend_d",
                            (
                                "generation_id",
                                "trade_date",
                                "ts_code",
                                "suspend_timing",
                                "suspend_type",
                            ),
                            "trade_date, ts_code, suspend_timing, suspend_type",
                        ),
                    ):
                        # WHY: suspend_d legitimately repeats a ts_code across
                        # AM/PM sessions; its ORDER BY spans suspend_timing so the
                        # copied bytes stay deterministic and byte-identical to
                        # the lineage the source audit hashed over.
                        table_results[table_name] = self._copy_artifact_query(
                            source,
                            destination,
                            select_sql=(
                                f"SELECT {', '.join(row_fields)} "
                                f"FROM {table_name} "
                                f"WHERE generation_id IN ("
                                f"    SELECT generation_id "
                                f"    FROM artifact_selected_market_generations"
                                f") ORDER BY {order_key}"
                            ),
                            select_params=(),
                            insert_sql=(
                                f"INSERT INTO {table_name} "
                                f"VALUES ({', '.join('?' for _ in row_fields)})"
                            ),
                            fields=row_fields,
                        )
                    table_results["market_session_generation_head"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT trade_date, generation_id, manifest_sha256,
                                   published_at
                            FROM market_session_generation_head
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_market_generations
                            )
                            ORDER BY trade_date
                        """,
                        select_params=(),
                        insert_sql=(
                            "INSERT INTO market_session_generation_head VALUES (?, ?, ?, ?)"
                        ),
                        fields=(
                            "trade_date",
                            "generation_id",
                            "manifest_sha256",
                            "published_at",
                        ),
                    )
                    table_results["membership_session_generations"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_sequence, generation_id, trade_date,
                                   policy_version, status, source_kind,
                                   signal_session_eligible, temporal_role,
                                   temporal_contract_sha256, source_authority_json,
                                   source_authority_sha256, market_generation_id,
                                   market_manifest_sha256, market_lineage_sha256,
                                   anchor_trade_date, anchor_receipt_dataset,
                                   anchor_receipt_partition, anchor_receipt_raw_sha256,
                                   anchor_normalized_sha256, anchor_row_count,
                                   anchor_in_scope_row_count, started_at, terminal_at,
                                   terminal_reason, manifest_json, manifest_sha256,
                                   lineage_sha256
                            FROM membership_session_generations
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_membership_generations
                            )
                            ORDER BY generation_sequence
                        """,
                        select_params=(),
                        insert_sql=(
                            "INSERT INTO membership_session_generations VALUES ("
                            + ", ".join("?" for _ in range(27))
                            + ")"
                        ),
                        fields=(
                            "generation_sequence",
                            "generation_id",
                            "trade_date",
                            "policy_version",
                            "status",
                            "source_kind",
                            "signal_session_eligible",
                            "temporal_role",
                            "temporal_contract_sha256",
                            "source_authority_json",
                            "source_authority_sha256",
                            "market_generation_id",
                            "market_manifest_sha256",
                            "market_lineage_sha256",
                            "anchor_trade_date",
                            "anchor_receipt_dataset",
                            "anchor_receipt_partition",
                            "anchor_receipt_raw_sha256",
                            "anchor_normalized_sha256",
                            "anchor_row_count",
                            "anchor_in_scope_row_count",
                            "started_at",
                            "terminal_at",
                            "terminal_reason",
                            "manifest_json",
                            "manifest_sha256",
                            "lineage_sha256",
                        ),
                    )
                    table_results["membership_generation_rows"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT generation_id, trade_date, ts_code, exchange,
                                   name, industry, list_date, membership_present,
                                   metadata_stale_possible, unknown_metadata,
                                   observed_in_daily, signal_session_eligible,
                                   signal_eligible, source_kind,
                                   metadata_anchor_date
                            FROM membership_generation_rows
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_membership_generations
                            )
                            ORDER BY generation_id, ts_code
                        """,
                        select_params=(),
                        insert_sql=(
                            "INSERT INTO membership_generation_rows VALUES ("
                            + ", ".join("?" for _ in range(15))
                            + ")"
                        ),
                        fields=(
                            "generation_id",
                            "trade_date",
                            "ts_code",
                            "exchange",
                            "name",
                            "industry",
                            "list_date",
                            "membership_present",
                            "metadata_stale_possible",
                            "unknown_metadata",
                            "observed_in_daily",
                            "signal_session_eligible",
                            "signal_eligible",
                            "source_kind",
                            "metadata_anchor_date",
                        ),
                    )
                    table_results["membership_generation_evidence"] = (
                        self._copy_artifact_query(
                            source,
                            destination,
                            select_sql="""
                                SELECT generation_id, evidence_kind, attempt_id,
                                       trade_date, raw_sha256,
                                       request_semantics_sha256, terminal_status,
                                       classification_kind
                                FROM membership_generation_evidence
                                WHERE generation_id IN (
                                    SELECT generation_id
                                    FROM artifact_selected_membership_generations
                                )
                                ORDER BY generation_id, evidence_kind, attempt_id
                            """,
                            select_params=(),
                            insert_sql=(
                                "INSERT INTO membership_generation_evidence "
                                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                            ),
                            fields=(
                                "generation_id",
                                "evidence_kind",
                                "attempt_id",
                                "trade_date",
                                "raw_sha256",
                                "request_semantics_sha256",
                                "terminal_status",
                                "classification_kind",
                            ),
                        )
                    )
                    table_results["membership_session_head"] = self._copy_artifact_query(
                        source,
                        destination,
                        select_sql="""
                            SELECT trade_date, generation_id, manifest_sha256,
                                   lineage_sha256, published_at
                            FROM membership_session_head
                            WHERE generation_id IN (
                                SELECT generation_id
                                FROM artifact_selected_membership_generations
                            )
                            ORDER BY trade_date
                        """,
                        select_params=(),
                        insert_sql="INSERT INTO membership_session_head VALUES (?, ?, ?, ?, ?)",
                        fields=(
                            "trade_date",
                            "generation_id",
                            "manifest_sha256",
                            "lineage_sha256",
                            "published_at",
                        ),
                    )
                    calendar_range = destination.execute(
                        "SELECT MIN(cal_date), MAX(cal_date), COUNT(*) FROM trade_sessions"
                    ).fetchone()
                    daily_range = destination.execute(
                        "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_universe"
                    ).fetchone()
                    materialized_ranges = {
                        "trade_sessions": {
                            "start_date": calendar_range[0],
                            "end_date": calendar_range[1],
                            "rows": int(calendar_range[2]),
                        },
                        "daily_universe": {
                            "start_date": daily_range[0],
                            "end_date": daily_range[1],
                            "rows": int(daily_range[2]),
                        },
                    }
                    raw_rows = [raw_objects[key] for key in sorted(raw_objects)]
                    raw_tree_sha256 = _sha256(raw_rows)
                    raw_total_bytes = sum(int(row["bytes"]) for row in raw_rows)
                    controlled_keys = set()
                    for linked in destination.execute(
                        """
                        SELECT attempt.dataset, attempt.partition_key,
                               attempt.request_semantics_json
                        FROM fetch_attempts AS attempt
                        JOIN fetch_promotion_events AS event
                          ON event.attempt_id = attempt.attempt_id
                        JOIN receipts AS receipt
                          ON receipt.dataset = attempt.dataset
                         AND receipt.partition_key = attempt.partition_key
                         AND receipt.raw_sha256 = attempt.raw_sha256
                        WHERE event.status IN ('stored', 'reused')
                        """
                    ):
                        semantics = json.loads(linked["request_semantics_json"])
                        if semantics.get("schema_version") == "tushare-wire-request/v1":
                            controlled_keys.add((linked["dataset"], linked["partition_key"]))
                    controlled_request_lineage_complete = set(selected_keys).issubset(
                        controlled_keys
                    )
                    controlled_generation_partitions = set()
                    for linked in destination.execute(
                        """
                        SELECT shard.logical_partition_key,
                               attempt.request_semantics_json
                        FROM stock_basic_generation_shards AS shard
                        JOIN fetch_attempts AS attempt
                          ON attempt.attempt_id = shard.attempt_id
                        JOIN fetch_promotion_events AS event
                          ON event.attempt_id = attempt.attempt_id
                         AND event.status = 'generation_staged'
                        WHERE shard.generation_id = ?
                        """,
                        (generation_id,),
                    ):
                        semantics = json.loads(linked["request_semantics_json"])
                        if (
                            semantics.get("schema_version") == "tushare-wire-request/v1"
                            and semantics.get("dataset") == "stock_basic"
                            and semantics.get("partition_key") == linked["logical_partition_key"]
                        ):
                            controlled_generation_partitions.add(linked["logical_partition_key"])
                    controlled_generation_lineage_complete = (
                        controlled_generation_partitions == set(REQUIRED_STOCK_PARTITIONS)
                    )
                    controlled_request_lineage_complete = (
                        controlled_request_lineage_complete
                        and controlled_generation_lineage_complete
                    )
                    stock_generation = {
                        "schema_version": STOCK_GENERATION_SCHEMA_VERSION,
                        "scope_key": STOCK_GENERATION_SCOPE,
                        "generation_id": generation_id,
                        "manifest_sha256": audit["stock_generation_manifest_sha256"],
                        "rows_sha256": audit["stock_generation_rows_sha256"],
                        "audit_identity_sha256": audit["stock_generation_audit_identity_sha256"],
                        "lineage_sha256": audit["stock_generation_lineage_sha256"],
                    }
                    logical = {
                        "schema_version": UNIVERSE_ARTIFACT_SCHEMA_VERSION,
                        "artifact_kind": "audited_sqlite_receipt_snapshot",
                        "artifact_role": "development_only",
                        "temporal_binding": temporal_binding
                        or {
                            "schema_version": "legacy_development_unbound",
                            "promotion_eligible": False,
                        },
                        "producer_code_sha256": _sha256(
                            {
                                "research_pit_store": hashlib.sha256(
                                    Path(__file__).read_bytes()
                                ).hexdigest(),
                                "research_suspension_evidence": hashlib.sha256(
                                    _cninfo_parser_code_path().read_bytes()
                                ).hexdigest(),
                            }
                        ),
                        "coverage": {
                            "start_date": audit["start_date"],
                            "end_date": audit["end_date"],
                        },
                        "materialized_ranges": materialized_ranges,
                        "coverage_audit_sha256": audit["coverage_audit_sha256"],
                        "receipt_manifest_sha256": audit["receipt_manifest_sha256"],
                        "stock_generation": stock_generation,
                        "market_generations": {
                            "count": audit["market_generation_count"],
                            "root_sha256": audit["market_generation_root_sha256"],
                            "refs": audit["market_generation_refs"],
                        },
                        "official_suspensions": {
                            "count": audit["official_suspension_interval_count"],
                            "root_sha256": audit[
                                "official_suspension_evidence_root_sha256"
                            ],
                            "refs": audit["official_suspension_refs"],
                        },
                        "membership_generations": {
                            "count": audit["quarantined_membership_session_count"],
                            "root_sha256": audit["membership_generation_root_sha256"],
                            "refs": audit["membership_generation_refs"],
                            "pre_anchor_session_count": audit["pre_anchor_session_count"],
                            "maximum_consecutive_quarantined_sessions": audit[
                                "maximum_consecutive_quarantined_sessions"
                            ],
                        },
                        "tables": {
                            name: {"sha256": value["sha256"], "rows": value["rows"]}
                            for name, value in sorted(table_results.items())
                        },
                        "raw": {
                            "tree_sha256": raw_tree_sha256,
                            "file_count": len(raw_rows),
                            "total_bytes": raw_total_bytes,
                        },
                        "scope": {
                            "exchanges": list(SUPPORTED_A_SHARE_EXCHANGES),
                            "markets": list(SUPPORTED_A_SHARE_MARKETS),
                            "statuses": list(REQUIRED_STOCK_STATUSES),
                            "lifecycle_policy": audit["lifecycle_policy"],
                            "bse": "excluded_from_consumer_view",
                            "cdr": "excluded_from_consumer_view",
                        },
                        "quality": {
                            "raw_to_normalized_lineage_verified": True,
                            "daily_lifecycle_reconciliation_verified": True,
                            "official_suspension_evidence_verified": True,
                            "controlled_request_lineage_complete": (
                                controlled_request_lineage_complete
                            ),
                            "controlled_receipt_count": len(set(selected_keys) & controlled_keys),
                            "controlled_generation_lineage_complete": (
                                controlled_generation_lineage_complete
                            ),
                            "controlled_generation_shard_count": len(
                                controlled_generation_partitions
                            ),
                            "direct_transport_trust_verified": False,
                            "final_oos_eligible": False,
                        },
                        "final_oos_eligible": False,
                    }
                    artifact_root = _sha256(logical)
                    metadata = {**logical, "artifact_root_sha256": artifact_root}
                    destination.executemany(
                        "INSERT INTO artifact_metadata (key, value_json) VALUES (?, ?)",
                        [(key, _canonical_json(value)) for key, value in sorted(metadata.items())],
                    )
                    destination.commit()
                    if list(destination.execute("PRAGMA foreign_key_check")):
                        raise PITReceiptError("exported universe has broken receipt lineage")
                    if destination.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise PITReceiptError("exported universe SQLite integrity check failed")
                finally:
                    destination.close()

            fsync_file(database_path)
            sqlite_sha256 = _file_sha256(database_path)
            sqlite_bytes = database_path.stat().st_size
            manifest_payload = {
                **metadata,
                "sqlite": {
                    "path": database_path.name,
                    "sha256": sqlite_sha256,
                    "bytes": sqlite_bytes,
                },
                "maximum_rows_buffered": max(
                    value["maximum_rows_buffered"] for value in table_results.values()
                ),
            }
            bundle_sha256 = _sha256(manifest_payload)
            signed_manifest_payload = {
                **manifest_payload,
                "bundle_sha256": bundle_sha256,
            }
            manifest = {
                **signed_manifest_payload,
                "manifest_sha256": _sha256(signed_manifest_payload),
            }
            manifest_path = staging_root / "manifest.json"
            with manifest_path.open("wb") as manifest_handle:
                manifest_handle.write(
                    (
                        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                    ).encode("utf-8")
                )
                manifest_handle.flush()
                os.fsync(manifest_handle.fileno())
            for current_root, directories, _files in os.walk(staging_root, topdown=False):
                for directory_name in directories:
                    self._fsync_directory(Path(current_root) / directory_name)
                self._fsync_directory(Path(current_root))

            verified_staging_root = staging_parent / bundle_sha256
            os.rename(staging_root, verified_staging_root)
            staging_root = verified_staging_root
            self._fsync_directory(staging_parent)
            loader = (
                AuditedPointInTimeUniverse.from_file
                if temporal_binding is not None
                else AuditedPointInTimeUniverse.from_legacy_unbound_file
            )
            authority_kwargs = (
                {
                    "expected_artifact_root_sha256": artifact_root,
                    "expected_temporal_contract_sha256": temporal_binding["contract_sha256"],
                    "expected_temporal_role": temporal_binding["role"],
                }
                if temporal_binding is not None
                else {}
            )
            staged_universe = loader(
                str(staging_root / "metadata.sqlite3"),
                expected_coverage_audit_sha256=expected_audit,
                **authority_kwargs,
            )
            staged_universe.close()

            final_root = output_dir / bundle_sha256
            if final_root.exists():
                existing_database = final_root / "metadata.sqlite3"
                existing_manifest = final_root / "manifest.json"
                universe = loader(
                    str(existing_database),
                    expected_coverage_audit_sha256=expected_audit,
                    **authority_kwargs,
                )
                try:
                    existing_payload = universe.manifest
                finally:
                    universe.close()
                return self._artifact_descriptor(
                    existing_database, existing_manifest, existing_payload, "reused"
                )
            try:
                os.rename(staging_root, final_root)
            except OSError:
                if not final_root.exists():
                    raise
                existing_database = final_root / "metadata.sqlite3"
                existing_manifest = final_root / "manifest.json"
                universe = loader(
                    str(existing_database),
                    expected_coverage_audit_sha256=expected_audit,
                    **authority_kwargs,
                )
                try:
                    existing_payload = universe.manifest
                finally:
                    universe.close()
                return self._artifact_descriptor(
                    existing_database, existing_manifest, existing_payload, "reused"
                )
            self._fsync_directory(output_dir)
            return self._artifact_descriptor(
                final_root / "metadata.sqlite3",
                final_root / "manifest.json",
                manifest,
                "stored",
            )
        finally:
            if staging_parent.exists():
                shutil.rmtree(staging_parent)

    def receipt_count(self) -> int:
        with self._connect() as connection:
            return int(connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0])

    def daily_symbols(self, trade_date: str) -> set[str]:
        session = _iso_date(trade_date, "trade_date")
        with self._connect() as connection:
            return {
                row["ts_code"]
                for row in connection.execute(
                    "SELECT ts_code FROM daily_universe WHERE trade_date = ?",
                    (session,),
                )
            }

    def execute_for_test(self, sql: str, params: Sequence[Any]) -> None:
        with self._connect() as connection:
            connection.execute(sql, tuple(params))


def assert_same_temporal_authority(*universes: Any) -> None:
    """Fail closed when artifacts from different temporal authorities are composed."""

    if not universes or any(
        not getattr(universe, "external_temporal_authority_verified", False)
        for universe in universes
    ):
        raise PITReceiptError("temporal authority composition requires bound verified artifacts")
    authorities = {
        (
            getattr(universe, "temporal_role", None),
            getattr(universe, "temporal_contract_sha256", None),
        )
        for universe in universes
    }
    if len(authorities) > 1:
        raise PITReceiptError("artifacts do not share the same temporal authority")


class AuditedPointInTimeUniverse:
    """Read-only consumer for an externally anchored audited receipt snapshot."""

    def __init__(
        self,
        *,
        database_path: Path,
        manifest: Dict[str, Any],
        connection: sqlite3.Connection,
        external_temporal_authority_verified: bool,
    ) -> None:
        self.database_path = database_path
        self.manifest = manifest
        self._connection = connection
        self.external_temporal_authority_verified = external_temporal_authority_verified

    @staticmethod
    def _readonly_connection(path: Path) -> sqlite3.Connection:
        uri = f"file:{quote(str(path), safe='/')}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN")
        return connection

    @staticmethod
    def _hash_query(
        connection: sqlite3.Connection,
        sql: str,
        fields: Sequence[str],
        params: Sequence[Any] = (),
    ) -> Dict[str, Any]:
        digest = hashlib.sha256()
        count = 0
        cursor = connection.execute(sql, tuple(params))
        while True:
            rows = cursor.fetchmany(1000)
            if not rows:
                break
            for row in rows:
                digest.update(
                    _canonical_json({field: row[field] for field in fields}).encode("utf-8")
                )
                digest.update(b"\n")
                count += 1
        return {"sha256": digest.hexdigest(), "rows": count}

    @classmethod
    def _verify_table_roots(
        cls, connection: sqlite3.Connection, expected: Mapping[str, Any]
    ) -> None:
        queries = {
            "fetch_attempts": (
                """
                SELECT attempt_sequence, attempt_id, dataset, partition_key, endpoint,
                       params_json, fields_json, request_semantics_json,
                       request_semantics_sha256, wire_request_sha256, attempt_no,
                       started_at, retrieved_at, elapsed_ns, row_cap, http_status,
                       response_headers_json, clock_attestation_json, body_complete,
                       raw_path, raw_sha256, raw_bytes, outcome, error_kind,
                       error_message, recorded_at
                FROM fetch_attempts ORDER BY attempt_sequence
                """,
                (
                    "attempt_sequence",
                    "attempt_id",
                    "dataset",
                    "partition_key",
                    "endpoint",
                    "params_json",
                    "fields_json",
                    "request_semantics_json",
                    "request_semantics_sha256",
                    "wire_request_sha256",
                    "attempt_no",
                    "started_at",
                    "retrieved_at",
                    "elapsed_ns",
                    "row_cap",
                    "http_status",
                    "response_headers_json",
                    "clock_attestation_json",
                    "body_complete",
                    "raw_path",
                    "raw_sha256",
                    "raw_bytes",
                    "outcome",
                    "error_kind",
                    "error_message",
                    "recorded_at",
                ),
            ),
            "fetch_promotion_events": (
                """
                SELECT event_sequence, attempt_id, status, details_json, recorded_at
                FROM fetch_promotion_events ORDER BY event_sequence
                """,
                (
                    "event_sequence",
                    "attempt_id",
                    "status",
                    "details_json",
                    "recorded_at",
                ),
            ),
            "receipts": (
                """
                SELECT dataset, partition_key, endpoint, params_json, retrieved_at,
                       http_status, raw_path, raw_sha256, raw_bytes, response_code,
                       response_message, row_cap, row_count, normalized_sha256,
                       parser_version
                FROM receipts ORDER BY dataset, partition_key
                """,
                (
                    "dataset",
                    "partition_key",
                    "endpoint",
                    "params_json",
                    "retrieved_at",
                    "http_status",
                    "raw_path",
                    "raw_sha256",
                    "raw_bytes",
                    "response_code",
                    "response_message",
                    "row_cap",
                    "row_count",
                    "normalized_sha256",
                    "parser_version",
                ),
            ),
            "official_notice_receipts": (
                """
                SELECT receipt_id, source_profile, source_url, published_at,
                       retrieved_at, notice_role, ts_code, announcement_number,
                       effective_date, raw_path, raw_sha256, raw_bytes,
                       text_sha256, normalized_sha256, parser_version
                FROM official_notice_receipts ORDER BY receipt_id
                """,
                (
                    "receipt_id",
                    "source_profile",
                    "source_url",
                    "published_at",
                    "retrieved_at",
                    "notice_role",
                    "ts_code",
                    "announcement_number",
                    "effective_date",
                    "raw_path",
                    "raw_sha256",
                    "raw_bytes",
                    "text_sha256",
                    "normalized_sha256",
                    "parser_version",
                ),
            ),
            "official_suspension_intervals": (
                """
                SELECT interval_id, ts_code, start_date, resume_date,
                       start_receipt_id, resume_receipt_id,
                       evidence_root_sha256, recorded_at
                FROM official_suspension_intervals
                ORDER BY ts_code, start_date, resume_date, interval_id
                """,
                (
                    "interval_id",
                    "ts_code",
                    "start_date",
                    "resume_date",
                    "start_receipt_id",
                    "resume_receipt_id",
                    "evidence_root_sha256",
                    "recorded_at",
                ),
            ),
            "stock_basic_generations": (
                """
                SELECT generation_sequence, generation_id, scope_key,
                       contract_sha256, status, started_at, terminal_at,
                       terminal_reason, manifest_sha256,
                       expected_request_semantics_json,
                       expected_request_semantics_sha256
                FROM stock_basic_generations ORDER BY generation_sequence
                """,
                (
                    "generation_sequence",
                    "generation_id",
                    "scope_key",
                    "contract_sha256",
                    "status",
                    "started_at",
                    "terminal_at",
                    "terminal_reason",
                    "manifest_sha256",
                    "expected_request_semantics_json",
                    "expected_request_semantics_sha256",
                ),
            ),
            "stock_basic_generation_shards": (
                """
                SELECT generation_id, logical_partition_key, attempt_id,
                       request_semantics_sha256, retrieved_at, raw_sha256,
                       raw_bytes, normalized_sha256, row_count
                FROM stock_basic_generation_shards
                ORDER BY logical_partition_key
                """,
                (
                    "generation_id",
                    "logical_partition_key",
                    "attempt_id",
                    "request_semantics_sha256",
                    "retrieved_at",
                    "raw_sha256",
                    "raw_bytes",
                    "normalized_sha256",
                    "row_count",
                ),
            ),
            "stock_basic_generation_rows": (
                """
                SELECT generation_id, logical_partition_key, ts_code, symbol,
                       name, exchange, market, list_status, list_date, delist_date
                FROM stock_basic_generation_rows
                ORDER BY logical_partition_key, ts_code
                """,
                (
                    "generation_id",
                    "logical_partition_key",
                    "ts_code",
                    "symbol",
                    "name",
                    "exchange",
                    "market",
                    "list_status",
                    "list_date",
                    "delist_date",
                ),
            ),
            "stock_basic_generation_head": (
                """
                SELECT scope_key, generation_id, manifest_sha256, published_at
                FROM stock_basic_generation_head ORDER BY scope_key
                """,
                (
                    "scope_key",
                    "generation_id",
                    "manifest_sha256",
                    "published_at",
                ),
            ),
            "security_master": (
                """
                SELECT ts_code, symbol, name, exchange, market, list_status,
                       list_date, delist_date, generation_id,
                       logical_partition_key
                FROM security_master ORDER BY ts_code
                """,
                (
                    "ts_code",
                    "symbol",
                    "name",
                    "exchange",
                    "market",
                    "list_status",
                    "list_date",
                    "delist_date",
                    "generation_id",
                    "logical_partition_key",
                ),
            ),
            "trade_sessions": (
                """
                SELECT exchange, cal_date, is_open, pretrade_date,
                       receipt_dataset, receipt_partition
                FROM trade_sessions ORDER BY exchange, cal_date
                """,
                (
                    "exchange",
                    "cal_date",
                    "is_open",
                    "pretrade_date",
                    "receipt_dataset",
                    "receipt_partition",
                ),
            ),
            "daily_universe": (
                """
                SELECT trade_date, ts_code, exchange, name, industry, list_date,
                       receipt_dataset, receipt_partition
                FROM daily_universe ORDER BY trade_date, ts_code
                """,
                (
                    "trade_date",
                    "ts_code",
                    "exchange",
                    "name",
                    "industry",
                    "list_date",
                    "receipt_dataset",
                    "receipt_partition",
                ),
            ),
            "market_session_generations": (
                """
                SELECT generation_sequence, generation_id, trade_date,
                       contract_sha256, status, vintage, final_oos_eligible,
                       started_at, terminal_at, terminal_reason, manifest_sha256,
                       lineage_sha256, expected_request_semantics_json,
                       expected_request_semantics_sha256
                FROM market_session_generations ORDER BY generation_sequence
                """,
                (
                    "generation_sequence",
                    "generation_id",
                    "trade_date",
                    "contract_sha256",
                    "status",
                    "vintage",
                    "final_oos_eligible",
                    "started_at",
                    "terminal_at",
                    "terminal_reason",
                    "manifest_sha256",
                    "lineage_sha256",
                    "expected_request_semantics_json",
                    "expected_request_semantics_sha256",
                ),
            ),
            "market_session_generation_shards": (
                """
                SELECT generation_id, dataset, attempt_id,
                       request_semantics_sha256, retrieved_at, raw_sha256,
                       raw_bytes, normalized_sha256, row_count
                FROM market_session_generation_shards
                ORDER BY generation_id, dataset
                """,
                (
                    "generation_id",
                    "dataset",
                    "attempt_id",
                    "request_semantics_sha256",
                    "retrieved_at",
                    "raw_sha256",
                    "raw_bytes",
                    "normalized_sha256",
                    "row_count",
                ),
            ),
            "market_session_generation_rows_daily": (
                """
                SELECT generation_id, trade_date, ts_code, open, high, low,
                       close, pre_close, change, pct_chg, vol, amount
                FROM market_session_generation_rows_daily
                ORDER BY trade_date, ts_code
                """,
                (
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "open",
                    "high",
                    "low",
                    "close",
                    "pre_close",
                    "change",
                    "pct_chg",
                    "vol",
                    "amount",
                ),
            ),
            "market_session_generation_rows_adj_factor": (
                """
                SELECT generation_id, trade_date, ts_code, adj_factor
                FROM market_session_generation_rows_adj_factor
                ORDER BY trade_date, ts_code
                """,
                (
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "adj_factor",
                ),
            ),
            "market_session_generation_rows_stk_limit": (
                """
                SELECT generation_id, trade_date, ts_code, pre_close, up_limit,
                       down_limit
                FROM market_session_generation_rows_stk_limit
                ORDER BY trade_date, ts_code
                """,
                (
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "pre_close",
                    "up_limit",
                    "down_limit",
                ),
            ),
            "market_session_generation_rows_suspend_d": (
                """
                SELECT generation_id, trade_date, ts_code, suspend_timing,
                       suspend_type
                FROM market_session_generation_rows_suspend_d
                ORDER BY trade_date, ts_code, suspend_timing, suspend_type
                """,
                (
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "suspend_timing",
                    "suspend_type",
                ),
            ),
            "market_session_generation_head": (
                """
                SELECT trade_date, generation_id, manifest_sha256, published_at
                FROM market_session_generation_head ORDER BY trade_date
                """,
                (
                    "trade_date",
                    "generation_id",
                    "manifest_sha256",
                    "published_at",
                ),
            ),
            "membership_session_generations": (
                """
                SELECT generation_sequence, generation_id, trade_date,
                       policy_version, status, source_kind,
                       signal_session_eligible, temporal_role,
                       temporal_contract_sha256, source_authority_json,
                       source_authority_sha256, market_generation_id,
                       market_manifest_sha256, market_lineage_sha256,
                       anchor_trade_date, anchor_receipt_dataset,
                       anchor_receipt_partition, anchor_receipt_raw_sha256,
                       anchor_normalized_sha256, anchor_row_count,
                       anchor_in_scope_row_count, started_at, terminal_at,
                       terminal_reason, manifest_json, manifest_sha256,
                       lineage_sha256
                FROM membership_session_generations
                ORDER BY generation_sequence
                """,
                (
                    "generation_sequence",
                    "generation_id",
                    "trade_date",
                    "policy_version",
                    "status",
                    "source_kind",
                    "signal_session_eligible",
                    "temporal_role",
                    "temporal_contract_sha256",
                    "source_authority_json",
                    "source_authority_sha256",
                    "market_generation_id",
                    "market_manifest_sha256",
                    "market_lineage_sha256",
                    "anchor_trade_date",
                    "anchor_receipt_dataset",
                    "anchor_receipt_partition",
                    "anchor_receipt_raw_sha256",
                    "anchor_normalized_sha256",
                    "anchor_row_count",
                    "anchor_in_scope_row_count",
                    "started_at",
                    "terminal_at",
                    "terminal_reason",
                    "manifest_json",
                    "manifest_sha256",
                    "lineage_sha256",
                ),
            ),
            "membership_generation_rows": (
                """
                SELECT generation_id, trade_date, ts_code, exchange, name,
                       industry, list_date, membership_present,
                       metadata_stale_possible, unknown_metadata,
                       observed_in_daily, signal_session_eligible,
                       signal_eligible, source_kind, metadata_anchor_date
                FROM membership_generation_rows
                ORDER BY generation_id, ts_code
                """,
                (
                    "generation_id",
                    "trade_date",
                    "ts_code",
                    "exchange",
                    "name",
                    "industry",
                    "list_date",
                    "membership_present",
                    "metadata_stale_possible",
                    "unknown_metadata",
                    "observed_in_daily",
                    "signal_session_eligible",
                    "signal_eligible",
                    "source_kind",
                    "metadata_anchor_date",
                ),
            ),
            "membership_generation_evidence": (
                """
                SELECT generation_id, evidence_kind, attempt_id, trade_date,
                       raw_sha256, request_semantics_sha256, terminal_status,
                       classification_kind
                FROM membership_generation_evidence
                ORDER BY generation_id, evidence_kind, attempt_id
                """,
                (
                    "generation_id",
                    "evidence_kind",
                    "attempt_id",
                    "trade_date",
                    "raw_sha256",
                    "request_semantics_sha256",
                    "terminal_status",
                    "classification_kind",
                ),
            ),
            "membership_session_head": (
                """
                SELECT trade_date, generation_id, manifest_sha256,
                       lineage_sha256, published_at
                FROM membership_session_head ORDER BY trade_date
                """,
                (
                    "trade_date",
                    "generation_id",
                    "manifest_sha256",
                    "lineage_sha256",
                    "published_at",
                ),
            ),
        }
        if set(expected) != set(queries):
            raise PITReceiptError("audited artifact table manifest is incomplete")
        for table_name, (sql, fields) in queries.items():
            actual = cls._hash_query(connection, sql, fields)
            if actual != expected[table_name]:
                raise PITReceiptError(f"audited artifact table hash mismatch: {table_name}")

    @staticmethod
    def _verify_raw_tree(
        bundle_root: Path,
        connection: sqlite3.Connection,
        expected: Mapping[str, Any],
    ) -> None:
        rows_by_path: Dict[str, Dict[str, Any]] = {}
        for row in connection.execute(
            """
            SELECT raw_path, raw_sha256, raw_bytes FROM receipts
            UNION ALL
            SELECT raw_path, raw_sha256, raw_bytes FROM official_notice_receipts
            UNION ALL
            SELECT raw_path, raw_sha256, raw_bytes FROM fetch_attempts
            WHERE raw_path IS NOT NULL
            ORDER BY raw_path
            """
        ):
            raw = {
                "path": row["raw_path"],
                "sha256": row["raw_sha256"],
                "bytes": _require_int(row["raw_bytes"], "artifact raw bytes"),
            }
            previous = rows_by_path.setdefault(raw["path"], raw)
            if previous != raw:
                raise PITReceiptError("audited artifact raw descriptor collision")
        rows = [rows_by_path[key] for key in sorted(rows_by_path)]
        actual = {
            "tree_sha256": _sha256(rows),
            "file_count": len(rows),
            "total_bytes": sum(row["bytes"] for row in rows),
        }
        if actual != expected:
            raise PITReceiptError("audited artifact raw tree manifest mismatch")
        for row in rows:
            relative = Path(row["path"])
            if relative.is_absolute() or ".." in relative.parts:
                raise PITReceiptError("audited artifact raw path is unsafe")
            path = bundle_root / relative
            if path.is_symlink() or not path.is_file():
                raise PITReceiptError("audited artifact raw file is missing or unsafe")
            if path.stat().st_size != row["bytes"] or not hmac.compare_digest(
                _file_sha256(path), row["sha256"]
            ):
                raise PITReceiptError("audited artifact raw receipt file hash mismatch")
        expected_paths = {row["path"] for row in rows}
        raw_root = bundle_root / "raw"
        actual_paths = set()
        if raw_root.exists():
            for path in raw_root.rglob("*"):
                if path.is_symlink():
                    raise PITReceiptError("audited artifact raw tree contains a symlink")
                if path.is_file():
                    actual_paths.add(path.relative_to(bundle_root).as_posix())
        if actual_paths != expected_paths:
            raise PITReceiptError("audited artifact raw file set mismatch")

    @staticmethod
    def _verify_materialized_ranges(
        connection: sqlite3.Connection, expected: Mapping[str, Any]
    ) -> None:
        queries = {
            "trade_sessions": ("SELECT MIN(cal_date), MAX(cal_date), COUNT(*) FROM trade_sessions"),
            "daily_universe": (
                "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_universe"
            ),
        }
        if set(expected) != set(queries):
            raise PITReceiptError("audited artifact materialized ranges are incomplete")
        for table_name, sql in queries.items():
            row = connection.execute(sql).fetchone()
            actual = {
                "start_date": row[0],
                "end_date": row[1],
                "rows": int(row[2]),
            }
            if actual != expected[table_name]:
                raise PITReceiptError(f"audited artifact materialized range mismatch: {table_name}")

    @staticmethod
    def _verify_market_generations_structure(expected: Mapping[str, Any]) -> None:
        """Structurally re-bind the market generation proof without a database.

        WHY: the manifest's ``market_generations`` object is the explicit binding
        between the frozen snapshot and the per-session market generations it
        claims. Verifying it from the manifest alone (before touching SQLite)
        means a forged / rolled-back / extra market generation whose root does
        not match its refs, whose dates are not strictly increasing, or whose
        hashes / vintage are out of policy fails closed even if every outer
        signature was re-computed to match the forgery.
        """

        if set(expected) != {"count", "root_sha256", "refs"}:
            raise PITReceiptError("audited artifact market generations shape is invalid")
        refs = expected["refs"]
        if not isinstance(refs, list) or not refs:
            raise PITReceiptError("audited artifact market generations refs are empty")
        if int(expected["count"]) != len(refs):
            raise PITReceiptError("audited artifact market generations count mismatch")
        root_sha256 = _require_sha256(expected["root_sha256"], "market generations root hash")
        if not hmac.compare_digest(_sha256(refs), root_sha256):
            raise PITReceiptError("audited artifact market generations root mismatch")
        required_ref_keys = {
            "trade_date",
            "generation_id",
            "manifest_sha256",
            "lineage_sha256",
            "vintage",
        }
        previous_date: str | None = None
        seen_dates: set[str] = set()
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != required_ref_keys:
                raise PITReceiptError("audited artifact market generation ref shape is invalid")
            trade_date = _iso_date(ref["trade_date"], "trade_date")
            if trade_date in seen_dates or (
                previous_date is not None and trade_date <= previous_date
            ):
                raise PITReceiptError(
                    "audited artifact market generation dates are not strictly increasing"
                )
            seen_dates.add(trade_date)
            previous_date = trade_date
            if not str(ref["generation_id"]):
                raise PITReceiptError("audited artifact market generation id is missing")
            _require_sha256(ref["manifest_sha256"], "market generation manifest hash")
            _require_sha256(ref["lineage_sha256"], "market generation lineage hash")
            if ref["vintage"] not in MARKET_SESSION_VINTAGES:
                raise PITReceiptError("audited artifact market generation vintage is invalid")

    @staticmethod
    def _verify_official_suspensions_structure(expected: Mapping[str, Any]) -> None:
        if set(expected) != {"count", "root_sha256", "refs"}:
            raise PITReceiptError("audited artifact official suspensions shape is invalid")
        refs = expected["refs"]
        if not isinstance(refs, list):
            raise PITReceiptError("audited artifact official suspension refs are invalid")
        if _require_int(expected["count"], "official suspension count") != len(refs):
            raise PITReceiptError("audited artifact official suspension count mismatch")
        root = _require_sha256(
            expected["root_sha256"], "official suspension evidence root"
        )
        if not hmac.compare_digest(_sha256(refs), root):
            raise PITReceiptError("audited artifact official suspension root mismatch")
        required_keys = {
            "interval_id",
            "ts_code",
            "start_date",
            "resume_date",
            "start_receipt_id",
            "resume_receipt_id",
            "evidence_root_sha256",
        }
        previous = None
        for ref in refs:
            if not isinstance(ref, dict) or set(ref) != required_keys:
                raise PITReceiptError(
                    "audited artifact official suspension ref shape is invalid"
                )
            symbol = _market_ts_code(ref["ts_code"], "official suspension")
            start_date = _iso_date(ref["start_date"], "official suspension start date")
            resume_date = _iso_date(ref["resume_date"], "official suspension resume date")
            if resume_date <= start_date:
                raise PITReceiptError("audited artifact official suspension interval is invalid")
            current = (symbol, start_date, str(ref["interval_id"]))
            if previous is not None and current <= previous:
                raise PITReceiptError(
                    "audited artifact official suspension refs are not strictly ordered"
                )
            previous = current
            _require_sha256(ref["interval_id"], "official suspension interval identity")
            _require_sha256(ref["start_receipt_id"], "official suspension start receipt")
            _require_sha256(ref["resume_receipt_id"], "official suspension resume receipt")
            _require_sha256(
                ref["evidence_root_sha256"], "official suspension interval root"
            )

    @staticmethod
    def _verify_market_generations_coverage(
        connection: sqlite3.Connection,
        refs: Sequence[Mapping[str, Any]],
        *,
        start_date: str,
        end_date: str,
    ) -> None:
        """Bind the market generation refs to the snapshot's open-day coverage.

        WHY: every covered open session must be backed by exactly one bound
        market generation ref, and vice versa. Scoped to the coverage window so
        calendar padding outside [start_date, end_date] (wide receipt snapshots
        that deliberately carry extra sessions for receipt lineage) does not
        masquerade as a hidden covered session.
        """

        open_days = {
            row["cal_date"]
            for row in connection.execute(
                """
                SELECT DISTINCT cal_date FROM trade_sessions
                WHERE is_open = 1 AND cal_date BETWEEN ? AND ?
                """,
                (start_date, end_date),
            )
        }
        ref_days = {str(ref["trade_date"]) for ref in refs}
        if open_days != ref_days:
            raise PITReceiptError("audited artifact market generations coverage mismatch")

    @classmethod
    def _verify_stock_generation_materialization(
        cls, connection: sqlite3.Connection, expected: Mapping[str, Any]
    ) -> None:
        required_keys = {
            "schema_version",
            "scope_key",
            "generation_id",
            "manifest_sha256",
            "rows_sha256",
            "audit_identity_sha256",
            "lineage_sha256",
        }
        if set(expected) != required_keys:
            raise PITReceiptError("audited stock generation manifest is invalid")
        if expected.get("schema_version") != STOCK_GENERATION_SCHEMA_VERSION:
            raise PITReceiptError("audited stock generation schema is invalid")
        if expected.get("scope_key") != STOCK_GENERATION_SCOPE:
            raise PITReceiptError("audited stock generation scope is invalid")
        generation_id = str(expected.get("generation_id") or "")
        manifest_sha256 = _require_sha256(
            expected.get("manifest_sha256"), "stock generation manifest hash"
        )
        rows_sha256 = _require_sha256(expected.get("rows_sha256"), "stock generation rows hash")
        audit_identity_sha256 = _require_sha256(
            expected.get("audit_identity_sha256"),
            "stock generation audit identity hash",
        )
        _require_sha256(expected.get("lineage_sha256"), "stock generation lineage hash")
        head = connection.execute(
            """
            SELECT generation_id, manifest_sha256
            FROM stock_basic_generation_head WHERE scope_key = ?
            """,
            (STOCK_GENERATION_SCOPE,),
        ).fetchone()
        generation = connection.execute(
            """
            SELECT status, manifest_sha256 FROM stock_basic_generations
            WHERE generation_id = ? AND scope_key = ?
            """,
            (generation_id, STOCK_GENERATION_SCOPE),
        ).fetchone()
        if (
            head is None
            or generation is None
            or generation["status"] != "published"
            or head["generation_id"] != generation_id
            or head["manifest_sha256"] != manifest_sha256
            or generation["manifest_sha256"] != manifest_sha256
        ):
            raise PITReceiptError("audited stock generation head mismatch")
        generation_rows = (
            dict(row)
            for row in connection.execute(
                """
                SELECT logical_partition_key, ts_code, symbol, name, exchange,
                       market, list_status, list_date, delist_date
                FROM stock_basic_generation_rows
                WHERE generation_id = ?
                ORDER BY logical_partition_key, ts_code
                """,
                (generation_id,),
            )
        )
        actual_rows_sha256, _row_count = _stream_rows_sha256(generation_rows)
        if not hmac.compare_digest(actual_rows_sha256, rows_sha256):
            raise PITReceiptError("audited stock generation rows hash mismatch")
        actual_audit_identity = _sha256(
            {
                "schema_version": "stock-basic-generation-audit/v1",
                "generation_id": generation_id,
                "manifest_sha256": manifest_sha256,
                "rows_sha256": rows_sha256,
            }
        )
        if not hmac.compare_digest(actual_audit_identity, audit_identity_sha256):
            raise PITReceiptError("audited stock generation identity mismatch")
        fields = (
            "ts_code",
            "symbol",
            "name",
            "exchange",
            "market",
            "list_status",
            "list_date",
            "delist_date",
            "generation_id",
            "logical_partition_key",
        )
        materialized = cls._hash_query(
            connection,
            """
            SELECT ts_code, symbol, name, exchange, market, list_status,
                   list_date, delist_date, generation_id,
                   logical_partition_key
            FROM security_master ORDER BY ts_code
            """,
            fields,
        )
        projected = cls._hash_query(
            connection,
            """
            SELECT ts_code, symbol, name, exchange, market, list_status,
                   list_date, delist_date, generation_id,
                   logical_partition_key
            FROM stock_basic_generation_rows
            WHERE generation_id = ? ORDER BY ts_code
            """,
            fields,
            (generation_id,),
        )
        if materialized != projected:
            raise PITReceiptError("audited security master materialization mismatch")

    @staticmethod
    def _verify_controlled_request_lineage(
        connection: sqlite3.Connection, quality: Mapping[str, Any]
    ) -> None:
        attempt_count = int(connection.execute("SELECT COUNT(*) FROM fetch_attempts").fetchone()[0])
        event_count = int(
            connection.execute("SELECT COUNT(*) FROM fetch_promotion_events").fetchone()[0]
        )
        missing_terminal = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fetch_attempts AS attempt
                LEFT JOIN fetch_promotion_events AS event
                  ON event.attempt_id = attempt.attempt_id
                WHERE event.attempt_id IS NULL
                """
            ).fetchone()[0]
        )
        orphan_events = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fetch_promotion_events AS event
                LEFT JOIN fetch_attempts AS attempt
                  ON attempt.attempt_id = event.attempt_id
                WHERE attempt.attempt_id IS NULL
                """
            ).fetchone()[0]
        )
        if attempt_count != event_count or missing_terminal or orphan_events:
            raise PITReceiptError("attempt terminal event cardinality mismatch")
        invalid_raw_refs = int(
            connection.execute(
                """
                SELECT COUNT(*) FROM fetch_attempts
                WHERE (raw_path IS NULL AND
                       (raw_sha256 IS NOT NULL OR raw_bytes != 0))
                   OR (raw_path IS NOT NULL AND
                       (raw_sha256 IS NULL OR length(raw_sha256) != 64 OR raw_bytes <= 0))
                """
            ).fetchone()[0]
        )
        if invalid_raw_refs:
            raise PITReceiptError("attempt raw CAS reference is invalid")
        for row in connection.execute(
            "SELECT request_semantics_json, request_semantics_sha256 FROM fetch_attempts"
        ):
            try:
                semantics = json.loads(row["request_semantics_json"])
            except json.JSONDecodeError as exc:
                raise PITReceiptError("attempt request semantics are invalid") from exc
            if _sha256(semantics) != row["request_semantics_sha256"]:
                raise PITReceiptError("attempt request semantics hash mismatch")
        receipt_keys = {
            (row["dataset"], row["partition_key"])
            for row in connection.execute("SELECT dataset, partition_key FROM receipts")
        }
        controlled_keys = set()
        for row in connection.execute(
            """
            SELECT attempt.dataset, attempt.partition_key,
                   attempt.request_semantics_json
            FROM fetch_attempts AS attempt
            JOIN fetch_promotion_events AS event
              ON event.attempt_id = attempt.attempt_id
            JOIN receipts AS receipt
              ON receipt.dataset = attempt.dataset
             AND receipt.partition_key = attempt.partition_key
             AND receipt.raw_sha256 = attempt.raw_sha256
            WHERE event.status IN ('stored', 'reused')
            """
        ):
            semantics = json.loads(row["request_semantics_json"])
            if semantics.get("schema_version") == "tushare-wire-request/v1":
                controlled_keys.add((row["dataset"], row["partition_key"]))
        controlled_count = len(receipt_keys & controlled_keys)
        controlled_generation_partitions = set()
        for row in connection.execute(
            """
            SELECT shard.logical_partition_key, attempt.request_semantics_json
            FROM stock_basic_generation_shards AS shard
            JOIN stock_basic_generation_head AS head
              ON head.generation_id = shard.generation_id
            JOIN fetch_attempts AS attempt
              ON attempt.attempt_id = shard.attempt_id
            JOIN fetch_promotion_events AS event
              ON event.attempt_id = attempt.attempt_id
             AND event.status = 'generation_staged'
            WHERE head.scope_key = ?
            """,
            (STOCK_GENERATION_SCOPE,),
        ):
            semantics = json.loads(row["request_semantics_json"])
            if (
                semantics.get("schema_version") == "tushare-wire-request/v1"
                and semantics.get("dataset") == "stock_basic"
                and semantics.get("partition_key") == row["logical_partition_key"]
            ):
                controlled_generation_partitions.add(row["logical_partition_key"])
        generation_complete = controlled_generation_partitions == set(REQUIRED_STOCK_PARTITIONS)
        complete = receipt_keys.issubset(controlled_keys) and generation_complete
        if quality.get("controlled_request_lineage_complete") is not complete:
            raise PITReceiptError("controlled request lineage quality mismatch")
        if int(quality.get("controlled_receipt_count", -1)) != controlled_count:
            raise PITReceiptError("controlled receipt count quality mismatch")
        if quality.get("controlled_generation_lineage_complete") is not generation_complete:
            raise PITReceiptError("controlled stock generation quality mismatch")
        if int(quality.get("controlled_generation_shard_count", -1)) != len(
            controlled_generation_partitions
        ):
            raise PITReceiptError("controlled stock generation shard count mismatch")

    @classmethod
    def _from_file_impl(
        cls,
        path: str,
        *,
        expected_coverage_audit_sha256: str,
        expected_artifact_root_sha256: str = None,
        expected_temporal_contract_sha256: str = None,
        expected_temporal_role: str = None,
        allow_legacy_unbound: bool = False,
    ) -> "AuditedPointInTimeUniverse":
        expected_audit = _require_sha256(
            expected_coverage_audit_sha256, "expected coverage audit hash"
        )
        provided_path = Path(path)
        if provided_path.is_symlink():
            raise PITReceiptError("audited SQLite artifact is missing or unsafe")
        database_path = provided_path.resolve()
        if database_path.suffix.lower() != ".sqlite3":
            raise PITReceiptError("audited universe requires a SQLite artifact")
        if database_path.is_symlink() or not database_path.is_file():
            raise PITReceiptError("audited SQLite artifact is missing or unsafe")
        bundle_root = database_path.parent
        manifest_path = bundle_root / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise PITReceiptError("audited SQLite artifact manifest is missing or unsafe")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PITReceiptError("audited SQLite artifact manifest is invalid") from exc
        if not isinstance(manifest, dict):
            raise PITReceiptError("audited SQLite artifact manifest is invalid")
        expected_manifest_keys = {
            "schema_version",
            "artifact_kind",
            "artifact_role",
            "temporal_binding",
            "producer_code_sha256",
            "coverage",
            "materialized_ranges",
            "coverage_audit_sha256",
            "receipt_manifest_sha256",
            "stock_generation",
            "market_generations",
            "official_suspensions",
            "membership_generations",
            "tables",
            "raw",
            "scope",
            "quality",
            "final_oos_eligible",
            "artifact_root_sha256",
            "sqlite",
            "maximum_rows_buffered",
            "bundle_sha256",
            "manifest_sha256",
        }
        legacy_manifest = "temporal_binding" not in manifest
        if legacy_manifest:
            expected_manifest_keys.remove("temporal_binding")
        if set(manifest) != expected_manifest_keys:
            raise PITReceiptError("audited SQLite artifact manifest keys are invalid")
        manifest_digest = _require_sha256(manifest.get("manifest_sha256"), "manifest hash")
        manifest_payload = {
            key: value for key, value in manifest.items() if key != "manifest_sha256"
        }
        if not hmac.compare_digest(_sha256(manifest_payload), manifest_digest):
            raise PITReceiptError("audited artifact manifest hash mismatch")
        bundle_digest = _require_sha256(manifest.get("bundle_sha256"), "bundle hash")
        bundle_payload = {
            key: value
            for key, value in manifest.items()
            if key not in {"bundle_sha256", "manifest_sha256"}
        }
        if not hmac.compare_digest(_sha256(bundle_payload), bundle_digest):
            raise PITReceiptError("audited artifact bundle hash mismatch")
        if manifest.get("schema_version") != UNIVERSE_ARTIFACT_SCHEMA_VERSION:
            raise PITReceiptError("unsupported audited universe schema")
        if manifest.get("artifact_kind") != "audited_sqlite_receipt_snapshot":
            raise PITReceiptError("unsupported audited SQLite artifact kind")
        if manifest.get("artifact_role") != "development_only":
            raise PITReceiptError("audited artifact role is invalid")
        if manifest.get("final_oos_eligible") is not False:
            raise PITReceiptError("audited artifact cannot claim final OOS eligibility")
        temporal = manifest.get("temporal_binding")
        if legacy_manifest:
            temporal = {
                "schema_version": "legacy_development_unbound",
                "promotion_eligible": False,
            }
        elif (
            isinstance(temporal, dict)
            and temporal.get("schema_version") == "legacy_development_unbound"
        ):
            if temporal != {
                "schema_version": "legacy_development_unbound",
                "promotion_eligible": False,
            }:
                raise PITReceiptError("legacy temporal binding is invalid")
        else:
            expected_temporal_keys = {
                "schema_version",
                "contract_sha256",
                "role",
                "start_date",
                "end_date",
                "permitted_operation",
                "promotion_eligible",
            }
            if not isinstance(temporal, dict) or set(temporal) != expected_temporal_keys:
                raise PITReceiptError("artifact temporal binding is invalid")
            _require_sha256(temporal["contract_sha256"], "temporal contract hash")
            if temporal["schema_version"] != "research-artifact-temporal-binding/v1":
                raise PITReceiptError("artifact temporal binding schema is invalid")
            if temporal["role"] not in {"development", "contaminated_diagnostic"}:
                raise PITReceiptError("artifact temporal role is invalid")
            if (
                temporal["permitted_operation"] != "publish"
                or temporal["promotion_eligible"] is not False
            ):
                raise PITReceiptError("artifact temporal eligibility is invalid")
            if (
                temporal["start_date"] != manifest["coverage"]["start_date"]
                or temporal["end_date"] != manifest["coverage"]["end_date"]
            ):
                raise PITReceiptError("artifact temporal range disagrees with coverage")
        is_legacy_unbound = temporal.get("schema_version") == "legacy_development_unbound"
        if is_legacy_unbound != allow_legacy_unbound:
            raise PITReceiptError(
                "legacy unbound artifact requires explicit legacy loader"
                if is_legacy_unbound
                else "bound artifact cannot use the legacy loader"
            )
        cls._verify_market_generations_structure(manifest["market_generations"])
        cls._verify_official_suspensions_structure(manifest["official_suspensions"])
        audit_digest = _require_sha256(manifest.get("coverage_audit_sha256"), "coverage audit hash")
        if not hmac.compare_digest(audit_digest, expected_audit):
            raise PITReceiptError("coverage audit hash does not match external anchor")
        artifact_root = _require_sha256(manifest.get("artifact_root_sha256"), "artifact root hash")
        if not allow_legacy_unbound:
            expected_root = _require_sha256(
                expected_artifact_root_sha256, "expected artifact root hash"
            )
            expected_contract = _require_sha256(
                expected_temporal_contract_sha256,
                "expected temporal contract hash",
            )
            if not hmac.compare_digest(artifact_root, expected_root):
                raise PITReceiptError("artifact root does not match external anchor")
            if not hmac.compare_digest(temporal["contract_sha256"], expected_contract):
                raise PITReceiptError("temporal contract does not match external anchor")
            if temporal["role"] != expected_temporal_role:
                raise PITReceiptError("temporal role does not match external anchor")
        if bundle_root.name != bundle_digest:
            raise PITReceiptError("audited artifact directory hash mismatch")
        sqlite_manifest = manifest.get("sqlite")
        if (
            not isinstance(sqlite_manifest, dict)
            or sqlite_manifest.get("path") != database_path.name
        ):
            raise PITReceiptError("audited artifact SQLite path mismatch")
        if (
            database_path.with_name(database_path.name + "-wal").exists()
            or database_path.with_name(database_path.name + "-shm").exists()
        ):
            raise PITReceiptError("audited artifact contains mutable SQLite sidecars")
        file_stat = database_path.stat()
        if file_stat.st_size != int(sqlite_manifest.get("bytes", -1)):
            raise PITReceiptError("artifact file hash mismatch (byte count differs)")
        expected_file_hash = _require_sha256(sqlite_manifest.get("sha256"), "artifact file hash")
        if not hmac.compare_digest(_file_sha256(database_path), expected_file_hash):
            raise PITReceiptError("artifact file hash mismatch")

        connection = cls._readonly_connection(database_path)
        try:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise PITReceiptError("audited artifact SQLite integrity check failed")
            if list(connection.execute("PRAGMA foreign_key_check")):
                raise PITReceiptError("audited artifact contains broken receipt lineage")
            metadata = {
                row["key"]: json.loads(row["value_json"])
                for row in connection.execute(
                    "SELECT key, value_json FROM artifact_metadata ORDER BY key"
                )
            }
            logical_keys = {
                "schema_version",
                "artifact_kind",
                "artifact_role",
                "temporal_binding",
                "producer_code_sha256",
                "coverage",
                "materialized_ranges",
                "coverage_audit_sha256",
                "receipt_manifest_sha256",
                "stock_generation",
                "market_generations",
                "official_suspensions",
                "membership_generations",
                "tables",
                "raw",
                "scope",
                "quality",
                "final_oos_eligible",
            }
            if legacy_manifest:
                logical_keys.remove("temporal_binding")
            if set(metadata) != logical_keys | {"artifact_root_sha256"}:
                raise PITReceiptError("audited artifact metadata keys are invalid")
            logical = {key: metadata[key] for key in logical_keys}
            if (
                _sha256(logical) != artifact_root
                or metadata["artifact_root_sha256"] != artifact_root
            ):
                raise PITReceiptError("audited artifact semantic root mismatch")
            for key in logical_keys | {"artifact_root_sha256"}:
                if manifest.get(key) != metadata[key]:
                    raise PITReceiptError(f"manifest and SQLite metadata disagree: {key}")
            cls._verify_table_roots(connection, manifest["tables"])
            cls._verify_stock_generation_materialization(connection, manifest["stock_generation"])
            cls._verify_materialized_ranges(connection, manifest["materialized_ranges"])
            cls._verify_market_generations_coverage(
                connection,
                manifest["market_generations"]["refs"],
                start_date=manifest["coverage"]["start_date"],
                end_date=manifest["coverage"]["end_date"],
            )
            cls._verify_raw_tree(bundle_root, connection, manifest["raw"])
            cls._verify_controlled_request_lineage(connection, manifest["quality"])

            snapshot_store = PITReceiptStore.__new__(PITReceiptStore)
            snapshot_store.root = bundle_root
            snapshot_store.raw_root = bundle_root / "raw"
            snapshot_store.database_path = database_path
            recomputed_audit = snapshot_store.audit_coverage(
                start_date=manifest["coverage"]["start_date"],
                end_date=manifest["coverage"]["end_date"],
                _connection=connection,
            )
            if not hmac.compare_digest(recomputed_audit["coverage_audit_sha256"], expected_audit):
                raise PITReceiptError("audited artifact coverage proof mismatch")
            if recomputed_audit["receipt_manifest_sha256"] != manifest["receipt_manifest_sha256"]:
                raise PITReceiptError("audited artifact receipt root mismatch")
            recomputed_generation = {
                "schema_version": STOCK_GENERATION_SCHEMA_VERSION,
                "scope_key": STOCK_GENERATION_SCOPE,
                "generation_id": recomputed_audit["stock_generation_id"],
                "manifest_sha256": recomputed_audit["stock_generation_manifest_sha256"],
                "rows_sha256": recomputed_audit["stock_generation_rows_sha256"],
                "audit_identity_sha256": recomputed_audit["stock_generation_audit_identity_sha256"],
                "lineage_sha256": recomputed_audit["stock_generation_lineage_sha256"],
            }
            if recomputed_generation != manifest["stock_generation"]:
                raise PITReceiptError("audited artifact stock generation proof mismatch")
            recomputed_market_generations = {
                "count": recomputed_audit["market_generation_count"],
                "root_sha256": recomputed_audit["market_generation_root_sha256"],
                "refs": recomputed_audit["market_generation_refs"],
            }
            if recomputed_market_generations != manifest["market_generations"]:
                raise PITReceiptError("audited artifact market generation proof mismatch")
            recomputed_official_suspensions = {
                "count": recomputed_audit["official_suspension_interval_count"],
                "root_sha256": recomputed_audit[
                    "official_suspension_evidence_root_sha256"
                ],
                "refs": recomputed_audit["official_suspension_refs"],
            }
            if recomputed_official_suspensions != manifest["official_suspensions"]:
                raise PITReceiptError(
                    "audited artifact official suspension proof mismatch"
                )
            recomputed_membership_generations = {
                "count": recomputed_audit["quarantined_membership_session_count"],
                "root_sha256": recomputed_audit["membership_generation_root_sha256"],
                "refs": recomputed_audit["membership_generation_refs"],
                "pre_anchor_session_count": recomputed_audit["pre_anchor_session_count"],
                "maximum_consecutive_quarantined_sessions": recomputed_audit[
                    "maximum_consecutive_quarantined_sessions"
                ],
            }
            if recomputed_membership_generations != manifest["membership_generations"]:
                raise PITReceiptError("audited artifact membership generation proof mismatch")
            return cls(
                database_path=database_path,
                manifest=dict(manifest),
                connection=connection,
                external_temporal_authority_verified=not allow_legacy_unbound,
            )
        except Exception:
            connection.close()
            raise

    @classmethod
    def from_file(
        cls,
        path: str,
        *,
        expected_coverage_audit_sha256: str,
        expected_artifact_root_sha256: str,
        expected_temporal_contract_sha256: str,
        expected_temporal_role: str,
    ) -> "AuditedPointInTimeUniverse":
        """Open a bound artifact only when all external authority anchors match."""

        _require_sha256(expected_coverage_audit_sha256, "expected coverage audit hash")
        _require_sha256(expected_artifact_root_sha256, "expected artifact root hash")
        _require_sha256(expected_temporal_contract_sha256, "expected temporal contract hash")
        if expected_temporal_role not in {"development", "contaminated_diagnostic"}:
            raise PITReceiptError("expected temporal role is invalid")
        return cls._from_file_impl(
            path,
            expected_coverage_audit_sha256=expected_coverage_audit_sha256,
            expected_artifact_root_sha256=expected_artifact_root_sha256,
            expected_temporal_contract_sha256=expected_temporal_contract_sha256,
            expected_temporal_role=expected_temporal_role,
        )

    @classmethod
    def from_legacy_unbound_file(
        cls,
        path: str,
        *,
        expected_coverage_audit_sha256: str,
    ) -> "AuditedPointInTimeUniverse":
        """Explicitly opt into a non-promotable legacy development artifact."""

        return cls._from_file_impl(
            path,
            expected_coverage_audit_sha256=expected_coverage_audit_sha256,
            allow_legacy_unbound=True,
        )

    def _require_open(self) -> sqlite3.Connection:
        if self._connection is None:
            raise PITReceiptError("audited universe is closed")
        return self._connection

    @property
    def artifact_root_sha256(self) -> str:
        return self.manifest["artifact_root_sha256"]

    @property
    def universe_sha256(self) -> str:
        return self.artifact_root_sha256

    @property
    def calendar_sha256(self) -> str:
        return self.manifest["tables"]["trade_sessions"]["sha256"]

    @property
    def source_manifest_sha256(self) -> str:
        return self.manifest["receipt_manifest_sha256"]

    @property
    def coverage_audit_sha256(self) -> str:
        return self.manifest["coverage_audit_sha256"]

    @property
    def stock_generation_id(self) -> str:
        return self.manifest["stock_generation"]["generation_id"]

    @property
    def stock_generation_manifest_sha256(self) -> str:
        return self.manifest["stock_generation"]["manifest_sha256"]

    @property
    def start_date(self) -> str:
        return self.manifest["coverage"]["start_date"]

    @property
    def end_date(self) -> str:
        return self.manifest["coverage"]["end_date"]

    @property
    def final_oos_eligible(self) -> bool:
        return False

    @property
    def temporal_role(self) -> str:
        binding = self.manifest.get("temporal_binding") or {}
        if binding.get("schema_version") == "research-artifact-temporal-binding/v1":
            return binding["role"]
        return "legacy_development_unbound"

    @property
    def temporal_contract_sha256(self) -> Any:
        binding = self.manifest.get("temporal_binding") or {}
        return binding.get("contract_sha256")

    @property
    def is_audited_store_artifact(self) -> bool:
        return True

    def item_as_of(self, symbol: str, signal_date: str) -> Any:
        connection = self._require_open()
        session = _iso_date(signal_date, "signal_date")
        if not self.start_date <= session <= self.end_date:
            raise ValueError(f"signal date is outside PIT universe coverage: {session}")
        is_session = connection.execute(
            """
            SELECT 1 FROM trade_sessions
            WHERE exchange = 'SSE' AND cal_date = ? AND is_open = 1
            """,
            (session,),
        ).fetchone()
        if is_session is None:
            raise ValueError(f"signal date is not a covered trading session: {session}")
        normalized_symbol = str(symbol).strip()
        rows = list(
            connection.execute(
                """
                SELECT substr(daily.ts_code, 1, 6) AS symbol,
                       daily.ts_code, daily.exchange, 'a' AS market,
                       daily.name, daily.industry, daily.list_date
                FROM daily_universe AS daily
                WHERE daily.trade_date = ?
                  AND substr(daily.ts_code, 1, 6) = ?
                  AND daily.list_date IS NOT NULL
                  AND daily.list_date <= daily.trade_date
                  AND (
                      (daily.exchange = 'SSE' AND substr(daily.ts_code, 1, 3) != '900')
                      OR
                      (daily.exchange = 'SZSE' AND substr(daily.ts_code, 1, 3) != '200')
                  )
                """,
                (session, normalized_symbol),
            )
        )
        if len(rows) > 1:
            raise PITReceiptError("audited universe contains duplicate symbols")
        return dict(rows[0]) if rows else None

    def items_as_of(self, signal_date: str) -> List[Dict[str, Any]]:
        """Return one signal day's complete audited A-share membership."""

        connection = self._require_open()
        session = _iso_date(signal_date, "signal_date")
        if not self.start_date <= session <= self.end_date:
            raise ValueError(f"signal date is outside PIT universe coverage: {session}")
        is_session = connection.execute(
            """
            SELECT 1 FROM trade_sessions
            WHERE exchange = 'SSE' AND cal_date = ? AND is_open = 1
            """,
            (session,),
        ).fetchone()
        if is_session is None:
            raise ValueError(f"signal date is not a covered trading session: {session}")
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT substr(daily.ts_code, 1, 6) AS symbol,
                       daily.ts_code, daily.exchange, 'a' AS market,
                       daily.name, daily.industry, daily.list_date
                FROM daily_universe AS daily
                WHERE daily.trade_date = ?
                  AND daily.list_date IS NOT NULL
                  AND daily.list_date <= daily.trade_date
                  AND (
                      (daily.exchange = 'SSE' AND substr(daily.ts_code, 1, 3) != '900')
                      OR
                      (daily.exchange = 'SZSE' AND substr(daily.ts_code, 1, 3) != '200')
                  )
                ORDER BY symbol
                """,
                (session,),
            )
        ]
        symbols = [row["symbol"] for row in rows]
        if len(symbols) != len(set(symbols)):
            raise PITReceiptError("audited universe contains duplicate symbols")
        return rows

    def open_sessions(self, start_date: str, end_date: str) -> List[str]:
        """Return covered SSE open sessions with one bounded read-only query."""

        connection = self._require_open()
        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if last < first:
            raise ValueError("end_date precedes start_date")
        if first < self.start_date or last > self.end_date:
            raise ValueError("requested session range is outside PIT universe coverage")
        rows = connection.execute(
            """
            SELECT cal_date FROM trade_sessions
            WHERE exchange = 'SSE' AND is_open = 1
              AND cal_date BETWEEN ? AND ?
            ORDER BY cal_date
            """,
            (first, last),
        ).fetchall()
        sessions = [str(row["cal_date"]) for row in rows]
        if sessions != sorted(set(sessions)):
            raise PITReceiptError("audited universe contains duplicate open sessions")
        return sessions

    def seed_items(self, start_date: str, end_date: str) -> List[Dict[str, Any]]:
        connection = self._require_open()
        first = _iso_date(start_date, "start_date")
        last = _iso_date(end_date, "end_date")
        if first < self.start_date or last > self.end_date or last < first:
            raise ValueError("requested seed range is outside PIT universe coverage")
        rows = connection.execute(
            """
            WITH first_seen AS (
                SELECT daily.ts_code, MIN(daily.trade_date) AS first_date
                FROM daily_universe AS daily
                WHERE daily.trade_date BETWEEN ? AND ?
                  AND daily.list_date IS NOT NULL
                  AND daily.list_date <= daily.trade_date
                  AND (
                      (daily.exchange = 'SSE' AND substr(daily.ts_code, 1, 3) != '900')
                      OR
                      (daily.exchange = 'SZSE' AND substr(daily.ts_code, 1, 3) != '200')
                  )
                GROUP BY daily.ts_code
            )
            SELECT substr(daily.ts_code, 1, 6) AS symbol,
                   'a' AS market, daily.name, daily.exchange
            FROM first_seen
            JOIN daily_universe AS daily
              ON daily.ts_code = first_seen.ts_code
             AND daily.trade_date = first_seen.first_date
            ORDER BY symbol
            """,
            (first, last),
        )
        result = [dict(row) for row in rows]
        symbols = [row["symbol"] for row in result]
        if len(symbols) != len(set(symbols)):
            raise PITReceiptError("audited universe contains duplicate seed symbols")
        return result

    def causal_signal_bars(
        self, symbol: Any, start_date: Any, as_of_date: Any
    ) -> List[Dict[str, Any]]:
        """Return causal signal-price bars for one symbol, read-only over [start, as_of].

        WHY: this is the only sanctioned path from the frozen snapshot to
        signal inputs. It resolves the 6-digit symbol to exactly one ts_code
        via ``security_master`` or, when the current master omits a historical
        member, the audited ``daily_universe`` rows. It then reads raw daily and
        adj_factor generation rows from the read-only snapshot, re-binds every
        row to its manifest market generation proof, and delegates factor application to
        ``app.research_market_data.causal_adjusted_bars`` so factors later than
        ``as_of_date`` are excluded by construction. No network, no cache, no
        live link back to the source store.
        """

        from app.research_market_data import causal_adjusted_bars

        connection = self._require_open()

        normalized_symbol = str(symbol).strip()
        if len(normalized_symbol) != 6 or not normalized_symbol.isdigit():
            raise PITReceiptError(f"unknown symbol in security_master: {symbol!r}")
        master_rows = list(
            connection.execute(
                """
                SELECT ts_code FROM security_master
                WHERE symbol = ?
                  AND exchange IN ('SSE', 'SZSE')
                  AND market IN ('主板', '创业板', '科创板')
                  AND list_status IN ('L', 'D')
                """,
                (normalized_symbol,),
            )
        )
        if not master_rows:
            master_rows = list(
                connection.execute(
                    """
                    SELECT DISTINCT ts_code FROM daily_universe
                    WHERE substr(ts_code, 1, 6) = ?
                      AND list_date IS NOT NULL
                      AND list_date <= trade_date
                      AND (
                          (exchange = 'SSE' AND substr(ts_code, 1, 3) != '900')
                          OR
                          (exchange = 'SZSE' AND substr(ts_code, 1, 3) != '200')
                      )
                    ORDER BY ts_code
                    """,
                    (normalized_symbol,),
                )
            )
        if not master_rows:
            raise PITReceiptError(f"unknown symbol in security_master: {symbol!r}")
        if len(master_rows) > 1:
            raise PITReceiptError(f"symbol is not unique in security_master: {normalized_symbol}")
        ts_code = master_rows[0]["ts_code"]

        start = _iso_date(start_date, "start_date")
        as_of = _iso_date(as_of_date, "as_of_date")
        if not (self.start_date <= start <= self.end_date):
            raise ValueError(f"start_date is outside PIT universe coverage: {start}")
        if not (self.start_date <= as_of <= self.end_date):
            raise ValueError(f"as_of_date is outside PIT universe coverage: {as_of}")
        if start > as_of:
            raise ValueError("start_date is after as_of_date")
        is_open = connection.execute(
            """
            SELECT 1 FROM trade_sessions
            WHERE exchange = 'SSE' AND cal_date = ? AND is_open = 1
            """,
            (as_of,),
        ).fetchone()
        if is_open is None:
            raise ValueError(f"as_of_date is not a covered SSE open session: {as_of}")

        refs = {
            _iso_date(ref["trade_date"], "trade_date"): ref
            for ref in self.manifest["market_generations"]["refs"]
        }

        def _bind_generation(rows: Sequence[sqlite3.Row], dataset: str) -> None:
            # WHY: every row fetched from the snapshot must belong to a session
            # the manifest explicitly anchors, and its generation_id must agree
            # with that ref — otherwise a forged / rolled-back / extra
            # generation whose rows leaked into the snapshot fails closed.
            seen: set[str] = set()
            for row in rows:
                session = _iso_date(row["trade_date"], "trade_date")
                ref = refs.get(session)
                if ref is None:
                    raise PITReceiptError(f"{dataset} row is not in market_generations: {session}")
                if str(row["generation_id"]) != str(ref["generation_id"]):
                    raise PITReceiptError(f"{dataset} row generation_id mismatch: {session}")
                if session in seen:
                    raise PITReceiptError(f"duplicate {dataset} row: {session}")
                seen.add(session)

        daily_rows = list(
            connection.execute(
                """
                SELECT generation_id, trade_date, ts_code, open, high, low, close,
                       pre_close, pct_chg, vol, amount
                FROM market_session_generation_rows_daily
                WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (ts_code, start, as_of),
            )
        )
        factor_rows = list(
            connection.execute(
                """
                SELECT generation_id, trade_date, ts_code, adj_factor
                FROM market_session_generation_rows_adj_factor
                WHERE ts_code = ? AND trade_date BETWEEN ? AND ?
                ORDER BY trade_date
                """,
                (ts_code, start, as_of),
            )
        )
        _bind_generation(daily_rows, "daily")
        _bind_generation(factor_rows, "adj_factor")

        raw_bars = [
            {
                "ts_code": row["ts_code"],
                "trade_date": _iso_date(row["trade_date"], "trade_date"),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
            }
            for row in daily_rows
        ]
        adjustment_factors = [
            {
                "ts_code": row["ts_code"],
                "trade_date": _iso_date(row["trade_date"], "trade_date"),
                "adj_factor": row["adj_factor"],
            }
            for row in factor_rows
        ]
        # WHY: causal_adjusted_bars raises MarketEvidenceError (a ValueError) on
        # duplicate or missing factors and on raw-bar inconsistencies, and it
        # excludes any factor strictly after as_of — exactly the fail-closed
        # semantics this reader owes its callers.
        bars = causal_adjusted_bars(raw_bars, adjustment_factors, as_of_date=as_of)

        # Tushare ``daily`` 的原始数量单位是 vol=手、amount=千元。消费者既需要
        # 保存原值以便审计，也需要明确换算成研究层统一使用的股/元；禁止仅改名
        # 后把千元误当元，从而破坏流动性门槛。这里在只读边界再次做有限性校验，
        # 即使上游表约束将来回归，也不会把 NaN/inf/负数量送入回放。
        quantity_by_date: Dict[str, Dict[str, float]] = {}
        for row in daily_rows:
            session = _iso_date(row["trade_date"], "trade_date")

            def _finite(field: str, *, constraint: str) -> float:
                try:
                    number = float(row[field])
                except (TypeError, ValueError) as exc:
                    raise PITReceiptError(f"daily {field} is not numeric: {session}") from exc
                invalid = (
                    not math.isfinite(number)
                    or (constraint == "positive" and number <= 0)
                    or (constraint == "nonnegative" and number < 0)
                )
                if invalid:
                    raise PITReceiptError(
                        f"daily {field} must be finite and {constraint}: {session}"
                    )
                return number

            pre_close = _finite("pre_close", constraint="positive")
            pct_chg = _finite("pct_chg", constraint="numeric")
            volume_lots = _finite("vol", constraint="nonnegative")
            amount_thousand_yuan = _finite("amount", constraint="nonnegative")
            volume_shares = volume_lots * 100.0
            amount_yuan = amount_thousand_yuan * 1000.0
            if not math.isfinite(volume_shares) or not math.isfinite(amount_yuan):
                raise PITReceiptError(f"daily quantity conversion overflow: {session}")
            quantity_by_date[session] = {
                "raw_pre_close": pre_close,
                "raw_pct_chg": pct_chg,
                "raw_volume_lots": volume_lots,
                "raw_amount_thousand_yuan": amount_thousand_yuan,
                "volume_shares": volume_shares,
                "amount_yuan": amount_yuan,
            }

        proof_by_date = {
            session: {
                "trade_date": ref["trade_date"],
                "generation_id": ref["generation_id"],
                "manifest_sha256": ref["manifest_sha256"],
                "lineage_sha256": ref["lineage_sha256"],
                "vintage": ref["vintage"],
            }
            for session, ref in refs.items()
        }
        return [
            {
                **bar,
                **quantity_by_date[bar["trade_date"]],
                "generation_proof": proof_by_date[bar["trade_date"]],
            }
            for bar in bars
        ]

    def next_open_execution_evidence(
        self, symbol: Any, trade_date: Any, side: Any
    ) -> Dict[str, Any]:
        """Return the next-open fill verdict for one symbol/session, anchored to the snapshot.

        WHY: this is the only sanctioned path from the frozen snapshot to a
        single trade's next-open tradability verdict. It reuses the same
        symbol / date / open-session anchoring as ``causal_signal_bars``,
        reads the day's daily / stk_limit / suspend_d rows scoped to the
        manifest-anchored generation, and delegates the tradability decision to
        ``app.research_market_data.next_open_fill_gate``. No network, no cache,
        no live link back to the source store. ``raw_price`` comes only from
        ``daily.open``; missing daily / stk_limit lets the gate fail closed
        with a missing-bar reason.
        """

        from app.research_market_data import next_open_fill_gate

        connection = self._require_open()

        normalized_symbol = str(symbol).strip()
        if len(normalized_symbol) != 6 or not normalized_symbol.isdigit():
            raise PITReceiptError(f"unknown symbol in security_master: {symbol!r}")
        master_rows = list(
            connection.execute(
                """
                SELECT ts_code FROM security_master
                WHERE symbol = ?
                  AND exchange IN ('SSE', 'SZSE')
                  AND market IN ('主板', '创业板', '科创板')
                  AND list_status IN ('L', 'D')
                """,
                (normalized_symbol,),
            )
        )
        if not master_rows:
            master_rows = list(
                connection.execute(
                    """
                    SELECT DISTINCT ts_code FROM daily_universe
                    WHERE substr(ts_code, 1, 6) = ?
                      AND list_date IS NOT NULL
                      AND list_date <= trade_date
                      AND (
                          (exchange = 'SSE' AND substr(ts_code, 1, 3) != '900')
                          OR
                          (exchange = 'SZSE' AND substr(ts_code, 1, 3) != '200')
                      )
                    ORDER BY ts_code
                    """,
                    (normalized_symbol,),
                )
            )
        if not master_rows:
            raise PITReceiptError(f"unknown symbol in security_master: {symbol!r}")
        if len(master_rows) > 1:
            raise PITReceiptError(f"symbol is not unique in security_master: {normalized_symbol}")
        ts_code = master_rows[0]["ts_code"]

        session = _iso_date(trade_date, "trade_date")
        if not (self.start_date <= session <= self.end_date):
            raise ValueError(f"trade_date is outside PIT universe coverage: {session}")
        is_open = connection.execute(
            """
            SELECT 1 FROM trade_sessions
            WHERE exchange = 'SSE' AND cal_date = ? AND is_open = 1
            """,
            (session,),
        ).fetchone()
        if is_open is None:
            raise ValueError(f"trade_date is not a covered SSE open session: {session}")

        ref = {
            _iso_date(ref["trade_date"], "trade_date"): ref
            for ref in self.manifest["market_generations"]["refs"]
        }.get(session)
        if ref is None:
            raise PITReceiptError(f"trade_date is not in market_generations: {session}")
        generation_id = str(ref["generation_id"])

        # WHY: scope by the manifest-anchored generation_id so a forged /
        # rolled-back / extra generation whose rows leaked into the snapshot can
        # never supply execution evidence; the duplicate guard then fails closed
        # if the PK were ever regressed.
        daily_rows = list(
            connection.execute(
                """
                SELECT generation_id, trade_date, ts_code, open, high, low, close
                FROM market_session_generation_rows_daily
                WHERE generation_id = ? AND ts_code = ? AND trade_date = ?
                """,
                (generation_id, ts_code, session),
            )
        )
        if len(daily_rows) > 1:
            raise PITReceiptError(f"duplicate daily row: {session}")
        raw_bar: Mapping[str, Any] | None = None
        if daily_rows:
            row = daily_rows[0]
            if str(row["generation_id"]) != generation_id:
                raise PITReceiptError(f"daily row generation_id mismatch: {session}")
            raw_bar = {
                "ts_code": row["ts_code"],
                "trade_date": _iso_date(row["trade_date"], "trade_date"),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
            }

        limit_rows = list(
            connection.execute(
                """
                SELECT generation_id, trade_date, ts_code, pre_close, up_limit,
                       down_limit
                FROM market_session_generation_rows_stk_limit
                WHERE generation_id = ? AND ts_code = ? AND trade_date = ?
                """,
                (generation_id, ts_code, session),
            )
        )
        if len(limit_rows) > 1:
            raise PITReceiptError(f"duplicate stk_limit row: {session}")
        price_limit: Mapping[str, Any] | None = None
        if limit_rows:
            row = limit_rows[0]
            if str(row["generation_id"]) != generation_id:
                raise PITReceiptError(f"stk_limit row generation_id mismatch: {session}")
            price_limit = {
                "pre_close": row["pre_close"],
                "up_limit": row["up_limit"],
                "down_limit": row["down_limit"],
            }

        # WHY: any full-day (type S) suspension makes the open unfillable; type
        # R (resumption) does not, so only an S row is forwarded to the gate.
        suspension: Mapping[str, Any] | None = None
        suspend_rows = list(
            connection.execute(
                """
                SELECT generation_id, trade_date, ts_code, suspend_timing,
                       suspend_type
                FROM market_session_generation_rows_suspend_d
                WHERE generation_id = ? AND ts_code = ? AND trade_date = ?
                """,
                (generation_id, ts_code, session),
            )
        )
        for row in suspend_rows:
            if str(row["generation_id"]) != generation_id:
                raise PITReceiptError(f"suspend_d row generation_id mismatch: {session}")
            if suspension is None and str(row["suspend_type"] or "").strip().upper() == "S":
                suspension = {
                    "suspend_timing": row["suspend_timing"],
                    "suspend_type": row["suspend_type"],
                }

        result = next_open_fill_gate(
            side=side,
            raw_bar=raw_bar,
            price_limit=price_limit,
            suspension=suspension,
        )
        generation_proof = {
            "trade_date": ref["trade_date"],
            "generation_id": ref["generation_id"],
            "manifest_sha256": ref["manifest_sha256"],
            "lineage_sha256": ref["lineage_sha256"],
            "vintage": ref["vintage"],
        }
        return {**result, "generation_proof": generation_proof}

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    def __enter__(self) -> "AuditedPointInTimeUniverse":
        self._require_open()
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
