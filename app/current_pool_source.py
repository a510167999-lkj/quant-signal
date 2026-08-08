"""Fetch a Jiaoch stock master snapshot for current-pool development research.

This artifact deliberately does not claim that ST/name-change/suspension risk
coverage is complete.  It is an input to the separate current-pool audit, not
an authority for production recommendations.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from app.durable_io import fsync_directory
from app.research_pit_contracts import (
    CURRENT_POOL_EXCLUDED_IDENTITY_ALLOWLIST,
    CURRENT_POOL_MARKET_EVIDENCE_VALUES,
    CURRENT_POOL_UNIVERSE_RECEIPT_FIELDS,
    CURRENT_POOL_UNIVERSE_ITEM_FIELDS,
    CURRENT_POOL_UNIVERSE_UNSIGNED_FIELDS,
    STOCK_BASIC_FIELDS,
)
from app.research_pit_sources import resolve_tushare_source
from app.research_pit_transport import UrllibTushareTransport


SCHEMA_VERSION = "current-pool-universe-input/v1"
CURRENT_POOL_UNIVERSE_SCHEMA_V2 = "current-pool-universe-input/v2"
CURRENT_POOL_UNIVERSE_SCHEMAS = frozenset(
    {SCHEMA_VERSION, CURRENT_POOL_UNIVERSE_SCHEMA_V2}
)
_EXCHANGES = ("SSE", "SZSE")
_V2_EXCHANGES = ("SSE", "SZSE", "BSE")
_SCHEMA_EXCHANGES = {
    SCHEMA_VERSION: _EXCHANGES,
    CURRENT_POOL_UNIVERSE_SCHEMA_V2: _V2_EXCHANGES,
}
_EXCHANGE_SUFFIXES = {"SSE": ".SH", "SZSE": ".SZ", "BSE": ".BJ"}
_LIST_STATUSES = ("L", "D", "P", "G")
_MAX_BODY_BYTES = 5 * 1024 * 1024
_PARTITION_ROW_CAP = 6_000
_MARKET_PREFIXES = {
    "主板": {
        "SSE": ("600", "601", "603", "605"),
        "SZSE": ("000", "001", "002", "003"),
    },
    "创业板": {"SZSE": ("300", "301", "302")},
    "科创板": {"SSE": ("688", "689")},
}

PartitionFetcher = Callable[[str, str, tuple[str, ...]], Mapping[str, Any]]
_HEX = frozenset("0123456789abcdef")


def _exchanges_for_schema(schema: Any) -> tuple[str, ...]:
    if not isinstance(schema, str):
        raise ValueError("current-pool universe descriptor rejected")
    exchanges = _SCHEMA_EXCHANGES.get(schema)
    if exchanges is None:
        raise ValueError("current-pool universe descriptor rejected")
    return exchanges


def current_pool_universe_partition_coverage(schema: Any) -> dict[str, Any]:
    exchanges = _exchanges_for_schema(schema)
    return {
        "exchanges": list(exchanges),
        "list_statuses": list(_LIST_STATUSES),
        "partition_count": len(exchanges) * len(_LIST_STATUSES),
    }


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _strict_json_loads(raw: bytes) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON object contains a duplicate key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"JSON contains a non-finite value: {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Jiaoch stock_basic response was not valid JSON") from exc


def _contains_semantic_token(value: Any, token: str) -> bool:
    if not token:
        return False
    if isinstance(value, str):
        return token in value
    if isinstance(value, Mapping):
        return any(
            _contains_semantic_token(key, token) or _contains_semantic_token(item, token)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_semantic_token(item, token) for item in value)
    return False


def _canonical_date(value: str, field: str, *, optional: bool = False) -> str | None:
    text = str(value or "").strip()
    if optional and not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field} must be a canonical ISO date") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"{field} must be a canonical ISO date")
    return text


def _rows_from_envelope(
    envelope: Mapping[str, Any], requested_fields: tuple[str, ...]
) -> list[dict[str, Any]]:
    code = envelope.get("code") if isinstance(envelope, Mapping) else None
    if isinstance(code, bool) or not isinstance(code, int) or code != 0:
        raise ValueError("stock_basic source returned a non-success envelope")
    data = envelope.get("data")
    if not isinstance(data, Mapping):
        raise ValueError("stock_basic source response data is missing")
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, list) or not all(isinstance(field, str) for field in fields):
        raise ValueError("stock_basic response is missing requested fields")
    if any(field not in fields for field in requested_fields):
        raise ValueError("stock_basic response is missing requested fields")
    if len(fields) != len(set(fields)):
        raise ValueError("stock_basic response contains duplicate fields")
    if not isinstance(items, list):
        raise ValueError("stock_basic response items are missing")
    if len(items) > _PARTITION_ROW_CAP:
        raise ValueError("stock_basic partition exceeded its row cap")
    positions = {field: fields.index(field) for field in requested_fields}
    rows: list[dict[str, Any]] = []
    for values in items:
        if not isinstance(values, list) or len(values) != len(fields):
            raise ValueError("stock_basic response contains an invalid row")
        rows.append({field: values[index] for field, index in positions.items()})
    return rows


def _market_from_exchange_and_symbol(exchange: str, symbol: str) -> str | None:
    if exchange == "BSE":
        return "北交所" if symbol.startswith(("4", "8", "9")) else None
    matches = [
        market
        for market, exchange_prefixes in _MARKET_PREFIXES.items()
        if symbol.startswith(exchange_prefixes.get(exchange, ()))
    ]
    return matches[0] if len(matches) == 1 else None


def _is_six_ascii_digits(value: str) -> bool:
    return len(value) == 6 and all(character in "0123456789" for character in value)


def _canonical_excluded_identity_row(
    row: Mapping[str, Any], *, exchange: str, list_status: str
) -> dict[str, Any]:
    """Validate the one narrowly excludable provider identity defect.

    The historical Jiaoch/Tushare stock master contains exactly two observed
    delisted identities with a legacy alphabetic prefix.  They are public
    source rows, not current securities.  Only their complete, audited
    fingerprints are excludable; every future unknown value remains
    fail-closed until the allowlist is deliberately updated.
    """

    if set(row) != set(STOCK_BASIC_FIELDS):
        raise ValueError("stock_basic item identity is inconsistent")
    string_fields = ("ts_code", "symbol", "name", "exchange", "list_status")
    if any(type(row.get(field)) is not str for field in string_fields):
        raise ValueError("stock_basic item identity is inconsistent")
    if row.get("market") is not None and type(row.get("market")) is not str:
        raise ValueError("stock_basic item identity is inconsistent")
    if any(
        row.get(field) is not None and type(row.get(field)) is not str
        for field in ("list_date", "delist_date")
    ):
        raise ValueError("stock_basic item identity is inconsistent")
    text = {key: str(row.get(key) or "").strip() for key in STOCK_BASIC_FIELDS}
    if any(not text[key] for key in string_fields):
        raise ValueError("stock_basic item identity is inconsistent")
    fingerprint = (
        row["ts_code"],
        row["symbol"],
        row["exchange"],
        row["list_status"],
        row["name"],
        row["market"],
    )
    if (
        fingerprint not in CURRENT_POOL_EXCLUDED_IDENTITY_ALLOWLIST
        or text["exchange"] != exchange
        or text["list_status"] != list_status
    ):
        raise ValueError("stock_basic item identity is inconsistent")
    expected_suffix = _EXCHANGE_SUFFIXES[exchange]
    if (
        not text["ts_code"].endswith(expected_suffix)
        or text["ts_code"][: -len(expected_suffix)] != text["symbol"]
        or _is_six_ascii_digits(text["symbol"])
    ):
        raise ValueError("stock_basic item identity is inconsistent")
    _canonical_date(text["list_date"], "list_date", optional=True)
    _canonical_date(text["delist_date"], "delist_date", optional=True)
    # Ensure the public row is JSON-canonicalizable before it is admitted as
    # auditable evidence.  Preserve the wire values exactly in the receipt.
    public_row = {field: row[field] for field in STOCK_BASIC_FIELDS}
    _canonical_json(public_row)
    return public_row


def _excludable_nonlisted_identity(
    row: Mapping[str, Any], *, exchange: str, list_status: str
) -> dict[str, Any] | None:
    symbol = str(row.get("symbol") or "").strip()
    if _is_six_ascii_digits(symbol):
        return None
    if list_status == "L":
        return None
    return _canonical_excluded_identity_row(
        row, exchange=exchange, list_status=list_status
    )


def _normalize_item(
    row: Mapping[str, Any], *, exchanges: tuple[str, ...] = _EXCHANGES
) -> dict[str, Any]:
    text = {key: str(row.get(key) or "").strip() for key in STOCK_BASIC_FIELDS}
    required = ("ts_code", "symbol", "name", "exchange", "list_status")
    if any(not text[key] for key in required):
        raise ValueError("stock_basic item is missing identity or status fields")
    if text["exchange"] not in exchanges or text["list_status"] not in _LIST_STATUSES:
        raise ValueError("stock_basic item has an unexpected exchange or list status")
    expected_suffix = _EXCHANGE_SUFFIXES[text["exchange"]]
    if (
        not _is_six_ascii_digits(text["symbol"])
        or not text["ts_code"].endswith(expected_suffix)
        or text["ts_code"][: -len(expected_suffix)] != text["symbol"]
    ):
        raise ValueError("stock_basic item identity is inconsistent")
    market = text["market"]
    market_evidence = "provider"
    if not market:
        if text["list_status"] == "L":
            raise ValueError("stock_basic item is missing identity or status fields")
        market = _market_from_exchange_and_symbol(text["exchange"], text["symbol"])
        if market is None:
            raise ValueError("stock_basic nonlisted market cannot be inferred")
        market_evidence = "inferred_nonlisted_code"
    return {
        "ts_code": text["ts_code"],
        "symbol": text["symbol"],
        "name": text["name"],
        "market": market,
        "exchange": text["exchange"],
        "list_status": text["list_status"],
        "list_date": _canonical_date(text["list_date"], "list_date", optional=True),
        "delist_date": _canonical_date(text["delist_date"], "delist_date", optional=True),
        "market_evidence": market_evidence,
    }


def _normalize_descriptor_item(
    item: Mapping[str, Any], *, exchanges: tuple[str, ...]
) -> dict[str, Any]:
    if set(item) != set(CURRENT_POOL_UNIVERSE_ITEM_FIELDS):
        raise ValueError("current-pool universe descriptor rejected")
    evidence = item.get("market_evidence")
    if evidence not in CURRENT_POOL_MARKET_EVIDENCE_VALUES:
        raise ValueError("current-pool universe descriptor rejected")
    provider_view = {key: item.get(key) for key in STOCK_BASIC_FIELDS}
    if evidence == "inferred_nonlisted_code":
        provider_view["market"] = ""
    try:
        normalized = _normalize_item(provider_view, exchanges=exchanges)
    except (TypeError, ValueError) as exc:
        raise ValueError("current-pool universe descriptor rejected") from exc
    if normalized != dict(item):
        raise ValueError("current-pool universe descriptor rejected")
    return normalized


def verify_current_pool_universe_descriptor(payload: Mapping[str, Any]) -> dict[str, Any]:
    schema = payload.get("schema") if isinstance(payload, Mapping) else None
    try:
        exchanges = _exchanges_for_schema(schema)
    except ValueError:
        raise ValueError("current-pool universe descriptor rejected") from None
    if (
        not isinstance(payload, Mapping)
        or set(payload)
        != {*CURRENT_POOL_UNIVERSE_UNSIGNED_FIELDS, "descriptor_sha256"}
        or payload.get("source_id") != "jiaoch"
    ):
        raise ValueError("current-pool universe descriptor rejected")
    embedded = payload.get("descriptor_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "descriptor_sha256"}
    digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if embedded != digest:
        raise ValueError("current-pool universe descriptor rejected")
    if payload.get("partition_coverage") != current_pool_universe_partition_coverage(schema):
        raise ValueError("current-pool universe descriptor rejected")
    expected = [(exchange, status) for exchange in exchanges for status in _LIST_STATUSES]
    receipts = payload.get("partition_receipts")
    if not isinstance(receipts, list) or len(receipts) != len(expected):
        raise ValueError("current-pool universe descriptor rejected")
    for receipt, (exchange, status) in zip(receipts, expected, strict=True):
        if (
            not isinstance(receipt, Mapping)
            or set(receipt) != set(CURRENT_POOL_UNIVERSE_RECEIPT_FIELDS)
            or receipt.get("api_name") != "stock_basic"
            or receipt.get("params") != {"exchange": exchange, "list_status": status}
            or type(receipt.get("raw_row_count")) is not int
            or not 0 <= receipt["raw_row_count"] <= _PARTITION_ROW_CAP
            or type(receipt.get("normalized_row_count")) is not int
            or not 0 <= receipt["normalized_row_count"] <= receipt["raw_row_count"]
            or type(receipt.get("excluded_invalid_identity_count")) is not int
            or not 0
            <= receipt["excluded_invalid_identity_count"]
            <= receipt["raw_row_count"]
            or not isinstance(receipt.get("rows_sha256"), str)
            or len(receipt["rows_sha256"]) != 64
            or any(char not in _HEX for char in receipt["rows_sha256"])
            or not isinstance(receipt.get("excluded_rows_sha256"), str)
            or len(receipt["excluded_rows_sha256"]) != 64
            or any(char not in _HEX for char in receipt["excluded_rows_sha256"])
            or not isinstance(receipt.get("excluded_invalid_identity_rows"), list)
        ):
            raise ValueError("current-pool universe descriptor rejected")
        excluded_rows = receipt["excluded_invalid_identity_rows"]
        if (
            receipt["raw_row_count"]
            != receipt["normalized_row_count"]
            + receipt["excluded_invalid_identity_count"]
            or receipt["excluded_invalid_identity_count"] != len(excluded_rows)
            or (status == "L" and excluded_rows)
        ):
            raise ValueError("current-pool universe descriptor rejected")
        try:
            canonical_excluded = [
                _canonical_excluded_identity_row(
                    row, exchange=exchange, list_status=status
                )
                for row in excluded_rows
            ]
        except (KeyError, TypeError, ValueError):
            raise ValueError("current-pool universe descriptor rejected") from None
        if (
            canonical_excluded != sorted(canonical_excluded, key=_canonical_json)
            or len({_canonical_json(row) for row in canonical_excluded})
            != len(canonical_excluded)
            or receipt["excluded_rows_sha256"]
            != hashlib.sha256(_canonical_json(canonical_excluded)).hexdigest()
        ):
            raise ValueError("current-pool universe descriptor rejected")
    items = payload.get("items")
    if (
        not isinstance(items, list)
        or sum(receipt["normalized_row_count"] for receipt in receipts) != len(items)
        or type(payload.get("excluded_invalid_identity_count")) is not int
        or payload["excluded_invalid_identity_count"]
        != sum(receipt["excluded_invalid_identity_count"] for receipt in receipts)
    ):
        raise ValueError("current-pool universe descriptor rejected")
    seen = set()
    normalized_items = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ValueError("current-pool universe descriptor rejected")
        normalized = _normalize_descriptor_item(item, exchanges=exchanges)
        if normalized["ts_code"] in seen:
            raise ValueError("current-pool universe descriptor rejected")
        seen.add(normalized["ts_code"])
        normalized_items.append(normalized)
        symbol, exchange, market = normalized["symbol"], normalized["exchange"], normalized["market"]
        if (
            (exchange == "SSE" and not symbol.startswith("6"))
            or (exchange == "SZSE" and not symbol.startswith(("0", "3")))
            or (exchange == "BSE" and not symbol.startswith(("4", "8", "9")))
        ):
            raise ValueError("current-pool universe descriptor rejected")
        if exchange == "BSE" and market != "北交所":
            raise ValueError("current-pool universe descriptor rejected")
        board_prefixes = {
            market_name: tuple(
                prefix
                for prefixes in exchange_prefixes.values()
                for prefix in prefixes
            )
            for market_name, exchange_prefixes in _MARKET_PREFIXES.items()
        }
        if market in board_prefixes and not symbol.startswith(board_prefixes[market]):
            raise ValueError("current-pool universe descriptor rejected")
    if normalized_items != sorted(
        normalized_items, key=lambda item: item["ts_code"]
    ):
        raise ValueError("current-pool universe descriptor rejected")
    for receipt in receipts:
        params = receipt["params"]
        partition_items = sorted(
            (
                dict(item)
                for item in items
                if item["exchange"] == params["exchange"]
                and item["list_status"] == params["list_status"]
            ),
            key=lambda item: item["ts_code"],
        )
        if (
            receipt["normalized_row_count"] != len(partition_items)
            or receipt["rows_sha256"]
            != hashlib.sha256(_canonical_json(partition_items)).hexdigest()
        ):
            raise ValueError("current-pool universe descriptor rejected")
    return {"descriptor_sha256": digest, "symbols": seen}


def _write_content_addressed(output_dir: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    descriptor = {**payload, "descriptor_sha256": digest}
    verify_current_pool_universe_descriptor(descriptor)
    content = (
        json.dumps(descriptor, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
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
            fsync_directory(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    elif destination.read_bytes() != content:
        raise ValueError("content-addressed current-pool descriptor mismatch")
    return {"path": str(destination), "descriptor_sha256": digest, "created": created}


def _build_current_pool_descriptor(
    *,
    as_of: str,
    retrieved_at: str,
    output_dir: str | Path,
    fetch_partition: PartitionFetcher,
    schema: str,
    exchanges: tuple[str, ...],
) -> dict[str, Any]:
    if not isinstance(as_of, str) or len(as_of) != 10:
        raise ValueError("as_of must be a canonical ISO date")
    canonical_as_of = _canonical_date(as_of, "as_of")
    canonical_retrieved_at = str(retrieved_at)
    try:
        retrieved = datetime.fromisoformat(canonical_retrieved_at)
    except ValueError as exc:
        raise ValueError("retrieved_at must be an aware ISO timestamp") from exc
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        raise ValueError("retrieved_at must be an aware ISO timestamp")
    if retrieved.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() != canonical_as_of:
        raise ValueError("retrieved_at must belong to the as_of collection day")
    rows: list[dict[str, Any]] = []
    receipts = []
    for exchange in exchanges:
        for list_status in _LIST_STATUSES:
            envelope = fetch_partition(exchange, list_status, STOCK_BASIC_FIELDS)
            partition_rows = _rows_from_envelope(envelope, STOCK_BASIC_FIELDS)
            normalized_partition = []
            excluded_partition = []
            for row in partition_rows:
                excluded = _excludable_nonlisted_identity(
                    row, exchange=exchange, list_status=list_status
                )
                if excluded is not None:
                    excluded_partition.append(excluded)
                    continue
                normalized = _normalize_item(row, exchanges=exchanges)
                if normalized["exchange"] != exchange or normalized["list_status"] != list_status:
                    raise ValueError("stock_basic item does not match its requested partition")
                rows.append(normalized)
                normalized_partition.append(normalized)
            normalized_partition.sort(key=lambda item: item["ts_code"])
            excluded_partition.sort(key=_canonical_json)
            receipts.append(
                {
                    "api_name": "stock_basic",
                    "params": {"exchange": exchange, "list_status": list_status},
                    "raw_row_count": len(partition_rows),
                    "normalized_row_count": len(normalized_partition),
                    "rows_sha256": hashlib.sha256(
                        _canonical_json(normalized_partition)
                    ).hexdigest(),
                    "excluded_invalid_identity_count": len(excluded_partition),
                    "excluded_invalid_identity_rows": excluded_partition,
                    "excluded_rows_sha256": hashlib.sha256(
                        _canonical_json(excluded_partition)
                    ).hexdigest(),
                }
            )
    if len(rows) > len(exchanges) * len(_LIST_STATUSES) * _PARTITION_ROW_CAP:
        raise ValueError("stock_basic collection exceeded its total row cap")
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        ts_code = row["ts_code"]
        if ts_code in items:
            raise ValueError(f"duplicate stock_basic ts_code across partitions: {ts_code}")
        items[ts_code] = row
    payload = {
        "schema": schema,
        "source_id": "jiaoch",
        "as_of": canonical_as_of,
        "retrieved_at": canonical_retrieved_at,
        "items": [items[key] for key in sorted(items)],
        "partition_receipts": receipts,
        "excluded_invalid_identity_count": sum(
            receipt["excluded_invalid_identity_count"] for receipt in receipts
        ),
        "partition_coverage": current_pool_universe_partition_coverage(schema),
        "risk_snapshot_complete": False,
        "risk_coverage": {
            "stock_st": "not_collected",
            "namechange": "not_collected",
            "suspend_d": "not_collected",
        },
        "production_recommendation_eligible": False,
    }
    return _write_content_addressed(output_dir, payload)


def build_current_pool_descriptor(
    *,
    as_of: str,
    retrieved_at: str,
    output_dir: str | Path,
    fetch_partition: PartitionFetcher,
) -> dict[str, Any]:
    return _build_current_pool_descriptor(
        as_of=as_of,
        retrieved_at=retrieved_at,
        output_dir=output_dir,
        fetch_partition=fetch_partition,
        schema=SCHEMA_VERSION,
        exchanges=_EXCHANGES,
    )


def build_current_pool_descriptor_v2(
    *,
    as_of: str,
    retrieved_at: str,
    output_dir: str | Path,
    fetch_partition: PartitionFetcher,
) -> dict[str, Any]:
    return _build_current_pool_descriptor(
        as_of=as_of,
        retrieved_at=retrieved_at,
        output_dir=output_dir,
        fetch_partition=fetch_partition,
        schema=CURRENT_POOL_UNIVERSE_SCHEMA_V2,
        exchanges=_V2_EXCHANGES,
    )


def _fetch_jiaoch_current_pool_descriptor(
    *,
    as_of: str,
    output_dir: str | Path,
    timeout_seconds: float = 30.0,
    now_provider: Callable[[], datetime] | None = None,
    build_descriptor: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    now = (now_provider or (lambda: datetime.now(ZoneInfo("Asia/Shanghai"))))()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("collection clock must return an aware datetime")
    retrieved = now.astimezone(ZoneInfo("Asia/Shanghai"))
    collection_day = retrieved.date().isoformat()
    if as_of != collection_day:
        raise ValueError("--as-of must equal the Shanghai collection day")
    source = resolve_tushare_source("jiaoch", api_url=None, allow_insecure_http=False)
    transport = UrllibTushareTransport(proxy_url=source.proxy_url)

    def fetch_partition(
        exchange: str, list_status: str, fields: tuple[str, ...]
    ) -> Mapping[str, Any]:
        body = _canonical_json(
            {
                "api_name": "stock_basic",
                "token": source.token,
                "params": {"exchange": exchange, "list_status": list_status},
                "fields": ",".join(fields),
            }
        )
        response = transport.post(
            url=f"{source.api_url.rstrip('/')}/stock_basic",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-current-pool-collector/1",
            },
            body=body,
            timeout_s=timeout_seconds,
            max_body_bytes=_MAX_BODY_BYTES,
        )
        if response.status != 200 or not response.body_complete:
            raise ValueError("Jiaoch stock_basic transport response was not complete")
        if source.token.encode("utf-8") in response.body:
            raise ValueError("Jiaoch response contained a credential and was discarded")
        envelope = _strict_json_loads(response.body)
        if _contains_semantic_token(envelope, source.token):
            raise ValueError("Jiaoch response contained a credential and was discarded")
        if not isinstance(envelope, Mapping):
            raise ValueError("Jiaoch stock_basic response envelope was invalid")
        return envelope

    return build_descriptor(
        as_of=collection_day,
        retrieved_at=retrieved.isoformat(),
        output_dir=output_dir,
        fetch_partition=fetch_partition,
    )


def fetch_jiaoch_current_pool_descriptor(
    *,
    as_of: str,
    output_dir: str | Path,
    timeout_seconds: float = 30.0,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return _fetch_jiaoch_current_pool_descriptor(
        as_of=as_of,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        now_provider=now_provider,
        build_descriptor=build_current_pool_descriptor,
    )


def fetch_jiaoch_current_pool_descriptor_v2(
    *,
    as_of: str,
    output_dir: str | Path,
    timeout_seconds: float = 30.0,
    now_provider: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    return _fetch_jiaoch_current_pool_descriptor(
        as_of=as_of,
        output_dir=output_dir,
        timeout_seconds=timeout_seconds,
        now_provider=now_provider,
        build_descriptor=build_current_pool_descriptor_v2,
    )
