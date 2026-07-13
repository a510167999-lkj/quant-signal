"""Deterministic policy and coverage audit for the 2026 current stock pool."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


POLICY_ID = "current-pool-mainboard-chinext-v1"
CURRENT_POOL_AUDIT_SCHEMA = "current-pool-coverage-audit"
CURRENT_POOL_AUDIT_SCHEMA_VERSION = "current-pool-coverage-audit/v1"
_MAINBOARD_PREFIXES = ("000", "001", "002", "003", "600", "601", "603", "605")
_CHINEXT_PREFIXES = ("300", "301", "302")


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _raw_symbol(item: Mapping[str, Any]) -> str:
    return _text(item.get("ts_code") or item.get("symbol") or item.get("code")).upper()


def _parse_symbol(value: str) -> tuple[str, str]:
    parts = value.split(".")
    if len(parts) > 2:
        return "", ""
    code = parts[0]
    suffix = parts[1] if len(parts) == 2 else ""
    if len(code) != 6 or not code.isdigit() or suffix not in {"", "SH", "SZ", "BJ"}:
        return "", ""
    return code, suffix


def _board_from_symbol(symbol: str) -> str | None:
    code, _ = _parse_symbol(symbol)
    if code.startswith(_CHINEXT_PREFIXES):
        return "chinext"
    if code.startswith(_MAINBOARD_PREFIXES):
        return "mainboard"
    if code.startswith(("688", "689")):
        return "science_technology"
    if symbol.endswith(".BJ") or code.startswith(("4", "8", "9")):
        return "beijing"
    return None


def _flag(item: Mapping[str, Any], *names: str) -> bool:
    nested = item.get("risk_flags")
    sources = (nested, item) if isinstance(nested, Mapping) else (item,)
    for source in sources:
        for name in names:
            value = source.get(name)
            if value is True or (
                isinstance(value, (int, str))
                and str(value).strip().lower() in {"1", "y", "true"}
            ):
                return True
    return False


def classify_current_pool_item(item: Mapping[str, Any]) -> dict[str, Any]:
    """Classify one stock using structured fields before code/name fallbacks."""

    if not isinstance(item, Mapping):
        raise TypeError("item must be a mapping")
    raw_symbol = _raw_symbol(item)
    symbol, suffix = _parse_symbol(raw_symbol)
    inferred_board = _board_from_symbol(raw_symbol)
    market = _text(item.get("market"))
    exchange = _text(item.get("exchange")).upper()
    status = _text(item.get("list_status")).upper()
    name = _text(item.get("name"))
    reasons: list[str] = []

    if not symbol:
        reasons.append("excluded_symbol_invalid")
    elif symbol.startswith("6") and exchange in {"SZSE", "SZ", "BSE", "BJSE"}:
        reasons.append("excluded_conflict_exchange_symbol")
    elif symbol.startswith(("0", "3")) and exchange in {"SSE", "SH", "BSE", "BJSE"}:
        reasons.append("excluded_conflict_exchange_symbol")
    elif suffix == "BJ" and exchange and exchange not in {"BSE", "BJSE"}:
        reasons.append("excluded_conflict_exchange_symbol")
    elif suffix == "SH" and exchange in {"SZSE", "SZ", "BSE", "BJSE"}:
        reasons.append("excluded_conflict_exchange_symbol")
    elif suffix == "SZ" and exchange in {"SSE", "SH", "BSE", "BJSE"}:
        reasons.append("excluded_conflict_exchange_symbol")
    elif suffix == "SZ" and symbol.startswith("6"):
        reasons.append("excluded_conflict_exchange_symbol")
    elif suffix == "SH" and symbol.startswith(("0", "3")):
        reasons.append("excluded_conflict_exchange_symbol")

    structured_board = {"主板": "mainboard", "创业板": "chinext"}.get(market)
    if (
        structured_board is not None
        and inferred_board is not None
        and structured_board != inferred_board
        and "excluded_conflict_exchange_symbol" not in reasons
    ):
        reasons.append("excluded_conflict_market_symbol")

    if not reasons and exchange in {"BSE", "BJSE"}:
        reasons.append("excluded_exchange_beijing")
    elif not reasons and exchange and exchange not in {"SSE", "SZSE", "SH", "SZ"}:
        reasons.append("excluded_exchange_unsupported")

    if not reasons:
        if market in {"科创板", "STAR", "STAR MARKET"}:
            reasons.append("excluded_market_science_technology")
        elif market in {"北交所", "北京证券交易所"}:
            reasons.append("excluded_market_beijing")
        elif market and market not in {"主板", "创业板"}:
            reasons.append("excluded_market_unsupported")
        elif not market and inferred_board == "science_technology":
            reasons.append("excluded_market_science_technology")
        elif not market and inferred_board == "beijing":
            reasons.append("excluded_exchange_beijing")
        elif not market and inferred_board is None:
            reasons.append("excluded_board_unsupported")

    if not status:
        reasons.append("excluded_list_status_missing")
    elif status != "L":
        reasons.append(
            {
                "D": "excluded_list_status_delisted",
                "P": "excluded_list_status_paused",
            }.get(status, "excluded_list_status_not_listed")
        )

    normalized_name = name.lstrip().upper()
    if normalized_name.startswith(("ST", "*ST")):
        reasons.append("excluded_name_st")
    if "退市整理" in name:
        reasons.append("excluded_name_delisting_period")

    if _flag(item, "is_st", "st"):
        reasons.append("excluded_risk_st")
    if _flag(item, "is_delisting", "delisting", "in_delisting_period"):
        reasons.append("excluded_risk_delisting")
    daily_reasons: list[str] = []
    if _flag(item, "is_suspended", "suspended", "is_paused"):
        daily_reasons.append("daily_entry_blocked_suspended")

    board = {
        "主板": "mainboard",
        "创业板": "chinext",
    }.get(market, inferred_board)
    reasons = list(dict.fromkeys(reasons))
    return {
        "symbol": symbol,
        "eligible": not reasons,
        "board": board,
        "reason_codes": reasons,
        "signal_allowed_today": not reasons and not daily_reasons,
        "daily_entry_blocked": bool(daily_reasons),
        "daily_reason_codes": daily_reasons,
    }


def _bar_count(history: Any) -> int:
    if isinstance(history, bool):
        raise TypeError("history must be a sequence or non-negative integer")
    if isinstance(history, int):
        if history < 0:
            raise ValueError("history bar count cannot be negative")
        return history
    if isinstance(history, Sequence) and not isinstance(history, (str, bytes)):
        return len(history)
    raise TypeError("history must be a sequence or non-negative integer")


def _percentage(numerator: int, denominator: int) -> float:
    return round(100.0 * numerator / denominator, 2) if denominator else 0.0


def build_current_pool_coverage(
    items: Sequence[Mapping[str, Any]],
    histories: Mapping[str, Any],
    min_signal_bars: int = 90,
) -> dict[str, Any]:
    """Build a canonical development-only coverage audit for the current pool."""

    if (
        isinstance(min_signal_bars, bool)
        or not isinstance(min_signal_bars, int)
        or min_signal_bars < 90
    ):
        raise ValueError("min_signal_bars must be an integer of at least 90")
    if not isinstance(histories, Mapping):
        raise TypeError("histories must be a mapping")

    normalized_histories: dict[str, int] = {}
    for raw_history_symbol, history in histories.items():
        history_symbol, _ = _parse_symbol(_text(raw_history_symbol).upper())
        if not history_symbol:
            raise ValueError("histories contain an invalid symbol")
        if history_symbol in normalized_histories:
            raise ValueError("histories must have unique canonical symbols")
        normalized_histories[history_symbol] = _bar_count(history)

    classified = sorted(
        (classify_current_pool_item(item) for item in items),
        key=lambda row: row["symbol"],
    )
    symbols = [row["symbol"] for row in classified]
    if not all(symbols) or len(symbols) != len(set(symbols)):
        raise ValueError("items must have unique non-empty symbols")
    eligible = [row for row in classified if row["eligible"]]

    cohorts = {"0-59": 0, "60-89": 0, "90-249": 0, "250-499": 0, "500+": 0}
    item_history_status: list[dict[str, Any]] = []
    successes = 0
    ready = 0
    for row in classified:
        symbol = row["symbol"]
        if not row["eligible"]:
            item_history_status.append(
                {
                    "symbol": symbol,
                    "eligible": False,
                    "bar_count": None,
                    "fetch_status": "not_applicable",
                    "signal_ready": False,
                }
            )
            continue
        if symbol not in normalized_histories:
            item_history_status.append(
                {
                    "symbol": symbol,
                    "eligible": True,
                    "bar_count": None,
                    "fetch_status": "failure",
                    "signal_ready": False,
                }
            )
            continue
        bars = normalized_histories[symbol]
        item_history_status.append(
            {
                "symbol": symbol,
                "eligible": True,
                "bar_count": bars,
                "fetch_status": "success",
                "signal_ready": bars >= min_signal_bars,
            }
        )
        successes += 1
        ready += bars >= min_signal_bars
        if bars < 60:
            cohorts["0-59"] += 1
        elif bars < 90:
            cohorts["60-89"] += 1
        elif bars < 250:
            cohorts["90-249"] += 1
        elif bars < 500:
            cohorts["250-499"] += 1
        else:
            cohorts["500+"] += 1

    eligible_count = len(eligible)
    counts = {
        "mother_pool": len(classified),
        "eligible": eligible_count,
        "history_success": successes,
        "history_failure": eligible_count - successes,
        "signal_ready": ready,
    }
    normalized_inputs = [
        {
            "symbol": classification["symbol"],
            "classification": classification,
            "bar_count": status["bar_count"],
            "fetch_status": status["fetch_status"],
        }
        for classification, status in zip(classified, item_history_status)
    ]
    input_canonical = json.dumps(
        normalized_inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    audit: dict[str, Any] = {
        "schema": CURRENT_POOL_AUDIT_SCHEMA,
        "schema_version": CURRENT_POOL_AUDIT_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "development_only": True,
        "min_signal_bars": min_signal_bars,
        "input_summary_sha256": hashlib.sha256(
            input_canonical.encode("utf-8")
        ).hexdigest(),
        "counts": counts,
        "percentages": {
            "eligible_of_mother_pool": _percentage(eligible_count, len(classified)),
            "history_success_of_eligible": _percentage(successes, eligible_count),
            "history_failure_of_eligible": _percentage(eligible_count - successes, eligible_count),
            "signal_ready_of_eligible": _percentage(ready, eligible_count),
        },
        "history_cohorts": cohorts,
        "item_history_status": item_history_status,
    }
    canonical = json.dumps(audit, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    audit["canonical_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return audit
