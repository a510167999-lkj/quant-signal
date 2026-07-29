"""Sealed, content-addressed SSE trade-calendar authority."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import hmac
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping
import uuid

from app import jiaoch_points_raw_authority as raw_authority
from app.research_pit_transport import UrllibTushareTransport


__all__ = ("verify_jiaoch_trade_cal_authority",)

AUTHORITY_MANIFEST_SCHEMA = "jiaoch-trade-cal-authority/v1"
ATTEMPT_SCHEMA = "jiaoch-trade-cal-raw-attempt/v1"
COLLECTOR_VERSION = "app.jiaoch_trade_cal_authority/1"

_PRODUCER_SCHEMA = "jiaoch-trade-cal-producer/v1"
_COLLECTION_BINDING_SCHEMA = "jiaoch-trade-cal-collection-binding/v1"
_SOURCE_ID = "jiaoch"
_CREDENTIAL_SLOT_ID = "points-primary"
_API_NAME = "trade_cal"
_ENDPOINT = "https://jiaoch.site/trade_cal"
_FIELDS_TEXT = "exchange,cal_date,is_open,pretrade_date"
_FIELDS = ["exchange", "cal_date", "is_open", "pretrade_date"]
_EXCHANGE = "SSE"
_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_ATTEMPT_BYTES = 64 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_PRODUCER_SOURCE_BYTES = 4 * 1024 * 1024
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_UUID4_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_DATE_PATTERN = re.compile(r"[0-9]{8}")
_ATTEMPT_PATH_PATTERN = re.compile(r"trade_cal_attempts/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json")
_MANIFEST_PATH_PATTERN = re.compile(
    r"trade_cal_manifests/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json"
)
_ATTEMPT_FIELDS = frozenset(
    {
        "api_name",
        "attempt_id",
        "authority_status",
        "body_complete",
        "calendar_authority_status",
        "collection_binding",
        "collector_version",
        "credential_echo_check",
        "credential_slot_id",
        "development_session_alignment_verified",
        "development_session_count_claimed",
        "embargo_consumed",
        "endpoint",
        "experiment_launch_eligible",
        "final_oos_consumed",
        "formal_materialization_eligible",
        "http_status",
        "production_profile_registered",
        "production_recommendation_eligible",
        "raw_object",
        "raw_response_persisted",
        "request_semantics",
        "request_semantics_sha256",
        "retrieved_at",
        "route",
        "row_authority_status",
        "rows_published",
        "schema",
        "source_id",
    }
)
_COLLECTION_BINDING_FIELDS = frozenset(
    {
        "auxiliary_policy_sha256",
        "collection_call_id",
        "generation_id",
        "producer_root_sha256",
        "schema",
    }
)
_ATTEMPT_DESCRIPTOR_FIELDS = frozenset(
    {
        "attempt_id",
        "attempt_relative_path",
        "attempt_sha256",
        "raw_relative_path",
        "raw_sha256",
    }
)
_MANIFEST_FIELDS = frozenset(
    {
        "attempt",
        "authority_scope",
        "auxiliary_policy_descriptor",
        "calendar_authority_status",
        "calendar_day_count",
        "calendar_integrity_verified",
        "calendar_rows_root_sha256",
        "collection_call_id",
        "collector_version",
        "credential_binding",
        "credential_slot_id",
        "development_session_alignment_verified",
        "development_session_count_claimed",
        "embargo_consumed",
        "end_date",
        "exchange",
        "experiment_launch_eligible",
        "final_oos_consumed",
        "formal_materialization_eligible",
        "natural_day_coverage_verified",
        "open_session_count",
        "open_sessions",
        "open_sessions_root_sha256",
        "open_sessions_sorted",
        "pretrade_anchor_date",
        "pretrade_chain_verified",
        "producer_binding",
        "production_profile_registered",
        "production_recommendation_eligible",
        "retrieved_at",
        "rows_published",
        "runtime_mapping_descriptor",
        "schema",
        "source_id",
        "start_date",
    }
)
_PRODUCER_FILES = (
    "durable_io.py",
    "jiaoch_credential_slots.py",
    "jiaoch_points_raw_authority.py",
    "jiaoch_trade_cal_authority.py",
    "research_pit_transport.py",
)


def _transport_factory() -> UrllibTushareTransport:
    return UrllibTushareTransport(proxy_url=None)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (RecursionError, TypeError, ValueError):
        raise ValueError("Jiaoch trade calendar canonical JSON rejected") from None


def _strict_json_loads(raw: bytes, *, label: str) -> Any:
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError(f"non-finite JSON value: {value}")

    try:
        return json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (RecursionError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise ValueError(f"{label} JSON rejected") from None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha256_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    return value


def _uuid4_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _UUID4_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    return value


def _date_window(start_value: Any, end_value: Any) -> tuple[date, date]:
    if type(start_value) is not date or type(end_value) is not date or start_value > end_value:
        raise ValueError("Jiaoch trade calendar date window rejected")
    return start_value, end_value


def _yyyymmdd(value: Any, *, label: str) -> date:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    try:
        parsed = datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError(f"{label} rejected") from None
    if parsed.strftime("%Y%m%d") != value:
        raise ValueError(f"{label} rejected")
    return parsed


def _utc_timestamp(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} rejected")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"{label} rejected") from None
    if (
        parsed.tzinfo is None
        or parsed.utcoffset() != timedelta(0)
        or parsed.astimezone(timezone.utc).isoformat() != value
    ):
        raise ValueError(f"{label} rejected")
    return value


def _trusted_utc_timestamp() -> str:
    observed = _utc_now()
    if (
        type(observed) is not datetime
        or observed.tzinfo is None
        or observed.utcoffset() != timedelta(0)
    ):
        raise ValueError("Jiaoch trade calendar private UTC clock rejected")
    return observed.astimezone(timezone.utc).isoformat()


def _timeout_seconds(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) <= 300
    ):
        raise ValueError("Jiaoch trade calendar timeout rejected")
    return float(value)


def _current_auxiliary_policy_descriptor() -> dict[str, Any]:
    from app.jiaoch_credential_slots import _auxiliary_policy_descriptor

    return _auxiliary_policy_descriptor()


def _auxiliary_policy_descriptor(value: Any) -> dict[str, Any]:
    current = _current_auxiliary_policy_descriptor()
    if type(value) is not dict or value != current:
        raise ValueError("Jiaoch trade calendar auxiliary policy rejected")
    if (
        set(value) != {"document", "schema", "sha256"}
        or value["schema"] != "jiaoch-credential-auxiliary-policy-descriptor/v1"
        or not hmac.compare_digest(
            _sha256(_canonical_json(value["document"])),
            _sha256_text(value["sha256"], label="Jiaoch auxiliary policy sha256"),
        )
    ):
        raise ValueError("Jiaoch trade calendar auxiliary policy rejected")
    return value


def _request_semantics(start: date, end: date) -> dict[str, Any]:
    return {
        "api_name": _API_NAME,
        "endpoint": _ENDPOINT,
        "fields": _FIELDS_TEXT,
        "method": "POST",
        "params": {
            "end_date": end.strftime("%Y%m%d"),
            "exchange": _EXCHANGE,
            "start_date": start.strftime("%Y%m%d"),
        },
    }


def _request_dates(request: Any) -> tuple[date, date]:
    if (
        type(request) is not dict
        or set(request) != {"api_name", "endpoint", "fields", "method", "params"}
        or request.get("api_name") != _API_NAME
        or request.get("endpoint") != _ENDPOINT
        or request.get("fields") != _FIELDS_TEXT
        or request.get("method") != "POST"
        or type(request.get("params")) is not dict
        or set(request["params"]) != {"end_date", "exchange", "start_date"}
        or request["params"].get("exchange") != _EXCHANGE
    ):
        raise ValueError("Jiaoch trade calendar request semantics rejected")
    start = _yyyymmdd(
        request["params"].get("start_date"),
        label="Jiaoch trade calendar request start_date",
    )
    end = _yyyymmdd(
        request["params"].get("end_date"),
        label="Jiaoch trade calendar request end_date",
    )
    return _date_window(start, end)


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "quant-jiaoch-trade-cal-authority/1",
    }


def _producer_binding() -> dict[str, Any]:
    source_root = raw_authority._safe_existing_directory(
        Path(__file__).parent,
        "Jiaoch trade calendar producer root",
    )
    entries = []
    for filename in _PRODUCER_FILES:
        raw = raw_authority._read_safe_file(
            source_root / filename,
            label="Jiaoch trade calendar producer source",
            max_bytes=_MAX_PRODUCER_SOURCE_BYTES,
        )
        entries.append({"path": f"app/{filename}", "sha256": _sha256(raw)})
    identity = {
        "collector_version": COLLECTOR_VERSION,
        "entries": entries,
        "schema": _PRODUCER_SCHEMA,
    }
    return {**identity, "root_sha256": _sha256(_canonical_json(identity))}


def _collection_binding(
    *,
    collection_call_id: str,
    generation_id: str,
    auxiliary_policy_sha256: str,
    producer_root_sha256: str,
) -> dict[str, str]:
    return {
        "auxiliary_policy_sha256": _sha256_text(
            auxiliary_policy_sha256,
            label="Jiaoch trade calendar auxiliary policy sha256",
        ),
        "collection_call_id": _uuid4_text(
            collection_call_id,
            label="Jiaoch trade calendar collection_call_id",
        ),
        "generation_id": _uuid4_text(
            generation_id,
            label="Jiaoch trade calendar generation_id",
        ),
        "producer_root_sha256": _sha256_text(
            producer_root_sha256,
            label="Jiaoch trade calendar producer root sha256",
        ),
        "schema": _COLLECTION_BINDING_SCHEMA,
    }


def _raw_relative_path(digest: str) -> str:
    return f"trade_cal_raw/sha256/{digest[:2]}/{digest}.body"


def _attempt_relative_path(digest: str) -> str:
    return f"trade_cal_attempts/sha256/{digest[:2]}/{digest}.json"


def _attempt_descriptor(publication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "attempt_id": publication["attempt_id"],
        "attempt_relative_path": publication["attempt_relative_path"],
        "attempt_sha256": publication["attempt_sha256"],
        "raw_relative_path": publication["raw_relative_path"],
        "raw_sha256": publication["raw_sha256"],
    }


def _publish_attempt(
    *,
    root: Path,
    raw_body: Any,
    credential: str,
    request: Mapping[str, Any],
    retrieved_at: str,
    http_status: Any,
    body_complete: Any,
    collection_binding: Mapping[str, str],
) -> dict[str, Any]:
    if (
        type(raw_body) is not bytes
        or len(raw_body) > _MAX_BODY_BYTES
        or type(credential) is not str
        or not credential
    ):
        raise ValueError("Jiaoch trade calendar raw response input rejected")
    if raw_authority._credential_echoes(raw_body, credential):
        raise ValueError("Jiaoch trade calendar credential echo rejected")
    if (
        type(http_status) is not int
        or not 100 <= http_status <= 599
        or type(body_complete) is not bool
    ):
        raise ValueError("Jiaoch trade calendar HTTP metadata rejected")
    timestamp = _utc_timestamp(
        retrieved_at,
        label="Jiaoch trade calendar retrieved_at",
    )
    start, end = _request_dates(dict(request))
    canonical_request = _request_semantics(start, end)
    if dict(request) != canonical_request:
        raise ValueError("Jiaoch trade calendar request semantics rejected")
    binding = _collection_binding(
        collection_call_id=collection_binding.get("collection_call_id"),
        generation_id=collection_binding.get("generation_id"),
        auxiliary_policy_sha256=collection_binding.get("auxiliary_policy_sha256"),
        producer_root_sha256=collection_binding.get("producer_root_sha256"),
    )
    if dict(collection_binding) != binding:
        raise ValueError("Jiaoch trade calendar collection binding rejected")

    raw_sha256 = _sha256(raw_body)
    raw_directory = raw_authority._content_addressed_directory(
        root,
        "trade_cal_raw",
        raw_sha256,
    )
    raw_path = raw_directory / f"{raw_sha256}.body"
    raw_created = raw_authority._write_create_only(
        raw_path,
        raw_body,
        label="Jiaoch trade calendar raw object",
        reuse_identical=True,
    )
    attempt_id = str(uuid.uuid4())
    request_raw = _canonical_json(canonical_request)
    attempt = {
        "api_name": _API_NAME,
        "attempt_id": attempt_id,
        "authority_status": "UNBOUND",
        "body_complete": body_complete,
        "calendar_authority_status": "NOT_GRANTED",
        "collection_binding": binding,
        "collector_version": COLLECTOR_VERSION,
        "credential_echo_check": "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE",
        "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "development_session_alignment_verified": False,
        "development_session_count_claimed": False,
        "embargo_consumed": False,
        "endpoint": _ENDPOINT,
        "experiment_launch_eligible": False,
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "http_status": http_status,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "raw_object": {
            "bytes": len(raw_body),
            "relative_path": _raw_relative_path(raw_sha256),
            "sha256": raw_sha256,
        },
        "raw_response_persisted": True,
        "request_semantics": canonical_request,
        "request_semantics_sha256": _sha256(request_raw),
        "retrieved_at": timestamp,
        "route": {
            "network_route": "direct",
            "redirect_policy": "refuse",
            "request_protocol": "tushare-path-per-interface/v1",
            "transport": "app.research_pit_transport.UrllibTushareTransport",
        },
        "row_authority_status": "NOT_GRANTED",
        "rows_published": False,
        "schema": ATTEMPT_SCHEMA,
        "source_id": _SOURCE_ID,
    }
    attempt_raw = _canonical_json(attempt)
    if len(attempt_raw) > _MAX_ATTEMPT_BYTES:
        raise ValueError("Jiaoch trade calendar attempt size rejected")
    attempt_sha256 = _sha256(attempt_raw)
    attempt_directory = raw_authority._content_addressed_directory(
        root,
        "trade_cal_attempts",
        attempt_sha256,
    )
    attempt_path = attempt_directory / f"{attempt_sha256}.json"
    attempt_created = raw_authority._write_create_only(
        attempt_path,
        attempt_raw,
        label="Jiaoch trade calendar attempt",
        reuse_identical=False,
    )
    relative_path = _attempt_relative_path(attempt_sha256)
    _verify_attempt_and_load(
        root=root,
        attempt_relative_path=relative_path,
        expected_attempt_sha256=attempt_sha256,
    )
    return {
        "attempt_created": attempt_created,
        "attempt_id": attempt_id,
        "attempt_relative_path": relative_path,
        "attempt_sha256": attempt_sha256,
        "raw_created": raw_created,
        "raw_relative_path": _raw_relative_path(raw_sha256),
        "raw_sha256": raw_sha256,
    }


def _validated_attempt_path(
    *,
    root: Path,
    attempt_relative_path: Any,
    expected_attempt_sha256: Any,
) -> Path:
    digest = _sha256_text(
        expected_attempt_sha256,
        label="Jiaoch trade calendar attempt sha256",
    )
    if type(attempt_relative_path) is not str:
        raise ValueError("Jiaoch trade calendar attempt path rejected")
    match = _ATTEMPT_PATH_PATTERN.fullmatch(attempt_relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("Jiaoch trade calendar attempt path rejected")
    return raw_authority._safe_existing_file(
        root / Path(*attempt_relative_path.split("/")),
        "Jiaoch trade calendar attempt",
    )


def _verify_attempt_payload(payload: Any) -> tuple[date, date]:
    if (
        type(payload) is not dict
        or set(payload) != _ATTEMPT_FIELDS
        or payload.get("schema") != ATTEMPT_SCHEMA
        or payload.get("source_id") != _SOURCE_ID
        or payload.get("collector_version") != COLLECTOR_VERSION
        or payload.get("api_name") != _API_NAME
        or payload.get("endpoint") != _ENDPOINT
        or payload.get("credential_slot_id") != _CREDENTIAL_SLOT_ID
        or payload.get("credential_echo_check") != "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE"
        or payload.get("raw_response_persisted") is not True
        or payload.get("authority_status") != "UNBOUND"
        or payload.get("calendar_authority_status") != "NOT_GRANTED"
        or payload.get("row_authority_status") != "NOT_GRANTED"
        or payload.get("development_session_alignment_verified") is not False
        or payload.get("development_session_count_claimed") is not False
        or payload.get("embargo_consumed") is not False
        or payload.get("experiment_launch_eligible") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("formal_materialization_eligible") is not False
        or payload.get("production_profile_registered") is not False
        or payload.get("production_recommendation_eligible") is not False
        or payload.get("rows_published") is not False
        or type(payload.get("http_status")) is not int
        or not 100 <= payload["http_status"] <= 599
        or type(payload.get("body_complete")) is not bool
    ):
        raise ValueError("Jiaoch trade calendar attempt descriptor rejected")
    _uuid4_text(payload.get("attempt_id"), label="Jiaoch trade calendar attempt_id")
    _utc_timestamp(
        payload.get("retrieved_at"),
        label="Jiaoch trade calendar attempt retrieved_at",
    )
    if payload.get("route") != {
        "network_route": "direct",
        "redirect_policy": "refuse",
        "request_protocol": "tushare-path-per-interface/v1",
        "transport": "app.research_pit_transport.UrllibTushareTransport",
    }:
        raise ValueError("Jiaoch trade calendar attempt route rejected")
    start, end = _request_dates(payload.get("request_semantics"))
    if payload["request_semantics"] != _request_semantics(start, end):
        raise ValueError("Jiaoch trade calendar request semantics rejected")
    request_semantics_sha256 = _sha256_text(
        payload.get("request_semantics_sha256"),
        label="Jiaoch trade calendar request semantics sha256",
    )
    if not hmac.compare_digest(
        request_semantics_sha256,
        _sha256(_canonical_json(payload["request_semantics"])),
    ):
        raise ValueError("Jiaoch trade calendar request semantics rejected")
    binding = payload.get("collection_binding")
    if type(binding) is not dict or set(binding) != _COLLECTION_BINDING_FIELDS:
        raise ValueError("Jiaoch trade calendar collection binding rejected")
    expected_binding = _collection_binding(
        collection_call_id=binding.get("collection_call_id"),
        generation_id=binding.get("generation_id"),
        auxiliary_policy_sha256=binding.get("auxiliary_policy_sha256"),
        producer_root_sha256=binding.get("producer_root_sha256"),
    )
    if binding != expected_binding:
        raise ValueError("Jiaoch trade calendar collection binding rejected")
    raw_object = payload.get("raw_object")
    if (
        type(raw_object) is not dict
        or set(raw_object) != {"bytes", "relative_path", "sha256"}
        or type(raw_object.get("bytes")) is not int
        or not 0 <= raw_object["bytes"] <= _MAX_BODY_BYTES
        or raw_object.get("relative_path") != _raw_relative_path(raw_object.get("sha256", ""))
    ):
        raise ValueError("Jiaoch trade calendar raw object rejected")
    _sha256_text(raw_object.get("sha256"), label="Jiaoch trade calendar raw sha256")
    return start, end


def _verify_attempt_and_load(
    *,
    root: Path,
    attempt_relative_path: str,
    expected_attempt_sha256: str,
) -> tuple[dict[str, Any], bytes]:
    path = _validated_attempt_path(
        root=root,
        attempt_relative_path=attempt_relative_path,
        expected_attempt_sha256=expected_attempt_sha256,
    )
    raw = raw_authority._read_safe_file(
        path,
        label="Jiaoch trade calendar attempt",
        max_bytes=_MAX_ATTEMPT_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_attempt_sha256):
        raise ValueError("Jiaoch trade calendar attempt content address rejected")
    payload = _strict_json_loads(raw, label="Jiaoch trade calendar attempt")
    if not hmac.compare_digest(raw, _canonical_json(payload)):
        raise ValueError("Jiaoch trade calendar attempt canonical form rejected")
    _verify_attempt_payload(payload)
    raw_object = payload["raw_object"]
    raw_body = raw_authority._read_safe_file(
        root / Path(*raw_object["relative_path"].split("/")),
        label="Jiaoch trade calendar raw object",
        max_bytes=_MAX_BODY_BYTES,
        expected_size=raw_object["bytes"],
    )
    if not hmac.compare_digest(_sha256(raw_body), raw_object["sha256"]):
        raise ValueError("Jiaoch trade calendar raw content address rejected")
    return payload, raw_body


def _calendar_evidence(
    raw: bytes,
    *,
    expected_start: date,
    expected_end: date,
) -> dict[str, Any]:
    payload = _strict_json_loads(raw, label="Jiaoch trade calendar response")
    if (
        type(payload) is not dict
        or set(payload) != {"code", "data", "msg"}
        or type(payload["code"]) is not int
        or payload["code"] != 0
        or type(payload["msg"]) is not str
        or payload["msg"] != "success"
    ):
        raise ValueError("Jiaoch trade calendar provider status rejected")
    data = payload["data"]
    if (
        type(data) is not dict
        or set(data) != {"fields", "items"}
        or type(data["fields"]) is not list
        or data["fields"] != _FIELDS
        or type(data["items"]) is not list
    ):
        raise ValueError("Jiaoch trade calendar interface identity rejected")
    rows = []
    identities = set()
    for item in data["items"]:
        if (
            type(item) is not list
            or len(item) != len(_FIELDS)
            or item[0] != _EXCHANGE
            or type(item[2]) is not int
            or item[2] not in {0, 1}
        ):
            raise ValueError("Jiaoch trade calendar row rejected")
        cal_date = _yyyymmdd(item[1], label="Jiaoch trade calendar cal_date")
        pretrade_date = _yyyymmdd(
            item[3],
            label="Jiaoch trade calendar pretrade_date",
        )
        if item[1] in identities:
            raise ValueError("Jiaoch trade calendar duplicate date rejected")
        identities.add(item[1])
        rows.append(
            {
                "cal_date": item[1],
                "exchange": item[0],
                "is_open": item[2],
                "pretrade_date": item[3],
                "_cal_date": cal_date,
                "_pretrade_date": pretrade_date,
            }
        )
    rows.sort(key=lambda row: row["cal_date"])
    expected_dates = []
    cursor = expected_start
    while cursor <= expected_end:
        expected_dates.append(cursor)
        cursor += timedelta(days=1)
    if [row["_cal_date"] for row in rows] != expected_dates:
        raise ValueError("Jiaoch trade calendar natural-day coverage rejected")
    anchor = rows[0]["_pretrade_date"] if rows else None
    if anchor is None or anchor >= expected_start:
        raise ValueError("Jiaoch trade calendar pretrade anchor rejected")
    last_open = anchor.strftime("%Y%m%d")
    open_sessions = []
    normalized_rows = []
    for row in rows:
        if row["pretrade_date"] != last_open:
            raise ValueError("Jiaoch trade calendar pretrade chain rejected")
        normalized_rows.append(
            {
                "cal_date": row["cal_date"],
                "exchange": row["exchange"],
                "is_open": row["is_open"],
                "pretrade_date": row["pretrade_date"],
            }
        )
        if row["is_open"] == 1:
            open_sessions.append(row["cal_date"])
            last_open = row["cal_date"]
    rows_descriptor = {
        "rows": normalized_rows,
        "schema": "jiaoch-trade-cal-normalized-rows/v1",
    }
    sessions_descriptor = {
        "end_date": expected_end.strftime("%Y%m%d"),
        "exchange": _EXCHANGE,
        "open_sessions": open_sessions,
        "schema": "jiaoch-trade-cal-open-sessions/v1",
        "start_date": expected_start.strftime("%Y%m%d"),
    }
    return {
        "calendar_day_count": len(normalized_rows),
        "calendar_rows_root_sha256": _sha256(_canonical_json(rows_descriptor)),
        "open_session_count": len(open_sessions),
        "open_sessions": open_sessions,
        "open_sessions_root_sha256": _sha256(_canonical_json(sessions_descriptor)),
        "pretrade_anchor_date": anchor.strftime("%Y%m%d"),
    }


def _manifest_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "calendar_day_count": payload["calendar_day_count"],
        "calendar_rows_root_sha256": payload["calendar_rows_root_sha256"],
        "open_session_count": payload["open_session_count"],
        "open_sessions": payload["open_sessions"],
        "open_sessions_root_sha256": payload["open_sessions_root_sha256"],
        "pretrade_anchor_date": payload["pretrade_anchor_date"],
    }


def _verify_manifest_payload(*, root: Path, payload: Any) -> dict[str, Any]:
    if (
        type(payload) is not dict
        or set(payload) != _MANIFEST_FIELDS
        or payload.get("schema") != AUTHORITY_MANIFEST_SCHEMA
        or payload.get("source_id") != _SOURCE_ID
        or payload.get("collector_version") != COLLECTOR_VERSION
        or payload.get("credential_slot_id") != _CREDENTIAL_SLOT_ID
        or payload.get("authority_scope") != "SSE_TRADING_CALENDAR_WINDOW_ONLY"
        or payload.get("calendar_authority_status") != "VERIFIED_SINGLE_SEALED_CALL"
        or payload.get("calendar_integrity_verified") is not True
        or payload.get("natural_day_coverage_verified") is not True
        or payload.get("pretrade_chain_verified") is not True
        or payload.get("open_sessions_sorted") is not True
        or payload.get("development_session_alignment_verified") is not False
        or payload.get("development_session_count_claimed") is not False
        or payload.get("embargo_consumed") is not False
        or payload.get("experiment_launch_eligible") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("formal_materialization_eligible") is not False
        or payload.get("production_profile_registered") is not False
        or payload.get("production_recommendation_eligible") is not False
        or payload.get("rows_published") is not False
        or payload.get("exchange") != _EXCHANGE
    ):
        raise ValueError("Jiaoch trade calendar manifest descriptor rejected")
    _uuid4_text(
        payload.get("collection_call_id"),
        label="Jiaoch trade calendar collection_call_id",
    )
    timestamp = _utc_timestamp(
        payload.get("retrieved_at"),
        label="Jiaoch trade calendar manifest retrieved_at",
    )
    if type(payload.get("start_date")) is not str or type(payload.get("end_date")) is not str:
        raise ValueError("Jiaoch trade calendar manifest date rejected")
    try:
        start = date.fromisoformat(payload["start_date"])
        end = date.fromisoformat(payload["end_date"])
    except ValueError:
        raise ValueError("Jiaoch trade calendar manifest date rejected") from None
    if start.isoformat() != payload["start_date"] or end.isoformat() != payload["end_date"]:
        raise ValueError("Jiaoch trade calendar manifest date rejected")
    _date_window(start, end)
    auxiliary = _auxiliary_policy_descriptor(payload.get("auxiliary_policy_descriptor"))
    runtime = payload.get("runtime_mapping_descriptor")
    if (
        type(runtime) is not dict
        or set(runtime) != {"auxiliary_policy_sha256", "credential_proof_status", "generation_id"}
        or runtime.get("credential_proof_status") != "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF"
        or runtime.get("auxiliary_policy_sha256") != auxiliary["sha256"]
    ):
        raise ValueError("Jiaoch trade calendar auxiliary policy runtime mapping rejected")
    _uuid4_text(runtime.get("generation_id"), label="Jiaoch trade calendar generation_id")
    if payload.get("credential_binding") != {
        "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
        "credential_proof_claimed": False,
        "same_runtime_credential_used": True,
    }:
        raise ValueError("Jiaoch trade calendar credential binding rejected")
    producer = payload.get("producer_binding")
    if producer != _producer_binding():
        raise ValueError("Jiaoch trade calendar producer binding rejected")
    attempt_descriptor = payload.get("attempt")
    if (
        type(attempt_descriptor) is not dict
        or set(attempt_descriptor) != _ATTEMPT_DESCRIPTOR_FIELDS
    ):
        raise ValueError("Jiaoch trade calendar attempt descriptor rejected")
    _uuid4_text(
        attempt_descriptor.get("attempt_id"),
        label="Jiaoch trade calendar attempt_id",
    )
    _sha256_text(
        attempt_descriptor.get("attempt_sha256"),
        label="Jiaoch trade calendar attempt sha256",
    )
    _sha256_text(
        attempt_descriptor.get("raw_sha256"),
        label="Jiaoch trade calendar raw sha256",
    )
    attempt, raw = _verify_attempt_and_load(
        root=root,
        attempt_relative_path=attempt_descriptor["attempt_relative_path"],
        expected_attempt_sha256=attempt_descriptor["attempt_sha256"],
    )
    expected_binding = _collection_binding(
        collection_call_id=payload["collection_call_id"],
        generation_id=runtime["generation_id"],
        auxiliary_policy_sha256=runtime["auxiliary_policy_sha256"],
        producer_root_sha256=producer["root_sha256"],
    )
    expected_request = _request_semantics(start, end)
    if (
        attempt["attempt_id"] != attempt_descriptor["attempt_id"]
        or attempt["raw_object"]["relative_path"] != attempt_descriptor["raw_relative_path"]
        or attempt["raw_object"]["sha256"] != attempt_descriptor["raw_sha256"]
        or attempt["collection_binding"] != expected_binding
        or attempt["request_semantics"] != expected_request
        or attempt["retrieved_at"] != timestamp
        or attempt["http_status"] != 200
        or attempt["body_complete"] is not True
    ):
        raise ValueError("Jiaoch trade calendar attempt binding rejected")
    evidence = _calendar_evidence(
        raw,
        expected_start=start,
        expected_end=end,
    )
    if evidence != _manifest_evidence(payload):
        raise ValueError("Jiaoch trade calendar evidence root rejected")
    return payload


def _rollback_created_manifest(path: Path, expected_raw: bytes) -> None:
    observed = raw_authority._read_safe_file(
        path,
        label="Jiaoch trade calendar rollback manifest",
        max_bytes=len(expected_raw),
        expected_size=len(expected_raw),
    )
    if not hmac.compare_digest(observed, expected_raw):
        raise ValueError("Jiaoch trade calendar rollback identity rejected")
    path.unlink()
    raw_authority.fsync_directory(path.parent)


def _collect_jiaoch_trade_cal_with_route_credential(
    *,
    credential: str,
    generation_id: str,
    auxiliary_policy_descriptor: Mapping[str, Any],
    output_root: str | Path,
    start_date: date,
    end_date: date,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Private sealed entrypoint implementation; no caller-supplied transport or clock."""

    if type(credential) is not str or not credential:
        raise ValueError("Jiaoch trade calendar credential rejected")
    generation = _uuid4_text(generation_id, label="Jiaoch trade calendar generation_id")
    auxiliary = _auxiliary_policy_descriptor(dict(auxiliary_policy_descriptor))
    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "Jiaoch trade calendar authority root",
    )
    start, end = _date_window(start_date, end_date)
    timeout = _timeout_seconds(timeout_seconds)
    timestamp = _trusted_utc_timestamp()
    request = _request_semantics(start, end)
    call_id = str(uuid.uuid4())
    producer = _producer_binding()
    binding = _collection_binding(
        collection_call_id=call_id,
        generation_id=generation,
        auxiliary_policy_sha256=auxiliary["sha256"],
        producer_root_sha256=producer["root_sha256"],
    )
    transport = _transport_factory()
    request_body = _canonical_json(
        {
            "api_name": _API_NAME,
            "fields": _FIELDS_TEXT,
            "params": request["params"],
            "token": credential,
        }
    )
    response = transport.post(
        url=_ENDPOINT,
        headers=_request_headers(),
        body=request_body,
        timeout_s=timeout,
        max_body_bytes=_MAX_BODY_BYTES,
    )
    raw_body = getattr(response, "body", None)
    http_status = getattr(response, "status", None)
    body_complete = getattr(response, "body_complete", None)
    publication = _publish_attempt(
        root=root,
        raw_body=raw_body,
        credential=credential,
        request=request,
        retrieved_at=timestamp,
        http_status=http_status,
        body_complete=body_complete,
        collection_binding=binding,
    )
    if http_status != 200 or body_complete is not True:
        raise ValueError("Jiaoch trade calendar HTTP entity rejected")
    evidence = _calendar_evidence(
        raw_body,
        expected_start=start,
        expected_end=end,
    )
    if _producer_binding() != producer:
        raise ValueError("Jiaoch trade calendar producer drift rejected")
    manifest = {
        "attempt": _attempt_descriptor(publication),
        "authority_scope": "SSE_TRADING_CALENDAR_WINDOW_ONLY",
        "auxiliary_policy_descriptor": auxiliary,
        "calendar_authority_status": "VERIFIED_SINGLE_SEALED_CALL",
        "calendar_day_count": evidence["calendar_day_count"],
        "calendar_integrity_verified": True,
        "calendar_rows_root_sha256": evidence["calendar_rows_root_sha256"],
        "collection_call_id": call_id,
        "collector_version": COLLECTOR_VERSION,
        "credential_binding": {
            "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
            "credential_proof_claimed": False,
            "same_runtime_credential_used": True,
        },
        "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "development_session_alignment_verified": False,
        "development_session_count_claimed": False,
        "embargo_consumed": False,
        "end_date": end.isoformat(),
        "exchange": _EXCHANGE,
        "experiment_launch_eligible": False,
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "natural_day_coverage_verified": True,
        "open_session_count": evidence["open_session_count"],
        "open_sessions": evidence["open_sessions"],
        "open_sessions_root_sha256": evidence["open_sessions_root_sha256"],
        "open_sessions_sorted": True,
        "pretrade_anchor_date": evidence["pretrade_anchor_date"],
        "pretrade_chain_verified": True,
        "producer_binding": producer,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "retrieved_at": timestamp,
        "rows_published": False,
        "runtime_mapping_descriptor": {
            "auxiliary_policy_sha256": auxiliary["sha256"],
            "credential_proof_status": "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF",
            "generation_id": generation,
        },
        "schema": AUTHORITY_MANIFEST_SCHEMA,
        "source_id": _SOURCE_ID,
        "start_date": start.isoformat(),
    }
    _verify_manifest_payload(root=root, payload=manifest)
    manifest_raw = _canonical_json(manifest)
    if len(manifest_raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("Jiaoch trade calendar manifest size rejected")
    digest = _sha256(manifest_raw)
    directory = raw_authority._content_addressed_directory(
        root,
        "trade_cal_manifests",
        digest,
    )
    path = directory / f"{digest}.json"
    relative_path = f"trade_cal_manifests/sha256/{digest[:2]}/{digest}.json"
    created = False
    try:
        created = raw_authority._write_create_only(
            path,
            manifest_raw,
            label="Jiaoch trade calendar authority manifest",
            reuse_identical=False,
        )
        verify_jiaoch_trade_cal_authority(
            output_root=root,
            authority_manifest_relative_path=relative_path,
            expected_authority_manifest_sha256=digest,
        )
    except BaseException:
        if created:
            try:
                _rollback_created_manifest(path, manifest_raw)
            except Exception:
                raise ValueError("Jiaoch trade calendar manifest rollback failed") from None
        raise
    return {
        "authority_manifest_created": created,
        "authority_manifest_relative_path": relative_path,
        "authority_manifest_sha256": digest,
    }


def _validated_manifest_path(
    *,
    root: Path,
    authority_manifest_relative_path: Any,
    expected_authority_manifest_sha256: Any,
) -> Path:
    digest = _sha256_text(
        expected_authority_manifest_sha256,
        label="Jiaoch trade calendar manifest sha256",
    )
    if type(authority_manifest_relative_path) is not str:
        raise ValueError("Jiaoch trade calendar manifest path rejected")
    match = _MANIFEST_PATH_PATTERN.fullmatch(authority_manifest_relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("Jiaoch trade calendar manifest path rejected")
    return raw_authority._safe_existing_file(
        root / Path(*authority_manifest_relative_path.split("/")),
        "Jiaoch trade calendar authority manifest",
    )


def verify_jiaoch_trade_cal_authority(
    *,
    output_root: str | Path,
    authority_manifest_relative_path: str,
    expected_authority_manifest_sha256: str,
) -> dict[str, Any]:
    """Verify the terminal manifest, attempt, raw response and calendar offline."""

    root = raw_authority._safe_existing_directory(
        Path(output_root),
        "Jiaoch trade calendar authority root",
    )
    path = _validated_manifest_path(
        root=root,
        authority_manifest_relative_path=authority_manifest_relative_path,
        expected_authority_manifest_sha256=expected_authority_manifest_sha256,
    )
    raw = raw_authority._read_safe_file(
        path,
        label="Jiaoch trade calendar authority manifest",
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_authority_manifest_sha256):
        raise ValueError("Jiaoch trade calendar manifest content address rejected")
    payload = _strict_json_loads(raw, label="Jiaoch trade calendar manifest")
    if not hmac.compare_digest(raw, _canonical_json(payload)):
        raise ValueError("Jiaoch trade calendar manifest canonical form rejected")
    _verify_manifest_payload(root=root, payload=payload)
    return {
        "authority_manifest_sha256": expected_authority_manifest_sha256,
        "calendar_authority_status": "VERIFIED_SINGLE_SEALED_CALL",
        "development_session_alignment_verified": False,
        "embargo_consumed": False,
        "end_date": payload["end_date"],
        "exchange": _EXCHANGE,
        "final_oos_consumed": False,
        "open_session_count": payload["open_session_count"],
        "open_sessions_root_sha256": payload["open_sessions_root_sha256"],
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "start_date": payload["start_date"],
        "verified": True,
    }
