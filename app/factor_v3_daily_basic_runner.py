"""Resumable, daily-basic-only collection for the frozen Factor V3 development window.

The module only orchestrates a sealed Jiaoch ``daily_basic`` route.  It never
opens the historical-minute slot, and it deliberately leaves materialization
and production eligibility outside of this development-only collection stage.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Any
import uuid

from app.durable_io import fsync_directory, fsync_file

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover - Windows uses msvcrt.
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:  # pragma: no cover - POSIX uses fcntl.
    _msvcrt = None


__all__ = (
    "FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR",
    "FactorV3DailyBasicRunnerError",
    "build_factor_v3_daily_basic_run_spec",
    "load_factor_v3_daily_basic_run_spec",
    "run_factor_v3_daily_basic_collection",
    "verify_factor_v3_daily_basic_run",
    "main",
)


_RUN_SPEC_SCHEMA = "factor-v3-daily-basic-run-spec/v2"
_RUN_STATE_SCHEMA = "factor-v3-daily-basic-run-state/v2"
_POLICY_SCHEMA = "jiaoch-credential-factor-v3-daily-basic-policy-descriptor/v1"
_POLICY_DOCUMENT_SCHEMA = "jiaoch-credential-factor-v3-daily-basic-routing-policy/v1"
_MAX_RUN_SPEC_BYTES = 4 * 1024 * 1024
_MAX_STATE_BYTES = 2 * 1024 * 1024
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_UUID4_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
_RUN_SPEC_FIELDS = frozenset(
    {
        "collection_policy_descriptor",
        "collector",
        "exact_set_authority_inputs",
        "run_spec_sha256",
        "schema",
        "session_count",
        "sessions",
        "sessions_sha256",
        "source_authority_root_sha256",
    }
)
_STATE_FIELDS = frozenset(
    {
        "collection_set_refs",
        "completed_session_count",
        "credential_generation_id",
        "exact_set_publication",
        "receipt",
        "run_spec_sha256",
        "schema",
        "state_sha256",
        "status",
    }
)
_EXACT_INPUT_FIELDS = frozenset(
    {
        "audited_development_universe_sqlite_path",
        "expected_development_artifact_root_sha256",
        "expected_development_coverage_audit_sha256",
        "expected_development_temporal_contract_sha256",
        "expected_development_temporal_role",
        "expected_security_code_transition_contract_sha256",
        "expected_feature_history_frozen_source_attestation_sha256",
        "expected_feature_history_frozen_source_commit",
        "feature_history_frozen_source_attestation_path",
        "feature_history_frozen_source_root",
        "feature_history_run_root",
        "feature_history_run_spec_path",
        "security_code_transition_evidence_root",
    }
)
_COLLECTOR_FIELDS = frozenset({"max_attempts", "timeout_seconds", "workers"})
_TERMINAL_PUBLICATION_FIELDS = frozenset(
    {
        "attestation_relative_path",
        "attestation_sha256",
        "publication_relative_path",
        "publication_sha256",
        "receipt_relative_path",
        "receipt_sha256",
        "schema",
    }
)
_TERMINAL_RECEIPT_FIELDS = frozenset(
    {
        "authority_root_sha256",
        "receipt_relative_path",
        "receipt_sha256",
        "verified",
    }
)
_TERMINAL_VERIFICATION_FIELDS = frozenset(
    {
        "authority_root_sha256",
        "receipt_sha256",
        "trade_date_count",
        "trade_dates",
        "verified",
    }
)
_FAILURE_DIAGNOSTIC_SCHEMA = "jiaoch-factor-v3-daily-basic-collection-failure/v2"
_FAILURE_DIAGNOSTIC_FIELDS = frozenset(
    {"attempts", "route_id", "schema", "trade_date"}
)
_FAILURE_ATTEMPT_FIELDS = frozenset(
    {
        "attempt",
        "body_complete",
        "exception_type",
        "failure_code",
        "http_status",
        "outcome",
    }
)
_FAILURE_OUTCOMES = frozenset(
    {
        "transport_exception",
        "raw_publication_rejected",
        "http_entity_rejected",
        "response_shape_rejected",
    }
)
_FAILURE_CODES = frozenset(
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
_FAILURE_EXCEPTION_TYPES = frozenset(
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

_DAILY_BASIC_POLICY_DOCUMENT = {
    "schema": _POLICY_DOCUMENT_SCHEMA,
    "routes": [
        {
            "api_name": "daily_basic",
            "credential_slot_id": "points-primary",
            "purpose": "factor-v3-daily-basic",
            "route_id": "factor-v3-daily-basic:points-primary:daily_basic",
        }
    ],
}
FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR = {
    "credential_proof_claimed": False,
    "document": _DAILY_BASIC_POLICY_DOCUMENT,
    "schema": _POLICY_SCHEMA,
    "sha256": hashlib.sha256(
        json.dumps(
            _DAILY_BASIC_POLICY_DOCUMENT,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest(),
}


class FactorV3DailyBasicRunnerError(ValueError):
    """Raised when this bounded daily_basic collection cannot safely continue."""


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic canonical JSON rejected") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
    return value


def _require_uuid4(value: Any, *, label: str) -> str:
    if type(value) is not str or _UUID4_RE.fullmatch(value) is None:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
    return value


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_RUN_SPEC_BYTES:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in items:
            if key in output:
                raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
            output[key] = item
        return output

    try:
        value = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _value: None)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected") from exc
    if type(value) is not dict or _canonical_bytes(value) != raw.rstrip(b"\n"):
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
    return value


def _read_json(path: Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    try:
        before = path.stat()
        if path.is_symlink() or not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
        raw = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} unavailable") from exc
    if before.st_size != len(raw) or after.st_size != before.st_size or after.st_mtime_ns != before.st_mtime_ns:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} drifted")
    return _strict_json(raw, label=label)


def _safe_directory(path: Path, *, label: str, create: bool) -> Path:
    if path.is_symlink():
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
    if not path.exists():
        if not create or not path.parent.is_dir() or path.parent.is_symlink():
            raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} unavailable")
        try:
            path.mkdir()
            fsync_directory(path.parent)
        except OSError as exc:
            raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} unavailable") from exc
    if not path.is_dir() or path.is_symlink():
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} rejected")
    return path.resolve(strict=True)


def _create_only(path: Path, content: bytes, *, label: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise FactorV3DailyBasicRunnerError(f"factor-v3 daily-basic {label} exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    fsync_directory(path.parent)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state temporary exists")
    try:
        _create_only(temporary, _canonical_bytes(dict(payload)) + b"\n", label="state temporary")
        os.replace(temporary, path)
        fsync_file(path)
        fsync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def _validated_failure_diagnostic(value: Any) -> dict[str, Any]:
    if (
        type(value) is not dict
        or set(value) != _FAILURE_DIAGNOSTIC_FIELDS
        or value.get("schema") != _FAILURE_DIAGNOSTIC_SCHEMA
        or value.get("route_id") != "factor-v3-daily-basic:points-primary:daily_basic"
        or type(value.get("trade_date")) is not str
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic failure diagnostic rejected"
        )
    try:
        if date.fromisoformat(value["trade_date"]).isoformat() != value["trade_date"]:
            raise ValueError
    except ValueError as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic failure diagnostic rejected"
        ) from exc
    attempts = value.get("attempts")
    if type(attempts) is not list or not 1 <= len(attempts) <= 10:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic failure diagnostic rejected"
        )
    for expected_number, attempt in enumerate(attempts, 1):
        if (
            type(attempt) is not dict
            or set(attempt) != _FAILURE_ATTEMPT_FIELDS
            or attempt.get("attempt") != expected_number
            or attempt.get("outcome") not in _FAILURE_OUTCOMES
            or attempt.get("failure_code") not in _FAILURE_CODES
            or attempt.get("exception_type") not in _FAILURE_EXCEPTION_TYPES
            or (
                attempt.get("http_status") is not None
                and (
                    type(attempt.get("http_status")) is not int
                    or not 100 <= attempt["http_status"] <= 599
                )
            )
            or (
                attempt.get("body_complete") is not None
                and type(attempt.get("body_complete")) is not bool
            )
        ):
            raise FactorV3DailyBasicRunnerError(
                "factor-v3 daily-basic failure diagnostic rejected"
            )
    return json.loads(_canonical_bytes(value))


def _persist_collection_failure_diagnostic(
    *, paths: Mapping[str, Path], error: BaseException
) -> None:
    from app.jiaoch_daily_basic_collection_set import JiaochDailyBasicCollectionError

    if not isinstance(error, JiaochDailyBasicCollectionError):
        return
    payload = _validated_failure_diagnostic(error.diagnostic)
    path = paths["failure_diagnostic"]
    if path.exists():
        existing = _read_json(
            path,
            label="collection failure diagnostic",
            max_bytes=16 * 1024,
        )
        if existing != payload:
            raise FactorV3DailyBasicRunnerError(
                "factor-v3 daily-basic failure diagnostic drifted"
            )
        return
    _create_only(
        path,
        _canonical_bytes(payload) + b"\n",
        label="collection failure diagnostic",
    )


def _validate_sessions(value: Any, *, expected_count: int | None = None) -> list[str]:
    if type(value) is not list or not value or any(type(item) is not str for item in value):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic sessions rejected")
    if value != sorted(value) or len(set(value)) != len(value):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic requires exact ordered 733 sessions")
    for item in value:
        try:
            if date.fromisoformat(item).isoformat() != item:
                raise ValueError
        except ValueError as exc:
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic sessions rejected") from exc
    if expected_count is not None and len(value) != expected_count:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic requires exact 733 sessions")
    return list(value)


def _validated_collector(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _COLLECTOR_FIELDS:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collector rejected")
    attempts, timeout, workers = value.get("max_attempts"), value.get("timeout_seconds"), value.get("workers")
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or not 1 <= attempts <= 10
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0.1 <= float(timeout) <= 300.0
        or workers != 1
    ):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collector rejected")
    return {"max_attempts": attempts, "timeout_seconds": float(timeout), "workers": 1}


def _validated_policy(value: Any) -> dict[str, Any]:
    if type(value) is not dict or value != FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection policy rejected")
    return json.loads(_canonical_bytes(value))


def _reject_secret_shape(value: Any) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            if str(key).lower().replace("-", "_") in {"token", "credential", "secret", "password", "api_key"}:
                raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run spec must not contain credentials")
            _reject_secret_shape(nested)
    elif type(value) is list:
        for nested in value:
            _reject_secret_shape(nested)


def _validated_exact_inputs(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _EXACT_INPUT_FIELDS:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic exact-set inputs rejected")
    output = json.loads(_canonical_bytes(value))
    for field in (
        "audited_development_universe_sqlite_path",
        "feature_history_run_root",
        "feature_history_run_spec_path",
        "feature_history_frozen_source_attestation_path",
        "feature_history_frozen_source_root",
        "security_code_transition_evidence_root",
    ):
        if type(output[field]) is not str or not Path(output[field]).is_absolute():
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic exact-set inputs rejected")
    for field in (
        "expected_development_artifact_root_sha256",
        "expected_development_coverage_audit_sha256",
        "expected_development_temporal_contract_sha256",
        "expected_feature_history_frozen_source_attestation_sha256",
        "expected_security_code_transition_contract_sha256",
    ):
        _require_sha256(output[field], label=field)
    if output["expected_development_temporal_role"] != "development_4":
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic temporal role rejected")
    from app import factor_v3_feature_history_frozen_source_attestation as frozen

    if (
        output["feature_history_frozen_source_root"]
        != str(frozen.FROZEN_SOURCE_ROOT)
        or output["expected_feature_history_frozen_source_commit"]
        != frozen.FROZEN_SOURCE_COMMIT
        or _COMMIT_RE.fullmatch(
            output["expected_feature_history_frozen_source_commit"]
        )
        is None
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic frozen source identity rejected"
        )
    return output


def _verified_733_sources(
    exact_set_authority_inputs: Mapping[str, Any],
) -> tuple[list[str], str]:
    """Rebuild the ordered 250+483 union from both durable authorities."""

    from app import factor_v3_daily_basic_733_exact_set_authority as authority

    inputs = _validated_exact_inputs(exact_set_authority_inputs)
    try:
        source, identity = authority._load_733_authority(
            feature_history_run_spec_path=inputs["feature_history_run_spec_path"],
            feature_history_run_root=inputs["feature_history_run_root"],
            feature_history_frozen_source_attestation_path=inputs[
                "feature_history_frozen_source_attestation_path"
            ],
            expected_feature_history_frozen_source_attestation_sha256=inputs[
                "expected_feature_history_frozen_source_attestation_sha256"
            ],
            feature_history_frozen_source_root=inputs[
                "feature_history_frozen_source_root"
            ],
            expected_feature_history_frozen_source_commit=inputs[
                "expected_feature_history_frozen_source_commit"
            ],
            audited_development_universe_sqlite_path=inputs[
                "audited_development_universe_sqlite_path"
            ],
            expected_development_coverage_audit_sha256=inputs[
                "expected_development_coverage_audit_sha256"
            ],
            expected_development_artifact_root_sha256=inputs[
                "expected_development_artifact_root_sha256"
            ],
            expected_development_temporal_contract_sha256=inputs[
                "expected_development_temporal_contract_sha256"
            ],
            expected_development_temporal_role=inputs[
                "expected_development_temporal_role"
            ],
        )
    except (AttributeError, KeyError, OSError, ValueError) as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic 733 source authorities rejected"
        ) from exc
    sessions = [partition.trade_date for partition in source.partitions]
    _validate_sessions(sessions, expected_count=733)
    root_sha256 = identity.get("root_sha256")
    _require_sha256(root_sha256, label="source authority root")
    if identity.get("sessions_sha256") != _sha256(sessions):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic 733 source authorities rejected"
        )
    return sessions, root_sha256


def _validated_run_spec(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RUN_SPEC_FIELDS or value.get("schema") != _RUN_SPEC_SCHEMA:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run spec rejected")
    expected = _require_sha256(value.get("run_spec_sha256"), label="run spec hash")
    unsigned = {key: item for key, item in value.items() if key != "run_spec_sha256"}
    if _sha256(unsigned) != expected:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run spec rejected")
    _reject_secret_shape(unsigned)
    sessions = _validate_sessions(value.get("sessions"), expected_count=733)
    if value.get("session_count") != len(sessions) or value.get("sessions_sha256") != _sha256(sessions):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run spec rejected")
    _validated_policy(value.get("collection_policy_descriptor"))
    _validated_collector(value.get("collector"))
    _validated_exact_inputs(value.get("exact_set_authority_inputs"))
    _require_sha256(value.get("source_authority_root_sha256"), label="source authority root")
    return json.loads(_canonical_bytes(value))


def build_factor_v3_daily_basic_run_spec(
    *,
    exact_set_authority_inputs: Mapping[str, Any],
    timeout_seconds: float,
    max_attempts: int,
) -> dict[str, Any]:
    """Create the 733-date acquisition spec derived from the two authorities.

    These are collection sessions, not a materializer's T-1 feature labels:
    the full 250+483 union stays frozen here even when a downstream transform
    later consumes a smaller shifted source-date label union.
    """

    validated_inputs = _validated_exact_inputs(exact_set_authority_inputs)
    sessions, source_authority_root_sha256 = _verified_733_sources(validated_inputs)
    _validate_sessions(sessions, expected_count=733)
    if sessions != sorted(sessions) or len(set(sessions)) != 733:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic requires exact 733 sessions")
    unsigned = {
        "schema": _RUN_SPEC_SCHEMA,
        "source_authority_root_sha256": source_authority_root_sha256,
        "sessions": sessions,
        "session_count": len(sessions),
        "sessions_sha256": _sha256(sessions),
        "collection_policy_descriptor": json.loads(_canonical_bytes(FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR)),
        "collector": _validated_collector(
            {"timeout_seconds": timeout_seconds, "max_attempts": max_attempts, "workers": 1}
        ),
        "exact_set_authority_inputs": validated_inputs,
    }
    return _validated_run_spec({**unsigned, "run_spec_sha256": _sha256(unsigned)})


def load_factor_v3_daily_basic_run_spec(path: str | Path) -> dict[str, Any]:
    return _validated_run_spec(_read_json(Path(path), label="run spec", max_bytes=_MAX_RUN_SPEC_BYTES))


def _paths(run_root: str | Path, *, create: bool) -> dict[str, Path]:
    root_path = Path(run_root)
    if not root_path.is_absolute():
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run root must be absolute")
    root = _safe_directory(root_path, label="run root", create=create)
    return {
        "root": root,
        "lock": root / ".factor-v3-daily-basic-runner.lock",
        "run_spec": root / "run-spec.json",
        "state": root / "state.json",
        "failure_diagnostic": root / "collection-failure.json",
        "points": root / "points-output",
        "authority": root / "exact-set-authority",
    }


def _state_payload(
    *,
    run_spec_sha256: str,
    status: str,
    completed_session_count: int,
    credential_generation_id: str | None,
    collection_set_refs: Sequence[Mapping[str, Any]],
    exact_set_publication: Mapping[str, Any] | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"initialized", "collecting", "collected", "published", "verified", "failed"}:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    if type(completed_session_count) is not int or not 0 <= completed_session_count <= 733:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    refs = [dict(item) for item in collection_set_refs]
    if len(refs) != completed_session_count:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    if status in {"collecting", "collected", "published", "verified"} and credential_generation_id is None:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    if credential_generation_id is not None:
        _require_uuid4(credential_generation_id, label="credential generation")
    if status in {"published", "verified"} and (exact_set_publication is None or receipt is None):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    unsigned = {
        "schema": _RUN_STATE_SCHEMA,
        "run_spec_sha256": run_spec_sha256,
        "status": status,
        "completed_session_count": completed_session_count,
        "credential_generation_id": credential_generation_id,
        "collection_set_refs": refs,
        "exact_set_publication": None if exact_set_publication is None else dict(exact_set_publication),
        "receipt": None if receipt is None else dict(receipt),
    }
    return {**unsigned, "state_sha256": _sha256(unsigned)}


def _validated_state(value: Any, *, spec: Mapping[str, Any]) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _STATE_FIELDS or value.get("schema") != _RUN_STATE_SCHEMA:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    unsigned = {key: item for key, item in value.items() if key != "state_sha256"}
    if value.get("state_sha256") != _sha256(unsigned) or value.get("run_spec_sha256") != spec["run_spec_sha256"]:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic state rejected")
    return _state_payload(
        run_spec_sha256=value["run_spec_sha256"], status=value["status"],
        completed_session_count=value["completed_session_count"],
        credential_generation_id=value["credential_generation_id"],
        collection_set_refs=value["collection_set_refs"],
        exact_set_publication=value["exact_set_publication"], receipt=value["receipt"],
    )


def _load_or_initialize(paths: Mapping[str, Path], spec: Mapping[str, Any], *, allow_initialize: bool) -> dict[str, Any]:
    snapshot = _canonical_bytes(spec) + b"\n"
    if not paths["run_spec"].exists():
        if not allow_initialize:
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run is absent")
        _create_only(paths["run_spec"], snapshot, label="run spec snapshot")
    elif _read_json(paths["run_spec"], label="run spec snapshot", max_bytes=_MAX_RUN_SPEC_BYTES) != spec:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run spec drifted")
    if not paths["state"].exists():
        if not allow_initialize:
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run state is absent")
        initial = _state_payload(run_spec_sha256=spec["run_spec_sha256"], status="initialized", completed_session_count=0, credential_generation_id=None, collection_set_refs=[])
        _atomic_json(paths["state"], initial)
        return initial
    return _validated_state(_read_json(paths["state"], label="run state", max_bytes=_MAX_STATE_BYTES), spec=spec)


def _validate_open_lock_identity(path: Path, descriptor: int) -> None:
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    try:
        parent_stat = path.parent.lstat()
        descriptor_stat = os.fstat(descriptor)
        path_stat = path.lstat()
        if (
            path.parent.is_symlink()
            or not stat.S_ISDIR(parent_stat.st_mode)
            or getattr(parent_stat, "st_file_attributes", 0) & reparse_flag
            or path.is_symlink()
            or not stat.S_ISREG(descriptor_stat.st_mode)
            or not stat.S_ISREG(path_stat.st_mode)
            or descriptor_stat.st_nlink != 1
            or path_stat.st_nlink != 1
            or getattr(descriptor_stat, "st_file_attributes", 0) & reparse_flag
            or getattr(path_stat, "st_file_attributes", 0) & reparse_flag
            or (descriptor_stat.st_dev, descriptor_stat.st_ino)
            != (path_stat.st_dev, path_stat.st_ino)
        ):
            raise OSError
    except OSError as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic lock unavailable"
        ) from exc


@contextmanager
def _run_lock(path: Path) -> Iterator[None]:
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        parent_stat = path.parent.lstat()
        if (
            path.parent.is_symlink()
            or not stat.S_ISDIR(parent_stat.st_mode)
            or getattr(parent_stat, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            or path.is_symlink()
        ):
            raise OSError
    except OSError as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic lock unavailable"
        ) from exc
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic lock unavailable"
        ) from exc
    try:
        _validate_open_lock_identity(path, descriptor)
    except FactorV3DailyBasicRunnerError:
        os.close(descriptor)
        raise
    try:
        if _fcntl is not None:
            _fcntl.flock(descriptor, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
        elif _msvcrt is not None:
            if os.fstat(descriptor).st_size == 0:
                os.write(descriptor, b"\0")
                os.fsync(descriptor)
            os.lseek(descriptor, 0, os.SEEK_SET)
            _msvcrt.locking(descriptor, _msvcrt.LK_NBLCK, 1)
        else:
            raise FactorV3DailyBasicRunnerError(
                "factor-v3 daily-basic advisory locking is unavailable"
            )
        _validate_open_lock_identity(path, descriptor)
    except (OSError, FactorV3DailyBasicRunnerError) as exc:
        os.close(descriptor)
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic run is already locked"
        ) from exc
    try:
        yield
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(descriptor, _fcntl.LOCK_UN)
            elif _msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                _msvcrt.locking(descriptor, _msvcrt.LK_UNLCK, 1)
        except OSError as exc:
            raise FactorV3DailyBasicRunnerError(
                "factor-v3 daily-basic lock cleanup failed"
            ) from exc
        finally:
            os.close(descriptor)


def _run_credential_generation_id(*, run_spec_path: str | Path, run_root: str | Path) -> str:
    spec = load_factor_v3_daily_basic_run_spec(run_spec_path)
    paths = _paths(run_root, create=True)
    with _run_lock(paths["lock"]):
        state = _load_or_initialize(paths, spec, allow_initialize=True)
        existing = state["credential_generation_id"]
        if existing is not None:
            return _require_uuid4(existing, label="credential generation")
        generation_id = str(uuid.uuid4())
        _atomic_json(paths["state"], _state_payload(
            run_spec_sha256=spec["run_spec_sha256"], status="initialized", completed_session_count=0,
            credential_generation_id=generation_id, collection_set_refs=[]
        ))
        return generation_id


def _collect_one_daily_basic(**kwargs: Any) -> dict[str, Any]:
    from app.jiaoch_daily_basic_collection_set import _collect_jiaoch_daily_basic_collection_set_with_route_credential

    return _collect_jiaoch_daily_basic_collection_set_with_route_credential(**kwargs)


def _verify_one_daily_basic(*, points_output_root: Path, ref: Mapping[str, Any]) -> dict[str, Any]:
    from app.jiaoch_daily_basic_collection_set import verify_jiaoch_daily_basic_collection_set

    if type(ref) is not dict or set(ref) != {"trade_date", "collection_set_relative_path", "collection_set_sha256"}:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection reference rejected")
    verified = verify_jiaoch_daily_basic_collection_set(
        output_root=points_output_root,
        collection_set_relative_path=ref["collection_set_relative_path"],
        expected_collection_set_sha256=ref["collection_set_sha256"],
    )
    if verified.get("verified") is not True or verified.get("trade_date") != ref["trade_date"]:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection verification rejected")
    return dict(ref)


def _assert_complete_points_output(points_root: Path, refs: Sequence[Mapping[str, Any]], sessions: Sequence[str]) -> None:
    if len(refs) != len(sessions) or [item.get("trade_date") for item in refs] != list(sessions):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection coverage rejected")
    for ref in refs:
        _verify_one_daily_basic(points_output_root=points_root, ref=ref)
    for path in points_root.rglob("*"):
        if path.is_symlink() or path.name.endswith((".tmp", ".partial", "-wal", "-shm")):
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic points output has partial artifacts")


def _exact_set_kwargs(
    *,
    authority_root: Path,
    points_root: Path,
    refs: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
    expected_source_authority_root_sha256: str,
) -> dict[str, Any]:
    validated = _validated_exact_inputs(inputs)
    source_root = _require_sha256(
        expected_source_authority_root_sha256,
        label="source authority root",
    )
    return {
        "feature_history_run_spec_path": validated["feature_history_run_spec_path"],
        "feature_history_run_root": validated["feature_history_run_root"],
        "feature_history_frozen_source_attestation_path": validated[
            "feature_history_frozen_source_attestation_path"
        ],
        "expected_feature_history_frozen_source_attestation_sha256": validated[
            "expected_feature_history_frozen_source_attestation_sha256"
        ],
        "feature_history_frozen_source_root": validated[
            "feature_history_frozen_source_root"
        ],
        "expected_feature_history_frozen_source_commit": validated[
            "expected_feature_history_frozen_source_commit"
        ],
        "audited_development_universe_sqlite_path": validated[
            "audited_development_universe_sqlite_path"
        ],
        "expected_development_coverage_audit_sha256": validated[
            "expected_development_coverage_audit_sha256"
        ],
        "expected_development_artifact_root_sha256": validated[
            "expected_development_artifact_root_sha256"
        ],
        "expected_development_temporal_contract_sha256": validated[
            "expected_development_temporal_contract_sha256"
        ],
        "expected_development_temporal_role": validated[
            "expected_development_temporal_role"
        ],
        "expected_source_authority_root_sha256": source_root,
        "points_output_root": points_root,
        "collection_set_refs": refs,
        "security_code_transition_evidence_root": validated[
            "security_code_transition_evidence_root"
        ],
        "expected_security_code_transition_contract_sha256": validated[
            "expected_security_code_transition_contract_sha256"
        ],
        "output_root": authority_root,
    }


def _publish_exact_set_coverage(
    *,
    authority_root: Path,
    points_root: Path,
    refs: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
    expected_source_authority_root_sha256: str,
) -> dict[str, Any]:
    from app import factor_v3_daily_basic_733_exact_set_authority as authority

    publication = authority.publish_factor_v3_daily_basic_733_exact_set_coverage(
        **_exact_set_kwargs(
            authority_root=authority_root,
            points_root=points_root,
            refs=refs,
            inputs=inputs,
            expected_source_authority_root_sha256=(
                expected_source_authority_root_sha256
            ),
        )
    )
    if (
        type(publication) is not dict
        or set(publication) != _TERMINAL_PUBLICATION_FIELDS
        or publication["schema"]
        != "factor-v3-daily-basic-733-exact-set-publication/v2"
    ):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic exact-set publication rejected")
    return dict(publication)


def _verify_exact_set_coverage(
    *,
    authority_root: Path,
    points_root: Path,
    refs: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
    publication: Mapping[str, Any],
    expected_source_authority_root_sha256: str,
) -> dict[str, Any]:
    from app import factor_v3_daily_basic_733_exact_set_authority as authority

    try:
        verified = authority.verify_factor_v3_daily_basic_733_exact_set_coverage(
            **_exact_set_kwargs(
                authority_root=authority_root,
                points_root=points_root,
                refs=refs,
                inputs=inputs,
                expected_source_authority_root_sha256=(
                    expected_source_authority_root_sha256
                ),
            ),
            publication=publication,
        )
    except (KeyError, OSError, ValueError) as exc:
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic terminal exact-set verification rejected"
        ) from exc
    if (
        type(verified) is not dict
        or set(verified) != _TERMINAL_VERIFICATION_FIELDS
        or verified.get("verified") is not True
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic terminal exact-set verification rejected"
        )
    sessions = _validate_sessions(verified.get("trade_dates"), expected_count=733)
    if (
        verified.get("trade_date_count") != 733
        or [item.get("trade_date") for item in refs] != sessions
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic terminal exact-set verification rejected"
        )
    _require_sha256(verified.get("authority_root_sha256"), label="authority root")
    _require_sha256(verified.get("receipt_sha256"), label="receipt")
    return dict(verified)


def _assert_terminal_verification(
    *,
    publication: Any,
    receipt: Any,
    verified: Any,
) -> None:
    if (
        type(publication) is not dict
        or set(publication) != _TERMINAL_PUBLICATION_FIELDS
        or publication.get("schema")
        != "factor-v3-daily-basic-733-exact-set-publication/v2"
        or type(receipt) is not dict
        or set(receipt) != _TERMINAL_RECEIPT_FIELDS
        or receipt.get("verified") is not True
        or type(verified) is not dict
        or set(verified) != _TERMINAL_VERIFICATION_FIELDS
        or verified.get("verified") is not True
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic terminal receipt rejected"
        )
    for field in (
        "attestation_sha256",
        "publication_sha256",
        "receipt_sha256",
    ):
        _require_sha256(publication.get(field), label=field)
    _require_sha256(receipt.get("authority_root_sha256"), label="authority root")
    _require_sha256(receipt.get("receipt_sha256"), label="receipt")
    if (
        receipt["authority_root_sha256"] != verified.get("authority_root_sha256")
        or receipt["receipt_sha256"] != verified.get("receipt_sha256")
        or receipt["receipt_sha256"] != publication.get("receipt_sha256")
        or receipt["receipt_relative_path"]
        != publication.get("receipt_relative_path")
    ):
        raise FactorV3DailyBasicRunnerError(
            "factor-v3 daily-basic terminal receipt rejected"
        )


def _result(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "completed_session_count": state["completed_session_count"],
        "receipt": state["receipt"],
        "run_spec_sha256": state["run_spec_sha256"],
        "status": state["status"],
    }


def _mark_verification_failed(
    *,
    paths: Mapping[str, Path],
    spec: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    failed = _state_payload(
        run_spec_sha256=spec["run_spec_sha256"],
        status="failed",
        completed_session_count=state["completed_session_count"],
        credential_generation_id=state["credential_generation_id"],
        collection_set_refs=state["collection_set_refs"],
        exact_set_publication=state["exact_set_publication"],
        receipt=state["receipt"],
    )
    _atomic_json(paths["state"], failed)


def _run_factor_v3_daily_basic_collection_with_route_credential(
    *, run_spec_path: str | Path, run_root: str | Path, credential: str, source_generation_id: str,
    daily_basic_policy_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Private sealed entrypoint invoked only by the daily-basic credential slot."""

    if _validated_policy(daily_basic_policy_descriptor) != FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR:
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection policy rejected")
    if type(credential) is not str or not credential or any(ord(char) < 32 for char in credential):
        raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic Jiaoch source rejected")
    _require_uuid4(source_generation_id, label="credential generation")
    spec = load_factor_v3_daily_basic_run_spec(run_spec_path)
    paths = _paths(run_root, create=True)
    sessions = _validate_sessions(spec["sessions"], expected_count=733)
    with _run_lock(paths["lock"]):
        state = _load_or_initialize(paths, spec, allow_initialize=True)
        if state["status"] == "verified":
            try:
                points_root = _safe_directory(
                    paths["points"], label="points root", create=False
                )
                _assert_complete_points_output(
                    points_root, state["collection_set_refs"], sessions
                )
                exact_verified = _verify_exact_set_coverage(
                    authority_root=_safe_directory(
                        paths["authority"], label="authority root", create=False
                    ),
                    points_root=points_root,
                    refs=state["collection_set_refs"],
                    inputs=spec["exact_set_authority_inputs"],
                    publication=state["exact_set_publication"],
                    expected_source_authority_root_sha256=spec[
                        "source_authority_root_sha256"
                    ],
                )
                _assert_terminal_verification(
                    publication=state["exact_set_publication"],
                    receipt=state["receipt"],
                    verified=exact_verified,
                )
                return _result(state)
            except BaseException:
                _mark_verification_failed(paths=paths, spec=spec, state=state)
                raise
        persisted = state["credential_generation_id"]
        if persisted is not None and persisted != source_generation_id:
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic credential generation drifted")
        active_generation = source_generation_id if persisted is None else persisted
        points_root = _safe_directory(paths["points"], label="points root", create=True)
        refs = [dict(item) for item in state["collection_set_refs"]]
        try:
            for index, session in enumerate(sessions):
                if index < len(refs):
                    _verify_one_daily_basic(points_output_root=points_root, ref=refs[index])
                    continue
                _atomic_json(paths["state"], _state_payload(
                    run_spec_sha256=spec["run_spec_sha256"], status="collecting", completed_session_count=len(refs),
                    credential_generation_id=active_generation, collection_set_refs=refs
                ))
                result = _collect_one_daily_basic(
                    credential=credential, generation_id=active_generation,
                    policy_sha256=FACTOR_V3_DAILY_BASIC_COLLECTION_POLICY_DESCRIPTOR["sha256"],
                    output_root=points_root, trade_date=session,
                    timeout_seconds=spec["collector"]["timeout_seconds"], max_attempts=spec["collector"]["max_attempts"],
                )
                ref = _verify_one_daily_basic(points_output_root=points_root, ref=result)
                if ref["trade_date"] != session:
                    raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic collection session drifted")
                refs.append(ref)
                _atomic_json(paths["state"], _state_payload(
                    run_spec_sha256=spec["run_spec_sha256"], status="collecting", completed_session_count=len(refs),
                    credential_generation_id=active_generation, collection_set_refs=refs
                ))
            _assert_complete_points_output(points_root, refs, sessions)
            _atomic_json(paths["state"], _state_payload(
                run_spec_sha256=spec["run_spec_sha256"], status="collected", completed_session_count=733,
                credential_generation_id=active_generation, collection_set_refs=refs
            ))
            publication = _publish_exact_set_coverage(
                authority_root=_safe_directory(paths["authority"], label="authority root", create=True),
                points_root=points_root, refs=refs, inputs=spec["exact_set_authority_inputs"],
                expected_source_authority_root_sha256=spec[
                    "source_authority_root_sha256"
                ],
            )
            exact_verified = _verify_exact_set_coverage(
                authority_root=paths["authority"],
                points_root=points_root,
                refs=refs,
                inputs=spec["exact_set_authority_inputs"],
                publication=publication,
                expected_source_authority_root_sha256=spec[
                    "source_authority_root_sha256"
                ],
            )
            receipt = {
                "authority_root_sha256": exact_verified["authority_root_sha256"],
                "receipt_relative_path": publication["receipt_relative_path"],
                "receipt_sha256": exact_verified["receipt_sha256"],
                "verified": True,
            }
            _assert_terminal_verification(
                publication=publication,
                receipt=receipt,
                verified=exact_verified,
            )
            verified = _state_payload(
                run_spec_sha256=spec["run_spec_sha256"], status="verified", completed_session_count=733,
                credential_generation_id=active_generation, collection_set_refs=refs,
                exact_set_publication=publication, receipt=receipt,
            )
            _atomic_json(paths["state"], verified)
            return _result(verified)
        except BaseException as exc:
            _persist_collection_failure_diagnostic(paths=paths, error=exc)
            previous = _validated_state(_read_json(paths["state"], label="run state", max_bytes=_MAX_STATE_BYTES), spec=spec)
            _atomic_json(paths["state"], _state_payload(
                run_spec_sha256=spec["run_spec_sha256"], status="failed", completed_session_count=previous["completed_session_count"],
                credential_generation_id=previous["credential_generation_id"], collection_set_refs=previous["collection_set_refs"],
                exact_set_publication=None, receipt=None,
            ))
            raise


def run_factor_v3_daily_basic_collection(*, run_spec_path: str | Path, run_root: str | Path) -> dict[str, Any]:
    from app.jiaoch_credential_slots import _collect_jiaoch_factor_v3_daily_basic_from_environment_for_run

    return _collect_jiaoch_factor_v3_daily_basic_from_environment_for_run(
        run_spec_path=run_spec_path, run_root=run_root,
        source_generation_id=_run_credential_generation_id(run_spec_path=run_spec_path, run_root=run_root),
    )


def verify_factor_v3_daily_basic_run(*, run_spec_path: str | Path, run_root: str | Path) -> dict[str, Any]:
    spec = load_factor_v3_daily_basic_run_spec(run_spec_path)
    paths = _paths(run_root, create=False)
    sessions = _validate_sessions(spec["sessions"], expected_count=733)
    with _run_lock(paths["lock"]):
        state = _load_or_initialize(paths, spec, allow_initialize=False)
        if state["status"] != "verified" or state["receipt"] is None:
            raise FactorV3DailyBasicRunnerError("factor-v3 daily-basic run is not verified")
        try:
            points_root = _safe_directory(
                paths["points"], label="points root", create=False
            )
            _assert_complete_points_output(
                points_root, state["collection_set_refs"], sessions
            )
            exact_verified = _verify_exact_set_coverage(
                authority_root=_safe_directory(
                    paths["authority"], label="authority root", create=False
                ),
                points_root=points_root,
                refs=state["collection_set_refs"],
                inputs=spec["exact_set_authority_inputs"],
                publication=state["exact_set_publication"],
                expected_source_authority_root_sha256=spec[
                    "source_authority_root_sha256"
                ],
            )
            _assert_terminal_verification(
                publication=state["exact_set_publication"],
                receipt=state["receipt"],
                verified=exact_verified,
            )
            return _result(state)
        except BaseException:
            _mark_verification_failed(paths=paths, spec=spec, state=state)
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factor-v3-daily-basic-runner")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--run-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = run_factor_v3_daily_basic_collection(run_spec_path=args.run_spec, run_root=args.run_root) if args.command == "run" else verify_factor_v3_daily_basic_run(run_spec_path=args.run_spec, run_root=args.run_root)
    except (FactorV3DailyBasicRunnerError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
