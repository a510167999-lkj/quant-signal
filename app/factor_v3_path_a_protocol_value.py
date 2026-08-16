"""Attach Path-A valuation fields onto existing identity books and score."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.factor_v3_path_a_protocol_daily_basic_value import (
    DEFAULT_OUTPUT_ROOT,
    VALUE_FIELDS,
    day_path,
)
from app.factor_v3_path_a_protocol_signal_specs import (
    VALUE_PB_OK_TAG,
    VALUE_PE_NULL_TAG,
    VALUE_PE_TTM_OK_TAG,
)


def symbol_to_ts_code(items: list[dict[str, Any]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in items:
        symbol = str(row.get("symbol") or "")
        ts_code = str(row.get("ts_code") or "")
        if symbol and ts_code:
            out[symbol] = ts_code
    return out


def load_value_index(
    output_root: Path,
    dates: set[str],
) -> dict[tuple[str, str], dict[str, Any]]:
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for day in sorted(dates):
        path = day_path(output_root, day)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("rows") or []:
            code = str(row.get("ts_code") or "")
            if not code:
                continue
            index[(code, day)] = {
                field: row.get(field) for field in VALUE_FIELDS if field != "ts_code"
            }
    return index


def attach_value_fields(
    trades: list[dict[str, Any]],
    *,
    value_index: dict[tuple[str, str], dict[str, Any]],
    ts_by_symbol: dict[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for trade in trades:
        copy = dict(trade)
        symbol = str(copy.get("symbol") or "")
        day = str(copy.get("signal_date") or "")[:10]
        ts_code = ts_by_symbol.get(symbol) or ""
        rec = value_index.get((ts_code, day)) or {}
        pe = rec.get("pe")
        pe_ttm = rec.get("pe_ttm")
        pb = rec.get("pb")
        copy["pe"] = pe
        copy["pe_ttm"] = pe_ttm
        copy["pb"] = pb
        copy["total_mv"] = rec.get("total_mv")
        copy["circ_mv"] = rec.get("circ_mv")
        tags = set(copy.get("signal_tags") or [])
        if pe is None:
            tags.add(VALUE_PE_NULL_TAG)
        if pe_ttm is not None:
            tags.add(VALUE_PE_TTM_OK_TAG)
        if pb is not None:
            tags.add(VALUE_PB_OK_TAG)
        copy["signal_tags"] = sorted(tags)
        out.append(copy)
    return out
