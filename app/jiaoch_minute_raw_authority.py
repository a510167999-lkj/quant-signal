"""Content-addressed raw Jiaoch response attempts without source authority."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import uuid
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from app.durable_io import fsync_directory


ATTEMPT_SCHEMA = "jiaoch-minute-raw-attempt/v1"
COLLECTOR_VERSION = "app.jiaoch_minute_raw_authority/1"
_SOURCE_ID = "jiaoch"
_API_ENDPOINTS = {
    "daily": "https://jiaoch.site/daily",
    "stk_mins": "https://jiaoch.site/stk_mins",
}
_REQUEST_PROTOCOL = "tushare-path-per-interface/v1"
_TRANSPORT = "app.research_pit_transport.UrllibTushareTransport"
_MAX_RAW_BYTES = 1024 * 1024
_MAX_ATTEMPT_BYTES = 64 * 1024
_MAX_ECHO_SCAN_DEPTH = 64
_REPARSE_ATTRIBUTE = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_TS_CODE_PATTERN = re.compile(r"(?:60[0135][0-9]{3}\.SH|(?:00[0-3]|30[0-2])[0-9]{3}\.SZ)")
_ALLOWED_CREDENTIAL_SLOTS = frozenset({"historical-minute"})
_STK_MINS_PARAM_KEYS = frozenset({"end_date", "freq", "start_date", "ts_code"})
_DAILY_PARAM_KEYS = frozenset({"trade_date", "ts_code"})
_DAILY_FIELDS = "ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount"
_ATTEMPT_ID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}"
)
_ATTEMPT_PATH_PATTERN = re.compile(r"attempts/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json")
_SECRET_PARAM_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "credential",
        "secret",
        "token",
    }
)
_ATTEMPT_FIELDS = frozenset(
    {
        "api_name",
        "attempt_id",
        "authority_status",
        "body_complete",
        "collector_version",
        "credential_echo_check",
        "credential_slot_id",
        "embargo_consumed",
        "endpoint",
        "final_oos_consumed",
        "http_status",
        "production_profile_registered",
        "production_recommendation_eligible",
        "raw_object",
        "raw_response_persisted",
        "request_semantics",
        "request_semantics_sha256",
        "retrieved_at",
        "route",
        "rows_published",
        "schema",
        "source_id",
    }
)


class _JsonObjectPairs(list):
    pass


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
        raise ValueError("Jiaoch raw attempt canonical JSON rejected") from None


def _strict_json_loads(raw: bytes) -> Any:
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
        raise ValueError("Jiaoch raw attempt JSON rejected") from None


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _aware_timestamp(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("retrieved_at must be an aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("retrieved_at must be an aware timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("retrieved_at must be an aware timestamp")
    return parsed.isoformat()


def _normalized_secret_key(value: str) -> str:
    return re.sub(r"[^a-z]", "", value.casefold())


def _canonical_params(params: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(params, Mapping) or not params:
        raise ValueError("request params rejected")
    result: dict[str, str] = {}
    for key, value in params.items():
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(value, str)
            or _normalized_secret_key(key) in _SECRET_PARAM_KEYS
        ):
            if isinstance(key, str) and _normalized_secret_key(key) in _SECRET_PARAM_KEYS:
                raise ValueError("secret request parameter rejected")
            raise ValueError("request params rejected")
        result[key] = value
    return result


def _closed_calibration_params(
    api_name: str,
    params: Mapping[str, str],
    fields: str,
) -> dict[str, str]:
    result = _canonical_params(params)
    ts_code = result.get("ts_code")
    if not isinstance(ts_code, str) or _TS_CODE_PATTERN.fullmatch(ts_code) is None:
        raise ValueError("Jiaoch calibration request ts_code rejected")
    if api_name == "stk_mins":
        if (
            set(result) != _STK_MINS_PARAM_KEYS
            or result.get("freq") not in {"1min", "5min"}
            or fields != ""
        ):
            raise ValueError("Jiaoch stk_mins calibration request rejected")
        try:
            start = datetime.strptime(result["start_date"], "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(result["end_date"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            raise ValueError("Jiaoch stk_mins calibration request rejected") from None
        if (
            start.strftime("%Y-%m-%d %H:%M:%S") != result["start_date"]
            or end.strftime("%Y-%m-%d %H:%M:%S") != result["end_date"]
            or start.date() != end.date()
            or start >= end
            or start.strftime("%H:%M:%S") != "09:00:00"
            or end.strftime("%H:%M:%S") != "16:00:00"
        ):
            raise ValueError("Jiaoch stk_mins calibration request rejected")
    elif api_name == "daily":
        if set(result) != _DAILY_PARAM_KEYS or fields != _DAILY_FIELDS:
            raise ValueError("Jiaoch daily calibration request rejected")
        try:
            parsed_date = datetime.strptime(result["trade_date"], "%Y%m%d")
        except ValueError:
            raise ValueError("Jiaoch daily calibration request rejected") from None
        if parsed_date.strftime("%Y%m%d") != result["trade_date"]:
            raise ValueError("Jiaoch daily calibration request rejected")
    else:
        raise ValueError("Jiaoch raw attempt api_name rejected")
    return result


def _json_semantically_echoes_credential(raw: bytes, credential: str) -> bool:
    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_JsonObjectPairs,
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    except RecursionError:
        raise ValueError("Jiaoch raw response credential echo scan rejected") from None

    stack = [(parsed, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > _MAX_ECHO_SCAN_DEPTH:
            raise ValueError("Jiaoch raw response credential echo scan rejected")
        if isinstance(value, str):
            if credential in value:
                return True
        elif isinstance(value, _JsonObjectPairs):
            for key, nested in value:
                stack.append((nested, depth + 1))
                stack.append((key, depth + 1))
        elif isinstance(value, list):
            for nested in value:
                stack.append((nested, depth + 1))
    return False


def _credential_echoes(raw: bytes, credential: str) -> bool:
    direct = credential.encode("utf-8")
    escaped = json.dumps(credential, ensure_ascii=True)[1:-1].encode("ascii")
    return direct in raw or escaped in raw or _json_semantically_echoes_credential(raw, credential)


def _request_semantics(
    *,
    api_name: str,
    params: Mapping[str, str],
    fields: str,
    credential: str,
) -> dict[str, Any]:
    if api_name not in _API_ENDPOINTS:
        raise ValueError("Jiaoch raw attempt api_name rejected")
    if not isinstance(fields, str):
        raise ValueError("Jiaoch raw attempt fields rejected")
    canonical_params = _closed_calibration_params(api_name, params, fields)
    request = {
        "api_name": api_name,
        "endpoint": _API_ENDPOINTS[api_name],
        "fields": fields,
        "method": "POST",
        "params": canonical_params,
    }
    if _credential_echoes(_canonical_json(request), credential):
        raise ValueError("secret request semantics rejected")
    return request


def _path_is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return False
    return path.is_symlink() or bool(
        int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
    )


def _safe_existing_directory(path: Path, label: str) -> Path:
    if not path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ValueError(f"{label} path rejected")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            metadata = current.lstat()
        except FileNotFoundError:
            raise ValueError(f"{label} path rejected") from None
        if not stat.S_ISDIR(metadata.st_mode) or _path_is_link_or_reparse(current):
            raise ValueError(f"{label} path contains a link or reparse point")
    return path.resolve(strict=True)


def _ensure_child_directory(parent: Path, name: str) -> Path:
    parent = _safe_existing_directory(parent, "Jiaoch authority directory")
    destination = parent / name
    try:
        destination.mkdir()
    except FileExistsError:
        pass
    else:
        fsync_directory(parent)
    return _safe_existing_directory(destination, "Jiaoch authority directory")


def _safe_existing_file(path: Path, label: str) -> Path:
    parent = _safe_existing_directory(path.parent, f"{label} parent")
    candidate = parent / path.name
    try:
        metadata = candidate.lstat()
    except FileNotFoundError:
        raise ValueError(f"{label} is missing") from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _path_is_link_or_reparse(candidate)
        or int(getattr(metadata, "st_nlink", 1)) != 1
    ):
        raise ValueError(f"{label} contains a link or reparse point")
    return candidate


def _validate_open_file_metadata(
    metadata: os.stat_result,
    *,
    label: str,
    max_bytes: int,
    expected_size: int | None,
) -> None:
    if (
        not stat.S_ISREG(metadata.st_mode)
        or int(getattr(metadata, "st_nlink", 1)) != 1
        or int(getattr(metadata, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
    ):
        raise ValueError(f"{label} contains a link or reparse point")
    if (
        metadata.st_size < 0
        or metadata.st_size > max_bytes
        or (expected_size is not None and metadata.st_size != expected_size)
    ):
        raise ValueError(f"{label} size rejected")


def _read_safe_file(
    path: Path,
    *,
    label: str,
    max_bytes: int,
    expected_size: int | None = None,
) -> bytes:
    candidate = _safe_existing_file(path, label)
    before_open = candidate.lstat()
    _validate_open_file_metadata(
        before_open,
        label=label,
        max_bytes=max_bytes,
        expected_size=expected_size,
    )
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(candidate, flags)
    except OSError:
        raise ValueError(f"{label} safe open rejected") from None
    try:
        opened = os.fstat(descriptor)
        _validate_open_file_metadata(
            opened,
            label=label,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
        if not os.path.samestat(before_open, opened):
            raise ValueError(f"{label} identity rejected")

        remaining = opened.st_size
        chunks: list[bytes] = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise ValueError(f"{label} size rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError(f"{label} size rejected")

        after_read = os.fstat(descriptor)
        _validate_open_file_metadata(
            after_read,
            label=label,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
        if not os.path.samestat(opened, after_read):
            raise ValueError(f"{label} identity rejected")
        try:
            path_after_read = candidate.lstat()
        except FileNotFoundError:
            raise ValueError(f"{label} identity rejected") from None
        if (
            not stat.S_ISREG(path_after_read.st_mode)
            or int(getattr(path_after_read, "st_nlink", 1)) != 1
            or int(getattr(path_after_read, "st_file_attributes", 0)) & _REPARSE_ATTRIBUTE
            or not os.path.samestat(after_read, path_after_read)
        ):
            raise ValueError(f"{label} identity rejected")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _write_create_only(
    path: Path,
    raw: bytes,
    *,
    label: str,
    reuse_identical: bool,
) -> bool:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        if not reuse_identical:
            _safe_existing_file(path, label)
            raise ValueError(f"{label} content-address conflict")
        existing = _read_safe_file(
            path,
            label=label,
            max_bytes=len(raw),
            expected_size=len(raw),
        )
        if hmac.compare_digest(existing, raw):
            return False
        raise ValueError(f"{label} content-address conflict")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink()
        finally:
            fsync_directory(path.parent)
        raise
    fsync_directory(path.parent)
    return True


def _content_addressed_directory(root: Path, kind: str, digest: str) -> Path:
    kind_root = _ensure_child_directory(root, kind)
    sha_root = _ensure_child_directory(kind_root, "sha256")
    return _ensure_child_directory(sha_root, digest[:2])


def _raw_relative_path(digest: str) -> str:
    return f"raw/sha256/{digest[:2]}/{digest}.body"


def _attempt_relative_path(digest: str) -> str:
    return f"attempts/sha256/{digest[:2]}/{digest}.json"


def publish_jiaoch_minute_raw_attempt(
    *,
    output_root: str | Path,
    raw_body: bytes,
    credential: str,
    credential_slot_id: str,
    api_name: str,
    params: Mapping[str, str],
    fields: str,
    retrieved_at: str,
    network_route: str,
    http_status: int,
    body_complete: bool,
) -> dict[str, Any]:
    """Persist one raw response and one independent, always-unbound attempt."""

    if (
        type(raw_body) is not bytes
        or len(raw_body) > _MAX_RAW_BYTES
        or not isinstance(credential, str)
        or not credential
    ):
        raise ValueError("Jiaoch raw response input rejected")
    if _credential_echoes(raw_body, credential):
        raise ValueError("Jiaoch raw response credential echo rejected")
    if credential_slot_id not in _ALLOWED_CREDENTIAL_SLOTS:
        raise ValueError("Jiaoch credential slot id rejected")
    request = _request_semantics(
        api_name=api_name,
        params=params,
        fields=fields,
        credential=credential,
    )
    if network_route not in {"direct", "loopback_http_proxy"}:
        raise ValueError("Jiaoch network route rejected")
    if (
        type(http_status) is not int
        or not 100 <= http_status <= 599
        or type(body_complete) is not bool
    ):
        raise ValueError("Jiaoch HTTP entity metadata rejected")
    canonical_retrieved_at = _aware_timestamp(retrieved_at)

    root = _safe_existing_directory(Path(output_root), "Jiaoch authority root")
    raw_sha256 = _sha256(raw_body)
    raw_directory = _content_addressed_directory(root, "raw", raw_sha256)
    raw_path = raw_directory / f"{raw_sha256}.body"
    raw_created = _write_create_only(
        raw_path,
        raw_body,
        label="Jiaoch raw content-addressed object",
        reuse_identical=True,
    )

    attempt_id = str(uuid.uuid4())
    request_raw = _canonical_json(request)
    attempt = {
        "api_name": api_name,
        "attempt_id": attempt_id,
        "authority_status": "UNBOUND",
        "body_complete": body_complete,
        "collector_version": COLLECTOR_VERSION,
        "credential_echo_check": "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE",
        "credential_slot_id": credential_slot_id,
        "embargo_consumed": False,
        "endpoint": _API_ENDPOINTS[api_name],
        "final_oos_consumed": False,
        "http_status": http_status,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "raw_object": {
            "bytes": len(raw_body),
            "relative_path": _raw_relative_path(raw_sha256),
            "sha256": raw_sha256,
        },
        "raw_response_persisted": True,
        "request_semantics": request,
        "request_semantics_sha256": _sha256(request_raw),
        "retrieved_at": canonical_retrieved_at,
        "route": {
            "network_route": network_route,
            "redirect_policy": "refuse",
            "request_protocol": _REQUEST_PROTOCOL,
            "transport": _TRANSPORT,
        },
        "rows_published": False,
        "schema": ATTEMPT_SCHEMA,
        "source_id": _SOURCE_ID,
    }
    attempt_raw = _canonical_json(attempt)
    if len(attempt_raw) > _MAX_ATTEMPT_BYTES:
        raise ValueError("Jiaoch raw attempt size rejected")
    attempt_sha256 = _sha256(attempt_raw)
    attempt_directory = _content_addressed_directory(root, "attempts", attempt_sha256)
    attempt_path = attempt_directory / f"{attempt_sha256}.json"
    attempt_created = _write_create_only(
        attempt_path,
        attempt_raw,
        label="Jiaoch raw attempt",
        reuse_identical=False,
    )
    attempt_relative_path = _attempt_relative_path(attempt_sha256)
    verify_jiaoch_minute_raw_attempt(
        output_root=root,
        attempt_relative_path=attempt_relative_path,
        expected_attempt_sha256=attempt_sha256,
    )
    return {
        "attempt_created": attempt_created,
        "attempt_id": attempt_id,
        "attempt_relative_path": attempt_relative_path,
        "attempt_sha256": attempt_sha256,
        "raw_created": raw_created,
        "raw_relative_path": _raw_relative_path(raw_sha256),
        "raw_sha256": raw_sha256,
    }


def _validated_attempt_path(
    *,
    root: Path,
    attempt_relative_path: str,
    expected_attempt_sha256: str,
) -> Path:
    if (
        not isinstance(attempt_relative_path, str)
        or not isinstance(expected_attempt_sha256, str)
        or _SHA256_PATTERN.fullmatch(expected_attempt_sha256) is None
    ):
        raise ValueError("Jiaoch raw attempt path rejected")
    match = _ATTEMPT_PATH_PATTERN.fullmatch(attempt_relative_path)
    if (
        match is None
        or match.group(1) != expected_attempt_sha256[:2]
        or match.group(2) != expected_attempt_sha256
    ):
        raise ValueError("Jiaoch raw attempt path rejected")
    return _safe_existing_file(
        root / Path(*attempt_relative_path.split("/")),
        "Jiaoch raw attempt",
    )


def _verify_attempt_payload(payload: Any) -> None:
    if (
        not isinstance(payload, Mapping)
        or set(payload) != _ATTEMPT_FIELDS
        or payload.get("schema") != ATTEMPT_SCHEMA
        or payload.get("source_id") != _SOURCE_ID
        or payload.get("collector_version") != COLLECTOR_VERSION
        or payload.get("credential_echo_check") != "PASSED_AT_COLLECTION_NOT_OFFLINE_REPRODUCIBLE"
        or payload.get("raw_response_persisted") is not True
        or payload.get("authority_status") != "UNBOUND"
        or payload.get("production_profile_registered") is not False
        or payload.get("production_recommendation_eligible") is not False
        or payload.get("embargo_consumed") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("rows_published") is not False
    ):
        raise ValueError("Jiaoch raw attempt descriptor rejected")
    if (
        not isinstance(payload.get("attempt_id"), str)
        or _ATTEMPT_ID_PATTERN.fullmatch(payload["attempt_id"]) is None
        or payload.get("credential_slot_id") not in _ALLOWED_CREDENTIAL_SLOTS
        or payload.get("api_name") not in _API_ENDPOINTS
        or payload.get("endpoint") != _API_ENDPOINTS[payload["api_name"]]
        or type(payload.get("http_status")) is not int
        or not 100 <= payload["http_status"] <= 599
        or type(payload.get("body_complete")) is not bool
    ):
        raise ValueError("Jiaoch raw attempt descriptor rejected")
    _aware_timestamp(payload.get("retrieved_at"))
    if payload.get("route") != {
        "network_route": payload["route"].get("network_route")
        if isinstance(payload.get("route"), Mapping)
        else None,
        "redirect_policy": "refuse",
        "request_protocol": _REQUEST_PROTOCOL,
        "transport": _TRANSPORT,
    } or payload["route"]["network_route"] not in {
        "direct",
        "loopback_http_proxy",
    }:
        raise ValueError("Jiaoch raw attempt route rejected")

    request = payload.get("request_semantics")
    if (
        not isinstance(request, Mapping)
        or set(request) != {"api_name", "endpoint", "fields", "method", "params"}
        or request.get("api_name") != payload["api_name"]
        or request.get("endpoint") != payload["endpoint"]
        or request.get("method") != "POST"
        or not isinstance(request.get("fields"), str)
    ):
        raise ValueError("Jiaoch raw attempt request semantics rejected")
    try:
        canonical_params = _closed_calibration_params(
            payload["api_name"],
            request.get("params"),
            request["fields"],
        )
    except ValueError:
        raise ValueError("Jiaoch raw attempt request semantics rejected") from None
    if dict(request["params"]) != canonical_params:
        raise ValueError("Jiaoch raw attempt request semantics rejected")
    request_sha256 = _sha256(_canonical_json(request))
    if not isinstance(payload.get("request_semantics_sha256"), str) or not hmac.compare_digest(
        payload["request_semantics_sha256"],
        request_sha256,
    ):
        raise ValueError("Jiaoch raw attempt request semantics rejected")

    raw_object = payload.get("raw_object")
    if (
        not isinstance(raw_object, Mapping)
        or set(raw_object) != {"bytes", "relative_path", "sha256"}
        or not isinstance(raw_object.get("sha256"), str)
        or _SHA256_PATTERN.fullmatch(raw_object["sha256"]) is None
        or type(raw_object.get("bytes")) is not int
        or not 0 <= raw_object["bytes"] <= _MAX_RAW_BYTES
        or raw_object.get("relative_path") != _raw_relative_path(raw_object["sha256"])
    ):
        raise ValueError("Jiaoch raw object path rejected")


def verify_jiaoch_minute_raw_attempt(
    *,
    output_root: str | Path,
    attempt_relative_path: str,
    expected_attempt_sha256: str,
) -> dict[str, Any]:
    """Offline verification of one attempt and its raw body; no token or network."""

    root = _safe_existing_directory(Path(output_root), "Jiaoch authority root")
    attempt_path = _validated_attempt_path(
        root=root,
        attempt_relative_path=attempt_relative_path,
        expected_attempt_sha256=expected_attempt_sha256,
    )
    attempt_raw = _read_safe_file(
        attempt_path,
        label="Jiaoch raw attempt",
        max_bytes=_MAX_ATTEMPT_BYTES,
    )
    if not hmac.compare_digest(_sha256(attempt_raw), expected_attempt_sha256):
        raise ValueError("Jiaoch raw attempt content address rejected")
    payload = _strict_json_loads(attempt_raw)
    if not hmac.compare_digest(attempt_raw, _canonical_json(payload)):
        raise ValueError("Jiaoch raw attempt descriptor rejected")
    _verify_attempt_payload(payload)

    raw_object = payload["raw_object"]
    raw_path = root / Path(*raw_object["relative_path"].split("/"))
    raw_body = _read_safe_file(
        raw_path,
        label="Jiaoch raw object",
        max_bytes=_MAX_RAW_BYTES,
        expected_size=raw_object["bytes"],
    )
    if not hmac.compare_digest(_sha256(raw_body), raw_object["sha256"]):
        raise ValueError("Jiaoch raw object verification rejected")
    return {
        "api_name": payload["api_name"],
        "attempt_id": payload["attempt_id"],
        "attempt_sha256": expected_attempt_sha256,
        "authority_status": "UNBOUND",
        "body_complete": payload["body_complete"],
        "credential_slot_id": payload["credential_slot_id"],
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "http_status": payload["http_status"],
        "network_route": payload["route"]["network_route"],
        "production_recommendation_eligible": False,
        "raw_bytes": len(raw_body),
        "raw_relative_path": raw_object["relative_path"],
        "raw_sha256": raw_object["sha256"],
        "retrieved_at": payload["retrieved_at"],
        "request_semantics": dict(payload["request_semantics"]),
    }
