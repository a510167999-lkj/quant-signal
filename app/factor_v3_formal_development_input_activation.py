"""Factor V3 formal-development input activation TDD contract skeleton."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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
PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE = (
    "attempts/sha256/{prefix}/{attempt_key}"
)
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
MATERIALIZER_INPUT_AUTHORITY_SCHEMA = (
    "audited-pit-factor-v3-development-input-authority/v1"
)
ACTIVATION_INPUT_BINDING_FIELDS = (
    "attempt_key_sha256",
    "candidate_authority_root_sha256",
    "candidate_descriptor_sha256",
    "candidate_publication_file_sha256",
    "factor_v2_evaluation_authority_receipt_file_sha256",
    "factor_v2_evaluation_authority_receipt_root_sha256",
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
    "candidate_keys": {
        "fields": ["candidate_key"],
        "order": ["candidate_key"],
    },
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


class FactorV3FormalDevelopmentInputActivationError(ValueError):
    """Raised when a formal-development activation contract fails closed."""


def publish_factor_v3_formal_development_input_activation(
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
    output_root: str | Path,
) -> dict[str, Any]:
    """Publish one activated input snapshot after all future authority gates pass."""

    raise NotImplementedError("TDD RED: formal development input activation is not implemented")


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
    activation_publication_path: str | Path,
    expected_activation_publication_sha256: str,
    verifier_output_root: str | Path,
) -> dict[str, Any]:
    """Independently replay one activation and publish a create-once receipt."""

    raise NotImplementedError("TDD RED: independent activation verifier is not implemented")


__all__ = (
    "ACTIVATION_SCHEMA",
    "ACTIVATION_INPUT_BINDING_FIELDS",
    "EXACT_CALENDAR_COUNTS",
    "FACTOR_V2_EVALUATION_PROJECTION_FIELDS",
    "FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS",
    "FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA",
    "FACTOR_V2_EVALUATION_PROJECTION_SCHEMA",
    "FactorV3FormalDevelopmentInputActivationError",
    "INDEPENDENT_VERIFIER_RECEIPT_SCHEMA",
    "MATERIALIZER_INPUT_AUTHORITY_SCHEMA",
    "PARENT_PROJECTION_CONTRACT",
    "PARENT_PROJECTION_FIELDS",
    "PARENT_PROJECTION_SCHEMA",
    "PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE",
    "PARENT_SOURCE_EPOCH_FILE_CONTRACT",
    "PARENT_SOURCE_EPOCH_FILE_NAMES",
    "PARENT_SOURCE_RUN_CLAIM_SCHEMA",
    "PARENT_SOURCE_RUN_RECEIPT_SCHEMA",
    "PARENT_SOURCE_VERIFY_CLAIM_SCHEMA",
    "PARENT_SOURCE_TERMINAL_RECEIPT_SCHEMA",
    "PARENT_SOURCE_ROOT_ACTION_RUN",
    "PARENT_SOURCE_ROOT_ACTION_VERIFY",
    "PARENT_SOURCE_ATTEMPT_KEY_SCHEMA",
    "PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA",
    "PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION",
    "PARENT_SOURCE_ROOT_STATE_EMPTY",
    "PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED",
    "PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED",
    "PARENT_SOURCE_ROOT_STATE_TERMINAL",
    "PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED",
    "PARENT_SOURCE_ROOT_TRANSITION_REJECT",
    "PARENT_SOURCE_ROOT_TRANSITION_START_RUN",
    "PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY",
    "PUBLICATION_SCHEMA",
    "SAFETY_FALSE_FIELDS",
    "UPSTREAM_SOURCE_SEGMENTS",
    "publish_factor_v3_formal_development_input_activation",
    "verify_factor_v3_formal_development_input_activation",
)
