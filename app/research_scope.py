"""Frozen stock-market scope shared by replay production and verification."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence


POLICY_ID = "research-mainboard-chinext/v1"
ALLOWED_PREFIXES = (
    "000",
    "001",
    "002",
    "003",
    "300",
    "301",
    "302",
    "600",
    "601",
    "603",
    "605",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def market_scope_contract() -> dict[str, Any]:
    semantic = {
        "policy_id": POLICY_ID,
        "allowed_prefixes": list(ALLOWED_PREFIXES),
        "excluded_boards": ["science_technology", "beijing"],
    }
    return {
        **semantic,
        "policy_sha256": hashlib.sha256(
            _canonical_json(semantic).encode("utf-8")
        ).hexdigest(),
    }


def normalize_research_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    parts = raw.split(".")
    if len(parts) > 2:
        return ""
    code = parts[0]
    suffix = parts[1] if len(parts) == 2 else ""
    if len(code) != 6 or not code.isdigit() or suffix not in {"", "SH", "SZ"}:
        return ""
    if code.startswith("6") and suffix == "SZ":
        return ""
    if code.startswith(("0", "3")) and suffix == "SH":
        return ""
    return code


def is_mainboard_chinext_symbol(value: Any) -> bool:
    code = normalize_research_symbol(value)
    return bool(code) and code.startswith(ALLOWED_PREFIXES)


def is_mainboard_chinext_item(item: Mapping[str, Any]) -> bool:
    if not isinstance(item, Mapping):
        return False
    return is_mainboard_chinext_symbol(
        item.get("ts_code") or item.get("symbol") or item.get("code")
    )


def verify_trade_market_scope(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not isinstance(trades, Sequence) or isinstance(trades, (str, bytes)):
        raise ValueError("qualified trades must be a sequence")
    rejected = [
        str(trade.get("symbol") or "")
        for trade in trades
        if not isinstance(trade, Mapping)
        or not is_mainboard_chinext_symbol(trade.get("symbol"))
    ]
    if rejected:
        raise ValueError("qualified trade is outside the frozen mainboard+ChiNext scope")
    contract = market_scope_contract()
    return {**contract, "verified_trade_count": len(trades)}

