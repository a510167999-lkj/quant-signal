"""Factor V3 development-only input authority publication and replay.

The candidate deliberately stops before formal materialization. It replays the
available feature-history and daily-basic verifiers, pins the still-unreplayed
Factor V2 parent adapter contract, and publishes content-addressed snapshots.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
from typing import Any, Mapping, Sequence

from app import audited_pit_factor_v3_feature_history_authority as history_authority
from app import audited_pit_factor_v3_points_contract as points
from app import factor_v2_decision_branch_selector as branch_selector
from app import factor_v3_daily_basic_733_exact_set_authority as daily_authority
from app import factor_v3_feature_history_frozen_source_attestation as frozen_attestation
from app import factor_v3_feature_history_runner as history_runner
from app import factor_v3_verified_row_exporter as row_exporter
from app import jiaoch_daily_basic_exact_set_authority as legacy_daily
from app import research_scope


SOURCE_SPEC_SCHEMA = "factor-v3-development-input-source-spec/v2"
DESCRIPTOR_SCHEMA = "factor-v3-development-input-authority/v3"
PUBLICATION_SCHEMA = "factor-v3-development-input-authority-publication/v3"
PUBLICATION_MANIFEST_SCHEMA = (
    "factor-v3-development-input-authority-publication-manifest/v3"
)
DERIVED_ADAPTER_SCHEMA = "factor-v3-development-input-derived-adapter/v2"
PRODUCER_SNAPSHOT_SCHEMA = (
    "factor-v3-development-input-candidate-producer-snapshot/v1"
)
AUTHORITY_STATUS = "UNVERIFIED_PRODUCER_DEVELOPMENT_INPUT_CANDIDATE"
FACTOR_V2_FROZEN_SOURCE_COMMIT = "3e9bd1bcf12024f9bf52a0b0fbcdd86f7bc64109"
FACTOR_V2_FROZEN_SOURCE_TREE_OID = "bf378b4e23436e5e6fa2e9d35ba14f8ab4495c96"
FACTOR_V2_FROZEN_SOURCE_BLOB_SHA256 = {
    "app/audited_pit_continuous_ridge_oof.py": (
        "6f51dca47e2fe5816b540f1a99b947b3cb8dc3f44e27306580814ea54ee91c47"
    ),
    "app/audited_pit_factor_v2.py": (
        "9a480e86ee8b462e175c0499fbcfe810e50622435cced767aff8b37b50edebd9"
    ),
    "app/audited_pit_factor_v2_parent.py": (
        "3e4c4e3bbaba96b784f2161ad055f0864360adbe00bf4fd5dde40d4dd13a8522"
    ),
    "app/audited_pit_factor_v2_runtime.py": (
        "efeaf00fda5d064e09a4faa4932e5b77487d79df476be6a56a415a92c7865126"
    ),
    "app/audited_pit_factor_v2_suspension.py": (
        "92ce088d0c5ae7012be77279aa7cd777e4c7dd69a639b80307f72f4bd4c107d4"
    ),
    "app/audited_pit_factor_v2_training_overlay.py": (
        "a8bae99348d28555e77fd90d0ad674bc2f926c2d994f23275433e6b02b360a21"
    ),
    "app/audited_pit_ranked_liquidity_store.py": (
        "f0ec56877973ee1fbcaec20d75999783f1d46cea60b14b8f80d34442bb9499d7"
    ),
    "app/audited_pit_shallow_gbdt.py": (
        "1a41d9e75c42b2eadd575a21ef3e2a1fe00f41e16f9a190987cf9371b4100d7c"
    ),
    "app/audited_pit_training_dataset_materializer.py": (
        "88d8ada6e25bc5fcb6b56f4825fb75914208102d592dd60044cc02c388948c95"
    ),
    "app/audited_pit_training_dataset_store.py": (
        "fe886d4799a74727f9ac421714fe1f0bd917ce8fa3e511c5bf7c16e2b2655171"
    ),
    "app/durable_io.py": (
        "afc983c24e0437b0c1db23b57577c141f6aa9faec84d3a7f7a82723fa5fb8c7f"
    ),
    "app/research_suspension_evidence.py": (
        "20273a95ffa5cbaacc107fd02fbfb9ebfba7fa3d97965d20daf401b1c1808f08"
    ),
}
FACTOR_V2_PARENT_SUBPROCESS_CONTRACT = {
    "bootstrap": "sys.path.insert(0, verified_frozen_source_root)",
    "environment": {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "sensitive_environment_forwarding_allowed": False,
    },
    "network_allowed": False,
    "public_entrypoints": [
        "app.audited_pit_factor_v2_parent.verify_preregistered_development_4_parent",
        (
            "app.audited_pit_factor_v2_training_overlay."
            "verify_preregistered_development_4_factor_v2_training_overlay"
        ),
    ],
    "python_flags": ["-I"],
    "research_execution_allowed": False,
    "same_app_tree_required": True,
    "schema": "factor-v3-frozen-factor-v2-parent-subprocess-contract/v1",
    "source_import_mode": "verified_frozen_source_root_only",
    "stdout_schema": "factor-v3-frozen-factor-v2-parent-public-verification/v1",
}

SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "final_oos_consumed",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
)
PROVENANCE_FALSE_FIELDS = (
    "git_executable_verified",
    "git_tree_blob_binding_verified",
    "loaded_source_identity_verified",
    "producer_binding_verified",
)

_TOP_FIELDS = frozenset({"schema", "feature_history", "daily_basic", "factor_v2"})
_FEATURE_FIELDS = frozenset(
    {
        "run_spec_path",
        "run_root",
        "frozen_source_attestation_path",
        "expected_frozen_source_attestation_sha256",
        "frozen_source_root",
        "expected_frozen_source_commit",
    }
)
_DAILY_FIELDS = frozenset(
    {
        "audited_development_universe_sqlite_path",
        "expected_development_coverage_audit_sha256",
        "expected_development_artifact_root_sha256",
        "expected_development_temporal_contract_sha256",
        "expected_development_temporal_role",
        "expected_source_authority_root_sha256",
        "points_output_root",
        "collection_set_refs",
        "security_code_transition_evidence_root",
        "expected_security_code_transition_contract_sha256",
        "authority_output_root",
        "publication",
    }
)
_FACTOR_V2_FIELDS = frozenset(
    {
        "decision_receipt_path",
        "expected_decision_receipt_raw_file_sha256",
        "parent_snapshot_path",
        "expected_parent_snapshot_file_sha256",
        "parent_payload_fields",
        "pinned_parent_adapter",
    }
)
_PINNED_PARENT_ADAPTER_FIELDS = frozenset(
    {
        "schema",
        "frozen_source_root",
        "expected_frozen_source_commit",
        "expected_frozen_source_tree_oid",
        "expected_frozen_source_blob_sha256",
        "parent_materialization_manifest_path",
        "overlay_manifest_path",
        "suspension_metadata_path",
        "subprocess_contract",
        "public_verifier_replay_performed",
    }
)
_COLLECTION_REF_FIELDS = frozenset(
    {"collection_set_relative_path", "collection_set_sha256", "trade_date"}
)
_PUBLICATION_FIELDS = frozenset(
    {
        "authority_root_sha256",
        "descriptor_relative_path",
        "descriptor_sha256",
        "publication_relative_path",
        "publication_sha256",
        "schema",
    }
)
_SNAPSHOT_NAMES = (
    "calendar",
    "factor_v2_parent",
    "daily_basic",
    "daily_traded_cross_section",
    "listing_membership",
    "suspensions",
    "security_code_transitions",
    "upstream_board_ledger",
    "factor_v2_evaluation",
    "feature_history_receipt",
    "daily_basic_exact_set_receipt",
)
_UPSTREAM_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_COMMIT_RE = re.compile(r"[0-9a-f]{40}")
_SENSITIVE_TERMS = (
    "api_key",
    "authorization",
    "capability",
    "credential",
    "password",
    "secret",
    "token",
)
_MAX_JSON_BYTES = 256 * 1024 * 1024


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _unique_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _strict_json_loads(raw: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda item: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {item}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        if "duplicate" in str(exc):
            raise ValueError(f"{label} duplicate key rejected") from exc
        raise ValueError(f"{label} is not strict JSON") from exc
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    if _canonical_bytes(value) != raw:
        raise ValueError(f"{label} must be canonical JSON")
    return value


def _strict_sha256(value: Any, *, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase SHA-256")
    return value


def _strict_date(value: Any, *, label: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{label} must be an ISO date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO date") from exc
    if parsed.isoformat() != value:
        raise ValueError(f"{label} must be an ISO date")
    return value


def _strict_absolute_path(value: Any, *, label: str) -> Path:
    if type(value) is not str:
        raise ValueError(f"{label} must be an absolute path")
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{label} must be an absolute path")
    return path


def _is_reparse_point(path: str | Path) -> bool:
    candidate = Path(path)
    if candidate.is_symlink():
        return True
    try:
        metadata = candidate.lstat()
    except OSError:
        return False
    attributes = getattr(metadata, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & flag)


def _assert_safe_path(path: Path, *, label: str, regular: bool | None = None) -> None:
    if not path.exists():
        raise ValueError(f"{label} unavailable or missing")
    current = path
    while True:
        if _is_reparse_point(current):
            raise ValueError(f"{label} reparse point rejected")
        if current == current.parent:
            break
        current = current.parent
    if regular is True and not path.is_file():
        raise ValueError(f"{label} must be a regular file")
    if regular is False and not path.is_dir():
        raise ValueError(f"{label} must be a directory")


def _read_regular_bytes(path: Path, *, label: str, max_bytes: int = _MAX_JSON_BYTES) -> bytes:
    _assert_safe_path(path, label=label, regular=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ValueError(f"{label} unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > max_bytes:
            raise ValueError(f"{label} is not a bounded regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(1024 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{label} exceeds the byte limit")
        after = os.fstat(descriptor)
        before_fingerprint = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
        )
        after_fingerprint = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
        )
        if before_fingerprint != after_fingerprint:
            raise ValueError(f"{label} changed while being read")
    finally:
        os.close(descriptor)
    _assert_safe_path(path, label=label, regular=True)
    current = path.stat()
    if (current.st_size, current.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError(f"{label} changed while being read")
    return b"".join(chunks)


def _read_canonical_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = _read_regular_bytes(path, label=label)
    return _strict_json_loads(raw, label=label), raw


def _assert_fields(value: Any, expected: frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != expected:
        raise ValueError(f"{label} fields rejected")
    return value


def _assert_no_sensitive(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str or any(term in key.lower() for term in _SENSITIVE_TERMS):
                raise ValueError(f"{label} contains sensitive fields")
            _assert_no_sensitive(nested, label=label)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for nested in value:
            _assert_no_sensitive(nested, label=label)


def _public_projection_with_count(value: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redacted = 0

    def project(nested: Any) -> Any:
        nonlocal redacted
        if isinstance(nested, Mapping):
            result: dict[str, Any] = {}
            for key, item in nested.items():
                if any(term in key.lower() for term in _SENSITIVE_TERMS):
                    redacted += 1
                    continue
                result[key] = project(item)
            return result
        if isinstance(nested, list):
            return [project(item) for item in nested]
        return nested

    projected = project(value)
    if type(projected) is not dict:
        raise ValueError("public projection rejected")
    _assert_no_sensitive(projected, label="public projection")
    return projected, redacted


def _public_projection(value: Mapping[str, Any]) -> dict[str, Any]:
    return _public_projection_with_count(value)[0]


def _redacted_receipt_projection(
    value: Mapping[str, Any],
    *,
    self_hash_field: str,
    source_hash_field: str,
) -> dict[str, Any]:
    projection, redacted_count = _public_projection_with_count(value)
    source_hash = _strict_sha256(
        projection.pop(self_hash_field, None),
        label=f"source receipt {self_hash_field}",
    )
    return {
        "projection_schema": "factor-v3-public-receipt-redacted-projection/v1",
        "redacted_field_count": redacted_count,
        source_hash_field: source_hash,
        "source_receipt_projection": projection,
        "source_receipt_projection_sha256": _canonical_sha256(projection),
    }


def _safe_relative_path(root: Path, relative: Any, *, label: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative:
        raise ValueError(f"{label} path rejected")
    pure = Path(*relative.split("/"))
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"{label} path rejected")
    path = root.joinpath(*relative.split("/"))
    try:
        if not path.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
            raise ValueError(f"{label} path escapes root")
    except OSError as exc:
        raise ValueError(f"{label} path rejected") from exc
    return path


def _validate_pinned_parent_adapter_source(value: Any) -> dict[str, Any]:
    adapter = _assert_fields(
        value,
        _PINNED_PARENT_ADAPTER_FIELDS,
        label="pinned parent adapter",
    )
    if adapter.get("schema") != "factor-v3-pinned-factor-v2-parent-adapter-source/v1":
        raise ValueError("pinned parent adapter schema rejected")
    for key in (
        "frozen_source_root",
        "parent_materialization_manifest_path",
        "overlay_manifest_path",
        "suspension_metadata_path",
    ):
        _strict_absolute_path(adapter[key], label=f"pinned parent adapter {key}")
    if adapter.get("expected_frozen_source_commit") != FACTOR_V2_FROZEN_SOURCE_COMMIT:
        raise ValueError("pinned parent frozen source commit rejected")
    if adapter.get("expected_frozen_source_tree_oid") != FACTOR_V2_FROZEN_SOURCE_TREE_OID:
        raise ValueError("pinned parent frozen source tree rejected")
    blobs = adapter.get("expected_frozen_source_blob_sha256")
    if type(blobs) is not dict or blobs != FACTOR_V2_FROZEN_SOURCE_BLOB_SHA256:
        raise ValueError("pinned parent frozen source blobs rejected")
    for path, digest in blobs.items():
        if type(path) is not str or not path.startswith("app/") or "\\" in path:
            raise ValueError("pinned parent frozen source blob path rejected")
        _strict_sha256(digest, label=f"pinned parent frozen source blob {path}")
    if adapter.get("subprocess_contract") != FACTOR_V2_PARENT_SUBPROCESS_CONTRACT:
        raise ValueError("pinned parent subprocess contract rejected")
    if adapter.get("public_verifier_replay_performed") is not False:
        raise ValueError("pinned parent public verifier replay claim rejected")
    _assert_no_sensitive(adapter, label="pinned parent adapter")
    return dict(adapter)


def _validate_source_spec(value: Any) -> dict[str, Any]:
    spec = _assert_fields(value, _TOP_FIELDS, label="source spec")
    if spec.get("schema") != SOURCE_SPEC_SCHEMA:
        raise ValueError("source spec schema rejected")
    feature = _assert_fields(spec.get("feature_history"), _FEATURE_FIELDS, label="feature history")
    daily = _assert_fields(spec.get("daily_basic"), _DAILY_FIELDS, label="daily basic")
    factor_v2 = _assert_fields(spec.get("factor_v2"), _FACTOR_V2_FIELDS, label="factor-v2")
    for key in ("run_spec_path", "run_root", "frozen_source_attestation_path", "frozen_source_root"):
        _strict_absolute_path(feature[key], label=f"feature history {key}")
    _strict_sha256(
        feature["expected_frozen_source_attestation_sha256"],
        label="feature history attestation SHA",
    )
    if type(feature["expected_frozen_source_commit"]) is not str or _COMMIT_RE.fullmatch(
        feature["expected_frozen_source_commit"]
    ) is None:
        raise ValueError("feature history frozen commit rejected")
    for key in (
        "audited_development_universe_sqlite_path",
        "points_output_root",
        "security_code_transition_evidence_root",
        "authority_output_root",
    ):
        _strict_absolute_path(daily[key], label=f"daily basic {key}")
    for key in (
        "expected_development_coverage_audit_sha256",
        "expected_development_artifact_root_sha256",
        "expected_development_temporal_contract_sha256",
        "expected_source_authority_root_sha256",
        "expected_security_code_transition_contract_sha256",
    ):
        _strict_sha256(daily[key], label=f"daily basic {key}")
    if type(daily["expected_development_temporal_role"]) is not str or not daily[
        "expected_development_temporal_role"
    ]:
        raise ValueError("daily basic temporal role rejected")
    refs = daily["collection_set_refs"]
    if type(refs) is not list or len(refs) != 733:
        raise ValueError("daily basic collection refs require exact 733 sessions")
    for ref in refs:
        _assert_fields(ref, _COLLECTION_REF_FIELDS, label="daily basic collection ref")
        _strict_date(ref["trade_date"], label="daily basic collection date")
        _strict_sha256(ref["collection_set_sha256"], label="daily basic collection SHA")
        if type(ref["collection_set_relative_path"]) is not str:
            raise ValueError("daily basic collection ref path rejected")
    if type(daily["publication"]) is not dict:
        raise ValueError("daily basic publication fields rejected")
    for key in ("decision_receipt_path", "parent_snapshot_path"):
        _strict_absolute_path(factor_v2[key], label=f"factor-v2 {key}")
    for key in (
        "expected_decision_receipt_raw_file_sha256",
        "expected_parent_snapshot_file_sha256",
    ):
        _strict_sha256(factor_v2[key], label=f"factor-v2 {key}")
    if factor_v2["parent_payload_fields"] != ["features"]:
        raise ValueError("factor-v2 parent payload fields rejected")
    _validate_pinned_parent_adapter_source(factor_v2["pinned_parent_adapter"])
    _assert_no_sensitive(spec, label="source spec")
    return spec


def _load_source_spec(
    path_value: str | Path,
    *,
    expected_sha256: str,
) -> tuple[Path, dict[str, Any], str]:
    expected = _strict_sha256(expected_sha256, label="expected source spec SHA")
    path = Path(path_value)
    if not path.is_absolute():
        raise ValueError("source spec path must be absolute")
    value, raw = _read_canonical_json(path, label="source spec")
    observed = _sha256_bytes(raw)
    if observed != expected:
        raise ValueError("source spec content address drifted")
    return path, _validate_source_spec(value), observed


def _daily_kwargs(spec: Mapping[str, Any]) -> dict[str, Any]:
    feature = spec["feature_history"]
    daily = spec["daily_basic"]
    return {
        "feature_history_run_spec_path": feature["run_spec_path"],
        "feature_history_run_root": feature["run_root"],
        "feature_history_frozen_source_attestation_path": feature[
            "frozen_source_attestation_path"
        ],
        "expected_feature_history_frozen_source_attestation_sha256": feature[
            "expected_frozen_source_attestation_sha256"
        ],
        "feature_history_frozen_source_root": feature["frozen_source_root"],
        "expected_feature_history_frozen_source_commit": feature[
            "expected_frozen_source_commit"
        ],
        "audited_development_universe_sqlite_path": daily[
            "audited_development_universe_sqlite_path"
        ],
        "expected_development_coverage_audit_sha256": daily[
            "expected_development_coverage_audit_sha256"
        ],
        "expected_development_artifact_root_sha256": daily[
            "expected_development_artifact_root_sha256"
        ],
        "expected_development_temporal_contract_sha256": daily[
            "expected_development_temporal_contract_sha256"
        ],
        "expected_development_temporal_role": daily[
            "expected_development_temporal_role"
        ],
        "expected_source_authority_root_sha256": daily[
            "expected_source_authority_root_sha256"
        ],
        "points_output_root": daily["points_output_root"],
        "collection_set_refs": daily["collection_set_refs"],
        "security_code_transition_evidence_root": daily[
            "security_code_transition_evidence_root"
        ],
        "expected_security_code_transition_contract_sha256": daily[
            "expected_security_code_transition_contract_sha256"
        ],
        "output_root": daily["authority_output_root"],
        "publication": daily["publication"],
    }


def _validate_history_receipt(
    attestation: Any,
    *,
    prewindow: Sequence[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if (
        type(attestation) is not dict
        or attestation.get("verified") is not True
        or attestation.get("session_count") != 250
        or attestation.get("sessions_sha256") != _canonical_sha256(prewindow)
        or type(attestation.get("authority_binding")) is not dict
    ):
        raise ValueError("feature history frozen attestation binding rejected")
    binding = attestation["authority_binding"]
    receipt = binding.get("receipt")
    if type(receipt) is not dict:
        raise ValueError("feature history collection authority receipt unavailable")
    if (
        receipt.get("schema_version")
        != "audited-pit-factor-v3-feature-history-authority-receipt/v3"
        or receipt.get("verified") is not True
        or receipt.get("authority_status") != "VERIFIED_FEATURE_HISTORY_ONLY"
        or receipt.get("feature_history_only") is not True
        or receipt.get("session_count") != 250
        or receipt.get("sessions_sha256") != _canonical_sha256(prewindow)
        or receipt.get("factor_materialization_eligible") is not False
        or receipt.get("experiment_launch_eligible") is not False
    ):
        raise ValueError("feature history real receipt fields rejected")
    for field in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_profile_registered",
        "production_recommendation_eligible",
    ):
        if receipt.get(field) is not False:
            raise ValueError("feature history development-only scope rejected")
    receipt_sha = _strict_sha256(receipt.get("receipt_sha256"), label="feature history receipt SHA")
    if attestation.get("receipt_sha256") != receipt_sha:
        raise ValueError("feature history frozen attestation binding rejected")
    publication_root = binding.get("collection_publication_output_root")
    manifest_relative_path = binding.get("manifest_relative_path")
    manifest_sha256 = binding.get("manifest_sha256")
    if (
        type(publication_root) is not str
        or not Path(publication_root).is_absolute()
        or type(manifest_relative_path) is not str
    ):
        raise ValueError("feature history immutable snapshot binding rejected")
    _strict_sha256(manifest_sha256, label="feature history manifest SHA")
    source_binding = {
        "collection_publication_output_root": publication_root,
        "manifest_relative_path": manifest_relative_path,
        "manifest_sha256": manifest_sha256,
        "receipt": dict(receipt),
    }
    return _public_projection(receipt), source_binding


def _validate_daily_receipt(
    receipt: Any,
    result: Any,
    *,
    raw_sha256: str,
    sessions: Sequence[str],
) -> dict[str, Any]:
    if type(receipt) is not dict:
        raise ValueError("factor-v3 733 authority receipt rejected")
    if (
        receipt.get("schema") != "factor-v3-daily-basic-733-exact-set-receipt/v2"
        or receipt.get("authority_status")
        != "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET"
    ):
        raise ValueError("factor-v3 733 authority status rejected")
    if (
        receipt.get("authority_scope")
        != "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY"
        or receipt.get("row_authority_status")
        != "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY"
        or receipt.get("trade_date_count") != 733
        or receipt.get("trade_dates") != list(sessions)
        or receipt.get("trade_dates_sha256") != _canonical_sha256(sessions)
        or receipt.get("all_supported_segments_compared_before_scope_filter") is not True
        or receipt.get("factor_v3_development_materialization_input_eligible") is not True
        or receipt.get("formal_factor_v3_materialization_performed") is not False
        or receipt.get("rows_published") is not False
    ):
        raise ValueError("factor-v3 733 exact session authority rejected")
    for field in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_profile_registered",
        "production_recommendation_eligible",
    ):
        if receipt.get(field) is not False:
            raise ValueError("factor-v3 733 development-only scope rejected")
    per_date = receipt.get("per_date_statistics")
    if type(per_date) is not list or len(per_date) != 733:
        raise ValueError("factor-v3 733 per-date statistics rejected")
    for expected_date, entry in zip(sessions, per_date, strict=True):
        if type(entry) is not dict or entry.get("trade_date") != expected_date:
            raise ValueError("factor-v3 733 per-date session sequence rejected")
        counts = entry.get("daily_basic_raw_segment_counts")
        if (
            type(counts) is not dict
            or set(counts) != set(_UPSTREAM_SEGMENTS)
            or any(type(counts[name]) is not int or counts[name] <= 0 for name in _UPSTREAM_SEGMENTS)
        ):
            raise ValueError("BSE/STAR upstream segment evidence rejected")
        _strict_sha256(
            entry.get("authoritative_daily_raw_codes_sha256"),
            label="authoritative daily raw codes SHA",
        )
        _strict_sha256(
            entry.get("daily_basic_canonical_rows_sha256"),
            label="daily basic canonical rows SHA",
        )
    if receipt.get("per_date_statistics_sha256") != _canonical_sha256(per_date):
        raise ValueError("factor-v3 733 per-date statistics root rejected")
    normalized_root = _strict_sha256(
        receipt.get("normalized_daily_basic_row_authority_root_sha256"),
        label="normalized daily basic row authority root",
    )
    unsigned = dict(receipt)
    authority_root = unsigned.pop("authority_root_sha256", None)
    if authority_root != _canonical_sha256(unsigned):
        raise ValueError("factor-v3 733 authority self hash rejected")
    if (
        type(result) is not dict
        or result.get("verified") is not True
        or result.get("trade_date_count") != 733
        or result.get("trade_dates") != list(sessions)
        or result.get("authority_root_sha256") != authority_root
        or result.get("receipt_sha256") != raw_sha256
    ):
        raise ValueError("factor-v3 733 public verification binding rejected")
    _strict_sha256(normalized_root, label="normalized daily basic row root")
    return _public_projection(receipt)


def _validate_branch_receipt(value: Any, *, expected_raw_sha256: str) -> dict[str, Any]:
    expected_fields = {
        "arm_decisions",
        "arm_order",
        "embargo_consumed",
        "evaluation_artifact_sha256",
        "final_oos_consumed",
        "low_rvol_overlay_status",
        "production_recommendation_eligible",
        "receipt_sha256",
        "schema_version",
        "selected_arm",
        "selected_branch",
        "selection_rule",
        "source_decision_receipt_raw_file_sha256",
        "source_decision_receipt_sha256",
        "verified",
    }
    if type(value) is not dict or set(value) != expected_fields:
        raise ValueError("factor-v2 branch receipt fields rejected")
    if (
        value.get("schema_version")
        != "factor-v2-decision-branch-selector-receipt/v1"
        or value.get("verified") is not True
        or value.get("source_decision_receipt_raw_file_sha256") != expected_raw_sha256
        or value.get("embargo_consumed") is not False
        or value.get("final_oos_consumed") is not False
        or value.get("production_recommendation_eligible") is not False
    ):
        raise ValueError("factor-v2 branch receipt rejected")
    for field in (
        "evaluation_artifact_sha256",
        "source_decision_receipt_raw_file_sha256",
        "source_decision_receipt_sha256",
    ):
        _strict_sha256(value[field], label=f"factor-v2 branch {field}")
    unsigned = dict(value)
    receipt_sha = unsigned.pop("receipt_sha256")
    if receipt_sha != _canonical_sha256(unsigned):
        raise ValueError("factor-v2 branch receipt self hash rejected")
    decisions = value["arm_decisions"]
    arm_order = value["arm_order"]
    if (
        type(decisions) is not dict
        or type(arm_order) is not list
        or set(decisions) != set(arm_order)
        or any(decisions.get(arm) not in {"GREEN", "RED"} for arm in arm_order)
    ):
        raise ValueError("factor-v2 branch decision map rejected")
    expected_branch = next(
        (arm for arm in arm_order if decisions[arm] == "GREEN"),
        "low_rvol20_rank_overlay_20",
    )
    if value["selected_branch"] != expected_branch:
        raise ValueError("factor-v2 branch selection rejected")
    return dict(value)


def validate_factor_v2_common_eligible_parent_hash_binding(
    *,
    parent_snapshot_path: str | Path,
    expected_parent_snapshot_file_sha256: str,
    parent_payload_fields: Sequence[str],
    pinned_parent_adapter: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate hashes only; this does not grant Factor V2 source authority."""

    expected_raw = _strict_sha256(
        expected_parent_snapshot_file_sha256,
        label="factor-v2 parent expected file SHA",
    )
    path = Path(parent_snapshot_path)
    payload, raw = _read_canonical_json(path, label="factor-v2 parent snapshot")
    if _sha256_bytes(raw) != expected_raw:
        raise ValueError("factor-v2 parent content address drifted")
    if list(parent_payload_fields) != ["features"]:
        raise ValueError("factor-v2 parent payload fields rejected")
    pinned_source = _validate_pinned_parent_adapter_source(dict(pinned_parent_adapter))
    pinned_source_sha256 = _canonical_sha256(pinned_source)
    expectation = points.FACTOR_V3_POINTS_CONTRACT["preregistered_parent_expectation"]
    expected_fields = set(expectation) | {
        "candidate_identity_root_sha256",
        "rows",
        "rows_sha256",
    }
    if set(payload) != expected_fields:
        raise ValueError("factor-v2 parent fields rejected")
    for key, expected in expectation.items():
        if payload.get(key) != expected:
            raise ValueError(f"factor-v2 parent binding drifted: {key}")
    rows = payload.get("rows")
    if type(rows) is not list or len(rows) != expectation["common_eligible_candidate_count"]:
        raise ValueError("factor-v2 parent row count rejected")
    if payload.get("rows_sha256") != _canonical_sha256(rows):
        raise ValueError("factor-v2 parent rows root rejected")
    allowed = {"candidate_key", "features", "signal_date", "ts_code"}
    identities: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    development = expectation["sessions"]
    for row in rows:
        if type(row) is not dict or set(row) != allowed:
            raise ValueError("factor-v2 parent payload whitelist rejected")
        signal_date = _strict_date(row.get("signal_date"), label="factor-v2 signal date")
        code = row.get("ts_code")
        candidate_key = row.get("candidate_key")
        if (
            type(code) is not str
            or not research_scope.is_mainboard_chinext_symbol(code)
            or candidate_key != f"cn-a-share:{code}|{signal_date}"
            or not development["start"] <= signal_date <= development["end"]
        ):
            raise ValueError("factor-v2 parent identity or scope rejected")
        key = (candidate_key, signal_date)
        if key in seen:
            raise ValueError("factor-v2 parent duplicate identity rejected")
        seen.add(key)
        features = row.get("features")
        if (
            type(features) is not list
            or len(features) != 10
            or any(
                type(item) not in (int, float) or not math.isfinite(float(item))
                for item in features
            )
        ):
            raise ValueError("factor-v2 parent base feature vector rejected")
        identities.append({"candidate_key": candidate_key, "signal_date": signal_date})
    ordered_rows = sorted(rows, key=lambda row: (row["signal_date"], row["candidate_key"]))
    if rows != ordered_rows:
        raise ValueError("factor-v2 parent full export order rejected")
    candidate_keys = sorted(row["candidate_key"] for row in ordered_rows)
    source_feature_projection = [
        {
            "candidate_key": row["candidate_key"],
            "features": row["features"],
            "signal_date": row["signal_date"],
        }
        for row in ordered_rows
    ]
    candidate_keys_sha256 = _canonical_sha256(candidate_keys)
    source_feature_projection_rows_sha256 = _canonical_sha256(source_feature_projection)
    full_export_rows_sha256 = _canonical_sha256(ordered_rows)
    if (
        payload.get("candidate_identity_root_sha256") != _canonical_sha256(identities)
        or expectation["common_eligible_candidate_keys_sha256"]
        != candidate_keys_sha256
        or expectation["common_eligible_source_feature_rows_sha256"]
        != source_feature_projection_rows_sha256
        or payload.get("rows_sha256") != full_export_rows_sha256
    ):
        raise ValueError("factor-v2 parent frozen identity roots rejected")
    derived_adapter = {
        "added_identity_field": "ts_code_from_candidate_key",
        "candidate_key_order": ["candidate_key"],
        "candidate_key_projection_fields": ["candidate_key"],
        "candidate_keys_sha256": candidate_keys_sha256,
        "full_export_fields": ["candidate_key", "features", "signal_date", "ts_code"],
        "full_export_order": ["signal_date", "candidate_key"],
        "full_export_rows_sha256": full_export_rows_sha256,
        "materializer_v1_hash_compatible": False,
        "pinned_parent_adapter_source_spec_sha256": pinned_source_sha256,
        "schema": "factor-v3-factor-v2-parent-row-derived-adapter/v1",
        "source_feature_order": ["signal_date", "candidate_key"],
        "source_feature_projection_fields": ["candidate_key", "signal_date", "features"],
        "source_feature_projection_rows_sha256": source_feature_projection_rows_sha256,
    }
    unsigned_receipt = {
        "authority_status": "PINNED_HASH_MATCH_ONLY_SOURCE_PRODUCER_UNVERIFIED",
        "candidate_keys_sha256": candidate_keys_sha256,
        "content_hash_bound": True,
        "development_only": True,
        "factor_v2_common_eligible_overlay_artifact_sha256": expectation[
            "factor_v2_common_eligible_overlay_artifact_sha256"
        ],
        "factor_v2_common_eligible_overlay_manifest_file_sha256": expectation[
            "factor_v2_common_eligible_overlay_manifest_file_sha256"
        ],
        "factor_v2_common_eligible_receipt_sha256": expectation[
            "factor_v2_common_eligible_receipt_sha256"
        ],
        "factor_v2_parent_artifact_sha256": expectation[
            "factor_v2_parent_artifact_sha256"
        ],
        "factor_v2_parent_manifest_file_sha256": expectation[
            "factor_v2_parent_manifest_file_sha256"
        ],
        "file_sha256": expected_raw,
        "full_export_rows_sha256": full_export_rows_sha256,
        "formal_materialization_eligible": False,
        "pinned_frozen_source_commit": FACTOR_V2_FROZEN_SOURCE_COMMIT,
        "pinned_frozen_source_tree_oid": FACTOR_V2_FROZEN_SOURCE_TREE_OID,
        "pinned_parent_adapter_source_spec_sha256": pinned_source_sha256,
        "points_contract_sha256": points.FACTOR_V3_POINTS_CONTRACT_SHA256,
        "public_verifier_replay_performed": False,
        "row_count": len(rows),
        "rows_sha256": full_export_rows_sha256,
        "schema": "factor-v2-common-eligible-parent-pinned-candidate/v1",
        "source_feature_projection_rows_sha256": (
            source_feature_projection_rows_sha256
        ),
        "source_provenance_verified": False,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    receipt = {
        **unsigned_receipt,
        "receipt_sha256": _canonical_sha256(unsigned_receipt),
    }
    return {
        "derived_adapter": derived_adapter,
        "file_sha256": expected_raw,
        "payload": payload,
        "receipt": receipt,
        "row_count": len(rows),
        "rows_sha256": full_export_rows_sha256,
        "source_provenance_verified": False,
    }


def _compact_date(value: str) -> str:
    return value.replace("-", "")


def _iso_date(value: str) -> str:
    if type(value) is not str or len(value) != 8 or not value.isdigit():
        raise ValueError("verified SQLite export date rejected")
    return _strict_date(
        f"{value[:4]}-{value[4:6]}-{value[6:]}",
        label="verified SQLite export date",
    )


def _read_membership_and_suspension_rows(
    *,
    feature_history_source_binding: Mapping[str, Any],
    audited_development_universe_sqlite_path: str | Path,
    expected_development_database_sha256: str,
    prewindow_dates: Sequence[str],
    development_dates: Sequence[str],
) -> dict[str, Any]:
    """Export immutable rows from the verified CAS and development databases."""

    binding = feature_history_source_binding
    if type(binding) is not dict or set(binding) != {
        "collection_publication_output_root",
        "manifest_relative_path",
        "manifest_sha256",
        "receipt",
    }:
        raise ValueError("feature history source binding fields rejected")
    publication_root = Path(binding["collection_publication_output_root"])
    _assert_safe_path(
        publication_root,
        label="feature history collection publication root",
        regular=False,
    )
    manifest_path = _safe_relative_path(
        publication_root,
        binding["manifest_relative_path"],
        label="feature history collection manifest",
    )
    manifest, manifest_raw = _read_canonical_json(
        manifest_path,
        label="feature history collection manifest",
    )
    if _sha256_bytes(manifest_raw) != _strict_sha256(
        binding["manifest_sha256"],
        label="feature history manifest SHA",
    ):
        raise ValueError("feature history collection manifest content address drifted")
    receipt = binding["receipt"]
    if type(receipt) is not dict:
        raise ValueError("feature history collection receipt rejected")
    snapshot_relative = manifest.get("snapshot_index_relative_path")
    snapshot_sha = _strict_sha256(
        manifest.get("snapshot_index_sha256"),
        label="feature history snapshot index SHA",
    )
    if snapshot_sha != receipt.get("snapshot_index_sha256"):
        raise ValueError("feature history snapshot index receipt binding drifted")
    snapshot_path = _safe_relative_path(
        publication_root,
        snapshot_relative,
        label="feature history snapshot index",
    )
    snapshot, snapshot_raw = _read_canonical_json(
        snapshot_path,
        label="feature history snapshot index",
    )
    if _sha256_bytes(snapshot_raw) != snapshot_sha:
        raise ValueError("feature history snapshot index content address drifted")
    database = snapshot.get("database")
    if (
        snapshot.get("schema")
        != "audited-pit-factor-v3-feature-history-snapshot-index/v1"
        or snapshot.get("session_count") != 250
        or snapshot.get("sessions_sha256") != _canonical_sha256(list(prewindow_dates))
        or type(database) is not dict
        or set(database) != {"bytes", "path", "sha256"}
        or database.get("path") != "metadata.sqlite3"
        or type(database.get("bytes")) is not int
        or database["bytes"] <= 0
    ):
        raise ValueError("feature history snapshot index fields rejected")
    database_sha = _strict_sha256(
        database.get("sha256"),
        label="feature history snapshot database SHA",
    )
    if database_sha != receipt.get("pit_store_database_sha256"):
        raise ValueError("feature history snapshot database receipt binding drifted")
    database_path = snapshot_path.parent / database["path"]
    exported = row_exporter.export_verified_factor_v3_rows(
        cas_snapshot_database_path=database_path,
        expected_cas_snapshot_database_sha256=database_sha,
        development_artifact_database_path=audited_development_universe_sqlite_path,
        expected_development_artifact_database_sha256=_strict_sha256(
            expected_development_database_sha256,
            label="development artifact database SHA",
        ),
        cas_snapshot_trade_dates=[_compact_date(item) for item in prewindow_dates],
        development_trade_dates=[_compact_date(item) for item in development_dates],
    )
    if (
        type(exported) is not dict
        or exported.get("schema") != "factor-v3-verified-row-export/v1"
        or exported.get("immutable_sqlite_contract") != "mode=ro&immutable=1"
        or type(exported.get("daily_universe")) is not dict
        or type(exported.get("suspension_events")) is not dict
    ):
        raise ValueError("verified immutable row export receipt rejected")
    membership = [
        {
            "list_date": _iso_date(row["list_date"]),
            "trade_date": _iso_date(row["trade_date"]),
            "ts_code": row["ts_code"],
        }
        for row in exported["daily_universe"].get("rows", [])
    ]
    suspensions = [
        {
            "suspend_type": row["suspend_type"],
            "trade_date": _iso_date(row["trade_date"]),
            "ts_code": row["ts_code"],
        }
        for row in exported["suspension_events"].get("rows", [])
    ]
    if (
        _canonical_sha256(exported["daily_universe"].get("rows", []))
        != exported["daily_universe"].get("rows_sha256")
        or _canonical_sha256(exported["suspension_events"].get("rows", []))
        != exported["suspension_events"].get("rows_sha256")
    ):
        raise ValueError("verified immutable row export roots rejected")
    return {"membership": membership, "suspensions": suspensions}


def _resolve_codes(
    codes: Sequence[str],
    *,
    trade_date: str,
    transition: legacy_daily.TransitionAuthority,
    label: str,
) -> dict[str, str]:
    filtered, identities, _excluded = legacy_daily._transition_filter_codes(
        tuple(sorted(codes)),
        trade_date=trade_date,
        transitions_by_code=transition.transitions_by_code,
        source_label=label,
    )
    return dict(zip(filtered, identities, strict=True))


def _previous_day(value: str) -> str:
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()


def _derive_rows(
    *,
    spec: Mapping[str, Any],
    source: legacy_daily.AuditedDailyAuthority,
    transition: legacy_daily.TransitionAuthority,
    source_dates: Sequence[str],
    prewindow: Sequence[str],
    development: Sequence[str],
    feature_history_source_binding: Mapping[str, Any],
    development_database_sha256: str,
) -> dict[str, Any]:
    refs = legacy_daily._validated_collection_refs(
        spec["daily_basic"]["collection_set_refs"],
        required_dates=[item.trade_date for item in source.partitions],
    )
    daily_rows: list[dict[str, Any]] = []
    cross_rows: list[dict[str, str]] = []
    observed_code_dates: dict[tuple[str, str], list[str]] = {}
    for index, (trade_date, authoritative, ref) in enumerate(
        zip(
            [item.trade_date for item in source.partitions],
            source.partitions,
            refs,
            strict=True,
        )
    ):
        partition = legacy_daily._load_daily_basic_partition(
            points_output_root=spec["daily_basic"]["points_output_root"],
            collection_ref=ref,
        )
        normalized = legacy_daily._validate_daily_basic_partition(
            partition,
            collection_ref=ref,
        )
        basic_identities = _resolve_codes(
            [row.ts_code for row in normalized],
            trade_date=trade_date,
            transition=transition,
            label="daily_basic export",
        )
        daily_identities = _resolve_codes(
            authoritative.ts_codes,
            trade_date=trade_date,
            transition=transition,
            label="authoritative daily export",
        )
        if set(basic_identities) != set(daily_identities):
            raise ValueError("daily-basic and daily cross-section exact set drifted")
        if index >= len(source_dates):
            continue
        for row in normalized:
            security_id = basic_identities.get(row.ts_code)
            if security_id is None or not row.research_scope_mainboard_chinext:
                continue
            payload = asdict(row)
            payload["security_id"] = security_id
            daily_rows.append(payload)
            observed_code_dates.setdefault((security_id, row.ts_code), []).append(trade_date)
        for code, security_id in sorted(daily_identities.items()):
            if not research_scope.is_mainboard_chinext_symbol(code):
                continue
            cross_rows.append(
                {"security_id": security_id, "trade_date": trade_date, "ts_code": code}
            )
    daily_rows.sort(key=lambda row: (row["trade_date"], row["security_id"], row["ts_code"]))
    cross_rows.sort(key=lambda row: (row["trade_date"], row["security_id"], row["ts_code"]))
    daily_keys = {(row["security_id"], row["trade_date"], row["ts_code"]) for row in daily_rows}
    cross_keys = {(row["security_id"], row["trade_date"], row["ts_code"]) for row in cross_rows}
    if len(daily_keys) != len(daily_rows) or len(cross_keys) != len(cross_rows):
        raise ValueError("daily source duplicate identity rejected")
    if daily_keys != cross_keys:
        raise ValueError("daily source exact target cross-section rejected")

    exported = _read_membership_and_suspension_rows(
        feature_history_source_binding=feature_history_source_binding,
        audited_development_universe_sqlite_path=spec["daily_basic"][
            "audited_development_universe_sqlite_path"
        ],
        expected_development_database_sha256=development_database_sha256,
        prewindow_dates=prewindow,
        development_dates=development,
    )
    if type(exported) is not dict or set(exported) != {"membership", "suspensions"}:
        raise ValueError("verified membership/suspension export rejected")
    membership = exported["membership"]
    suspensions = exported["suspensions"]
    if type(membership) is not list or type(suspensions) is not list:
        raise ValueError("verified membership/suspension rows rejected")
    listing_by_identity: dict[str, dict[str, str]] = {}
    membership_dates_by_code: dict[tuple[str, str, str], list[str]] = {}
    membership_seen: set[tuple[str, str]] = set()
    all_market_dates = set(prewindow) | set(development)
    ordered_market_dates = [*prewindow, *development]
    market_index = {trade_date: index for index, trade_date in enumerate(ordered_market_dates)}
    membership_by_date: dict[str, list[tuple[str, str]]] = {}
    raw_membership_seen: set[tuple[str, str]] = set()
    for row in membership:
        if type(row) is not dict or set(row) != {"list_date", "trade_date", "ts_code"}:
            raise ValueError("membership row fields rejected")
        trade_date = _strict_date(row["trade_date"], label="membership trade date")
        list_date = _strict_date(row["list_date"], label="membership list date")
        code = row["ts_code"]
        if trade_date not in all_market_dates or type(code) is not str:
            raise ValueError("membership row scope rejected")
        raw_key = (trade_date, code)
        if raw_key in raw_membership_seen:
            raise ValueError("membership duplicate source code rejected")
        raw_membership_seen.add(raw_key)
        membership_by_date.setdefault(trade_date, []).append((code, list_date))
    for trade_date in ordered_market_dates:
        dated_rows = membership_by_date.get(trade_date, [])
        if not dated_rows:
            continue
        identities = _resolve_codes(
            [code for code, _list_date in dated_rows],
            trade_date=trade_date,
            transition=transition,
            label="membership export",
        )
        for code, list_date in sorted(dated_rows):
            security_id = identities.get(code)
            if security_id is None or not research_scope.is_mainboard_chinext_symbol(code):
                continue
            key = (security_id, trade_date)
            if key in membership_seen:
                raise ValueError("membership duplicate identity rejected")
            membership_seen.add(key)
            membership_dates_by_code.setdefault(
                (security_id, code, list_date),
                [],
            ).append(trade_date)
            current = listing_by_identity.get(security_id)
            if current is None:
                listing_by_identity[security_id] = {
                    "listing_date": list_date,
                    "security_id": security_id,
                }
            elif current["listing_date"] != list_date:
                raise ValueError("listing date authority drifted")
            observed_code_dates.setdefault((security_id, code), []).append(trade_date)
    membership_snapshot_rows: list[dict[str, Any]] = []
    for (security_id, code, list_date), observed in sorted(
        membership_dates_by_code.items()
    ):
        ordered = sorted(set(observed), key=market_index.__getitem__)
        groups: list[list[str]] = []
        for trade_date in ordered:
            if (
                not groups
                or market_index[trade_date]
                != market_index[groups[-1][-1]] + 1
            ):
                groups.append([trade_date])
            else:
                groups[-1].append(trade_date)
        for group in groups:
            membership_snapshot_rows.append(
                {
                    "listing_date": list_date,
                    "membership_end": group[-1],
                    "membership_start": group[0],
                    "observed_session_count": len(group),
                    "observed_sessions_sha256": _canonical_sha256(group),
                    "security_id": security_id,
                    "ts_code": code,
                }
            )
    membership_snapshot_rows.sort(
        key=lambda row: (
            row["security_id"],
            row["membership_start"],
            row["ts_code"],
        )
    )
    if not {
        (row["security_id"], row["trade_date"]) for row in cross_rows
    }.issubset(membership_seen):
        raise ValueError("daily cross-section lacks exact daily membership")
    required_identities = {row["security_id"] for row in daily_rows}
    for parent_row in spec["factor_v2"].get("_parent_rows", []):
        parent_identity = _resolve_codes(
            [parent_row["ts_code"]],
            trade_date=parent_row["signal_date"],
            transition=transition,
            label="factor-v2 parent identity",
        ).get(parent_row["ts_code"])
        if parent_identity is None:
            raise ValueError("factor-v2 parent transition identity rejected")
        if (parent_identity, parent_row["signal_date"]) not in membership_seen:
            raise ValueError("factor-v2 parent signal-date membership is missing")
        required_identities.add(parent_identity)
        observed_code_dates.setdefault(
            (parent_identity, parent_row["ts_code"]),
            [],
        ).append(parent_row["signal_date"])
    if not required_identities.issubset(listing_by_identity):
        raise ValueError("listing membership is missing a required target identity")

    suspension_rows: list[dict[str, str]] = []
    suspension_seen: set[tuple[str, str]] = set()
    cross_pairs = {(row["security_id"], row["trade_date"]) for row in cross_rows}
    suspensions_by_date: dict[str, list[dict[str, str]]] = {}
    raw_suspension_seen: set[tuple[str, str]] = set()
    for row in suspensions:
        if type(row) is not dict or set(row) != {"suspend_type", "trade_date", "ts_code"}:
            raise ValueError("suspension row fields rejected")
        trade_date = _strict_date(row["trade_date"], label="suspension trade date")
        code = row["ts_code"]
        if trade_date not in all_market_dates or type(code) is not str:
            raise ValueError("suspension row scope rejected")
        if trade_date not in source_dates or row["suspend_type"] != "S":
            continue
        raw_key = (trade_date, code)
        if raw_key in raw_suspension_seen:
            raise ValueError("suspension duplicate source code rejected")
        raw_suspension_seen.add(raw_key)
        suspensions_by_date.setdefault(trade_date, []).append(
            {"suspend_type": "S", "trade_date": trade_date, "ts_code": code}
        )
    for trade_date in source_dates:
        dated_rows = suspensions_by_date.get(trade_date, [])
        if not dated_rows:
            continue
        identities = _resolve_codes(
            [row["ts_code"] for row in dated_rows],
            trade_date=trade_date,
            transition=transition,
            label="suspension export",
        )
        for row in sorted(dated_rows, key=lambda item: item["ts_code"]):
            code = row["ts_code"]
            security_id = identities.get(code)
            if security_id is None or not research_scope.is_mainboard_chinext_symbol(code):
                continue
            key = (security_id, trade_date)
            if key in suspension_seen or key in cross_pairs:
                raise ValueError("suspension duplicate or traded conflict rejected")
            suspension_seen.add(key)
            suspension_rows.append(
                {
                    "security_id": security_id,
                    "suspend_type": row["suspend_type"],
                    "trade_date": trade_date,
                    "ts_code": code,
                }
            )
    suspension_rows.sort(key=lambda row: (row["trade_date"], row["security_id"]))

    transition_rows: list[dict[str, str]] = []
    for (security_id, code), observed_dates in sorted(observed_code_dates.items()):
        if not research_scope.is_mainboard_chinext_symbol(code):
            continue
        descriptor = transition.transitions_by_code.get(code)
        listing = listing_by_identity.get(security_id)
        start = min(observed_dates)
        end = max(observed_dates)
        if listing is not None:
            start = min(start, listing["listing_date"])
        if descriptor is not None:
            effective = str(descriptor["effective_date"])
            if code == descriptor["predecessor_ts_code"]:
                end = min(end, _previous_day(effective))
            else:
                start = max(start, effective)
        transition_rows.append(
            {
                "effective_end": end,
                "effective_start": start,
                "security_id": security_id,
                "ts_code": code,
            }
        )
    return {
        "daily_rows": daily_rows,
        "cross_rows": cross_rows,
        "listing_rows": membership_snapshot_rows,
        "suspension_rows": suspension_rows,
        "transition_rows": transition_rows,
    }


def _candidate_producer_snapshot() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    paths = (
        Path(__file__),
        Path(points.__file__),
        Path(history_runner.__file__),
        Path(history_authority.__file__),
        Path(frozen_attestation.__file__),
        Path(daily_authority.__file__),
        Path(legacy_daily.__file__),
        Path(branch_selector.__file__),
        Path(research_scope.__file__),
        Path(row_exporter.__file__),
    )
    entries = []
    for path in paths:
        raw = _read_regular_bytes(path, label="producer source", max_bytes=16 * 1024 * 1024)
        entries.append(
            {
                "bytes": len(raw),
                "path": path.resolve().relative_to(repository_root).as_posix(),
                "sha256": _sha256_bytes(raw),
            }
        )
    identity = {
        "candidate_snapshot_only": True,
        "entries": entries,
        "schema": PRODUCER_SNAPSHOT_SCHEMA,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
    }
    return {**identity, "root_sha256": _canonical_sha256(identity)}


def _derived_adapter(
    *,
    daily_result: Mapping[str, Any],
    daily_publication: Mapping[str, Any],
    candidate_producer_snapshot_root: str,
) -> dict[str, Any]:
    return {
        "candidate_producer_snapshot_root_sha256": candidate_producer_snapshot_root,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
        "schema": DERIVED_ADAPTER_SCHEMA,
        "source_authority_root_sha256": daily_result["authority_root_sha256"],
        "source_publication_sha256": daily_publication.get("publication_sha256"),
        "source_receipt_sha256": daily_result["receipt_sha256"],
    }


def _build_snapshots(
    *,
    spec: dict[str, Any],
    source_spec_sha256: str,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    feature = spec["feature_history"]
    factor_v2 = spec["factor_v2"]
    run_spec = history_runner.load_factor_v3_feature_history_run_spec(
        feature["run_spec_path"]
    )
    history_authority.verify_factor_v3_feature_history_collection_plan(
        collection_plan=run_spec["collection_plan"],
        trade_cal_output_root=run_spec["trade_cal_output_root"],
        trade_cal_publication=run_spec["trade_cal_publication"],
        development_session_refs=run_spec["development_session_refs"],
        temporal_partition_contract=run_spec["temporal_partition_contract"],
    )
    attestation = frozen_attestation.verify_factor_v3_feature_history_frozen_source_attestation(
        attestation_path=feature["frozen_source_attestation_path"],
        expected_attestation_sha256=feature[
            "expected_frozen_source_attestation_sha256"
        ],
        frozen_source_root=feature["frozen_source_root"],
        expected_frozen_source_commit=feature["expected_frozen_source_commit"],
        feature_history_run_spec_path=feature["run_spec_path"],
        feature_history_run_root=feature["run_root"],
    )
    daily_kwargs = _daily_kwargs(spec)
    daily_result = daily_authority.verify_factor_v3_daily_basic_733_exact_set_coverage(
        **daily_kwargs
    )
    sessions = daily_result.get("trade_dates") if type(daily_result) is dict else None
    if (
        type(sessions) is not list
        or len(sessions) != 733
        or sessions != sorted(sessions)
        or len(set(sessions)) != 733
    ):
        raise ValueError("factor-v3 requires exact ordered 733 sessions")
    sessions = [_strict_date(item, label="factor-v3 market session") for item in sessions]
    prewindow = sessions[:250]
    development = sessions[250:]
    source_dates = sessions[:-1]
    if len(prewindow) != 250 or len(development) != 483 or len(source_dates) != 732:
        raise ValueError("factor-v3 250/483/733/732 calendar contract rejected")
    history_receipt, feature_history_source_binding = _validate_history_receipt(
        attestation,
        prewindow=prewindow,
    )

    publication = spec["daily_basic"]["publication"]
    receipt_relative = publication.get("receipt_relative_path")
    receipt_expected = publication.get("receipt_sha256")
    _strict_sha256(receipt_expected, label="factor-v3 733 receipt SHA")
    daily_root = Path(spec["daily_basic"]["authority_output_root"])
    _assert_safe_path(daily_root, label="factor-v3 733 authority root", regular=False)
    daily_receipt_path = _safe_relative_path(
        daily_root,
        receipt_relative,
        label="factor-v3 733 receipt",
    )
    daily_receipt_raw_value, daily_receipt_raw = _read_canonical_json(
        daily_receipt_path,
        label="factor-v3 733 authority receipt",
    )
    daily_receipt = _validate_daily_receipt(
        daily_receipt_raw_value,
        daily_result,
        raw_sha256=_sha256_bytes(daily_receipt_raw),
        sessions=sessions,
    )
    if _sha256_bytes(daily_receipt_raw) != receipt_expected:
        raise ValueError("factor-v3 733 authority status/content address drifted")

    source, source_identity = daily_authority._load_733_authority(**daily_kwargs)
    partitions = tuple(source.partitions)
    partition_dates = [item.trade_date for item in partitions]
    if (
        len(partitions) != 733
        or partition_dates != sessions
        or source_identity.get("session_count") != 733
        or source_identity.get("root_sha256")
        != spec["daily_basic"]["expected_source_authority_root_sha256"]
    ):
        raise ValueError("factor-v3 733 source authority session contract rejected")
    expectation = points.FACTOR_V3_POINTS_CONTRACT["preregistered_parent_expectation"]
    expected_sessions = expectation["sessions"]
    if (
        expected_sessions.get("count") != 483
        or expected_sessions.get("start") != development[0]
        or expected_sessions.get("end") != development[-1]
        or expected_sessions.get("sha256") != _canonical_sha256(development)
    ):
        raise ValueError("factor-v3 483 development sessions differ from frozen contract")

    branch = _validate_branch_receipt(
        branch_selector.build_factor_v2_decision_branch_receipt(
            factor_v2["decision_receipt_path"],
            expected_raw_file_sha256=factor_v2[
                "expected_decision_receipt_raw_file_sha256"
            ],
        ),
        expected_raw_sha256=factor_v2["expected_decision_receipt_raw_file_sha256"],
    )
    parent = validate_factor_v2_common_eligible_parent_hash_binding(
        parent_snapshot_path=factor_v2["parent_snapshot_path"],
        expected_parent_snapshot_file_sha256=factor_v2[
            "expected_parent_snapshot_file_sha256"
        ],
        parent_payload_fields=factor_v2["parent_payload_fields"],
        pinned_parent_adapter=factor_v2["pinned_parent_adapter"],
    )
    spec_with_parent = dict(spec)
    spec_with_parent["factor_v2"] = {
        **factor_v2,
        "_parent_rows": parent["payload"]["rows"],
    }
    transition = daily_authority._load_transition_authority(
        security_code_transition_evidence_root=spec["daily_basic"][
            "security_code_transition_evidence_root"
        ],
        expected_security_code_transition_contract_sha256=spec["daily_basic"][
            "expected_security_code_transition_contract_sha256"
        ],
    )
    derived_rows = _derive_rows(
        spec=spec_with_parent,
        source=source,
        transition=transition,
        source_dates=source_dates,
        prewindow=prewindow,
        development=development,
        feature_history_source_binding=feature_history_source_binding,
        development_database_sha256=_strict_sha256(
            source_identity["development_authority"]["sqlite_sha256"],
            label="development authority SQLite SHA",
        ),
    )
    producer_snapshot = _candidate_producer_snapshot()
    adapter = _derived_adapter(
        daily_result=daily_result,
        daily_publication=publication,
        candidate_producer_snapshot_root=producer_snapshot["root_sha256"],
    )
    per_date = []
    totals = {name: 0 for name in _UPSTREAM_SEGMENTS}
    for entry in daily_receipt["per_date_statistics"][:-1]:
        counts = {name: int(entry["daily_basic_raw_segment_counts"][name]) for name in _UPSTREAM_SEGMENTS}
        for name, count in counts.items():
            totals[name] += count
        per_date.append(
            {
                "normalized_rows_sha256": entry[
                    "daily_basic_canonical_rows_sha256"
                ],
                "raw_source_rows_sha256": entry[
                    "authoritative_daily_raw_codes_sha256"
                ],
                "segment_counts": counts,
                "trade_date": entry["trade_date"],
            }
        )
    calendar = {
        "all_market_session_count": 733,
        "all_market_sessions": sessions,
        "all_market_sessions_sha256": _canonical_sha256(sessions),
        "development_session_count": 483,
        "development_sessions": development,
        "development_sessions_sha256": _canonical_sha256(development),
        "prewindow_session_count": 250,
        "prewindow_sessions": prewindow,
        "prewindow_sessions_sha256": _canonical_sha256(prewindow),
        "schema": "factor-v3-development-calendar-snapshot/v2",
        "source_date_count": 732,
        "source_dates": source_dates,
        "source_dates_sha256": _canonical_sha256(source_dates),
    }
    history_projection = _redacted_receipt_projection(
        feature_history_source_binding["receipt"],
        self_hash_field="receipt_sha256",
        source_hash_field="source_receipt_sha256",
    )
    daily_projection = _redacted_receipt_projection(
        daily_receipt_raw_value,
        self_hash_field="authority_root_sha256",
        source_hash_field="source_authority_root_sha256",
    )
    snapshots: dict[str, dict[str, Any]] = {
        "calendar": calendar,
        "factor_v2_parent": {
            "derived_adapter": parent["derived_adapter"],
            "parent_hash_binding_receipt": parent["receipt"],
            "schema": "factor-v3-development-factor-v2-parent-snapshot/v2",
            **parent["payload"],
            "source_file_sha256": parent["file_sha256"],
            "validator": "validate_factor_v2_common_eligible_parent_hash_binding",
        },
        "daily_basic": {
            "derived_adapter": adapter,
            "rows": derived_rows["daily_rows"],
            "rows_sha256": _canonical_sha256(derived_rows["daily_rows"]),
            "schema": "factor-v3-development-daily-basic-snapshot/v2",
            "source_dates": source_dates,
            "source_dates_sha256": _canonical_sha256(source_dates),
        },
        "daily_traded_cross_section": {
            "derived_adapter": adapter,
            "rows": derived_rows["cross_rows"],
            "rows_sha256": _canonical_sha256(derived_rows["cross_rows"]),
            "schema": "factor-v3-development-daily-cross-section-snapshot/v2",
            "source_dates_sha256": _canonical_sha256(source_dates),
        },
        "listing_membership": {
            "interval_semantics": "contiguous_verified_market_session_membership_only",
            "market_sessions_sha256": _canonical_sha256(sessions),
            "rows": derived_rows["listing_rows"],
            "rows_sha256": _canonical_sha256(derived_rows["listing_rows"]),
            "schema": "factor-v3-development-listing-membership-snapshot/v2",
        },
        "suspensions": {
            "rows": derived_rows["suspension_rows"],
            "rows_sha256": _canonical_sha256(derived_rows["suspension_rows"]),
            "schema": "factor-v3-development-suspension-snapshot/v2",
        },
        "security_code_transitions": {
            "contract_sha256": transition.contract_sha256,
            "evidence_receipt_sha256": transition.evidence_receipt_sha256,
            "rows": derived_rows["transition_rows"],
            "rows_sha256": _canonical_sha256(derived_rows["transition_rows"]),
            "schema": "factor-v3-development-security-transition-snapshot/v2",
        },
        "upstream_board_ledger": {
            "derived_adapter": adapter,
            "downstream_scope_filter": "mainboard_chinext_candidate_join_only",
            "per_date": per_date,
            "per_date_board_ledger_root_sha256": _canonical_sha256(per_date),
            "pre_filter_segment_counts": totals,
            "preserved_before_target_scope_filter": True,
            "schema": "factor-v3-development-upstream-board-ledger/v2",
            "source_segments": list(_UPSTREAM_SEGMENTS),
        },
        "factor_v2_evaluation": {
            "branch_receipt_sha256": branch["receipt_sha256"],
            "decision_receipt_raw_file_sha256": branch[
                "source_decision_receipt_raw_file_sha256"
            ],
            "decision_receipt_sha256": branch[
                "source_decision_receipt_sha256"
            ],
            "evaluation_artifact_sha256": branch["evaluation_artifact_sha256"],
            "schema": "factor-v3-development-factor-v2-evaluation-snapshot/v2",
            "selected_branch": branch["selected_branch"],
        },
        "feature_history_receipt": {
            "schema": "factor-v3-development-feature-history-receipt-snapshot/v2",
            **history_projection,
        },
        "daily_basic_exact_set_receipt": {
            "derived_adapter": adapter,
            "schema": "factor-v3-development-daily-basic-receipt-snapshot/v2",
            **daily_projection,
        },
    }
    for name, payload in snapshots.items():
        if name not in _SNAPSHOT_NAMES:
            raise ValueError("snapshot name rejected")
        _assert_no_sensitive(payload, label=f"{name} snapshot")
    context = {
        "branch": branch,
        "calendar": calendar,
        "daily_receipt": daily_receipt,
        "daily_result": daily_result,
        "history_receipt": history_receipt,
        "parent": parent,
        "producer_snapshot": producer_snapshot,
        "source_spec_sha256": source_spec_sha256,
    }
    return snapshots, context


def _candidate(
    *,
    source_spec_path: str | Path,
    expected_source_spec_sha256: str,
) -> dict[str, Any]:
    source_path, spec, source_sha = _load_source_spec(
        source_spec_path,
        expected_sha256=expected_source_spec_sha256,
    )
    snapshots, context = _build_snapshots(spec=spec, source_spec_sha256=source_sha)
    snapshot_descriptors = {}
    for name in _SNAPSHOT_NAMES:
        payload = snapshots[name]
        raw = _canonical_bytes(payload)
        rows = payload.get("rows")
        snapshot_descriptors[name] = {
            "file_sha256": _sha256_bytes(raw),
            "payload_root_sha256": _canonical_sha256(payload),
            "relative_path": f"{name}.json",
            "row_count": len(rows) if type(rows) is list else None,
            "rows_sha256": payload.get("rows_sha256"),
            "schema": payload["schema"],
        }
    calendar = context["calendar"]
    descriptor_calendar = {
        key: calendar[key]
        for key in (
            "all_market_session_count",
            "all_market_sessions_sha256",
            "development_session_count",
            "development_sessions_sha256",
            "prewindow_session_count",
            "prewindow_sessions_sha256",
            "source_date_count",
            "source_dates_sha256",
        )
    }
    history_summary = {
        key: context["history_receipt"][key]
        for key in (
            "authority_status",
            "feature_history_only",
            "receipt_sha256",
            "schema_version",
            "session_count",
            "sessions_sha256",
            "source_authority_root_sha256",
            "verified",
        )
    }
    daily_summary = {
        key: context["daily_receipt"][key]
        for key in (
            "authority_root_sha256",
            "authority_scope",
            "authority_status",
            "normalized_daily_basic_row_authority_root_sha256",
            "row_authority_status",
            "schema",
            "trade_date_count",
            "trade_dates_sha256",
        )
    }
    unsigned = {
        "authority_status": AUTHORITY_STATUS,
        "calendar": descriptor_calendar,
        "development_only": True,
        "factor_v2_branch": context["branch"],
        "formal_materialization_eligible": False,
        "parent_source_authority_verified": False,
        "points_contract_sha256": points.FACTOR_V3_POINTS_CONTRACT_SHA256,
        "producer_snapshot": context["producer_snapshot"],
        "schema": DESCRIPTOR_SCHEMA,
        "snapshots": snapshot_descriptors,
        "source_receipts": {
            "daily_basic": daily_summary,
            "feature_history": history_summary,
        },
        "source_spec_sha256": source_sha,
        "source_authority_complete": False,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    descriptor = {**unsigned, "authority_root_sha256": _canonical_sha256(unsigned)}
    _assert_no_sensitive(descriptor, label="input authority descriptor")
    if _sha256_bytes(
        _read_regular_bytes(source_path, label="source spec postverification")
    ) != source_sha:
        raise ValueError("source spec changed during authority derivation")
    return {"descriptor": descriptor, "snapshots": snapshots}


def _descriptor_relative_path(authority_root: str) -> str:
    return (
        "factor_v3_development_input_authorities/sha256/"
        f"{authority_root[:2]}/{authority_root}/input-authority.json"
    )


def _publication_relative_path(publication_sha: str) -> str:
    return (
        "factor_v3_development_input_publications/sha256/"
        f"{publication_sha[:2]}/{publication_sha}.json"
    )


def _create_or_verify(path: Path, raw: bytes, *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = _read_regular_bytes(path, label=label)
        if existing != raw:
            raise ValueError(f"{label} existing content drifted")
        return
    temporary = path.with_name(f".{path.name}.{os.getpid()}.partial")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    try:
        descriptor = os.open(str(temporary), flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, path)
    except FileExistsError:
        if not path.exists() or _read_regular_bytes(path, label=label) != raw:
            raise ValueError(f"{label} concurrent publication drifted") from None
    finally:
        temporary.unlink(missing_ok=True)
    if _read_regular_bytes(path, label=label) != raw:
        raise ValueError(f"{label} publication postverification failed")


def _validated_publication(value: Any) -> dict[str, Any]:
    publication = _assert_fields(value, _PUBLICATION_FIELDS, label="input authority publication")
    if publication.get("schema") != PUBLICATION_SCHEMA:
        raise ValueError("input authority publication schema rejected")
    authority_root = _strict_sha256(publication["authority_root_sha256"], label="authority root")
    descriptor_sha = _strict_sha256(publication["descriptor_sha256"], label="descriptor SHA")
    publication_sha = _strict_sha256(publication["publication_sha256"], label="publication SHA")
    if publication["descriptor_relative_path"] != _descriptor_relative_path(authority_root):
        raise ValueError("input authority descriptor path rejected")
    if publication["publication_relative_path"] != _publication_relative_path(publication_sha):
        raise ValueError("input authority publication path rejected")
    _strict_sha256(descriptor_sha, label="descriptor SHA")
    return publication


def publish_factor_v3_development_input_authority(
    *,
    source_spec_path: str | Path,
    expected_source_spec_sha256: str,
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish one development-only, content-addressed input snapshot set."""

    candidate = _candidate(
        source_spec_path=source_spec_path,
        expected_source_spec_sha256=expected_source_spec_sha256,
    )
    descriptor = candidate["descriptor"]
    authority_root = descriptor["authority_root_sha256"]
    root = Path(output_root)
    if not root.is_absolute():
        raise ValueError("input authority output root must be absolute")
    root.mkdir(parents=True, exist_ok=True)
    _assert_safe_path(root, label="input authority output root", regular=False)
    descriptor_relative = _descriptor_relative_path(authority_root)
    descriptor_path = _safe_relative_path(root, descriptor_relative, label="input authority descriptor")
    snapshot_root = descriptor_path.parent
    snapshot_root.mkdir(parents=True, exist_ok=True)
    for name in _SNAPSHOT_NAMES:
        _create_or_verify(
            snapshot_root / f"{name}.json",
            _canonical_bytes(candidate["snapshots"][name]),
            label=f"{name} snapshot",
        )
    descriptor_raw = _canonical_bytes(descriptor)
    _create_or_verify(descriptor_path, descriptor_raw, label="input authority descriptor")
    descriptor_sha = _sha256_bytes(descriptor_raw)
    manifest = {
        "authority_root_sha256": authority_root,
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": descriptor_sha,
        "development_only": True,
        "parent_source_authority_verified": False,
        "producer_snapshot_root_sha256": descriptor["producer_snapshot"]["root_sha256"],
        "schema": PUBLICATION_MANIFEST_SCHEMA,
        "source_spec_sha256": descriptor["source_spec_sha256"],
        "source_authority_complete": False,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    manifest_raw = _canonical_bytes(manifest)
    manifest_sha = _sha256_bytes(manifest_raw)
    publication_relative = _publication_relative_path(manifest_sha)
    publication_path = _safe_relative_path(root, publication_relative, label="input authority publication")
    _create_or_verify(publication_path, manifest_raw, label="input authority publication")
    publication = {
        "authority_root_sha256": authority_root,
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": descriptor_sha,
        "publication_relative_path": publication_relative,
        "publication_sha256": manifest_sha,
        "schema": PUBLICATION_SCHEMA,
    }
    verify_factor_v3_development_input_authority(
        source_spec_path=source_spec_path,
        expected_source_spec_sha256=expected_source_spec_sha256,
        output_root=root,
        publication=publication,
    )
    return publication


def verify_factor_v3_development_input_authority(
    *,
    source_spec_path: str | Path,
    expected_source_spec_sha256: str,
    output_root: str | Path,
    publication: Mapping[str, Any],
) -> dict[str, Any]:
    """Replay the available upstream verifiers and every candidate snapshot byte."""

    public = _validated_publication(dict(publication))
    root = Path(output_root)
    _assert_safe_path(root, label="input authority output root", regular=False)
    expected = _candidate(
        source_spec_path=source_spec_path,
        expected_source_spec_sha256=expected_source_spec_sha256,
    )
    descriptor = expected["descriptor"]
    if descriptor["authority_root_sha256"] != public["authority_root_sha256"]:
        raise ValueError("input authority source replay root drifted")
    descriptor_path = _safe_relative_path(
        root,
        public["descriptor_relative_path"],
        label="input authority descriptor",
    )
    observed_descriptor, descriptor_raw = _read_canonical_json(
        descriptor_path,
        label="input authority descriptor",
    )
    if (
        _sha256_bytes(descriptor_raw) != public["descriptor_sha256"]
        or observed_descriptor != descriptor
    ):
        raise ValueError("input authority descriptor content address drifted")
    snapshot_root = descriptor_path.parent
    expected_names = {"input-authority.json", *(f"{name}.json" for name in _SNAPSHOT_NAMES)}
    actual_names: set[str] = set()
    for member in snapshot_root.iterdir():
        if _is_reparse_point(member):
            raise ValueError("input authority snapshot reparse point rejected")
        if not member.is_file():
            raise ValueError("input authority snapshot contains unbound extra member")
        actual_names.add(member.name)
    if actual_names != expected_names:
        missing = expected_names - actual_names
        extra = actual_names - expected_names
        if missing:
            raise ValueError("input authority snapshot missing member")
        raise ValueError(f"input authority snapshot unbound extra member: {sorted(extra)}")
    for name in _SNAPSHOT_NAMES:
        path = snapshot_root / f"{name}.json"
        payload, raw = _read_canonical_json(path, label=f"{name} snapshot")
        source_descriptor = descriptor["snapshots"][name]
        if (
            payload != expected["snapshots"][name]
            or _sha256_bytes(raw) != source_descriptor["file_sha256"]
            or _canonical_sha256(payload) != source_descriptor["payload_root_sha256"]
        ):
            raise ValueError(f"{name} snapshot content address drifted")
        _assert_no_sensitive(payload, label=f"{name} snapshot")
    manifest_path = _safe_relative_path(
        root,
        public["publication_relative_path"],
        label="input authority publication",
    )
    manifest, manifest_raw = _read_canonical_json(
        manifest_path,
        label="input authority publication",
    )
    expected_manifest = {
        "authority_root_sha256": descriptor["authority_root_sha256"],
        "descriptor_relative_path": public["descriptor_relative_path"],
        "descriptor_sha256": public["descriptor_sha256"],
        "development_only": True,
        "parent_source_authority_verified": False,
        "producer_snapshot_root_sha256": descriptor["producer_snapshot"]["root_sha256"],
        "schema": PUBLICATION_MANIFEST_SCHEMA,
        "source_spec_sha256": descriptor["source_spec_sha256"],
        "source_authority_complete": False,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    if (
        _sha256_bytes(manifest_raw) != public["publication_sha256"]
        or manifest != expected_manifest
    ):
        raise ValueError("input authority publication content address drifted")
    return {
        "authority_root_sha256": descriptor["authority_root_sha256"],
        "candidate_verified": True,
        "descriptor_sha256": public["descriptor_sha256"],
        "development_only": True,
        "formal_materialization_eligible": False,
        "parent_source_authority_verified": False,
        "source_authority_complete": False,
        "source_date_count": descriptor["calendar"]["source_date_count"],
        "verified": False,
        **{field: False for field in PROVENANCE_FALSE_FIELDS},
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }


__all__ = (
    "AUTHORITY_STATUS",
    "DERIVED_ADAPTER_SCHEMA",
    "DESCRIPTOR_SCHEMA",
    "FACTOR_V2_FROZEN_SOURCE_BLOB_SHA256",
    "FACTOR_V2_FROZEN_SOURCE_COMMIT",
    "FACTOR_V2_FROZEN_SOURCE_TREE_OID",
    "FACTOR_V2_PARENT_SUBPROCESS_CONTRACT",
    "PRODUCER_SNAPSHOT_SCHEMA",
    "PROVENANCE_FALSE_FIELDS",
    "PUBLICATION_SCHEMA",
    "SAFETY_FALSE_FIELDS",
    "publish_factor_v3_development_input_authority",
    "validate_factor_v2_common_eligible_parent_hash_binding",
    "verify_factor_v3_development_input_authority",
)
