"""Fail-closed loader for the audited current A-share recommendation pool."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections import OrderedDict
from collections.abc import Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.current_pool import (
    CURRENT_POOL_AUDIT_SCHEMA,
    CURRENT_POOL_AUDIT_SCHEMA_VERSION,
    POLICY_ID,
    classify_current_pool_item,
)


_SHA256 = re.compile(r"[0-9a-f]{64}")
_EXPECTED_SOURCES = {
    "universe": "jiaoch",
    "history_summary": "jiaoch",
    "risk_snapshot": "jiaoch",
}
_REQUIRED_UNIVERSE_FIELDS = ("ts_code", "name", "market", "exchange", "list_status")
_REQUIRED_RISK_FLAGS = {"is_st", "is_suspended"}
_AUDIT_CACHE: OrderedDict[tuple[str, int, int, int, int], dict[str, Any]] = OrderedDict()
_AUDIT_CACHE_LIMIT = 8


class CurrentPoolGateError(ValueError):
    """The current-pool audit cannot safely authorize recommendation symbols."""


# Compatibility name for code written during the audit preflight. Both names
# identify the same fail-closed public exception type.
CurrentPoolAuditError = CurrentPoolGateError


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CurrentPoolAuditError("current-pool audit has duplicate JSON keys")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise CurrentPoolAuditError("current-pool audit contains a non-finite number")


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("canonical_sha256", None)
    try:
        canonical = json.dumps(
            unsigned,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise CurrentPoolAuditError("current-pool audit is not canonical JSON") from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _symbol_from_item(item: Mapping[str, Any]) -> str:
    raw = str(item.get("ts_code") or item.get("symbol") or "").strip().upper()
    return raw.split(".", 1)[0]


def _validate_universe_item(item: Mapping[str, Any]) -> None:
    if any(type(item.get(field)) is not str or not item[field].strip() for field in _REQUIRED_UNIVERSE_FIELDS):
        raise CurrentPoolGateError("current-pool audit universe structured fields are invalid")
    if not re.fullmatch(r"\d{6}\.(?:SH|SZ|BJ)", item["ts_code"].strip().upper()):
        raise CurrentPoolGateError("current-pool audit universe structured fields are invalid")
    risk_flags = item.get("risk_flags")
    if not isinstance(risk_flags, Mapping) or set(risk_flags) != _REQUIRED_RISK_FLAGS:
        raise CurrentPoolGateError("current-pool audit universe risk flags are invalid")
    if any(type(risk_flags[key]) is not bool for key in _REQUIRED_RISK_FLAGS):
        raise CurrentPoolGateError("current-pool audit universe risk flags are invalid")


def _source_age_hours(source_as_of: Any, now: datetime) -> float:
    raw_source_date = source_as_of if isinstance(source_as_of, str) else ""
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", raw_source_date):
        raise CurrentPoolAuditError("current-pool source_as_of is invalid")
    try:
        source_date = date.fromisoformat(raw_source_date)
    except ValueError as exc:
        raise CurrentPoolAuditError("current-pool source_as_of is invalid") from exc
    observed = now
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    else:
        observed = observed.astimezone(ZoneInfo("Asia/Shanghai"))
    if source_date > observed.date():
        raise CurrentPoolAuditError("current-pool source_as_of is in the future")
    source_time = datetime.combine(source_date, datetime.min.time(), ZoneInfo("Asia/Shanghai"))
    age = (observed - source_time).total_seconds() / 3600
    return max(0.0, age)


def _verify_current_pool_audit_bytes(raw_bytes: bytes) -> dict[str, Any]:
    """Verify immutable bytes without applying request-time freshness policy."""

    try:
        raw = raw_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CurrentPoolAuditError("current-pool audit is invalid UTF-8") from exc
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except CurrentPoolAuditError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CurrentPoolAuditError("current-pool audit is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise CurrentPoolAuditError("current-pool audit root must be an object")
    digest = payload.get("canonical_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise CurrentPoolAuditError("current-pool audit canonical hash is invalid")
    if digest != _canonical_sha256(payload):
        raise CurrentPoolAuditError("current-pool audit canonical hash mismatch")
    if (
        payload.get("schema") != CURRENT_POOL_AUDIT_SCHEMA
        or payload.get("schema_version") != CURRENT_POOL_AUDIT_SCHEMA_VERSION
    ):
        raise CurrentPoolAuditError("current-pool audit schema mismatch")
    if payload.get("policy_id") != POLICY_ID:
        raise CurrentPoolAuditError("current-pool audit policy mismatch")
    if payload.get("development_only") is not True or payload.get("evidence_scope") != "development_only":
        raise CurrentPoolAuditError("current-pool audit evidence scope is invalid")
    if payload.get("source_ids") != _EXPECTED_SOURCES:
        raise CurrentPoolAuditError("current-pool audit sources are invalid")
    hashes = payload.get("input_descriptor_sha256")
    if (
        not isinstance(hashes, dict)
        or set(hashes) != set(_EXPECTED_SOURCES)
        or any(not isinstance(value, str) or not _SHA256.fullmatch(value) for value in hashes.values())
    ):
        raise CurrentPoolAuditError("current-pool audit input hashes are invalid")
    if payload.get("risk_snapshot_complete") is not True or payload.get("risk_gate_passed") is not True:
        raise CurrentPoolAuditError("current-pool audit risk gate is incomplete")
    if payload.get("production_recommendation_eligible") is not False:
        raise CurrentPoolAuditError("current-pool audit must not claim production proof")
    minimum = payload.get("min_signal_bars")
    if isinstance(minimum, bool) or not isinstance(minimum, int) or minimum < 90:
        raise CurrentPoolAuditError("current-pool minimum signal bars must be at least 90")
    universe_items = payload.get("universe_items")
    statuses = payload.get("item_history_status")
    if not isinstance(universe_items, list) or not isinstance(statuses, list):
        raise CurrentPoolAuditError("current-pool audit universe/status rows are invalid")
    universe_by_symbol: dict[str, Mapping[str, Any]] = {}
    for item in universe_items:
        if not isinstance(item, Mapping):
            raise CurrentPoolAuditError("current-pool audit universe row is invalid")
        _validate_universe_item(item)
        symbol = _symbol_from_item(item)
        if not symbol or symbol in universe_by_symbol:
            raise CurrentPoolAuditError("current-pool audit universe symbols are invalid")
        universe_by_symbol[symbol] = item
    status_by_symbol: dict[str, Mapping[str, Any]] = {}
    for status in statuses:
        if not isinstance(status, Mapping):
            raise CurrentPoolAuditError("current-pool audit status row is invalid")
        symbol = str(status.get("symbol") or "").strip()
        if not re.fullmatch(r"\d{6}", symbol) or symbol in status_by_symbol:
            raise CurrentPoolAuditError("current-pool audit status symbols are invalid")
        status_by_symbol[symbol] = status
    if set(universe_by_symbol) != set(status_by_symbol):
        raise CurrentPoolAuditError("current-pool audit universe/status symbols mismatch")

    allowed: set[str] = set()
    for symbol, item in universe_by_symbol.items():
        status = status_by_symbol[symbol]
        classification = classify_current_pool_item(item)
        if classification["symbol"] != symbol:
            raise CurrentPoolAuditError("current-pool audit symbol classification mismatch")
        if status.get("eligible") is not classification["eligible"]:
            raise CurrentPoolAuditError("current-pool audit classification mismatch")
        if status.get("signal_allowed_today") is not classification["signal_allowed_today"]:
            raise CurrentPoolAuditError("current-pool audit daily risk classification mismatch")
        bars = status.get("bar_count")
        fetch_status = status.get("fetch_status")
        expected_ready = (
            classification["eligible"]
            and fetch_status == "success"
            and isinstance(bars, int)
            and not isinstance(bars, bool)
            and bars >= minimum
        )
        if status.get("signal_ready") is not expected_ready:
            raise CurrentPoolAuditError("current-pool audit history readiness mismatch")
        if classification["eligible"] and fetch_status == "success" and (
            isinstance(bars, bool) or not isinstance(bars, int) or bars < 0
        ):
            raise CurrentPoolAuditError("current-pool audit history bar count is invalid")
        if expected_ready and classification["signal_allowed_today"]:
            allowed.add(symbol)

    return {
        "allowed_symbols": allowed,
        "canonical_sha256": digest,
        "source_as_of": payload["source_as_of"],
        "evidence_scope": payload["evidence_scope"],
        "production_recommendation_eligible": False,
    }


def _version_key(path: Path, stat: os.stat_result) -> tuple[str, int, int, int, int]:
    return (
        str(path.resolve()),
        int(stat.st_dev),
        int(stat.st_ino),
        int(stat.st_mtime_ns),
        int(stat.st_size),
    )


def _read_verified_audit(path: str | Path) -> dict[str, Any]:
    audit_path = Path(path)
    try:
        descriptor = os.open(audit_path, os.O_RDONLY)
    except FileNotFoundError as exc:
        raise CurrentPoolGateError("current-pool audit is missing") from exc
    except (OSError, TypeError, ValueError) as exc:
        raise CurrentPoolGateError("current-pool audit is unreadable") from exc
    try:
        before = os.fstat(descriptor)
        key = _version_key(audit_path, before)
        cached = _AUDIT_CACHE.get(key)
        if cached is None:
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1024 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
            after = os.fstat(descriptor)
            if _version_key(audit_path, before) != _version_key(audit_path, after):
                raise CurrentPoolGateError("current-pool audit changed while being read")
            cached = _verify_current_pool_audit_bytes(b"".join(chunks))
            _AUDIT_CACHE[key] = cached
            _AUDIT_CACHE.move_to_end(key)
            while len(_AUDIT_CACHE) > _AUDIT_CACHE_LIMIT:
                _AUDIT_CACHE.popitem(last=False)
        else:
            after = os.fstat(descriptor)
            if _version_key(audit_path, before) != _version_key(audit_path, after):
                raise CurrentPoolGateError("current-pool audit changed while being read")
            _AUDIT_CACHE.move_to_end(key)
        return {**cached, "allowed_symbols": set(cached["allowed_symbols"])}
    finally:
        os.close(descriptor)


def verify_current_pool_audit(path: str | Path) -> dict[str, Any]:
    """Verify immutable audit bytes without applying production freshness limits."""

    try:
        return _read_verified_audit(path)
    except CurrentPoolGateError:
        raise
    except Exception as exc:
        raise CurrentPoolGateError("current-pool audit is invalid") from exc


def load_current_pool_audit(
    path: str | Path,
    *,
    now: datetime,
    max_age_hours: int,
    expected_source_dates: set[str] | None = None,
) -> dict[str, Any]:
    """Public error boundary: every malformed input fails with one stable type."""

    try:
        if (
            isinstance(max_age_hours, bool)
            or not isinstance(max_age_hours, int)
            or max_age_hours <= 0
        ):
            raise CurrentPoolGateError("current-pool max age must be a positive integer")
        verified = verify_current_pool_audit(path)
        age_hours = _source_age_hours(verified["source_as_of"], now)
        if expected_source_dates is not None:
            expected = {str(value)[:10] for value in expected_source_dates}
            if not expected or verified["source_as_of"] not in expected:
                raise CurrentPoolGateError(
                    "current-pool source_as_of does not match the expected trade data date"
                )
        elif age_hours > max_age_hours:
            raise CurrentPoolGateError("current-pool audit is stale")
        return {**verified, "age_hours": age_hours}
    except CurrentPoolGateError:
        raise
    except Exception as exc:
        raise CurrentPoolGateError("current-pool audit is invalid") from exc
