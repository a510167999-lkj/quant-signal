"""Development-only, offline Factor V3 materialization candidates.

This module deliberately does not alter the frozen points contract.  It can
only produce a content-addressed *development candidate* from one concrete,
verified input snapshot; it never grants a formal, embargo, final-OOS, or
production authority.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any

from app import audited_pit_factor_v3_points_contract as points
from app import research_scope
from app.research_scope import is_mainboard_chinext_symbol


INPUT_BUNDLE_SCHEMA = "audited-pit-factor-v3-development-input-bundle/v1"
INPUT_AUTHORITY_DESCRIPTOR_SCHEMA = "audited-pit-factor-v3-development-input-authority/v1"
CANDIDATE_SCHEMA = "audited-pit-factor-v3-development-candidate/v1"
RECEIPT_SCHEMA = "audited-pit-factor-v3-development-materialization-receipt/v1"
_CANDIDATE_DIRECTORY = "factor-v3-development-candidates"
_RECEIPT_DIRECTORY = "factor-v3-development-receipts"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_IDENTITIES = frozenset(
    {
        "factor_v2_parent_producer_root_sha256",
        "extended_trading_calendar_descriptor_root_sha256",
        "extended_trading_calendar_receipt_sha256",
        "feature_history_source_authority_root_sha256",
        "factor_v3_feature_history_verification_receipt_sha256",
        "daily_basic_normalized_row_authority_root_sha256",
        "daily_basic_coverage_receipt_sha256",
        "daily_traded_cross_section_root_sha256",
        "pit_listing_membership_root_sha256",
        "pit_suspension_root_sha256",
        "security_code_transition_contract_sha256",
        "security_code_transition_evidence_root_sha256",
        "factor_v2_terminal_decision_descriptor_sha256",
        "factor_v2_evaluator_descriptor_sha256",
        "factor_v2_cost_slippage_execution_descriptor_sha256",
    }
)
_UPSTREAM_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
_ALLOWED_EXCLUSION_REASONS = (
    "ipo_age_less_than_6_calendar_months",
    "observed_trading_records_less_than_15_in_20_market_session_window",
    "observed_trading_records_less_than_120_in_250_market_session_window",
    "unresolved_authoritative_security_code_transition",
)
_SOURCE_DESCRIPTOR_NAMES = frozenset(
    {
        "factor_v2_parent",
        "calendar",
        "daily_basic",
        "daily_traded_cross_section",
        "listing_membership",
        "suspensions",
        "security_code_transitions",
        "upstream_board_ledger",
        "factor_v2_evaluation",
        "feature_history_receipt",
        "daily_basic_exact_set_receipt",
    }
)
_SENSITIVE_FIELD_TERMS = (
    "capability",
    "token",
    "secret",
    "password",
    "credential",
    "authorization",
    "api_key",
)
_SOURCE_DESCRIPTOR_IDENTITY_BINDINGS = {
    "factor_v2_parent": (None, "factor_v2_parent_producer_root_sha256"),
    "calendar": (
        "extended_trading_calendar_receipt_sha256",
        "extended_trading_calendar_descriptor_root_sha256",
    ),
    "daily_basic": (
        "daily_basic_coverage_receipt_sha256",
        "daily_basic_normalized_row_authority_root_sha256",
    ),
    "daily_traded_cross_section": (
        "daily_basic_coverage_receipt_sha256",
        "daily_traded_cross_section_root_sha256",
    ),
    "listing_membership": (
        "pit_listing_membership_root_sha256",
        "pit_listing_membership_root_sha256",
    ),
    "suspensions": (
        "pit_suspension_root_sha256",
        "pit_suspension_root_sha256",
    ),
    "security_code_transitions": (
        "security_code_transition_evidence_root_sha256",
        "security_code_transition_contract_sha256",
    ),
    "upstream_board_ledger": (
        "daily_basic_coverage_receipt_sha256",
        "daily_basic_normalized_row_authority_root_sha256",
    ),
    "factor_v2_evaluation": (
        "factor_v2_evaluator_descriptor_sha256",
        "factor_v2_evaluator_descriptor_sha256",
    ),
    "feature_history_receipt": (
        "factor_v3_feature_history_verification_receipt_sha256",
        "feature_history_source_authority_root_sha256",
    ),
    "daily_basic_exact_set_receipt": (
        "daily_basic_coverage_receipt_sha256",
        "daily_basic_normalized_row_authority_root_sha256",
    ),
}


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("factor-v3 materializer value is not canonical JSON") from exc


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _file_sha256(path: Path) -> str:
    _assert_safe_existing_path(path, label="producer source file")
    if not path.is_file():
        raise ValueError("producer source file is not regular")
    return _sha256_bytes(_read_regular_bytes_no_follow(path, label="producer source file"))


def factor_v3_materializer_producer_binding() -> dict[str, Any]:
    """Content-address the only code modules that determine this candidate's semantics."""

    modules = {
        "materializer": Path(__file__),
        "points_contract": Path(str(points.__file__)),
        "research_scope": Path(str(research_scope.__file__)),
    }
    repository_root = Path(__file__).resolve().parents[1]
    files = {
        name: {
            "relative_path": path.resolve().relative_to(repository_root).as_posix(),
            "sha256": _file_sha256(path),
        }
        for name, path in modules.items()
    }
    unsigned = {
        "schema_version": "audited-pit-factor-v3-materializer-producer-binding/v1",
        "files": files,
    }
    return {**unsigned, "root_sha256": _canonical_sha256(unsigned)}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError("factor-v3 materializer JSON contains duplicate keys")
        output[key] = value
    return output


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"factor-v3 materializer JSON contains non-finite value: {value}")


def _strict_json_loads(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{label} root must be an object")
    if _canonical_bytes(payload) != raw:
        raise ValueError(f"{label} is not canonical JSON")
    return payload


def _is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.stat().st_file_attributes
    except AttributeError:
        return False
    return bool(attributes & 0x400)


def _assert_safe_existing_path(path: Path, *, label: str) -> None:
    if not path.exists() or _is_reparse_point(path):
        raise ValueError(f"{label} must be an existing non-symlink path")
    current = path.parent
    while current != current.parent:
        if current.exists() and _is_reparse_point(current):
            raise ValueError(f"{label} is below a symlink or reparse point")
        current = current.parent


def _assert_no_live_store_sidecars(path: Path, *, label: str) -> None:
    if any(Path(f"{path}{suffix}").exists() for suffix in ("-wal", "-shm", "-WAL", "-SHM")):
        raise ValueError(f"{label} has WAL/SHM live store sidecars")
    if path.suffix.lower() in {".sqlite", ".sqlite3", ".db"}:
        raise ValueError(f"{label} must not be a live store")


def _same_file_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
        and left.st_ino == right.st_ino
        and left.st_dev == right.st_dev
    )


def _directory_identity(path: Path, *, label: str) -> tuple[int, int]:
    _assert_safe_existing_path(path, label=label)
    if not path.is_dir():
        raise ValueError(f"{label} must be a directory")
    stat = path.stat()
    return stat.st_dev, stat.st_ino


def _read_regular_bytes_no_follow(path: Path, *, label: str) -> bytes:
    _assert_safe_existing_path(path, label=label)
    if not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(str(path), flags)
    try:
        opened = os.fstat(descriptor)
        resolved = path.stat()
        if _is_reparse_point(path) or not _same_file_identity(opened, resolved):
            raise ValueError(f"{label} changed to a reparse point during open")
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(1024 * 1024, remaining))
            if not chunk:
                raise ValueError(f"{label} changed while being read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != opened.st_size or not _same_file_identity(opened, path.stat()):
            raise ValueError(f"{label} changed while being read")
        return raw
    finally:
        os.close(descriptor)


def _read_snapshot_bundle(path_value: str | Path) -> tuple[Path, dict[str, Any], str]:
    path = Path(path_value)
    _assert_safe_existing_path(path, label="input bundle")
    _assert_no_live_store_sidecars(path, label="input bundle")
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValueError("input bundle must be a regular immutable JSON snapshot")
    raw = _read_regular_bytes_no_follow(path, label="input bundle")
    return path, _strict_json_loads(raw, label="input bundle"), _sha256_bytes(raw)


def _file_fingerprint(path: Path) -> tuple[int, int, str]:
    _assert_safe_existing_path(path, label="immutable source snapshot")
    if not path.is_file():
        raise ValueError("immutable source snapshot must be a regular file")
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, _sha256_bytes(
        _read_regular_bytes_no_follow(path, label="immutable source snapshot")
    )


def _assert_fingerprints_unchanged(fingerprints: Mapping[Path, tuple[int, int, str]]) -> None:
    for path, expected in fingerprints.items():
        if _file_fingerprint(path) != expected:
            raise ValueError("immutable source snapshot changed during offline verification")


def _safe_relative_snapshot_path(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or not value or Path(value).name != value:
        raise ValueError(f"{label} must be a plain snapshot filename")
    if Path(value).suffix.lower() != ".json":
        raise ValueError(f"{label} must reference canonical JSON")
    return value


def _read_json_snapshot(path: Path, *, label: str) -> tuple[dict[str, Any], str]:
    _assert_safe_existing_path(path, label=label)
    _assert_no_live_store_sidecars(path, label=label)
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValueError(f"{label} must be a regular immutable JSON snapshot")
    raw = _read_regular_bytes_no_follow(path, label=label)
    return _strict_json_loads(raw, label=label), _sha256_bytes(raw)


def _load_pinned_input_authority(
    *,
    input_authority_descriptor_path: str | Path,
    expected_input_authority_descriptor_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[Path, tuple[int, int, str]]]:
    descriptor_path = Path(input_authority_descriptor_path)
    descriptor, descriptor_file_sha256 = _read_json_snapshot(
        descriptor_path,
        label="input authority descriptor",
    )
    expected_descriptor_sha256 = _strict_sha256(
        expected_input_authority_descriptor_sha256,
        label="expected input authority descriptor SHA",
    )
    if descriptor_file_sha256 != expected_descriptor_sha256:
        raise ValueError("input authority descriptor content address drifted")
    _assert_no_capability(descriptor, path="input authority descriptor")
    if (
        descriptor.get("schema_version") != INPUT_AUTHORITY_DESCRIPTOR_SCHEMA
        or descriptor.get("authority_status") != "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT"
        or descriptor.get("development_only") is not True
    ):
        raise ValueError("input authority descriptor is not a development-only verified snapshot")
    root = descriptor_path.parent
    _assert_safe_existing_path(root, label="input authority snapshot root")
    descriptors = _strict_mapping(descriptor.get("source_descriptors"), label="source descriptors")
    if set(descriptors) != _SOURCE_DESCRIPTOR_NAMES:
        raise ValueError("input authority descriptor source set is incomplete")
    source_payloads: dict[str, dict[str, Any]] = {}
    fingerprints: dict[Path, tuple[int, int, str]] = {
        descriptor_path: _file_fingerprint(descriptor_path)
    }
    allowed_names = {descriptor_path.name}
    for name in sorted(_SOURCE_DESCRIPTOR_NAMES):
        source_descriptor = _strict_mapping(descriptors[name], label=f"{name} source descriptor")
        if set(source_descriptor) != {
            "relative_path",
            "file_sha256",
            "receipt_sha256",
            "producer_root_sha256",
        }:
            raise ValueError(f"{name} source descriptor fields are invalid")
        filename = _safe_relative_snapshot_path(
            source_descriptor["relative_path"],
            label=f"{name} relative_path",
        )
        source_path = root / filename
        payload, file_sha256 = _read_json_snapshot(source_path, label=f"{name} source snapshot")
        if file_sha256 != _strict_sha256(source_descriptor["file_sha256"], label=f"{name} file SHA"):
            raise ValueError(f"{name} source snapshot content address drifted")
        _strict_sha256(source_descriptor["receipt_sha256"], label=f"{name} receipt SHA")
        _strict_sha256(source_descriptor["producer_root_sha256"], label=f"{name} producer root")
        _assert_no_capability(payload, path=f"{name} source snapshot")
        source_payloads[name] = payload
        fingerprints[source_path] = _file_fingerprint(source_path)
        allowed_names.add(filename)
    for member in root.rglob("*"):
        if _is_reparse_point(member):
            raise ValueError("input authority snapshot must not contain a symlink or reparse point")
        if member.is_file() and member.name not in allowed_names:
            raise ValueError("input authority snapshot contains an unbound file")
        if member.name.lower().endswith(("-wal", "-shm")) or member.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("input authority snapshot must not contain a live store or WAL/SHM")
    identities = _strict_mapping(descriptor.get("authority_identities"), label="descriptor authority identities")
    if set(identities) != _REQUIRED_IDENTITIES:
        raise ValueError("input authority descriptor identities are incomplete")
    normalized_identities = {
        name: _strict_sha256(value, label=f"descriptor authority identity {name}")
        for name, value in identities.items()
    }
    for name, (receipt_identity, producer_identity) in _SOURCE_DESCRIPTOR_IDENTITY_BINDINGS.items():
        source_descriptor = _strict_mapping(descriptors[name], label=f"{name} source descriptor")
        expected_receipt = (
            source_payloads["factor_v2_parent"].get("factor_v2_common_eligible_receipt_sha256")
            if receipt_identity is None
            else normalized_identities[receipt_identity]
        )
        if (
            source_descriptor["receipt_sha256"] != expected_receipt
            or source_descriptor["producer_root_sha256"] != normalized_identities[producer_identity]
        ):
            raise ValueError(f"{name} descriptor receipt or producer binding drifted")
    bundle = {
        "schema_version": INPUT_BUNDLE_SCHEMA,
        "points_contract_sha256": descriptor.get("points_contract_sha256"),
        "authority_identities": normalized_identities,
        "factor_v2_parent": source_payloads["factor_v2_parent"],
        "calendar": source_payloads["calendar"],
        "daily_basic": source_payloads["daily_basic"],
        "daily_traded_cross_section": source_payloads["daily_traded_cross_section"],
        "listing_membership": source_payloads["listing_membership"],
        "suspensions": source_payloads["suspensions"],
        "security_code_transitions": source_payloads["security_code_transitions"],
        "upstream_board_ledger": source_payloads["upstream_board_ledger"],
        "factor_v2_evaluation": source_payloads["factor_v2_evaluation"],
    }
    descriptor_context = {
        "descriptor_file_sha256": descriptor_file_sha256,
        "source_descriptors": deepcopy(dict(descriptors)),
        "parent_payload_fields": list(
            _strict_sequence(descriptor.get("parent_payload_fields"), label="parent payload fields")
        ),
        "feature_history_receipt": source_payloads["feature_history_receipt"],
        "daily_basic_receipt": source_payloads["daily_basic_exact_set_receipt"],
    }
    return bundle, descriptor_context, fingerprints


def _strict_mapping(value: Any, *, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _strict_sequence(value: Any, *, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{label} must be an array")
    return value


def _strict_sha256(value: Any, *, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_date(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO date")
    return value


def _strict_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    output = float(value)
    if not math.isfinite(output) or output <= 0:
        raise ValueError(f"{label} must be finite and positive")
    return output


def _assert_no_capability(value: Any, *, path: str = "input") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} contains a non-string key")
            if any(term in key.lower() for term in _SENSITIVE_FIELD_TERMS):
                raise ValueError("sensitive capability or credential data must never persist")
            _assert_no_capability(nested, path=f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, nested in enumerate(value):
            _assert_no_capability(nested, path=f"{path}[{index}]")


def _validated_dates(value: Any, *, label: str) -> list[str]:
    dates = [_strict_date(item, label=f"{label} item") for item in _strict_sequence(value, label=label)]
    if not dates or dates != sorted(dates) or len(dates) != len(set(dates)):
        raise ValueError(f"{label} must be non-empty, ordered, and unique")
    return dates


def _validate_sha_bound_rows(
    section: Mapping[str, Any],
    *,
    key: str,
    label: str,
) -> list[Mapping[str, Any]]:
    rows = list(_strict_sequence(section.get("rows"), label=f"{label} rows"))
    if _canonical_sha256(rows) != _strict_sha256(section.get(key), label=f"{label} {key}"):
        raise ValueError(f"{label} rows content address drifted")
    return [_strict_mapping(row, label=f"{label} row") for row in rows]


def _validate_frozen_points_contract(bundle: Mapping[str, Any]) -> Mapping[str, Any]:
    contract = points.FACTOR_V3_POINTS_CONTRACT
    if points.canonical_sha256(contract) != points.FACTOR_V3_POINTS_CONTRACT_SHA256:
        raise ValueError("frozen factor-v3 points contract drifted")
    if bundle.get("points_contract_sha256") != points.FACTOR_V3_POINTS_CONTRACT_SHA256:
        raise ValueError("input bundle is not bound to the frozen points contract")
    prerequisites = _strict_mapping(
        contract.get("formal_materialization_prerequisites"),
        label="frozen points prerequisites",
    )
    if prerequisites.get("formal_materializer_implemented") is not False:
        raise ValueError("materializer must not flip the frozen formal implementation gate")
    for safety_field in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_profile_registered",
        "production_recommendation_eligible",
    ):
        if contract.get(safety_field) is not False:
            raise ValueError("materializer must not use a non-development points contract")
    return contract


def _validate_authority_identities(bundle: Mapping[str, Any]) -> dict[str, str]:
    identities = _strict_mapping(bundle.get("authority_identities"), label="authority identities")
    if set(identities) != _REQUIRED_IDENTITIES:
        raise ValueError("authority identities are incomplete or contain an unbound identity")
    return {
        name: _strict_sha256(value, label=f"authority identity {name}")
        for name, value in identities.items()
    }


def _validate_calendar(
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
    identities: Mapping[str, str],
) -> dict[str, Any]:
    calendar = _strict_mapping(bundle.get("calendar"), label="calendar")
    all_sessions = _validated_dates(calendar.get("all_market_sessions"), label="all market sessions")
    prewindow = _validated_dates(calendar.get("prewindow_sessions"), label="prewindow sessions")
    development = _validated_dates(calendar.get("development_sessions"), label="development sessions")
    if len(prewindow) != 250 or len(development) != 483:
        raise ValueError("calendar must bind exactly 250 prewindow and 483 development sessions")
    if all_sessions != [*prewindow, *development]:
        raise ValueError("all market sessions must be the exact prewindow plus development sequence")
    for field, dates in (
        ("all_market_sessions_sha256", all_sessions),
        ("prewindow_sessions_sha256", prewindow),
        ("development_sessions_sha256", development),
    ):
        if _canonical_sha256(dates) != _strict_sha256(calendar.get(field), label=field):
            raise ValueError(f"{field} drifted")
    if calendar.get("authority_receipt_sha256") != identities[
        "extended_trading_calendar_receipt_sha256"
    ]:
        raise ValueError("extended trading calendar receipt identity drifted")
    expected = _strict_mapping(
        _strict_mapping(contract.get("preregistered_parent_expectation"), label="parent expectation").get("sessions"),
        label="frozen development sessions",
    )
    if (
        expected.get("count") != len(development)
        or expected.get("start") != development[0]
        or expected.get("end") != development[-1]
        or expected.get("sha256") != _canonical_sha256(development)
    ):
        raise ValueError("483 development session identity differs from the frozen points contract")
    source_dates = [*prewindow, *development[:-1]]
    return {
        "all_sessions": all_sessions,
        "prewindow": prewindow,
        "development": development,
        "source_dates": source_dates,
        "source_dates_sha256": _canonical_sha256(source_dates),
    }


def _validate_parent(
    bundle: Mapping[str, Any],
    contract: Mapping[str, Any],
    development_sessions: Sequence[str],
    parent_payload_fields: Sequence[Any],
) -> list[dict[str, Any]]:
    parent = _strict_mapping(bundle.get("factor_v2_parent"), label="factor-v2 parent")
    expectation = _strict_mapping(
        contract.get("preregistered_parent_expectation"), label="frozen parent expectation"
    )
    for key, expected in expectation.items():
        if parent.get(key) != expected:
            raise ValueError(f"factor-v2 parent binding drifted: {key}")
    rows = _validate_sha_bound_rows(parent, key="rows_sha256", label="factor-v2 parent")
    if len(rows) != int(expectation["common_eligible_candidate_count"]):
        raise ValueError("factor-v2 parent row count differs from common-eligible identity")
    normalized: list[dict[str, Any]] = []
    identities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    development_set = set(development_sessions)
    if (
        not parent_payload_fields
        or any(
            not isinstance(field, str)
            or not field
            or field in {"candidate_key", "signal_date", "ts_code"}
            or any(term in field.lower() for term in _SENSITIVE_FIELD_TERMS)
            for field in parent_payload_fields
        )
        or len(set(parent_payload_fields)) != len(parent_payload_fields)
    ):
        raise ValueError("parent payload field whitelist is invalid")
    expected_fields = {"candidate_key", "signal_date", "ts_code", *parent_payload_fields}
    for row in rows:
        if set(row) != expected_fields:
            raise ValueError("parent payload fields differ from the pinned whitelist")
        candidate_key = row.get("candidate_key")
        signal_date = _strict_date(row.get("signal_date"), label="parent signal_date")
        ts_code = row.get("ts_code")
        if (
            not isinstance(candidate_key, str)
            or not isinstance(ts_code, str)
            or not is_mainboard_chinext_symbol(ts_code)
            or candidate_key != f"cn-a-share:{ts_code}|{signal_date}"
            or signal_date not in development_set
        ):
            raise ValueError("factor-v2 parent candidate grain or market scope is invalid")
        identity = (candidate_key, signal_date)
        if identity in seen:
            raise ValueError("factor-v2 parent candidate identity is duplicated")
        seen.add(identity)
        copied = dict(row)
        normalized.append(copied)
        identities.append({"candidate_key": candidate_key, "signal_date": signal_date})
    if parent.get("candidate_identity_root_sha256") != _canonical_sha256(identities):
        raise ValueError("factor-v2 parent candidate identity root drifted")
    if (
        expectation["common_eligible_candidate_keys_sha256"]
        != _canonical_sha256([row["candidate_key"] for row in normalized])
        or expectation["common_eligible_source_feature_rows_sha256"]
        != _canonical_sha256(normalized)
    ):
        raise ValueError("factor-v2 parent rows do not replay the frozen common-eligible roots")
    return normalized


def _validate_verification_receipts(
    *,
    feature_history: Mapping[str, Any],
    daily_basic: Mapping[str, Any],
    identities: Mapping[str, str],
    calendar: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    history_receipt = _strict_mapping(feature_history, label="feature-history verification receipt")
    daily_receipt = _strict_mapping(daily_basic, label="daily-basic verification receipt")
    _assert_no_capability(history_receipt, path="feature-history verification receipt")
    _assert_no_capability(daily_receipt, path="daily-basic verification receipt")
    if history_receipt.get("verified") is not True:
        raise ValueError("feature-history authority is not verified")
    if history_receipt.get("receipt_sha256") != identities[
        "factor_v3_feature_history_verification_receipt_sha256"
    ]:
        raise ValueError("feature-history verification receipt identity drifted")
    if history_receipt.get("source_authority_root_sha256") != identities[
        "feature_history_source_authority_root_sha256"
    ]:
        raise ValueError("feature-history source identity drifted")
    if (
        history_receipt.get("prewindow_session_count") != 250
        or history_receipt.get("prewindow_sessions_sha256") != _canonical_sha256(calendar["prewindow"])
        or history_receipt.get("factor_materialization_eligible") is not False
        or history_receipt.get("formal_factor_materialization_eligible") is not False
    ):
        raise ValueError("feature-history verification receipt is not development-only exact prewindow evidence")
    if daily_receipt.get("verified") is not True:
        raise ValueError("daily-basic authority is not verified")
    if daily_receipt.get("authority_status") != "VERIFIED_DEVELOPMENT_DAILY_BASIC_EXACT_SET":
        raise ValueError("daily-basic authority status is invalid")
    if (
        daily_receipt.get("coverage_receipt_sha256")
        != identities["daily_basic_coverage_receipt_sha256"]
        or daily_receipt.get("normalized_row_authority_root_sha256")
        != identities["daily_basic_normalized_row_authority_root_sha256"]
        or daily_receipt.get("source_dates_sha256") != calendar["source_dates_sha256"]
        or daily_receipt.get("factor_v3_development_materialization_input_eligible") is not True
        or daily_receipt.get("formal_factor_v3_materialization_performed") is not False
    ):
        raise ValueError("daily-basic exact source-date union verification drifted")
    return {"feature_history": dict(history_receipt), "daily_basic": dict(daily_receipt)}


def _validate_source_rows(
    bundle: Mapping[str, Any],
    *,
    source_dates: Sequence[str],
) -> dict[str, Any]:
    daily = _strict_mapping(bundle.get("daily_basic"), label="daily basic")
    declared_dates = _validated_dates(daily.get("source_dates"), label="daily basic source dates")
    if declared_dates != list(source_dates) or daily.get("source_dates_sha256") != _canonical_sha256(source_dates):
        raise ValueError("daily-basic source-date union is not exact")
    daily_rows = _validate_sha_bound_rows(daily, key="rows_sha256", label="daily basic")
    cross_section = _strict_mapping(
        bundle.get("daily_traded_cross_section"), label="daily traded cross section"
    )
    cross_rows = _validate_sha_bound_rows(
        cross_section,
        key="rows_sha256",
        label="daily traded cross section",
    )
    source_date_set = set(source_dates)
    daily_index: dict[tuple[str, str], dict[str, Any]] = {}
    cross_index: dict[tuple[str, str], dict[str, str]] = {}
    for row in daily_rows:
        security_id = row.get("security_id")
        trade_date = _strict_date(row.get("trade_date"), label="daily basic trade_date")
        ts_code = row.get("ts_code")
        if not isinstance(security_id, str) or not security_id or not isinstance(ts_code, str):
            raise ValueError("daily basic security identity is invalid")
        if trade_date not in source_date_set:
            raise ValueError("daily basic row is outside exact source-date union")
        if not is_mainboard_chinext_symbol(ts_code):
            raise ValueError("daily basic row is outside target scope after upstream proof")
        _strict_number(row.get("turnover_rate_f"), label="daily basic turnover_rate_f")
        key = (security_id, trade_date)
        if key in daily_index:
            raise ValueError("daily basic identity is duplicated")
        daily_index[key] = dict(row)
    for row in cross_rows:
        security_id = row.get("security_id")
        trade_date = _strict_date(row.get("trade_date"), label="daily cross section trade_date")
        ts_code = row.get("ts_code")
        if not isinstance(security_id, str) or not security_id or not isinstance(ts_code, str):
            raise ValueError("daily cross section security identity is invalid")
        if trade_date not in source_date_set or not is_mainboard_chinext_symbol(ts_code):
            raise ValueError("daily cross section is outside bound target source scope")
        key = (security_id, trade_date)
        if key in cross_index:
            raise ValueError("daily cross section identity is duplicated")
        cross_index[key] = {"security_id": security_id, "trade_date": trade_date, "ts_code": ts_code}
    missing = set(cross_index).difference(daily_index)
    extra = set(daily_index).difference(cross_index)
    if missing:
        raise ValueError("daily_basic missing for authoritatively traded security")
    if extra:
        raise ValueError("daily_basic has unaccounted rows outside authoritative cross section")
    for key, cross_row in cross_index.items():
        if daily_index[key]["ts_code"] != cross_row["ts_code"]:
            raise ValueError("daily_basic code differs from authoritative traded cross section")
    suspensions = _strict_mapping(bundle.get("suspensions"), label="suspensions")
    suspension_index: set[tuple[str, str]] = set()
    for row in _strict_sequence(suspensions.get("rows"), label="suspension rows"):
        entry = _strict_mapping(row, label="suspension row")
        security_id = entry.get("security_id")
        trade_date = _strict_date(entry.get("trade_date"), label="suspension trade_date")
        if not isinstance(security_id, str) or not security_id or trade_date not in source_date_set:
            raise ValueError("suspension row is outside exact source-date union")
        key = (security_id, trade_date)
        if key in suspension_index or key in cross_index:
            raise ValueError("authoritative suspension conflicts with traded cross section")
        suspension_index.add(key)
    return {
        "daily_index": daily_index,
        "cross_index": cross_index,
        "suspension_index": suspension_index,
    }


def _validate_listing_and_transitions(bundle: Mapping[str, Any]) -> dict[str, Any]:
    listing = _strict_mapping(bundle.get("listing_membership"), label="listing and membership")
    listings: dict[str, dict[str, str]] = {}
    for row in _strict_sequence(listing.get("rows"), label="listing rows"):
        entry = _strict_mapping(row, label="listing row")
        security_id = entry.get("security_id")
        listing_date = _strict_date(entry.get("listing_date"), label="listing date")
        membership_start = _strict_date(
            entry.get("membership_start"),
            label="listing membership_start",
        )
        membership_end = _strict_date(
            entry.get("membership_end"),
            label="listing membership_end",
        )
        if (
            not isinstance(security_id, str)
            or not security_id
            or security_id in listings
            or membership_start > listing_date
            or listing_date > membership_end
        ):
            raise ValueError("listing identity is invalid or duplicated")
        listings[security_id] = {
            "listing_date": listing_date,
            "membership_start": membership_start,
            "membership_end": membership_end,
        }
    transitions = _strict_mapping(bundle.get("security_code_transitions"), label="security code transitions")
    transition_rows = _validate_sha_bound_rows(
        transitions,
        key="rows_sha256",
        label="security code transitions",
    )
    normalized: list[dict[str, str]] = []
    for row in transition_rows:
        security_id = row.get("security_id")
        ts_code = row.get("ts_code")
        start = _strict_date(row.get("effective_start"), label="transition effective_start")
        end = _strict_date(row.get("effective_end"), label="transition effective_end")
        if (
            not isinstance(security_id, str)
            or not security_id
            or not isinstance(ts_code, str)
            or not is_mainboard_chinext_symbol(ts_code)
            or start > end
        ):
            raise ValueError("security transition row is invalid")
        normalized.append(
            {
                "security_id": security_id,
                "ts_code": ts_code,
                "effective_start": start,
                "effective_end": end,
            }
        )
    return {"listings": listings, "transitions": normalized}


def _validate_upstream_ledger(
    bundle: Mapping[str, Any],
    *,
    source_dates: Sequence[str],
    daily_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = _strict_mapping(bundle.get("upstream_board_ledger"), label="upstream board ledger")
    if ledger.get("preserved_before_target_scope_filter") is not True:
        raise ValueError("BSE/STAR must be preserved before downstream target filtering")
    if tuple(ledger.get("source_segments", ())) != _UPSTREAM_SEGMENTS:
        raise ValueError("upstream board ledger must prove BSE and STAR before filtering")
    counts = _strict_mapping(ledger.get("pre_filter_segment_counts"), label="upstream segment counts")
    if set(counts) != set(_UPSTREAM_SEGMENTS) or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in counts.values()
    ):
        raise ValueError("upstream board ledger counts are invalid")
    per_date = list(_strict_sequence(ledger.get("per_date"), label="upstream per-date board ledger"))
    if len(per_date) != len(source_dates):
        raise ValueError("upstream board ledger must cover the exact source-date union")
    normalized_per_date: list[dict[str, Any]] = []
    for expected_date, raw in zip(source_dates, per_date):
        entry = _strict_mapping(raw, label="upstream per-date board row")
        if set(entry) != {
            "trade_date",
            "raw_source_rows_sha256",
            "normalized_rows_sha256",
            "segment_counts",
        } or _strict_date(entry["trade_date"], label="upstream board trade_date") != expected_date:
            raise ValueError("upstream board ledger date sequence drifted")
        segment_counts = _strict_mapping(entry["segment_counts"], label="upstream per-date segment counts")
        if set(segment_counts) != set(_UPSTREAM_SEGMENTS) or any(
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
            for value in segment_counts.values()
        ):
            raise ValueError("upstream per-date BSE/STAR proof is incomplete")
        normalized_per_date.append(
            {
                "trade_date": expected_date,
                "raw_source_rows_sha256": _strict_sha256(
                    entry["raw_source_rows_sha256"], label="upstream raw rows root"
                ),
                "normalized_rows_sha256": _strict_sha256(
                    entry["normalized_rows_sha256"], label="upstream normalized rows root"
                ),
                "segment_counts": {name: int(segment_counts[name]) for name in _UPSTREAM_SEGMENTS},
            }
        )
    per_date_root = _canonical_sha256(normalized_per_date)
    if (
        ledger.get("per_date_board_ledger_root_sha256") != per_date_root
        or daily_receipt.get("upstream_board_ledger_root_sha256") != per_date_root
    ):
        raise ValueError("daily-basic exact-set receipt does not bind the BSE/STAR board ledger")
    return {
        "preserved_before_target_scope_filter": True,
        "source_segments": list(_UPSTREAM_SEGMENTS),
        "pre_filter_segment_counts": {name: int(counts[name]) for name in _UPSTREAM_SEGMENTS},
        "per_date": normalized_per_date,
        "per_date_board_ledger_root_sha256": per_date_root,
        "downstream_scope_filter": "mainboard_chinext_candidate_join_only",
    }


def _validate_evaluator(bundle: Mapping[str, Any], identities: Mapping[str, str]) -> dict[str, str]:
    evaluator = _strict_mapping(bundle.get("factor_v2_evaluation"), label="factor-v2 evaluation")
    expected = {
        "terminal_decision_descriptor_sha256": identities[
            "factor_v2_terminal_decision_descriptor_sha256"
        ],
        "evaluator_descriptor_sha256": identities["factor_v2_evaluator_descriptor_sha256"],
        "cost_slippage_execution_descriptor_sha256": identities[
            "factor_v2_cost_slippage_execution_descriptor_sha256"
        ],
    }
    if dict(evaluator) != expected:
        raise ValueError("factor-v2 evaluator/cost/slippage identity must only be frozen and propagated")
    return expected


def preverify_factor_v3_development_input_bundle(
    *,
    input_authority_descriptor_path: str | Path,
    expected_input_authority_descriptor_sha256: str,
) -> dict[str, Any]:
    """Validate a concrete offline bundle before any candidate output is written."""

    bundle, descriptor_context, fingerprints = _load_pinned_input_authority(
        input_authority_descriptor_path=input_authority_descriptor_path,
        expected_input_authority_descriptor_sha256=(
            expected_input_authority_descriptor_sha256
        ),
    )
    _assert_no_capability(bundle)
    if bundle.get("schema_version") != INPUT_BUNDLE_SCHEMA:
        raise ValueError("input bundle schema is invalid")
    contract = _validate_frozen_points_contract(bundle)
    identities = _validate_authority_identities(bundle)
    calendar = _validate_calendar(bundle, contract, identities)
    parent_rows = _validate_parent(
        bundle,
        contract,
        calendar["development"],
        descriptor_context["parent_payload_fields"],
    )
    verification_receipts = _validate_verification_receipts(
        feature_history=descriptor_context["feature_history_receipt"],
        daily_basic=descriptor_context["daily_basic_receipt"],
        identities=identities,
        calendar=calendar,
    )
    source_rows = _validate_source_rows(bundle, source_dates=calendar["source_dates"])
    listing_transition = _validate_listing_and_transitions(bundle)
    upstream_board_ledger = _validate_upstream_ledger(
        bundle,
        source_dates=calendar["source_dates"],
        daily_receipt=verification_receipts["daily_basic"],
    )
    evaluator = _validate_evaluator(bundle, identities)
    _assert_fingerprints_unchanged(fingerprints)
    return {
        "input_authority_descriptor_file_sha256": descriptor_context[
            "descriptor_file_sha256"
        ],
        "source_descriptors": descriptor_context["source_descriptors"],
        "points_contract_sha256": points.FACTOR_V3_POINTS_CONTRACT_SHA256,
        "identities": identities,
        "calendar": calendar,
        "parent_rows": parent_rows,
        "verification_receipts": verification_receipts,
        "daily_index": source_rows["daily_index"],
        "cross_index": source_rows["cross_index"],
        "suspension_index": source_rows["suspension_index"],
        "listings": listing_transition["listings"],
        "transitions": listing_transition["transitions"],
        "upstream_board_ledger": upstream_board_ledger,
        "factor_v2_evaluation": evaluator,
        "input_fingerprints": fingerprints,
    }


def _active_transition_code(
    transitions: Sequence[Mapping[str, str]],
    *,
    security_id: str,
    trade_date: str,
) -> str | None:
    matches = [
        row["ts_code"]
        for row in transitions
        if row["security_id"] == security_id
        and row["effective_start"] <= trade_date <= row["effective_end"]
    ]
    return matches[0] if len(matches) == 1 else None


def _security_for_parent(
    transitions: Sequence[Mapping[str, str]],
    *,
    ts_code: str,
    signal_date: str,
) -> str | None:
    matches = [
        row["security_id"]
        for row in transitions
        if row["ts_code"] == ts_code
        and row["effective_start"] <= signal_date <= row["effective_end"]
    ]
    return matches[0] if len(matches) == 1 else None


def _add_calendar_months(value: str, months: int) -> str:
    parsed = date.fromisoformat(value)
    absolute_month = parsed.year * 12 + parsed.month - 1 + months
    year, month_offset = divmod(absolute_month, 12)
    month = month_offset + 1
    month_lengths = (31, 29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return date(year, month, min(parsed.day, month_lengths[month - 1])).isoformat()


def _midranks(values: Sequence[float]) -> list[float]:
    ordered = sorted(range(len(values)), key=lambda index: (values[index], index))
    ranks = [0.0] * len(values)
    cursor = 0
    while cursor < len(ordered):
        end = cursor + 1
        while end < len(ordered) and values[ordered[end]] == values[ordered[cursor]]:
            end += 1
        average_rank = (cursor + 1 + end) / 2.0
        for position in ordered[cursor:end]:
            ranks[position] = 2.0 * average_rank / (len(values) + 1.0) - 1.0
        cursor = end
    return ranks


def _exclusion_evidence_root(reason: str, identities: Mapping[str, str]) -> str:
    if reason == "ipo_age_less_than_6_calendar_months":
        return identities["pit_listing_membership_root_sha256"]
    if reason == "unresolved_authoritative_security_code_transition":
        return identities["security_code_transition_evidence_root_sha256"]
    return identities["daily_basic_normalized_row_authority_root_sha256"]


def _build_candidate_payload(preverified: Mapping[str, Any]) -> dict[str, Any]:
    calendar = _strict_mapping(preverified["calendar"], label="preverified calendar")
    development = list(calendar["development"])
    all_sessions = list(calendar["all_sessions"])
    positions = {trade_date: index for index, trade_date in enumerate(all_sessions)}
    identities = _strict_mapping(preverified["identities"], label="preverified identities")
    transitions = _strict_sequence(preverified["transitions"], label="preverified transitions")
    listings = _strict_mapping(preverified["listings"], label="preverified listings")
    daily_index = _strict_mapping(preverified["daily_index"], label="preverified daily index")
    cross_index = _strict_mapping(preverified["cross_index"], label="preverified cross index")
    suspension_index = set(preverified["suspension_index"])
    eligible_raw: list[dict[str, Any]] = []
    exclusions: list[dict[str, str]] = []
    for parent in preverified["parent_rows"]:
        candidate_key = str(parent["candidate_key"])
        signal_date = str(parent["signal_date"])
        security_id = _security_for_parent(
            transitions,
            ts_code=str(parent["ts_code"]),
            signal_date=signal_date,
        )
        if security_id is None:
            reason = "unresolved_authoritative_security_code_transition"
            exclusions.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "reason": reason,
                    "authority_evidence_root_sha256": _exclusion_evidence_root(reason, identities),
                }
            )
            continue
        listing = listings.get(security_id)
        if not isinstance(listing, Mapping):
            raise ValueError("PIT listing authority is missing for a resolved parent security")
        listing_date = str(listing["listing_date"])
        if not str(listing["membership_start"]) <= signal_date <= str(listing["membership_end"]):
            raise ValueError("PIT listing membership does not cover a parent signal date")
        if signal_date < _add_calendar_months(listing_date, 6):
            reason = "ipo_age_less_than_6_calendar_months"
            exclusions.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "reason": reason,
                    "authority_evidence_root_sha256": _exclusion_evidence_root(reason, identities),
                }
            )
            continue
        signal_position = positions[signal_date]
        window = all_sessions[signal_position - 250 : signal_position]
        if len(window) != 250:
            raise ValueError("candidate does not have the exact 250-session PIT window")
        observed: list[float] = []
        short_window_dates = set(window[-20:])
        short_observed: list[float] = []
        transition_unresolved = False
        for source_date in window:
            expected_code = _active_transition_code(
                transitions,
                security_id=security_id,
                trade_date=source_date,
            )
            if expected_code is None:
                transition_unresolved = True
                break
            key = (security_id, source_date)
            cross = cross_index.get(key)
            if cross is None:
                if key in suspension_index:
                    continue
                raise ValueError("unaccounted or unproven source missingness")
            daily = daily_index.get(key)
            if daily is None:
                raise ValueError("daily_basic missing for authoritatively traded security")
            if cross["ts_code"] != expected_code or daily["ts_code"] != expected_code:
                transition_unresolved = True
                break
            value = _strict_number(daily["turnover_rate_f"], label="observed turnover_rate_f")
            observed.append(value)
            if source_date in short_window_dates:
                short_observed.append(value)
        if transition_unresolved:
            reason = "unresolved_authoritative_security_code_transition"
            exclusions.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "reason": reason,
                    "authority_evidence_root_sha256": _exclusion_evidence_root(reason, identities),
                }
            )
            continue
        if len(short_observed) < 15:
            reason = "observed_trading_records_less_than_15_in_20_market_session_window"
            exclusions.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "reason": reason,
                    "authority_evidence_root_sha256": _exclusion_evidence_root(reason, identities),
                }
            )
            continue
        if len(observed) < 120:
            reason = "observed_trading_records_less_than_120_in_250_market_session_window"
            exclusions.append(
                {
                    "candidate_key": candidate_key,
                    "signal_date": signal_date,
                    "reason": reason,
                    "authority_evidence_root_sha256": _exclusion_evidence_root(reason, identities),
                }
            )
            continue
        t_minus_one = window[-1]
        t_minus_one_key = (security_id, t_minus_one)
        if t_minus_one_key in suspension_index or t_minus_one_key not in daily_index:
            raise ValueError("daily_basic source is unavailable at required T-1")
        level = _strict_number(
            daily_index[t_minus_one_key]["turnover_rate_f"],
            label="T-1 turnover_rate_f",
        )
        mean_20 = sum(short_observed) / len(short_observed)
        mean_250 = sum(observed) / len(observed)
        abnormal = math.log(mean_20 / mean_250)
        eligible_raw.append(
            {
                "parent": dict(parent),
                "source_input_date_label": t_minus_one,
                "turnover_rate_f": level,
                "abnormal_turnover_rate_f_20_to_250": abnormal,
            }
        )
    exclusions.sort(key=lambda row: (row["signal_date"], row["candidate_key"]))
    if any(row["reason"] not in _ALLOWED_EXCLUSION_REASONS for row in exclusions):
        raise ValueError("history exclusion reason is not preregistered")
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in eligible_raw:
        by_date[str(row["parent"]["signal_date"])].append(row)
    output_rows: list[dict[str, Any]] = []
    for signal_date in development:
        group = sorted(by_date[signal_date], key=lambda row: str(row["parent"]["candidate_key"]))
        levels = _midranks([float(row["turnover_rate_f"]) for row in group])
        abnormals = _midranks(
            [float(row["abnormal_turnover_rate_f_20_to_250"]) for row in group]
        )
        for source, level_rank, abnormal_rank in zip(group, levels, abnormals):
            output_rows.append(
                {
                    **source["parent"],
                    "source_input_date_label": source["source_input_date_label"],
                    "turnover_rate_f_rank": level_rank,
                    "abnormal_turnover_rate_f_20_to_250_rank": abnormal_rank,
                }
            )
    output_identity_rows = [
        {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"]}
        for row in output_rows
    ]
    exclusion_identity_rows = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
            "reason": row["reason"],
            "authority_evidence_root_sha256": row["authority_evidence_root_sha256"],
        }
        for row in exclusions
    ]
    eligible_candidate_keys = [row["candidate_key"] for row in output_rows]
    excluded_candidate_keys = [row["candidate_key"] for row in exclusions]
    exclusion_reason_buckets = [
        {
            "reason": reason,
            "excluded_row_count": sum(1 for row in exclusions if row["reason"] == reason),
            "candidate_keys_sha256": _canonical_sha256(
                [row["candidate_key"] for row in exclusions if row["reason"] == reason]
            ),
            "identity_rows_sha256": _canonical_sha256(
                [row for row in exclusion_identity_rows if row["reason"] == reason]
            ),
        }
        for reason in _ALLOWED_EXCLUSION_REASONS
        if any(row["reason"] == reason for row in exclusions)
    ]
    per_signal_ledger = []
    for signal_date in development:
        parent_count = sum(1 for row in preverified["parent_rows"] if row["signal_date"] == signal_date)
        eligible_count = sum(1 for row in output_rows if row["signal_date"] == signal_date)
        excluded_count = sum(1 for row in exclusions if row["signal_date"] == signal_date)
        if parent_count != eligible_count + excluded_count:
            raise ValueError("candidate ledger does not reconcile at signal-date grain")
        per_signal_ledger.append(
            {
                "signal_date": signal_date,
                "parent_row_count": parent_count,
                "history_eligible_row_count": eligible_count,
                "excluded_row_count": excluded_count,
            }
        )
    output_identity_root = _canonical_sha256(output_identity_rows)
    arms = {
        arm: {
            "feature_names": list(feature_names),
            "identity_root_sha256": output_identity_root,
            "row_count": len(output_rows),
            "projection_only": True,
        }
        for arm, feature_names in points.FACTOR_V3_POINTS_ARMS.items()
    }
    return {
        "schema_version": CANDIDATE_SCHEMA,
        "temporal_role": "development_4",
        "development_only": True,
        "authority_status": "DEVELOPMENT_ONLY_CANDIDATE_NOT_FORMAL",
        "formal_materialization_performed": False,
        "formal_receipt_eligible": False,
        "experiment_launch_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "points_contract_sha256": preverified["points_contract_sha256"],
        "frozen_points_formal_materializer_implemented": False,
        "producer_binding": factor_v3_materializer_producer_binding(),
        "input_authority_descriptor_file_sha256": preverified[
            "input_authority_descriptor_file_sha256"
        ],
        "source_descriptors": deepcopy(preverified["source_descriptors"]),
        "authority_identities": dict(preverified["identities"]),
        "calendar": {
            "development_session_count": len(development),
            "development_sessions_sha256": _canonical_sha256(development),
            "prewindow_session_count": 250,
            "prewindow_sessions_sha256": _canonical_sha256(calendar["prewindow"]),
            "source_date_union_count": len(calendar["source_dates"]),
            "source_dates_sha256": calendar["source_dates_sha256"],
            "source_dates": list(calendar["source_dates"]),
        },
        "factor_v2_evaluation": dict(preverified["factor_v2_evaluation"]),
        "upstream_board_ledger": deepcopy(preverified["upstream_board_ledger"]),
        "rows": output_rows,
        "rows_sha256": _canonical_sha256(output_rows),
        "history_eligible_row_count": len(output_rows),
        "history_eligible_identity_root_sha256": output_identity_root,
        "output_identity_root_sha256": output_identity_root,
        "eligible_candidate_keys_sha256": _canonical_sha256(eligible_candidate_keys),
        "eligible_identity_rows_sha256": output_identity_root,
        "excluded_row_count": len(exclusions),
        "excluded_candidate_keys_sha256": _canonical_sha256(excluded_candidate_keys),
        "exclusion_ledger": exclusions,
        "exclusion_ledger_sha256": _canonical_sha256(exclusion_identity_rows),
        "exclusion_reason_rows_sha256": _canonical_sha256(exclusion_identity_rows),
        "exclusion_reason_buckets": exclusion_reason_buckets,
        "exclusion_reason_buckets_sha256": _canonical_sha256(exclusion_reason_buckets),
        "per_signal_ledger": per_signal_ledger,
        "per_signal_ledger_sha256": _canonical_sha256(per_signal_ledger),
        "arms": arms,
    }


def _safe_output_root(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.exists():
        _assert_safe_existing_path(path, label="candidate output root")
        if not path.is_dir():
            raise ValueError("candidate output root must be a directory")
    else:
        path.mkdir(parents=True, exist_ok=False)
        _assert_safe_existing_path(path, label="candidate output root")
    for child in path.rglob("*"):
        if _is_reparse_point(child):
            raise ValueError("candidate output root must not contain a symlink or reparse point")
        if child.name.lower().endswith(("-wal", "-shm")) or child.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            raise ValueError("candidate output root must not contain a live store or WAL/SHM")
    return path


def _candidate_path(output_root: Path, *, artifact_sha256: str) -> Path:
    return output_root / _CANDIDATE_DIRECTORY / "sha256" / artifact_sha256[:2] / f"{artifact_sha256}.json"


def _receipt_path(output_root: Path, *, receipt_sha256: str) -> Path:
    return output_root / _RECEIPT_DIRECTORY / "sha256" / receipt_sha256[:2] / f"{receipt_sha256}.json"


def _write_create_only(path: Path, raw: bytes, *, label: str) -> bool:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_safe_existing_path(path.parent, label=f"{label} parent")
    expected_parent_identity = _directory_identity(path.parent, label=f"{label} parent")
    try:
        descriptor = os.open(
            str(path),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
    except FileExistsError:
        _assert_safe_existing_path(path, label=label)
        if (
            _directory_identity(path.parent, label=f"{label} parent")
            != expected_parent_identity
            or not path.is_file()
            or _read_regular_bytes_no_follow(path, label=label) != raw
        ):
            raise ValueError(f"{label} content-addressed candidate already exists with different bytes")
        return False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        raise
    if (
        _directory_identity(path.parent, label=f"{label} parent")
        != expected_parent_identity
        or not path.is_file()
        or _is_reparse_point(path)
        or _read_regular_bytes_no_follow(path, label=label) != raw
    ):
        raise ValueError(f"{label} create-only write did not preserve canonical bytes")
    return True


def _read_candidate_or_receipt(path_value: str | Path, *, label: str) -> tuple[Path, dict[str, Any], str]:
    path = Path(path_value)
    _assert_safe_existing_path(path, label=label)
    _assert_no_live_store_sidecars(path, label=label)
    if not path.is_file() or path.suffix.lower() != ".json":
        raise ValueError(f"{label} must be a regular JSON file")
    raw = _read_regular_bytes_no_follow(path, label=label)
    return path, _strict_json_loads(raw, label=label), _sha256_bytes(raw)


def _candidate_with_hash(payload: Mapping[str, Any]) -> tuple[dict[str, Any], str, bytes]:
    unsigned = dict(payload)
    artifact_sha256 = _canonical_sha256(unsigned)
    candidate = {**unsigned, "artifact_sha256": artifact_sha256}
    return candidate, artifact_sha256, _canonical_bytes(candidate)


def _receipt_payload(
    *,
    candidate: Mapping[str, Any],
    artifact_sha256: str,
    candidate_file_sha256: str,
) -> tuple[dict[str, Any], str, bytes]:
    unsigned = {
        "schema_version": RECEIPT_SCHEMA,
        "temporal_role": "development_4",
        "development_only": True,
        "authority_status": "DEVELOPMENT_ONLY_CANDIDATE_NOT_FORMAL",
        "formal_materialization_performed": False,
        "formal_receipt_eligible": False,
        "experiment_launch_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "artifact_sha256": artifact_sha256,
        "candidate_file_sha256": _strict_sha256(
            candidate_file_sha256,
            label="candidate file SHA",
        ),
        "points_contract_sha256": candidate["points_contract_sha256"],
        "frozen_points_formal_materializer_implemented": False,
        "producer_binding": deepcopy(candidate["producer_binding"]),
        "input_authority_descriptor_file_sha256": candidate[
            "input_authority_descriptor_file_sha256"
        ],
        "source_descriptors": deepcopy(candidate["source_descriptors"]),
        "authority_identities": deepcopy(candidate["authority_identities"]),
        "factor_v2_evaluation": deepcopy(candidate["factor_v2_evaluation"]),
        "output_identity_root_sha256": candidate["output_identity_root_sha256"],
        "history_eligible_row_count": candidate["history_eligible_row_count"],
        "excluded_row_count": candidate["excluded_row_count"],
        "rows_sha256": candidate["rows_sha256"],
        "eligible_candidate_keys_sha256": candidate["eligible_candidate_keys_sha256"],
        "eligible_identity_rows_sha256": candidate["eligible_identity_rows_sha256"],
        "excluded_candidate_keys_sha256": candidate["excluded_candidate_keys_sha256"],
        "exclusion_ledger_sha256": candidate["exclusion_ledger_sha256"],
        "exclusion_reason_rows_sha256": candidate["exclusion_reason_rows_sha256"],
        "exclusion_reason_buckets_sha256": candidate["exclusion_reason_buckets_sha256"],
        "per_signal_ledger_sha256": candidate["per_signal_ledger_sha256"],
    }
    receipt_sha256 = _canonical_sha256(unsigned)
    receipt = {**unsigned, "receipt_sha256": receipt_sha256}
    return receipt, receipt_sha256, _canonical_bytes(receipt)


def verify_factor_v3_development_candidate(
    *,
    candidate_path: str | Path,
    receipt_path: str | Path,
    input_authority_descriptor_path: str | Path,
    expected_input_authority_descriptor_sha256: str,
    expected_artifact_sha256: str,
    expected_receipt_sha256: str,
) -> dict[str, Any]:
    """Replay a development candidate from immutable JSON snapshots without writing."""

    receipt_file, receipt, receipt_file_sha256 = _read_candidate_or_receipt(
        receipt_path,
        label="materialization receipt",
    )
    expected_receipt = _strict_sha256(expected_receipt_sha256, label="expected receipt SHA")
    claimed_receipt_sha256 = receipt.get("receipt_sha256")
    if claimed_receipt_sha256 != expected_receipt or _canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    ) != expected_receipt:
        raise ValueError("materialization receipt self-integrity drifted")
    candidate_file, candidate, candidate_file_sha256 = _read_candidate_or_receipt(
        candidate_path,
        label="materialization candidate",
    )
    expected_artifact = _strict_sha256(expected_artifact_sha256, label="expected artifact SHA")
    if candidate.get("artifact_sha256") != expected_artifact or _canonical_sha256(
        {key: value for key, value in candidate.items() if key != "artifact_sha256"}
    ) != expected_artifact:
        raise ValueError("materialization candidate self-integrity drifted")
    if receipt.get("artifact_sha256") != expected_artifact:
        raise ValueError("materialization receipt is not bound to candidate")
    _assert_no_capability(candidate, path="materialization candidate")
    _assert_no_capability(receipt, path="materialization receipt")
    before = {
        "candidate": (candidate_file.stat().st_size, candidate_file.stat().st_mtime_ns, candidate_file_sha256),
        "receipt": (receipt_file.stat().st_size, receipt_file.stat().st_mtime_ns, receipt_file_sha256),
    }
    preverified = preverify_factor_v3_development_input_bundle(
        input_authority_descriptor_path=input_authority_descriptor_path,
        expected_input_authority_descriptor_sha256=(
            expected_input_authority_descriptor_sha256
        ),
    )
    rebuilt_payload = _build_candidate_payload(preverified)
    _assert_fingerprints_unchanged(preverified["input_fingerprints"])
    rebuilt_candidate, rebuilt_artifact_sha256, rebuilt_raw = _candidate_with_hash(rebuilt_payload)
    rebuilt_receipt, rebuilt_receipt_sha256, rebuilt_receipt_raw = _receipt_payload(
        candidate=rebuilt_candidate,
        artifact_sha256=rebuilt_artifact_sha256,
        candidate_file_sha256=_sha256_bytes(rebuilt_raw),
    )
    if (
        rebuilt_artifact_sha256 != expected_artifact
        or rebuilt_receipt_sha256 != expected_receipt
        or rebuilt_raw != _read_regular_bytes_no_follow(
            candidate_file,
            label="materialization candidate",
        )
        or receipt.get("candidate_file_sha256") != candidate_file_sha256
        or rebuilt_receipt_raw != _read_regular_bytes_no_follow(
            receipt_file,
            label="materialization receipt",
        )
        or rebuilt_candidate != candidate
        or rebuilt_receipt != receipt
    ):
        raise ValueError("materialization post-verifier replay drifted")
    after = {
        "candidate": (
            candidate_file.stat().st_size,
            candidate_file.stat().st_mtime_ns,
            _sha256_bytes(
                _read_regular_bytes_no_follow(
                    candidate_file,
                    label="materialization candidate",
                )
            ),
        ),
        "receipt": (
            receipt_file.stat().st_size,
            receipt_file.stat().st_mtime_ns,
            _sha256_bytes(
                _read_regular_bytes_no_follow(
                    receipt_file,
                    label="materialization receipt",
                )
            ),
        ),
    }
    if before != after:
        raise ValueError("materialization verifier modified an immutable candidate or receipt")
    return {
        "verified": True,
        "artifact_sha256": expected_artifact,
        "receipt_sha256": expected_receipt,
        "candidate_file_sha256": candidate_file_sha256,
        "receipt_file_sha256": receipt_file_sha256,
        "history_eligible_row_count": candidate["history_eligible_row_count"],
        "excluded_row_count": candidate["excluded_row_count"],
    }


def materialize_factor_v3_development_candidate(
    *,
    input_authority_descriptor_path: str | Path,
    expected_input_authority_descriptor_sha256: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Create and post-verify one development-only candidate, or fail closed."""

    preverified = preverify_factor_v3_development_input_bundle(
        input_authority_descriptor_path=input_authority_descriptor_path,
        expected_input_authority_descriptor_sha256=(
            expected_input_authority_descriptor_sha256
        ),
    )
    payload = _build_candidate_payload(preverified)
    _assert_fingerprints_unchanged(preverified["input_fingerprints"])
    candidate, artifact_sha256, candidate_raw = _candidate_with_hash(payload)
    candidate_file_sha256 = _sha256_bytes(candidate_raw)
    receipt, receipt_sha256, receipt_raw = _receipt_payload(
        candidate=candidate,
        artifact_sha256=artifact_sha256,
        candidate_file_sha256=candidate_file_sha256,
    )
    destination = _safe_output_root(output_root)
    candidate_file = _candidate_path(destination, artifact_sha256=artifact_sha256)
    receipt_file = _receipt_path(destination, receipt_sha256=receipt_sha256)
    _write_create_only(candidate_file, candidate_raw, label="materialization candidate")
    _write_create_only(receipt_file, receipt_raw, label="materialization receipt")
    verification = verify_factor_v3_development_candidate(
        candidate_path=candidate_file,
        receipt_path=receipt_file,
        input_authority_descriptor_path=input_authority_descriptor_path,
        expected_input_authority_descriptor_sha256=(
            expected_input_authority_descriptor_sha256
        ),
        expected_artifact_sha256=artifact_sha256,
        expected_receipt_sha256=receipt_sha256,
    )
    return {
        "candidate_path": str(candidate_file),
        "receipt_path": str(receipt_file),
        "artifact_sha256": artifact_sha256,
        "receipt_sha256": receipt_sha256,
        "candidate_file_sha256": candidate_file_sha256,
        "receipt_file_sha256": _sha256_bytes(receipt_raw),
        "verification": verification,
    }
