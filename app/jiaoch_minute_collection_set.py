"""Sealed three-request Jiaoch historical-minute collection sets."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping
import uuid

from app import jiaoch_minute_raw_authority as raw_authority
from app.jiaoch_minute_raw_authority import (
    publish_jiaoch_minute_raw_attempt,
    verify_jiaoch_minute_raw_attempt,
)
from app.jiaoch_minute_reconciliation import (
    JiaochMinuteCrossFrequencyReconciliationReceipt,
    reconcile_jiaoch_minute_raw_bodies_unbound,
)
from app.research_pit_transport import UrllibTushareTransport


__all__ = (
    "reconcile_verified_jiaoch_minute_collection_set",
    "verify_jiaoch_minute_collection_set",
)

COLLECTION_SET_SCHEMA = "jiaoch-minute-collection-set/v1"
COLLECTOR_VERSION = "app.jiaoch_minute_collection_set/1"

_PRODUCER_SCHEMA = "jiaoch-minute-collection-producer/v1"
_SOURCE_ID = "jiaoch"
_CREDENTIAL_SLOT_ID = "historical-minute"
_AUTHORITY_SCOPE = "RAW_SOURCE_COLLECTION_ONLY"
_MAX_BODY_BYTES = 1024 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_PRODUCER_SOURCE_BYTES = 4 * 1024 * 1024
_DAILY_FIELDS_TEXT = "ts_code,trade_date,open,high,low,close,vol,amount,ah_vol,ah_amount"
_MINUTE_FIELDS = [
    "ts_code",
    "trade_time",
    "open",
    "close",
    "high",
    "low",
    "vol",
    "amount",
]
_DAILY_FIELDS = [
    "ts_code",
    "trade_date",
    "open",
    "high",
    "low",
    "close",
    "vol",
    "amount",
    "ah_vol",
    "ah_amount",
]
_SUPPORTED_TS_CODE = re.compile(r"(?:60[0135][0-9]{3}\.SH|(?:00[0-3]|30[0-2])[0-9]{3}\.SZ)")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
_UUID4_PATTERN = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_MANIFEST_PATH_PATTERN = re.compile(r"collection_sets/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json")
_REPARSE_ATTRIBUTE = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
_MANIFEST_FIELDS = frozenset(
    {
        "attempts",
        "authority_scope",
        "collection_binding_status",
        "collection_call_id",
        "collector_version",
        "credential_binding",
        "credential_slot_id",
        "embargo_consumed",
        "evidence_complete",
        "execution_session",
        "final_oos_consumed",
        "producer_binding",
        "production_profile_registered",
        "production_recommendation_eligible",
        "requested_ts_code",
        "retrieved_at",
        "rows_published",
        "runtime_mapping_descriptor",
        "schema",
        "source_id",
    }
)
_ATTEMPT_BINDING_FIELDS = frozenset(
    {
        "api_name",
        "attempt_id",
        "attempt_relative_path",
        "attempt_sha256",
        "endpoint",
        "fields",
        "params",
        "raw_relative_path",
        "raw_sha256",
        "role",
        "route_id",
    }
)
_PRODUCER_FILES = (
    "durable_io.py",
    "jiaoch_credential_slots.py",
    "jiaoch_minute_collection_set.py",
    "jiaoch_minute_raw_authority.py",
    "research_pit_transport.py",
)


def _transport_factory() -> UrllibTushareTransport:
    return UrllibTushareTransport(proxy_url=None)


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
        raise ValueError("Jiaoch minute collection canonical JSON rejected") from None


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


def _execution_session(value: Any) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise ValueError("Jiaoch minute collection execution_session rejected")
    return value


def _ts_code(value: Any) -> str:
    if type(value) is not str or _SUPPORTED_TS_CODE.fullmatch(value) is None:
        raise ValueError("Jiaoch minute collection ts_code rejected")
    return value


def _aware_timestamp(value: Any) -> str:
    if type(value) is not str:
        raise ValueError("Jiaoch minute collection retrieved_at rejected")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Jiaoch minute collection retrieved_at rejected") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Jiaoch minute collection retrieved_at rejected")
    return parsed.isoformat()


def _timeout_seconds(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not 0 < float(value) <= 300
    ):
        raise ValueError("Jiaoch minute collection timeout rejected")
    return float(value)


def _sha256_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    return value


def _uuid4_text(value: Any, *, label: str) -> str:
    if type(value) is not str or _UUID4_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} rejected")
    return value


def _request_specs(ts_code: str, session: date) -> tuple[dict[str, Any], ...]:
    day = session.isoformat()
    return (
        {
            "api_name": "stk_mins",
            "endpoint": "https://jiaoch.site/stk_mins",
            "fields": "",
            "params": {
                "end_date": f"{day} 16:00:00",
                "freq": "1min",
                "start_date": f"{day} 09:00:00",
                "ts_code": ts_code,
            },
            "response_fields": _MINUTE_FIELDS,
            "role": "one-minute",
            "route_id": "historical-minute:stk_mins",
        },
        {
            "api_name": "stk_mins",
            "endpoint": "https://jiaoch.site/stk_mins",
            "fields": "",
            "params": {
                "end_date": f"{day} 16:00:00",
                "freq": "5min",
                "start_date": f"{day} 09:00:00",
                "ts_code": ts_code,
            },
            "response_fields": _MINUTE_FIELDS,
            "role": "five-minute",
            "route_id": "historical-minute:stk_mins",
        },
        {
            "api_name": "daily",
            "endpoint": "https://jiaoch.site/daily",
            "fields": _DAILY_FIELDS_TEXT,
            "params": {
                "trade_date": session.strftime("%Y%m%d"),
                "ts_code": ts_code,
            },
            "response_fields": _DAILY_FIELDS,
            "role": "daily-calibration",
            "route_id": "historical-minute:calibration-daily",
        },
    )


def _response_interface_identity(raw: bytes, *, expected_fields: list[str], role: str) -> None:
    payload = _strict_json_loads(raw, label=f"Jiaoch {role} response")
    if (
        type(payload) is not dict
        or set(payload) != {"code", "data", "msg"}
        or type(payload["code"]) is not int
        or payload["code"] != 0
        or type(payload["msg"]) is not str
        or payload["msg"] != "success"
    ):
        raise ValueError(f"Jiaoch {role} provider status rejected")
    data = payload["data"]
    if (
        type(data) is not dict
        or set(data) != {"fields", "items"}
        or type(data["fields"]) is not list
        or data["fields"] != expected_fields
        or type(data["items"]) is not list
    ):
        raise ValueError(f"Jiaoch {role} interface identity rejected")


def _producer_binding() -> dict[str, Any]:
    source_root = _safe_existing_directory(
        Path(__file__).parent,
        "Jiaoch minute collection producer root",
    )
    entries = []
    for filename in _PRODUCER_FILES:
        path = source_root / filename
        raw = _read_safe_file(
            path,
            label="Jiaoch minute collection producer source",
            max_bytes=_MAX_PRODUCER_SOURCE_BYTES,
        )
        entries.append(
            {
                "path": f"app/{filename}",
                "sha256": _sha256(raw),
            }
        )
    identity = {
        "collector_version": COLLECTOR_VERSION,
        "entries": entries,
        "schema": _PRODUCER_SCHEMA,
    }
    return {
        **identity,
        "root_sha256": _sha256(_canonical_json(identity)),
    }


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


def _validate_open_file(
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
    before = candidate.lstat()
    _validate_open_file(
        before,
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
        _validate_open_file(
            opened,
            label=label,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
        if not os.path.samestat(before, opened):
            raise ValueError(f"{label} identity rejected")
        remaining = opened.st_size
        chunks = []
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                raise ValueError(f"{label} size rejected")
            chunks.append(chunk)
            remaining -= len(chunk)
        if os.read(descriptor, 1):
            raise ValueError(f"{label} size rejected")
        after = os.fstat(descriptor)
        _validate_open_file(
            after,
            label=label,
            max_bytes=max_bytes,
            expected_size=expected_size,
        )
        path_after = candidate.lstat()
        if (
            not os.path.samestat(opened, after)
            or not os.path.samestat(after, path_after)
            or _path_is_link_or_reparse(candidate)
        ):
            raise ValueError(f"{label} identity rejected")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _attempt_binding(spec: Mapping[str, Any], publication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "api_name": spec["api_name"],
        "attempt_id": publication["attempt_id"],
        "attempt_relative_path": publication["attempt_relative_path"],
        "attempt_sha256": publication["attempt_sha256"],
        "endpoint": spec["endpoint"],
        "fields": spec["fields"],
        "params": spec["params"],
        "raw_relative_path": publication["raw_relative_path"],
        "raw_sha256": publication["raw_sha256"],
        "role": spec["role"],
        "route_id": spec["route_id"],
    }


def _request_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Accept-Encoding": "identity",
        "Connection": "close",
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "quant-jiaoch-minute-collection-set/1",
    }


def _collect_jiaoch_minute_collection_set_with_route_credential(
    *,
    credential: str,
    generation_id: str,
    policy_sha256: str,
    output_root: str | Path,
    requested_ts_code: str,
    execution_session: date,
    retrieved_at: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Private implementation called only by the sealed credential entrypoint."""

    if type(credential) is not str or not credential:
        raise ValueError("Jiaoch minute collection route credential rejected")
    generation = _uuid4_text(generation_id, label="Jiaoch runtime generation_id")
    policy = _sha256_text(policy_sha256, label="Jiaoch routing policy_sha256")
    root = _safe_existing_directory(Path(output_root), "Jiaoch minute collection root")
    ts_code = _ts_code(requested_ts_code)
    session = _execution_session(execution_session)
    timestamp = _aware_timestamp(retrieved_at)
    timeout = _timeout_seconds(timeout_seconds)
    specs = _request_specs(ts_code, session)
    transport = _transport_factory()
    attempts = []
    for spec in specs:
        request_body = _canonical_json(
            {
                "api_name": spec["api_name"],
                "fields": spec["fields"],
                "params": spec["params"],
                "token": credential,
            }
        )
        response = transport.post(
            url=spec["endpoint"],
            headers=_request_headers(),
            body=request_body,
            timeout_s=timeout,
            max_body_bytes=_MAX_BODY_BYTES,
        )
        raw_body = getattr(response, "body", None)
        http_status = getattr(response, "status", None)
        body_complete = getattr(response, "body_complete", None)
        publication = publish_jiaoch_minute_raw_attempt(
            output_root=root,
            raw_body=raw_body,
            credential=credential,
            credential_slot_id=_CREDENTIAL_SLOT_ID,
            api_name=spec["api_name"],
            params=spec["params"],
            fields=spec["fields"],
            retrieved_at=timestamp,
            network_route="direct",
            http_status=http_status,
            body_complete=body_complete,
        )
        if http_status != 200 or body_complete is not True:
            raise ValueError(f"Jiaoch {spec['role']} HTTP entity rejected")
        _response_interface_identity(
            raw_body,
            expected_fields=spec["response_fields"],
            role=spec["role"],
        )
        attempts.append(_attempt_binding(spec, publication))

    manifest = {
        "attempts": attempts,
        "authority_scope": _AUTHORITY_SCOPE,
        "collection_binding_status": "BOUND_TO_SINGLE_CLOSED_RUNTIME_CALL",
        "collection_call_id": str(uuid.uuid4()),
        "collector_version": COLLECTOR_VERSION,
        "credential_binding": {
            "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
            "credential_proof_claimed": False,
            "same_runtime_credential_used": True,
        },
        "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "embargo_consumed": False,
        "evidence_complete": True,
        "execution_session": session.isoformat(),
        "final_oos_consumed": False,
        "producer_binding": _producer_binding(),
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "requested_ts_code": ts_code,
        "retrieved_at": timestamp,
        "rows_published": False,
        "runtime_mapping_descriptor": {
            "credential_proof_status": "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF",
            "generation_id": generation,
            "policy_sha256": policy,
        },
        "schema": COLLECTION_SET_SCHEMA,
        "source_id": _SOURCE_ID,
    }
    manifest_raw = _canonical_json(manifest)
    if len(manifest_raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("Jiaoch minute collection manifest size rejected")
    digest = _sha256(manifest_raw)
    directory = raw_authority._content_addressed_directory(root, "collection_sets", digest)
    path = directory / f"{digest}.json"
    created = raw_authority._write_create_only(
        path,
        manifest_raw,
        label="Jiaoch minute collection set",
        reuse_identical=False,
    )
    relative_path = f"collection_sets/sha256/{digest[:2]}/{digest}.json"
    verify_jiaoch_minute_collection_set(
        output_root=root,
        collection_set_relative_path=relative_path,
        expected_collection_set_sha256=digest,
    )
    return {
        "collection_set_created": created,
        "collection_set_relative_path": relative_path,
        "collection_set_sha256": digest,
    }


def _manifest_path(
    *,
    root: Path,
    relative_path: str,
    expected_sha256: str,
) -> Path:
    digest = _sha256_text(expected_sha256, label="Jiaoch collection set sha256")
    if type(relative_path) is not str:
        raise ValueError("Jiaoch minute collection set path rejected")
    match = _MANIFEST_PATH_PATTERN.fullmatch(relative_path)
    if match is None or match.group(1) != digest[:2] or match.group(2) != digest:
        raise ValueError("Jiaoch minute collection set path rejected")
    return _safe_existing_file(
        root / Path(*relative_path.split("/")),
        "Jiaoch minute collection set",
    )


def _verified_raw_body(
    *,
    root: Path,
    binding: Mapping[str, Any],
    expected_spec: Mapping[str, Any],
    expected_retrieved_at: str,
) -> bytes:
    verification = verify_jiaoch_minute_raw_attempt(
        output_root=root,
        attempt_relative_path=binding["attempt_relative_path"],
        expected_attempt_sha256=binding["attempt_sha256"],
    )
    expected_summary = {
        "api_name": expected_spec["api_name"],
        "body_complete": True,
        "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "http_status": 200,
        "network_route": "direct",
        "raw_relative_path": binding["raw_relative_path"],
        "retrieved_at": expected_retrieved_at,
        "request_semantics": {
            "api_name": expected_spec["api_name"],
            "endpoint": expected_spec["endpoint"],
            "fields": expected_spec["fields"],
            "method": "POST",
            "params": expected_spec["params"],
        },
    }
    if any(verification.get(key) != value for key, value in expected_summary.items()):
        raise ValueError("Jiaoch minute collection attempt binding rejected")
    if (
        verification.get("attempt_id") != binding["attempt_id"]
        or verification.get("raw_sha256") != binding["raw_sha256"]
    ):
        raise ValueError("Jiaoch minute collection attempt binding rejected")
    raw = _read_safe_file(
        root / Path(*binding["raw_relative_path"].split("/")),
        label="Jiaoch minute collection raw object",
        max_bytes=_MAX_BODY_BYTES,
        expected_size=verification["raw_bytes"],
    )
    if not hmac.compare_digest(_sha256(raw), binding["raw_sha256"]):
        raise ValueError("Jiaoch minute collection raw binding rejected")
    _response_interface_identity(
        raw,
        expected_fields=expected_spec["response_fields"],
        role=expected_spec["role"],
    )
    return raw


def _verify_and_load_collection_set(
    *,
    output_root: str | Path,
    collection_set_relative_path: str,
    expected_collection_set_sha256: str,
) -> tuple[dict[str, Any], dict[str, bytes]]:
    root = _safe_existing_directory(Path(output_root), "Jiaoch minute collection root")
    path = _manifest_path(
        root=root,
        relative_path=collection_set_relative_path,
        expected_sha256=expected_collection_set_sha256,
    )
    raw = _read_safe_file(
        path,
        label="Jiaoch minute collection set",
        max_bytes=_MAX_MANIFEST_BYTES,
    )
    if not hmac.compare_digest(_sha256(raw), expected_collection_set_sha256):
        raise ValueError("Jiaoch minute collection set content address rejected")
    payload = _strict_json_loads(raw, label="Jiaoch minute collection set")
    if (
        type(payload) is not dict
        or set(payload) != _MANIFEST_FIELDS
        or not hmac.compare_digest(raw, _canonical_json(payload))
        or payload.get("schema") != COLLECTION_SET_SCHEMA
        or payload.get("source_id") != _SOURCE_ID
        or payload.get("collector_version") != COLLECTOR_VERSION
        or payload.get("credential_slot_id") != _CREDENTIAL_SLOT_ID
        or payload.get("authority_scope") != _AUTHORITY_SCOPE
        or payload.get("collection_binding_status") != "BOUND_TO_SINGLE_CLOSED_RUNTIME_CALL"
        or payload.get("evidence_complete") is not True
        or payload.get("embargo_consumed") is not False
        or payload.get("final_oos_consumed") is not False
        or payload.get("production_profile_registered") is not False
        or payload.get("production_recommendation_eligible") is not False
        or payload.get("rows_published") is not False
    ):
        raise ValueError("Jiaoch minute collection set descriptor rejected")
    _uuid4_text(payload.get("collection_call_id"), label="Jiaoch collection_call_id")
    timestamp = _aware_timestamp(payload.get("retrieved_at"))
    session_raw = payload.get("execution_session")
    if type(session_raw) is not str:
        raise ValueError("Jiaoch minute collection execution_session rejected")
    try:
        session = date.fromisoformat(session_raw)
    except ValueError:
        raise ValueError("Jiaoch minute collection execution_session rejected") from None
    if session.isoformat() != session_raw:
        raise ValueError("Jiaoch minute collection execution_session rejected")
    ts_code = _ts_code(payload.get("requested_ts_code"))

    runtime = payload.get("runtime_mapping_descriptor")
    if (
        type(runtime) is not dict
        or set(runtime) != {"credential_proof_status", "generation_id", "policy_sha256"}
        or runtime.get("credential_proof_status") != "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF"
    ):
        raise ValueError("Jiaoch runtime mapping descriptor rejected")
    _uuid4_text(runtime.get("generation_id"), label="Jiaoch runtime generation_id")
    _sha256_text(runtime.get("policy_sha256"), label="Jiaoch routing policy_sha256")
    if payload.get("credential_binding") != {
        "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
        "credential_proof_claimed": False,
        "same_runtime_credential_used": True,
    }:
        raise ValueError("Jiaoch minute credential binding rejected")
    if payload.get("producer_binding") != _producer_binding():
        raise ValueError("Jiaoch minute collection producer binding rejected")

    attempts = payload.get("attempts")
    specs = _request_specs(ts_code, session)
    if type(attempts) is not list or len(attempts) != len(specs):
        raise ValueError("Jiaoch minute collection attempts rejected")
    bodies = {}
    attempt_ids = set()
    attempt_digests = set()
    for binding, spec in zip(attempts, specs, strict=True):
        if (
            type(binding) is not dict
            or set(binding) != _ATTEMPT_BINDING_FIELDS
            or binding.get("role") != spec["role"]
            or binding.get("route_id") != spec["route_id"]
            or binding.get("api_name") != spec["api_name"]
            or binding.get("endpoint") != spec["endpoint"]
            or binding.get("fields") != spec["fields"]
            or binding.get("params") != spec["params"]
        ):
            raise ValueError("Jiaoch minute collection attempt descriptor rejected")
        _uuid4_text(binding.get("attempt_id"), label="Jiaoch attempt_id")
        _sha256_text(binding.get("attempt_sha256"), label="Jiaoch attempt sha256")
        _sha256_text(binding.get("raw_sha256"), label="Jiaoch raw sha256")
        attempt_ids.add(binding["attempt_id"])
        attempt_digests.add(binding["attempt_sha256"])
        bodies[spec["role"]] = _verified_raw_body(
            root=root,
            binding=binding,
            expected_spec=spec,
            expected_retrieved_at=timestamp,
        )
    if len(attempt_ids) != 3 or len(attempt_digests) != 3:
        raise ValueError("Jiaoch minute collection attempts rejected")
    if timestamp != payload["retrieved_at"]:
        raise ValueError("Jiaoch minute collection retrieved_at rejected")
    return payload, bodies


def verify_jiaoch_minute_collection_set(
    *,
    output_root: str | Path,
    collection_set_relative_path: str,
    expected_collection_set_sha256: str,
) -> dict[str, Any]:
    """Verify a collection set and all three raw attempts without token or network."""

    payload, _ = _verify_and_load_collection_set(
        output_root=output_root,
        collection_set_relative_path=collection_set_relative_path,
        expected_collection_set_sha256=expected_collection_set_sha256,
    )
    return {
        "collection_set_sha256": expected_collection_set_sha256,
        "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
        "requested_ts_code": payload["requested_ts_code"],
        "verified": True,
    }


def reconcile_verified_jiaoch_minute_collection_set(
    *,
    output_root: str | Path,
    collection_set_relative_path: str,
    expected_collection_set_sha256: str,
    stable_security_id: str,
    pit_security_descriptor_sha256: str,
    board: str,
) -> JiaochMinuteCrossFrequencyReconciliationReceipt:
    """Reconcile only after an explicit verified collection-set binding."""

    payload, bodies = _verify_and_load_collection_set(
        output_root=output_root,
        collection_set_relative_path=collection_set_relative_path,
        expected_collection_set_sha256=expected_collection_set_sha256,
    )
    receipt = reconcile_jiaoch_minute_raw_bodies_unbound(
        one_minute_response_body=bodies["one-minute"],
        five_minute_response_body=bodies["five-minute"],
        daily_response_body=bodies["daily-calibration"],
        requested_ts_code=payload["requested_ts_code"],
        execution_session=date.fromisoformat(payload["execution_session"]),
        stable_security_id=stable_security_id,
        pit_security_descriptor_sha256=pit_security_descriptor_sha256,
        board=board,
    )
    return replace(
        receipt,
        collection_set_status="VERIFIED",
        collection_set_sha256=expected_collection_set_sha256,
        reconciliation_status="CROSS_FREQUENCY_RECONCILED_WITH_VERIFIED_COLLECTION_SET",
        source_authority_status="COLLECTION_SET_VERIFIED",
    )
