"""Data-only frozen contract shared by independent materializer-v2 programs."""

from __future__ import annotations

from types import MappingProxyType

from app import research_goal_contract as research_goal


FORMAL_ACTIVATION_INDEPENDENT_RECEIPT_SCHEMA = (
    "factor-v3-formal-development-input-activation-independent-verifier-receipt/v2"
)
FORMAL_MATERIALIZATION_RUN_SPEC_SCHEMA = (
    "audited-pit-factor-v3-formal-materialization-run-spec/v2"
)
FORMAL_MATERIALIZATION_SQLITE_SCHEMA = (
    "audited-pit-factor-v3-formal-development-dataset-sqlite/v2"
)
FORMAL_MATERIALIZATION_PRODUCER_RECEIPT_SCHEMA = (
    "audited-pit-factor-v3-formal-development-materialization-producer-receipt/v2"
)
FORMAL_MATERIALIZATION_INDEPENDENT_RECEIPT_SCHEMA = (
    "audited-pit-factor-v3-formal-development-materialization-independent-verifier-receipt/v2"
)
FORMAL_MATERIALIZATION_PUBLICATION_SCHEMA = (
    "audited-pit-factor-v3-formal-development-materialization-publication/v2"
)
DISPOSABLE_MATERIALIZATION_PRODUCER_RECEIPT_SCHEMA = (
    "audited-pit-factor-v3-disposable-materialization-producer-receipt/v2"
)
DISPOSABLE_MATERIALIZATION_INDEPENDENT_RECEIPT_SCHEMA = (
    "audited-pit-factor-v3-disposable-materialization-independent-receipt/v2"
)
DISPOSABLE_MATERIALIZATION_PUBLICATION_SCHEMA = (
    "audited-pit-factor-v3-disposable-materialization-publication/v2"
)

FORMAL_AUTHORITY_SCOPE = "FORMAL_FROZEN_POINTS_CONTRACT"
DISPOSABLE_AUTHORITY_SCOPE = "DISPOSABLE_TEST_FIXTURE_ONLY"

EXACT_CALENDAR_COUNTS = MappingProxyType(
    {
        "prewindow_session_count": 250,
        "development_session_count": 483,
        "all_market_session_count": 733,
        "source_date_count": 732,
    }
)
FROZEN_DEVELOPMENT_SESSION_START = "2024-07-05"
FROZEN_DEVELOPMENT_SESSION_END = "2026-07-03"
FROZEN_DEVELOPMENT_SESSIONS_SHA256 = (
    "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
)

UPSTREAM_SOURCE_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
DOWNSTREAM_ELIGIBLE_SEGMENTS = (
    "SSE_MAIN",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
SEGMENT_CLASSIFICATION_CONTRACT = MappingProxyType(
    {
        "BSE": ("suffix", ".BJ"),
        "SSE_MAIN": ("prefix_and_suffix", ("60", ".SH")),
        "SSE_STAR": ("prefix_and_suffix", ("688", ".SH")),
        "SZSE_CHINEXT": ("prefix_and_suffix", ("30", ".SZ")),
        "SZSE_MAIN": ("prefix_and_suffix", ("00", ".SZ")),
    }
)
BOARD_LEDGER_ROW_FIELDS = (
    "trade_date",
    "segment",
    "ts_code",
    "stable_security_id",
    "segment_row_count",
    "segment_candidate_keys_sha256",
    "segment_rows_sha256",
    "classification_authority_category",
    "classification_authority_schema",
    "classification_authority_raw_sha256",
)

PARENT_FROZEN_BINDINGS = MappingProxyType(
    {
        "original_parent_feature_row_count": 1_796_835,
        "original_parent_feature_rows_sha256": (
            "7cbd9bfe61052f87d736f6a7d14fdc1ad350ce66add98347ea36d9c2ed48b337"
        ),
        "all_candidate_keys_sha256": (
            "ffed3b95e3b31803c7e993af06cf155e42c379590a41c919989e31d5cf374acd"
        ),
        "preregistered_suspension_excluded_candidate_count": 1,
        "preregistered_suspension_excluded_candidate_keys_sha256": (
            "a2149f2a5de78780459652aaf64bcb940ab2ad7631dd3260007f1ad1ec1ab18a"
        ),
        "factor_v2_parent_artifact_sha256": (
            "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
        ),
        "factor_v2_parent_manifest_file_sha256": (
            "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
        ),
        "factor_v2_common_eligible_overlay_artifact_sha256": (
            "abd4b2166da4520952d7bfc5c8a988bc0a8027dd576a90ae0fdda2560b3a02f8"
        ),
        "factor_v2_common_eligible_overlay_manifest_file_sha256": (
            "9131f15e13f247a4663fae658af544b94bf2af01a3494b8eb0a3d0c095ee1312"
        ),
        "factor_v2_points_predecessor_spec_sha256": (
            "685487c7159a6f0e9748bb46265b93d4c86f4a9dc7dc734beac2c267547a2cdf"
        ),
        "factor_v2_common_eligible_receipt_sha256": (
            "86199759116362c6317db7ca73b78dc56c9adaada57c2661a4b33028be44db4d"
        ),
        "candidate_keys_sha256": (
            "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
        ),
        "source_feature_projection_rows_sha256": (
            "62f02c3d3b068f50b95d29a06a218e58dd72693113081570ded95ae73d7ec59f"
        ),
    }
)
PARENT_PROJECTION_ROOT_FIELDS = (
    "candidate_keys_sha256",
    "source_feature_projection_rows_sha256",
    "full_export_rows_sha256",
)
FACTOR_V2_EVALUATION_ROOT_FIELDS = (
    "terminal_decision_descriptor_sha256",
    "evaluator_descriptor_sha256",
    "cost_slippage_execution_descriptor_sha256",
)
PARENT_HELD_RAW_FIELDS = (
    "parent_projection_raw_sha256",
    "terminal_evaluator_raw_sha256",
    "cost_slippage_execution_raw_sha256",
)
PARENT_COMMON_ELIGIBLE_CANDIDATE_COUNT = 1_796_834

BASE_FEATURE_NAMES = (
    "amount_level_20_rank",
    "amount_volatility_20_rank",
    "amount_surge_5_to_60_rank",
    "amihud_20_rank",
    "realized_volatility_20_pct_rank",
    "max_return_20_pct_rank",
    "signal_return_1d_pct_rank",
    "reversal_20_skip5_pct_rank",
    "cross_section_above_ma20_fraction",
    "cross_section_median_return_5d_pct",
)
TURNOVER_LEVEL_FEATURE = "turnover_rate_f_rank"
ABNORMAL_TURNOVER_FEATURE = "abnormal_turnover_rate_f_20_to_250_rank"
ARM_FEATURE_NAMES = MappingProxyType(
    {
        "control": BASE_FEATURE_NAMES,
        "turnover_level": (*BASE_FEATURE_NAMES, TURNOVER_LEVEL_FEATURE),
        "abnormal_turnover": (*BASE_FEATURE_NAMES, ABNORMAL_TURNOVER_FEATURE),
    }
)
ARM_ORDER = tuple(ARM_FEATURE_NAMES)
ARM_FEATURE_COUNTS = MappingProxyType(
    {arm: len(features) for arm, features in ARM_FEATURE_NAMES.items()}
)

ACTIVATION_V2_FORMAL_TRUE_FIELDS = (
    "activation_verified",
    "compound_authority_verified",
    "contract_binding_validated",
    "formal_materialization_eligible",
    "parent_source_authority_verified",
    "publisher_terminal_chain_verified",
    "source_authority_complete",
    "source_authority_verified",
    "terminal_evaluator_authority_verified",
    "verified",
)
ACTIVATION_V2_NATIVE_TRUE_FIELDS = (
    "machine_global_root_lease_verified",
    "native_run_completion_verified",
    "native_terminal_authority_verified",
    "native_verify_completion_verified",
    "root_epoch_terminal_verified",
)
ACTIVATION_V2_INDEPENDENT_TRUE_FIELDS = (
    "independent_public_replay_performed",
    "independent_verifier_process_verified",
    "single_attempt_verified",
)
ACTIVATION_V2_REQUIRED_TRUE_FIELDS = (
    *ACTIVATION_V2_FORMAL_TRUE_FIELDS,
    *ACTIVATION_V2_NATIVE_TRUE_FIELDS,
    *ACTIVATION_V2_INDEPENDENT_TRUE_FIELDS,
)
COMPOUND_V2_NATIVE_TERMINAL_RAW_BINDINGS = (
    # Epoch raws use non-root names in the activation transitive closure.
    "run_claim_raw_sha256",
    "run_receipt_raw_sha256",
    "verify_claim_raw_sha256",
    "terminal_receipt_raw_sha256",
    "parent_producer_raw_sha256",
    "parent_verifier_raw_sha256",
    "evaluator_producer_raw_sha256",
    "evaluator_verifier_raw_sha256",
    "compound_run_receipt_raw_sha256",
    "compound_terminal_receipt_raw_sha256",
    "native_run_completion_raw_sha256",
    "native_verify_completion_raw_sha256",
    "program_set_root_sha256",
    "attempt_key_sha256",
    "global_attempt_identity_sha256",
    "run_spec_raw_sha256",
)
COMPOUND_V2_NATIVE_TERMINAL_CAS_RAW_FIELDS = tuple(
    field
    for field in COMPOUND_V2_NATIVE_TERMINAL_RAW_BINDINGS
    if field.endswith("_raw_sha256")
)
REGISTERED_ACTIVATION_OUTER_RAW_CLOSURE_FIELDS = (
    "activation_descriptor_raw_sha256",
    "activation_independent_receipt_raw_sha256",
    "activation_publication_raw_sha256",
    "compound_native_terminal_raw_sha256",
    "compound_publication_raw_sha256",
)
REGISTERED_ACTIVATION_EXACT_RAW_CLOSURE_FIELDS = (
    *REGISTERED_ACTIVATION_OUTER_RAW_CLOSURE_FIELDS,
    *COMPOUND_V2_NATIVE_TERMINAL_CAS_RAW_FIELDS,
)
REGISTERED_ACTIVATION_IDENTITY_FIELDS = (
    "program_set_root_sha256",
    "attempt_key_sha256",
    "global_attempt_identity_sha256",
    "run_spec_raw_sha256",
    "semantic_input_root_sha256",
)
REGISTERED_ACTIVATION_ALL_BINDING_FIELDS = (
    *REGISTERED_ACTIVATION_OUTER_RAW_CLOSURE_FIELDS,
    *COMPOUND_V2_NATIVE_TERMINAL_RAW_BINDINGS,
    "semantic_input_root_sha256",
)
REGISTERED_ACTIVATION_HELD_PHYSICAL_FIELDS = (
    "ancestor_file_ids",
    "file_id",
    "hardlink_count",
    "owner_sid",
    "dacl_sha256",
    "byte_size",
    "raw_sha256",
    "category",
)
REGISTERED_ACTIVATION_EXPECTED_CATEGORY_BY_RAW_FIELD = MappingProxyType(
    {
        "activation_descriptor_raw_sha256": "factor-v3-formal-activation-descriptor",
        "activation_independent_receipt_raw_sha256": (
            "factor-v3-formal-activation-independent-verifier"
        ),
        "activation_publication_raw_sha256": "factor-v3-formal-activation-publication",
        "compound_native_terminal_raw_sha256": (
            "factor-authority-native-terminal-authority"
        ),
        "compound_publication_raw_sha256": "factor-authority-compound-success",
        "root_run_claim_raw_sha256": "factor-authority-root-epoch",
        "root_run_receipt_raw_sha256": "factor-authority-root-epoch",
        "root_verify_claim_raw_sha256": "factor-authority-root-epoch",
        "root_terminal_receipt_raw_sha256": "factor-authority-root-epoch",
        "parent_producer_raw_sha256": "factor-v3-parent-source-producer",
        "parent_verifier_raw_sha256": (
            "factor-v3-parent-source-independent-verifier"
        ),
        "evaluator_producer_raw_sha256": "factor-v2-terminal-evaluator-producer",
        "evaluator_verifier_raw_sha256": (
            "factor-v2-terminal-evaluator-independent-verifier"
        ),
        "compound_run_receipt_raw_sha256": (
            "factor-authority-compound-run-receipt"
        ),
        "compound_terminal_receipt_raw_sha256": (
            "factor-authority-compound-terminal-receipt"
        ),
        "native_run_completion_raw_sha256": (
            "factor-authority-native-run-completion"
        ),
        "native_verify_completion_raw_sha256": (
            "factor-authority-native-verify-completion"
        ),
    }
)
REGISTERED_AUTHORITY_NAMESPACE_CATEGORIES = MappingProxyType(
    {
        "global_attempt_ledger": "factor-v3-formal-materializer-attempt-ledger",
        "compound_publication": "factor-authority-compound-success",
        "compound_native_terminal": "factor-authority-native-terminal-authority",
        "activation_descriptor": "factor-v3-formal-activation-descriptor",
        "activation_publication": "factor-v3-formal-activation-publication",
        "activation_independent_receipt": (
            "factor-v3-formal-activation-independent-verifier"
        ),
        "materialization_artifact": "factor-v3-formal-materialization-artifact",
        "materialization_producer_scratch": (
            "factor-v3-formal-materialization-producer-scratch"
        ),
        "materialization_producer_receipt": (
            "factor-v3-formal-materialization-producer-receipt"
        ),
        "materialization_independent_receipt": (
            "factor-v3-formal-materialization-independent-verifier"
        ),
        "materialization_verifier_scratch": (
            "factor-v3-formal-materialization-verifier-scratch"
        ),
        "materialization_native_run": (
            "factor-v3-formal-materialization-native-run"
        ),
        "materialization_native_verify": (
            "factor-v3-formal-materialization-native-verify"
        ),
        "materialization_native_terminal": (
            "factor-v3-formal-materialization-native-terminal"
        ),
        "materialization_failure_terminal": (
            "factor-v3-formal-materialization-failure-terminal"
        ),
        "materialization_publication_staging": (
            "factor-v3-formal-materialization-publication-staging"
        ),
        "materialization_publication": "factor-v3-formal-materialization-success",
    }
)
CAS_DESCRIPTOR_FIELDS = (
    "byte_size",
    "category",
    "file_id_commitment",
    "file_sha256",
    "payload_root_sha256",
    "registered_root_id",
    "relative_path",
    "role",
    "schema",
)

ALLOWED_EXCLUSION_REASONS = (
    "ipo_age_less_than_6_calendar_months",
    "observed_trading_records_less_than_15_in_20_market_session_window",
    "observed_trading_records_less_than_120_in_250_market_session_window",
    "unresolved_authoritative_security_code_transition",
)
EXCLUSION_IDENTITY_FIELDS = (
    "candidate_key",
    "signal_date",
    "reason",
    "authority_evidence_root_sha256",
)

SQLITE_TABLE_CONTRACT = MappingProxyType(
    {
        "metadata": (("key", "TEXT", 1, 1), ("value_json", "BLOB", 1, 0)),
        "sessions": (
            ("session_position", "INTEGER", 1, 1),
            ("trade_date", "TEXT", 1, 0),
            ("temporal_role", "TEXT", 1, 0),
        ),
        "identities": (
            ("identity_position", "INTEGER", 1, 1),
            ("signal_date", "TEXT", 1, 0),
            ("candidate_key", "TEXT", 1, 0),
            ("stable_security_id", "TEXT", 1, 0),
            ("ts_code", "TEXT", 1, 0),
            ("source_input_date_label", "TEXT", 1, 0),
            ("outcome_binding_sha256", "TEXT", 1, 0),
            ("fold_binding_sha256", "TEXT", 1, 0),
            ("cost_binding_sha256", "TEXT", 1, 0),
        ),
        "arms": (
            ("arm_position", "INTEGER", 1, 1),
            ("arm_name", "TEXT", 1, 0),
            ("feature_count", "INTEGER", 1, 0),
            ("feature_names_json", "BLOB", 1, 0),
            ("identity_root_sha256", "TEXT", 1, 0),
            ("float64_rows_sha256", "TEXT", 1, 0),
            ("row_count", "INTEGER", 1, 0),
        ),
        "arm_feature_rows": (
            ("arm_name", "TEXT", 1, 1),
            ("identity_position", "INTEGER", 1, 2),
            ("feature_values_f64le", "BLOB", 1, 0),
        ),
        "exclusions": (
            ("candidate_key", "TEXT", 1, 1),
            ("signal_date", "TEXT", 1, 2),
            ("reason", "TEXT", 1, 0),
            ("authority_evidence_root_sha256", "TEXT", 1, 0),
        ),
        "per_signal_ledger": (
            ("signal_date", "TEXT", 1, 1),
            ("parent_row_count", "INTEGER", 1, 0),
            ("eligible_row_count", "INTEGER", 1, 0),
            ("excluded_row_count", "INTEGER", 1, 0),
        ),
    }
)
SQLITE_TABLES = tuple(SQLITE_TABLE_CONTRACT)
SQLITE_EXPECTED_OBJECT_IDENTITIES = (
    ("index", "sqlite_autoindex_arms_1", "arms"),
    ("index", "sqlite_autoindex_identities_1", "identities"),
    ("index", "sqlite_autoindex_sessions_1", "sessions"),
    ("table", "arm_feature_rows", "arm_feature_rows"),
    ("table", "arms", "arms"),
    ("table", "exclusions", "exclusions"),
    ("table", "identities", "identities"),
    ("table", "metadata", "metadata"),
    ("table", "per_signal_ledger", "per_signal_ledger"),
    ("table", "sessions", "sessions"),
)
SQLITE_PRAGMA_CONTRACT = MappingProxyType(
    {
        "application_id": 1_180_268_626,
        "foreign_keys": 1,
        "journal_mode": "delete",
        "page_size": 4096,
        "user_version": 2,
    }
)
RANK_FEATURE_NAMES = (
    *BASE_FEATURE_NAMES[:8],
    TURNOVER_LEVEL_FEATURE,
    ABNORMAL_TURNOVER_FEATURE,
)
ZERO_ONE_FEATURE_NAMES = ("cross_section_above_ma20_fraction",)
UNBOUNDED_FINITE_FEATURE_NAMES = ("cross_section_median_return_5d_pct",)
FLOAT_STORAGE_CONTRACT = MappingProxyType(
    {
        "encoding": "ieee754-float64-little-endian",
        "row_order": ("signal_date", "candidate_key"),
        "feature_order": "arm_feature_names_exact_order",
        "finite_required": True,
        "rank_feature_bounds": (-1.0, 1.0),
        "zero_one_feature_bounds": (0.0, 1.0),
        "unbounded_finite_features": UNBOUNDED_FINITE_FEATURE_NAMES,
    }
)

SAFETY_FALSE_FIELDS = (
    "automatic_trading_eligible",
    "embargo_consumed",
    "experiment_launch_eligible",
    "final_oos_consumed",
    "model_training_started",
    "oof_scoring_started",
    "orders_submitted",
    "production_profile_registered",
    "production_recommendation_eligible",
    "recommendation_generation_eligible",
    "vps_deployment_eligible",
    "vps_deployment_started",
)

PRODUCER_ROLE = "factor-v3-formal-materializer-v2-producer"
INDEPENDENT_VERIFIER_ROLE = "factor-v3-formal-materializer-v2-independent-verifier"
RUNNER_ROLE = "factor-v3-formal-materializer-v2-native-runner"

PUBLICATION_STAGE_ORDER = (
    "atomic-create-global-attempt-claim",
    "hold-and-preverify-all-inputs",
    "create-sqlite-artifact-cas",
    "create-producer-receipt-cas",
    "create-native-run-receipt-cas",
    "independent-verifier-replay",
    "create-independent-verifier-receipt-cas",
    "create-native-verify-receipt-cas",
    "create-native-terminal-receipt-cas",
    "postverify-all-held-inputs",
    "create-final-publication-cas",
)
MATERIALIZATION_STAGE_PROBE_STAGES = PUBLICATION_STAGE_ORDER[:-1]
FAILURE_STAGE_ORDER = ("create-failure-terminal-cas",)
HELD_INPUT_PROBE_STAGES = (
    "after-open-before-first-read",
    "after-first-read-before-postverify",
    "after-postverify-before-publication",
)
HELD_INPUT_POSTVERIFY_FIELDS = (
    "ancestor_file_ids_unchanged",
    "file_id_unchanged",
    "hardlink_count_is_one",
    "no_reparse_point",
    "owner_dacl_unchanged",
    "raw_sha256_unchanged",
    "size_unchanged",
    "staging_namespace_empty",
)
PUBLICATION_PROBE_STAGES = (
    "after-all-postverification-before-final-publication",
)
DISPOSABLE_PUBLICATION_CATEGORY = "factor-v3-disposable-materialization-publication"

RUNNER_ATTEMPT_STATES = (
    "EMPTY",
    "CLAIMED",
    "PRODUCED",
    "INDEPENDENTLY_VERIFIED",
    "PUBLISHED",
    "FAILED_TERMINAL",
)
RUNNER_CLAIM_SCHEMA = "factor-v3-formal-materializer-v2-run-claim/v1"
RUNNER_STATUS_SCHEMA = "factor-v3-formal-materializer-v2-run-status/v1"
RUNNER_CLI_MODES = ("run", "verify")

# Keep materializer market scope identical to the frozen research goal.
assert UPSTREAM_SOURCE_SEGMENTS == research_goal.UPSTREAM_SOURCE_SEGMENTS
assert DOWNSTREAM_ELIGIBLE_SEGMENTS == research_goal.DOWNSTREAM_ELIGIBLE_SEGMENTS
assert tuple(SEGMENT_CLASSIFICATION_CONTRACT.keys()) == tuple(
    research_goal.SEGMENT_CLASSIFICATION_CONTRACT.keys()
)

