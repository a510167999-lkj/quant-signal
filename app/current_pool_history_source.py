"""Development-only history coverage for the current A-share pool.

This artifact is a coverage summary, not a replay dataset.  It binds a strict
current-universe descriptor to active, deep-verified market generations and
only exposes per-symbol completeness counts.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from app.current_pool_source import (
    _strict_json_loads,
    verify_current_pool_universe_descriptor,
)
from app.research_pit_store import MARKET_SESSION_VINTAGES, PITReceiptStore


SCHEMA_VERSION = "current-pool-history-summary/v1"
_HEX = frozenset("0123456789abcdef")
_REF_FIELDS = {
    "trade_date",
    "generation_id",
    "manifest_sha256",
    "lineage_sha256",
    "vintage",
}
_ITEM_FIELDS = {
    "ts_code",
    "bar_count",
    "daily_row_count",
    "adj_factor_row_count",
    "stk_limit_row_count",
    "first_complete_trade_date",
    "last_complete_trade_date",
}
_UNSIGNED_FIELDS = {
    "schema",
    "source_id",
    "as_of",
    "universe_descriptor_sha256",
    "universe_item_count",
    "history_start",
    "history_end",
    "open_session_count",
    "market_generation_refs",
    "market_generation_root_sha256",
    "items",
    "items_sha256",
    "current_universe_bias",
    "development_only",
    "live_proof",
    "replay_eligible",
    "production_recommendation_eligible",
}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_date(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical ISO date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO date")
    return text


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in _HEX for character in value)
    )


def _load_universe_descriptor(path: str | Path) -> dict[str, Any]:
    descriptor_path = Path(path)
    try:
        payload = _strict_json_loads(descriptor_path.read_bytes())
        verified = verify_current_pool_universe_descriptor(payload)
        digest = verified["descriptor_sha256"]
        if len(descriptor_path.stem) == 64 and descriptor_path.stem != digest:
            raise ValueError("content-addressed universe filename mismatch")
    except (OSError, TypeError, ValueError) as exc:
        raise ValueError("current-pool universe descriptor rejected") from exc
    return dict(payload)


def verify_current_pool_history_descriptor(
    payload: Mapping[str, Any], universe_descriptor: Mapping[str, Any]
) -> dict[str, Any]:
    """Strictly verify a summary and its binding to one universe descriptor."""

    error = "current-pool history summary descriptor rejected"
    try:
        verified_universe = verify_current_pool_universe_descriptor(universe_descriptor)
        if (
            not isinstance(payload, Mapping)
            or set(payload) != {*_UNSIGNED_FIELDS, "descriptor_sha256"}
            or payload.get("schema") != SCHEMA_VERSION
            or payload.get("source_id") != "jiaoch"
            or payload.get("current_universe_bias") is not True
            or payload.get("development_only") is not True
            or payload.get("live_proof") is not False
            or payload.get("replay_eligible") is not False
            or payload.get("production_recommendation_eligible") is not False
        ):
            raise ValueError(error)

        unsigned = {key: value for key, value in payload.items() if key != "descriptor_sha256"}
        descriptor_digest = _sha256(unsigned)
        if payload.get("descriptor_sha256") != descriptor_digest:
            raise ValueError(error)
        if payload.get("universe_descriptor_sha256") != verified_universe["descriptor_sha256"]:
            raise ValueError(error)

        as_of = _canonical_date(payload.get("as_of"), "as_of")
        history_start = _canonical_date(payload.get("history_start"), "history_start")
        history_end = _canonical_date(payload.get("history_end"), "history_end")
        universe_as_of = _canonical_date(universe_descriptor.get("as_of"), "universe as_of")
        if as_of != universe_as_of or history_end < history_start or history_end > as_of:
            raise ValueError(error)

        refs = payload.get("market_generation_refs")
        open_session_count = payload.get("open_session_count")
        if (
            type(open_session_count) is not int
            or open_session_count <= 0
            or not isinstance(refs, list)
            or len(refs) != open_session_count
        ):
            raise ValueError(error)
        normalized_refs: list[dict[str, Any]] = []
        for ref in refs:
            if (
                not isinstance(ref, Mapping)
                or set(ref) != _REF_FIELDS
                or not isinstance(ref.get("generation_id"), str)
                or not ref["generation_id"]
                or not _is_sha256(ref.get("manifest_sha256"))
                or not _is_sha256(ref.get("lineage_sha256"))
                or ref.get("vintage") not in MARKET_SESSION_VINTAGES
            ):
                raise ValueError(error)
            session = _canonical_date(ref.get("trade_date"), "market trade_date")
            if not history_start <= session <= history_end:
                raise ValueError(error)
            normalized_refs.append(dict(ref))
        ref_dates = [ref["trade_date"] for ref in normalized_refs]
        if ref_dates != sorted(set(ref_dates)):
            raise ValueError(error)
        if payload.get("market_generation_root_sha256") != _sha256(normalized_refs):
            raise ValueError(error)

        universe_items = universe_descriptor.get("items")
        if not isinstance(universe_items, list):
            raise ValueError(error)
        universe_symbols = [item.get("ts_code") for item in universe_items]
        if type(payload.get("universe_item_count")) is not int or payload[
            "universe_item_count"
        ] != len(universe_symbols):
            raise ValueError(error)

        items = payload.get("items")
        if not isinstance(items, list) or len(items) != len(universe_symbols):
            raise ValueError(error)
        normalized_items: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Mapping) or set(item) != _ITEM_FIELDS:
                raise ValueError(error)
            symbol = item.get("ts_code")
            counts = [
                item.get("bar_count"),
                item.get("daily_row_count"),
                item.get("adj_factor_row_count"),
                item.get("stk_limit_row_count"),
            ]
            if (
                type(symbol) is not str
                or any(type(count) is not int for count in counts)
                or any(count < 0 or count > open_session_count for count in counts)
                or counts[0] > min(counts[1:])
            ):
                raise ValueError(error)
            first = item.get("first_complete_trade_date")
            last = item.get("last_complete_trade_date")
            if counts[0] == 0:
                if first is not None or last is not None:
                    raise ValueError(error)
            else:
                first = _canonical_date(first, "first complete trade_date")
                last = _canonical_date(last, "last complete trade_date")
                if first not in ref_dates or last not in ref_dates or first > last:
                    raise ValueError(error)
            normalized_items.append(dict(item))

        item_symbols = [item["ts_code"] for item in normalized_items]
        if (
            item_symbols != sorted(set(item_symbols))
            or item_symbols != sorted(universe_symbols)
            or payload.get("items_sha256") != _sha256(normalized_items)
        ):
            raise ValueError(error)
    except (KeyError, TypeError, ValueError):
        raise ValueError(error) from None
    return {
        "descriptor_sha256": descriptor_digest,
        "item_count": len(normalized_items),
        "open_session_count": open_session_count,
    }


def _write_content_addressed(
    output_dir: str | Path,
    unsigned_payload: dict[str, Any],
    universe_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    digest = _sha256(unsigned_payload)
    descriptor = {**unsigned_payload, "descriptor_sha256": digest}
    verify_current_pool_history_descriptor(descriptor, universe_descriptor)
    content = (
        json.dumps(
            descriptor,
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{digest}.json"
    created = not destination.exists()
    if created:
        file_descriptor, temporary = tempfile.mkstemp(
            prefix=f"{destination.name}.", suffix=".tmp", dir=str(directory)
        )
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            directory_descriptor = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif destination.read_bytes() != content:
        raise ValueError("content-addressed current-pool history summary mismatch")
    return {"path": str(destination), "descriptor_sha256": digest, "created": created}


def build_current_pool_history_summary(
    *,
    universe_path: str | Path,
    store_dir: str | Path,
    history_start: str,
    history_end: str,
    as_of: str,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Build a deterministic summary from verified active market generations."""

    universe = _load_universe_descriptor(universe_path)
    canonical_as_of = _canonical_date(as_of, "as_of")
    start = _canonical_date(history_start, "history_start")
    end = _canonical_date(history_end, "history_end")
    if end < start:
        raise ValueError("history_end precedes history_start")
    if end > canonical_as_of:
        raise ValueError("history_end cannot exceed as_of")
    universe_as_of = _canonical_date(universe.get("as_of"), "universe as_of")
    if canonical_as_of != universe_as_of:
        raise ValueError("as_of must match the universe descriptor")

    store = PITReceiptStore(str(store_dir))
    sessions = store.common_open_sessions(start_date=start, end_date=end)
    ts_codes = sorted(item["ts_code"] for item in universe["items"])
    coverage = store.current_pool_complete_bar_coverage(
        sessions=sessions,
        ts_codes=ts_codes,
    )
    if coverage["market_generation_count"] != len(sessions):
        raise ValueError("current-pool market generation coverage count mismatch")

    items = coverage["items"]
    unsigned_payload = {
        "schema": SCHEMA_VERSION,
        "source_id": "jiaoch",
        "as_of": canonical_as_of,
        "universe_descriptor_sha256": universe["descriptor_sha256"],
        "universe_item_count": len(ts_codes),
        "history_start": start,
        "history_end": end,
        "open_session_count": len(sessions),
        "market_generation_refs": coverage["market_generation_refs"],
        "market_generation_root_sha256": coverage["market_generation_root_sha256"],
        "items": items,
        "items_sha256": _sha256(items),
        "current_universe_bias": True,
        "development_only": True,
        "live_proof": False,
        "replay_eligible": False,
        "production_recommendation_eligible": False,
    }
    return _write_content_addressed(output_dir, unsigned_payload, universe)
