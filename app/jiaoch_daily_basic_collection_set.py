"""One-route, content-addressed Jiaoch ``daily_basic`` collection sets.

This is intentionally separate from ``jiaoch_points_collection_set``.  The
latter owns a two-interface daily_basic+moneyflow contract; using it here
would collect an unplanned interface.  This module closes exactly one
points-primary route and retains the same raw-attempt verification boundary.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
import hashlib
import hmac
import json
from pathlib import Path
import re
from typing import Any
import uuid

from app import jiaoch_points_collection_set as points_common
from app import jiaoch_points_raw_authority as raw_authority


COLLECTION_SET_SCHEMA = "jiaoch-factor-v3-daily-basic-collection-set/v1"
COLLECTOR_VERSION = "app.jiaoch_daily_basic_collection_set/1"
_SOURCE_ID = "jiaoch"
_CREDENTIAL_SLOT_ID = "points-primary"
_AUTHORITY_SCOPE = "RAW_SOURCE_COLLECTION_ONLY"
_MAX_BODY_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 128 * 1024
_MAX_PRODUCER_SOURCE_BYTES = 4 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_MANIFEST_RE = re.compile(r"daily_basic_collection_sets/sha256/([0-9a-f]{2})/([0-9a-f]{64})\.json")
_SESSION_INDEX_SCHEMA = "jiaoch-factor-v3-daily-basic-session-index/v1"
_PRODUCER_FILES = (
    "durable_io.py",
    "factor_v3_daily_basic_runner.py",
    "jiaoch_credential_slots.py",
    "jiaoch_daily_basic_collection_set.py",
    "jiaoch_points_collection_set.py",
    "jiaoch_points_raw_authority.py",
    "research_pit_transport.py",
)
_MANIFEST_FIELDS = frozenset(
    {
        "attempts", "authority_scope", "collection_binding_status", "collection_call_id",
        "collector_version", "credential_binding", "credential_slot_id", "embargo_consumed",
        "evidence_complete", "final_oos_consumed", "producer_binding",
        "production_profile_registered", "production_recommendation_eligible", "retrieved_at",
        "rows_published", "runtime_mapping_descriptor", "schema", "source_id", "trade_date",
    }
)
_ATTEMPT_FIELDS = frozenset(
    {
        "api_name", "attempt_id", "attempt_relative_path", "attempt_sha256", "endpoint", "fields",
        "params", "raw_relative_path", "raw_sha256", "role", "route_id",
    }
)
_SESSION_INDEX_FIELDS = frozenset(
    {
        "collection_set_relative_path",
        "collection_set_sha256",
        "generation_id",
        "policy_sha256",
        "producer_root_sha256",
        "schema",
        "trade_date",
    }
)
_FAILURE_SCHEMA = "jiaoch-factor-v3-daily-basic-collection-failure/v2"
_SAFE_FAILURE_CODES = frozenset(
    {
        "http_entity_rejected",
        "interface_identity_rejected",
        "provider_status_rejected",
        "raw_publication_rejected",
        "response_shape_rejected",
        "row_integrity_rejected",
        "transport_exception",
    }
)
_SAFE_FAILURE_EXCEPTION_TYPES = frozenset(
    {
        "ConnectionError",
        "HTTPError",
        "OSError",
        "Other",
        "PITCollectionError",
        "SSLError",
        "TimeoutError",
        "TypeError",
        "URLError",
        "UnicodeDecodeError",
        "ValueError",
    }
)


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Jiaoch daily_basic collection canonical JSON rejected") from exc


class JiaochDailyBasicCollectionError(ValueError):
    """A collection failure carrying only a bounded, non-secret diagnosis."""

    def __init__(self, diagnostic: Mapping[str, Any]) -> None:
        self.diagnostic = json.loads(_canonical_json(diagnostic))
        super().__init__("Jiaoch daily_basic collection failed")


def _safe_failure_exception_type(exc: BaseException) -> str:
    name = type(exc).__name__
    return name if name in _SAFE_FAILURE_EXCEPTION_TYPES else "Other"


def _safe_failure_status(value: Any) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _safe_failure_body_complete(value: Any) -> bool | None:
    return value if type(value) is bool else None


def _safe_response_failure_code(exc: BaseException) -> str:
    message = str(exc)
    for marker, code in (
        ("provider status rejected", "provider_status_rejected"),
        ("interface identity rejected", "interface_identity_rejected"),
        ("row integrity rejected", "row_integrity_rejected"),
    ):
        if message.endswith(marker):
            return code
    return "response_shape_rejected"


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _request_spec(session: date) -> dict[str, Any]:
    if type(session) is not date:
        raise ValueError("Jiaoch daily_basic collection trade date rejected")
    return {
        "api_name": "daily_basic",
        "endpoint": "http://jiaoch.site/daily_basic",
        "fields": raw_authority._FIELDS_BY_API["daily_basic"],
        "params": {"trade_date": session.strftime("%Y%m%d"), "ts_code": ""},
        "response_fields": raw_authority._FIELDS_BY_API["daily_basic"].split(","),
        "role": "daily-basic-cross-section",
        "route_id": "factor-v3-daily-basic:points-primary:daily_basic",
    }


def _producer_binding() -> dict[str, Any]:
    root = raw_authority._safe_existing_directory(
        Path(__file__).parent, "Jiaoch daily_basic collection producer root"
    )
    entries = []
    for filename in _PRODUCER_FILES:
        raw = raw_authority._read_safe_file(
            root / filename,
            label="Jiaoch daily_basic collection producer source",
            max_bytes=_MAX_PRODUCER_SOURCE_BYTES,
        )
        entries.append({"path": f"app/{filename}", "sha256": _sha256(raw)})
    identity = {
        "collector_version": COLLECTOR_VERSION,
        "entries": entries,
        "schema": "jiaoch-factor-v3-daily-basic-collector-producer/v1",
    }
    return {**identity, "root_sha256": _sha256(_canonical_json(identity))}


def _safe_root(output_root: str | Path) -> Path:
    return raw_authority._safe_existing_directory(
        Path(output_root), "Jiaoch daily_basic collection root"
    )


def _validated_generation(value: Any) -> str:
    if type(value) is not str or _UUID4_RE.fullmatch(value) is None:
        raise ValueError("Jiaoch daily_basic runtime generation rejected")
    return value


def _validated_policy(value: Any) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError("Jiaoch daily_basic routing policy rejected")
    from app.factor_v3_daily_basic_runner import FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR

    expected = FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR["sha256"]
    if not hmac.compare_digest(value, expected):
        raise ValueError("Jiaoch daily_basic routing policy rejected")
    return value


def _attempt_binding(spec: Mapping[str, Any], publication: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "api_name": spec["api_name"], "attempt_id": publication["attempt_id"],
        "attempt_relative_path": publication["attempt_relative_path"],
        "attempt_sha256": publication["attempt_sha256"], "endpoint": spec["endpoint"],
        "fields": spec["fields"], "params": spec["params"],
        "raw_relative_path": publication["raw_relative_path"], "raw_sha256": publication["raw_sha256"],
        "role": spec["role"], "route_id": spec["route_id"],
    }


def _manifest_path(*, root: Path, relative_path: str, expected_sha256: str) -> Path:
    if type(relative_path) is not str or type(expected_sha256) is not str:
        raise ValueError("Jiaoch daily_basic collection set path rejected")
    match = _MANIFEST_RE.fullmatch(relative_path)
    if match is None or match.group(1) != expected_sha256[:2] or match.group(2) != expected_sha256:
        raise ValueError("Jiaoch daily_basic collection set path rejected")
    return raw_authority._safe_existing_file(
        root / Path(*relative_path.split("/")), "Jiaoch daily_basic collection set"
    )


def _session_index_path(*, root: Path, session: date) -> Path:
    directory = raw_authority._ensure_child_directory(root, "daily_basic_collection_sessions")
    return directory / f"{session.isoformat()}.json"


def _load_existing_session(
    *, root: Path, session: date, generation_id: str, policy_sha256: str, producer_root_sha256: str
) -> dict[str, Any] | None:
    path = _session_index_path(root=root, session=session)
    if not path.exists():
        return None
    raw = raw_authority._read_safe_file(
        path, label="Jiaoch daily_basic session index", max_bytes=16 * 1024
    )
    payload = points_common._strict_json_loads(raw, label="Jiaoch daily_basic session index")
    if (
        type(payload) is not dict
        or set(payload) != _SESSION_INDEX_FIELDS
        or not hmac.compare_digest(raw, _canonical_json(payload))
        or payload.get("schema") != _SESSION_INDEX_SCHEMA
        or payload.get("trade_date") != session.isoformat()
        or payload.get("generation_id") != generation_id
        or payload.get("policy_sha256") != policy_sha256
        or payload.get("producer_root_sha256") != producer_root_sha256
    ):
        raise ValueError("Jiaoch daily_basic session index rejected")
    relative_path = payload.get("collection_set_relative_path")
    digest = payload.get("collection_set_sha256")
    if type(relative_path) is not str or type(digest) is not str:
        raise ValueError("Jiaoch daily_basic session index rejected")
    verify_jiaoch_daily_basic_collection_set(
        output_root=root,
        collection_set_relative_path=relative_path,
        expected_collection_set_sha256=digest,
    )
    return {
        "trade_date": session.isoformat(),
        "collection_set_relative_path": relative_path,
        "collection_set_sha256": digest,
    }


def _write_session_index(
    *, root: Path, session: date, generation_id: str, policy_sha256: str,
    producer_root_sha256: str, publication: Mapping[str, Any]
) -> dict[str, Any]:
    payload = {
        "schema": _SESSION_INDEX_SCHEMA,
        "trade_date": session.isoformat(),
        "generation_id": generation_id,
        "policy_sha256": policy_sha256,
        "producer_root_sha256": producer_root_sha256,
        "collection_set_relative_path": publication["collection_set_relative_path"],
        "collection_set_sha256": publication["collection_set_sha256"],
    }
    raw = _canonical_json(payload)
    path = _session_index_path(root=root, session=session)
    raw_authority._write_create_only(
        path, raw, label="Jiaoch daily_basic session index", reuse_identical=True
    )
    existing = _load_existing_session(
        root=root,
        session=session,
        generation_id=generation_id,
        policy_sha256=policy_sha256,
        producer_root_sha256=producer_root_sha256,
    )
    if existing is None:
        raise ValueError("Jiaoch daily_basic session index missing")
    return existing


def _verify_payload(*, root: Path, payload: Any) -> dict[str, Any]:
    if (
        type(payload) is not dict or set(payload) != _MANIFEST_FIELDS
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
        raise ValueError("Jiaoch daily_basic collection set descriptor rejected")
    call_id = _validated_generation(payload.get("collection_call_id"))
    session_text = payload.get("trade_date")
    try:
        session = date.fromisoformat(session_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("Jiaoch daily_basic collection trade date rejected") from exc
    if session.isoformat() != session_text:
        raise ValueError("Jiaoch daily_basic collection trade date rejected")
    timestamp = points_common._utc_timestamp(
        payload.get("retrieved_at"), label="Jiaoch daily_basic collection retrieved_at"
    )
    runtime = payload.get("runtime_mapping_descriptor")
    if (
        type(runtime) is not dict
        or set(runtime) != {"credential_proof_status", "generation_id", "policy_sha256"}
        or runtime.get("credential_proof_status") != "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF"
    ):
        raise ValueError("Jiaoch daily_basic runtime mapping rejected")
    generation = _validated_generation(runtime.get("generation_id"))
    policy = _validated_policy(runtime.get("policy_sha256"))
    if payload.get("credential_binding") != {
        "basis": "ONE_SEALED_ENTRYPOINT_INVOCATION",
        "credential_proof_claimed": False,
        "same_runtime_credential_used": True,
    }:
        raise ValueError("Jiaoch daily_basic credential binding rejected")
    producer = payload.get("producer_binding")
    if producer != _producer_binding():
        raise ValueError("Jiaoch daily_basic producer binding rejected")
    attempts = payload.get("attempts")
    spec = _request_spec(session)
    if type(attempts) is not list or len(attempts) != 1:
        raise ValueError("Jiaoch daily_basic collection attempts rejected")
    binding = attempts[0]
    if (
        type(binding) is not dict or set(binding) != _ATTEMPT_FIELDS
        or any(binding.get(field) != spec[field] for field in ("api_name", "endpoint", "fields", "params", "role", "route_id"))
        or type(binding.get("attempt_id")) is not str or _UUID4_RE.fullmatch(binding["attempt_id"]) is None
        or type(binding.get("attempt_sha256")) is not str or _SHA256_RE.fullmatch(binding["attempt_sha256"]) is None
        or type(binding.get("raw_sha256")) is not str or _SHA256_RE.fullmatch(binding["raw_sha256"]) is None
    ):
        raise ValueError("Jiaoch daily_basic collection attempt rejected")
    points_common._verified_raw_body(
        root=root, binding=binding, expected_spec=spec, expected_retrieved_at=timestamp,
        expected_collection_binding={
            "collection_call_id": call_id, "generation_id": generation, "policy_sha256": policy,
            "producer_root_sha256": producer["root_sha256"],
            "schema": "jiaoch-points-raw-collection-binding/v1",
        },
        expected_trade_date=session.strftime("%Y%m%d"),
    )
    return payload


def _load_and_verify(*, output_root: str | Path, collection_set_relative_path: str, expected_collection_set_sha256: str) -> dict[str, Any]:
    root = _safe_root(output_root)
    path = _manifest_path(root=root, relative_path=collection_set_relative_path, expected_sha256=expected_collection_set_sha256)
    raw = raw_authority._read_safe_file(path, label="Jiaoch daily_basic collection set", max_bytes=_MAX_MANIFEST_BYTES)
    if not hmac.compare_digest(_sha256(raw), expected_collection_set_sha256):
        raise ValueError("Jiaoch daily_basic collection set content address rejected")
    payload = points_common._strict_json_loads(raw, label="Jiaoch daily_basic collection set")
    if not hmac.compare_digest(raw, _canonical_json(payload)):
        raise ValueError("Jiaoch daily_basic collection set canonical form rejected")
    return _verify_payload(root=root, payload=payload)


def verify_jiaoch_daily_basic_collection_set(
    *, output_root: str | Path, collection_set_relative_path: str, expected_collection_set_sha256: str
) -> dict[str, Any]:
    """Verify one daily_basic-only manifest and its one raw CAS attempt offline."""

    payload = _load_and_verify(
        output_root=output_root, collection_set_relative_path=collection_set_relative_path,
        expected_collection_set_sha256=expected_collection_set_sha256,
    )
    return {
        "collection_set_sha256": expected_collection_set_sha256, "credential_slot_id": _CREDENTIAL_SLOT_ID,
        "embargo_consumed": False, "final_oos_consumed": False,
        "production_profile_registered": False, "production_recommendation_eligible": False,
        "rows_published": False, "trade_date": payload["trade_date"], "verified": True,
    }


def _collect_jiaoch_daily_basic_collection_set_with_route_credential(
    *, credential: str, generation_id: str, policy_sha256: str, output_root: str | Path,
    trade_date: str, timeout_seconds: float, max_attempts: int,
) -> dict[str, Any]:
    """Private one-route collector; no second API may be added to this call."""

    if type(credential) is not str or not credential:
        raise ValueError("Jiaoch daily_basic collection credential rejected")
    generation = _validated_generation(generation_id)
    policy = _validated_policy(policy_sha256)
    if type(trade_date) is not str:
        raise ValueError("Jiaoch daily_basic collection trade date rejected")
    try:
        session = date.fromisoformat(trade_date)
    except ValueError as exc:
        raise ValueError("Jiaoch daily_basic collection trade date rejected") from exc
    if session.isoformat() != trade_date or type(max_attempts) is not int or not 1 <= max_attempts <= 10:
        raise ValueError("Jiaoch daily_basic collection controls rejected")
    timeout = points_common._timeout_seconds(timeout_seconds)
    root = _safe_root(output_root)
    spec = _request_spec(session)
    timestamp = points_common._trusted_utc_timestamp()
    call_id = str(uuid.uuid4())
    producer = _producer_binding()
    existing = _load_existing_session(
        root=root,
        session=session,
        generation_id=generation,
        policy_sha256=policy,
        producer_root_sha256=producer["root_sha256"],
    )
    if existing is not None:
        return existing
    transport = points_common._transport_factory()
    publication: dict[str, Any] | None = None
    failure: Exception | None = None
    failure_attempts: list[dict[str, Any]] = []
    for attempt_number in range(1, max_attempts + 1):
        stage = "transport"
        attempt_diagnostic: dict[str, Any] = {
            "attempt": attempt_number,
            "body_complete": None,
            "exception_type": None,
            "failure_code": None,
            "http_status": None,
            "outcome": "transport_exception",
        }
        try:
            response = transport.post(
                url=spec["endpoint"], headers=points_common._request_headers(),
                body=_canonical_json({"api_name": spec["api_name"], "fields": spec["fields"], "params": spec["params"], "token": credential}),
                timeout_s=timeout, max_body_bytes=_MAX_BODY_BYTES,
            )
            raw_body = getattr(response, "body", None)
            http_status = getattr(response, "status", None)
            body_complete = getattr(response, "body_complete", None)
            attempt_diagnostic["http_status"] = _safe_failure_status(http_status)
            attempt_diagnostic["body_complete"] = _safe_failure_body_complete(body_complete)
            stage = "raw_publication"
            publication = raw_authority._publish_jiaoch_points_raw_attempt_for_collection(
                output_root=root, raw_body=raw_body, credential=credential, credential_slot_id=_CREDENTIAL_SLOT_ID,
                api_name="daily_basic", params=spec["params"], fields=spec["fields"], retrieved_at=timestamp,
                network_route="direct", http_status=http_status, body_complete=body_complete,
                collection_call_id=call_id, generation_id=generation, policy_sha256=policy,
                producer_root_sha256=producer["root_sha256"],
            )
            if http_status != 200 or body_complete is not True:
                stage = "http_entity"
                raise ValueError("Jiaoch daily_basic HTTP entity rejected")
            stage = "response_shape"
            points_common._response_interface_identity(
                raw_body, expected_fields=spec["response_fields"],
                expected_trade_date=session.strftime("%Y%m%d"), role=spec["role"],
            )
            break
        except Exception as exc:
            publication = None
            failure = exc
            attempt_diagnostic["outcome"] = {
                "transport": "transport_exception",
                "raw_publication": "raw_publication_rejected",
                "http_entity": "http_entity_rejected",
                "response_shape": "response_shape_rejected",
            }[stage]
            attempt_diagnostic["failure_code"] = (
                _safe_response_failure_code(exc)
                if stage == "response_shape"
                else attempt_diagnostic["outcome"]
            )
            attempt_diagnostic["exception_type"] = _safe_failure_exception_type(exc)
            failure_attempts.append(attempt_diagnostic)
    if publication is None:
        raise JiaochDailyBasicCollectionError(
            {
                "attempts": failure_attempts,
                "route_id": spec["route_id"],
                "schema": _FAILURE_SCHEMA,
                "trade_date": session.isoformat(),
            }
        ) from failure
    if _producer_binding() != producer:
        raise ValueError("Jiaoch daily_basic producer drift rejected")
    manifest = {
        "attempts": [_attempt_binding(spec, publication)], "authority_scope": _AUTHORITY_SCOPE,
        "collection_binding_status": "BOUND_TO_SINGLE_CLOSED_RUNTIME_CALL", "collection_call_id": call_id,
        "collector_version": COLLECTOR_VERSION,
        "credential_binding": {"basis": "ONE_SEALED_ENTRYPOINT_INVOCATION", "credential_proof_claimed": False, "same_runtime_credential_used": True},
        "credential_slot_id": _CREDENTIAL_SLOT_ID, "embargo_consumed": False, "evidence_complete": True,
        "final_oos_consumed": False, "producer_binding": producer,
        "production_profile_registered": False, "production_recommendation_eligible": False,
        "retrieved_at": timestamp, "rows_published": False,
        "runtime_mapping_descriptor": {"credential_proof_status": "DESCRIPTIVE_ONLY_NOT_CRYPTOGRAPHIC_PROOF", "generation_id": generation, "policy_sha256": policy},
        "schema": COLLECTION_SET_SCHEMA, "source_id": _SOURCE_ID, "trade_date": session.isoformat(),
    }
    _verify_payload(root=root, payload=manifest)
    raw = _canonical_json(manifest)
    if len(raw) > _MAX_MANIFEST_BYTES:
        raise ValueError("Jiaoch daily_basic collection manifest too large")
    digest = _sha256(raw)
    directory = raw_authority._content_addressed_directory(root, "daily_basic_collection_sets", digest)
    path = directory / f"{digest}.json"
    relative_path = f"daily_basic_collection_sets/sha256/{digest[:2]}/{digest}.json"
    created = raw_authority._write_create_only(path, raw, label="Jiaoch daily_basic collection set", reuse_identical=False)
    try:
        verify_jiaoch_daily_basic_collection_set(
            output_root=root, collection_set_relative_path=relative_path,
            expected_collection_set_sha256=digest,
        )
    except BaseException:
        if created:
            path.unlink(missing_ok=True)
            raw_authority.fsync_directory(path.parent)
        raise
    return _write_session_index(
        root=root,
        session=session,
        generation_id=generation,
        policy_sha256=policy,
        producer_root_sha256=producer["root_sha256"],
        publication={
            "trade_date": session.isoformat(),
            "collection_set_relative_path": relative_path,
            "collection_set_sha256": digest,
        },
    )
