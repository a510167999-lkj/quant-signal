from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from app import audited_pit_shallow_gbdt as shallow_gbdt
from app import audited_pit_score_contract as score_contract
from app import factor_v2_decision_branch_selector as branch_selector
from app.factor_v2_terminal_evaluator_authority import (
    FACTOR_V2_TERMINAL_DECISION_BINDING_SCHEMA,
    FACTOR_V2_TERMINAL_EVALUATOR_ADAPTER_SCHEMA,
    validate_factor_v2_terminal_evaluator_development_adapter,
)


ARM_ORDER = ("v2_control", "overnight_20", "intraday_20")
ARM_FEATURES = {
    "v2_control": [*shallow_gbdt.FEATURE_NAMES],
    "overnight_20": [
        *shallow_gbdt.FEATURE_NAMES,
        "overnight_return_20_pct_rank",
    ],
    "intraday_20": [
        *shallow_gbdt.FEATURE_NAMES,
        "intraday_return_20_pct_rank",
    ],
}
ARM_STRATEGY_SHA256 = {
    "v2_control": "216512ff65773e14a3ecf87c2679fc9d4c8787a6beab86d5972848315cd1ef7c",
    "overnight_20": "663890eb043353c58f565066210869035eae627c2a12030f860f29aa8ee84945",
    "intraday_20": "002a76e6c3300c886250573efaaf42e71543663b2c49a6c40e5baf505883811a",
}
EXPECTED_CHECKS = {
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
PARENT_ARTIFACT_SHA256 = (
    "9cff7474222360ed830d0f24164864dcb946695464467c8be9ccc5dda2b33469"
)
PARENT_MANIFEST_FILE_SHA256 = (
    "b8b0ef670b00742f5ccb9aaa5a7535b55590c22ecebc8fa70f82b6f12816d5b5"
)
PARENT_OUTCOME_ROW_COUNT = 1_782_860
PARENT_OUTCOME_ROWS_SHA256 = (
    "1e14af73e758d0f686b095e4b19e896eb6a1bf0c0885c7ebca299ecbec5b9730"
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _development_sessions() -> list[str]:
    holidays = frozenset(
        """
        2024-09-16 2024-09-17
        2024-10-01 2024-10-02 2024-10-03 2024-10-04 2024-10-07
        2025-01-01
        2025-01-28 2025-01-29 2025-01-30 2025-01-31 2025-02-03 2025-02-04
        2025-04-04
        2025-05-01 2025-05-02 2025-05-05
        2025-06-02
        2025-10-01 2025-10-02 2025-10-03 2025-10-06 2025-10-07 2025-10-08
        2026-01-01 2026-01-02
        2026-02-16 2026-02-17 2026-02-18 2026-02-19 2026-02-20 2026-02-23
        2026-04-06
        2026-05-01 2026-05-04 2026-05-05
        2026-06-19
        """.split()
    )
    sessions: list[str] = []
    cursor = date(2024, 7, 5)
    end = date(2026, 7, 3)
    while cursor <= end:
        if cursor.weekday() < 5 and cursor.isoformat() not in holidays:
            sessions.append(cursor.isoformat())
        cursor += timedelta(days=1)
    assert len(sessions) == 483
    assert _sha(sessions) == "d4dd11e90438a407ba470398a218696a3abe4151881dd41956248dace37c27b6"
    return sessions


def _evaluation_contract() -> dict[str, Any]:
    cost = {
        field: deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC[field])
        for field in (
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
    selection = deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["selection"])
    thresholds = deepcopy(
        shallow_gbdt.SHALLOW_GBDT_OOF_SPEC["advancement_thresholds"]
    )
    unsigned = {
        "schema_version": "audited-pit-factor-v2-frozen-evaluation-contract/v1",
        "strategy_sha256": _sha(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC),
        "score_contract": dict(score_contract.SHALLOW_GBDT_SCORE_CONTRACT),
        "score_contract_sha256": _sha(
            dict(score_contract.SHALLOW_GBDT_SCORE_CONTRACT)
        ),
        "positive_filter": {
            "schema_version": "audited-pit-training-dataset-oof-positive-filter/v1",
            "comparison": "strictly_greater_than_0.5",
        },
        "cost_and_execution": cost,
        "cost_and_execution_sha256": _sha(cost),
        "selection": selection,
        "selection_sha256": _sha(selection),
        "advancement_thresholds": thresholds,
        "advancement_thresholds_sha256": _sha(thresholds),
    }
    return {**unsigned, "root_sha256": _sha(unsigned)}


def _execution_source_binding(sessions: list[str]) -> dict[str, Any]:
    spool = {
        "file": "strict_execution_spool.sqlite3",
        "size_bytes": 1,
        "sha256": _sha("spool"),
        "receipt_sha256": _sha("spool-receipt"),
    }
    unsigned = {
        "schema_version": "audited-pit-training-dataset-oof-execution-source-binding/v1",
        "materialization": {
            "artifact_sha256": PARENT_ARTIFACT_SHA256,
            "manifest_file_sha256": PARENT_MANIFEST_FILE_SHA256,
        },
        "sessions": sessions,
        "sessions_sha256": _sha(sessions),
        "ordered_session_count": len(sessions),
        "ordered_sessions_root_sha256": _sha(
            [
                {"ordinal": ordinal, "session": session}
                for ordinal, session in enumerate(sessions)
            ]
        ),
        "first_session": sessions[0],
        "last_session": sessions[-1],
        "outcome_candidate_count": PARENT_OUTCOME_ROW_COUNT,
        "outcome_payloads_sha256": PARENT_OUTCOME_ROWS_SHA256,
        "execution_spool": spool,
        "execution_spool_receipt_sha256": spool["receipt_sha256"],
    }
    return {**unsigned, "root_sha256": _sha(unsigned)}


def _arm_source(arm: str) -> dict[str, Any]:
    return {
        "artifact_sha256": _sha([arm, "artifact"]),
        "manifest_file_sha256": _sha([arm, "manifest"]),
        "arm": arm,
        "strategy_sha256": ARM_STRATEGY_SHA256[arm],
        "score_database_sha256": _sha([arm, "scores"]),
        "score_row_count": 1_511_000,
        "score_rows_sha256": _sha([arm, "score-rows"]),
        "fold_count": 6,
        "fold_receipts_sha256": _sha([arm, "fold-receipts"]),
        "fold_score_rows_sha256": [_sha([arm, index]) for index in range(1, 7)],
        "model_evidence_root_sha256": _sha([arm, "models"]),
        "oof_runtime_binding_root_sha256": _sha([arm, "runtime"]),
        "source_binding_root_sha256": _sha([arm, "source"]),
        "gate_binding_root_sha256": _sha([arm, "gate"]),
        "verification_receipt_sha256": _sha([arm, "verification"]),
    }


def _arm_decisions(selected: str) -> dict[str, str]:
    if selected == "v2_control":
        return {"v2_control": "GREEN", "overnight_20": "RED", "intraday_20": "RED"}
    if selected == "overnight_20":
        return {"v2_control": "RED", "overnight_20": "GREEN", "intraday_20": "RED"}
    if selected == "intraday_20":
        return {"v2_control": "RED", "overnight_20": "RED", "intraday_20": "GREEN"}
    if selected == branch_selector.LOW_RVOL_BRANCH:
        return {arm: "RED" for arm in ARM_ORDER}
    raise AssertionError(selected)


def _fixture(
    tmp_path: Path,
    *,
    selected: str = "v2_control",
    mutate_manifest: Callable[[dict[str, Any]], None] | None = None,
    mutate_verification: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    sessions = _development_sessions()
    execution_source = _execution_source_binding(sessions)
    arm_sources = {arm: _arm_source(arm) for arm in ARM_ORDER}
    decisions = _arm_decisions(selected)
    common_root = _sha(["common", 1_511_000])
    fold_counts = [251_834, 251_834, 251_833, 251_833, 251_833, 251_833]
    per_fold = [
        {
            "fold_index": index,
            "full_gate_score_row_count": count + (1 if index == 1 else 0),
            "excluded_validation_candidate_count": 1 if index == 1 else 0,
            "expected_score_row_count": count,
            "common_identity_root_sha256": _sha(["common", index]),
            "candidate_keys_sha256": _sha(["keys", index]),
            "signal_dates_sha256": _sha(["dates", index]),
        }
        for index, count in enumerate(fold_counts, start=1)
    ]
    common_identity_unsigned = {
        "schema_version": "audited-pit-factor-v2-common-oof-identity/v1",
        "identity_fields": [
            "fold_index",
            "validation_ordinal",
            "candidate_key",
            "signal_date",
        ],
        "arm_order": list(ARM_ORDER),
        "expected_count_formula": (
            "full_gate_score_row_count - excluded_validation_candidate_count"
        ),
        "full_gate_score_row_count": 1_511_001,
        "full_gate_identity_root_sha256": _sha("full-gate-identity"),
        "global_excluded_candidate_count": 1,
        "excluded_validation_candidate_count": 1,
        "excluded_non_validation_candidate_count": 0,
        "excluded_validation_candidate_keys_sha256": _sha(["excluded"]),
        "excluded_validation_identity_root_sha256": _sha("excluded-identity"),
        "expected_score_row_count": 1_511_000,
        "common_identity_root_sha256": common_root,
        "observed_arm_score_row_counts": {
            arm: 1_511_000 for arm in ARM_ORDER
        },
        "observed_arm_identity_root_sha256": {
            arm: common_root for arm in ARM_ORDER
        },
        "per_fold": per_fold,
        "all_three_arms_exact_identity": True,
    }
    common_identity = {
        **common_identity_unsigned,
        "receipt_sha256": _sha(common_identity_unsigned),
    }
    manifest: dict[str, Any] = {
        "schema_version": "audited-pit-factor-v2-development-evaluation/v1",
        "evaluation_producer_binding": {
            "schema_version": "audited-pit-factor-v2-development-evaluation-producer/v1",
            "loaded_entrypoints": {},
            "module_sha256": {},
            "pandas_version": "2.2.3",
            "python_version": "3.11.5",
            "sqlite_version": "3.42.0",
            "root_sha256": branch_selector.EVALUATION_PRODUCER_ROOT_SHA256,
        },
        "temporal_role": "development",
        "factor_v2_spec_sha256": branch_selector.FACTOR_V2_SPEC_SHA256,
        "arm_order": list(ARM_ORDER),
        "evaluation_contract": _evaluation_contract(),
        "source_binding": {
            "parent_materialization": execution_source["materialization"],
            "execution_spool": execution_source["execution_spool"],
            "execution_source_binding": execution_source,
            "execution_source_binding_root_sha256": execution_source["root_sha256"],
            "overlay_materialization": {"artifact_sha256": _sha("overlay")},
            "full_parent_replay_gate": {"artifact_sha256": _sha("gate")},
            "arms": arm_sources,
        },
        "evaluation_sessions": sessions[126:],
        "evaluation_sessions_sha256": _sha(sessions[126:]),
        "common_identity": common_identity,
        "arms": {
            arm: {
                "schema_version": "audited-pit-factor-v2-arm-development-evaluation/v1",
                "arm": arm,
                "score_source": arm_sources[arm],
                "preregistered_gate": {
                    "embargo_consumed": False,
                    "final_oos_consumed": False,
                },
                "preregistered_decision": decisions[arm],
            }
            for arm in ARM_ORDER
        },
        "comparison": {
            "schema_version": "audited-pit-factor-v2-common-subset-comparison/v1",
            "arm_order": list(ARM_ORDER),
            "baseline_arm": "v2_control",
            "common_prefilter_score_identity": True,
            "same_execution_source": True,
            "automatic_winner_selected": False,
        },
        "scope": {
            "point_in_time": True,
            "development_only": True,
            "strict_artifact_native_execution": True,
            "training_performed_by_evaluator": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "eligible_for_profile_registration": False,
            "production_recommendation_eligible": False,
        },
        "automatic_winner_selected": False,
        "production_profile_registered": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    if mutate_manifest is not None:
        mutate_manifest(manifest)
    artifact_sha256 = _sha(manifest)
    manifest = {**manifest, "artifact_sha256": artifact_sha256}
    manifest_raw = _canonical_bytes(manifest)
    manifest_file_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    manifest_path = tmp_path / f"{artifact_sha256}.json"
    manifest_path.write_bytes(manifest_raw)

    verification: dict[str, Any] = {
        "schema_version": "audited-pit-factor-v2-development-evaluation-verification/v1",
        "artifact_sha256": artifact_sha256,
        "manifest_file_sha256": manifest_file_sha256,
        "factor_v2_spec_sha256": branch_selector.FACTOR_V2_SPEC_SHA256,
        "arm_order": list(ARM_ORDER),
        "common_identity_root_sha256": manifest["common_identity"][
            "common_identity_root_sha256"
        ],
        "arm_decisions": dict(decisions),
        "automatic_winner_selected": False,
        "checks": dict(EXPECTED_CHECKS),
        "verified": True,
    }
    if mutate_verification is not None:
        mutate_verification(verification)
    verification["receipt_sha256"] = _sha(verification)

    decision = {
        "schema_version": branch_selector.RECEIPT_SCHEMA_VERSION,
        "temporal_role": "development",
        "factor_v2_spec_sha256": branch_selector.FACTOR_V2_SPEC_SHA256,
        "evaluation_artifact_sha256": artifact_sha256,
        "evaluation_manifest_file_sha256": manifest_file_sha256,
        "evaluation_producer_root_sha256": branch_selector.EVALUATION_PRODUCER_ROOT_SHA256,
        "verification_producer_root_sha256": branch_selector.VERIFICATION_PRODUCER_ROOT_SHA256,
        "arm_order": list(ARM_ORDER),
        "arm_decisions": dict(decisions),
        "source_run_identity": branch_selector.SOURCE_RUN_IDENTITY,
        "verified": True,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "production_recommendation_eligible": False,
    }
    decision["receipt_sha256"] = _sha(decision)
    decision_raw = _canonical_bytes(decision)
    decision_raw_sha256 = hashlib.sha256(decision_raw).hexdigest()
    decision_path = tmp_path / f"{decision_raw_sha256}.json"
    decision_path.write_bytes(decision_raw)
    kwargs = {
        "decision_receipt_path": decision_path,
        "expected_decision_receipt_raw_file_sha256": decision_raw_sha256,
        "evaluation_manifest_path": manifest_path,
        "expected_evaluation_artifact_sha256": artifact_sha256,
        "expected_evaluation_manifest_file_sha256": manifest_file_sha256,
        "evaluation_verification_receipt": verification,
        "expected_evaluation_verification_receipt_sha256": verification[
            "receipt_sha256"
        ],
    }
    return kwargs, manifest


@pytest.mark.parametrize("selected", ARM_ORDER)
def test_authority_binds_selected_branch_and_all_terminal_contracts(
    tmp_path: Path,
    selected: str,
) -> None:
    kwargs, manifest = _fixture(tmp_path, selected=selected)

    result = validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)

    assert result["schema_version"] == FACTOR_V2_TERMINAL_EVALUATOR_ADAPTER_SCHEMA
    assert result["authority_status"] == "UNVERIFIED_DEVELOPMENT_ADAPTER"
    assert result["contract_binding_validated"] is True
    assert result["verified"] is False
    assert (
        result["terminal_decision"]["schema_version"]
        == FACTOR_V2_TERMINAL_DECISION_BINDING_SCHEMA
    )
    assert result["terminal_decision"]["schema_version"] != (
        branch_selector.BRANCH_RECEIPT_SCHEMA_VERSION
    )
    assert result["selected_branch"] == selected
    assert result["base_feature_contract"]["feature_names"] == list(
        shallow_gbdt.FEATURE_NAMES
    )
    assert result["selected_feature_contract"]["feature_names"] == ARM_FEATURES[
        selected
    ]
    assert result["three_arm_oof_contract"]["arm_order"] == list(ARM_ORDER)
    assert set(result["three_arm_oof_contract"]["arms"]) == set(ARM_ORDER)
    assert result["three_arm_oof_contract"]["arm_decisions"] == _arm_decisions(
        selected
    )
    assert result["target_contract"]["label"] == shallow_gbdt.SHALLOW_GBDT_OOF_SPEC[
        "label"
    ]
    assert result["fold_contract"]["source_session_count"] == 483
    assert result["fold_contract"]["fold_count"] == 6
    assert result["fold_contract"]["purge"] == (
        "complete_exit_date_strictly_before_validation_start"
    )
    assert result["model_contract"]["model"] == shallow_gbdt.SHALLOW_GBDT_OOF_SPEC[
        "model"
    ]
    assert result["execution_cost_contract"]["roundtrip_cost_bps"] == 25.0
    assert result["execution_cost_contract"]["slippage_bps"] == 10.0
    assert result["outcome_contract"][
        "execution_source_binding_root_sha256"
    ] == manifest["source_binding"]["execution_source_binding_root_sha256"]
    assert result["outcome_contract"]["root_sha256"] == _sha(
        {
            key: value
            for key, value in result["outcome_contract"].items()
            if key != "root_sha256"
        }
    )
    assert result["terminal_decision"]["branch_receipt_sha256"]
    assert result["independent_verification"]["receipt_sha256"] == kwargs[
        "expected_evaluation_verification_receipt_sha256"
    ]
    assert (
        result["independent_verification"]["public_verifier_replay_performed"]
        is False
    )
    assert result["binding_root_sha256"] == _sha(
        {
            key: value
            for key, value in result.items()
            if key != "binding_root_sha256"
        }
    )
    for field in (
        "embargo_consumed",
        "final_oos_consumed",
        "production_profile_registered",
        "production_recommendation_eligible",
        "recommendation_generation_eligible",
        "automatic_trading_eligible",
        "orders_submitted",
        "publisher_terminal_chain_verified",
        "public_verifier_replay_performed",
        "source_authority_complete",
        "formal_materialization_eligible",
    ):
        assert result[field] is False


def test_low_rvol_fallback_requires_a_separate_terminal_public_authority(
    tmp_path: Path,
) -> None:
    kwargs, _ = _fixture(tmp_path, selected=branch_selector.LOW_RVOL_BRANCH)

    with pytest.raises(ValueError, match="low-rvol|terminal"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


@pytest.mark.parametrize(
    "mutator,match",
    [
        (
            lambda value: value["source_binding"]["execution_source_binding"][
                "sessions"
            ].pop(),
            "483|session",
        ),
        (
            lambda value: value["source_binding"]["arms"]["v2_control"].__setitem__(
                "fold_count", 5
            ),
            "fold",
        ),
        (
            lambda value: value["common_identity"].__setitem__(
                "all_three_arms_exact_identity", False
            ),
            "identity",
        ),
        (
            lambda value: value["evaluation_contract"]["cost_and_execution"].__setitem__(
                "roundtrip_cost_bps", 24.0
            ),
            "cost|execution",
        ),
        (
            lambda value: value["evaluation_contract"]["cost_and_execution"].__setitem__(
                "slippage_bps", 11.0
            ),
            "cost|execution",
        ),
        (
            lambda value: value["scope"].__setitem__("embargo_consumed", True),
            "scope|embargo",
        ),
    ],
)
def test_manifest_contract_drift_fails_closed(
    tmp_path: Path,
    mutator: Callable[[dict[str, Any]], None],
    match: str,
) -> None:
    kwargs, _ = _fixture(tmp_path, mutate_manifest=mutator)

    with pytest.raises(ValueError, match=match):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_public_verification_receipt_must_match_manifest_and_decision(
    tmp_path: Path,
) -> None:
    kwargs, _ = _fixture(
        tmp_path,
        mutate_verification=lambda value: value["arm_decisions"].__setitem__(
            "v2_control", "RED"
        ),
    )

    with pytest.raises(ValueError, match="decision|verification"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_public_verification_checks_are_exact_and_all_true(tmp_path: Path) -> None:
    kwargs, _ = _fixture(
        tmp_path,
        mutate_verification=lambda value: value["checks"].__setitem__(
            "frozen_selection_cost_and_gates_verified", False
        ),
    )

    with pytest.raises(ValueError, match="check|verification"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_real_producer_root_field_cannot_be_replaced_by_legacy_alias(
    tmp_path: Path,
) -> None:
    def mutate(value: dict[str, Any]) -> None:
        producer = value["evaluation_producer_binding"]
        producer["producer_root_sha256"] = producer.pop("root_sha256")

    kwargs, _ = _fixture(tmp_path, mutate_manifest=mutate)

    with pytest.raises(ValueError, match="producer.*field|producer.*schema"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


@pytest.mark.parametrize(
    "field,value",
    [
        ("outcome_candidate_count", PARENT_OUTCOME_ROW_COUNT - 1),
        ("outcome_payloads_sha256", "0" * 64),
    ],
)
def test_frozen_parent_outcome_contract_is_exact(
    tmp_path: Path,
    field: str,
    value: Any,
) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["source_binding"]["execution_source_binding"][field] = value

    kwargs, _ = _fixture(tmp_path, mutate_manifest=mutate)

    with pytest.raises(ValueError, match="outcome"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_selected_arm_strategy_hash_is_not_caller_defined(tmp_path: Path) -> None:
    def mutate(manifest: dict[str, Any]) -> None:
        manifest["source_binding"]["arms"]["v2_control"][
            "strategy_sha256"
        ] = "0" * 64

    kwargs, _ = _fixture(tmp_path, mutate_manifest=mutate)

    with pytest.raises(ValueError, match="strategy"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_verification_receipt_extra_field_is_rejected(tmp_path: Path) -> None:
    kwargs, _ = _fixture(
        tmp_path,
        mutate_verification=lambda value: value.__setitem__("unbound", True),
    )

    with pytest.raises(ValueError, match="verification.*field"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_duplicate_manifest_json_key_is_rejected_before_authority(tmp_path: Path) -> None:
    kwargs, _ = _fixture(tmp_path)
    path = Path(kwargs["evaluation_manifest_path"])
    raw = path.read_bytes()
    duplicate = raw[:-1] + b',"scope":{}}'
    path.write_bytes(duplicate)
    kwargs["expected_evaluation_manifest_file_sha256"] = hashlib.sha256(
        duplicate
    ).hexdigest()

    with pytest.raises(ValueError, match="duplicate JSON key|strict JSON"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_caller_resigned_complete_chain_never_becomes_formal_authority(
    tmp_path: Path,
) -> None:
    kwargs, _ = _fixture(tmp_path, selected="overnight_20")

    result = validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)

    assert result["authority_status"] == "UNVERIFIED_DEVELOPMENT_ADAPTER"
    assert result["verified"] is False
    assert result["source_authority_complete"] is False
    assert result["formal_materialization_eligible"] is False
    assert result["public_verifier_replay_performed"] is False
    assert result["publisher_terminal_chain_verified"] is False


def test_expected_content_addresses_cannot_be_replaced_by_self_signed_values(
    tmp_path: Path,
) -> None:
    kwargs, _ = _fixture(tmp_path)
    kwargs["expected_evaluation_manifest_file_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="content address|SHA"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_manifest_extra_field_and_noncanonical_bytes_are_rejected(tmp_path: Path) -> None:
    kwargs, manifest = _fixture(tmp_path)
    path = Path(kwargs["evaluation_manifest_path"])
    manifest["unbound"] = True
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with pytest.raises(ValueError, match="canonical|content address|field"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)


def test_frozen_model_contract_drift_is_rejected_before_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kwargs, _ = _fixture(tmp_path)
    mutated = deepcopy(shallow_gbdt.SHALLOW_GBDT_OOF_SPEC)
    mutated["model"]["parameters"]["max_depth"] = 3
    monkeypatch.setattr(shallow_gbdt, "SHALLOW_GBDT_OOF_SPEC", mutated)

    with pytest.raises(ValueError, match="model|strategy|frozen"):
        validate_factor_v2_terminal_evaluator_development_adapter(**kwargs)
