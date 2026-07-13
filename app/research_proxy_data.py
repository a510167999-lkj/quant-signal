"""Exact ETF proxy data contract for strict A-share research replay.

Two broad-market ETFs (510300.SH / 159915.SZ) act as a raw, unadjusted
execution-price proxy. This module only establishes the data contract —
symbols, fund_daily fields, the official row cap, and fail-closed coverage
auditing. It does not touch the collector, PIT store, or artifact replay.

Coverage audits fail closed: a missing session for either symbol is never
ignored on the assumption that ETF suspensions can be papered over. The proxy
must prove every open session or the audit refuses.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Sequence


class EtfProxyError(ValueError):
    pass


# --- frozen contract constants --------------------------------------------

ETF_PROXY_REQUIRED_SYMBOLS: tuple[str, ...] = ("510300.SH", "159915.SZ")

FUND_DAILY_FIELDS: tuple[str, ...] = (
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
)

FUND_DAILY_ROW_CAP: int = 5000  # tushare fund_daily official single-call cap

ETF_PROXY_PRICE_BASIS: str = "raw_unadjusted_proxy"

ETF_PROXY_COVERAGE_SCHEMA_VERSION: str = "etf_proxy_coverage_v1"


@dataclass(frozen=True)
class EtfProxySpec:
    symbol: str
    fields: tuple[str, ...]
    row_cap: int
    price_basis: str
    wire_params: Mapping[str, str]


# --- helpers ---------------------------------------------------------------

def _iso_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise EtfProxyError(f"invalid {field}: {value!r}") from exc


def _positive_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EtfProxyError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number <= 0:
        raise EtfProxyError(f"{field} must be positive and finite")
    return number


def _non_negative_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EtfProxyError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise EtfProxyError(f"{field} must be non-negative and finite")
    return number


def _finite_number(value: Any, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EtfProxyError(f"{field} must be numeric") from exc
    if not math.isfinite(number):
        raise EtfProxyError(f"{field} must be finite")
    return number


# --- spec builder ----------------------------------------------------------

def build_etf_proxy_specs(start_date: Any, end_date: Any) -> list[EtfProxySpec]:
    """One fetch spec per required ETF symbol, wire dates in YYYYMMDD."""

    start = _iso_date(start_date, "start_date")
    end = _iso_date(end_date, "end_date")
    if start > end:
        raise EtfProxyError("start_date must not exceed end_date")
    wire_start = start.replace("-", "")
    wire_end = end.replace("-", "")
    # Each spec carries its OWN wire_params, pinned to the exact ts_code — the
    # symbol is not a free parameter the caller can override by swapping
    # spec.symbol. A fresh dict per spec keeps the contract per-instance.
    return [
        EtfProxySpec(
            symbol=symbol,
            fields=FUND_DAILY_FIELDS,
            row_cap=FUND_DAILY_ROW_CAP,
            price_basis=ETF_PROXY_PRICE_BASIS,
            wire_params={
                "ts_code": symbol,
                "start_date": wire_start,
                "end_date": wire_end,
            },
        )
        for symbol in ETF_PROXY_REQUIRED_SYMBOLS
    ]


# --- row normalization -----------------------------------------------------

def normalize_etf_proxy_rows(
    rows: Sequence[Mapping[str, Any]],
    expected_symbol: str,
    start: Any,
    end: Any,
) -> list[dict[str, Any]]:
    """Validate fund_daily rows for one symbol; return raw prices sorted by date.

    No price adjustment is ever applied — the proxy carries raw execution
    prices only. Dates are normalized to ISO; rows must fall inside the
    ``[start, end]`` window and be unique per symbol-day.

    ``expected_symbol`` must be one of the frozen required proxy symbols — a
    self-consistent third-party ETF (e.g. 510050.SH) is rejected even when its
    rows match, because it is not part of the contract.
    """

    expected = str(expected_symbol).strip()
    if expected not in ETF_PROXY_REQUIRED_SYMBOLS:
        raise EtfProxyError(
            f"expected_symbol {expected!r} is not a required ETF proxy symbol"
        )
    window_start = _iso_date(start, "start")
    window_end = _iso_date(end, "end")
    if window_start > window_end:
        raise EtfProxyError("start must not exceed end")

    required_fields = set(FUND_DAILY_FIELDS)
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise EtfProxyError("proxy row must be a mapping")
        row_fields = set(row.keys())
        if row_fields != required_fields:
            raise EtfProxyError(
                f"proxy row fields {sorted(row_fields)} != required "
                f"{sorted(required_fields)}"
            )
        symbol = str(row.get("ts_code") or "").strip()
        if symbol == "":
            raise EtfProxyError("proxy row symbol is missing")
        if symbol != expected:
            raise EtfProxyError(
                f"proxy row symbol {symbol!r} does not match expected {expected!r}"
            )
        session = _iso_date(row.get("trade_date"), "proxy trade_date")
        if session < window_start or session > window_end:
            raise EtfProxyError(
                f"proxy row {session} is outside window "
                f"[{window_start}, {window_end}]"
            )
        if session in seen:
            raise EtfProxyError(f"duplicate proxy row for {symbol} on {session}")
        seen.add(session)

        open_ = _positive_number(row.get("open"), "open")
        high = _positive_number(row.get("high"), "high")
        low = _positive_number(row.get("low"), "low")
        close = _positive_number(row.get("close"), "close")
        pre_close = _positive_number(row.get("pre_close"), "pre_close")
        if high < max(open_, close):
            raise EtfProxyError("proxy high is below open or close")
        if low > min(open_, close):
            raise EtfProxyError("proxy low is above open or close")

        normalized.append(
            {
                "ts_code": symbol,
                "trade_date": session,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": pre_close,
                "change": _finite_number(row.get("change"), "change"),
                "pct_chg": _finite_number(row.get("pct_chg"), "pct_chg"),
                "vol": _non_negative_number(row.get("vol"), "vol"),
                "amount": _non_negative_number(row.get("amount"), "amount"),
            }
        )

    normalized.sort(key=lambda r: r["trade_date"])
    return normalized


# --- coverage audit --------------------------------------------------------

def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def audit_etf_proxy_coverage(
    rows_by_symbol: Mapping[str, Sequence[Mapping[str, Any]]],
    open_sessions: Sequence[Any],
) -> dict[str, Any]:
    """Fail-closed coverage check: every open session proven for both ETFs.

    ETF suspensions are not assumed away — a missing row is a hard failure.
    The audit never trusts that the caller has already normalized: it re-runs
    :func:`normalize_etf_proxy_rows` over the ``open_sessions`` first/last
    window so that field-set drift, OHLC violations, symbol swaps, duplicate
    days and out-of-window rows are all re-caught here, then enforces exact
    session coverage. The returned ``coverage_sha256`` fingerprints the sorted
    coverage payload, so it is stable regardless of input row or dict ordering.
    """

    sessions = [_iso_date(s, "open_session") for s in open_sessions]
    if not sessions:
        raise EtfProxyError("open_sessions must be non-empty")
    if len(set(sessions)) != len(sessions):
        raise EtfProxyError("open_sessions must be unique")
    if sessions != sorted(sessions):
        raise EtfProxyError("open_sessions must be strictly ascending")
    session_set = set(sessions)

    # The supplied symbol set must be EXACTLY the required contract — neither
    # a missing symbol nor a stray third-party ETF is tolerated.
    if set(rows_by_symbol) != set(ETF_PROXY_REQUIRED_SYMBOLS):
        raise EtfProxyError(
            "proxy rows must cover exactly the required symbols "
            f"{list(ETF_PROXY_REQUIRED_SYMBOLS)}; got {sorted(rows_by_symbol)}"
        )

    window_start = sessions[0]
    window_end = sessions[-1]

    by_date: dict[str, dict[str, Mapping[str, Any]]] = {}
    for symbol in ETF_PROXY_REQUIRED_SYMBOLS:
        rows = rows_by_symbol[symbol]
        # Re-validate from scratch — do not assume the caller normalized.
        # This re-checks the exact field set, symbol, OHLC geometry, numeric
        # ranges, date window and per-day uniqueness.
        normalized = normalize_etf_proxy_rows(rows, symbol, window_start, window_end)
        index = {row["trade_date"]: row for row in normalized}

        missing = session_set - set(index)
        if missing:
            raise EtfProxyError(
                f"missing proxy session(s) for {symbol}: {sorted(missing)}"
            )
        extra = set(index) - session_set
        if extra:
            raise EtfProxyError(
                f"extra/out-of-window proxy session(s) for {symbol}: {sorted(extra)}"
            )
        by_date[symbol] = index

    root = [dict(by_date[symbol][session]) for symbol in ETF_PROXY_REQUIRED_SYMBOLS
            for session in sessions]

    coverage_sha256 = hashlib.sha256(
        _canonical_json(
            {
                "audit_schema_version": ETF_PROXY_COVERAGE_SCHEMA_VERSION,
                "symbols": list(ETF_PROXY_REQUIRED_SYMBOLS),
                "fields": list(FUND_DAILY_FIELDS),
                "row_cap": FUND_DAILY_ROW_CAP,
                "price_basis": ETF_PROXY_PRICE_BASIS,
                "open_sessions": sessions,
                "root": root,
            }
        ).encode("utf-8")
    ).hexdigest()

    return {
        "root": root,
        "count": len(root),
        "coverage_sha256": coverage_sha256,
        "final_oos_eligible": False,
    }
