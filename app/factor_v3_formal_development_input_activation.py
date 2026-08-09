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
PARENT_SOURCE_TERMINAL_EPOCH_RECEIPT_SCHEMA = (
    "factor-v3-parent-source-machine-global-terminal-epoch-receipt/v1"
)
MATERIALIZER_INPUT_AUTHORITY_SCHEMA = (
    "audited-pit-factor-v3-development-input-authority/v1"
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
PARENT_SOURCE_ROOT_STATE_COMPLETED = 2
PARENT_SOURCE_ROOT_STATE_TERMINAL = 3
PARENT_SOURCE_ROOT_ACTION_VERIFY = 2
PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY = 3


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
    "EXACT_CALENDAR_COUNTS",
    "FACTOR_V2_EVALUATION_PROJECTION_FIELDS",
    "FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA",
    "FACTOR_V2_EVALUATION_PROJECTION_SCHEMA",
    "FactorV3FormalDevelopmentInputActivationError",
    "INDEPENDENT_VERIFIER_RECEIPT_SCHEMA",
    "MATERIALIZER_INPUT_AUTHORITY_SCHEMA",
    "PARENT_PROJECTION_CONTRACT",
    "PARENT_PROJECTION_FIELDS",
    "PARENT_PROJECTION_SCHEMA",
    "PARENT_SOURCE_ROOT_ACTION_VERIFY",
    "PARENT_SOURCE_ROOT_STATE_COMPLETED",
    "PARENT_SOURCE_ROOT_STATE_TERMINAL",
    "PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY",
    "PARENT_SOURCE_TERMINAL_EPOCH_RECEIPT_SCHEMA",
    "PUBLICATION_SCHEMA",
    "SAFETY_FALSE_FIELDS",
    "UPSTREAM_SOURCE_SEGMENTS",
    "publish_factor_v3_formal_development_input_activation",
    "verify_factor_v3_formal_development_input_activation",
)
