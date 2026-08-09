"""Frozen input replay used only by the independent activation verifier."""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping

from app import factor_v2_decision_branch_selector as factor_v2_branch_selector
from app import audited_pit_factor_v3_points_contract as points


DISPOSABLE_TEST_AUTHORITY_SCOPE = "DISPOSABLE_TEST_FIXTURE_ONLY"
CANDIDATE_PUBLICATION_SCHEMA = (
    "factor-v3-development-input-authority-publication-manifest/v3"
)
CANDIDATE_DESCRIPTOR_SCHEMA = "factor-v3-development-input-authority/v3"
CANDIDATE_AUTHORITY_STATUS = "UNVERIFIED_PRODUCER_DEVELOPMENT_INPUT_CANDIDATE"
CANDIDATE_PRODUCER_SCHEMA = (
    "factor-v3-development-input-candidate-producer-snapshot/v1"
)
CANDIDATE_PROVENANCE_FALSE_FIELDS = (
    "git_executable_verified",
    "git_tree_blob_binding_verified",
    "loaded_source_identity_verified",
    "producer_binding_verified",
)
CANDIDATE_SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "final_oos_consumed",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
)
CANDIDATE_SNAPSHOT_NAMES = (
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
_CANDIDATE_PRODUCER_PATHS = (
    "app/factor_v3_development_input_authority.py",
    "app/audited_pit_factor_v3_points_contract.py",
    "app/factor_v3_feature_history_runner.py",
    "app/audited_pit_factor_v3_feature_history_authority.py",
    "app/factor_v3_feature_history_frozen_source_attestation.py",
    "app/factor_v3_daily_basic_733_exact_set_authority.py",
    "app/jiaoch_daily_basic_exact_set_authority.py",
    "app/factor_v2_decision_branch_selector.py",
    "app/research_scope.py",
    "app/factor_v3_verified_row_exporter.py",
)
_SENSITIVE_TERMS = (
    "api_key", "authorization", "capability", "credential", "password",
    "secret", "token",
)
_ALLOWED_SYMBOL_PREFIXES = (
    "000", "001", "002", "003", "300", "301", "302", "600", "601",
    "603", "605",
)
_FROZEN_FEATURE_FIELDS = {
    "authority_manifest_relative_path", "authority_manifest_sha256",
    "feature_run_root", "feature_run_spec_file_sha256",
    "feature_run_spec_path", "feature_run_spec_sha256",
    "pit_store_database_sha256", "publication_capability_sha256",
    "publication_issuance_relative_path", "publication_issuance_sha256",
    "receipt_sha256", "session_count", "sessions_sha256",
    "snapshot_index_sha256", "source_authority_root_sha256",
}
_DAILY_BASIC_V2_RECEIPT_FIELDS = {
    "all_supported_segments_compared_before_scope_filter",
    "arbitrary_row_drops_permitted",
    "audited_daily_authority",
    "authority_root_sha256",
    "authority_scope",
    "authority_status",
    "embargo_consumed",
    "exact_set_verified",
    "factor_v3_development_materialization_input_eligible",
    "factor_v3_target_identity_root_sha256",
    "factor_v3_target_scope",
    "final_oos_consumed",
    "formal_factor_v3_materialization_performed",
    "normalized_daily_basic_row_authority_root_sha256",
    "per_date_statistics",
    "per_date_statistics_sha256",
    "producer_binding",
    "production_profile_registered",
    "production_recommendation_eligible",
    "publication_capability_sha256",
    "raw_source_rows_bound",
    "row_authority_status",
    "rows_published",
    "schema",
    "security_code_transition_authority",
    "silent_row_drops_permitted",
    "source_binding_root_sha256",
    "source_missingness",
    "source_ts_code_exact_set_verified_after_transition_filter",
    "trade_date_count",
    "trade_dates",
    "trade_dates_sha256",
    "transition_boundary_authority_root_sha256",
    "transition_boundary_count",
    "transition_overlap_authority_root_sha256",
    "transition_resolved_identity_exact_set_verified",
}
PARENT_PROJECTION_SCHEMA = "factor-v3-parent-row-projection-roots/v1"
PARENT_PROJECTION_FIELDS = (
    "candidate_keys_sha256",
    "source_feature_projection_rows_sha256",
    "full_export_rows_sha256",
)
PARENT_PROJECTION_CONTRACT = {
    "candidate_keys": {"fields": ["candidate_key"], "order": ["candidate_key"]},
    "source_feature_projection_rows": {
        "fields": ["candidate_key", "signal_date", "features"],
        "order": ["signal_date", "candidate_key"],
    },
    "full_export_rows": {
        "fields": ["candidate_key", "features", "signal_date", "ts_code"],
        "order": ["signal_date", "candidate_key"],
    },
}
FACTOR_V2_EVALUATION_PROJECTION_FIELDS = (
    "terminal_decision_descriptor_sha256",
    "evaluator_descriptor_sha256",
    "cost_slippage_execution_descriptor_sha256",
)
FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS = (
    "branch_receipt_sha256",
    "decision_receipt_sha256",
    "decision_receipt_raw_file_sha256",
    "evaluation_artifact_sha256",
    "selected_branch",
    "snapshot_schema",
)
FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA = (
    "factor-v2-terminal-evaluator-formal-development-authority-receipt/v1"
)
DISPOSABLE_PARENT_SOURCE_RECEIPT_SCHEMA = (
    "factor-v3-parent-source-disposable-contract-receipt/v1"
)
DISPOSABLE_EVALUATION_RECEIPT_SCHEMA = (
    "factor-v2-evaluation-disposable-contract-receipt/v1"
)
FACTOR_V2_EVALUATION_PROJECTION_SCHEMA = (
    "factor-v2-formal-development-evaluation-projection/v1"
)
POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-points-contract-authority-verdict/v1"
)
FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-feature-history-native-authority-verdict/v1"
)
DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-daily-basic-native-authority-verdict/v1"
)
PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-parent-source-native-authority-verdict/v1"
)
FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v2-evaluation-native-authority-verdict/v1"
)
PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION = (
    "factor-v3-parent-source-machine-global-root-lease/v1"
)
PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE = "attempts/sha256/{prefix}/{attempt_key}"
PARENT_SOURCE_EPOCH_FILE_CONTRACT = {
    "run.claim.json": {
        "action": 1,
        "fields": (
            "action", "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-run-claim/v1",
        "state": 1,
    },
    "run.receipt.json": {
        "fields": (
            "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_claim_sha256", "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-run-receipt/v1",
        "state": 2,
    },
    "verify.claim.json": {
        "action": 2,
        "fields": (
            "action", "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_receipt_sha256", "run_spec_sha256", "schema", "state",
        ),
        "schema": "factor-v3-parent-source-root-verify-claim/v1",
        "state": 3,
    },
    "terminal.receipt.json": {
        "fields": (
            "attempt_key_sha256", "global_attempt_identity_sha256",
            "run_receipt_sha256", "run_spec_sha256", "schema", "state",
            "verify_claim_sha256",
        ),
        "schema": "factor-v3-parent-source-root-terminal-receipt/v1",
        "state": 4,
    },
}
PARENT_SOURCE_EPOCH_FILE_NAMES = tuple(PARENT_SOURCE_EPOCH_FILE_CONTRACT)
SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible", "embargo_consumed", "experiment_launch_eligible",
    "final_oos_consumed", "formal_materialization_performed", "model_training_started",
    "oof_scoring_started", "orders_submitted", "production_profile_registered",
    "production_recommendation_eligible", "recommendation_generation_eligible",
)
UPSTREAM_SOURCE_SEGMENTS = (
    "BSE", "SSE_MAIN", "SSE_STAR", "SZSE_CHINEXT", "SZSE_MAIN",
)
ACTIVATION_INPUT_BINDING_FIELDS = (
    "attempt_key_sha256", "candidate_authority_root_sha256",
    "candidate_descriptor_sha256", "candidate_publication_file_sha256",
    "daily_basic_authority_receipt_file_sha256", "daily_basic_authority_receipt_path",
    "factor_v2_evaluation_authority_receipt_file_sha256",
    "factor_v2_evaluation_authority_receipt_root_sha256",
    "feature_history_collection_issuance_file_sha256",
    "feature_history_collection_issuance_path",
    "feature_history_frozen_attestation_file_sha256",
    "feature_history_frozen_attestation_path", "global_attempt_identity_sha256",
    "global_attempt_ledger_root", "global_run_claim_path", "global_run_receipt_path",
    "global_terminal_receipt_path", "global_verify_claim_path",
    "native_lease_identity_sha256", "native_lease_policy_version",
    "parent_source_authority_receipt_file_sha256",
    "parent_source_authority_receipt_root_sha256",
    "parent_source_terminal_receipt_file_sha256", "run_spec_sha256",
    "semantic_input_root_sha256",
)
_DISPOSABLE_PARENT_RECEIPT_FIELDS = {
    "approved_transition", "attempt_key_sha256", "authority_status",
    "calendar_projection", "contract_binding_validated", "development_only",
    "formal_materialization_eligible", "global_attempt_identity_sha256",
    "global_attempt_ledger_root", "global_run_claim_path",
    "global_run_receipt_path", "global_terminal_receipt_path",
    "global_verify_claim_path", "independent_public_replay_performed",
    "machine_global_root_lease_verified", "native_lease_identity_sha256",
    "native_lease_policy_version", "observed_root_state", "parent_projection",
    "parent_source_authority_verified",
    "points_contract_common_eligible_projection", "receipt_root_sha256",
    "requested_action", "root_epoch_terminal_verified", "run_claim_sha256",
    "run_receipt_sha256", "run_spec_sha256", "schema",
    "semantic_input_root_sha256", "single_attempt_verified",
    "source_authority_complete", "source_authority_verified",
    "terminal_receipt_file_sha256", "terminal_root_state", "test_fixture_only",
    "upstream_board_projection", "verified", "verify_claim_sha256",
    *SAFETY_FALSE_FIELDS,
}
_DISPOSABLE_EVALUATION_RECEIPT_FIELDS = {
    "authority_status", "candidate_evaluation_binding",
    "contract_binding_validated", "development_only", "evaluation_projection",
    "formal_materialization_eligible", "independent_public_replay_performed",
    "publisher_terminal_chain_verified", "receipt_root_sha256", "schema",
    "source_authority_complete", "test_fixture_only", "verified",
    *SAFETY_FALSE_FIELDS,
}


class IndependentCoreError(ValueError):
    pass


def _fail(message: str) -> None:
    raise IndependentCoreError(message)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value: Any) -> str:
    return _sha_bytes(_canonical_bytes(value))


def _strict_sha(value: Any, *, label: str) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
        or value == "0" * 64
    ):
        _fail(f"{label} rejected")
    return value


def _fields(value: Any, expected: set[str] | frozenset[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        _fail(f"{label} fields rejected")
    return value


def _false(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            _fail(f"{label} {field} must remain false")


def _true(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not True:
            _fail(f"{label} {field} must be true")


def _path_key(path: Path) -> str:
    return os.path.normcase(str(path))


def _is_reparse(path: Path) -> bool:
    info = os.lstat(path)
    attributes = getattr(info, "st_file_attributes", 0)
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & flag)


def _existing(path: Path, *, label: str, directory: bool | None = None) -> Path:
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    try:
        canonical = path.resolve(strict=True)
    except OSError as exc:
        raise IndependentCoreError(f"{label} unavailable") from exc
    if _path_key(path) != _path_key(canonical):
        _fail(f"{label} must be canonical")
    cursor = path
    while True:
        if _is_reparse(cursor):
            _fail(f"{label} reparse path rejected")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    if directory is True and not path.is_dir():
        _fail(f"{label} directory required")
    if directory is False and not path.is_file():
        _fail(f"{label} regular file required")
    return path


def _read(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    _existing(path, label=label, directory=False)
    raw = path.read_bytes()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise IndependentCoreError(f"{label} JSON rejected") from exc
    if type(payload) is not dict or _canonical_bytes(payload) != raw:
        _fail(f"{label} canonical JSON rejected")
    return payload, raw


def _cas(
    path_value: str | Path,
    expected_sha_value: str,
    *,
    category: str,
    label: str,
) -> tuple[Path, dict[str, Any], bytes, str]:
    path = _existing(Path(path_value), label=label, directory=False)
    expected_sha = _strict_sha(expected_sha_value, label=f"expected {label} SHA")
    if (
        path.name != f"{expected_sha}.json"
        or path.parent.name != expected_sha[:2]
        or path.parent.parent.name != "sha256"
        or path.parent.parent.parent.name != category
    ):
        _fail(f"{label} CAS path rejected")
    payload, raw = _read(path, label=label)
    if _sha_bytes(raw) != expected_sha:
        _fail(f"{label} SHA mismatch")
    return path, payload, raw, expected_sha


def _relative(root: Path, value: Any, *, label: str) -> Path:
    if type(value) is not str or not value or "\\" in value:
        _fail(f"{label} relative path rejected")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        _fail(f"{label} relative path rejected")
    return _existing(root.joinpath(*parts), label=label, directory=False)


def _self_hash(payload: Mapping[str, Any], *, label: str) -> str:
    unsigned = dict(payload)
    root = _strict_sha(unsigned.pop("receipt_root_sha256", None), label=f"{label} root")
    if _sha(unsigned) != root:
        _fail(f"{label} self hash rejected")
    return root


def _test_verdict(evidence: Mapping[str, Any], schema: str) -> dict[str, Any]:
    return {
        **deepcopy(dict(evidence)),
        "authority_scope": DISPOSABLE_TEST_AUTHORITY_SCOPE,
        "contract_binding_validated": True,
        "schema": schema,
        "test_fixture_only": True,
        "verified": False,
    }


def _candidate_descriptor_relative_path(root: str) -> str:
    return (
        "factor_v3_development_input_authorities/sha256/"
        f"{root[:2]}/{root}/input-authority.json"
    )


def _candidate_publication_relative_path(root: str) -> str:
    return (
        "factor_v3_development_input_publications/sha256/"
        f"{root[:2]}/{root}.json"
    )


def _candidate_producer_snapshot() -> dict[str, Any]:
    repository_root = Path(__file__).resolve().parents[1]
    entries = []
    for relative in _CANDIDATE_PRODUCER_PATHS:
        raw = (repository_root / relative).read_bytes()
        entries.append(
            {"bytes": len(raw), "path": relative, "sha256": _sha_bytes(raw)}
        )
    identity = {
        "candidate_snapshot_only": True,
        "entries": entries,
        "schema": CANDIDATE_PRODUCER_SCHEMA,
        **{field: False for field in CANDIDATE_PROVENANCE_FALSE_FIELDS},
    }
    return {**identity, "root_sha256": _sha(identity)}


def _assert_no_sensitive(value: Any, *, label: str) -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if type(key) is not str or any(
                term in key.lower() for term in _SENSITIVE_TERMS
            ):
                _fail(f"{label} contains sensitive fields")
            _assert_no_sensitive(nested, label=label)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_sensitive(nested, label=label)


def _project_public(value: Mapping[str, Any]) -> tuple[dict[str, Any], int]:
    redacted = 0

    def visit(nested: Any) -> Any:
        nonlocal redacted
        if isinstance(nested, Mapping):
            result = {}
            for key, item in nested.items():
                if any(term in key.lower() for term in _SENSITIVE_TERMS):
                    redacted += 1
                    continue
                result[key] = visit(item)
            return result
        if isinstance(nested, list):
            return [visit(item) for item in nested]
        return nested

    projected = visit(value)
    _assert_no_sensitive(projected, label="public receipt projection")
    return projected, redacted


def _validate_public_projection(
    *,
    source_receipt: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    kind: str,
    sessions: list[str],
) -> None:
    self_field = (
        "receipt_sha256" if kind == "feature_history_v3" else "authority_root_sha256"
    )
    source_field = (
        "source_receipt_sha256"
        if kind == "feature_history_v3"
        else "source_authority_root_sha256"
    )
    source = dict(source_receipt)
    source_root = _strict_sha(
        source.pop(self_field, None), label=f"{kind} source root"
    )
    if _sha(source) != source_root:
        _fail(f"{kind} source receipt self hash rejected")
    projection, redacted_count = _project_public(source_receipt)
    projection.pop(self_field)
    expected = {
        "projection_schema": "factor-v3-public-receipt-redacted-projection/v1",
        "redacted_field_count": redacted_count,
        source_field: source_root,
        "source_receipt_projection": projection,
        "source_receipt_projection_sha256": _sha(projection),
    }
    actual = {
        key: value
        for key, value in snapshot.items()
        if key not in {"schema", "derived_adapter"}
    }
    expected_fields = {
        "projection_schema", "redacted_field_count", "schema",
        "source_receipt_projection", "source_receipt_projection_sha256",
        source_field,
    }
    if kind == "daily_basic_733_v2":
        expected_fields.add("derived_adapter")
    if set(snapshot) != expected_fields or actual != expected:
        _fail(f"{kind} public receipt projection mismatch")
    if kind == "feature_history_v3":
        if (
            snapshot["schema"]
            != "factor-v3-development-feature-history-receipt-snapshot/v2"
            or source_receipt.get("schema_version")
            != "audited-pit-factor-v3-feature-history-authority-receipt/v3"
            or source_receipt.get("verified") is not True
            or source_receipt.get("authority_status")
            != "VERIFIED_FEATURE_HISTORY_ONLY"
            or source_receipt.get("feature_history_only") is not True
            or source_receipt.get("session_count") != len(sessions)
            or source_receipt.get("sessions_sha256") != _sha(sessions)
        ):
            _fail("feature-history receipt semantics rejected")
        _false(
            source_receipt,
            (
                "embargo_consumed", "experiment_launch_eligible",
                "factor_materialization_eligible", "final_oos_consumed",
                "production_profile_registered",
                "production_recommendation_eligible",
            ),
            label="feature-history receipt",
        )
    else:
        if (
            set(source_receipt) != _DAILY_BASIC_V2_RECEIPT_FIELDS
            or snapshot["schema"]
            != "factor-v3-development-daily-basic-receipt-snapshot/v2"
            or source_receipt.get("schema")
            != "factor-v3-daily-basic-733-exact-set-receipt/v2"
            or source_receipt.get("authority_status")
            != "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET"
            or source_receipt.get("authority_scope")
            != "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY"
            or source_receipt.get("row_authority_status")
            != "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY"
            or source_receipt.get("trade_date_count") != len(sessions)
            or source_receipt.get("trade_dates") != sessions
            or source_receipt.get("trade_dates_sha256") != _sha(sessions)
        ):
            _fail("daily-basic receipt semantics rejected")
        _false(
            source_receipt,
            (
                "arbitrary_row_drops_permitted",
                "embargo_consumed",
                "final_oos_consumed",
                "formal_factor_v3_materialization_performed",
                "production_profile_registered",
                "production_recommendation_eligible",
                "rows_published",
                "silent_row_drops_permitted",
            ),
            label="daily-basic receipt",
        )
        for field in (
            "all_supported_segments_compared_before_scope_filter",
            "exact_set_verified",
            "factor_v3_development_materialization_input_eligible",
            "raw_source_rows_bound",
            "source_ts_code_exact_set_verified_after_transition_filter",
            "transition_resolved_identity_exact_set_verified",
        ):
            if source_receipt.get(field) is not True:
                _fail("daily-basic receipt eligibility binding rejected")


def _is_mainboard_chinext_symbol(value: Any) -> bool:
    raw = str(value or "").strip().upper()
    parts = raw.split(".")
    if len(parts) != 2:
        return False
    code, suffix = parts
    if len(code) != 6 or not code.isdigit() or suffix not in {"SH", "SZ"}:
        return False
    if code.startswith("6") and suffix != "SH":
        return False
    if code.startswith(("0", "3")) and suffix != "SZ":
        return False
    return code.startswith(_ALLOWED_SYMBOL_PREFIXES)


def _derive_attempt_key(semantic_root: str) -> str:
    return _sha(
        {
            "schema": "factor-v3-parent-source-development-authority-attempt-key/v1",
            "semantic_input_root_sha256": semantic_root,
        }
    )


def _derive_global_identity(attempt_key: str) -> str:
    return _sha(
        {
            "attempt_key_sha256": attempt_key,
            "schema": "factor-v3-parent-source-global-attempt-identity/v1",
        }
    )


def _load_candidate_container(
    *,
    candidate_output_root: str | Path,
    candidate_publication_path: str | Path,
    expected_candidate_publication_sha256: str,
) -> dict[str, Any]:
    root = _existing(
        Path(candidate_output_root), label="candidate output root", directory=True
    )
    publication_sha = _strict_sha(
        expected_candidate_publication_sha256,
        label="expected candidate publication SHA",
    )
    publication_path = _existing(
        Path(candidate_publication_path),
        label="candidate publication",
        directory=False,
    )
    expected_path = root.joinpath(
        *_candidate_publication_relative_path(publication_sha).split("/")
    )
    if _path_key(publication_path) != _path_key(expected_path):
        _fail("candidate publication CAS path rejected")
    manifest, manifest_raw = _read(publication_path, label="candidate publication")
    if _sha_bytes(manifest_raw) != publication_sha:
        _fail("candidate publication SHA mismatch")
    manifest_fields = {
        "authority_root_sha256", "descriptor_relative_path", "descriptor_sha256",
        "development_only", "parent_source_authority_verified",
        "producer_snapshot_root_sha256", "schema", "source_spec_sha256",
        "source_authority_complete", *CANDIDATE_PROVENANCE_FALSE_FIELDS,
        *CANDIDATE_SAFETY_FALSE_FIELDS,
    }
    _fields(manifest, manifest_fields, label="candidate publication")
    if (
        manifest["schema"] != CANDIDATE_PUBLICATION_SCHEMA
        or manifest["development_only"] is not True
        or manifest["parent_source_authority_verified"] is not False
        or manifest["source_authority_complete"] is not False
    ):
        _fail("candidate publication claims rejected")
    _false(
        manifest,
        (
            *CANDIDATE_PROVENANCE_FALSE_FIELDS,
            *CANDIDATE_SAFETY_FALSE_FIELDS,
        ),
        label="candidate publication",
    )
    authority_root = _strict_sha(
        manifest["authority_root_sha256"], label="candidate authority root"
    )
    descriptor_relative = _candidate_descriptor_relative_path(
        authority_root
    )
    if manifest["descriptor_relative_path"] != descriptor_relative:
        _fail("candidate descriptor relative path rejected")
    descriptor_path = _relative(
        root, descriptor_relative, label="candidate descriptor"
    )
    descriptor, descriptor_raw = _read(
        descriptor_path, label="candidate descriptor"
    )
    if _sha_bytes(descriptor_raw) != _strict_sha(
        manifest["descriptor_sha256"], label="candidate descriptor SHA"
    ):
        _fail("candidate descriptor SHA mismatch")
    descriptor_fields = {
        "authority_root_sha256", "authority_status", "calendar", "development_only",
        "factor_v2_branch", "formal_materialization_eligible",
        "parent_source_authority_verified", "points_contract_sha256",
        "producer_snapshot", "schema", "snapshots", "source_receipts",
        "source_spec_sha256", "source_authority_complete",
        *CANDIDATE_PROVENANCE_FALSE_FIELDS,
        *CANDIDATE_SAFETY_FALSE_FIELDS,
    }
    _fields(descriptor, descriptor_fields, label="candidate descriptor")
    unsigned_descriptor = dict(descriptor)
    unsigned_descriptor.pop("authority_root_sha256")
    if (
        _sha(unsigned_descriptor) != authority_root
        or descriptor["schema"] != CANDIDATE_DESCRIPTOR_SCHEMA
        or descriptor["authority_status"] != CANDIDATE_AUTHORITY_STATUS
        or descriptor["development_only"] is not True
        or descriptor["formal_materialization_eligible"] is not False
        or descriptor["parent_source_authority_verified"] is not False
        or descriptor["source_authority_complete"] is not False
        or descriptor["source_spec_sha256"] != manifest["source_spec_sha256"]
    ):
        _fail("candidate descriptor contract rejected")
    _false(
        descriptor,
        (
            *CANDIDATE_PROVENANCE_FALSE_FIELDS,
            *CANDIDATE_SAFETY_FALSE_FIELDS,
        ),
        label="candidate descriptor",
    )
    if (
        descriptor["producer_snapshot"]
        != _candidate_producer_snapshot()
        or descriptor["producer_snapshot"]["root_sha256"]
        != manifest["producer_snapshot_root_sha256"]
    ):
        _fail("candidate producer source identity rejected")
    points.assert_frozen_factor_v3_points_contract()
    if (
        descriptor["points_contract_sha256"]
        != points.FACTOR_V3_POINTS_CONTRACT_SHA256
    ):
        _fail("candidate points contract binding rejected")

    entries = descriptor["snapshots"]
    if type(entries) is not dict or set(entries) != set(
        CANDIDATE_SNAPSHOT_NAMES
    ):
        _fail("candidate snapshot namespace rejected")
    snapshots: dict[str, dict[str, Any]] = {}
    snapshot_raw: dict[str, bytes] = {}
    for name in CANDIDATE_SNAPSHOT_NAMES:
        entry = _fields(
            entries[name],
            {
                "file_sha256", "payload_root_sha256", "relative_path",
                "row_count", "rows_sha256", "schema",
            },
            label=f"candidate {name} descriptor",
        )
        if entry["relative_path"] != f"{name}.json":
            _fail(f"candidate {name} relative path rejected")
        snapshot_path = _relative(
            descriptor_path.parent,
            entry["relative_path"],
            label=f"candidate {name}",
        )
        payload, raw = _read(snapshot_path, label=f"candidate {name}")
        _assert_no_sensitive(payload, label=f"candidate {name}")
        rows = payload.get("rows")
        row_count = len(rows) if type(rows) is list else None
        rows_sha = _sha(rows) if type(rows) is list else None
        if (
            _sha_bytes(raw) != _strict_sha(
                entry["file_sha256"], label=f"candidate {name} file SHA"
            )
            or _sha(payload) != _strict_sha(
                entry["payload_root_sha256"], label=f"candidate {name} root"
            )
            or payload.get("schema") != entry["schema"]
            or entry["row_count"] != row_count
            or entry["rows_sha256"] != rows_sha
            or payload.get("rows_sha256") != rows_sha
        ):
            _fail(f"candidate {name} descriptor/payload mismatch")
        snapshots[name] = payload
        snapshot_raw[name] = raw
    expected_namespace = {
        "input-authority.json",
        *(f"{name}.json" for name in CANDIDATE_SNAPSHOT_NAMES),
    }
    if {child.name for child in descriptor_path.parent.iterdir()} != expected_namespace:
        _fail("candidate snapshot exact namespace rejected")
    return {
        "root": root,
        "publication_path": publication_path,
        "publication_sha256": publication_sha,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "descriptor": descriptor,
        "descriptor_path": descriptor_path,
        "descriptor_raw": descriptor_raw,
        "snapshot_payloads": snapshots,
        "snapshot_raw": snapshot_raw,
    }


def _replay_candidate_semantics(candidate: dict[str, Any]) -> None:
    snapshots = candidate["snapshot_payloads"]
    calendar = _fields(
        snapshots["calendar"],
        {
            "all_market_session_count",
            "all_market_sessions",
            "all_market_sessions_sha256",
            "development_session_count",
            "development_sessions",
            "development_sessions_sha256",
            "prewindow_session_count",
            "prewindow_sessions",
            "prewindow_sessions_sha256",
            "schema",
            "source_date_count",
            "source_dates",
            "source_dates_sha256",
        },
        label="candidate calendar",
    )
    all_sessions = calendar.get("all_market_sessions")
    prewindow = calendar.get("prewindow_sessions")
    development = calendar.get("development_sessions")
    source_dates = calendar.get("source_dates")
    if (
        type(all_sessions) is not list
        or type(prewindow) is not list
        or type(development) is not list
        or type(source_dates) is not list
        or len(all_sessions) != 733
        or len(prewindow) != 250
        or len(development) != 483
        or len(source_dates) != 732
        or all_sessions != [*prewindow, *development]
        or source_dates != all_sessions[:-1]
        or len(set(all_sessions)) != 733
        or calendar["schema"] != "factor-v3-development-calendar-snapshot/v2"
    ):
        _fail("independent calendar sequence rejected")
    try:
        parsed_sessions = [date.fromisoformat(value) for value in all_sessions]
    except (TypeError, ValueError) as exc:
        raise IndependentCoreError("independent calendar date rejected") from exc
    if (
        [value.isoformat() for value in parsed_sessions] != all_sessions
        or any(
            previous >= current
            for previous, current in zip(parsed_sessions, parsed_sessions[1:])
        )
    ):
        _fail("independent calendar order rejected")
    for prefix, values, count_field, hash_field in (
        ("all", all_sessions, "all_market_session_count", "all_market_sessions_sha256"),
        ("prewindow", prewindow, "prewindow_session_count", "prewindow_sessions_sha256"),
        ("development", development, "development_session_count", "development_sessions_sha256"),
        ("source", source_dates, "source_date_count", "source_dates_sha256"),
    ):
        if calendar.get(count_field) != len(values) or calendar.get(hash_field) != _sha(values):
            _fail(f"independent {prefix} calendar metadata rejected")
    calendar_projection = {
        key: calendar[key]
        for key in (
            "all_market_session_count", "all_market_sessions_sha256",
            "development_session_count", "development_sessions_sha256",
            "prewindow_session_count", "prewindow_sessions_sha256",
            "source_date_count", "source_dates_sha256",
        )
    }
    if candidate["descriptor"]["calendar"] != calendar_projection:
        _fail("candidate calendar descriptor mismatch")

    parent = snapshots["factor_v2_parent"]
    rows = parent.get("rows")
    if type(rows) is not list:
        _fail("candidate parent rows rejected")
    development_set = set(development)
    for row in rows:
        _fields(
            row,
            {"candidate_key", "features", "signal_date", "ts_code"},
            label="candidate parent row",
        )
        try:
            parsed = date.fromisoformat(row["signal_date"])
        except (TypeError, ValueError) as exc:
            raise IndependentCoreError("candidate parent date rejected") from exc
        if (
            parsed.isoformat() != row["signal_date"]
            or row["signal_date"] not in development_set
            or row["candidate_key"]
            != f"cn-a-share:{row['ts_code']}|{row['signal_date']}"
            or not _is_mainboard_chinext_symbol(
                row["ts_code"]
            )
            or type(row["features"]) is not list
            or len(row["features"]) != 10
            or any(
                type(item) not in (int, float) or not math.isfinite(float(item))
                for item in row["features"]
            )
        ):
            _fail("candidate parent row contract rejected")
    if rows != sorted(rows, key=lambda row: (row["signal_date"], row["candidate_key"])):
        _fail("candidate parent row order rejected")
    candidate_keys = sorted(row["candidate_key"] for row in rows)
    source_rows = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
            "features": row["features"],
        }
        for row in rows
    ]
    projection = {
        "schema": PARENT_PROJECTION_SCHEMA,
        "contract": deepcopy(PARENT_PROJECTION_CONTRACT),
        "candidate_keys_sha256": _sha(candidate_keys),
        "source_feature_projection_rows_sha256": _sha(source_rows),
        "full_export_rows_sha256": _sha(rows),
    }
    expectation = points.FACTOR_V3_POINTS_CONTRACT[
        "preregistered_parent_expectation"
    ]
    if any(parent.get(key) != value for key, value in expectation.items()):
        _fail("candidate parent preregistered expectation drifted")
    identities = [
        {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"]}
        for row in rows
    ]
    if (
        parent.get("rows_sha256") != projection["full_export_rows_sha256"]
        or parent.get("candidate_identity_root_sha256") != _sha(identities)
        or parent.get("validator")
        != "validate_factor_v2_common_eligible_parent_hash_binding"
        or any(
            parent.get("derived_adapter", {}).get(field) != projection[field]
            for field in PARENT_PROJECTION_FIELDS
        )
    ):
        _fail("candidate parent projection rejected")
    binding = parent.get("parent_hash_binding_receipt")
    if type(binding) is not dict:
        _fail("candidate parent binding receipt rejected")
    unsigned_binding = dict(binding)
    binding_root = _strict_sha(
        unsigned_binding.pop("receipt_sha256", None),
        label="candidate parent binding receipt root",
    )
    if (
        _sha(unsigned_binding) != binding_root
        or binding.get("authority_status")
        != "PINNED_HASH_MATCH_ONLY_SOURCE_PRODUCER_UNVERIFIED"
        or binding.get("formal_materialization_eligible") is not False
        or binding.get("source_provenance_verified") is not False
        or any(binding.get(field) != projection[field] for field in PARENT_PROJECTION_FIELDS)
    ):
        _fail("candidate parent unverified binding rejected")

    board = snapshots["upstream_board_ledger"]
    per_date = board.get("per_date")
    if (
        board.get("preserved_before_target_scope_filter") is not True
        or board.get("downstream_scope_filter")
        != "mainboard_chinext_candidate_join_only"
        or board.get("source_segments") != list(UPSTREAM_SOURCE_SEGMENTS)
        or type(per_date) is not list
        or [entry.get("trade_date") for entry in per_date] != source_dates
        or board.get("per_date_board_ledger_root_sha256") != _sha(per_date)
    ):
        _fail("candidate board ledger rejected")
    pre_filter_counts = _fields(
        board.get("pre_filter_segment_counts"),
        set(UPSTREAM_SOURCE_SEGMENTS),
        label="candidate board pre-filter segment counts",
    )
    for segment in UPSTREAM_SOURCE_SEGMENTS:
        daily_counts = [entry["segment_counts"].get(segment) for entry in per_date]
        if (
            any(type(value) is not int or value <= 0 for value in daily_counts)
            or pre_filter_counts[segment] != sum(daily_counts)
        ):
            _fail("candidate board upstream segment rejected")
    derived_schemas = {
        "daily_basic": "factor-v3-development-daily-basic-snapshot/v2",
        "daily_traded_cross_section": (
            "factor-v3-development-daily-cross-section-snapshot/v2"
        ),
        "listing_membership": (
            "factor-v3-development-listing-membership-snapshot/v2"
        ),
        "suspensions": "factor-v3-development-suspension-snapshot/v2",
        "security_code_transitions": (
            "factor-v3-development-security-transition-snapshot/v2"
        ),
    }
    for name, schema in derived_schemas.items():
        snapshot = snapshots[name]
        if snapshot.get("schema") != schema or type(snapshot.get("rows")) is not list:
            _fail(f"candidate {name} schema/rows rejected")
    daily = snapshots["daily_basic"]
    cross = snapshots["daily_traded_cross_section"]
    if (
        daily.get("source_dates") != source_dates
        or daily.get("source_dates_sha256") != _sha(source_dates)
        or cross.get("source_dates_sha256") != _sha(source_dates)
    ):
        _fail("candidate derived source-date binding rejected")
    adapter = daily.get("derived_adapter")
    if (
        type(adapter) is not dict
        or adapter.get("schema")
        != "factor-v3-development-input-derived-adapter/v2"
        or adapter.get("candidate_producer_snapshot_root_sha256")
        != candidate["descriptor"]["producer_snapshot"]["root_sha256"]
    ):
        _fail("candidate derived daily adapter rejected")
    _false(
        adapter,
        CANDIDATE_PROVENANCE_FALSE_FIELDS,
        label="candidate derived daily adapter",
    )
    candidate["calendar"] = {
        **calendar_projection,
        "source_dates": source_dates,
        "prewindow_sessions": prewindow,
    }
    candidate["parent_projection"] = projection
    candidate["parent_row_count"] = len(rows)
    candidate["board_projection"] = {
        "downstream_scope_filter": board["downstream_scope_filter"],
        "preserved_before_target_scope_filter": True,
        "source_date_count": 732,
        "source_segments": list(UPSTREAM_SOURCE_SEGMENTS),
        "upstream_board_ledger_root_sha256": board[
            "per_date_board_ledger_root_sha256"
        ],
    }
    evaluation = snapshots["factor_v2_evaluation"]
    _fields(
        evaluation,
        {
            "branch_receipt_sha256", "decision_receipt_sha256",
            "decision_receipt_raw_file_sha256", "evaluation_artifact_sha256",
            "schema", "selected_branch",
        },
        label="candidate Factor V2 evaluation snapshot",
    )
    candidate["evaluation_binding"] = {
        "branch_receipt_sha256": evaluation["branch_receipt_sha256"],
        "decision_receipt_sha256": evaluation["decision_receipt_sha256"],
        "decision_receipt_raw_file_sha256": evaluation[
            "decision_receipt_raw_file_sha256"
        ],
        "evaluation_artifact_sha256": evaluation["evaluation_artifact_sha256"],
        "selected_branch": evaluation["selected_branch"],
        "snapshot_schema": evaluation["schema"],
    }
    branch = candidate["descriptor"]["factor_v2_branch"]
    unsigned_branch = dict(branch)
    branch_root = _strict_sha(
        unsigned_branch.pop("receipt_sha256", None), label="candidate branch root"
    )
    if (
        _sha(unsigned_branch) != branch_root
        or branch.get("arm_order") != list(factor_v2_branch_selector.ARM_ORDER)
        or branch.get("selection_rule") != factor_v2_branch_selector.SELECTION_RULE
        or branch.get("formal_materialization_eligible") is not False
        or branch.get("source_authority_complete") is not False
        or branch.get("verified") is not False
        or branch_root != candidate["evaluation_binding"]["branch_receipt_sha256"]
        or branch.get("selected_branch")
        != candidate["evaluation_binding"]["selected_branch"]
    ):
        _fail("candidate Factor V2 branch binding rejected")


def _validate_frozen_feature_identity(value: Any) -> dict[str, Any]:
    feature = _fields(
        value, _FROZEN_FEATURE_FIELDS, label="frozen feature-history identity"
    )
    for field in (
        "authority_manifest_sha256",
        "feature_run_spec_file_sha256",
        "feature_run_spec_sha256",
        "pit_store_database_sha256",
        "publication_capability_sha256",
        "publication_issuance_sha256",
        "receipt_sha256",
        "sessions_sha256",
        "snapshot_index_sha256",
        "source_authority_root_sha256",
    ):
        _strict_sha(feature[field], label=f"frozen feature-history {field}")
    if (
        feature["session_count"] != 250
        or type(feature["feature_run_root"]) is not str
        or not Path(feature["feature_run_root"]).is_absolute()
        or type(feature["feature_run_spec_path"]) is not str
        or not Path(feature["feature_run_spec_path"]).is_absolute()
    ):
        _fail("frozen feature-history run identity rejected")
    for relative_field, sha_field in (
        ("authority_manifest_relative_path", "authority_manifest_sha256"),
        ("publication_issuance_relative_path", "publication_issuance_sha256"),
    ):
        parts = feature[relative_field].split("/")
        digest = feature[sha_field]
        if (
            len(parts) != 4
            or parts[-3] != "sha256"
            or parts[-2] != digest[:2]
            or parts[-1] != f"{digest}.json"
        ):
            _fail("frozen feature-history CAS identity rejected")
    return feature


def _load_candidate_sources(
    candidate: dict[str, Any],
    *,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
) -> None:
    history_path, issuance, history_raw, history_file_sha = _cas(
        feature_history_collection_issuance_path,
        expected_feature_history_collection_issuance_sha256,
        category="feature_history_collection_publication_receipts",
        label="feature-history collection issuance",
    )
    attestation_path, attestation, attestation_raw, attestation_file_sha = _cas(
        feature_history_frozen_attestation_path,
        expected_feature_history_frozen_attestation_sha256,
        category="factor_v3_feature_history_frozen_source_attestations",
        label="feature-history frozen attestation",
    )
    daily_path, daily_receipt, daily_raw, daily_file_sha = _cas(
        daily_basic_authority_receipt_path,
        expected_daily_basic_authority_receipt_sha256,
        category="factor_v3_daily_basic_733_receipts",
        label="daily-basic 733 authority receipt",
    )
    if (
        set(issuance)
        != {
            "authority_manifest_sha256", "publication_capability_sha256",
            "publication_schema", "publication_status", "schema",
        }
        or issuance["schema"]
        != "audited-pit-factor-v3-feature-history-publication-issuance/v1"
        or issuance["publication_schema"]
        != "audited-pit-factor-v3-feature-history-collection-publication/v1"
        or issuance["publication_status"]
        != "DURABLE_POSTVERIFIED_AND_RETURNED"
    ):
        _fail("feature-history issuance contract rejected")
    snapshots = candidate["snapshot_payloads"]
    calendar = snapshots["calendar"]
    history_snapshot = snapshots["feature_history_receipt"]
    history_receipt = {
        **history_snapshot["source_receipt_projection"],
        "receipt_sha256": history_snapshot["source_receipt_sha256"],
    }
    summaries = candidate["descriptor"].get("source_receipts")
    _fields(
        summaries,
        {"daily_basic", "feature_history"},
        label="candidate source receipt summaries",
    )
    history_summary = _fields(
        summaries["feature_history"],
        {
            "authority_status", "feature_history_only", "receipt_sha256",
            "schema_version", "session_count", "sessions_sha256",
            "source_authority_root_sha256", "verified",
        },
        label="candidate feature-history summary",
    )
    daily_summary = _fields(
        summaries["daily_basic"],
        {
            "authority_root_sha256", "authority_scope", "authority_status",
            "normalized_daily_basic_row_authority_root_sha256",
            "row_authority_status", "schema", "trade_date_count",
            "trade_dates_sha256",
        },
        label="candidate daily-basic summary",
    )
    if (
        history_summary["receipt_sha256"] != history_receipt["receipt_sha256"]
        or history_summary["sessions_sha256"]
        != history_receipt["sessions_sha256"]
        or history_summary["source_authority_root_sha256"]
        != history_receipt["source_authority_root_sha256"]
        or daily_summary["authority_root_sha256"]
        != daily_receipt["authority_root_sha256"]
        or daily_summary["trade_dates_sha256"]
        != daily_receipt["trade_dates_sha256"]
        or daily_summary["normalized_daily_basic_row_authority_root_sha256"]
        != daily_receipt["normalized_daily_basic_row_authority_root_sha256"]
    ):
        _fail("candidate source receipt summary binding rejected")
    attested_feature = _validate_frozen_feature_identity(
        attestation.get("feature_history")
    )
    if (
        set(attestation)
        != {"attestor_producer", "feature_history", "frozen_source", "schema", "verified"}
        or
        attestation.get("schema")
        != "factor-v3-feature-history-frozen-source-attestation/v2"
        or attestation.get("verified") is not True
        or type(attestation.get("attestor_producer")) is not dict
        or type(attestation.get("frozen_source")) is not dict
        or set(attestation["frozen_source"])
        != {
            "commit", "checkout_policy", "physical_files",
            "physical_files_root_sha256", "producer_binding", "root",
        }
        or issuance["authority_manifest_sha256"]
        != history_receipt["collection_publication_manifest_sha256"]
        or any(
            attested_feature[field] != expected
            for field, expected in {
                "authority_manifest_sha256": issuance[
                    "authority_manifest_sha256"
                ],
                "pit_store_database_sha256": history_receipt[
                    "pit_store_database_sha256"
                ],
                "publication_capability_sha256": issuance[
                    "publication_capability_sha256"
                ],
                "publication_issuance_relative_path": "/".join(
                    history_path.parts[-4:]
                ),
                "publication_issuance_sha256": history_file_sha,
                "receipt_sha256": history_receipt["receipt_sha256"],
                "session_count": 250,
                "sessions_sha256": history_receipt["sessions_sha256"],
                "snapshot_index_sha256": history_receipt[
                    "snapshot_index_sha256"
                ],
                "source_authority_root_sha256": history_receipt[
                    "source_authority_root_sha256"
                ],
            }.items()
        )
    ):
        _fail("feature-history issuance/attestation/receipt binding rejected")
    try:
        _validate_public_projection(
            source_receipt=history_receipt,
            snapshot=history_snapshot,
            kind="feature_history_v3",
            sessions=calendar["prewindow_sessions"],
        )
        _validate_public_projection(
            source_receipt=daily_receipt,
            snapshot=snapshots["daily_basic_exact_set_receipt"],
            kind="daily_basic_733_v2",
            sessions=calendar["all_market_sessions"],
        )
    except ValueError as exc:
        raise IndependentCoreError(str(exc)) from exc
    daily_snapshot = snapshots["daily_basic_exact_set_receipt"]
    daily_projection = daily_snapshot["source_receipt_projection"]
    statistics = daily_projection["per_date_statistics"]
    if (
        daily_projection["trade_dates"] != calendar["all_market_sessions"]
        or daily_projection["trade_dates_sha256"]
        != _sha(calendar["all_market_sessions"])
        or daily_projection["per_date_statistics_sha256"] != _sha(statistics)
        or [entry["trade_date"] for entry in statistics]
        != calendar["all_market_sessions"]
    ):
        _fail("daily-basic 733 statistics replay rejected")
    board_entries = snapshots["upstream_board_ledger"]["per_date"]
    for index, board_entry in enumerate(board_entries):
        statistic = statistics[index]
        _fields(
            board_entry,
            {
                "normalized_rows_sha256",
                "raw_source_rows_sha256",
                "segment_counts",
                "trade_date",
            },
            label="candidate board per-date entry",
        )
        _fields(
            statistic,
            {
                "authoritative_daily_raw_codes_sha256",
                "daily_basic_canonical_rows_sha256",
                "daily_basic_raw_segment_counts",
                "trade_date",
            },
            label="candidate daily-basic per-date statistic",
        )
        board_counts = _fields(
            board_entry["segment_counts"],
            set(UPSTREAM_SOURCE_SEGMENTS),
            label="candidate board per-date segment counts",
        )
        daily_counts = _fields(
            statistic["daily_basic_raw_segment_counts"],
            set(UPSTREAM_SOURCE_SEGMENTS),
            label="candidate daily-basic raw segment counts",
        )
        _strict_sha(
            board_entry["raw_source_rows_sha256"],
            label="candidate board raw-source rows root",
        )
        _strict_sha(
            board_entry["normalized_rows_sha256"],
            label="candidate board normalized rows root",
        )
        if (
            any(
                type(board_counts[name]) is not int or board_counts[name] <= 0
                for name in UPSTREAM_SOURCE_SEGMENTS
            )
            or any(
                type(daily_counts[name]) is not int or daily_counts[name] <= 0
                for name in UPSTREAM_SOURCE_SEGMENTS
            )
            or statistic["trade_date"] != board_entry["trade_date"]
            or statistic["authoritative_daily_raw_codes_sha256"]
            != board_entry["raw_source_rows_sha256"]
            or statistic["daily_basic_canonical_rows_sha256"]
            != board_entry["normalized_rows_sha256"]
            or daily_counts != board_counts
        ):
            _fail("candidate board/daily receipt per-date binding rejected")

    descriptor = candidate["descriptor"]
    projection = candidate["parent_projection"]
    development = calendar["development_sessions"]
    points_evidence = {
        "candidate_keys_sha256": projection["candidate_keys_sha256"],
        "development_session_count": len(development),
        "development_session_end": development[-1],
        "development_session_sha256": _sha(development),
        "development_session_start": development[0],
        "factor_v3_points_contract_sha256": (
            points.FACTOR_V3_POINTS_CONTRACT_SHA256
        ),
        "full_export_rows_sha256": projection["full_export_rows_sha256"],
        "parent_row_count": candidate["parent_row_count"],
        "preregistered_parent_expectation_sha256": (
            points.FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256
        ),
        "source_feature_projection_rows_sha256": projection[
            "source_feature_projection_rows_sha256"
        ],
    }
    history_entry = descriptor["snapshots"]["feature_history_receipt"]
    history_evidence = {
        "candidate_authority_root_sha256": candidate["manifest"][
            "authority_root_sha256"
        ],
        "candidate_descriptor_sha256": candidate["manifest"]["descriptor_sha256"],
        "candidate_producer_snapshot_root_sha256": descriptor[
            "producer_snapshot"
        ]["root_sha256"],
        "feature_history_collection_issuance_file_sha256": history_file_sha,
        "feature_history_collection_issuance_path": str(history_path),
        "feature_history_frozen_attestation_file_sha256": attestation_file_sha,
        "feature_history_frozen_attestation_path": str(attestation_path),
        "feature_history_authority_manifest_relative_path": attested_feature[
            "authority_manifest_relative_path"
        ],
        "feature_history_feature_run_root": attested_feature["feature_run_root"],
        "feature_history_feature_run_spec_file_sha256": attested_feature[
            "feature_run_spec_file_sha256"
        ],
        "feature_history_feature_run_spec_path": attested_feature[
            "feature_run_spec_path"
        ],
        "feature_history_feature_run_spec_sha256": attested_feature[
            "feature_run_spec_sha256"
        ],
        "feature_history_projection_file_sha256": history_entry["file_sha256"],
        "feature_history_projection_path": str(
            candidate["descriptor_path"].parent / history_entry["relative_path"]
        ),
        "feature_history_projection_root_sha256": history_entry[
            "payload_root_sha256"
        ],
        "source_authority_root_sha256": history_receipt[
            "source_authority_root_sha256"
        ],
        "source_receipt_projection_sha256": history_snapshot[
            "source_receipt_projection_sha256"
        ],
        "source_receipt_sha256": history_snapshot["source_receipt_sha256"],
    }
    derived_roots = {
        name: descriptor["snapshots"][name]["payload_root_sha256"]
        for name in (
            "daily_basic", "daily_traded_cross_section", "listing_membership",
            "security_code_transitions", "suspensions", "upstream_board_ledger",
        )
    }
    daily_entry = descriptor["snapshots"]["daily_basic_exact_set_receipt"]
    daily_evidence = {
        "candidate_authority_root_sha256": candidate["manifest"][
            "authority_root_sha256"
        ],
        "candidate_descriptor_sha256": candidate["manifest"]["descriptor_sha256"],
        "candidate_producer_snapshot_root_sha256": descriptor[
            "producer_snapshot"
        ]["root_sha256"],
        "daily_basic_authority_receipt_file_sha256": daily_file_sha,
        "daily_basic_authority_receipt_path": str(daily_path),
        "daily_basic_projection_file_sha256": daily_entry["file_sha256"],
        "daily_basic_projection_path": str(
            candidate["descriptor_path"].parent / daily_entry["relative_path"]
        ),
        "daily_basic_projection_root_sha256": daily_entry["payload_root_sha256"],
        "derived_snapshot_roots_sha256": _sha(derived_roots),
        "normalized_daily_basic_row_authority_root_sha256": daily_projection[
            "normalized_daily_basic_row_authority_root_sha256"
        ],
        "source_authority_root_sha256": daily_snapshot[
            "source_authority_root_sha256"
        ],
        "source_receipt_projection_sha256": daily_snapshot[
            "source_receipt_projection_sha256"
        ],
        "source_receipt_sha256": daily_file_sha,
    }
    candidate["authority_scope"] = DISPOSABLE_TEST_AUTHORITY_SCOPE
    candidate["authority_verdicts"] = {
        "points_contract": _test_verdict(
            points_evidence, POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA
        ),
        "feature_history": _test_verdict(
            history_evidence, FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA
        ),
        "daily_basic": _test_verdict(
            daily_evidence, DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA
        ),
    }
    candidate["source_authority_receipts"] = {
        "daily_basic": {
            "path": daily_path,
            "raw": daily_raw,
            "file_sha256": daily_file_sha,
        },
        "feature_history_collection_issuance": {
            "path": history_path,
            "raw": history_raw,
            "file_sha256": history_file_sha,
        },
        "feature_history_frozen_attestation": {
            "path": attestation_path,
            "raw": attestation_raw,
            "file_sha256": attestation_file_sha,
        },
    }


def _load_candidate(
    *,
    candidate_output_root: str | Path,
    candidate_publication_path: str | Path,
    expected_candidate_publication_sha256: str,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
) -> dict[str, Any]:
    candidate = _load_candidate_container(
        candidate_output_root=candidate_output_root,
        candidate_publication_path=candidate_publication_path,
        expected_candidate_publication_sha256=(
            expected_candidate_publication_sha256
        ),
    )
    _replay_candidate_semantics(candidate)
    _load_candidate_sources(
        candidate,
        feature_history_collection_issuance_path=(
            feature_history_collection_issuance_path
        ),
        expected_feature_history_collection_issuance_sha256=(
            expected_feature_history_collection_issuance_sha256
        ),
        feature_history_frozen_attestation_path=(
            feature_history_frozen_attestation_path
        ),
        expected_feature_history_frozen_attestation_sha256=(
            expected_feature_history_frozen_attestation_sha256
        ),
        daily_basic_authority_receipt_path=daily_basic_authority_receipt_path,
        expected_daily_basic_authority_receipt_sha256=(
            expected_daily_basic_authority_receipt_sha256
        ),
    )
    return candidate


def _load_parent(
    *,
    path_value: str | Path,
    expected_sha_value: str,
    terminal_path_value: str | Path,
    expected_terminal_sha_value: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    path, receipt, receipt_raw, file_sha = _cas(
        path_value,
        expected_sha_value,
        category="disposable-parent-source-contract-receipts",
        label="disposable parent-source contract receipt",
    )
    _fields(
        receipt,
        _DISPOSABLE_PARENT_RECEIPT_FIELDS,
        label="disposable parent-source contract receipt",
    )
    receipt_root = _self_hash(
        receipt, label="disposable parent-source contract receipt"
    )
    if (
        receipt.get("schema") != DISPOSABLE_PARENT_SOURCE_RECEIPT_SCHEMA
        or receipt.get("authority_status")
        != "DISPOSABLE_TEST_PARENT_SOURCE_CONTRACT_ONLY"
        or receipt.get("test_fixture_only") is not True
        or receipt.get("contract_binding_validated") is not True
    ):
        _fail("disposable parent-source receipt status rejected")
    _false(
        receipt,
        (
            "formal_materialization_eligible", "independent_public_replay_performed",
            "machine_global_root_lease_verified", "parent_source_authority_verified",
            "root_epoch_terminal_verified", "single_attempt_verified",
            "source_authority_complete", "source_authority_verified", "verified",
            *SAFETY_FALSE_FIELDS,
        ),
        label="disposable parent-source receipt",
    )
    if (
        receipt.get("development_only") is not True
        or receipt.get("requested_action") != 2
        or receipt.get("observed_root_state") != 2
        or receipt.get("approved_transition") != 3
        or receipt.get("terminal_root_state") != 4
        or receipt.get("native_lease_policy_version")
        != PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION
    ):
        _fail("parent-source structural epoch state rejected")
    semantic = _strict_sha(
        receipt.get("semantic_input_root_sha256"), label="parent semantic root"
    )
    attempt = _strict_sha(
        receipt.get("attempt_key_sha256"), label="parent attempt key"
    )
    global_identity = _strict_sha(
        receipt.get("global_attempt_identity_sha256"),
        label="parent global attempt identity",
    )
    run_spec = _strict_sha(receipt.get("run_spec_sha256"), label="parent run spec")
    if (
        _derive_attempt_key(semantic)
        != attempt
        or _derive_global_identity(attempt)
        != global_identity
    ):
        _fail("parent attempt/global identity derivation rejected")
    ledger_root = _existing(
        Path(receipt["global_attempt_ledger_root"]),
        label="parent machine-global ledger root",
        directory=True,
    )
    epoch_dir = ledger_root.joinpath(
        *PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE.format(
            prefix=attempt[:2], attempt_key=attempt
        ).split("/")
    )
    _existing(epoch_dir, label="parent root epoch directory", directory=True)
    if {child.name for child in epoch_dir.iterdir()} != set(
        PARENT_SOURCE_EPOCH_FILE_NAMES
    ):
        _fail("parent root epoch exact namespace rejected")
    paths = {name: epoch_dir / name for name in PARENT_SOURCE_EPOCH_FILE_NAMES}
    expected_paths = {
        "global_run_claim_path": paths["run.claim.json"],
        "global_run_receipt_path": paths["run.receipt.json"],
        "global_verify_claim_path": paths["verify.claim.json"],
        "global_terminal_receipt_path": paths["terminal.receipt.json"],
    }
    if any(
        receipt.get(field) != str(expected)
        for field, expected in expected_paths.items()
    ):
        _fail("parent epoch path binding rejected")
    terminal_sha = _strict_sha(
        expected_terminal_sha_value, label="terminal epoch receipt SHA"
    )
    if _path_key(Path(terminal_path_value)) != _path_key(
        paths["terminal.receipt.json"]
    ):
        _fail("terminal epoch receipt path rejected")
    epoch_raw: dict[str, bytes] = {}
    epoch_payloads: dict[str, dict[str, Any]] = {}
    epoch_hashes: dict[str, str] = {}
    for name, epoch_path in paths.items():
        payload, raw = _read(epoch_path, label=f"parent epoch {name}")
        contract = PARENT_SOURCE_EPOCH_FILE_CONTRACT[name]
        _fields(payload, set(contract["fields"]), label=f"parent epoch {name}")
        if (
            payload["schema"] != contract["schema"]
            or payload["state"] != contract["state"]
            or ("action" in contract and payload["action"] != contract["action"])
            or payload["attempt_key_sha256"] != attempt
            or payload["global_attempt_identity_sha256"] != global_identity
            or payload["run_spec_sha256"] != run_spec
        ):
            _fail(f"parent epoch {name} contract rejected")
        epoch_payloads[name] = payload
        epoch_raw[name] = raw
        epoch_hashes[name] = _sha_bytes(raw)
    if (
        epoch_hashes["terminal.receipt.json"] != terminal_sha
        or epoch_payloads["run.receipt.json"]["run_claim_sha256"]
        != epoch_hashes["run.claim.json"]
        or epoch_payloads["verify.claim.json"]["run_receipt_sha256"]
        != epoch_hashes["run.receipt.json"]
        or epoch_payloads["terminal.receipt.json"]["run_receipt_sha256"]
        != epoch_hashes["run.receipt.json"]
        or epoch_payloads["terminal.receipt.json"]["verify_claim_sha256"]
        != epoch_hashes["verify.claim.json"]
    ):
        _fail("parent epoch hash chain rejected")
    for field, expected in (
        ("run_claim_sha256", epoch_hashes["run.claim.json"]),
        ("run_receipt_sha256", epoch_hashes["run.receipt.json"]),
        ("verify_claim_sha256", epoch_hashes["verify.claim.json"]),
        ("terminal_receipt_file_sha256", terminal_sha),
    ):
        if receipt.get(field) != expected:
            _fail(f"parent receipt {field} binding rejected")
    projection = receipt.get("parent_projection")
    expected_points = {
        "common_eligible_candidate_keys_sha256": projection[
            "candidate_keys_sha256"
        ],
        "common_eligible_source_feature_rows_sha256": projection[
            "source_feature_projection_rows_sha256"
        ],
    }
    if (
        projection != candidate["parent_projection"]
        or receipt.get("points_contract_common_eligible_projection")
        != expected_points
        or receipt.get("calendar_projection")
        != {
            key: value
            for key, value in candidate["calendar"].items()
            if key not in {"source_dates", "prewindow_sessions"}
        }
        or receipt.get("upstream_board_projection")
        != candidate["board_projection"]
    ):
        _fail("parent receipt candidate cross-binding rejected")
    evidence = {
        "attempt_key_sha256": attempt,
        "global_attempt_identity_sha256": global_identity,
        "global_attempt_ledger_root": str(ledger_root),
        "global_run_claim_path": receipt["global_run_claim_path"],
        "global_run_receipt_path": receipt["global_run_receipt_path"],
        "global_terminal_receipt_path": receipt["global_terminal_receipt_path"],
        "global_verify_claim_path": receipt["global_verify_claim_path"],
        "native_lease_identity_sha256": receipt["native_lease_identity_sha256"],
        "native_lease_policy_version": receipt["native_lease_policy_version"],
        "parent_source_authority_receipt_file_sha256": file_sha,
        "parent_source_authority_receipt_path": str(path),
        "parent_source_authority_receipt_root_sha256": receipt_root,
        "parent_source_candidate_keys_sha256": projection[
            "candidate_keys_sha256"
        ],
        "parent_source_full_export_rows_sha256": projection[
            "full_export_rows_sha256"
        ],
        "parent_source_row_count": candidate["parent_row_count"],
        "parent_source_source_feature_projection_rows_sha256": projection[
            "source_feature_projection_rows_sha256"
        ],
        "run_claim_sha256": epoch_hashes["run.claim.json"],
        "run_receipt_sha256": epoch_hashes["run.receipt.json"],
        "run_spec_sha256": run_spec,
        "semantic_input_root_sha256": semantic,
        "terminal_receipt_file_sha256": terminal_sha,
        "verify_claim_sha256": epoch_hashes["verify.claim.json"],
    }
    return {
        "path": path,
        "file_sha256": file_sha,
        "receipt_root_sha256": receipt_root,
        "receipt": receipt,
        "receipt_raw": receipt_raw,
        "ledger_root": ledger_root,
        "terminal_sha256": terminal_sha,
        "epoch_paths": paths,
        "epoch_raw": epoch_raw,
        "native_verdict": _test_verdict(
            evidence, PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA
        ),
    }


def _load_evaluation(
    *,
    path_value: str | Path,
    expected_sha_value: str,
    candidate: Mapping[str, Any],
) -> dict[str, Any]:
    path, receipt, raw, file_sha = _cas(
        path_value,
        expected_sha_value,
        category="disposable-evaluation-contract-receipts",
        label="disposable Factor V2 evaluation contract receipt",
    )
    _fields(
        receipt,
        _DISPOSABLE_EVALUATION_RECEIPT_FIELDS,
        label="disposable Factor V2 evaluation contract receipt",
    )
    receipt_root = _self_hash(
        receipt, label="disposable Factor V2 evaluation contract receipt"
    )
    if (
        receipt.get("schema") != DISPOSABLE_EVALUATION_RECEIPT_SCHEMA
        or receipt.get("authority_status")
        != "DISPOSABLE_TEST_FACTOR_V2_EVALUATION_CONTRACT_ONLY"
        or receipt.get("test_fixture_only") is not True
        or receipt.get("contract_binding_validated") is not True
        or receipt.get("development_only") is not True
    ):
        _fail("disposable Factor V2 evaluation receipt status rejected")
    _false(
        receipt,
        (
            "formal_materialization_eligible", "independent_public_replay_performed",
            "publisher_terminal_chain_verified", "source_authority_complete",
            "verified", *SAFETY_FALSE_FIELDS,
        ),
        label="disposable Factor V2 evaluation receipt",
    )
    projection = _fields(
        receipt.get("evaluation_projection"),
        {"schema", *FACTOR_V2_EVALUATION_PROJECTION_FIELDS},
        label="Factor V2 evaluation projection",
    )
    binding = _fields(
        receipt.get("candidate_evaluation_binding"),
        set(FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS),
        label="Factor V2 evaluation binding",
    )
    if (
        projection["schema"] != FACTOR_V2_EVALUATION_PROJECTION_SCHEMA
        or binding != candidate["evaluation_binding"]
    ):
        _fail("Factor V2 evaluation candidate binding rejected")
    evidence = {
        "candidate_evaluation_binding_sha256": _sha(binding),
        "cost_slippage_execution_descriptor_sha256": projection[
            "cost_slippage_execution_descriptor_sha256"
        ],
        "evaluation_authority_receipt_file_sha256": file_sha,
        "evaluation_authority_receipt_root_sha256": receipt_root,
        "evaluation_projection_sha256": _sha(projection),
        "evaluator_descriptor_sha256": projection["evaluator_descriptor_sha256"],
        "terminal_decision_descriptor_sha256": projection[
            "terminal_decision_descriptor_sha256"
        ],
    }
    return {
        "path": path,
        "file_sha256": file_sha,
        "receipt_root_sha256": receipt_root,
        "receipt_raw": raw,
        "projection": deepcopy(projection),
        "binding": deepcopy(binding),
        "native_verdict": _test_verdict(
            evidence, FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA
        ),
    }


def replay_inputs(
    *,
    candidate_output_root: str | Path,
    candidate_publication_path: str | Path,
    expected_candidate_publication_sha256: str,
    parent_source_authority_receipt_path: str | Path,
    expected_parent_source_authority_receipt_sha256: str,
    parent_source_terminal_epoch_receipt_path: str | Path,
    expected_parent_source_terminal_epoch_receipt_sha256: str,
    factor_v2_evaluation_authority_receipt_path: str | Path,
    expected_factor_v2_evaluation_authority_receipt_sha256: str,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
) -> dict[str, Any]:
    candidate = _load_candidate(
        candidate_output_root=candidate_output_root,
        candidate_publication_path=candidate_publication_path,
        expected_candidate_publication_sha256=expected_candidate_publication_sha256,
        feature_history_collection_issuance_path=(
            feature_history_collection_issuance_path
        ),
        expected_feature_history_collection_issuance_sha256=(
            expected_feature_history_collection_issuance_sha256
        ),
        feature_history_frozen_attestation_path=(
            feature_history_frozen_attestation_path
        ),
        expected_feature_history_frozen_attestation_sha256=(
            expected_feature_history_frozen_attestation_sha256
        ),
        daily_basic_authority_receipt_path=daily_basic_authority_receipt_path,
        expected_daily_basic_authority_receipt_sha256=(
            expected_daily_basic_authority_receipt_sha256
        ),
    )
    parent = _load_parent(
        path_value=parent_source_authority_receipt_path,
        expected_sha_value=expected_parent_source_authority_receipt_sha256,
        terminal_path_value=parent_source_terminal_epoch_receipt_path,
        expected_terminal_sha_value=(
            expected_parent_source_terminal_epoch_receipt_sha256
        ),
        candidate=candidate,
    )
    evaluation = _load_evaluation(
        path_value=factor_v2_evaluation_authority_receipt_path,
        expected_sha_value=expected_factor_v2_evaluation_authority_receipt_sha256,
        candidate=candidate,
    )
    receipt = parent["receipt"]
    bindings = {
        "attempt_key_sha256": receipt["attempt_key_sha256"],
        "candidate_authority_root_sha256": candidate["manifest"][
            "authority_root_sha256"
        ],
        "candidate_descriptor_sha256": candidate["manifest"]["descriptor_sha256"],
        "candidate_publication_file_sha256": candidate["publication_sha256"],
        "daily_basic_authority_receipt_file_sha256": candidate[
            "source_authority_receipts"
        ]["daily_basic"]["file_sha256"],
        "daily_basic_authority_receipt_path": str(
            candidate["source_authority_receipts"]["daily_basic"]["path"]
        ),
        "factor_v2_evaluation_authority_receipt_file_sha256": evaluation[
            "file_sha256"
        ],
        "factor_v2_evaluation_authority_receipt_root_sha256": evaluation[
            "receipt_root_sha256"
        ],
        "feature_history_collection_issuance_file_sha256": candidate[
            "source_authority_receipts"
        ]["feature_history_collection_issuance"]["file_sha256"],
        "feature_history_collection_issuance_path": str(
            candidate["source_authority_receipts"][
                "feature_history_collection_issuance"
            ]["path"]
        ),
        "feature_history_frozen_attestation_file_sha256": candidate[
            "source_authority_receipts"
        ]["feature_history_frozen_attestation"]["file_sha256"],
        "feature_history_frozen_attestation_path": str(
            candidate["source_authority_receipts"][
                "feature_history_frozen_attestation"
            ]["path"]
        ),
        "global_attempt_identity_sha256": receipt[
            "global_attempt_identity_sha256"
        ],
        "global_attempt_ledger_root": receipt["global_attempt_ledger_root"],
        "global_run_claim_path": receipt["global_run_claim_path"],
        "global_run_receipt_path": receipt["global_run_receipt_path"],
        "global_terminal_receipt_path": receipt["global_terminal_receipt_path"],
        "global_verify_claim_path": receipt["global_verify_claim_path"],
        "native_lease_identity_sha256": receipt["native_lease_identity_sha256"],
        "native_lease_policy_version": receipt["native_lease_policy_version"],
        "parent_source_authority_receipt_file_sha256": parent["file_sha256"],
        "parent_source_authority_receipt_root_sha256": parent[
            "receipt_root_sha256"
        ],
        "parent_source_terminal_receipt_file_sha256": parent["terminal_sha256"],
        "run_spec_sha256": receipt["run_spec_sha256"],
        "semantic_input_root_sha256": receipt["semantic_input_root_sha256"],
    }
    _fields(
        bindings,
        set(ACTIVATION_INPUT_BINDING_FIELDS),
        label="independent activation input bindings",
    )
    return {
        "authority_verdicts": {
            **candidate["authority_verdicts"],
            "factor_v2_evaluation": evaluation["native_verdict"],
            "parent_source": parent["native_verdict"],
        },
        "candidate": candidate,
        "parent": parent,
        "evaluation": evaluation,
        "input_bindings": bindings,
    }


__all__ = (
    "IndependentCoreError",
    "replay_inputs",
)
