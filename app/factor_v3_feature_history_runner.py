"""Resumable, feature-history-only factor-v3 PIT collection runner.

The runner deliberately owns orchestration only.  Collection authorization,
candidate publication, and authority verification remain in the frozen
feature-history authority module.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any
from urllib.parse import urlparse
import uuid

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app.audited_pit_factor_v3_points_contract import canonical_sha256
from app.durable_io import fsync_directory, fsync_file
from app.research_pit_collector import (
    ControlledTushareCollector,
    SystemTrustedClock,
    UrllibTushareTransport,
    build_bak_basic_specs,
)
from app.research_pit_store import PITReceiptStore

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None

try:
    import msvcrt as _msvcrt
except ImportError:
    _msvcrt = None


__all__ = (
    "FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR",
    "build_factor_v3_feature_history_run_spec",
    "load_factor_v3_feature_history_run_spec",
    "run_factor_v3_feature_history_collection",
    "verify_factor_v3_feature_history_run",
    "main",
)


_RUN_SPEC_SCHEMA = "factor-v3-feature-history-run-spec/v1"
_RUN_STATE_SCHEMA = "factor-v3-feature-history-run-state/v2"
_RUN_STATE_V1_SCHEMA = "factor-v3-feature-history-run-state/v1"
_POLICY_SCHEMA = "jiaoch-credential-feature-history-policy-descriptor/v1"
_POLICY_DOCUMENT_SCHEMA = "jiaoch-credential-feature-history-routing-policy/v1"
_MARKET_SESSION_VINTAGE = "historical_backfill"
_PUBLICATION_FIELDS = frozenset(
    {
        "authority_manifest_created",
        "authority_manifest_relative_path",
        "authority_manifest_sha256",
        "publication_capability",
        "publication_status",
        "schema",
    }
)
_RUN_SPEC_FIELDS = frozenset(
    {
        "collection_plan",
        "collection_policy_descriptor",
        "collector",
        "development_session_refs",
        "run_spec_sha256",
        "schema",
        "temporal_partition_contract",
        "trade_cal_output_root",
        "trade_cal_publication",
    }
)
_COLLECTOR_FIELDS = frozenset({"max_attempts", "timeout_seconds", "workers"})
_STATE_FIELDS = frozenset(
    {
        "collection_publication",
        "completed_session_count",
        "credential_generation_id",
        "receipt",
        "run_spec_sha256",
        "schema",
        "status",
        "state_sha256",
    }
)
_STATE_V1_FIELDS = _STATE_FIELDS - {"credential_generation_id"}
_ALLOWED_DATASETS = ("bak_basic", "daily", "adj_factor", "stk_limit", "suspend_d")
_MAX_RUN_SPEC_BYTES = 4 * 1024 * 1024
_MAX_STATE_BYTES = 512 * 1024
_RESULT_FIELDS = frozenset(
    {
        "collection_publication",
        "completed_session_count",
        "receipt",
        "run_spec_sha256",
        "status",
    }
)
_PUBLICATION_RESULT_FIELDS = (
    "authority_manifest_created",
    "authority_manifest_relative_path",
    "authority_manifest_sha256",
    "publication_status",
    "schema",
)


_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT = {
    "routes": [
        {
            "api_name": api_name,
            "credential_slot_id": "points-primary",
            "purpose": "factor-v3-feature-history",
            "route_id": f"feature-history:points-primary:{api_name}",
        }
        for api_name in _ALLOWED_DATASETS
    ],
    "schema": _POLICY_DOCUMENT_SCHEMA,
}
FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR = {
    "credential_proof_claimed": False,
    "document": _FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT,
    "schema": _POLICY_SCHEMA,
    "sha256": canonical_sha256(_FEATURE_HISTORY_ROUTE_POLICY_DOCUMENT),
}


class FactorV3FeatureHistoryRunnerError(ValueError):
    """Raised when a feature-history run cannot safely proceed."""


@dataclass(frozen=True, slots=True)
class _FrozenJiaochSource:
    token: str = field(repr=False)
    generation_id: str
    name: str = "jiaoch"
    api_url: str = "http://jiaoch.site"
    allowed_hosts: tuple[str, ...] = ("jiaoch.site",)
    request_protocol: str = "tushare-path-per-interface/v1"
    row_cap_overrides: tuple[tuple[str, int], ...] = (("stk_limit", 10_000),)
    network_route: str = "direct"
    proxy_url: None = None


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
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history JSON rejected") from exc


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_json(raw: bytes, *, label: str) -> dict[str, Any]:
    if not raw or len(raw) > _MAX_RUN_SPEC_BYTES:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")

    def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise FactorV3FeatureHistoryRunnerError(
                    f"factor-v3 feature-history {label} rejected"
                )
            result[key] = value
        return result

    def _constant(_value: str) -> None:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} rejected"
        )

    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} rejected"
        ) from exc
    if type(value) is not dict or _canonical_bytes(value) != raw.rstrip(b"\n"):
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    return value


def _require_regular_file(path: Path, *, label: str, max_bytes: int) -> Path:
    try:
        stat_result = path.stat()
    except OSError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} unavailable"
        ) from exc
    if path.is_symlink() or not stat.S_ISREG(stat_result.st_mode) or stat_result.st_size > max_bytes:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    return path


def _read_json_file(path: str | Path, *, label: str, max_bytes: int) -> dict[str, Any]:
    source = _require_regular_file(Path(path), label=label, max_bytes=max_bytes)
    try:
        return _strict_json(source.read_bytes(), label=label)
    except OSError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} unavailable"
        ) from exc


def _require_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or len(value) != 64:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    try:
        int(value, 16)
    except ValueError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} rejected"
        ) from exc
    if value.lower() != value:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    return value


def _validated_uuid4(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    try:
        parsed = uuid.UUID(value)
    except ValueError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            f"factor-v3 feature-history {label} rejected"
        ) from exc
    if parsed.version != 4 or str(parsed) != value:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    return value


def _reject_secret_shape(
    value: Any, *, reject_publication_capability: bool = False
) -> None:
    if type(value) is dict:
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in {"token", "credential", "password", "api_key", "secret"} or (
                reject_publication_capability
                and normalized == "publication_capability"
            ):
                raise FactorV3FeatureHistoryRunnerError(
                    "factor-v3 feature-history run spec must not contain credentials"
                )
            _reject_secret_shape(
                nested, reject_publication_capability=reject_publication_capability
            )
    elif type(value) in {list, tuple}:
        for nested in value:
                _reject_secret_shape(
                    nested, reject_publication_capability=reject_publication_capability
                )


def _validated_collector(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _COLLECTOR_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collector policy rejected")
    max_attempts = value.get("max_attempts")
    workers = value.get("workers")
    timeout = value.get("timeout_seconds")
    if (
        isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or max_attempts <= 0
        or max_attempts > 10
        or isinstance(workers, bool)
        or not isinstance(workers, int)
        or workers != 1
        or isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0.1 <= float(timeout) <= 300.0
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collector policy rejected")
    return {
        "max_attempts": max_attempts,
        "timeout_seconds": float(timeout),
        "workers": workers,
    }


def _validated_policy_descriptor(value: Any) -> dict[str, Any]:
    if type(value) is not dict or value != FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection policy rejected")
    return json.loads(_canonical_bytes(value))


def _validated_segments(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    if type(plan) is not dict:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection plan rejected")
    prewindow = plan.get("prewindow")
    segments = plan.get("segments")
    if (
        type(prewindow) is not dict
        or type(prewindow.get("sessions")) is not list
        or type(segments) is not list
        or len(segments) != 2
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection plan rejected")
    expected_roles = ("development", "contaminated_diagnostic")
    flattened: list[str] = []
    validated: list[dict[str, Any]] = []
    for segment, role in zip(segments, expected_roles):
        if type(segment) is not dict or segment.get("temporal_role") != role:
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection plan rejected")
        sessions = segment.get("sessions")
        if (
            type(sessions) is not list
            or not sessions
            or any(type(session) is not str for session in sessions)
            or sessions != sorted(sessions)
            or len(set(sessions)) != len(sessions)
            or segment.get("count") != len(sessions)
            or segment.get("start") != sessions[0]
            or segment.get("end") != sessions[-1]
            or segment.get("sessions_sha256") != _canonical_sha256(sessions)
        ):
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection plan rejected")
        flattened.extend(sessions)
        validated.append({"temporal_role": role, "sessions": list(sessions)})
    if flattened != prewindow["sessions"] or len(flattened) != 250:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history requires exact 250 sessions")
    return validated


def _validated_run_spec(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RUN_SPEC_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run spec rejected")
    if value.get("schema") != _RUN_SPEC_SCHEMA:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run spec rejected")
    expected_sha256 = _require_sha256(value.get("run_spec_sha256"), label="run spec hash")
    unsigned = {key: item for key, item in value.items() if key != "run_spec_sha256"}
    if _canonical_sha256(unsigned) != expected_sha256:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run spec rejected")
    _reject_secret_shape(unsigned)
    if type(value.get("trade_cal_output_root")) is not str or not Path(
        value["trade_cal_output_root"]
    ).is_absolute():
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history trade calendar root rejected")
    if type(value.get("trade_cal_publication")) is not dict:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history trade calendar publication rejected")
    if type(value.get("development_session_refs")) is not list:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history development refs rejected")
    if type(value.get("temporal_partition_contract")) is not dict:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history partition contract rejected")
    collector = _validated_collector(value.get("collector"))
    policy = _validated_policy_descriptor(value.get("collection_policy_descriptor"))
    plan = json.loads(_canonical_bytes(value["collection_plan"]))
    _validated_segments(plan)
    return {
        **value,
        "collection_plan": plan,
        "collection_policy_descriptor": policy,
        "collector": collector,
    }


def build_factor_v3_feature_history_run_spec(
    *,
    collection_plan: Mapping[str, Any],
    trade_cal_output_root: str | Path,
    trade_cal_publication: Mapping[str, Any],
    development_session_refs: Sequence[Mapping[str, Any]],
    temporal_partition_contract: Mapping[str, Any],
    timeout_seconds: float,
    max_attempts: int,
    workers: int,
) -> dict[str, Any]:
    """Build a self-addressed offline execution specification.

    This helper does not access credentials, create a store, or make requests.
    """

    collector = _validated_collector(
        {
            "timeout_seconds": timeout_seconds,
            "max_attempts": max_attempts,
            "workers": workers,
        }
    )
    unsigned = {
        "schema": _RUN_SPEC_SCHEMA,
        "collection_plan": json.loads(_canonical_bytes(dict(collection_plan))),
        "trade_cal_output_root": str(Path(trade_cal_output_root)),
        "trade_cal_publication": json.loads(_canonical_bytes(dict(trade_cal_publication))),
        "development_session_refs": json.loads(
            _canonical_bytes([dict(item) for item in development_session_refs])
        ),
        "temporal_partition_contract": json.loads(
            _canonical_bytes(dict(temporal_partition_contract))
        ),
        "collection_policy_descriptor": json.loads(
            _canonical_bytes(FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR)
        ),
        "collector": collector,
    }
    return _validated_run_spec({**unsigned, "run_spec_sha256": _canonical_sha256(unsigned)})


def load_factor_v3_feature_history_run_spec(path: str | Path) -> dict[str, Any]:
    """Load the canonical, content-addressed run specification."""

    return _validated_run_spec(_read_json_file(path, label="run spec", max_bytes=_MAX_RUN_SPEC_BYTES))


def _safe_directory(path: Path, *, label: str, create: bool = False) -> Path:
    if path.is_symlink():
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    if not path.exists():
        if not create:
            raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} unavailable")
        parent = path.parent
        if not parent.exists() or parent.is_symlink() or not parent.is_dir():
            raise FactorV3FeatureHistoryRunnerError(
                f"factor-v3 feature-history {label} parent rejected"
            )
        try:
            path.mkdir()
            fsync_directory(parent)
        except OSError as exc:
            raise FactorV3FeatureHistoryRunnerError(
                f"factor-v3 feature-history {label} unavailable"
            ) from exc
    if not path.is_dir():
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} rejected")
    return path.resolve(strict=True)


def _run_paths(run_root: str | Path, *, create: bool) -> dict[str, Path]:
    candidate = Path(run_root)
    if not candidate.is_absolute():
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run root must be absolute")
    root = _safe_directory(candidate, label="run root", create=create)
    return {
        "root": root,
        "lock": root / ".factor-v3-feature-history-runner.lock",
        "run_spec": root / "run-spec.json",
        "state": root / "state.json",
        "store": root / "pit-store",
        "publication_root": root / "collection-publication",
    }


def _create_only_bytes(path: Path, content: bytes, *, label: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except FileExistsError as exc:
        raise FactorV3FeatureHistoryRunnerError(f"factor-v3 feature-history {label} exists") from exc
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    fsync_directory(path.parent)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    content = _canonical_bytes(dict(value)) + b"\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state temporary exists")
    try:
        _create_only_bytes(temporary, content, label="state temporary")
        os.replace(temporary, path)
        fsync_file(path)
        fsync_directory(path.parent)
    finally:
        if temporary.exists():
            temporary.unlink()


def _state_payload(
    *,
    run_spec_sha256: str,
    status: str,
    completed_session_count: int,
    credential_generation_id: str | None = None,
    collection_publication: Mapping[str, Any] | None = None,
    receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if status not in {"initialized", "collecting", "collected", "published", "verified", "failed"}:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if (
        isinstance(completed_session_count, bool)
        or not isinstance(completed_session_count, int)
        or not 0 <= completed_session_count <= 250
        or (credential_generation_id is not None and type(credential_generation_id) is not str)
        or (collection_publication is not None and type(collection_publication) is not dict)
        or (receipt is not None and type(receipt) is not dict)
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if status == "published" and collection_publication is None:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if status == "verified" and (collection_publication is None or receipt is None):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if status == "initialized" and completed_session_count != 0:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if credential_generation_id is not None:
        _validated_uuid4(
            credential_generation_id,
            label="credential generation",
        )
    if status in {"collecting", "collected", "published", "verified"} and credential_generation_id is None:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if (
        status == "failed"
        and (completed_session_count != 0 or collection_publication is not None)
        and credential_generation_id is None
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if status in {"collected", "published", "verified"} and completed_session_count != 250:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    unsigned = {
        "schema": _RUN_STATE_SCHEMA,
        "run_spec_sha256": run_spec_sha256,
        "status": status,
        "completed_session_count": completed_session_count,
        "credential_generation_id": credential_generation_id,
        "collection_publication": (
            None if collection_publication is None else json.loads(_canonical_bytes(dict(collection_publication)))
        ),
        "receipt": None if receipt is None else json.loads(_canonical_bytes(dict(receipt))),
    }
    return {**unsigned, "state_sha256": _canonical_sha256(unsigned)}


def _validated_state(value: Any, *, run_spec_sha256: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _STATE_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    if value.get("schema") != _RUN_STATE_SCHEMA or value.get("run_spec_sha256") != run_spec_sha256:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    unsigned = {key: item for key, item in value.items() if key != "state_sha256"}
    if value.get("state_sha256") != _canonical_sha256(unsigned):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history state rejected")
    return _state_payload(
        run_spec_sha256=run_spec_sha256,
        status=value["status"],
        completed_session_count=value["completed_session_count"],
        credential_generation_id=value["credential_generation_id"],
        collection_publication=value["collection_publication"],
        receipt=value["receipt"],
    )


def _validated_v1_state(value: Any, *, run_spec_sha256: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _STATE_V1_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history legacy state rejected")
    if value.get("schema") != _RUN_STATE_V1_SCHEMA or value.get("run_spec_sha256") != run_spec_sha256:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history legacy state rejected")
    unsigned = {key: item for key, item in value.items() if key != "state_sha256"}
    if value.get("state_sha256") != _canonical_sha256(unsigned):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history legacy state rejected")
    status = value.get("status")
    completed = value.get("completed_session_count")
    publication = value.get("collection_publication")
    receipt = value.get("receipt")
    if (
        status not in {"initialized", "collecting", "collected", "published", "verified", "failed"}
        or isinstance(completed, bool)
        or not isinstance(completed, int)
        or not 0 <= completed <= 250
        or (publication is not None and type(publication) is not dict)
        or (receipt is not None and type(receipt) is not dict)
        or (status == "initialized" and completed != 0)
        or (status == "published" and publication is None)
        or (status == "verified" and (publication is None or receipt is None))
        or (status in {"collected", "published", "verified"} and completed != 250)
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history legacy state rejected")
    return {
        "status": status,
        "completed_session_count": completed,
        "collection_publication": publication,
        "receipt": receipt,
    }


def _legacy_store_generation_id(store_root: Path, *, required: bool) -> str | None:
    database_path = store_root / "metadata.sqlite3"
    if not database_path.exists():
        if required:
            raise FactorV3FeatureHistoryRunnerError(
                "factor-v3 feature-history legacy state cannot migrate"
            )
        return None
    _require_regular_file(
        database_path,
        label="legacy PIT database",
        max_bytes=2**63 - 1,
    )
    try:
        connection = sqlite3.connect(str(database_path), timeout=30)
        try:
            connection.execute("PRAGMA query_only=ON")
            rows = connection.execute(
                "SELECT request_semantics_json FROM fetch_attempts"
            ).fetchall()
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history legacy state cannot migrate"
        ) from exc
    generation_ids: set[str] = set()
    try:
        for (raw_semantics,) in rows:
            semantics = json.loads(raw_semantics)
            if type(semantics) is not dict:
                raise ValueError
            generation_ids.add(
                _validated_uuid4(
                    semantics.get("credential_generation_id"),
                    label="legacy credential generation",
                )
            )
    except (TypeError, ValueError, FactorV3FeatureHistoryRunnerError) as exc:
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history legacy state cannot migrate"
        ) from exc
    if len(generation_ids) == 1:
        return next(iter(generation_ids))
    if not generation_ids and not required:
        return None
    raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history legacy state cannot migrate")


def _migrate_v1_state(
    value: Any,
    *,
    run_spec_sha256: str,
    store_root: Path,
) -> dict[str, Any]:
    legacy = _validated_v1_state(value, run_spec_sha256=run_spec_sha256)
    needs_generation = (
        legacy["completed_session_count"] != 0
        or legacy["collection_publication"] is not None
        or legacy["receipt"] is not None
    )
    generation_id = _legacy_store_generation_id(store_root, required=needs_generation)
    status = legacy["status"]
    if status == "collecting" and generation_id is None:
        status = "failed"
    return _state_payload(
        run_spec_sha256=run_spec_sha256,
        status=status,
        completed_session_count=legacy["completed_session_count"],
        credential_generation_id=generation_id,
        collection_publication=legacy["collection_publication"],
        receipt=legacy["receipt"],
    )


def _load_or_initialize_run(
    paths: Mapping[str, Path], spec: Mapping[str, Any], *, allow_initialize: bool
) -> dict[str, Any]:
    spec_path = paths["run_spec"]
    state_path = paths["state"]
    canonical_spec = _canonical_bytes(spec) + b"\n"
    if not spec_path.exists():
        if not allow_initialize:
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run is unavailable")
        if state_path.exists() or paths["store"].exists() or paths["publication_root"].exists():
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run root is incomplete")
        _create_only_bytes(spec_path, canonical_spec, label="run spec snapshot")
        initial = _state_payload(
            run_spec_sha256=spec["run_spec_sha256"],
            status="initialized",
            completed_session_count=0,
        )
        _atomic_json(state_path, initial)
        return initial
    persisted_spec = _read_json_file(spec_path, label="run spec snapshot", max_bytes=_MAX_RUN_SPEC_BYTES)
    if persisted_spec != spec or _canonical_bytes(persisted_spec) + b"\n" != canonical_spec:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run spec drifted")
    persisted_state = _read_json_file(state_path, label="run state", max_bytes=_MAX_STATE_BYTES)
    if persisted_state.get("schema") == _RUN_STATE_V1_SCHEMA:
        migrated = _migrate_v1_state(
            persisted_state,
            run_spec_sha256=spec["run_spec_sha256"],
            store_root=paths["store"],
        )
        _atomic_json(state_path, migrated)
        return migrated
    return _validated_state(persisted_state, run_spec_sha256=spec["run_spec_sha256"])


def _run_credential_generation_id(
    *, run_spec_path: str | Path, run_root: str | Path
) -> str:
    """Return the run-scoped generation ID before resolving a credential slot.

    A restart may resolve the same points slot again, but its request lineage
    must retain the generation already sealed in the run state.
    """

    spec = load_factor_v3_feature_history_run_spec(run_spec_path)
    _verify_plan(spec)
    paths = _run_paths(run_root, create=True)
    with _run_lock(paths["lock"]):
        state = _load_or_initialize_run(paths, spec, allow_initialize=True)
        generation_id = state["credential_generation_id"]
        if generation_id is not None:
            return _validated_uuid4(generation_id, label="credential generation")
        if (
            state["completed_session_count"] != 0
            or state["collection_publication"] is not None
            or state["receipt"] is not None
        ):
            raise FactorV3FeatureHistoryRunnerError(
                "factor-v3 feature-history credential generation is unavailable"
            )
        generation_id = str(uuid.uuid4())
        _atomic_json(
            paths["state"],
            _state_payload(
                run_spec_sha256=spec["run_spec_sha256"],
                status=state["status"],
                completed_session_count=0,
                credential_generation_id=generation_id,
                collection_publication=state["collection_publication"],
                receipt=state["receipt"],
            ),
        )
        return generation_id


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
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history lock unavailable"
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
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history lock unavailable"
        ) from exc
    try:
        descriptor = os.open(str(path), flags, 0o600)
    except OSError as exc:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history lock unavailable") from exc
    try:
        _validate_open_lock_identity(path, descriptor)
    except FactorV3FeatureHistoryRunnerError:
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
            raise FactorV3FeatureHistoryRunnerError(
                "factor-v3 feature-history advisory locking is unavailable"
            )
        _validate_open_lock_identity(path, descriptor)
    except (OSError, FactorV3FeatureHistoryRunnerError) as exc:
        os.close(descriptor)
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run is already locked") from exc
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
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history lock cleanup failed") from exc
        finally:
            os.close(descriptor)


def _verify_plan(spec: Mapping[str, Any]) -> None:
    history_authority.verify_factor_v3_feature_history_collection_plan(
        collection_plan=spec["collection_plan"],
        trade_cal_output_root=spec["trade_cal_output_root"],
        trade_cal_publication=spec["trade_cal_publication"],
        development_session_refs=spec["development_session_refs"],
        temporal_partition_contract=spec["temporal_partition_contract"],
    )


def _collector_for_segment(
    *,
    store: PITReceiptStore,
    spec: Mapping[str, Any],
    source: Any,
    temporal_role: str,
    sessions: Sequence[str],
) -> ControlledTushareCollector:
    collector = spec["collector"]
    return ControlledTushareCollector(
        store=store,
        token=source.token,
        api_url=source.api_url,
        transport=UrllibTushareTransport(proxy_url=source.proxy_url),
        clock=SystemTrustedClock(),
        max_attempts=collector["max_attempts"],
        timeout_s=collector["timeout_seconds"],
        allowed_hosts=source.allowed_hosts,
        source_profile=source.name,
        request_protocol=source.request_protocol,
        row_cap_overrides=dict(source.row_cap_overrides),
        network_route=source.network_route,
        proxy_endpoint=source.proxy_url,
        credential_slot_id="points-primary",
        credential_route_purpose="factor-v3-feature-history",
        credential_route_id_prefix="feature-history",
        credential_generation_id=source.generation_id,
        temporal_contract=spec["temporal_partition_contract"],
        temporal_role=temporal_role,
        temporal_contract_sha256=spec["temporal_partition_contract"]["contract_sha256"],
        temporal_start_date=sessions[0],
        temporal_end_date=sessions[-1],
        workers=collector["workers"],
    )


def _fixed_jiaoch_source(*, credential: Any, source_generation_id: Any) -> _FrozenJiaochSource:
    if (
        type(credential) is not str
        or not credential
        or len(credential) > 4096
        or credential.strip() != credential
        or any(ord(character) < 32 or ord(character) == 127 for character in credential)
        or type(source_generation_id) is not str
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history Jiaoch source rejected")
    _validated_uuid4(source_generation_id, label="Jiaoch source")
    return _FrozenJiaochSource(token=credential, generation_id=source_generation_id)


def _validated_frozen_jiaoch_source(source: Any) -> Any:
    """Reject source substitutions before a collector can issue a request."""

    if type(source) is not _FrozenJiaochSource:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history Jiaoch source rejected")
    try:
        frozen = _fixed_jiaoch_source(
            credential=source.token,
            source_generation_id=source.generation_id,
        )
        name = source.name
        api_url = source.api_url
        allowed_hosts = source.allowed_hosts
        request_protocol = source.request_protocol
        row_cap_overrides = source.row_cap_overrides
        network_route = source.network_route
        proxy_url = source.proxy_url
        parsed = urlparse(api_url)
        port = parsed.port
    except (AttributeError, TypeError, ValueError) as exc:
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history Jiaoch source rejected"
        ) from exc
    if (
        source != frozen
        or name != "jiaoch"
        or parsed.scheme != "http"
        or parsed.hostname != "jiaoch.site"
        or port not in {None, 80}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.params
        or parsed.query
        or parsed.fragment
        or allowed_hosts != ("jiaoch.site",)
        or request_protocol != "tushare-path-per-interface/v1"
        or row_cap_overrides != (("stk_limit", 10_000),)
        or network_route != "direct"
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history Jiaoch source rejected")
    if proxy_url is not None:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history Jiaoch source rejected")
    return source


def _store_sidecars(database_path: Path) -> tuple[Path, Path]:
    return (
        database_path.with_name(database_path.name + "-wal"),
        database_path.with_name(database_path.name + "-shm"),
    )


def _assert_no_partial_store_artifacts(store_root: Path, sessions: Sequence[str]) -> None:
    database_path = store_root / "metadata.sqlite3"
    _require_regular_file(database_path, label="PIT database", max_bytes=2**63 - 1)
    if any(path.exists() for path in _store_sidecars(database_path)):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history PIT store has SQLite sidecars")
    for path in store_root.rglob("*"):
        if path.is_symlink() or path.name.endswith((".partial", ".tmp")):
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history PIT store has partial artifacts")
    try:
        connection = sqlite3.connect(
            f"{database_path.resolve(strict=True).as_uri()}?mode=ro&immutable=1",
            uri=True,
        )
        try:
            datasets = {
                str(row[0])
                for row in connection.execute("SELECT DISTINCT dataset FROM fetch_attempts")
            }
            if datasets != set(_ALLOWED_DATASETS):
                raise FactorV3FeatureHistoryRunnerError(
                    "factor-v3 feature-history store contains forbidden interfaces"
                )
            for dataset in _ALLOWED_DATASETS:
                partitions = {
                    str(row[0])
                    for row in connection.execute(
                        "SELECT DISTINCT partition_key FROM fetch_attempts WHERE dataset = ?",
                        (dataset,),
                    )
                }
                if partitions != set(sessions):
                    raise FactorV3FeatureHistoryRunnerError(
                        "factor-v3 feature-history store session coverage rejected"
                    )
            for table in ("market_session_generations", "membership_session_generations"):
                rows = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE status != 'published'"
                ).fetchone()
                if rows is None or int(rows[0]) != 0:
                    raise FactorV3FeatureHistoryRunnerError(
                        "factor-v3 feature-history store has partial generations"
                    )
            stock_count = connection.execute("SELECT COUNT(*) FROM stock_basic_generations").fetchone()
            if stock_count is None or int(stock_count[0]) != 0:
                raise FactorV3FeatureHistoryRunnerError(
                    "factor-v3 feature-history store contains stock_basic generation"
                )
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history PIT store rejected") from exc


def _finalize_store(store_root: Path, sessions: Sequence[str]) -> None:
    database_path = store_root / "metadata.sqlite3"
    _require_regular_file(database_path, label="PIT database", max_bytes=2**63 - 1)
    try:
        connection = sqlite3.connect(str(database_path), timeout=30)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            journal = connection.execute("PRAGMA journal_mode=DELETE").fetchone()
            if journal is None or str(journal[0]).lower() != "delete":
                raise FactorV3FeatureHistoryRunnerError(
                    "factor-v3 feature-history PIT store finalization rejected"
                )
            connection.execute("PRAGMA synchronous=FULL")
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise FactorV3FeatureHistoryRunnerError(
                    "factor-v3 feature-history PIT store integrity rejected"
                )
        finally:
            connection.close()
    except sqlite3.DatabaseError as exc:
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history PIT store finalization rejected"
        ) from exc
    fsync_file(database_path)
    fsync_directory(store_root)
    _assert_no_partial_store_artifacts(store_root, sessions)


def _validated_publication(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _PUBLICATION_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history publication rejected")
    return json.loads(_canonical_bytes(value))


def _publish_collection_candidate(
    *,
    publication_root: Path,
    store_root: Path,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    """Delegate all manifest derivation to the capability-bound authority."""

    return _validated_publication(
        history_authority._publish_factor_v3_feature_history_collection_candidate(
            collection_plan=spec["collection_plan"],
            development_session_refs=spec["development_session_refs"],
            feature_history_route_policy_descriptor=spec[
                "collection_policy_descriptor"
            ],
            pit_store_root=store_root,
            publication_output_root=publication_root,
            temporal_partition_contract=spec["temporal_partition_contract"],
            trade_cal_output_root=spec["trade_cal_output_root"],
            trade_cal_publication=spec["trade_cal_publication"],
        )
    )


def _verify_collection_authority(
    *,
    publication_root: Path,
    publication: Mapping[str, Any],
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    receipt = history_authority.verify_factor_v3_feature_history_collection_authority(
        collection_plan=spec["collection_plan"],
        trade_cal_output_root=spec["trade_cal_output_root"],
        trade_cal_publication=spec["trade_cal_publication"],
        development_session_refs=spec["development_session_refs"],
        temporal_partition_contract=spec["temporal_partition_contract"],
        collection_publication_output_root=publication_root,
        collection_publication=publication,
    )
    if type(receipt) is not dict or receipt.get("verified") is not True:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history authority verification rejected")
    return json.loads(_canonical_bytes(receipt))


def _redacted_publication_result(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if type(value) is not dict or not set(_PUBLICATION_RESULT_FIELDS) <= set(value):
        raise FactorV3FeatureHistoryRunnerError(
            "factor-v3 feature-history publication result rejected"
        )
    return {field: value[field] for field in _PUBLICATION_RESULT_FIELDS}


def _redacted_result(value: Any) -> dict[str, Any]:
    if type(value) is not dict or set(value) != _RESULT_FIELDS:
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history result rejected")
    return {
        "collection_publication": _redacted_publication_result(
            value["collection_publication"]
        ),
        "completed_session_count": value["completed_session_count"],
        "receipt": value["receipt"],
        "run_spec_sha256": value["run_spec_sha256"],
        "status": value["status"],
    }


def _result(state: Mapping[str, Any]) -> dict[str, Any]:
    publication = state["collection_publication"]
    if publication is not None:
        publication = _validated_publication(publication)
    return _redacted_result(
        {
            "collection_publication": publication,
            "completed_session_count": state["completed_session_count"],
            "receipt": state["receipt"],
            "run_spec_sha256": state["run_spec_sha256"],
            "status": state["status"],
        }
    )


def _stdout_result(value: Any) -> dict[str, Any]:
    result = _redacted_result(value)
    _reject_secret_shape(result, reject_publication_capability=True)
    return result


def _verify_existing_publication(
    *,
    paths: Mapping[str, Path],
    sessions: Sequence[str],
    spec: Mapping[str, Any],
    state: Mapping[str, Any],
) -> dict[str, Any]:
    publication = _validated_publication(state["collection_publication"])
    receipt = _verify_collection_authority(
        publication_root=_safe_directory(
            paths["publication_root"], label="publication root", create=False
        ),
        publication=publication,
        spec=spec,
    )
    verified = _state_payload(
        run_spec_sha256=spec["run_spec_sha256"],
        status="verified",
        completed_session_count=len(sessions),
        credential_generation_id=state["credential_generation_id"],
        collection_publication=publication,
        receipt=receipt,
    )
    _atomic_json(paths["state"], verified)
    return _result(verified)


def _mark_publication_verification_failed(
    *,
    paths: Mapping[str, Path],
    spec: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    """Persist a fail-closed state after a published candidate is rejected."""

    failed = _state_payload(
        run_spec_sha256=spec["run_spec_sha256"],
        status="failed",
        completed_session_count=state["completed_session_count"],
        credential_generation_id=state["credential_generation_id"],
        collection_publication=state["collection_publication"],
        receipt=state["receipt"],
    )
    _atomic_json(paths["state"], failed)


def _run_factor_v3_feature_history_collection_with_route_credential(
    *,
    run_spec_path: str | Path,
    run_root: str | Path,
    credential: str,
    source_generation_id: str,
    feature_history_policy_descriptor: Mapping[str, Any],
) -> dict[str, Any]:
    """Private closed entrypoint used only by Jiaoch credential slots."""

    if _validated_policy_descriptor(feature_history_policy_descriptor) != (
        FACTOR_V3_FEATURE_HISTORY_COLLECTION_POLICY_DESCRIPTOR
    ):
        raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history collection policy rejected")
    spec = load_factor_v3_feature_history_run_spec(run_spec_path)
    _verify_plan(spec)
    paths = _run_paths(run_root, create=True)
    segments = _validated_segments(spec["collection_plan"])
    sessions = [session for segment in segments for session in segment["sessions"]]
    with _run_lock(paths["lock"]):
        state = _load_or_initialize_run(paths, spec, allow_initialize=True)
        try:
            if state["status"] in {"published", "verified"} or (
                state["status"] == "failed" and state["collection_publication"] is not None
            ):
                return _verify_existing_publication(
                    paths=paths,
                    sessions=sessions,
                    spec=spec,
                    state=state,
                )
            persisted_generation_id = state["credential_generation_id"]
            if persisted_generation_id is None:
                active_generation_id = _validated_uuid4(
                    source_generation_id,
                    label="credential generation",
                )
            else:
                active_generation_id = _validated_uuid4(
                    persisted_generation_id,
                    label="credential generation",
                )
                if source_generation_id != active_generation_id:
                    raise FactorV3FeatureHistoryRunnerError(
                        "factor-v3 feature-history credential generation drifted"
                    )
            _atomic_json(
                paths["state"],
                _state_payload(
                    run_spec_sha256=spec["run_spec_sha256"],
                    status="collecting",
                    completed_session_count=state["completed_session_count"],
                    credential_generation_id=active_generation_id,
                    collection_publication=state["collection_publication"],
                    receipt=state["receipt"],
                ),
            )
            store_root = _safe_directory(paths["store"], label="PIT store", create=True)
            store = PITReceiptStore(str(store_root))
            source = _validated_frozen_jiaoch_source(
                _fixed_jiaoch_source(
                    credential=credential,
                    source_generation_id=active_generation_id,
                )
            )
            completed = state["completed_session_count"]
            session_offset = 0
            for segment in segments:
                collector = _collector_for_segment(
                    store=store,
                    spec=spec,
                    source=source,
                    temporal_role=segment["temporal_role"],
                    sessions=segment["sessions"],
                )
                for relative_index, session in enumerate(segment["sessions"]):
                    session_index = session_offset + relative_index
                    if session_index < completed:
                        continue
                    membership_spec = build_bak_basic_specs([session])
                    if len(membership_spec) != 1 or membership_spec[0].dataset != "bak_basic":
                        raise FactorV3FeatureHistoryRunnerError(
                            "factor-v3 feature-history membership specification rejected"
                        )
                    collector.fetch_membership_snapshot(membership_spec[0], resume=True)
                    collector.collect_market_session_generation(
                        session,
                        resume=True,
                        vintage=_MARKET_SESSION_VINTAGE,
                    )
                    completed = session_index + 1
                    _atomic_json(
                        paths["state"],
                        _state_payload(
                            run_spec_sha256=spec["run_spec_sha256"],
                            status="collecting",
                            completed_session_count=completed,
                            credential_generation_id=active_generation_id,
                        ),
                    )
                session_offset += len(segment["sessions"])
            _finalize_store(store_root, sessions)
            collected = _state_payload(
                run_spec_sha256=spec["run_spec_sha256"],
                status="collected",
                completed_session_count=len(sessions),
                credential_generation_id=active_generation_id,
            )
            _atomic_json(paths["state"], collected)
            publication_root = _safe_directory(
                paths["publication_root"], label="publication root", create=True
            )
            publication = _publish_collection_candidate(
                publication_root=publication_root,
                store_root=store_root,
                spec=spec,
            )
            published = _state_payload(
                run_spec_sha256=spec["run_spec_sha256"],
                status="published",
                completed_session_count=len(sessions),
                credential_generation_id=active_generation_id,
                collection_publication=publication,
            )
            _atomic_json(paths["state"], published)
            receipt = _verify_collection_authority(
                publication_root=publication_root,
                publication=publication,
                spec=spec,
            )
            verified = _state_payload(
                run_spec_sha256=spec["run_spec_sha256"],
                status="verified",
                completed_session_count=len(sessions),
                credential_generation_id=active_generation_id,
                collection_publication=publication,
                receipt=receipt,
            )
            _atomic_json(paths["state"], verified)
            return _result(verified)
        except BaseException:
            previous = _read_json_file(paths["state"], label="run state", max_bytes=_MAX_STATE_BYTES)
            failed = _state_payload(
                run_spec_sha256=spec["run_spec_sha256"],
                status="failed",
                completed_session_count=int(previous.get("completed_session_count") or 0),
                credential_generation_id=previous.get("credential_generation_id"),
                collection_publication=previous.get("collection_publication"),
                receipt=previous.get("receipt"),
            )
            _atomic_json(paths["state"], failed)
            raise


def run_factor_v3_feature_history_collection(
    *, run_spec_path: str | Path, run_root: str | Path
) -> dict[str, Any]:
    """Resolve a sealed Jiaoch generation, then run the closed five-route set."""

    from app.jiaoch_credential_slots import (
        _collect_jiaoch_feature_history_from_environment_for_run,
    )

    return _collect_jiaoch_feature_history_from_environment_for_run(
        run_spec_path=run_spec_path,
        run_root=run_root,
        source_generation_id=_run_credential_generation_id(
            run_spec_path=run_spec_path,
            run_root=run_root,
        ),
    )


def verify_factor_v3_feature_history_run(
    *, run_spec_path: str | Path, run_root: str | Path
) -> dict[str, Any]:
    """Re-run the capability-bound authority verification without collection."""

    spec = load_factor_v3_feature_history_run_spec(run_spec_path)
    _verify_plan(spec)
    paths = _run_paths(run_root, create=False)
    segments = _validated_segments(spec["collection_plan"])
    sessions = [session for segment in segments for session in segment["sessions"]]
    with _run_lock(paths["lock"]):
        state = _load_or_initialize_run(paths, spec, allow_initialize=False)
        if state["status"] not in {"published", "verified"} and not (
            state["status"] == "failed" and state["collection_publication"] is not None
        ):
            raise FactorV3FeatureHistoryRunnerError("factor-v3 feature-history run is not publishable")
        try:
            return _verify_existing_publication(
                paths=paths,
                sessions=sessions,
                spec=spec,
                state=state,
            )
        except BaseException:
            _mark_publication_verification_failed(
                paths=paths,
                spec=spec,
                state=state,
            )
            raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factor-v3-feature-history-runner")
    parser.add_argument("command", choices=("run", "verify"))
    parser.add_argument("--run-spec", required=True)
    parser.add_argument("--run-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "run":
            result = run_factor_v3_feature_history_collection(
                run_spec_path=args.run_spec,
                run_root=args.run_root,
            )
        else:
            result = verify_factor_v3_feature_history_run(
                run_spec_path=args.run_spec,
                run_root=args.run_root,
            )
        result = _stdout_result(result)
    except (FactorV3FeatureHistoryRunnerError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            _stdout_result(result), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
