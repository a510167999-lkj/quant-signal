from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Callable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.current_pool_source import verify_current_pool_universe_descriptor
from app.durable_io import fsync_directory
from app.research_pit_sources import resolve_tushare_source
from app.research_pit_transport import UrllibTushareTransport


SCHEMA_VERSION = "current-pool-risk-input/v1"
PARTITION_ROW_CAP = 10_000
_MAX_BODY_BYTES = 5 * 1024 * 1024
_STOCK_ST_FIELDS = ("ts_code", "name", "type", "type_name", "trade_date")
_SUSPEND_FIELDS = ("ts_code", "trade_date", "suspend_timing", "suspend_type")
_NAMECHANGE_FIELDS = ("ts_code", "name", "start_date", "end_date", "ann_date", "change_reason")
_HEX = frozenset("0123456789abcdef")

PartitionFetcher = Callable[[str, dict[str, str], tuple[str, ...]], Mapping[str, Any]]


class CurrentPoolRiskSourceError(ValueError):
    """Fixed-message boundary for external risk-source failures."""


def is_st_risk_name(value: Any) -> bool:
    """Return whether a normalized display name has an ST risk prefix."""

    if not isinstance(value, str):
        return False
    return value.strip().upper().startswith(("ST", "*ST"))


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _strict_json_loads(raw: bytes) -> Any:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError("JSON object contains a duplicate key")
            result[key] = value
        return result

    def reject(value):
        raise ValueError(f"JSON contains a non-finite value: {value}")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=reject)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Jiaoch risk response was not valid JSON") from exc


def _date(value: Any, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip()
    if optional and not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical date")
    return text


def _load_universe(path: str | Path, as_of: str) -> tuple[str, set[str], dict[str, Any]]:
    descriptor_path = Path(path)
    payload = _strict_json_loads(descriptor_path.read_bytes())
    if not isinstance(payload, dict) or payload.get("schema") != "current-pool-universe-input/v1":
        raise ValueError("verified universe descriptor required")
    verified = verify_current_pool_universe_descriptor(payload)
    digest = verified["descriptor_sha256"]
    if len(descriptor_path.stem) == 64 and descriptor_path.stem != digest:
        raise ValueError("verified universe descriptor required")
    if payload.get("as_of") != as_of or payload.get("source_id") != "jiaoch":
        raise ValueError("risk snapshot must match universe as_of")
    try:
        retrieved = datetime.fromisoformat(str(payload.get("retrieved_at") or ""))
    except ValueError as exc:
        raise ValueError("verified universe descriptor required") from exc
    if (
        retrieved.tzinfo is None
        or retrieved.utcoffset() is None
        or retrieved.utcoffset().total_seconds() != 8 * 3600
        or retrieved.date().isoformat() != as_of
        or payload.get("partition_coverage")
        != {
            "exchanges": ["SSE", "SZSE"],
            "list_statuses": ["L", "D", "P", "G"],
            "partition_count": 8,
        }
        or payload.get("risk_snapshot_complete") is not False
        or payload.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("verified universe descriptor required")
    items = payload.get("items")
    if not isinstance(items, list):
        raise ValueError("verified universe descriptor required")
    symbols = {str(item.get("ts_code") or "") for item in items if isinstance(item, dict)}
    if len(symbols) != len(items) or "" in symbols:
        raise ValueError("verified universe descriptor required")
    return digest, symbols, payload


def _rows(envelope: Mapping[str, Any], fields: tuple[str, ...], api_name: str) -> list[dict[str, Any]]:
    if not isinstance(envelope, Mapping) or type(envelope.get("code")) is not int or envelope.get("code") != 0:
        raise ValueError(f"{api_name} returned a non-success envelope")
    data = envelope.get("data")
    if not isinstance(data, Mapping) or data.get("fields") != list(fields) or not isinstance(data.get("items"), list):
        raise ValueError(f"{api_name} response schema mismatch")
    items = data["items"]
    if len(items) >= PARTITION_ROW_CAP:
        raise ValueError(f"{api_name} partition reached its row cap")
    result = []
    for values in items:
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError(f"{api_name} response contains an invalid row")
        result.append(dict(zip(fields, values, strict=True)))
    return result


def _write(output_dir: str | Path, payload: dict[str, Any], universe_payload: Mapping[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    descriptor = {**payload, "descriptor_sha256": digest}
    verify_current_pool_risk_descriptor(descriptor, universe_payload)
    content = (json.dumps(descriptor, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{digest}.json"
    created = not destination.exists()
    if created:
        fd, temporary = tempfile.mkstemp(prefix=f"{digest}.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
            fsync_directory(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif destination.read_bytes() != content:
        raise ValueError("content-addressed risk descriptor mismatch")
    return {"path": str(destination), "descriptor_sha256": digest, "created": created}


def verify_current_pool_risk_descriptor(payload: Mapping[str, Any], universe_payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA_VERSION or payload.get("source_id") != "jiaoch":
        raise ValueError("current-pool risk descriptor rejected")
    embedded = payload.get("descriptor_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "descriptor_sha256"}
    digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if embedded != digest:
        raise ValueError("current-pool risk descriptor rejected")
    as_of = _date(payload.get("as_of"), "as_of")
    try:
        retrieved = datetime.fromisoformat(str(payload.get("retrieved_at") or ""))
    except ValueError as exc:
        raise ValueError("current-pool risk descriptor rejected") from exc
    if (
        retrieved.tzinfo is None
        or retrieved.utcoffset() is None
        or retrieved.utcoffset().total_seconds() != 8 * 3600
        or retrieved.date().isoformat() != as_of
    ):
        raise ValueError("current-pool risk descriptor rejected")
    year_count = int(as_of[:4]) - 1989
    expected = [("stock_st", {"trade_date": as_of.replace("-", "")}), ("suspend_d", {"trade_date": as_of.replace("-", "")})]
    for year in range(1990, int(as_of[:4]) + 1):
        expected.append(("namechange", {"start_date": f"{year}0101", "end_date": min(date(year, 12, 31), date.fromisoformat(as_of)).strftime("%Y%m%d")}))
    receipts = payload.get("partition_receipts")
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        raise ValueError("current-pool risk descriptor rejected")
    for receipt, (api_name, params) in zip(receipts, expected, strict=True):
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != {"api_name", "params", "row_count", "rows_sha256"}
            or receipt.get("api_name") != api_name
            or receipt.get("params") != params
            or type(receipt.get("row_count")) is not int
            or not 0 <= receipt["row_count"] < PARTITION_ROW_CAP
            or not isinstance(receipt.get("rows_sha256"), str)
            or len(receipt["rows_sha256"]) != 64
            or any(char not in _HEX for char in receipt["rows_sha256"])
        ):
            raise ValueError("current-pool risk descriptor rejected")
    if payload.get("partition_coverage") != {"stock_st": 1, "suspend_d": 1, "namechange": year_count, "partition_count": year_count + 2} or payload.get("risk_snapshot_complete") is not True or payload.get("risk_gate_passed") is not True or payload.get("production_recommendation_eligible") is not False:
        raise ValueError("current-pool risk descriptor rejected")
    items = payload.get("items")
    required = {"ts_code", "is_st", "st_type", "is_suspended", "suspension_reason", "active_name"}
    if not isinstance(items, list):
        raise ValueError("current-pool risk descriptor rejected")
    symbols = set()
    for item in items:
        if not isinstance(item, Mapping) or set(item) != required or not isinstance(item.get("ts_code"), str) or item["ts_code"] in symbols:
            raise ValueError("current-pool risk descriptor rejected")
        symbols.add(item["ts_code"])
        st_type_present = isinstance(item.get("st_type"), str) and bool(item["st_type"].strip())
        if type(item.get("is_st")) is not bool or item["is_st"] != (
            st_type_present or is_st_risk_name(item.get("active_name"))
        ):
            raise ValueError("current-pool risk descriptor rejected")
        expected_reasons = {
            False: {None},
            True: {"suspended", "resume_day_no_new_entry", "conflict"},
        }
        if type(item.get("is_suspended")) is not bool or item.get("suspension_reason") not in expected_reasons[item["is_suspended"]]:
            raise ValueError("current-pool risk descriptor rejected")
        if item.get("active_name") is not None and (not isinstance(item["active_name"], str) or not item["active_name"].strip()):
            raise ValueError("current-pool risk descriptor rejected")
    if universe_payload is not None:
        universe = verify_current_pool_universe_descriptor(universe_payload)
        if payload.get("universe_descriptor_sha256") != universe["descriptor_sha256"] or symbols != universe["symbols"]:
            raise ValueError("current-pool risk descriptor rejected")
    return {"descriptor_sha256": digest, "symbols": symbols}


def build_current_pool_risk_descriptor(*, as_of: str, retrieved_at: str, universe_path: str | Path, output_dir: str | Path, fetch_partition: PartitionFetcher) -> dict[str, Any]:
    canonical_as_of = _date(as_of, "as_of")
    try:
        retrieved = datetime.fromisoformat(retrieved_at)
    except ValueError as exc:
        raise ValueError("retrieved_at must be an aware Shanghai timestamp") from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() is None or retrieved.utcoffset().total_seconds() != 8 * 3600:
        raise ValueError("retrieved_at must be an aware Shanghai timestamp")
    if retrieved.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() != canonical_as_of:
        raise ValueError("retrieved_at must belong to as_of collection day")
    universe_sha, universe, universe_payload = _load_universe(universe_path, canonical_as_of)
    compact = canonical_as_of.replace("-", "")
    receipts = []

    def collect(api_name: str, params: dict[str, str], fields: tuple[str, ...]):
        rows = _rows(fetch_partition(api_name, params, fields), fields, api_name)
        receipts.append({"api_name": api_name, "params": params, "row_count": len(rows), "rows_sha256": hashlib.sha256(_canonical_json(rows)).hexdigest()})
        return rows

    st_rows = collect("stock_st", {"trade_date": compact}, _STOCK_ST_FIELDS)
    suspend_rows = collect("suspend_d", {"trade_date": compact}, _SUSPEND_FIELDS)
    name_rows = []
    seen_name_rows = set()
    for year in range(1990, int(canonical_as_of[:4]) + 1):
        start_param = f"{year}0101"
        end_param = min(date(year, 12, 31), date.fromisoformat(canonical_as_of)).strftime("%Y%m%d")
        partition = collect("namechange", {"start_date": start_param, "end_date": end_param}, _NAMECHANGE_FIELDS)
        for row in partition:
            start = _date(row["start_date"], "start_date")
            if not (start_param <= start.replace("-", "") <= end_param):
                raise ValueError("namechange start_date does not belong to requested partition")
            identity = _canonical_json(row)
            if identity in seen_name_rows:
                raise ValueError("duplicate namechange row across partitions")
            seen_name_rows.add(identity)
        name_rows.extend(partition)

    flags = {symbol: {"ts_code": symbol, "is_st": False, "st_type": None, "is_suspended": False, "suspension_reason": None, "active_name": None} for symbol in universe}
    for row in st_rows:
        symbol = str(row["ts_code"] or "").strip()
        if symbol not in universe:
            raise ValueError("risk response contained unknown symbol")
        if _date(row["trade_date"], "trade_date") != canonical_as_of:
            raise ValueError("stock_st returned wrong trade date")
        st_type = str(row["type"] or "").strip()
        type_name = str(row["type_name"] or "").strip()
        name = str(row["name"] or "").strip()
        if not st_type or not type_name or not name:
            raise ValueError("stock_st row is missing risk identity")
        if flags[symbol]["is_st"]:
            raise ValueError("duplicate stock_st symbol")
        flags[symbol]["is_st"] = True
        flags[symbol]["st_type"] = st_type
    suspend_events: dict[str, set[str]] = {}
    for row in suspend_rows:
        symbol = str(row["ts_code"] or "").strip()
        if symbol not in universe:
            raise ValueError("risk response contained unknown symbol")
        if _date(row["trade_date"], "trade_date") != canonical_as_of:
            raise ValueError("suspend_d returned wrong trade date")
        kind = str(row["suspend_type"] or "").strip()
        if kind not in {"S", "R"}:
            raise ValueError("suspend_d returned unknown suspend_type")
        suspend_events.setdefault(symbol, set()).add(kind)
    for symbol, events in suspend_events.items():
        if events == {"S", "R"}:
            flags[symbol]["is_suspended"] = True
            flags[symbol]["suspension_reason"] = "conflict"
        elif events == {"S"}:
            flags[symbol]["is_suspended"] = True
            flags[symbol]["suspension_reason"] = "suspended"
        elif events == {"R"}:
            flags[symbol]["is_suspended"] = True
            flags[symbol]["suspension_reason"] = "resume_day_no_new_entry"
    for row in name_rows:
        symbol = str(row["ts_code"] or "").strip()
        if symbol not in universe:
            raise ValueError("risk response contained unknown symbol")
        start = _date(row["start_date"], "start_date")
        end = _date(row["end_date"], "end_date", optional=True)
        _date(row["ann_date"], "ann_date", optional=True)
        if start <= canonical_as_of and (end is None or end >= canonical_as_of):
            name = str(row["name"] or "").strip()
            if not name:
                raise ValueError("active namechange name is missing")
            if flags[symbol]["active_name"] not in {None, name}:
                raise ValueError("multiple active names for symbol")
            flags[symbol]["active_name"] = name
            if is_st_risk_name(name):
                flags[symbol]["is_st"] = True
    payload = {
        "schema": SCHEMA_VERSION,
        "source_id": "jiaoch",
        "as_of": canonical_as_of,
        "retrieved_at": retrieved_at,
        "universe_descriptor_sha256": universe_sha,
        "items": [flags[key] for key in sorted(flags)],
        "partition_receipts": receipts,
        "partition_coverage": {"stock_st": 1, "suspend_d": 1, "namechange": int(canonical_as_of[:4]) - 1989, "partition_count": int(canonical_as_of[:4]) - 1987},
        "risk_snapshot_complete": True,
        "risk_gate_passed": True,
        "production_recommendation_eligible": False,
    }
    return _write(output_dir, payload, universe_payload)


def _contains(value: Any, token: str) -> bool:
    if isinstance(value, str):
        return token in value
    if isinstance(value, Mapping):
        return any(_contains(k, token) or _contains(v, token) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains(v, token) for v in value)
    return False


def _fetch_jiaoch_current_pool_risk_descriptor(*, as_of: str, universe_path: str | Path, output_dir: str | Path, timeout_seconds: float, now_provider: Callable[[], datetime] | None) -> dict[str, Any]:
    if not isinstance(timeout_seconds, (int, float)) or isinstance(timeout_seconds, bool) or not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be finite and positive")
    now = (now_provider or (lambda: datetime.now(ZoneInfo("Asia/Shanghai"))))()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("collection clock must return an aware datetime")
    retrieved = now.astimezone(ZoneInfo("Asia/Shanghai"))
    if as_of != retrieved.date().isoformat():
        raise ValueError("--as-of must equal the Shanghai collection day")
    source = resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=False)
    if not source.api_url.startswith("https://"):
        raise ValueError("Jiaoch HTTPS endpoint required")
    transport = UrllibTushareTransport(proxy_url=source.proxy_url)

    def fetch(api_name: str, params: dict[str, str], fields: tuple[str, ...]) -> Mapping[str, Any]:
        body = _canonical_json({"api_name": api_name, "token": source.token, "params": params, "fields": ",".join(fields)})
        response = transport.post(url=f"{source.api_url.rstrip('/')}/{api_name}", headers={"Accept": "application/json", "Accept-Encoding": "identity", "Connection": "close", "Content-Type": "application/json; charset=utf-8", "User-Agent": "quant-current-pool-risk-collector/1"}, body=body, timeout_s=timeout_seconds, max_body_bytes=_MAX_BODY_BYTES)
        if response.status != 200 or not response.body_complete:
            raise ValueError("Jiaoch risk transport response was not complete")
        if source.token.encode() in response.body:
            raise ValueError("Jiaoch response contained a credential and was discarded")
        envelope = _strict_json_loads(response.body)
        if _contains(envelope, source.token):
            raise ValueError("Jiaoch response contained a credential and was discarded")
        if not isinstance(envelope, Mapping):
            raise ValueError("Jiaoch risk response envelope was invalid")
        return envelope

    return build_current_pool_risk_descriptor(as_of=as_of, retrieved_at=retrieved.isoformat(), universe_path=universe_path, output_dir=output_dir, fetch_partition=fetch)


def fetch_jiaoch_current_pool_risk_descriptor(*, as_of: str, universe_path: str | Path, output_dir: str | Path, timeout_seconds: float = 30.0, now_provider: Callable[[], datetime] | None = None) -> dict[str, Any]:
    try:
        return _fetch_jiaoch_current_pool_risk_descriptor(as_of=as_of, universe_path=universe_path, output_dir=output_dir, timeout_seconds=timeout_seconds, now_provider=now_provider)
    except Exception as exc:
        # __cause__ 必须为 None:异常 message 可能包含 token,通过异常链泄露会破坏
        # token 永不落盘的契约。只暴露异常类型名供运维诊断(clock gate / TLS / 超时等)。
        raise CurrentPoolRiskSourceError(
            f"current-pool risk source collection failed ({type(exc).__name__})"
        ) from None
