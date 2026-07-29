"""Fail-closed Jiaoch ``stk_mins`` permission and schema probe."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.durable_io import fsync_directory
from app.research_pit_sources import TushareSource, _validate_loopback_http_proxy
from app.research_pit_transport import UrllibTushareTransport


SCHEMA_VERSION = "jiaoch-historical-minute-source-diagnostic/v1"
_JIAOCH_API_URLS = frozenset({"https://jiaoch.site", "https://jiaoch.site/"})
_REQUEST_PROTOCOL = "tushare-path-per-interface/v1"
_INTERFACE = "stk_mins"
_PATH = "/stk_mins"
_FREQ = "1min"
_MAX_BODY_BYTES = 1024 * 1024
_MAX_DIAGNOSTIC_BYTES = 128 * 1024
_MAX_JSON_NESTING_DEPTH = 64
_TS_CODE_PATTERN = re.compile(r"[0-9]{6}\.(?:SH|SZ)")
_HEX = frozenset("0123456789abcdef")
_CLASSIFICATIONS = frozenset(
    {
        "PERMISSION_DENIED",
        "SCHEMA_UNBOUND",
        "SOURCE_ERROR",
        "RESPONSE_REJECTED",
        "CREDENTIAL_ECHO_REJECTED",
        "TRANSPORT_REJECTED",
        "TRANSPORT_ERROR",
    }
)
_UNSIGNED_FIELDS = frozenset(
    {
        "schema",
        "source_id",
        "interface",
        "request_semantics",
        "retrieved_at",
        "http_status",
        "body_complete",
        "body_within_limit",
        "response_body_bytes",
        "response_body_sha256",
        "provider_code",
        "classification",
        "transport_binding",
        "request_count",
        "retry_count",
        "fallback_used",
        "raw_response_persisted",
        "authority_status",
        "evidence_complete",
        "verified",
        "rows_published",
        "minute_amount_authority_status",
        "production_recommendation_eligible",
    }
)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _iter_bounded_json_tree(value: Any):
    stack = [(value, 0)]
    seen_containers: set[int] = set()
    while stack:
        item, depth = stack.pop()
        if depth > _MAX_JSON_NESTING_DEPTH:
            raise ValueError("JSON nesting depth exceeded")
        yield item
        if isinstance(item, Mapping):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError("JSON tree contains a repeated container")
            seen_containers.add(identity)
            for key, nested in reversed(tuple(item.items())):
                stack.append((nested, depth + 1))
                stack.append((key, depth + 1))
        elif isinstance(item, (list, tuple)):
            identity = id(item)
            if identity in seen_containers:
                raise ValueError("JSON tree contains a repeated container")
            seen_containers.add(identity)
            for nested in reversed(item):
                stack.append((nested, depth + 1))


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
        value = json.loads(
            raw,
            object_pairs_hook=object_pairs,
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Jiaoch stk_mins response was not valid JSON") from exc
    for item in _iter_bounded_json_tree(value):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("JSON contains a non-finite numeric value")
    return value


def _contains_semantic_token(value: Any, token: str) -> bool:
    if not token:
        return False
    return any(isinstance(item, str) and token in item for item in _iter_bounded_json_tree(value))


def _valid_sha256(value: Any) -> bool:
    return type(value) is str and len(value) == 64 and all(character in _HEX for character in value)


def _aware_timestamp(value: Any) -> str:
    if type(value) is not str or not value:
        raise ValueError("retrieved_at must be an aware ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("retrieved_at must be an aware ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("retrieved_at must be an aware ISO timestamp")
    return value


def _opaque_date_argument(value: Any, field: str) -> str:
    if (
        type(value) is not str
        or not value
        or len(value) > 64
        or value.strip() != value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{field} must be a bounded non-empty string")
    return value


def _validate_source(source: TushareSource) -> None:
    route_valid = False
    if isinstance(source, TushareSource):
        if source.proxy_url is None:
            route_valid = source.network_route == "direct"
        elif type(source.proxy_url) is str:
            try:
                normalized_proxy = _validate_loopback_http_proxy(source.proxy_url)
            except ValueError:
                normalized_proxy = None
            route_valid = (
                normalized_proxy == source.proxy_url
                and source.network_route == "loopback_http_proxy"
            )
    if (
        not isinstance(source, TushareSource)
        or source.name != "jiaoch"
        or source.api_url not in _JIAOCH_API_URLS
        or source.allowed_hosts != ("jiaoch.site",)
        or source.request_protocol != _REQUEST_PROTOCOL
        or type(source.token) is not str
        or not source.token
        or any(ord(character) < 32 or ord(character) == 127 for character in source.token)
        or not route_valid
    ):
        raise ValueError("Jiaoch stk_mins source binding rejected")


def _request_semantics(
    *,
    ts_code: str,
    start_date: str,
    end_date: str,
) -> dict[str, Any]:
    if type(ts_code) is not str or _TS_CODE_PATTERN.fullmatch(ts_code) is None:
        raise ValueError("ts_code must be a canonical SH/SZ Tushare code")
    return {
        "method": "POST",
        "path": _PATH,
        "api_name": _INTERFACE,
        "params": {
            "ts_code": ts_code,
            "start_date": _opaque_date_argument(start_date, "start_date"),
            "end_date": _opaque_date_argument(end_date, "end_date"),
            "freq": _FREQ,
        },
        "fields": "",
    }


def _base_manifest(
    *,
    request_semantics: Mapping[str, Any],
    retrieved_at: str,
    classification: str,
    http_status: int | None,
    body_complete: bool | None,
    body_within_limit: bool | None,
    response_body_bytes: int | None,
    response_body_sha256: str | None,
    provider_code: int | None,
) -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "source_id": "jiaoch",
        "interface": _INTERFACE,
        "request_semantics": dict(request_semantics),
        "retrieved_at": retrieved_at,
        "http_status": http_status,
        "body_complete": body_complete,
        "body_within_limit": body_within_limit,
        "response_body_bytes": response_body_bytes,
        "response_body_sha256": response_body_sha256,
        "provider_code": provider_code,
        "classification": classification,
        "request_count": 1,
        "retry_count": 0,
        "fallback_used": False,
        "raw_response_persisted": False,
        "authority_status": "UNBOUND",
        "evidence_complete": False,
        "verified": False,
        "rows_published": False,
        "minute_amount_authority_status": "UNBOUND",
        "production_recommendation_eligible": False,
    }


def _transport_error_manifest(
    *,
    request_semantics: Mapping[str, Any],
    retrieved_at: str,
) -> dict[str, Any]:
    return _base_manifest(
        request_semantics=request_semantics,
        retrieved_at=retrieved_at,
        classification="TRANSPORT_ERROR",
        http_status=None,
        body_complete=None,
        body_within_limit=None,
        response_body_bytes=None,
        response_body_sha256=None,
        provider_code=None,
    )


def _response_manifest(
    *,
    request_semantics: Mapping[str, Any],
    retrieved_at: str,
    status: Any,
    body_complete: Any,
    body: Any,
    token: str,
) -> dict[str, Any]:
    valid_status = type(status) is int
    complete = body_complete if type(body_complete) is bool else None
    body_is_bytes = type(body) is bytes
    within_limit = len(body) <= _MAX_BODY_BYTES if body_is_bytes else None

    if body_is_bytes and token.encode("utf-8") in body:
        return _base_manifest(
            request_semantics=request_semantics,
            retrieved_at=retrieved_at,
            classification="CREDENTIAL_ECHO_REJECTED",
            http_status=status if valid_status else None,
            body_complete=complete,
            body_within_limit=within_limit,
            response_body_bytes=None,
            response_body_sha256=None,
            provider_code=None,
        )

    response_bytes = len(body) if within_limit is True else None
    response_sha256 = hashlib.sha256(body).hexdigest() if within_limit is True else None
    if not valid_status or status != 200 or complete is not True or within_limit is not True:
        return _base_manifest(
            request_semantics=request_semantics,
            retrieved_at=retrieved_at,
            classification="TRANSPORT_REJECTED",
            http_status=status if valid_status else None,
            body_complete=complete,
            body_within_limit=within_limit,
            response_body_bytes=response_bytes,
            response_body_sha256=response_sha256,
            provider_code=None,
        )

    try:
        envelope = _strict_json_loads(body)
    except (TypeError, ValueError):
        envelope = None
    if envelope is not None and _contains_semantic_token(envelope, token):
        return _base_manifest(
            request_semantics=request_semantics,
            retrieved_at=retrieved_at,
            classification="CREDENTIAL_ECHO_REJECTED",
            http_status=status,
            body_complete=True,
            body_within_limit=True,
            response_body_bytes=None,
            response_body_sha256=None,
            provider_code=None,
        )
    if not isinstance(envelope, Mapping):
        return _base_manifest(
            request_semantics=request_semantics,
            retrieved_at=retrieved_at,
            classification="RESPONSE_REJECTED",
            http_status=status,
            body_complete=True,
            body_within_limit=True,
            response_body_bytes=response_bytes,
            response_body_sha256=response_sha256,
            provider_code=None,
        )
    code = envelope.get("code")
    if isinstance(code, bool) or not isinstance(code, int):
        return _base_manifest(
            request_semantics=request_semantics,
            retrieved_at=retrieved_at,
            classification="RESPONSE_REJECTED",
            http_status=status,
            body_complete=True,
            body_within_limit=True,
            response_body_bytes=response_bytes,
            response_body_sha256=response_sha256,
            provider_code=None,
        )
    message = envelope.get("msg")
    if code == -1 and isinstance(message, str) and "权限不足" in message:
        classification = "PERMISSION_DENIED"
    elif code == 0:
        classification = "SCHEMA_UNBOUND"
    else:
        classification = "SOURCE_ERROR"
    return _base_manifest(
        request_semantics=request_semantics,
        retrieved_at=retrieved_at,
        classification=classification,
        http_status=status,
        body_complete=True,
        body_within_limit=True,
        response_body_bytes=response_bytes,
        response_body_sha256=response_sha256,
        provider_code=code,
    )


def verify_jiaoch_historical_minute_diagnostic(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    tuple(_iter_bounded_json_tree(payload))
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {*_UNSIGNED_FIELDS, "descriptor_sha256"}
        or payload.get("schema") != SCHEMA_VERSION
        or payload.get("source_id") != "jiaoch"
        or payload.get("interface") != _INTERFACE
    ):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")
    unsigned = {key: value for key, value in payload.items() if key != "descriptor_sha256"}
    digest = hashlib.sha256(_canonical_json(unsigned)).hexdigest()
    if payload.get("descriptor_sha256") != digest:
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")

    request = payload.get("request_semantics")
    if (
        not isinstance(request, Mapping)
        or set(request) != {"method", "path", "api_name", "params", "fields"}
        or request.get("method") != "POST"
        or request.get("path") != _PATH
        or request.get("api_name") != _INTERFACE
        or request.get("fields") != ""
    ):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")
    params = request.get("params")
    if (
        not isinstance(params, Mapping)
        or set(params) != {"ts_code", "start_date", "end_date", "freq"}
        or type(params.get("ts_code")) is not str
        or _TS_CODE_PATTERN.fullmatch(params["ts_code"]) is None
        or params.get("freq") != _FREQ
    ):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")
    try:
        _opaque_date_argument(params.get("start_date"), "start_date")
        _opaque_date_argument(params.get("end_date"), "end_date")
        _aware_timestamp(payload.get("retrieved_at"))
    except ValueError:
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected") from None

    if (
        payload.get("classification") not in _CLASSIFICATIONS
        or payload.get("request_count") != 1
        or type(payload.get("request_count")) is not int
        or payload.get("retry_count") != 0
        or type(payload.get("retry_count")) is not int
        or payload.get("fallback_used") is not False
        or payload.get("raw_response_persisted") is not False
        or payload.get("authority_status") != "UNBOUND"
        or payload.get("evidence_complete") is not False
        or payload.get("verified") is not False
        or payload.get("rows_published") is not False
        or payload.get("minute_amount_authority_status") != "UNBOUND"
        or payload.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")
    transport_binding = payload.get("transport_binding")
    if transport_binding != {
        "implementation": "app.research_pit_transport.UrllibTushareTransport",
        "collector_post_invocations": 1,
        "collector_http_retries": 0,
        "collector_fallbacks": 0,
        "redirect_policy": "refuse",
        "network_route": transport_binding.get("network_route")
        if isinstance(transport_binding, Mapping)
        else None,
    } or transport_binding.get("network_route") not in {
        "direct",
        "loopback_http_proxy",
    }:
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")

    status = payload.get("http_status")
    complete = payload.get("body_complete")
    within = payload.get("body_within_limit")
    body_bytes = payload.get("response_body_bytes")
    body_sha = payload.get("response_body_sha256")
    code = payload.get("provider_code")
    if (
        (status is not None and (type(status) is not int or not 100 <= status <= 599))
        or (complete is not None and type(complete) is not bool)
        or (within is not None and type(within) is not bool)
        or (
            body_bytes is not None
            and (type(body_bytes) is not int or not 0 <= body_bytes <= _MAX_BODY_BYTES)
        )
        or ((body_sha is None) != (body_bytes is None))
        or (body_sha is not None and not _valid_sha256(body_sha))
        or (code is not None and (type(code) is not int or isinstance(code, bool)))
    ):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")

    classification = payload["classification"]
    valid_entity = status == 200 and complete is True and within is True
    if classification == "PERMISSION_DENIED":
        valid = valid_entity and code == -1 and body_sha is not None
    elif classification == "SCHEMA_UNBOUND":
        valid = valid_entity and code == 0 and body_sha is not None
    elif classification == "SOURCE_ERROR":
        valid = (
            valid_entity
            and type(code) is int
            and not isinstance(code, bool)
            and code != 0
            and body_sha is not None
        )
    elif classification == "RESPONSE_REJECTED":
        valid = valid_entity and code is None and body_sha is not None
    elif classification == "CREDENTIAL_ECHO_REJECTED":
        valid = code is None and body_sha is None and body_bytes is None
    elif classification == "TRANSPORT_REJECTED":
        valid = code is None and not valid_entity
    else:
        valid = (
            status is None
            and complete is None
            and within is None
            and body_bytes is None
            and body_sha is None
            and code is None
        )
    if not valid:
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected")
    return {
        "descriptor_sha256": digest,
        "classification": classification,
        "authority_status": "UNBOUND",
    }


def _write_content_addressed(
    output_dir: str | Path,
    payload: dict[str, Any],
    *,
    token: str,
) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    descriptor = {**payload, "descriptor_sha256": digest}
    verify_jiaoch_historical_minute_diagnostic(descriptor)
    if _contains_semantic_token(descriptor, token):
        raise ValueError("Jiaoch diagnostic credential persistence rejected")
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
            prefix=f"{destination.name}.",
            suffix=".tmp",
            dir=str(directory),
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
        raise ValueError("content-addressed Jiaoch diagnostic mismatch")
    load_jiaoch_historical_minute_diagnostic(destination)
    return {
        "path": str(destination),
        "descriptor_sha256": digest,
        "classification": descriptor["classification"],
        "created": created,
    }


def load_jiaoch_historical_minute_diagnostic(
    path: str | Path,
) -> dict[str, Any]:
    candidate = Path(path)
    try:
        if not candidate.is_file() or candidate.stat().st_size > _MAX_DIAGNOSTIC_BYTES:
            raise ValueError
        payload = _strict_json_loads(candidate.read_bytes())
        verification = verify_jiaoch_historical_minute_diagnostic(payload)
        if candidate.name != f"{verification['descriptor_sha256']}.json":
            raise ValueError
    except (OSError, TypeError, ValueError):
        raise ValueError("Jiaoch historical-minute diagnostic descriptor rejected") from None
    return dict(payload)


def collect_jiaoch_historical_minute_diagnostic(
    *,
    source: TushareSource,
    ts_code: str,
    start_date: str,
    end_date: str,
    retrieved_at: str,
    output_dir: str | Path,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Issue exactly one fail-closed Jiaoch ``stk_mins`` schema probe."""

    _validate_source(source)
    request_semantics = _request_semantics(
        ts_code=ts_code,
        start_date=start_date,
        end_date=end_date,
    )
    timestamp = _aware_timestamp(retrieved_at)
    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or not math.isfinite(float(timeout_seconds))
        or float(timeout_seconds) <= 0
    ):
        raise ValueError("timeout_seconds must be finite and positive")
    request_body = _canonical_json(
        {
            "api_name": _INTERFACE,
            "token": source.token,
            "params": request_semantics["params"],
            "fields": "",
        }
    )
    transport = UrllibTushareTransport(proxy_url=source.proxy_url)
    try:
        response = transport.post(
            url=f"{source.api_url.rstrip('/')}{_PATH}",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "Connection": "close",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "quant-jiaoch-stk-mins-probe/1",
            },
            body=request_body,
            timeout_s=float(timeout_seconds),
            max_body_bytes=_MAX_BODY_BYTES,
        )
    except Exception:
        manifest = _transport_error_manifest(
            request_semantics=request_semantics,
            retrieved_at=timestamp,
        )
    else:
        manifest = _response_manifest(
            request_semantics=request_semantics,
            retrieved_at=timestamp,
            status=getattr(response, "status", None),
            body_complete=getattr(response, "body_complete", None),
            body=getattr(response, "body", None),
            token=source.token,
        )
    manifest["transport_binding"] = {
        "implementation": "app.research_pit_transport.UrllibTushareTransport",
        "collector_post_invocations": 1,
        "collector_http_retries": 0,
        "collector_fallbacks": 0,
        "redirect_policy": "refuse",
        "network_route": source.network_route,
    }
    return _write_content_addressed(output_dir, manifest, token=source.token)
