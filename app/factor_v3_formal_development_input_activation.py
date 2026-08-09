"""Factor V3 formal-development input activation and independent replay.

The producer-side candidate is deliberately unverified.  This module only
activates its immutable public projection after independently replaying a
verified parent-source receipt, its machine-global terminal epoch, and the
Factor V2 evaluation authority receipt.  The resulting descriptor is an
eligibility publication for the next materializer generation; it is not a
materialization and is intentionally not materializer-v1 compatible.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
import math
import os
from pathlib import Path
import secrets
import stat
from typing import Any, Mapping

from app import audited_pit_factor_v3_materializer as materializer
from app import factor_v2_decision_branch_selector as factor_v2_branch_selector
from app import factor_v3_development_input_authority as candidate_authority
from app import factor_v3_feature_history_frozen_source_attestation as frozen_history
from app import factor_v3_formal_trusted_supervisor as trusted_supervisor
from app import factor_v3_parent_source_development_authority as parent_authority


ACTIVATION_SCHEMA = "factor-v3-formal-development-input-activation/v1"
PUBLICATION_SCHEMA = (
    "factor-v3-formal-development-input-activation-publication/v1"
)
INDEPENDENT_VERIFIER_RECEIPT_SCHEMA = (
    "factor-v3-formal-development-input-activation-independent-verifier-receipt/v1"
)
PARENT_SOURCE_ATTEMPT_KEY_SCHEMA = (
    "factor-v3-parent-source-development-authority-attempt-key/v1"
)
PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA = (
    "factor-v3-parent-source-global-attempt-identity/v1"
)
PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION = (
    "factor-v3-parent-source-machine-global-root-lease/v1"
)
PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE = "attempts/sha256/{prefix}/{attempt_key}"
PARENT_SOURCE_EPOCH_FILE_NAMES = (
    "run.claim.json",
    "run.receipt.json",
    "verify.claim.json",
    "terminal.receipt.json",
)
PARENT_SOURCE_RUN_CLAIM_SCHEMA = "factor-v3-parent-source-root-run-claim/v1"
PARENT_SOURCE_RUN_RECEIPT_SCHEMA = "factor-v3-parent-source-root-run-receipt/v1"
PARENT_SOURCE_VERIFY_CLAIM_SCHEMA = "factor-v3-parent-source-root-verify-claim/v1"
PARENT_SOURCE_TERMINAL_RECEIPT_SCHEMA = (
    "factor-v3-parent-source-root-terminal-receipt/v1"
)
ACTIVATION_ELIGIBILITY_DESCRIPTOR_SCHEMA = (
    "factor-v3-formal-development-input-activation-eligibility-descriptor/v1"
)
# Kept as a compatibility name for the frozen RED contract.  Its value is an
# activation eligibility schema, not the materializer-v1 input schema.
MATERIALIZER_INPUT_AUTHORITY_SCHEMA = ACTIVATION_ELIGIBILITY_DESCRIPTOR_SCHEMA
PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-parent-source-native-authority-verdict/v1"
)
FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v2-evaluation-native-authority-verdict/v1"
)
FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-feature-history-native-authority-verdict/v1"
)
DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-daily-basic-native-authority-verdict/v1"
)
POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA = (
    "factor-v3-points-contract-authority-verdict/v1"
)
FORMAL_AUTHORITY_SCOPE = "FORMAL_FROZEN_POINTS_CONTRACT"
DISPOSABLE_TEST_AUTHORITY_SCOPE = "DISPOSABLE_TEST_FIXTURE_ONLY"
ACTIVATION_INPUT_BINDING_FIELDS = (
    "attempt_key_sha256",
    "candidate_authority_root_sha256",
    "candidate_descriptor_sha256",
    "candidate_publication_file_sha256",
    "daily_basic_authority_receipt_file_sha256",
    "daily_basic_authority_receipt_path",
    "factor_v2_evaluation_authority_receipt_file_sha256",
    "factor_v2_evaluation_authority_receipt_root_sha256",
    "feature_history_collection_issuance_file_sha256",
    "feature_history_collection_issuance_path",
    "feature_history_frozen_attestation_file_sha256",
    "feature_history_frozen_attestation_path",
    "global_attempt_identity_sha256",
    "global_attempt_ledger_root",
    "global_run_claim_path",
    "global_run_receipt_path",
    "global_terminal_receipt_path",
    "global_verify_claim_path",
    "native_lease_identity_sha256",
    "native_lease_policy_version",
    "parent_source_authority_receipt_file_sha256",
    "parent_source_authority_receipt_root_sha256",
    "parent_source_terminal_receipt_file_sha256",
    "run_spec_sha256",
    "semantic_input_root_sha256",
)
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
EXACT_CALENDAR_COUNTS = {
    "prewindow_session_count": 250,
    "development_session_count": 483,
    "all_market_session_count": 733,
    "source_date_count": 732,
}
UPSTREAM_SOURCE_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "experiment_launch_eligible",
    "final_oos_consumed",
    "formal_materialization_performed",
    "model_training_started",
    "oof_scoring_started",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
)
PARENT_SOURCE_ROOT_STATE_EMPTY = 0
PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED = 1
PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED = 2
PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED = 3
PARENT_SOURCE_ROOT_STATE_TERMINAL = 4
PARENT_SOURCE_ROOT_ACTION_RUN = 1
PARENT_SOURCE_ROOT_ACTION_VERIFY = 2
PARENT_SOURCE_ROOT_TRANSITION_START_RUN = 1
PARENT_SOURCE_ROOT_TRANSITION_REJECT = 2
PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY = 3
PARENT_SOURCE_EPOCH_FILE_CONTRACT = {
    "run.claim.json": {
        "action": PARENT_SOURCE_ROOT_ACTION_RUN,
        "fields": (
            "action",
            "attempt_key_sha256",
            "global_attempt_identity_sha256",
            "run_spec_sha256",
            "schema",
            "state",
        ),
        "schema": PARENT_SOURCE_RUN_CLAIM_SCHEMA,
        "state": PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED,
    },
    "run.receipt.json": {
        "fields": (
            "attempt_key_sha256",
            "global_attempt_identity_sha256",
            "run_claim_sha256",
            "run_spec_sha256",
            "schema",
            "state",
        ),
        "schema": PARENT_SOURCE_RUN_RECEIPT_SCHEMA,
        "state": PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED,
    },
    "verify.claim.json": {
        "action": PARENT_SOURCE_ROOT_ACTION_VERIFY,
        "fields": (
            "action",
            "attempt_key_sha256",
            "global_attempt_identity_sha256",
            "run_receipt_sha256",
            "run_spec_sha256",
            "schema",
            "state",
        ),
        "schema": PARENT_SOURCE_VERIFY_CLAIM_SCHEMA,
        "state": PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED,
    },
    "terminal.receipt.json": {
        "fields": (
            "attempt_key_sha256",
            "global_attempt_identity_sha256",
            "run_receipt_sha256",
            "run_spec_sha256",
            "schema",
            "state",
            "verify_claim_sha256",
        ),
        "schema": PARENT_SOURCE_TERMINAL_RECEIPT_SCHEMA,
        "state": PARENT_SOURCE_ROOT_STATE_TERMINAL,
    },
}

_ACTIVATION_DIRECTORY = "input-authorities"
_AUTHORITY_VERDICT_DIRECTORY = "authority-verdicts"
_PUBLICATION_DIRECTORY = "publications"
_VERIFIER_RECEIPT_DIRECTORY = "receipts"
_PARENT_RECEIPT_FIELDS = frozenset(
    {
        "approved_transition",
        "attempt_key_sha256",
        "authority_status",
        "calendar_projection",
        "development_only",
        "formal_materialization_eligible",
        "global_attempt_identity_sha256",
        "global_attempt_ledger_root",
        "global_run_claim_path",
        "global_run_receipt_path",
        "global_terminal_receipt_path",
        "global_verify_claim_path",
        "independent_public_replay_performed",
        "machine_global_root_lease_verified",
        "native_lease_identity_sha256",
        "native_lease_policy_version",
        "observed_root_state",
        "parent_projection",
        "parent_source_authority_verified",
        "points_contract_common_eligible_projection",
        "receipt_root_sha256",
        "requested_action",
        "root_epoch_terminal_verified",
        "run_claim_sha256",
        "run_receipt_sha256",
        "run_spec_sha256",
        "schema",
        "semantic_input_root_sha256",
        "single_attempt_verified",
        "source_authority_complete",
        "source_authority_verified",
        "terminal_receipt_file_sha256",
        "terminal_root_state",
        "upstream_board_projection",
        "verified",
        "verify_claim_sha256",
        *SAFETY_FALSE_FIELDS,
    }
)
_EVALUATION_RECEIPT_FIELDS = frozenset(
    {
        "authority_status",
        "candidate_evaluation_binding",
        "development_only",
        "evaluation_projection",
        "formal_materialization_eligible",
        "independent_public_replay_performed",
        "publisher_terminal_chain_verified",
        "receipt_root_sha256",
        "schema",
        "source_authority_complete",
        "verified",
        *SAFETY_FALSE_FIELDS,
    }
)
_DISPOSABLE_PARENT_RECEIPT_FIELDS = frozenset(
    {*_PARENT_RECEIPT_FIELDS, "contract_binding_validated", "test_fixture_only"}
)
_DISPOSABLE_EVALUATION_RECEIPT_FIELDS = frozenset(
    {*_EVALUATION_RECEIPT_FIELDS, "contract_binding_validated", "test_fixture_only"}
)
_CANDIDATE_MANIFEST_FIELDS = frozenset(
    {
        "authority_root_sha256",
        "descriptor_relative_path",
        "descriptor_sha256",
        "development_only",
        "parent_source_authority_verified",
        "producer_snapshot_root_sha256",
        "schema",
        "source_spec_sha256",
        "source_authority_complete",
        *candidate_authority.PROVENANCE_FALSE_FIELDS,
        *candidate_authority.SAFETY_FALSE_FIELDS,
    }
)
_CANDIDATE_DESCRIPTOR_FIELDS = frozenset(
    {
        "authority_root_sha256",
        "authority_status",
        "calendar",
        "development_only",
        "factor_v2_branch",
        "formal_materialization_eligible",
        "parent_source_authority_verified",
        "points_contract_sha256",
        "producer_snapshot",
        "schema",
        "snapshots",
        "source_receipts",
        "source_spec_sha256",
        "source_authority_complete",
        *candidate_authority.PROVENANCE_FALSE_FIELDS,
        *candidate_authority.SAFETY_FALSE_FIELDS,
    }
)
_SNAPSHOT_DESCRIPTOR_FIELDS = frozenset(
    {"file_sha256", "payload_root_sha256", "relative_path", "row_count", "rows_sha256", "schema"}
)


class FactorV3FormalDevelopmentInputActivationError(ValueError):
    """Raised when a formal-development activation contract fails closed."""


def _fail(message: str) -> None:
    raise FactorV3FormalDevelopmentInputActivationError(message)


_verify_native_parent_source_authority_evidence: Any = None
_verify_factor_v2_evaluation_authority_evidence: Any = None
_verify_feature_history_authority_evidence: Any = None
_verify_daily_basic_authority_evidence: Any = None


def _assert_frozen_points_contract() -> None:
    try:
        candidate_authority.points.assert_frozen_factor_v3_points_contract()
    except RuntimeError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(
            "frozen Factor V3 points contract drifted"
        ) from exc


def _verify_formal_points_contract_authority_evidence(
    **evidence: Any,
) -> Mapping[str, Any]:
    _assert_frozen_points_contract()
    expectation = candidate_authority.points.FACTOR_V3_POINTS_CONTRACT[
        "preregistered_parent_expectation"
    ]
    sessions = expectation["sessions"]
    expected = {
        "candidate_keys_sha256": expectation[
            "common_eligible_candidate_keys_sha256"
        ],
        "development_session_count": sessions["count"],
        "development_session_end": sessions["end"],
        "development_session_sha256": sessions["sha256"],
        "development_session_start": sessions["start"],
        "factor_v3_points_contract_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
        ),
        "full_export_rows_sha256": expectation[
            "original_parent_feature_rows_sha256"
        ],
        "parent_row_count": expectation["common_eligible_candidate_count"],
        "preregistered_parent_expectation_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256
        ),
        "source_feature_projection_rows_sha256": expectation[
            "common_eligible_source_feature_rows_sha256"
        ],
    }
    if evidence != expected:
        _fail("formal frozen points contract authority evidence rejected")
    return {
        **evidence,
        "authority_scope": FORMAL_AUTHORITY_SCOPE,
        "schema": POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA,
        "verified": True,
    }


_verify_points_contract_authority_evidence: Any = None


def _assert_fields(value: Any, expected: frozenset[str] | set[str], *, label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != set(expected):
        _fail(f"{label} fields rejected")
    return value


def _sha256(value: Any, *, label: str) -> str:
    try:
        result = candidate_authority._strict_sha256(value, label=label)
    except ValueError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc
    if result == "0" * 64:
        _fail(f"{label} zero digest rejected")
    return result


def _canonical_sha256(value: Any) -> str:
    return candidate_authority._canonical_sha256(value)


def _sha256_bytes(raw: bytes) -> str:
    return candidate_authority._sha256_bytes(raw)


def _canonical_bytes(value: Any) -> bytes:
    return candidate_authority._canonical_bytes(value)


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        return candidate_authority._read_canonical_json(path, label=label)
    except (OSError, ValueError) as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _assert_existing_path(path: Path, *, label: str, directory: bool | None = None) -> None:
    try:
        candidate_authority._assert_safe_path(path, label=label, regular=None if directory is None else not directory)
    except (OSError, ValueError) as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _assert_output_path(path: Path, *, label: str) -> None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    _assert_existing_path(current, label=f"{label} ancestor", directory=True)
    if path.exists():
        _assert_existing_path(path, label=label, directory=True)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _overlap(left: Path, right: Path) -> bool:
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    return _is_relative_to(left_resolved, right_resolved) or _is_relative_to(right_resolved, left_resolved)


def _assert_isolated(root: Path, forbidden: Mapping[str, Path], *, label: str) -> None:
    if not root.is_absolute():
        _fail(f"{label} must be absolute")
    _assert_output_path(root, label=label)
    for name, other in forbidden.items():
        if _overlap(root, other):
            _fail(f"{label} overlap with {name} root rejected")


def _relative_path(root: Path, relative: Any, *, label: str) -> Path:
    try:
        return candidate_authority._safe_relative_path(root, relative, label=label)
    except (OSError, ValueError) as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _assert_receipt_cas_path(path: Path, expected_sha: str, *, category: str, label: str) -> None:
    if (
        path.name != f"{expected_sha}.json"
        or path.parent.name != expected_sha[:2]
        or path.parent.parent.name != "sha256"
        or path.parent.parent.parent.name != category
    ):
        _fail(f"{label} content-addressed path rejected")


def _read_receipt(
    path_value: str | Path,
    expected_sha_value: str,
    *,
    category: str,
    label: str,
) -> tuple[Path, dict[str, Any], bytes, str]:
    path = Path(path_value)
    if not path.is_absolute():
        _fail(f"{label} path must be absolute")
    try:
        canonical_path = path.resolve(strict=True)
    except OSError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(
            f"{label} canonical path unavailable"
        ) from exc
    if path != canonical_path:
        _fail(f"{label} path must be canonical and non-reparse")
    expected_sha = _sha256(expected_sha_value, label=f"expected {label} SHA")
    _assert_receipt_cas_path(path, expected_sha, category=category, label=label)
    payload, raw = _read_json(path, label=label)
    if _sha256_bytes(raw) != expected_sha:
        _fail(f"{label} file SHA mismatch")
    return path, payload, raw, expected_sha


def _assert_self_hash(payload: dict[str, Any], *, label: str) -> str:
    receipt_root = _sha256(payload.get("receipt_root_sha256"), label=f"{label} root")
    unsigned = dict(payload)
    unsigned.pop("receipt_root_sha256", None)
    if _canonical_sha256(unsigned) != receipt_root:
        _fail(f"{label} self hash mismatch")
    return receipt_root


def _assert_false_fields(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not False:
            _fail(f"{label} {field} must remain false")


def _assert_true_fields(payload: Mapping[str, Any], fields: tuple[str, ...], *, label: str) -> None:
    for field in fields:
        if payload.get(field) is not True:
            _fail(f"{label} {field} must be true")


def _validate_candidate(
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
    _assert_frozen_points_contract()
    root = Path(candidate_output_root)
    if not root.is_absolute():
        _fail("candidate output root must be absolute")
    _assert_existing_path(root, label="candidate output root", directory=True)
    if root != root.resolve(strict=True):
        _fail("candidate output root must be canonical and non-reparse")
    expected_sha = _sha256(expected_candidate_publication_sha256, label="candidate publication SHA")
    publication_path = Path(candidate_publication_path)
    if not publication_path.is_absolute() or publication_path != publication_path.resolve(
        strict=True
    ):
        _fail("candidate publication path must be canonical absolute")
    expected_relative = candidate_authority._publication_relative_path(expected_sha)
    expected_path = root.joinpath(*expected_relative.split("/"))
    if publication_path != expected_path:
        _fail("candidate publication content-addressed path rejected")
    manifest, manifest_raw = _read_json(publication_path, label="candidate publication")
    if _sha256_bytes(manifest_raw) != expected_sha:
        _fail("candidate publication SHA mismatch")
    _assert_fields(manifest, _CANDIDATE_MANIFEST_FIELDS, label="candidate publication")
    if manifest["schema"] != candidate_authority.PUBLICATION_MANIFEST_SCHEMA:
        _fail("candidate publication schema rejected")
    if manifest["development_only"] is not True:
        _fail("candidate publication must be development-only")
    for field in ("formal_materialization_eligible", "parent_source_authority_verified", "source_authority_complete"):
        if field in manifest and manifest[field] is not False:
            _fail(f"candidate publication {field} cannot self-promote")
    _assert_false_fields(manifest, candidate_authority.PROVENANCE_FALSE_FIELDS, label="candidate publication")
    _assert_false_fields(manifest, candidate_authority.SAFETY_FALSE_FIELDS, label="candidate publication")

    authority_root = _sha256(manifest["authority_root_sha256"], label="candidate authority root")
    descriptor_sha = _sha256(manifest["descriptor_sha256"], label="candidate descriptor SHA")
    descriptor_relative = candidate_authority._descriptor_relative_path(authority_root)
    if manifest["descriptor_relative_path"] != descriptor_relative:
        _fail("candidate descriptor content-addressed path rejected")
    descriptor_path = _relative_path(root, descriptor_relative, label="candidate descriptor")
    descriptor, descriptor_raw = _read_json(descriptor_path, label="candidate descriptor")
    if _sha256_bytes(descriptor_raw) != descriptor_sha:
        _fail("candidate descriptor SHA mismatch")
    _assert_fields(descriptor, _CANDIDATE_DESCRIPTOR_FIELDS, label="candidate descriptor")
    unsigned_descriptor = dict(descriptor)
    unsigned_descriptor.pop("authority_root_sha256")
    if _canonical_sha256(unsigned_descriptor) != authority_root:
        _fail("candidate descriptor authority root mismatch")
    if descriptor["schema"] != candidate_authority.DESCRIPTOR_SCHEMA or descriptor["authority_status"] != candidate_authority.AUTHORITY_STATUS:
        _fail("candidate descriptor status rejected")
    if descriptor["development_only"] is not True:
        _fail("candidate descriptor must be development-only")
    for field in ("formal_materialization_eligible", "parent_source_authority_verified", "source_authority_complete"):
        if descriptor[field] is not False:
            _fail(f"candidate descriptor {field} cannot self-promote")
    _assert_false_fields(descriptor, candidate_authority.PROVENANCE_FALSE_FIELDS, label="candidate descriptor")
    _assert_false_fields(descriptor, candidate_authority.SAFETY_FALSE_FIELDS, label="candidate descriptor")
    branch = descriptor.get("factor_v2_branch")
    if type(branch) is not dict or branch.get("formal_materialization_eligible") is not False or branch.get("verified") is not False:
        _fail("candidate Factor V2 branch cannot self-promote")
    producer = descriptor.get("producer_snapshot")
    if type(producer) is not dict or manifest["producer_snapshot_root_sha256"] != producer.get("root_sha256"):
        _fail("candidate producer snapshot binding rejected")
    _validate_candidate_producer_snapshot(producer)
    if descriptor["source_spec_sha256"] != manifest["source_spec_sha256"]:
        _fail("candidate source spec binding mismatch")
    _sha256(descriptor["source_spec_sha256"], label="candidate source spec SHA")
    if (
        descriptor["points_contract_sha256"]
        != candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
    ):
        _fail("candidate points contract binding rejected")

    snapshots = descriptor.get("snapshots")
    if type(snapshots) is not dict or set(snapshots) != set(candidate_authority._SNAPSHOT_NAMES):
        _fail("candidate snapshot namespace rejected")
    snapshot_payloads: dict[str, dict[str, Any]] = {}
    snapshot_raw: dict[str, bytes] = {}
    for name in candidate_authority._SNAPSHOT_NAMES:
        entry = _assert_fields(snapshots[name], _SNAPSHOT_DESCRIPTOR_FIELDS, label=f"candidate {name} descriptor")
        if entry["relative_path"] != f"{name}.json":
            _fail(f"candidate {name} path rejected")
        snapshot_path = _relative_path(descriptor_path.parent, entry["relative_path"], label=f"candidate {name}")
        payload, raw = _read_json(snapshot_path, label=f"candidate {name}")
        try:
            candidate_authority._assert_no_sensitive(payload, label=f"candidate {name}")
        except ValueError as exc:
            raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc
        if _sha256_bytes(raw) != _sha256(entry["file_sha256"], label=f"candidate {name} file SHA"):
            _fail(f"candidate {name} file SHA mismatch")
        if _canonical_sha256(payload) != _sha256(entry["payload_root_sha256"], label=f"candidate {name} payload root"):
            _fail(f"candidate {name} payload root mismatch")
        if payload.get("schema") != entry["schema"]:
            _fail(f"candidate {name} schema binding mismatch")
        rows = payload.get("rows")
        expected_count = len(rows) if type(rows) is list else None
        expected_rows_sha = _canonical_sha256(rows) if type(rows) is list else None
        if (
            entry["row_count"] != expected_count
            or entry["rows_sha256"] != expected_rows_sha
            or payload.get("rows_sha256") != expected_rows_sha
        ):
            _fail(f"candidate {name} row metadata mismatch")
        snapshot_payloads[name] = payload
        snapshot_raw[name] = raw
    expected_namespace = {
        "input-authority.json",
        *(f"{name}.json" for name in candidate_authority._SNAPSHOT_NAMES),
    }
    if {child.name for child in descriptor_path.parent.iterdir()} != expected_namespace:
        _fail("candidate snapshot exact namespace rejected")

    calendar = _validate_calendar(snapshot_payloads["calendar"], descriptor["calendar"])
    parent_projection = _validate_candidate_parent(
        snapshot_payloads["factor_v2_parent"],
        development_sessions=snapshot_payloads["calendar"]["development_sessions"],
    )
    points_verdict_root, authority_scope, points_verdict = (
        _validate_points_contract_authority(
        calendar_snapshot=snapshot_payloads["calendar"],
        parent_snapshot=snapshot_payloads["factor_v2_parent"],
        parent_projection=parent_projection,
        )
    )
    board_projection = _validate_board(snapshot_payloads["upstream_board_ledger"], calendar)
    evaluation_binding = _validate_candidate_evaluation(snapshot_payloads["factor_v2_evaluation"])
    _validate_candidate_source_receipts(
        descriptor["source_receipts"], snapshot_payloads, calendar
    )
    _validate_candidate_branch(branch, evaluation_binding)
    source_authority_verdict_roots = _validate_candidate_source_authority_verdicts(
        manifest=manifest,
        descriptor=descriptor,
        descriptor_path=descriptor_path,
        snapshots=snapshot_payloads,
        calendar_snapshot=snapshot_payloads["calendar"],
        authority_scope=authority_scope,
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
    source_authority_receipts = source_authority_verdict_roots.pop(
        "_source_authority_receipts"
    )
    source_authority_verdicts = source_authority_verdict_roots.pop(
        "_authority_verdicts"
    )
    return {
        "root": root,
        "publication_path": publication_path,
        "publication_sha256": expected_sha,
        "manifest": manifest,
        "manifest_raw": manifest_raw,
        "descriptor": descriptor,
        "descriptor_raw": descriptor_raw,
        "descriptor_path": descriptor_path,
        "snapshot_payloads": snapshot_payloads,
        "snapshot_raw": snapshot_raw,
        "calendar": calendar,
        "parent_projection": parent_projection,
        "parent_row_count": len(snapshot_payloads["factor_v2_parent"]["rows"]),
        "board_projection": board_projection,
        "evaluation_binding": evaluation_binding,
        "source_authority_verdict_roots": source_authority_verdict_roots,
        "points_contract_verdict_root_sha256": points_verdict_root,
        "authority_scope": authority_scope,
        "authority_verdicts": {
            "points_contract": points_verdict,
            **source_authority_verdicts,
        },
        "source_authority_receipts": source_authority_receipts,
    }


def _validate_candidate_producer_snapshot(value: Mapping[str, Any]) -> None:
    public_fields = {
        "candidate_snapshot_only",
        "entries",
        "root_sha256",
        "schema",
        *candidate_authority.PROVENANCE_FALSE_FIELDS,
    }
    _assert_fields(value, public_fields, label="candidate producer snapshot")
    if (
        value["candidate_snapshot_only"] is not True
        or value["schema"] != candidate_authority.PRODUCER_SNAPSHOT_SCHEMA
    ):
        _fail("candidate producer snapshot contract rejected")
    _assert_false_fields(
        value,
        candidate_authority.PROVENANCE_FALSE_FIELDS,
        label="candidate producer snapshot",
    )
    entries = value["entries"]
    if type(entries) is not list or not entries:
        _fail("candidate producer source entries rejected")
    seen: set[str] = set()
    for entry in entries:
        _assert_fields(entry, {"bytes", "path", "sha256"}, label="candidate producer source entry")
        if (
            type(entry["bytes"]) is not int
            or entry["bytes"] <= 0
            or type(entry["path"]) is not str
            or not entry["path"]
            or "\\" in entry["path"]
            or entry["path"] in seen
        ):
            _fail("candidate producer source entry rejected")
        seen.add(entry["path"])
        _sha256(entry["sha256"], label="candidate producer source SHA")
    unsigned = dict(value)
    root = _sha256(unsigned.pop("root_sha256"), label="candidate producer root")
    if _canonical_sha256(unsigned) != root:
        _fail("candidate producer snapshot root rejected")
    if dict(value) != candidate_authority._candidate_producer_snapshot():
        _fail("candidate producer snapshot does not match loaded source bytes")


def _validate_candidate_source_receipts(
    value: Any,
    snapshots: Mapping[str, dict[str, Any]],
    calendar: Mapping[str, Any],
) -> None:
    receipts = _assert_fields(
        value, {"daily_basic", "feature_history"}, label="candidate source receipts"
    )
    history_fields = {
        "authority_status",
        "feature_history_only",
        "receipt_sha256",
        "schema_version",
        "session_count",
        "sessions_sha256",
        "source_authority_root_sha256",
        "verified",
    }
    daily_fields = {
        "authority_root_sha256",
        "authority_scope",
        "authority_status",
        "normalized_daily_basic_row_authority_root_sha256",
        "row_authority_status",
        "schema",
        "trade_date_count",
        "trade_dates_sha256",
    }
    history = _assert_fields(
        receipts["feature_history"], history_fields, label="feature-history source summary"
    )
    daily = _assert_fields(
        receipts["daily_basic"], daily_fields, label="daily-basic source summary"
    )
    if (
        history["verified"] is not True
        or history["feature_history_only"] is not True
        or history["authority_status"] != "VERIFIED_FEATURE_HISTORY_ONLY"
        or history["schema_version"]
        != "audited-pit-factor-v3-feature-history-authority-receipt/v3"
        or history["session_count"] != 250
        or history["sessions_sha256"] != calendar["prewindow_sessions_sha256"]
        or daily["trade_date_count"] != 733
        or daily["trade_dates_sha256"] != calendar["all_market_sessions_sha256"]
        or daily["authority_scope"]
        != "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY"
        or daily["authority_status"]
        != "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET"
        or daily["row_authority_status"]
        != "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY"
        or daily["schema"] != "factor-v3-daily-basic-733-exact-set-receipt/v2"
    ):
        _fail("candidate source receipt summaries rejected")
    for field in ("receipt_sha256", "sessions_sha256", "source_authority_root_sha256"):
        _sha256(history[field], label=f"feature-history source summary {field}")
    for field in (
        "authority_root_sha256",
        "normalized_daily_basic_row_authority_root_sha256",
        "trade_dates_sha256",
    ):
        _sha256(daily[field], label=f"daily-basic source summary {field}")
    if (
        snapshots["feature_history_receipt"].get("source_receipt_sha256")
        != history["receipt_sha256"]
        or snapshots["daily_basic_exact_set_receipt"].get(
            "source_authority_root_sha256"
        )
        != daily["authority_root_sha256"]
    ):
        _fail("candidate source summaries do not bind receipt projections")
    history_projection = snapshots["feature_history_receipt"][
        "source_receipt_projection"
    ]
    for field in (
        "authority_status",
        "feature_history_only",
        "schema_version",
        "session_count",
        "sessions_sha256",
        "source_authority_root_sha256",
        "verified",
    ):
        if history_projection.get(field) != history[field]:
            _fail("candidate feature-history summary projection mismatch")
    daily_projection = snapshots["daily_basic_exact_set_receipt"][
        "source_receipt_projection"
    ]
    for field in (
        "authority_scope",
        "authority_status",
        "normalized_daily_basic_row_authority_root_sha256",
        "row_authority_status",
        "schema",
        "trade_date_count",
        "trade_dates_sha256",
    ):
        if daily_projection.get(field) != daily[field]:
            _fail("candidate daily-basic summary projection mismatch")


def _validate_candidate_branch(
    value: Mapping[str, Any], evaluation_binding: Mapping[str, Any]
) -> None:
    fields = {
        "arm_decisions",
        "arm_order",
        "contract_binding_validated",
        "embargo_consumed",
        "evaluation_artifact_sha256",
        "final_oos_consumed",
        "formal_materialization_eligible",
        "low_rvol_overlay_status",
        "production_recommendation_eligible",
        "publisher_terminal_chain_verified",
        "receipt_sha256",
        "schema_version",
        "selected_arm",
        "selected_branch",
        "selection_rule",
        "source_decision_receipt_raw_file_sha256",
        "source_decision_receipt_sha256",
        "source_authority_complete",
        "verified",
    }
    branch = _assert_fields(value, fields, label="candidate Factor V2 branch receipt")
    if (
        branch["schema_version"]
        != "factor-v2-decision-branch-structural-adapter/v2"
        or branch["contract_binding_validated"] is not True
        or branch["publisher_terminal_chain_verified"] is not False
        or branch["source_authority_complete"] is not False
        or branch["formal_materialization_eligible"] is not False
        or branch["verified"] is not False
        or branch["embargo_consumed"] is not False
        or branch["final_oos_consumed"] is not False
        or branch["production_recommendation_eligible"] is not False
    ):
        _fail("candidate Factor V2 branch receipt claims rejected")
    unsigned = dict(branch)
    branch_root = _sha256(
        unsigned.pop("receipt_sha256"), label="candidate Factor V2 branch receipt root"
    )
    if _canonical_sha256(unsigned) != branch_root:
        _fail("candidate Factor V2 branch receipt self hash rejected")
    decisions = branch["arm_decisions"]
    arm_order = branch["arm_order"]
    if (
        type(decisions) is not dict
        or type(arm_order) is not list
        or arm_order != list(factor_v2_branch_selector.ARM_ORDER)
        or set(decisions) != set(arm_order)
        or any(decisions.get(arm) not in {"GREEN", "RED"} for arm in arm_order)
    ):
        _fail("candidate Factor V2 branch decision map rejected")
    expected_selected = next(
        (arm for arm in arm_order if decisions[arm] == "GREEN"),
        factor_v2_branch_selector.LOW_RVOL_BRANCH,
    )
    expected_arm = expected_selected if expected_selected in arm_order else None
    expected_low_rvol_status = "VOID" if expected_arm is not None else "ELIGIBLE"
    if (
        branch["selection_rule"] != factor_v2_branch_selector.SELECTION_RULE
        or branch["selected_branch"] != expected_selected
        or branch["selected_arm"] != expected_arm
        or branch["low_rvol_overlay_status"] != expected_low_rvol_status
    ):
        _fail("candidate Factor V2 branch selection rejected")
    if (
        branch["receipt_sha256"] != evaluation_binding["branch_receipt_sha256"]
        or branch["source_decision_receipt_sha256"]
        != evaluation_binding["decision_receipt_sha256"]
        or branch["source_decision_receipt_raw_file_sha256"]
        != evaluation_binding["decision_receipt_raw_file_sha256"]
        or branch["evaluation_artifact_sha256"]
        != evaluation_binding["evaluation_artifact_sha256"]
        or branch["selected_branch"] != evaluation_binding["selected_branch"]
    ):
        _fail("candidate Factor V2 branch/evaluation snapshot binding rejected")


def _validate_candidate_source_authority_verdicts(
    *,
    manifest: Mapping[str, Any],
    descriptor: Mapping[str, Any],
    descriptor_path: Path,
    snapshots: Mapping[str, dict[str, Any]],
    calendar_snapshot: Mapping[str, Any],
    authority_scope: str,
    feature_history_collection_issuance_path: str | Path,
    expected_feature_history_collection_issuance_sha256: str,
    feature_history_frozen_attestation_path: str | Path,
    expected_feature_history_frozen_attestation_sha256: str,
    daily_basic_authority_receipt_path: str | Path,
    expected_daily_basic_authority_receipt_sha256: str,
) -> dict[str, Any]:
    history_path, history_issuance, history_raw, history_file_sha = _read_receipt(
        feature_history_collection_issuance_path,
        expected_feature_history_collection_issuance_sha256,
        category="feature_history_collection_publication_receipts",
        label="feature-history collection publication issuance",
    )
    attestation_path, attestation, attestation_raw, attestation_file_sha = _read_receipt(
        feature_history_frozen_attestation_path,
        expected_feature_history_frozen_attestation_sha256,
        category="factor_v3_feature_history_frozen_source_attestations",
        label="feature-history frozen-source attestation",
    )
    daily_path, daily_receipt, daily_raw, daily_file_sha = _read_receipt(
        daily_basic_authority_receipt_path,
        expected_daily_basic_authority_receipt_sha256,
        category="factor_v3_daily_basic_733_receipts",
        label="daily-basic authority receipt",
    )
    formal_schema_required = authority_scope == FORMAL_AUTHORITY_SCOPE
    history_snapshot = snapshots["feature_history_receipt"]
    history_receipt = {
        **history_snapshot["source_receipt_projection"],
        "receipt_sha256": history_snapshot["source_receipt_sha256"],
    }
    try:
        if (
            set(history_issuance)
            != {
                "authority_manifest_sha256",
                "publication_capability_sha256",
                "publication_schema",
                "publication_status",
                "schema",
            }
            or history_issuance["schema"]
            != "audited-pit-factor-v3-feature-history-publication-issuance/v1"
            or history_issuance["publication_schema"]
            != "audited-pit-factor-v3-feature-history-collection-publication/v1"
            or history_issuance["publication_status"]
            != "DURABLE_POSTVERIFIED_AND_RETURNED"
            or history_issuance["authority_manifest_sha256"]
            != history_receipt.get("collection_publication_manifest_sha256")
        ):
            _fail("feature-history collection publication issuance rejected")
        if (
            frozen_history._validated_attestation(
                attestation_path=attestation_path,
                expected_attestation_sha256=attestation_file_sha,
            )
            != attestation
        ):
            _fail("feature-history frozen-source attestation rejected")
        attested_feature = frozen_history._validated_feature_identity(
            attestation.get("feature_history")
        )
        expected_issuance_relative = "/".join(history_path.parts[-4:])
        authority_manifest_parts = attested_feature[
            "authority_manifest_relative_path"
        ].split("/")
        if (
            len(authority_manifest_parts) != 4
            or authority_manifest_parts[-3] != "sha256"
            or authority_manifest_parts[-2]
            != history_issuance["authority_manifest_sha256"][:2]
            or authority_manifest_parts[-1]
            != f"{history_issuance['authority_manifest_sha256']}.json"
            or not Path(attested_feature["feature_run_root"]).is_absolute()
            or not Path(attested_feature["feature_run_spec_path"]).is_absolute()
            or any(
            (
                attested_feature.get(field) != expected
                for field, expected in {
                    "authority_manifest_sha256": history_issuance[
                        "authority_manifest_sha256"
                    ],
                    "pit_store_database_sha256": history_receipt.get(
                        "pit_store_database_sha256"
                    ),
                    "publication_capability_sha256": history_issuance[
                        "publication_capability_sha256"
                    ],
                    "publication_issuance_relative_path": expected_issuance_relative,
                    "publication_issuance_sha256": history_file_sha,
                    "receipt_sha256": history_receipt["receipt_sha256"],
                    "session_count": len(calendar_snapshot["prewindow_sessions"]),
                    "sessions_sha256": history_receipt["sessions_sha256"],
                    "snapshot_index_sha256": history_receipt.get(
                        "snapshot_index_sha256"
                    ),
                    "source_authority_root_sha256": history_receipt[
                        "source_authority_root_sha256"
                    ],
                }.items()
            )
            )
        ):
            _fail("feature-history attestation/receipt/issuance binding rejected")
        candidate_authority.validate_factor_v3_public_source_receipt_projection(
            source_receipt=history_receipt,
            projected_snapshot=snapshots["feature_history_receipt"],
            kind="feature_history_v3",
            sessions=calendar_snapshot["prewindow_sessions"],
            formal_schema_required=formal_schema_required,
        )
        candidate_authority.validate_factor_v3_public_source_receipt_projection(
            source_receipt=daily_receipt,
            projected_snapshot=snapshots["daily_basic_exact_set_receipt"],
            kind="daily_basic_733_v2",
            sessions=calendar_snapshot["all_market_sessions"],
            formal_schema_required=formal_schema_required,
        )
        _validate_candidate_receipt_snapshots(snapshots, calendar_snapshot)
    except ValueError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc

    derived_snapshot_roots = {
        name: descriptor["snapshots"][name]["payload_root_sha256"]
        for name in (
            "daily_basic",
            "daily_traded_cross_section",
            "listing_membership",
            "security_code_transitions",
            "suspensions",
            "upstream_board_ledger",
        )
    }
    history = snapshots["feature_history_receipt"]
    history_entry = descriptor["snapshots"]["feature_history_receipt"]
    history_source = history["source_receipt_projection"]
    history_evidence = {
        "candidate_authority_root_sha256": manifest["authority_root_sha256"],
        "candidate_descriptor_sha256": manifest["descriptor_sha256"],
        "candidate_producer_snapshot_root_sha256": descriptor["producer_snapshot"][
            "root_sha256"
        ],
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
            descriptor_path.parent / history_entry["relative_path"]
        ),
        "feature_history_projection_root_sha256": history_entry[
            "payload_root_sha256"
        ],
        "source_authority_root_sha256": history_source[
            "source_authority_root_sha256"
        ],
        "source_receipt_projection_sha256": history[
            "source_receipt_projection_sha256"
        ],
        "source_receipt_sha256": history["source_receipt_sha256"],
    }
    history_verdict_root, history_scope, history_verdict = (
        _invoke_scoped_authority_verifier(
        _verify_feature_history_authority_evidence,
        evidence=history_evidence,
        schema=FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA,
        label="native feature-history authority",
        )
    )

    daily = snapshots["daily_basic_exact_set_receipt"]
    daily_entry = descriptor["snapshots"]["daily_basic_exact_set_receipt"]
    daily_source = daily["source_receipt_projection"]
    daily_evidence = {
        "candidate_authority_root_sha256": manifest["authority_root_sha256"],
        "candidate_descriptor_sha256": manifest["descriptor_sha256"],
        "candidate_producer_snapshot_root_sha256": descriptor["producer_snapshot"][
            "root_sha256"
        ],
        "daily_basic_authority_receipt_file_sha256": daily_file_sha,
        "daily_basic_authority_receipt_path": str(daily_path),
        "daily_basic_projection_file_sha256": daily_entry["file_sha256"],
        "daily_basic_projection_path": str(
            descriptor_path.parent / daily_entry["relative_path"]
        ),
        "daily_basic_projection_root_sha256": daily_entry["payload_root_sha256"],
        "derived_snapshot_roots_sha256": _canonical_sha256(derived_snapshot_roots),
        "normalized_daily_basic_row_authority_root_sha256": daily_source[
            "normalized_daily_basic_row_authority_root_sha256"
        ],
        "source_authority_root_sha256": daily["source_authority_root_sha256"],
        "source_receipt_projection_sha256": daily[
            "source_receipt_projection_sha256"
        ],
        "source_receipt_sha256": daily_file_sha,
    }
    daily_verdict_root, daily_scope, daily_verdict = (
        _invoke_scoped_authority_verifier(
        _verify_daily_basic_authority_evidence,
        evidence=daily_evidence,
        schema=DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA,
        label="native daily-basic authority",
        )
    )
    if history_scope != authority_scope or daily_scope != authority_scope:
        _fail("candidate source authority scope mismatch")
    return {
        "daily_basic_native_verdict_root_sha256": daily_verdict_root,
        "feature_history_native_verdict_root_sha256": history_verdict_root,
        "_authority_verdicts": {
            "daily_basic": daily_verdict,
            "feature_history": history_verdict,
        },
        "_source_authority_receipts": {
            "daily_basic": {
                "file_sha256": daily_file_sha,
                "path": daily_path,
                "raw": daily_raw,
            },
            "feature_history_collection_issuance": {
                "file_sha256": history_file_sha,
                "path": history_path,
                "raw": history_raw,
            },
            "feature_history_frozen_attestation": {
                "file_sha256": attestation_file_sha,
                "path": attestation_path,
                "raw": attestation_raw,
            },
        },
    }


def _validate_calendar(payload: Any, descriptor_calendar: Any) -> dict[str, Any]:
    fields = frozenset(
        {
            "all_market_session_count", "all_market_sessions", "all_market_sessions_sha256",
            "development_session_count", "development_sessions", "development_sessions_sha256",
            "prewindow_session_count", "prewindow_sessions", "prewindow_sessions_sha256",
            "schema", "source_date_count", "source_dates", "source_dates_sha256",
        }
    )
    calendar = _assert_fields(payload, fields, label="candidate calendar")
    if calendar["schema"] != "factor-v3-development-calendar-snapshot/v2":
        _fail("candidate calendar schema rejected")
    arrays = {
        "all_market": calendar["all_market_sessions"],
        "development": calendar["development_sessions"],
        "prewindow": calendar["prewindow_sessions"],
        "source": calendar["source_dates"],
    }
    for name, rows in arrays.items():
        if type(rows) is not list or any(type(item) is not str for item in rows) or rows != sorted(set(rows)):
            _fail(f"candidate calendar {name} sessions rejected")
    field_map = {
        "all_market": ("all_market_session_count", "all_market_sessions_sha256"),
        "development": ("development_session_count", "development_sessions_sha256"),
        "prewindow": ("prewindow_session_count", "prewindow_sessions_sha256"),
        "source": ("source_date_count", "source_dates_sha256"),
    }
    for name, rows in arrays.items():
        count_field, hash_field = field_map[name]
        if calendar[count_field] != len(rows) or _canonical_sha256(rows) != calendar[hash_field]:
            _fail(f"candidate calendar {name} count/hash rejected")
    for field, count in EXACT_CALENDAR_COUNTS.items():
        if calendar[field] != count:
            _fail(f"candidate calendar {field} rejected")
    if arrays["all_market"] != [*arrays["prewindow"], *arrays["development"]] or arrays["source"] != arrays["all_market"][:-1]:
        _fail("candidate calendar session partition rejected")
    projection = {
        **EXACT_CALENDAR_COUNTS,
        "all_market_sessions_sha256": calendar["all_market_sessions_sha256"],
        "prewindow_sessions_sha256": calendar["prewindow_sessions_sha256"],
        "development_sessions_sha256": calendar["development_sessions_sha256"],
        "source_dates_sha256": calendar["source_dates_sha256"],
    }
    _assert_fields(descriptor_calendar, frozenset(projection), label="candidate descriptor calendar")
    if descriptor_calendar != projection:
        _fail("candidate descriptor calendar binding mismatch")
    return {**projection, "source_dates": list(arrays["source"]), "prewindow_sessions": list(arrays["prewindow"])}


def _validate_candidate_parent(
    payload: Any, *, development_sessions: list[str]
) -> dict[str, Any]:
    if type(payload) is not dict:
        _fail("candidate parent snapshot fields rejected")
    minimal_fields = {
        "derived_adapter",
        "parent_hash_binding_receipt",
        "rows",
        "rows_sha256",
        "schema",
    }
    expectation = candidate_authority.points.FACTOR_V3_POINTS_CONTRACT[
        "preregistered_parent_expectation"
    ]
    public_fields = {
        *minimal_fields,
        *expectation,
        "candidate_identity_root_sha256",
        "source_file_sha256",
        "validator",
    }
    if set(payload) != public_fields:
        _fail("candidate parent snapshot fields rejected")
    parent = payload
    if parent["schema"] != "factor-v3-development-factor-v2-parent-snapshot/v2":
        _fail("candidate parent snapshot schema rejected")
    rows = parent["rows"]
    if type(rows) is not list:
        _fail("candidate parent rows rejected")
    row_fields = {"candidate_key", "features", "signal_date", "ts_code"}
    development_session_set = set(development_sessions)
    if len(development_session_set) != len(development_sessions):
        _fail("candidate development session set rejected")
    for row in rows:
        _assert_fields(row, row_fields, label="candidate parent row")
        if (
            type(row["candidate_key"]) is not str
            or type(row["signal_date"]) is not str
            or type(row["ts_code"]) is not str
        ):
            _fail("candidate parent row identity rejected")
        try:
            signal_date = date.fromisoformat(row["signal_date"])
        except ValueError as exc:
            raise FactorV3FormalDevelopmentInputActivationError(
                "candidate parent signal date rejected"
            ) from exc
        if (
            signal_date.isoformat() != row["signal_date"]
            or not candidate_authority.research_scope.is_mainboard_chinext_symbol(
                row["ts_code"]
            )
            or row["candidate_key"]
            != f"cn-a-share:{row['ts_code']}|{row['signal_date']}"
            or row["signal_date"] not in development_session_set
        ):
            _fail("candidate parent key/date/code binding rejected")
        features = row["features"]
        if (
            type(features) is not list
            or len(features) != 10
            or any(
                type(item) not in (int, float) or not math.isfinite(float(item))
                for item in features
            )
        ):
            _fail("candidate parent ten-feature vector rejected")
    ordered = sorted(rows, key=lambda row: (row["signal_date"], row["candidate_key"]))
    if rows != ordered or _canonical_sha256(rows) != parent["rows_sha256"]:
        _fail("candidate parent row order/hash rejected")
    candidate_keys = sorted(row["candidate_key"] for row in rows)
    source_rows = [
        {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"], "features": row["features"]}
        for row in rows
    ]
    projection = {
        "schema": PARENT_PROJECTION_SCHEMA,
        "contract": deepcopy(PARENT_PROJECTION_CONTRACT),
        "candidate_keys_sha256": _canonical_sha256(candidate_keys),
        "source_feature_projection_rows_sha256": _canonical_sha256(source_rows),
        "full_export_rows_sha256": _canonical_sha256(rows),
    }
    adapter_core = {
        "schema": "factor-v3-factor-v2-parent-row-derived-adapter/v1",
        "candidate_key_projection_fields": ["candidate_key"],
        "candidate_key_order": ["candidate_key"],
        "source_feature_projection_fields": ["candidate_key", "signal_date", "features"],
        "source_feature_order": ["signal_date", "candidate_key"],
        "full_export_fields": ["candidate_key", "features", "signal_date", "ts_code"],
        "full_export_order": ["signal_date", "candidate_key"],
        **{field: projection[field] for field in PARENT_PROJECTION_FIELDS},
    }
    adapter = parent["derived_adapter"]
    expected_adapter_fields = {
        *adapter_core,
        "added_identity_field",
        "materializer_v1_hash_compatible",
        "pinned_parent_adapter_source_spec_sha256",
    }
    _assert_fields(adapter, expected_adapter_fields, label="candidate parent derived adapter")
    if any(adapter[field] != value for field, value in adapter_core.items()):
        _fail("candidate parent derived adapter contract rejected")
    if (
        adapter["added_identity_field"] != "ts_code_from_candidate_key"
        or adapter["materializer_v1_hash_compatible"] is not False
    ):
        _fail("candidate parent materializer-v1 adapter claim rejected")
    _sha256(
        adapter["pinned_parent_adapter_source_spec_sha256"],
        label="candidate pinned parent adapter source spec",
    )
    binding_minimal = {
        "authority_status": "PINNED_HASH_MATCH_ONLY_SOURCE_PRODUCER_UNVERIFIED",
        "formal_materialization_eligible": False,
        "source_provenance_verified": False,
        **{field: projection[field] for field in PARENT_PROJECTION_FIELDS},
    }
    binding = parent["parent_hash_binding_receipt"]
    binding_fields = {
            "authority_status",
            "candidate_keys_sha256",
            "content_hash_bound",
            "development_only",
            "factor_v2_common_eligible_overlay_artifact_sha256",
            "factor_v2_common_eligible_overlay_manifest_file_sha256",
            "factor_v2_common_eligible_receipt_sha256",
            "factor_v2_parent_artifact_sha256",
            "factor_v2_parent_manifest_file_sha256",
            "file_sha256",
            "full_export_rows_sha256",
            "formal_materialization_eligible",
            "pinned_frozen_source_commit",
            "pinned_frozen_source_tree_oid",
            "pinned_parent_adapter_source_spec_sha256",
            "points_contract_sha256",
            "public_verifier_replay_performed",
            "receipt_sha256",
            "row_count",
            "rows_sha256",
            "schema",
            "source_feature_projection_rows_sha256",
            "source_provenance_verified",
            *candidate_authority.SAFETY_FALSE_FIELDS,
    }
    _assert_fields(binding, binding_fields, label="candidate parent hash binding receipt")
    unsigned_binding = dict(binding)
    binding_sha = _sha256(
        unsigned_binding.pop("receipt_sha256"),
        label="candidate parent hash binding receipt SHA",
    )
    if _canonical_sha256(unsigned_binding) != binding_sha:
        _fail("candidate parent hash binding receipt self hash rejected")
    for field, value in binding_minimal.items():
        if binding.get(field) != value:
            _fail("candidate parent unverified hash binding rejected")
    if (
        binding["content_hash_bound"] is not True
        or binding["development_only"] is not True
        or binding["public_verifier_replay_performed"] is not False
        or binding["row_count"] != len(rows)
        or binding["rows_sha256"] != projection["full_export_rows_sha256"]
        or binding["file_sha256"] != parent["source_file_sha256"]
        or binding["pinned_parent_adapter_source_spec_sha256"]
        != adapter["pinned_parent_adapter_source_spec_sha256"]
        or binding["points_contract_sha256"]
        != candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
        or parent["validator"]
        != "validate_factor_v2_common_eligible_parent_hash_binding"
    ):
        _fail("candidate parent public binding metadata rejected")
    _assert_false_fields(
        binding,
        candidate_authority.SAFETY_FALSE_FIELDS,
        label="candidate parent hash binding receipt",
    )
    for key, value in expectation.items():
        if parent[key] != value:
            _fail(f"candidate parent frozen expectation drifted: {key}")
    identities = [
        {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"]}
        for row in rows
    ]
    if parent["candidate_identity_root_sha256"] != _canonical_sha256(identities):
        _fail("candidate parent identity root rejected")
    return projection


def _validate_points_contract_authority(
    *,
    calendar_snapshot: Mapping[str, Any],
    parent_snapshot: Mapping[str, Any],
    parent_projection: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    _assert_frozen_points_contract()
    development = calendar_snapshot["development_sessions"]
    if type(development) is not list or not development:
        _fail("points development sessions unavailable")
    evidence = {
        "candidate_keys_sha256": parent_projection["candidate_keys_sha256"],
        "development_session_count": len(development),
        "development_session_end": development[-1],
        "development_session_sha256": _canonical_sha256(development),
        "development_session_start": development[0],
        "factor_v3_points_contract_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
        ),
        "full_export_rows_sha256": parent_projection["full_export_rows_sha256"],
        "parent_row_count": len(parent_snapshot["rows"]),
        "preregistered_parent_expectation_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256
        ),
        "source_feature_projection_rows_sha256": parent_projection[
            "source_feature_projection_rows_sha256"
        ],
    }
    return _invoke_scoped_authority_verifier(
        _verify_points_contract_authority_evidence,
        evidence=evidence,
        schema=POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA,
        label="Factor V3 points contract authority",
    )


def _validate_unverified_daily_adapter(value: Any, *, label: str) -> None:
    fields = {
        "candidate_producer_snapshot_root_sha256",
        "schema",
        "source_authority_root_sha256",
        "source_publication_sha256",
        "source_receipt_sha256",
        *candidate_authority.PROVENANCE_FALSE_FIELDS,
    }
    adapter = _assert_fields(value, fields, label=label)
    if adapter["schema"] != candidate_authority.DERIVED_ADAPTER_SCHEMA:
        _fail(f"{label} schema rejected")
    for field in (
        "candidate_producer_snapshot_root_sha256",
        "source_authority_root_sha256",
        "source_publication_sha256",
        "source_receipt_sha256",
    ):
        _sha256(adapter[field], label=f"{label} {field}")
    _assert_false_fields(
        adapter,
        candidate_authority.PROVENANCE_FALSE_FIELDS,
        label=label,
    )


def _validate_board(payload: Any, calendar: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "downstream_scope_filter",
        "per_date",
        "per_date_board_ledger_root_sha256",
        "pre_filter_segment_counts",
        "preserved_before_target_scope_filter",
        "schema",
        "source_segments",
    }
    if type(payload) is not dict or set(payload) not in (fields, {*fields, "derived_adapter"}):
        _fail("candidate upstream board ledger fields rejected")
    board = payload
    if "derived_adapter" in board:
        _validate_unverified_daily_adapter(
            board["derived_adapter"], label="candidate upstream board derived adapter"
        )
    if board["schema"] != "factor-v3-development-upstream-board-ledger/v2" or board["preserved_before_target_scope_filter"] is not True:
        _fail("candidate upstream board preservation rejected")
    if board["downstream_scope_filter"] != "mainboard_chinext_candidate_join_only" or board["source_segments"] != list(UPSTREAM_SOURCE_SEGMENTS):
        _fail("candidate upstream board scope/segments rejected")
    per_date = board["per_date"]
    if type(per_date) is not list or len(per_date) != EXACT_CALENDAR_COUNTS["source_date_count"]:
        _fail("candidate upstream board date count rejected")
    sums = {name: 0 for name in UPSTREAM_SOURCE_SEGMENTS}
    trade_dates: list[str] = []
    for entry in per_date:
        _assert_fields(entry, {"normalized_rows_sha256", "raw_source_rows_sha256", "segment_counts", "trade_date"}, label="candidate upstream board date")
        _sha256(entry["normalized_rows_sha256"], label="normalized board rows SHA")
        _sha256(entry["raw_source_rows_sha256"], label="raw board rows SHA")
        counts = _assert_fields(entry["segment_counts"], set(UPSTREAM_SOURCE_SEGMENTS), label="candidate board segment counts")
        for name in UPSTREAM_SOURCE_SEGMENTS:
            count = counts[name]
            if type(count) is not int or count <= 0:
                _fail("candidate board segment count rejected")
            sums[name] += count
        if type(entry["trade_date"]) is not str:
            _fail("candidate board trade date rejected")
        trade_dates.append(entry["trade_date"])
    if trade_dates != calendar["source_dates"]:
        _fail("candidate upstream board dates mismatch")
    if board["pre_filter_segment_counts"] != sums:
        _fail("candidate upstream board pre-filter totals rejected")
    ledger_root = _canonical_sha256(per_date)
    if board["per_date_board_ledger_root_sha256"] != ledger_root:
        _fail("candidate upstream board ledger root mismatch")
    return {
        "downstream_scope_filter": board["downstream_scope_filter"],
        "preserved_before_target_scope_filter": True,
        "source_date_count": len(per_date),
        "source_segments": list(UPSTREAM_SOURCE_SEGMENTS),
        "upstream_board_ledger_root_sha256": ledger_root,
    }


def _validate_candidate_evaluation(payload: Any) -> dict[str, Any]:
    expected_fields = {"branch_receipt_sha256", "decision_receipt_sha256", "decision_receipt_raw_file_sha256", "evaluation_artifact_sha256", "selected_branch", "schema"}
    source = _assert_fields(payload, expected_fields, label="candidate evaluation snapshot")
    for field in ("branch_receipt_sha256", "decision_receipt_sha256", "decision_receipt_raw_file_sha256", "evaluation_artifact_sha256"):
        _sha256(source[field], label=f"candidate evaluation {field}")
    if type(source["selected_branch"]) is not str or not source["selected_branch"]:
        _fail("candidate evaluation selected branch rejected")
    if source["schema"] != "factor-v3-development-factor-v2-evaluation-snapshot/v2":
        _fail("candidate evaluation snapshot schema rejected")
    return {
        "branch_receipt_sha256": source["branch_receipt_sha256"],
        "decision_receipt_sha256": source["decision_receipt_sha256"],
        "decision_receipt_raw_file_sha256": source["decision_receipt_raw_file_sha256"],
        "evaluation_artifact_sha256": source["evaluation_artifact_sha256"],
        "selected_branch": source["selected_branch"],
        "snapshot_schema": source["schema"],
    }


def _validate_candidate_receipt_snapshots(snapshots: Mapping[str, dict[str, Any]], calendar: Mapping[str, Any]) -> None:
    history = snapshots["feature_history_receipt"]
    source_history = history["source_receipt_projection"]
    if (
        source_history.get("verified") is not True
        or source_history.get("session_count") != 250
        or source_history.get("sessions_sha256")
        != calendar["prewindow_sessions_sha256"]
    ):
        _fail("candidate feature-history source receipt rejected")
    _assert_false_fields(
        source_history,
        (
            "embargo_consumed",
            "experiment_launch_eligible",
            "factor_materialization_eligible",
            "final_oos_consumed",
            "production_profile_registered",
            "production_recommendation_eligible",
        ),
        label="candidate feature-history source receipt projection",
    )
    daily = snapshots["daily_basic_exact_set_receipt"]
    _validate_unverified_daily_adapter(
        daily.get("derived_adapter"),
        label="candidate daily-basic receipt derived adapter",
    )
    source_daily = daily["source_receipt_projection"]
    if (
        source_daily.get("trade_date_count") != 733
        or source_daily.get("trade_dates_sha256")
        != calendar["all_market_sessions_sha256"]
        or source_daily.get("trade_dates")
        != [*calendar["source_dates"], snapshots["calendar"]["all_market_sessions"][-1]]
        or source_daily.get("all_supported_segments_compared_before_scope_filter")
        is not True
        or source_daily.get("factor_v3_development_materialization_input_eligible")
        is not True
        or source_daily.get("formal_factor_v3_materialization_performed") is not False
        or source_daily.get("rows_published") is not False
    ):
        _fail("candidate daily-basic source receipt rejected")
    _assert_false_fields(
        source_daily,
        (
            "embargo_consumed",
            "final_oos_consumed",
            "production_profile_registered",
            "production_recommendation_eligible",
        ),
        label="candidate daily-basic source receipt projection",
    )
    statistics = source_daily["per_date_statistics"]
    if (
        type(statistics) is not list
        or len(statistics) != 733
        or _canonical_sha256(statistics)
        != source_daily["per_date_statistics_sha256"]
    ):
        _fail("candidate daily-basic per-date statistics rejected")
    board_entries = snapshots["upstream_board_ledger"]["per_date"]
    for index, statistic in enumerate(statistics):
        _assert_fields(
            statistic,
            {
                "authoritative_daily_raw_codes_sha256",
                "daily_basic_canonical_rows_sha256",
                "daily_basic_raw_segment_counts",
                "trade_date",
            },
            label="candidate daily-basic per-date statistic",
        )
        counts = _assert_fields(
            statistic["daily_basic_raw_segment_counts"],
            set(UPSTREAM_SOURCE_SEGMENTS),
            label="candidate daily-basic raw segment counts",
        )
        if any(type(counts[name]) is not int or counts[name] <= 0 for name in UPSTREAM_SOURCE_SEGMENTS):
            _fail("candidate daily-basic raw segment counts rejected")
        if statistic["trade_date"] != source_daily["trade_dates"][index]:
            _fail("candidate daily-basic per-date session sequence rejected")
        _sha256(
            statistic["authoritative_daily_raw_codes_sha256"],
            label="candidate daily-basic authoritative raw codes SHA",
        )
        _sha256(
            statistic["daily_basic_canonical_rows_sha256"],
            label="candidate daily-basic canonical rows SHA",
        )
        if index < len(board_entries):
            board_entry = board_entries[index]
            if (
                statistic["trade_date"] != board_entry["trade_date"]
                or statistic["authoritative_daily_raw_codes_sha256"]
                != board_entry["raw_source_rows_sha256"]
                or statistic["daily_basic_canonical_rows_sha256"]
                != board_entry["normalized_rows_sha256"]
                or counts != board_entry["segment_counts"]
            ):
                _fail("candidate board/daily receipt per-date binding rejected")
    daily_snapshot = snapshots["daily_basic"]
    if daily_snapshot.get("source_dates") != calendar["source_dates"] or daily_snapshot.get("source_dates_sha256") != calendar["source_dates_sha256"]:
        _fail("candidate daily-basic source dates rejected")
def _validate_parent_projection(value: Any, expected: Mapping[str, Any]) -> dict[str, Any]:
    projection = _assert_fields(value, {"schema", "contract", *PARENT_PROJECTION_FIELDS}, label="parent projection")
    if projection.get("schema") != PARENT_PROJECTION_SCHEMA or projection.get("contract") != PARENT_PROJECTION_CONTRACT:
        _fail("parent projection schema/contract rejected")
    for field in PARENT_PROJECTION_FIELDS:
        _sha256(projection[field], label=f"parent projection {field}")
    if projection != expected:
        _fail("parent projection does not match candidate replay")
    return projection


def _invoke_authority_verifier(
    verifier: Any,
    *,
    evidence: dict[str, Any],
    schema: str,
    label: str,
) -> str:
    try:
        verdict = verifier(**deepcopy(evidence))
    except Exception as exc:
        raise FactorV3FormalDevelopmentInputActivationError(
            f"{label} rejected evidence"
        ) from exc
    expected = {**evidence, "schema": schema, "verified": True}
    if type(verdict) is not dict or verdict != expected:
        _fail(f"{label} verdict binding rejected")
    return _canonical_sha256(verdict)


def _invoke_scoped_authority_verifier(
    verifier: Any,
    *,
    evidence: dict[str, Any],
    schema: str,
    label: str,
) -> tuple[str, str, dict[str, Any]]:
    if not callable(verifier):
        _fail(f"{label} unavailable")
    try:
        verdict = verifier(**deepcopy(evidence))
    except Exception as exc:
        if isinstance(exc, FactorV3FormalDevelopmentInputActivationError):
            raise
        raise FactorV3FormalDevelopmentInputActivationError(
            f"{label} rejected evidence"
        ) from exc
    if type(verdict) is not dict:
        _fail(f"{label} verdict binding rejected")
    scope = verdict.get("authority_scope")
    if scope not in {FORMAL_AUTHORITY_SCOPE, DISPOSABLE_TEST_AUTHORITY_SCOPE}:
        _fail(f"{label} authority scope rejected")
    if scope == FORMAL_AUTHORITY_SCOPE:
        expected = {
            **evidence,
            "authority_scope": scope,
            "schema": schema,
            "verified": True,
        }
    else:
        expected = {
            **evidence,
            "authority_scope": scope,
            "contract_binding_validated": True,
            "schema": schema,
            "test_fixture_only": True,
            "verified": False,
        }
    if verdict != expected:
        _fail(f"{label} verdict binding rejected")
    return _canonical_sha256(verdict), scope, deepcopy(verdict)


def _validate_parent_receipt(
    *,
    path_value: str | Path,
    expected_sha_value: str,
    candidate: Mapping[str, Any],
    terminal_path_value: str | Path,
    expected_terminal_sha_value: str,
) -> dict[str, Any]:
    disposable = candidate["authority_scope"] == DISPOSABLE_TEST_AUTHORITY_SCOPE
    path, receipt, receipt_raw, file_sha = _read_receipt(
        path_value,
        expected_sha_value,
        category=(
            "disposable-parent-source-contract-receipts"
            if disposable
            else "parent-source-receipts"
        ),
        label="parent-source authority receipt",
    )
    _assert_fields(
        receipt,
        (
            _DISPOSABLE_PARENT_RECEIPT_FIELDS
            if disposable
            else _PARENT_RECEIPT_FIELDS
        ),
        label="parent-source authority receipt",
    )
    receipt_root = _assert_self_hash(receipt, label="parent-source authority receipt")
    if disposable:
        if (
            receipt["schema"] != DISPOSABLE_PARENT_SOURCE_RECEIPT_SCHEMA
            or receipt["authority_status"]
            != "DISPOSABLE_TEST_PARENT_SOURCE_CONTRACT_ONLY"
            or receipt["contract_binding_validated"] is not True
            or receipt["test_fixture_only"] is not True
            or receipt["development_only"] is not True
        ):
            _fail("disposable parent-source contract status rejected")
        _assert_false_fields(
            receipt,
            (
                "formal_materialization_eligible",
                "independent_public_replay_performed",
                "machine_global_root_lease_verified",
                "parent_source_authority_verified",
                "root_epoch_terminal_verified",
                "single_attempt_verified",
                "source_authority_complete",
                "source_authority_verified",
                "verified",
            ),
            label="disposable parent-source contract receipt",
        )
    else:
        if (
            receipt["schema"] != parent_authority.AUTHORITY_RECEIPT_SCHEMA
            or receipt["authority_status"]
            != "VERIFIED_DEVELOPMENT_PARENT_SOURCE_AUTHORITY"
        ):
            _fail("parent-source authority status rejected")
        _assert_true_fields(
            receipt,
            ("development_only", "formal_materialization_eligible", "independent_public_replay_performed", "machine_global_root_lease_verified", "parent_source_authority_verified", "root_epoch_terminal_verified", "single_attempt_verified", "source_authority_complete", "source_authority_verified", "verified"),
            label="parent-source authority receipt",
        )
    _assert_false_fields(receipt, SAFETY_FALSE_FIELDS, label="parent-source authority receipt")
    if receipt["requested_action"] != PARENT_SOURCE_ROOT_ACTION_VERIFY or receipt["observed_root_state"] != PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED or receipt["approved_transition"] != PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY or receipt["terminal_root_state"] != PARENT_SOURCE_ROOT_STATE_TERMINAL:
        _fail("parent-source epoch transition rejected")
    if receipt["native_lease_policy_version"] != PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION:
        _fail("parent-source native lease policy rejected")
    _sha256(receipt["native_lease_identity_sha256"], label="parent-source native lease identity")

    semantic = _sha256(receipt["semantic_input_root_sha256"], label="parent semantic input root")
    attempt = _sha256(receipt["attempt_key_sha256"], label="parent attempt key")
    global_identity = _sha256(receipt["global_attempt_identity_sha256"], label="parent global attempt identity")
    if trusted_supervisor.factor_v3_parent_source_derive_attempt_key_sha256(semantic) != attempt:
        _fail("parent attempt key derivation rejected")
    if trusted_supervisor.factor_v3_parent_source_derive_global_attempt_identity_sha256(attempt) != global_identity:
        _fail("parent global attempt identity derivation rejected")
    run_spec = _sha256(receipt["run_spec_sha256"], label="parent run spec")

    ledger_root_value = receipt["global_attempt_ledger_root"]
    if type(ledger_root_value) is not str:
        _fail("parent global ledger root rejected")
    ledger_root = Path(ledger_root_value)
    if not ledger_root.is_absolute() or str(ledger_root.resolve(strict=False)) != ledger_root_value:
        _fail("parent global ledger root must be canonical absolute")
    _assert_existing_path(ledger_root, label="parent global ledger root", directory=True)
    epoch_dir = ledger_root.joinpath(*PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE.format(prefix=attempt[:2], attempt_key=attempt).split("/"))
    _assert_existing_path(epoch_dir, label="parent epoch directory", directory=True)
    children = {child.name for child in epoch_dir.iterdir()}
    if children != set(PARENT_SOURCE_EPOCH_FILE_NAMES):
        _fail("parent epoch exact four-file namespace rejected")
    paths = {name: epoch_dir / name for name in PARENT_SOURCE_EPOCH_FILE_NAMES}
    expected_paths = {
        "global_run_claim_path": paths["run.claim.json"],
        "global_run_receipt_path": paths["run.receipt.json"],
        "global_verify_claim_path": paths["verify.claim.json"],
        "global_terminal_receipt_path": paths["terminal.receipt.json"],
    }
    for field, expected_path in expected_paths.items():
        if receipt[field] != str(expected_path):
            _fail(f"parent epoch {field} binding rejected")
    terminal_path = Path(terminal_path_value)
    terminal_sha = _sha256(expected_terminal_sha_value, label="expected terminal epoch receipt SHA")
    if terminal_path != paths["terminal.receipt.json"]:
        _fail("terminal epoch receipt canonical path rejected")

    payloads: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    epoch_raw: dict[str, bytes] = {}
    for name, epoch_path in paths.items():
        payload, raw = _read_json(epoch_path, label=f"parent epoch {name}")
        contract = PARENT_SOURCE_EPOCH_FILE_CONTRACT[name]
        _assert_fields(payload, set(contract["fields"]), label=f"parent epoch {name}")
        if payload["schema"] != contract["schema"] or payload["state"] != contract["state"] or ("action" in contract and payload["action"] != contract["action"]):
            _fail(f"parent epoch {name} contract rejected")
        for field, expected in (("attempt_key_sha256", attempt), ("global_attempt_identity_sha256", global_identity), ("run_spec_sha256", run_spec)):
            if payload[field] != expected:
                _fail(f"parent epoch {name} {field} mismatch")
        payloads[name] = payload
        hashes[name] = _sha256_bytes(raw)
        epoch_raw[name] = raw
    if hashes["terminal.receipt.json"] != terminal_sha:
        _fail("terminal epoch receipt SHA mismatch")
    if payloads["run.receipt.json"]["run_claim_sha256"] != hashes["run.claim.json"] or payloads["verify.claim.json"]["run_receipt_sha256"] != hashes["run.receipt.json"] or payloads["terminal.receipt.json"]["run_receipt_sha256"] != hashes["run.receipt.json"] or payloads["terminal.receipt.json"]["verify_claim_sha256"] != hashes["verify.claim.json"]:
        _fail("parent epoch hash chain rejected")
    for field, expected in (
        ("run_claim_sha256", hashes["run.claim.json"]),
        ("run_receipt_sha256", hashes["run.receipt.json"]),
        ("verify_claim_sha256", hashes["verify.claim.json"]),
        ("terminal_receipt_file_sha256", hashes["terminal.receipt.json"]),
    ):
        if receipt[field] != expected:
            _fail(f"parent receipt {field} chain binding rejected")

    projection = _validate_parent_projection(receipt["parent_projection"], candidate["parent_projection"])
    points_projection = _assert_fields(receipt["points_contract_common_eligible_projection"], {"common_eligible_candidate_keys_sha256", "common_eligible_source_feature_rows_sha256"}, label="points common eligible projection")
    if points_projection != {
        "common_eligible_candidate_keys_sha256": projection["candidate_keys_sha256"],
        "common_eligible_source_feature_rows_sha256": projection["source_feature_projection_rows_sha256"],
    }:
        _fail("points common eligible projection cross-binding rejected")
    if receipt["calendar_projection"] != {key: value for key, value in candidate["calendar"].items() if key not in {"source_dates", "prewindow_sessions"}}:
        _fail("parent calendar projection rejected")
    if receipt["upstream_board_projection"] != candidate["board_projection"]:
        _fail("parent upstream board projection rejected")
    native_evidence = {
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
        "run_claim_sha256": hashes["run.claim.json"],
        "run_receipt_sha256": hashes["run.receipt.json"],
        "run_spec_sha256": run_spec,
        "semantic_input_root_sha256": semantic,
        "terminal_receipt_file_sha256": terminal_sha,
        "verify_claim_sha256": hashes["verify.claim.json"],
    }
    native_verdict_root, native_scope, native_verdict = (
        _invoke_scoped_authority_verifier(
        _verify_native_parent_source_authority_evidence,
        evidence=native_evidence,
        schema=PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA,
        label="native parent-source authority",
        )
    )
    if native_scope != candidate["authority_scope"]:
        _fail("native parent-source authority scope mismatch")
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
        "native_verdict_root_sha256": native_verdict_root,
        "native_verdict": native_verdict,
    }


def _validate_evaluation_receipt(
    *, path_value: str | Path, expected_sha_value: str, candidate: Mapping[str, Any]
) -> dict[str, Any]:
    disposable = candidate["authority_scope"] == DISPOSABLE_TEST_AUTHORITY_SCOPE
    path, receipt, receipt_raw, file_sha = _read_receipt(
        path_value,
        expected_sha_value,
        category=(
            "disposable-evaluation-contract-receipts"
            if disposable
            else "evaluation-receipts"
        ),
        label="Factor V2 evaluation authority receipt",
    )
    _assert_fields(
        receipt,
        (
            _DISPOSABLE_EVALUATION_RECEIPT_FIELDS
            if disposable
            else _EVALUATION_RECEIPT_FIELDS
        ),
        label="Factor V2 evaluation authority receipt",
    )
    receipt_root = _assert_self_hash(receipt, label="Factor V2 evaluation authority receipt")
    if disposable:
        if (
            receipt["schema"] != DISPOSABLE_EVALUATION_RECEIPT_SCHEMA
            or receipt["authority_status"]
            != "DISPOSABLE_TEST_FACTOR_V2_EVALUATION_CONTRACT_ONLY"
            or receipt["contract_binding_validated"] is not True
            or receipt["test_fixture_only"] is not True
            or receipt["development_only"] is not True
        ):
            _fail("disposable Factor V2 evaluation contract status rejected")
        _assert_false_fields(
            receipt,
            (
                "formal_materialization_eligible",
                "independent_public_replay_performed",
                "publisher_terminal_chain_verified",
                "source_authority_complete",
                "verified",
            ),
            label="disposable Factor V2 evaluation contract receipt",
        )
    else:
        if receipt["schema"] != FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA or receipt["authority_status"] != "VERIFIED_FORMAL_DEVELOPMENT_EVALUATOR_AUTHORITY":
            _fail("Factor V2 evaluation authority status rejected")
        _assert_true_fields(receipt, ("development_only", "formal_materialization_eligible", "independent_public_replay_performed", "publisher_terminal_chain_verified", "source_authority_complete", "verified"), label="Factor V2 evaluation authority receipt")
    _assert_false_fields(receipt, SAFETY_FALSE_FIELDS, label="Factor V2 evaluation authority receipt")
    projection = _assert_fields(receipt["evaluation_projection"], {"schema", *FACTOR_V2_EVALUATION_PROJECTION_FIELDS}, label="Factor V2 evaluation projection")
    if projection["schema"] != FACTOR_V2_EVALUATION_PROJECTION_SCHEMA:
        _fail("Factor V2 evaluation projection schema rejected")
    for field in FACTOR_V2_EVALUATION_PROJECTION_FIELDS:
        _sha256(projection[field], label=f"Factor V2 evaluation projection {field}")
    binding = _assert_fields(receipt["candidate_evaluation_binding"], set(FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS), label="Factor V2 candidate evaluation binding")
    for field in FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS[:4]:
        _sha256(binding[field], label=f"Factor V2 evaluation binding {field}")
    if binding != candidate["evaluation_binding"]:
        _fail("Factor V2 evaluation source binding mismatch")
    authority_evidence = {
        "candidate_evaluation_binding_sha256": _canonical_sha256(binding),
        "cost_slippage_execution_descriptor_sha256": projection[
            "cost_slippage_execution_descriptor_sha256"
        ],
        "evaluation_authority_receipt_file_sha256": file_sha,
        "evaluation_authority_receipt_root_sha256": receipt_root,
        "evaluation_projection_sha256": _canonical_sha256(projection),
        "evaluator_descriptor_sha256": projection["evaluator_descriptor_sha256"],
        "terminal_decision_descriptor_sha256": projection[
            "terminal_decision_descriptor_sha256"
        ],
    }
    native_verdict_root, native_scope, native_verdict = (
        _invoke_scoped_authority_verifier(
        _verify_factor_v2_evaluation_authority_evidence,
        evidence=authority_evidence,
        schema=FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA,
        label="native Factor V2 evaluation authority",
        )
    )
    if native_scope != candidate["authority_scope"]:
        _fail("native Factor V2 evaluation authority scope mismatch")
    return {
        "path": path,
        "file_sha256": file_sha,
        "receipt_root_sha256": receipt_root,
        "receipt_raw": receipt_raw,
        "projection": deepcopy(projection),
        "binding": deepcopy(binding),
        "native_verdict_root_sha256": native_verdict_root,
        "native_verdict": native_verdict,
    }


def _replay_inputs(
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
    candidate = _validate_candidate(
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
    parent = _validate_parent_receipt(
        path_value=parent_source_authority_receipt_path,
        expected_sha_value=expected_parent_source_authority_receipt_sha256,
        candidate=candidate,
        terminal_path_value=parent_source_terminal_epoch_receipt_path,
        expected_terminal_sha_value=expected_parent_source_terminal_epoch_receipt_sha256,
    )
    evaluation = _validate_evaluation_receipt(
        path_value=factor_v2_evaluation_authority_receipt_path,
        expected_sha_value=expected_factor_v2_evaluation_authority_receipt_sha256,
        candidate=candidate,
    )
    parent_receipt = parent["receipt"]
    bindings = {
        "attempt_key_sha256": parent_receipt["attempt_key_sha256"],
        "candidate_authority_root_sha256": candidate["manifest"]["authority_root_sha256"],
        "candidate_descriptor_sha256": candidate["manifest"]["descriptor_sha256"],
        "candidate_publication_file_sha256": candidate["publication_sha256"],
        "daily_basic_authority_receipt_file_sha256": candidate[
            "source_authority_receipts"
        ]["daily_basic"]["file_sha256"],
        "daily_basic_authority_receipt_path": str(
            candidate["source_authority_receipts"]["daily_basic"]["path"]
        ),
        "factor_v2_evaluation_authority_receipt_file_sha256": evaluation["file_sha256"],
        "factor_v2_evaluation_authority_receipt_root_sha256": evaluation["receipt_root_sha256"],
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
        "global_attempt_identity_sha256": parent_receipt["global_attempt_identity_sha256"],
        "global_attempt_ledger_root": parent_receipt["global_attempt_ledger_root"],
        "global_run_claim_path": parent_receipt["global_run_claim_path"],
        "global_run_receipt_path": parent_receipt["global_run_receipt_path"],
        "global_terminal_receipt_path": parent_receipt["global_terminal_receipt_path"],
        "global_verify_claim_path": parent_receipt["global_verify_claim_path"],
        "native_lease_identity_sha256": parent_receipt["native_lease_identity_sha256"],
        "native_lease_policy_version": parent_receipt["native_lease_policy_version"],
        "parent_source_authority_receipt_file_sha256": parent["file_sha256"],
        "parent_source_authority_receipt_root_sha256": parent["receipt_root_sha256"],
        "parent_source_terminal_receipt_file_sha256": parent["terminal_sha256"],
        "run_spec_sha256": parent_receipt["run_spec_sha256"],
        "semantic_input_root_sha256": parent_receipt["semantic_input_root_sha256"],
    }
    _assert_fields(bindings, set(ACTIVATION_INPUT_BINDING_FIELDS), label="activation input bindings")
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


def _assert_raw_unchanged(path: Path, expected_raw: bytes, *, label: str) -> None:
    _payload, raw = _read_json(path, label=label)
    if raw != expected_raw:
        _fail(f"{label} changed across activation transaction")


def _postverify_inputs(replay: Mapping[str, Any]) -> None:
    candidate = replay["candidate"]
    _assert_raw_unchanged(
        candidate["publication_path"],
        candidate["manifest_raw"],
        label="candidate publication postverification",
    )
    _assert_raw_unchanged(
        candidate["descriptor_path"],
        candidate["descriptor_raw"],
        label="candidate descriptor postverification",
    )
    expected_candidate_names = {
        "input-authority.json",
        *(f"{name}.json" for name in candidate_authority._SNAPSHOT_NAMES),
    }
    if {
        child.name for child in candidate["descriptor_path"].parent.iterdir()
    } != expected_candidate_names:
        _fail("candidate namespace changed across activation transaction")
    for name, raw in candidate["snapshot_raw"].items():
        _assert_raw_unchanged(
            candidate["descriptor_path"].parent / f"{name}.json",
            raw,
            label=f"candidate {name} postverification",
        )
    for name, receipt in candidate["source_authority_receipts"].items():
        _assert_raw_unchanged(
            receipt["path"],
            receipt["raw"],
            label=f"candidate {name} source authority receipt postverification",
        )
    parent = replay["parent"]
    _assert_raw_unchanged(
        parent["path"],
        parent["receipt_raw"],
        label="parent-source receipt postverification",
    )
    if {child.name for child in next(iter(parent["epoch_paths"].values())).parent.iterdir()} != set(
        PARENT_SOURCE_EPOCH_FILE_NAMES
    ):
        _fail("parent epoch namespace changed across activation transaction")
    for name, path in parent["epoch_paths"].items():
        _assert_raw_unchanged(
            path,
            parent["epoch_raw"][name],
            label=f"parent epoch {name} postverification",
        )
    evaluation = replay["evaluation"]
    _assert_raw_unchanged(
        evaluation["path"],
        evaluation["receipt_raw"],
        label="evaluation receipt postverification",
    )


def _input_files(replay: Mapping[str, Any]) -> list[tuple[Path, bytes, str]]:
    candidate = replay["candidate"]
    files = [
        (candidate["publication_path"], candidate["manifest_raw"], "candidate publication"),
        (candidate["descriptor_path"], candidate["descriptor_raw"], "candidate descriptor"),
    ]
    files.extend(
        (
            candidate["descriptor_path"].parent / f"{name}.json",
            raw,
            f"candidate {name}",
        )
        for name, raw in candidate["snapshot_raw"].items()
    )
    files.extend(
        (
            receipt["path"],
            receipt["raw"],
            f"candidate {name} source authority receipt",
        )
        for name, receipt in candidate["source_authority_receipts"].items()
    )
    parent = replay["parent"]
    files.append((parent["path"], parent["receipt_raw"], "parent-source receipt"))
    files.extend(
        (path, parent["epoch_raw"][name], f"parent epoch {name}")
        for name, path in parent["epoch_paths"].items()
    )
    evaluation = replay["evaluation"]
    files.append(
        (evaluation["path"], evaluation["receipt_raw"], "evaluation receipt")
    )
    return files


class _HeldInputFile:
    def __init__(self, path: Path, raw: bytes, *, label: str) -> None:
        self.path = path
        self.raw = raw
        self.label = label
        self._native: Any | None = None
        self._descriptor: int | None = None
        expected_sha = _sha256_bytes(raw)
        if os.name == "nt":
            try:
                self._native = trusted_supervisor._HeldFile(
                    path,
                    expected_sha256=expected_sha,
                    label=label,
                    max_bytes=max(len(raw), 1),
                )
            except Exception as exc:
                raise FactorV3FormalDevelopmentInputActivationError(
                    f"{label} held identity rejected"
                ) from exc
            if self._native.raw != raw:
                self._native.close()
                self._native = None
                _fail(f"{label} changed before held acquisition")
            return
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(str(path), flags)
            opened = os.fstat(descriptor)
            terminal = path.stat()
            chunks: list[bytes] = []
            remaining = opened.st_size
            while remaining:
                chunk = os.read(descriptor, min(1024 * 1024, remaining))
                if not chunk:
                    raise OSError("short held read")
                chunks.append(chunk)
                remaining -= len(chunk)
            os.lseek(descriptor, 0, os.SEEK_SET)
            if (
                not stat.S_ISREG(opened.st_mode)
                or not os.path.samestat(opened, terminal)
                or b"".join(chunks) != raw
            ):
                raise OSError("held identity mismatch")
        except OSError as exc:
            if "descriptor" in locals():
                os.close(descriptor)
            raise FactorV3FormalDevelopmentInputActivationError(
                f"{label} held identity rejected"
            ) from exc
        self._descriptor = descriptor

    def postverify(self) -> None:
        if self._native is not None:
            try:
                self._native.postverify()
            except Exception as exc:
                raise FactorV3FormalDevelopmentInputActivationError(
                    f"{self.label} held postverification failed"
                ) from exc
            return
        assert self._descriptor is not None
        opened = os.fstat(self._descriptor)
        try:
            terminal = self.path.stat()
        except OSError as exc:
            raise FactorV3FormalDevelopmentInputActivationError(
                f"{self.label} held path drifted"
            ) from exc
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(self._descriptor, min(1024 * 1024, remaining))
            if not chunk:
                _fail(f"{self.label} held content drifted")
            chunks.append(chunk)
            remaining -= len(chunk)
        os.lseek(self._descriptor, 0, os.SEEK_SET)
        if not os.path.samestat(opened, terminal) or b"".join(chunks) != self.raw:
            _fail(f"{self.label} held content drifted")

    def close(self) -> None:
        if self._native is not None:
            self._native.close()
            self._native = None
        if self._descriptor is not None:
            os.close(self._descriptor)
            self._descriptor = None


def _hold_files(files: list[tuple[Path, bytes, str]]) -> list[_HeldInputFile]:
    held: list[_HeldInputFile] = []
    try:
        for path, raw, label in files:
            held.append(_HeldInputFile(path, raw, label=label))
    except BaseException:
        for item in reversed(held):
            item.close()
        raise
    return held


def _hold_inputs(replay: Mapping[str, Any]) -> list[_HeldInputFile]:
    return _hold_files(_input_files(replay))


def _postverify_held_inputs(held: list[_HeldInputFile]) -> None:
    for item in held:
        item.postverify()


def _authority_verdict_descriptors(
    replay: Mapping[str, Any],
) -> dict[str, dict[str, str]]:
    descriptors: dict[str, dict[str, str]] = {}
    for name, verdict in sorted(replay["authority_verdicts"].items()):
        raw = _canonical_bytes(verdict)
        digest = _sha256_bytes(raw)
        descriptors[name] = {
            "file_sha256": digest,
            "relative_path": (
                f"{_AUTHORITY_VERDICT_DIRECTORY}/sha256/{digest[:2]}/{digest}.json"
            ),
            "schema": verdict["schema"],
            "verdict_root_sha256": _canonical_sha256(verdict),
        }
    return descriptors


def _descriptor_for(replay: Mapping[str, Any]) -> dict[str, Any]:
    candidate = replay["candidate"]
    parent = replay["parent"]["receipt"]
    evaluation = replay["evaluation"]
    formal = candidate["authority_scope"] == FORMAL_AUTHORITY_SCOPE
    descriptor = {
        "activation_verified": formal,
        "activation_schema": ACTIVATION_SCHEMA,
        "authority_scope": candidate["authority_scope"],
        "authority_verifier_bindings": _authority_verdict_descriptors(replay),
        "authority_status": (
            "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT"
            if formal
            else "DISPOSABLE_TEST_FIXTURE_CONTRACT_REPLAY_ONLY"
        ),
        "calendar": {key: value for key, value in candidate["calendar"].items() if key not in {"source_dates", "prewindow_sessions"}},
        "development_only": True,
        "factor_v2_evaluation_projection": deepcopy(evaluation["projection"]),
        "factor_v2_evaluation_source_binding": deepcopy(evaluation["binding"]),
        "contract_binding_validated": True,
        "formal_materialization_eligible": formal,
        "input_bindings": deepcopy(replay["input_bindings"]),
        "machine_global_root_lease_verified": formal,
        "materializer_v1_compatible": False,
        "materializer_compatibility_reason": (
            "candidate-v3-public-projections-require-factor-v3-materializer-v2"
        ),
        "parent_payload_fields": ["features"],
        "parent_projection": deepcopy(candidate["parent_projection"]),
        "parent_source_authority_verified": formal,
        "points_contract_common_eligible_projection": deepcopy(parent["points_contract_common_eligible_projection"]),
        "root_epoch_terminal_verified": formal,
        "schema_version": MATERIALIZER_INPUT_AUTHORITY_SCHEMA,
        "source_authority_complete": formal,
        "source_descriptors": deepcopy(candidate["descriptor"]["snapshots"]),
        "test_fixture_only": (
            candidate["authority_scope"] == DISPOSABLE_TEST_AUTHORITY_SCOPE
        ),
        "upstream_board_projection": deepcopy(candidate["board_projection"]),
        "verified": formal,
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return descriptor


def _descriptor_relative_path(activation_root: str) -> str:
    return f"{_ACTIVATION_DIRECTORY}/sha256/{activation_root[:2]}/{activation_root}/input-authority.json"


def _publication_relative_path(publication_sha: str) -> str:
    return f"{_PUBLICATION_DIRECTORY}/sha256/{publication_sha[:2]}/{publication_sha}.json"


def _receipt_relative_path(receipt_sha: str) -> str:
    return f"{_VERIFIER_RECEIPT_DIRECTORY}/sha256/{receipt_sha[:2]}/{receipt_sha}.json"


def _manifest_for(descriptor: Mapping[str, Any]) -> tuple[dict[str, Any], str, str]:
    descriptor_raw = _canonical_bytes(descriptor)
    activation_root = _sha256_bytes(descriptor_raw)
    descriptor_relative = _descriptor_relative_path(activation_root)
    manifest = {
        "activation_verified": descriptor["activation_verified"],
        "activation_root_sha256": activation_root,
        "authority_scope": descriptor["authority_scope"],
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": activation_root,
        "development_only": True,
        "contract_binding_validated": True,
        "formal_materialization_eligible": descriptor[
            "formal_materialization_eligible"
        ],
        "materializer_v1_compatible": False,
        "materializer_compatibility_reason": (
            "candidate-v3-public-projections-require-factor-v3-materializer-v2"
        ),
        "schema": PUBLICATION_SCHEMA,
        "source_authority_complete": descriptor["source_authority_complete"],
        "test_fixture_only": (
            descriptor["authority_scope"] == DISPOSABLE_TEST_AUTHORITY_SCOPE
        ),
        "verified": descriptor["verified"],
        **{field: False for field in SAFETY_FALSE_FIELDS},
    }
    return manifest, activation_root, descriptor_relative


def _write_create_only(path: Path, raw: bytes, *, label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    materializer._assert_safe_existing_path(
        path.parent, label=f"{label} parent"
    )
    try:
        with materializer._locked_output_directory(
            path.parent, label=f"{label} parent transaction"
        ):
            _write_create_only_locked(path, raw, label=label)
    except (OSError, ValueError) as exc:
        if isinstance(exc, FactorV3FormalDevelopmentInputActivationError):
            raise
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _write_create_only_locked(path: Path, raw: bytes, *, label: str) -> None:
    temp_path: Path | None = None
    descriptor: int | None = None
    published_new = False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        materializer._assert_safe_existing_path(
            path.parent, label=f"{label} parent"
        )
        parent_identity = materializer._directory_identity(
            path.parent, label=f"{label} parent"
        )
        temp_path = path.parent / (
            f".factor-v3-cas-{os.getpid()}-{secrets.token_hex(16)}.tmp"
        )
        descriptor = os.open(
            str(temp_path),
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_BINARY", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        remaining = memoryview(raw)
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                raise OSError(f"{label} temporary write made no progress")
            remaining = remaining[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        if (
            materializer._directory_identity(
                path.parent, label=f"{label} parent"
            )
            != parent_identity
            or materializer._read_regular_bytes_no_follow(
                temp_path, label=f"{label} temporary artifact"
            )
            != raw
        ):
            raise ValueError(f"{label} temporary create-only bytes drifted")
        try:
            os.link(str(temp_path), str(path))
        except FileExistsError:
            materializer._assert_safe_existing_path(path, label=label)
            if (
                materializer._directory_identity(
                    path.parent, label=f"{label} parent"
                )
                != parent_identity
                or not path.is_file()
                or materializer._read_regular_bytes_no_follow(path, label=label)
                != raw
            ):
                raise ValueError(
                    f"{label} content-addressed artifact already exists with different bytes"
                )
        else:
            published_new = True
            materializer._assert_safe_existing_path(path, label=label)
            if (
                materializer._directory_identity(
                    path.parent, label=f"{label} parent"
                )
                != parent_identity
                or not path.is_file()
                or materializer._read_regular_bytes_no_follow(path, label=label)
                != raw
            ):
                raise ValueError(
                    f"{label} atomic create-only publication did not preserve bytes"
                )
        try:
            temp_path.unlink()
        except OSError as exc:
            if published_new:
                path.unlink(missing_ok=True)
            raise ValueError(
                f"{label} temporary hardlink alias cleanup failed"
            ) from exc
        temp_path = None
    except (OSError, ValueError) as exc:
        if published_new:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _postverify_activation_output(
    *,
    output_root: Path,
    descriptor: Mapping[str, Any],
    manifest: Mapping[str, Any],
    replay: Mapping[str, Any],
    include_publication: bool = True,
) -> None:
    descriptor_raw = _canonical_bytes(descriptor)
    manifest_raw = _canonical_bytes(manifest)
    activation_root = _sha256_bytes(descriptor_raw)
    descriptor_path = output_root.joinpath(
        *_descriptor_relative_path(activation_root).split("/")
    )
    publication_sha = _sha256_bytes(manifest_raw)
    publication_path = output_root.joinpath(
        *_publication_relative_path(publication_sha).split("/")
    )
    _assert_raw_unchanged(
        descriptor_path, descriptor_raw, label="activation descriptor postverification"
    )
    if include_publication:
        _assert_raw_unchanged(
            publication_path,
            manifest_raw,
            label="activation publication postverification",
        )
    expected_names = {
        "input-authority.json",
        *(f"{name}.json" for name in candidate_authority._SNAPSHOT_NAMES),
    }
    if {child.name for child in descriptor_path.parent.iterdir()} != expected_names:
        _fail("activation descriptor namespace changed")
    for name, expected_raw in replay["candidate"]["snapshot_raw"].items():
        _assert_raw_unchanged(
            descriptor_path.parent / f"{name}.json",
            expected_raw,
            label=f"activated source snapshot {name} postverification",
        )
    verdict_descriptors = descriptor["authority_verifier_bindings"]
    for name, verdict in replay["authority_verdicts"].items():
        verdict_descriptor = verdict_descriptors[name]
        _assert_raw_unchanged(
            output_root.joinpath(*verdict_descriptor["relative_path"].split("/")),
            _canonical_bytes(verdict),
            label=f"authority verdict {name} postverification",
        )


def _activation_files(
    *,
    output_root: Path,
    descriptor: Mapping[str, Any],
    manifest: Mapping[str, Any],
    replay: Mapping[str, Any],
    include_publication: bool = True,
) -> list[tuple[Path, bytes, str]]:
    descriptor_raw = _canonical_bytes(descriptor)
    activation_root = _sha256_bytes(descriptor_raw)
    descriptor_path = output_root.joinpath(
        *_descriptor_relative_path(activation_root).split("/")
    )
    manifest_raw = _canonical_bytes(manifest)
    publication_path = output_root.joinpath(
        *_publication_relative_path(_sha256_bytes(manifest_raw)).split("/")
    )
    files = [(descriptor_path, descriptor_raw, "activation descriptor")]
    if include_publication:
        files.append((publication_path, manifest_raw, "activation publication"))
    files.extend(
        (
            descriptor_path.parent / f"{name}.json",
            raw,
            f"activated source snapshot {name}",
        )
        for name, raw in replay["candidate"]["snapshot_raw"].items()
    )
    verdict_descriptors = descriptor["authority_verifier_bindings"]
    files.extend(
        (
            output_root.joinpath(
                *verdict_descriptors[name]["relative_path"].split("/")
            ),
            _canonical_bytes(verdict),
            f"authority verdict {name}",
        )
        for name, verdict in replay["authority_verdicts"].items()
    )
    return files


def _close_all_held_files(held_files: list[Any]) -> None:
    errors: list[BaseException] = []
    for held in reversed(held_files):
        try:
            held.close()
        except BaseException as exc:
            errors.append(exc)
    if errors:
        raise errors[0]


def _safe_output_root(path_value: str | Path, *, label: str) -> Path:
    path = Path(path_value)
    if not path.is_absolute():
        _fail(f"{label} must be absolute")
    try:
        return materializer._safe_output_root(path)
    except (OSError, ValueError) as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _forbidden_roots(replay: Mapping[str, Any]) -> dict[str, Path]:
    roots = {
        "candidate": replay["candidate"]["root"],
        "parent authority": replay["parent"]["path"].parents[3],
        "evaluation authority": replay["evaluation"]["path"].parents[3],
        "machine-global epoch": replay["parent"]["ledger_root"],
    }
    roots.update(
        {
            f"{name} source authority": receipt["path"].parents[3]
            for name, receipt in replay["candidate"][
                "source_authority_receipts"
            ].items()
        }
    )
    return roots


def _publish_factor_v3_formal_development_input_activation(
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
    output_root: str | Path,
    allow_disposable_test_fixture: bool,
) -> dict[str, Any]:
    """Publish one activated eligibility snapshot after every authority gate passes."""

    replay = _replay_inputs(
        candidate_output_root=candidate_output_root,
        candidate_publication_path=candidate_publication_path,
        expected_candidate_publication_sha256=expected_candidate_publication_sha256,
        parent_source_authority_receipt_path=parent_source_authority_receipt_path,
        expected_parent_source_authority_receipt_sha256=expected_parent_source_authority_receipt_sha256,
        parent_source_terminal_epoch_receipt_path=parent_source_terminal_epoch_receipt_path,
        expected_parent_source_terminal_epoch_receipt_sha256=expected_parent_source_terminal_epoch_receipt_sha256,
        factor_v2_evaluation_authority_receipt_path=factor_v2_evaluation_authority_receipt_path,
        expected_factor_v2_evaluation_authority_receipt_sha256=expected_factor_v2_evaluation_authority_receipt_sha256,
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
    authority_scope = replay["candidate"]["authority_scope"]
    if allow_disposable_test_fixture:
        if authority_scope != DISPOSABLE_TEST_AUTHORITY_SCOPE:
            _fail("disposable test activation requires disposable test authority scope")
    elif authority_scope != FORMAL_AUTHORITY_SCOPE:
        _fail("public formal activation rejects disposable test authority scope")
    output_path = Path(output_root)
    _assert_isolated(output_path, _forbidden_roots(replay), label="activation output root")
    output_path = _safe_output_root(output_path, label="activation output root")
    descriptor = _descriptor_for(replay)
    descriptor_raw = _canonical_bytes(descriptor)
    manifest, activation_root, descriptor_relative = _manifest_for(descriptor)
    descriptor_path = output_path.joinpath(*descriptor_relative.split("/"))
    publication_raw = _canonical_bytes(manifest)
    publication_sha = _sha256_bytes(publication_raw)
    publication_relative = _publication_relative_path(publication_sha)
    publication_path = output_path.joinpath(*publication_relative.split("/"))
    held_inputs = _hold_inputs(replay)
    held_outputs: list[_HeldInputFile] = []
    try:
        with materializer._locked_output_directory(
            output_path, label="activation output transaction root"
        ):
            for name in candidate_authority._SNAPSHOT_NAMES:
                snapshot_path = descriptor_path.parent / f"{name}.json"
                _write_create_only(
                    snapshot_path,
                    replay["candidate"]["snapshot_raw"][name],
                    label=f"activated source snapshot {name}",
                )
            for name, verdict in replay["authority_verdicts"].items():
                verdict_descriptor = descriptor["authority_verifier_bindings"][name]
                _write_create_only(
                    output_path.joinpath(
                        *verdict_descriptor["relative_path"].split("/")
                    ),
                    _canonical_bytes(verdict),
                    label=f"authority verdict {name}",
                )
            _write_create_only(
                descriptor_path, descriptor_raw, label="activation descriptor"
            )
            held_outputs.extend(
                _hold_files(
                    _activation_files(
                        output_root=output_path,
                        descriptor=descriptor,
                        manifest=manifest,
                        replay=replay,
                        include_publication=False,
                    )
                )
            )
            _postverify_inputs(replay)
            _postverify_activation_output(
                output_root=output_path,
                descriptor=descriptor,
                manifest=manifest,
                replay=replay,
                include_publication=False,
            )
            _postverify_held_inputs(held_inputs)
            _postverify_held_inputs(held_outputs)
            _write_create_only(
                publication_path, publication_raw, label="activation publication"
            )
    except (OSError, ValueError) as exc:
        if isinstance(exc, FactorV3FormalDevelopmentInputActivationError):
            raise
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc
    finally:
        _close_all_held_files([*held_inputs, *held_outputs])
    return {
        "activation_root_sha256": activation_root,
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": activation_root,
        "publication_relative_path": publication_relative,
        "publication_sha256": publication_sha,
        "schema": PUBLICATION_SCHEMA,
    }


def publish_factor_v3_formal_development_input_activation(
    **kwargs: Any,
) -> dict[str, Any]:
    """Publish only a formally authorized activation; test fixtures are rejected."""

    return _publish_factor_v3_formal_development_input_activation(
        **kwargs,
        allow_disposable_test_fixture=False,
    )


def _publish_disposable_test_factor_v3_formal_development_input_activation(
    **kwargs: Any,
) -> dict[str, Any]:
    """Exercise the contract proof engine without producing formal eligibility."""

    return _publish_factor_v3_formal_development_input_activation(
        **kwargs,
        allow_disposable_test_fixture=True,
    )


def _activation_output_root(publication_path: Path, expected_sha: str) -> Path:
    if not publication_path.is_absolute():
        _fail("activation publication path must be absolute")
    try:
        canonical = publication_path.resolve(strict=True)
    except OSError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(
            "activation publication canonical path unavailable"
        ) from exc
    if publication_path != canonical:
        _fail("activation publication path must be canonical and non-reparse")
    if publication_path.name != f"{expected_sha}.json" or publication_path.parent.name != expected_sha[:2] or publication_path.parent.parent.name != "sha256" or publication_path.parent.parent.parent.name != _PUBLICATION_DIRECTORY:
        _fail("activation publication content-addressed path rejected")
    return publication_path.parents[3]


def verify_factor_v3_formal_development_input_activation(
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
    activation_publication_path: str | Path,
    expected_activation_publication_sha256: str,
    verifier_output_root: str | Path,
) -> dict[str, Any]:
    """Fail closed until a separately registered formal independent TCB exists."""

    from app import (
        factor_v3_formal_development_input_activation_independent_verifier as independent,
    )

    try:
        return independent.verify_factor_v3_formal_development_input_activation_independently(
            candidate_output_root=candidate_output_root,
        candidate_publication_path=candidate_publication_path,
        expected_candidate_publication_sha256=expected_candidate_publication_sha256,
        parent_source_authority_receipt_path=parent_source_authority_receipt_path,
        expected_parent_source_authority_receipt_sha256=expected_parent_source_authority_receipt_sha256,
        parent_source_terminal_epoch_receipt_path=parent_source_terminal_epoch_receipt_path,
        expected_parent_source_terminal_epoch_receipt_sha256=expected_parent_source_terminal_epoch_receipt_sha256,
        factor_v2_evaluation_authority_receipt_path=factor_v2_evaluation_authority_receipt_path,
        expected_factor_v2_evaluation_authority_receipt_sha256=expected_factor_v2_evaluation_authority_receipt_sha256,
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
        activation_publication_path=activation_publication_path,
        expected_activation_publication_sha256=expected_activation_publication_sha256,
        verifier_output_root=verifier_output_root,
            allow_disposable_test_fixture=False,
        )
    except independent.IndependentActivationVerifierError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


def _verify_disposable_test_factor_v3_formal_development_input_activation(
    **kwargs: Any,
) -> dict[str, Any]:
    """Differentially replay a disposable proof-engine artifact with false claims."""

    from app import (
        factor_v3_formal_development_input_activation_independent_verifier as independent,
    )

    try:
        return independent.verify_factor_v3_formal_development_input_activation_independently(
            **kwargs,
            allow_disposable_test_fixture=True,
        )
    except independent.IndependentActivationVerifierError as exc:
        raise FactorV3FormalDevelopmentInputActivationError(str(exc)) from exc


__all__ = (
    "ACTIVATION_SCHEMA", "ACTIVATION_ELIGIBILITY_DESCRIPTOR_SCHEMA",
    "ACTIVATION_INPUT_BINDING_FIELDS", "EXACT_CALENDAR_COUNTS",
    "FACTOR_V2_EVALUATION_PROJECTION_FIELDS", "FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS",
    "FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA", "FACTOR_V2_EVALUATION_PROJECTION_SCHEMA",
    "FactorV3FormalDevelopmentInputActivationError", "INDEPENDENT_VERIFIER_RECEIPT_SCHEMA",
    "FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA",
    "MATERIALIZER_INPUT_AUTHORITY_SCHEMA", "PARENT_PROJECTION_CONTRACT", "PARENT_PROJECTION_FIELDS",
    "PARENT_PROJECTION_SCHEMA", "PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE", "PARENT_SOURCE_EPOCH_FILE_CONTRACT",
    "PARENT_SOURCE_EPOCH_FILE_NAMES", "PARENT_SOURCE_RUN_CLAIM_SCHEMA", "PARENT_SOURCE_RUN_RECEIPT_SCHEMA",
    "PARENT_SOURCE_VERIFY_CLAIM_SCHEMA", "PARENT_SOURCE_TERMINAL_RECEIPT_SCHEMA",
    "PARENT_SOURCE_ROOT_ACTION_RUN", "PARENT_SOURCE_ROOT_ACTION_VERIFY", "PARENT_SOURCE_ATTEMPT_KEY_SCHEMA",
    "PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA", "PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION",
    "PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA",
    "PARENT_SOURCE_ROOT_STATE_EMPTY", "PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED", "PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED",
    "PARENT_SOURCE_ROOT_STATE_TERMINAL", "PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED",
    "PARENT_SOURCE_ROOT_TRANSITION_REJECT", "PARENT_SOURCE_ROOT_TRANSITION_START_RUN",
    "PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY", "PUBLICATION_SCHEMA", "SAFETY_FALSE_FIELDS",
    "UPSTREAM_SOURCE_SEGMENTS", "publish_factor_v3_formal_development_input_activation",
    "verify_factor_v3_formal_development_input_activation",
)
