"""Unverified development adapter for a terminal Factor V2 evaluator branch."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

from app import audited_pit_score_contract as score_contract
from app import audited_pit_shallow_gbdt as shallow_gbdt
from app import factor_v2_decision_branch_selector as branch_selector


FACTOR_V2_TERMINAL_EVALUATOR_ADAPTER_SCHEMA = (
    "factor-v2-terminal-evaluator-unverified-development-adapter/v1"
)
FACTOR_V2_TERMINAL_DECISION_BINDING_SCHEMA = (
    "factor-v2-terminal-decision-binding/v1"
)
FACTOR_V2_DEVELOPMENT_EVALUATION_SCHEMA = (
    "audited-pit-factor-v2-development-evaluation/v1"
)
FACTOR_V2_DEVELOPMENT_EVALUATION_VERIFICATION_SCHEMA = (
    "audited-pit-factor-v2-development-evaluation-verification/v1"
)
FACTOR_V2_EVALUATION_CONTRACT_SCHEMA = (
    "audited-pit-factor-v2-frozen-evaluation-contract/v1"
)
FACTOR_V2_EXECUTION_SOURCE_SCHEMA = (
    "audited-pit-training-dataset-oof-execution-source-binding/v1"
)

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_ARM_ORDER = tuple(branch_selector.ARM_ORDER)
_BASE_FEATURE_NAMES = (
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
_ARM_FEATURE_NAMES = {
    "v2_control": _BASE_FEATURE_NAMES,
    "overnight_20": (*_BASE_FEATURE_NAMES, "overnight_return_20_pct_rank"),
    "intraday_20": (*_BASE_FEATURE_NAMES, "intraday_return_20_pct_rank"),
}
_ARM_STRATEGY_SHA256 = {
    "v2_control": "216512ff65773e14a3ecf87c2679fc9d4c8787a6beab86d5972848315cd1ef7c",
    "overnight_20": "663890eb043353c58f565066210869035eae627c2a12030f860f29aa8ee84945",
    "intraday_20": "002a76e6c3300c886250573efaaf42e71543663b2c49a6c40e5baf505883811a",
}
_SHALLOW_GBDT_SPEC_SHA256 = (
    "53d00badc8683670ef3d6c02307697e2c3ef8ec769b9d8da072d36420cea70ac"
)
_SOURCE_SESSION_COUNT = 483
_SOURCE_SESSION_START = "2024-07-05"
_SOURCE_SESSION_END = "2026-07-03"
_SOURCE_SESSIONS_SHA256 = (
    "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
)
_PARENT_ARTIFACT_SHA256 = (
    "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
)
_PARENT_MANIFEST_FILE_SHA256 = (
    "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
)
_PARENT_OUTCOME_ROW_COUNT = 1_782_860
_PARENT_OUTCOME_ROWS_SHA256 = (
    "1e14af73e758d0f686b095e4b19e896eb6a1bf0c0885c7ebca299ecbec5b9730"
)
_PARENT_FOLD_COUNT = 6
_PARENT_FOLDS_SHA256 = (
    "f60ff439951a5125a8aa72ae8c9974dc775fe67cee56cab5388bcaf7a66a62db"
)
_PUBLIC_EVALUATOR_BINDING = {
    "schema_version": "factor-v2-development-evaluator-public-verifier-binding/v1",
    "source_commit": "3e9bd1bcf12024f9bf52a0b0fbcdd86f7bc64109",
    "source_tree": "bf378b4e23436e5e6fa2e9d35ba14f8ab4495c96",
    "module": "app.audited_pit_factor_v2_development_evaluation",
    "entrypoint": "verify_factor_v2_development_evaluation",
    "module_git_blob_oid": "7659e3e7274e865d9909098ba102b720c87de644",
    "module_git_blob_sha256": (
        "a7d3515070d6dae9509bbc1c6d87334221f90ab11c986798a012321796ec002d"
    ),
    "verification_schema_version": (
        FACTOR_V2_DEVELOPMENT_EVALUATION_VERIFICATION_SCHEMA
    ),
}
_EXPECTED_CHECKS = {
    "content_addressing_verified": True,
    "three_arm_source_replay_verified": True,
    "exact_common_identity_verified": True,
    "exact_score_outcome_coverage_verified": True,
    "frozen_selection_cost_and_gates_verified": True,
    "common_subset_control_baseline_verified": True,
    "development_scope_verified": True,
    "embargo_not_consumed": True,
    "final_oos_not_consumed": True,
    "production_ineligible": True,
}
_EXPECTED_SCOPE = {
    "point_in_time": True,
    "development_only": True,
    "strict_artifact_native_execution": True,
    "training_performed_by_evaluator": False,
    "embargo_consumed": False,
    "final_oos_consumed": False,
    "eligible_for_profile_registration": False,
    "production_recommendation_eligible": False,
}
_MANIFEST_FIELDS = {
    "schema_version",
    "evaluation_producer_binding",
    "temporal_role",
    "factor_v2_spec_sha256",
    "arm_order",
    "evaluation_contract",
    "source_binding",
    "evaluation_sessions",
    "evaluation_sessions_sha256",
    "common_identity",
    "arms",
    "comparison",
    "scope",
    "automatic_winner_selected",
    "production_profile_registered",
    "embargo_consumed",
    "final_oos_consumed",
    "production_recommendation_eligible",
    "artifact_sha256",
}
_VERIFICATION_FIELDS = {
    "schema_version",
    "artifact_sha256",
    "manifest_file_sha256",
    "factor_v2_spec_sha256",
    "arm_order",
    "common_identity_root_sha256",
    "arm_decisions",
    "automatic_winner_selected",
    "checks",
    "verified",
    "receipt_sha256",
}
_EVALUATION_CONTRACT_FIELDS = {
    "schema_version",
    "strategy_sha256",
    "score_contract",
    "score_contract_sha256",
    "positive_filter",
    "cost_and_execution",
    "cost_and_execution_sha256",
    "selection",
    "selection_sha256",
    "advancement_thresholds",
    "advancement_thresholds_sha256",
    "root_sha256",
}
_EXECUTION_SOURCE_FIELDS = {
    "schema_version",
    "materialization",
    "sessions",
    "sessions_sha256",
    "ordered_session_count",
    "ordered_sessions_root_sha256",
    "first_session",
    "last_session",
    "outcome_candidate_count",
    "outcome_payloads_sha256",
    "execution_spool",
    "execution_spool_receipt_sha256",
    "root_sha256",
}
_ARM_SOURCE_FIELDS = {
    "artifact_sha256",
    "manifest_file_sha256",
    "arm",
    "strategy_sha256",
    "score_database_sha256",
    "score_row_count",
    "score_rows_sha256",
    "fold_count",
    "fold_receipts_sha256",
    "fold_score_rows_sha256",
    "model_evidence_root_sha256",
    "oof_runtime_binding_root_sha256",
    "source_binding_root_sha256",
    "gate_binding_root_sha256",
    "verification_receipt_sha256",
}
_DECISION_FIELDS = set(branch_selector.RECEIPT_FIELDS)
_EVALUATION_PRODUCER_FIELDS = {
    "schema_version",
    "loaded_entrypoints",
    "module_sha256",
    "pandas_version",
    "python_version",
    "sqlite_version",
    "root_sha256",
}
_COMMON_IDENTITY_FIELDS = {
    "schema_version",
    "identity_fields",
    "arm_order",
    "expected_count_formula",
    "full_gate_score_row_count",
    "full_gate_identity_root_sha256",
    "global_excluded_candidate_count",
    "excluded_validation_candidate_count",
    "excluded_non_validation_candidate_count",
    "excluded_validation_candidate_keys_sha256",
    "excluded_validation_identity_root_sha256",
    "expected_score_row_count",
    "common_identity_root_sha256",
    "observed_arm_score_row_counts",
    "observed_arm_identity_root_sha256",
    "per_fold",
    "all_three_arms_exact_identity",
    "receipt_sha256",
}
_COMMON_FOLD_FIELDS = {
    "fold_index",
    "full_gate_score_row_count",
    "excluded_validation_candidate_count",
    "expected_score_row_count",
    "common_identity_root_sha256",
    "candidate_keys_sha256",
    "signal_dates_sha256",
}
_SOURCE_BINDING_FIELDS = {
    "parent_materialization",
    "execution_spool",
    "execution_source_binding",
    "execution_source_binding_root_sha256",
    "overlay_materialization",
    "full_parent_replay_gate",
    "arms",
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
        raise ValueError("value is not strict canonical JSON") from exc


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _strict_sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256")
    return value


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse_canonical_object(raw: bytes, *, field: str) -> dict[str, Any]:
    try:
        value = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=lambda token: (_ for _ in ()).throw(
                ValueError(f"invalid JSON constant: {token}")
            ),
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError(f"{field} is not strict JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    if _canonical_bytes(value) != raw:
        raise ValueError(f"{field} is not canonical JSON")
    return value


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def _identity(metadata: os.stat_result) -> tuple[int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _read_direct_bytes(path: Path, *, field: str) -> bytes:
    if not path.is_absolute():
        raise ValueError(f"{field} path must be absolute")
    try:
        before_path = os.lstat(path)
    except OSError as exc:
        raise ValueError(f"{field} is unavailable") from exc
    if (
        stat.S_ISLNK(before_path.st_mode)
        or _is_reparse(before_path)
        or not stat.S_ISREG(before_path.st_mode)
    ):
        raise ValueError(f"{field} must be a direct regular file")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ValueError(f"{field} is unavailable") from exc
    try:
        before_fd = os.fstat(descriptor)
        if not stat.S_ISREG(before_fd.st_mode):
            raise ValueError(f"{field} must be a regular file")
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        after_fd = os.fstat(descriptor)
        try:
            after_path = os.lstat(path)
        except OSError as exc:
            raise ValueError(f"{field} changed while being read") from exc
        if (
            _identity(before_fd) != _identity(after_fd)
            or _identity(before_fd) != _identity(before_path)
            or _identity(before_fd) != _identity(after_path)
            or stat.S_ISLNK(after_path.st_mode)
            or _is_reparse(after_path)
        ):
            raise ValueError(f"{field} changed while being read")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _read_content_addressed_object(
    path: str | os.PathLike[str],
    *,
    expected_file_sha256: str,
    expected_basename_sha256: str,
    field: str,
) -> dict[str, Any]:
    file_sha256 = _strict_sha256(
        expected_file_sha256,
        field=f"expected {field} file SHA-256",
    )
    basename_sha256 = _strict_sha256(
        expected_basename_sha256,
        field=f"expected {field} basename SHA-256",
    )
    source = Path(path)
    if source.name != f"{basename_sha256}.json":
        raise ValueError(f"{field} content-addressed basename drifted")
    raw = _read_direct_bytes(source, field=field)
    if hashlib.sha256(raw).hexdigest() != file_sha256:
        raise ValueError(f"{field} content address SHA-256 drifted")
    return _parse_canonical_object(raw, field=field)


def _mapping(value: Any, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    return dict(value)


def _exact_fields(value: Mapping[str, Any], fields: set[str], *, field: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{field} field schema drifted")


def _ordered_sessions(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} sessions are invalid")
    sessions: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(f"{field} session is invalid")
        try:
            parsed = date.fromisoformat(item)
        except ValueError as exc:
            raise ValueError(f"{field} session is invalid") from exc
        normalized = parsed.isoformat()
        if normalized != item or (sessions and item <= sessions[-1]):
            raise ValueError(f"{field} sessions are not strictly ordered")
        sessions.append(item)
    return sessions


def _validate_local_frozen_contract() -> dict[str, Any]:
    spec = deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
    if (
        _sha256(spec) != _SHALLOW_GBDT_SPEC_SHA256
        or tuple(shallow_gbdt.FEATURE_NAMES) != _BASE_FEATURE_NAMES
        or spec.get("features") != list(_BASE_FEATURE_NAMES)
        or spec.get("required_market_session_count") != _SOURCE_SESSION_COUNT
        or spec.get("required_oof_fold_count") != _PARENT_FOLD_COUNT
    ):
        raise ValueError("frozen model strategy contract drifted")
    walk_forward = _mapping(spec.get("walk_forward"), field="frozen walk-forward")
    if walk_forward != {
        "folds": "continuous_non_overlapping_validation_windows",
        "minimum_training_sessions": 126,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "training_window_sessions": 126,
        "training_window_type": "trailing_frozen_signal_sessions",
        "validation_sessions": 63,
    }:
        raise ValueError("frozen fold and purge contract drifted")
    model = _mapping(spec.get("model"), field="frozen model")
    if (
        model.get("hyperparameter_search") is not False
        or model.get("validation_metric_model_selection") is not False
        or model.get("num_boost_round") != 200
    ):
        raise ValueError("frozen model hyperparameter contract drifted")
    if spec.get("roundtrip_cost_bps") != 25.0 or spec.get("slippage_bps") != 10.0:
        raise ValueError("frozen execution cost contract drifted")
    return spec


def _validate_evaluation_contract(
    value: Any,
    *,
    spec: Mapping[str, Any],
) -> dict[str, Any]:
    contract = _mapping(value, field="evaluation contract")
    _exact_fields(contract, _EVALUATION_CONTRACT_FIELDS, field="evaluation contract")
    unsigned = dict(contract)
    root = _strict_sha256(unsigned.pop("root_sha256", None), field="evaluation root")
    cost = {
        name: deepcopy(spec[name])
        for name in (
            "signal_tag",
            "hold_days",
            "close_stop_loss_pct",
            "roundtrip_cost_bps",
            "slippage_bps",
            "capital_model",
            "exposure_multiplier",
            "annual_financing_rate_pct",
        )
    }
    expected = {
        "schema_version": FACTOR_V2_EVALUATION_CONTRACT_SCHEMA,
        "strategy_sha256": _SHALLOW_GBDT_SPEC_SHA256,
        "score_contract": dict(score_contract.SHALLOW_GBDT_SCORE_CONTRACT),
        "score_contract_sha256": _sha256(
            dict(score_contract.SHALLOW_GBDT_SCORE_CONTRACT)
        ),
        "positive_filter": {
            "schema_version": "audited-pit-training-dataset-oof-positive-filter/v1",
            "comparison": "strictly_greater_than_0.5",
        },
        "cost_and_execution": cost,
        "cost_and_execution_sha256": _sha256(cost),
        "selection": deepcopy(spec["selection"]),
        "selection_sha256": _sha256(spec["selection"]),
        "advancement_thresholds": deepcopy(spec["advancement_thresholds"]),
        "advancement_thresholds_sha256": _sha256(spec["advancement_thresholds"]),
    }
    if unsigned != expected or root != _sha256(unsigned):
        raise ValueError("evaluation cost, execution, selection, or gate contract drifted")
    return contract


def _validate_execution_source(value: Any) -> dict[str, Any]:
    binding = _mapping(value, field="execution source binding")
    _exact_fields(binding, _EXECUTION_SOURCE_FIELDS, field="execution source binding")
    unsigned = dict(binding)
    root = _strict_sha256(
        unsigned.pop("root_sha256", None),
        field="execution source binding root",
    )
    if binding.get("schema_version") != FACTOR_V2_EXECUTION_SOURCE_SCHEMA:
        raise ValueError("execution source binding schema drifted")
    sessions = _ordered_sessions(binding.get("sessions"), field="execution source")
    ordered_rows = [
        {"ordinal": ordinal, "session": session}
        for ordinal, session in enumerate(sessions)
    ]
    if (
        len(sessions) != _SOURCE_SESSION_COUNT
        or sessions[0] != _SOURCE_SESSION_START
        or sessions[-1] != _SOURCE_SESSION_END
        or _sha256(sessions) != _SOURCE_SESSIONS_SHA256
        or binding.get("sessions_sha256") != _SOURCE_SESSIONS_SHA256
        or binding.get("ordered_session_count") != _SOURCE_SESSION_COUNT
        or binding.get("ordered_sessions_root_sha256") != _sha256(ordered_rows)
        or binding.get("first_session") != _SOURCE_SESSION_START
        or binding.get("last_session") != _SOURCE_SESSION_END
    ):
        raise ValueError("execution source 483-session contract drifted")
    materialization = _mapping(
        binding.get("materialization"), field="execution parent materialization"
    )
    if materialization != {
        "artifact_sha256": _PARENT_ARTIFACT_SHA256,
        "manifest_file_sha256": _PARENT_MANIFEST_FILE_SHA256,
    }:
        raise ValueError("execution parent materialization contract drifted")
    if (
        binding.get("outcome_candidate_count") != _PARENT_OUTCOME_ROW_COUNT
        or binding.get("outcome_payloads_sha256") != _PARENT_OUTCOME_ROWS_SHA256
    ):
        raise ValueError("execution outcome contract drifted")
    spool = _mapping(binding.get("execution_spool"), field="execution spool")
    if (
        set(spool) != {"file", "size_bytes", "sha256", "receipt_sha256"}
        or spool.get("file") != "strict_execution_spool.sqlite3"
        or type(spool.get("size_bytes")) is not int
        or spool["size_bytes"] <= 0
        or binding.get("execution_spool_receipt_sha256")
        != spool.get("receipt_sha256")
    ):
        raise ValueError("execution spool contract drifted")
    _strict_sha256(spool.get("sha256"), field="execution spool SHA-256")
    _strict_sha256(spool.get("receipt_sha256"), field="execution spool receipt")
    if root != _sha256(unsigned):
        raise ValueError("execution source binding root drifted")
    return binding


def _validate_arm_sources(value: Any) -> dict[str, dict[str, Any]]:
    sources = _mapping(value, field="arm source bindings")
    if set(sources) != set(_ARM_ORDER):
        raise ValueError("arm source schema drifted")
    result: dict[str, dict[str, Any]] = {}
    for arm in _ARM_ORDER:
        source = _mapping(sources.get(arm), field=f"{arm} source binding")
        _exact_fields(source, _ARM_SOURCE_FIELDS, field=f"{arm} source binding")
        for name in _ARM_SOURCE_FIELDS - {
            "arm",
            "score_row_count",
            "fold_count",
            "fold_score_rows_sha256",
        }:
            _strict_sha256(source.get(name), field=f"{arm} {name}")
        fold_rows = source.get("fold_score_rows_sha256")
        if (
            source.get("arm") != arm
            or source.get("strategy_sha256") != _ARM_STRATEGY_SHA256[arm]
            or source.get("fold_count") != _PARENT_FOLD_COUNT
            or type(source.get("score_row_count")) is not int
            or source["score_row_count"] <= 0
            or not isinstance(fold_rows, list)
            or len(fold_rows) != _PARENT_FOLD_COUNT
        ):
            raise ValueError(f"{arm} fold or strategy source contract drifted")
        for index, digest in enumerate(fold_rows):
            _strict_sha256(digest, field=f"{arm} fold {index} score root")
        result[arm] = source
    return result


def _validate_manifest(
    manifest: dict[str, Any],
    *,
    expected_artifact_sha256: str,
    spec: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    _exact_fields(manifest, _MANIFEST_FIELDS, field="evaluation manifest")
    unsigned = dict(manifest)
    artifact = _strict_sha256(
        unsigned.pop("artifact_sha256", None), field="evaluation artifact"
    )
    if artifact != expected_artifact_sha256 or _sha256(unsigned) != artifact:
        raise ValueError("evaluation manifest artifact content address drifted")
    if (
        manifest.get("schema_version") != FACTOR_V2_DEVELOPMENT_EVALUATION_SCHEMA
        or manifest.get("temporal_role") != "development"
        or manifest.get("factor_v2_spec_sha256")
        != branch_selector.FACTOR_V2_SPEC_SHA256
        or manifest.get("arm_order") != list(_ARM_ORDER)
    ):
        raise ValueError("evaluation manifest development schema drifted")
    producer = _mapping(
        manifest.get("evaluation_producer_binding"),
        field="evaluation producer binding",
    )
    _exact_fields(
        producer,
        _EVALUATION_PRODUCER_FIELDS,
        field="evaluation producer binding",
    )
    if (
        producer.get("schema_version")
        != "audited-pit-factor-v2-development-evaluation-producer/v1"
        or producer.get("root_sha256")
        != branch_selector.EVALUATION_PRODUCER_ROOT_SHA256
        or not isinstance(producer.get("loaded_entrypoints"), Mapping)
        or not isinstance(producer.get("module_sha256"), Mapping)
        or any(
            not isinstance(producer.get(field), str) or not producer[field]
            for field in ("pandas_version", "python_version", "sqlite_version")
        )
    ):
        raise ValueError("evaluation producer binding drifted")
    if manifest.get("scope") != _EXPECTED_SCOPE:
        raise ValueError("evaluation scope or embargo contract drifted")
    for field in (
        "automatic_winner_selected",
        "production_profile_registered",
        "embargo_consumed",
        "final_oos_consumed",
        "production_recommendation_eligible",
    ):
        if manifest.get(field) is not False:
            raise ValueError(f"evaluation scope field {field} drifted")
    _validate_evaluation_contract(manifest.get("evaluation_contract"), spec=spec)
    source = _mapping(manifest.get("source_binding"), field="evaluation source")
    _exact_fields(source, _SOURCE_BINDING_FIELDS, field="evaluation source")
    execution = _validate_execution_source(source.get("execution_source_binding"))
    if source.get("execution_source_binding_root_sha256") != execution["root_sha256"]:
        raise ValueError("execution source root cross-binding drifted")
    if source.get("execution_spool") != execution["execution_spool"]:
        raise ValueError("execution spool cross-binding drifted")
    parent = _mapping(
        source.get("parent_materialization"), field="parent materialization source"
    )
    if (
        parent.get("artifact_sha256") != _PARENT_ARTIFACT_SHA256
        or parent.get("manifest_file_sha256") != _PARENT_MANIFEST_FILE_SHA256
    ):
        raise ValueError("parent materialization source drifted")
    arm_sources = _validate_arm_sources(source.get("arms"))
    evaluation_sessions = _ordered_sessions(
        manifest.get("evaluation_sessions"), field="evaluation"
    )
    if (
        evaluation_sessions != execution["sessions"][126:]
        or manifest.get("evaluation_sessions_sha256")
        != _sha256(evaluation_sessions)
    ):
        raise ValueError("evaluation session derivation drifted")
    common = _mapping(manifest.get("common_identity"), field="common identity")
    _exact_fields(common, _COMMON_IDENTITY_FIELDS, field="common identity")
    common_unsigned = dict(common)
    common_receipt_sha256 = _strict_sha256(
        common_unsigned.pop("receipt_sha256", None),
        field="common identity receipt",
    )
    per_fold = common.get("per_fold")
    if (
        common.get("schema_version")
        != "audited-pit-factor-v2-common-oof-identity/v1"
        or common.get("identity_fields")
        != ["fold_index", "validation_ordinal", "candidate_key", "signal_date"]
        or common.get("arm_order") != list(_ARM_ORDER)
        or common.get("all_three_arms_exact_identity") is not True
        or common.get("expected_count_formula")
        != "full_gate_score_row_count - excluded_validation_candidate_count"
        or type(common.get("expected_score_row_count")) is not int
        or common["expected_score_row_count"] <= 0
        or not isinstance(per_fold, list)
        or len(per_fold) != _PARENT_FOLD_COUNT
        or common_receipt_sha256 != _sha256(common_unsigned)
    ):
        raise ValueError("common identity and 6-fold contract drifted")
    common_root = _strict_sha256(
        common.get("common_identity_root_sha256"), field="common identity root"
    )
    expected_count = common["expected_score_row_count"]
    full_count = common.get("full_gate_score_row_count")
    excluded_count = common.get("excluded_validation_candidate_count")
    global_excluded_count = common.get("global_excluded_candidate_count")
    non_validation_excluded_count = common.get(
        "excluded_non_validation_candidate_count"
    )
    if (
        type(full_count) is not int
        or type(excluded_count) is not int
        or type(global_excluded_count) is not int
        or type(non_validation_excluded_count) is not int
        or min(
            full_count,
            excluded_count,
            global_excluded_count,
            non_validation_excluded_count,
        )
        < 0
        or full_count - excluded_count != expected_count
        or global_excluded_count < excluded_count
        or non_validation_excluded_count
        != global_excluded_count - excluded_count
        or common.get("observed_arm_score_row_counts")
        != {arm: expected_count for arm in _ARM_ORDER}
        or common.get("observed_arm_identity_root_sha256")
        != {arm: common_root for arm in _ARM_ORDER}
    ):
        raise ValueError("common identity count coverage drifted")
    for field in (
        "full_gate_identity_root_sha256",
        "excluded_validation_candidate_keys_sha256",
        "excluded_validation_identity_root_sha256",
    ):
        _strict_sha256(common.get(field), field=f"common identity {field}")
    fold_expected_total = 0
    fold_full_total = 0
    fold_excluded_total = 0
    for index, fold in enumerate(per_fold, start=1):
        fold_mapping = _mapping(fold, field=f"common identity fold {index}")
        _exact_fields(
            fold_mapping,
            _COMMON_FOLD_FIELDS,
            field=f"common identity fold {index}",
        )
        if (
            fold_mapping.get("fold_index") != index
            or type(fold_mapping.get("full_gate_score_row_count")) is not int
            or type(fold_mapping.get("excluded_validation_candidate_count"))
            is not int
            or type(fold_mapping.get("expected_score_row_count")) is not int
            or min(
                fold_mapping["full_gate_score_row_count"],
                fold_mapping["excluded_validation_candidate_count"],
                fold_mapping["expected_score_row_count"],
            )
            < 0
            or fold_mapping["full_gate_score_row_count"]
            - fold_mapping["excluded_validation_candidate_count"]
            != fold_mapping["expected_score_row_count"]
        ):
            raise ValueError("common identity fold coverage drifted")
        for field in (
            "common_identity_root_sha256",
            "candidate_keys_sha256",
            "signal_dates_sha256",
        ):
            _strict_sha256(fold_mapping.get(field), field=f"fold {index} {field}")
        fold_expected_total += fold_mapping["expected_score_row_count"]
        fold_full_total += fold_mapping["full_gate_score_row_count"]
        fold_excluded_total += fold_mapping["excluded_validation_candidate_count"]
    if (
        fold_expected_total != expected_count
        or fold_full_total != full_count
        or fold_excluded_total != excluded_count
    ):
        raise ValueError("common identity fold totals drifted")
    arms = _mapping(manifest.get("arms"), field="arm evaluations")
    if set(arms) != set(_ARM_ORDER):
        raise ValueError("arm evaluation schema drifted")
    decisions: dict[str, str] = {}
    for arm in _ARM_ORDER:
        evidence = _mapping(arms.get(arm), field=f"{arm} evaluation")
        decision = evidence.get("preregistered_decision")
        if (
            evidence.get("schema_version")
            != "audited-pit-factor-v2-arm-development-evaluation/v1"
            or evidence.get("arm") != arm
            or evidence.get("score_source") != arm_sources[arm]
            or decision not in {"GREEN", "RED"}
        ):
            raise ValueError(f"{arm} evaluation evidence drifted")
        gate = _mapping(evidence.get("preregistered_gate"), field=f"{arm} gate")
        if gate.get("embargo_consumed") is not False or gate.get(
            "final_oos_consumed"
        ) is not False:
            raise ValueError(f"{arm} embargo or final-OOS gate drifted")
        decisions[arm] = decision
    comparison = _mapping(manifest.get("comparison"), field="arm comparison")
    if (
        comparison.get("schema_version")
        != "audited-pit-factor-v2-common-subset-comparison/v1"
        or comparison.get("arm_order") != list(_ARM_ORDER)
        or comparison.get("baseline_arm") != "v2_control"
        or comparison.get("common_prefilter_score_identity") is not True
        or comparison.get("same_execution_source") is not True
        or comparison.get("automatic_winner_selected") is not False
        or comparison.get("full_parent_gate_used_as_baseline", False) is not False
    ):
        raise ValueError("common subset comparison contract drifted")
    return execution, arm_sources, {"root": common_root, "decisions": decisions}


def _validate_verification_receipt(
    value: Mapping[str, Any],
    *,
    expected_receipt_sha256: str,
    artifact_sha256: str,
    manifest_file_sha256: str,
    common_identity_root_sha256: str,
    decisions: Mapping[str, str],
) -> dict[str, Any]:
    raw = _canonical_bytes(dict(value))
    receipt = _parse_canonical_object(raw, field="evaluation verification receipt")
    _exact_fields(receipt, _VERIFICATION_FIELDS, field="evaluation verification receipt")
    expected = _strict_sha256(
        expected_receipt_sha256,
        field="expected evaluation verification receipt",
    )
    unsigned = dict(receipt)
    receipt_sha256 = _strict_sha256(
        unsigned.pop("receipt_sha256", None), field="evaluation verification receipt"
    )
    if receipt_sha256 != expected or _sha256(unsigned) != receipt_sha256:
        raise ValueError("evaluation verification receipt content address drifted")
    if (
        receipt.get("schema_version")
        != FACTOR_V2_DEVELOPMENT_EVALUATION_VERIFICATION_SCHEMA
        or receipt.get("artifact_sha256") != artifact_sha256
        or receipt.get("manifest_file_sha256") != manifest_file_sha256
        or receipt.get("factor_v2_spec_sha256")
        != branch_selector.FACTOR_V2_SPEC_SHA256
        or receipt.get("arm_order") != list(_ARM_ORDER)
        or receipt.get("common_identity_root_sha256")
        != common_identity_root_sha256
        or receipt.get("arm_decisions") != dict(decisions)
        or receipt.get("automatic_winner_selected") is not False
        or receipt.get("checks") != _EXPECTED_CHECKS
        or receipt.get("verified") is not True
    ):
        raise ValueError("evaluation decision or verification checks drifted")
    return receipt


def _validated_decision(
    path: str | os.PathLike[str],
    *,
    expected_raw_file_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = _strict_sha256(
        expected_raw_file_sha256,
        field="expected decision receipt raw file SHA-256",
    )
    try:
        branch_receipt = branch_selector.build_factor_v2_decision_branch_receipt(
            path,
            expected_raw_file_sha256=expected,
        )
    except RuntimeError as exc:
        raise ValueError(f"terminal decision public verification failed: {exc}") from exc
    decision = _read_content_addressed_object(
        path,
        expected_file_sha256=expected,
        expected_basename_sha256=expected,
        field="terminal decision receipt",
    )
    _exact_fields(decision, _DECISION_FIELDS, field="terminal decision receipt")
    if branch_receipt.get("selected_branch") == branch_selector.LOW_RVOL_BRANCH:
        raise ValueError("low-rvol terminal branch requires a separate public authority")
    return decision, branch_receipt


def _feature_contract(
    *,
    schema_version: str,
    arm: str,
    feature_names: Sequence[str],
) -> dict[str, Any]:
    unsigned = {
        "schema_version": schema_version,
        "arm": arm,
        "feature_names": list(feature_names),
        "feature_count": len(feature_names),
        "feature_input_dtype": "float64",
        "feature_input_layout": "C_contiguous",
        "feature_types": ["float"] * len(feature_names),
        "feature_standardization": False,
        "order_semantics": "exact_positional",
    }
    return {**unsigned, "root_sha256": _sha256(unsigned)}


def validate_factor_v2_terminal_evaluator_development_adapter(
    *,
    decision_receipt_path: str | os.PathLike[str],
    expected_decision_receipt_raw_file_sha256: str,
    evaluation_manifest_path: str | os.PathLike[str],
    expected_evaluation_artifact_sha256: str,
    expected_evaluation_manifest_file_sha256: str,
    evaluation_verification_receipt: Mapping[str, Any],
    expected_evaluation_verification_receipt_sha256: str,
) -> dict[str, Any]:
    """Validate caller evidence structurally without granting source authority."""

    spec = _validate_local_frozen_contract()
    artifact_sha256 = _strict_sha256(
        expected_evaluation_artifact_sha256,
        field="expected evaluation artifact SHA-256",
    )
    manifest_file_sha256 = _strict_sha256(
        expected_evaluation_manifest_file_sha256,
        field="expected evaluation manifest file SHA-256",
    )
    decision, branch_receipt = _validated_decision(
        decision_receipt_path,
        expected_raw_file_sha256=expected_decision_receipt_raw_file_sha256,
    )
    manifest = _read_content_addressed_object(
        evaluation_manifest_path,
        expected_file_sha256=manifest_file_sha256,
        expected_basename_sha256=artifact_sha256,
        field="evaluation manifest",
    )
    execution, arm_sources, common = _validate_manifest(
        manifest,
        expected_artifact_sha256=artifact_sha256,
        spec=spec,
    )
    if (
        decision.get("evaluation_artifact_sha256") != artifact_sha256
        or decision.get("evaluation_manifest_file_sha256") != manifest_file_sha256
        or decision.get("arm_decisions") != common["decisions"]
        or branch_receipt.get("evaluation_artifact_sha256") != artifact_sha256
        or branch_receipt.get("arm_decisions") != common["decisions"]
    ):
        raise ValueError("terminal decision and evaluation manifest drifted")
    verification = _validate_verification_receipt(
        evaluation_verification_receipt,
        expected_receipt_sha256=(
            expected_evaluation_verification_receipt_sha256
        ),
        artifact_sha256=artifact_sha256,
        manifest_file_sha256=manifest_file_sha256,
        common_identity_root_sha256=common["root"],
        decisions=common["decisions"],
    )
    selected = branch_receipt["selected_branch"]
    selected_source = arm_sources[selected]
    base_features = _feature_contract(
        schema_version="factor-v2-base-feature-order/v1",
        arm="v2_control",
        feature_names=_BASE_FEATURE_NAMES,
    )
    selected_features = _feature_contract(
        schema_version="factor-v2-selected-feature-order/v1",
        arm=selected,
        feature_names=_ARM_FEATURE_NAMES[selected],
    )
    selected_features["arm_strategy_sha256"] = _ARM_STRATEGY_SHA256[selected]
    selected_features["root_sha256"] = _sha256(
        {key: value for key, value in selected_features.items() if key != "root_sha256"}
    )
    all_feature_contracts: dict[str, dict[str, Any]] = {}
    for arm in _ARM_ORDER:
        arm_features = _feature_contract(
            schema_version="factor-v2-arm-feature-order/v1",
            arm=arm,
            feature_names=_ARM_FEATURE_NAMES[arm],
        )
        arm_features["arm_strategy_sha256"] = _ARM_STRATEGY_SHA256[arm]
        arm_features["root_sha256"] = _sha256(
            {
                key: value
                for key, value in arm_features.items()
                if key != "root_sha256"
            }
        )
        all_feature_contracts[arm] = arm_features
    three_arm_unsigned = {
        "schema_version": "factor-v2-three-arm-oof-terminal-contract/v1",
        "arm_order": list(_ARM_ORDER),
        "arm_decisions": dict(common["decisions"]),
        "common_identity_root_sha256": common["root"],
        "all_three_arms_exact_identity": True,
        "fold_count": _PARENT_FOLD_COUNT,
        "arms": {
            arm: {
                "strategy_sha256": _ARM_STRATEGY_SHA256[arm],
                "feature_contract_root_sha256": all_feature_contracts[arm][
                    "root_sha256"
                ],
                "artifact_sha256": arm_sources[arm]["artifact_sha256"],
                "manifest_file_sha256": arm_sources[arm][
                    "manifest_file_sha256"
                ],
                "score_row_count": arm_sources[arm]["score_row_count"],
                "score_rows_sha256": arm_sources[arm]["score_rows_sha256"],
                "fold_receipts_sha256": arm_sources[arm][
                    "fold_receipts_sha256"
                ],
                "verification_receipt_sha256": arm_sources[arm][
                    "verification_receipt_sha256"
                ],
            }
            for arm in _ARM_ORDER
        },
    }
    three_arm_contract = {
        **three_arm_unsigned,
        "root_sha256": _sha256(three_arm_unsigned),
    }
    target_unsigned = {
        "schema_version": "factor-v2-terminal-target-contract/v1",
        "label": deepcopy(spec["label"]),
    }
    target_contract = {**target_unsigned, "root_sha256": _sha256(target_unsigned)}
    model_unsigned = {
        "schema_version": "factor-v2-terminal-model-contract/v1",
        "model": deepcopy(spec["model"]),
        "hyperparameter_search": False,
        "selected_arm_model_evidence_root_sha256": selected_source[
            "model_evidence_root_sha256"
        ],
        "selected_arm_runtime_binding_root_sha256": selected_source[
            "oof_runtime_binding_root_sha256"
        ],
    }
    model_contract = {**model_unsigned, "root_sha256": _sha256(model_unsigned)}
    fold_unsigned = {
        "schema_version": "factor-v2-terminal-fold-contract/v1",
        "source_session_count": _SOURCE_SESSION_COUNT,
        "source_session_start": _SOURCE_SESSION_START,
        "source_session_end": _SOURCE_SESSION_END,
        "source_sessions_sha256": _SOURCE_SESSIONS_SHA256,
        "fold_count": _PARENT_FOLD_COUNT,
        "folds_sha256": _PARENT_FOLDS_SHA256,
        "walk_forward": deepcopy(spec["walk_forward"]),
        "purge": spec["walk_forward"]["purge"],
        "selected_arm_fold_receipts_sha256": selected_source[
            "fold_receipts_sha256"
        ],
        "selected_arm_fold_score_rows_sha256": deepcopy(
            selected_source["fold_score_rows_sha256"]
        ),
    }
    fold_contract = {**fold_unsigned, "root_sha256": _sha256(fold_unsigned)}
    cost_names = (
        "signal_tag",
        "hold_days",
        "close_stop_loss_pct",
        "roundtrip_cost_bps",
        "slippage_bps",
        "capital_model",
        "exposure_multiplier",
        "annual_financing_rate_pct",
    )
    cost_unsigned = {
        "schema_version": "factor-v2-terminal-execution-cost-contract/v1",
        **{name: deepcopy(spec[name]) for name in cost_names},
    }
    execution_cost = {**cost_unsigned, "root_sha256": _sha256(cost_unsigned)}
    outcome_unsigned = {
        "schema_version": "factor-v2-terminal-outcome-contract/v1",
        "execution_source_binding_root_sha256": execution["root_sha256"],
        "parent_artifact_sha256": _PARENT_ARTIFACT_SHA256,
        "parent_manifest_file_sha256": _PARENT_MANIFEST_FILE_SHA256,
        "outcome_candidate_count": _PARENT_OUTCOME_ROW_COUNT,
        "outcome_payloads_sha256": _PARENT_OUTCOME_ROWS_SHA256,
        "execution_spool_receipt_sha256": execution[
            "execution_spool_receipt_sha256"
        ],
    }
    outcome_contract = {**outcome_unsigned, "root_sha256": _sha256(outcome_unsigned)}
    terminal_decision = {
        "schema_version": FACTOR_V2_TERMINAL_DECISION_BINDING_SCHEMA,
        "source_decision_receipt_raw_file_sha256": (
            expected_decision_receipt_raw_file_sha256
        ),
        "source_decision_receipt_sha256": decision["receipt_sha256"],
        "branch_receipt_sha256": branch_receipt["receipt_sha256"],
        "selected_branch": selected,
        "arm_decisions": dict(common["decisions"]),
        "public_contract_validated": True,
        "publisher_terminal_chain_verified": False,
    }
    independent_verification = {
        "schema_version": "factor-v2-terminal-independent-verification-binding/v1",
        "public_verifier": deepcopy(_PUBLIC_EVALUATOR_BINDING),
        "receipt_sha256": verification["receipt_sha256"],
        "evaluation_artifact_sha256": artifact_sha256,
        "evaluation_manifest_file_sha256": manifest_file_sha256,
        "checks": deepcopy(verification["checks"]),
        "verification_receipt_contract_validated": True,
        "public_verifier_replay_performed": False,
        "receipt_claimed_verified": verification["verified"],
        "verified": False,
    }
    unsigned_authority = {
        "schema_version": FACTOR_V2_TERMINAL_EVALUATOR_ADAPTER_SCHEMA,
        "authority_status": "UNVERIFIED_DEVELOPMENT_ADAPTER",
        "temporal_role": "development",
        "development_only": True,
        "contract_binding_validated": True,
        "verified": False,
        "factor_v2_spec_sha256": branch_selector.FACTOR_V2_SPEC_SHA256,
        "selected_branch": selected,
        "base_feature_contract": base_features,
        "selected_feature_contract": selected_features,
        "arm_feature_contracts": all_feature_contracts,
        "three_arm_oof_contract": three_arm_contract,
        "target_contract": target_contract,
        "fold_contract": fold_contract,
        "model_contract": model_contract,
        "execution_cost_contract": execution_cost,
        "outcome_contract": outcome_contract,
        "terminal_decision": terminal_decision,
        "independent_verification": independent_verification,
        "required_external_authorities": [
            "capability-bound-publisher-terminal-status-claim-receipt-chain/v1",
            "frozen-3e9-public-evaluator-independent-replay/v1",
        ],
        "publisher_terminal_chain_verified": False,
        "public_verifier_replay_performed": False,
        "source_authority_complete": False,
        "formal_materialization_eligible": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "recommendation_generation_eligible": False,
        "automatic_trading_eligible": False,
        "orders_submitted": False,
    }
    return {
        **unsigned_authority,
        "binding_root_sha256": _sha256(unsigned_authority),
    }
