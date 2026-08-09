from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

import pytest

from app import factor_v3_development_input_authority as candidate_authority
from app import factor_v3_daily_basic_733_exact_set_authority as daily_authority
from app import factor_v2_decision_branch_selector as factor_v2_branch_selector
from app import factor_v3_formal_development_input_activation as activation
from app import factor_v3_formal_development_input_activation_independent_core as independent_core
from app import factor_v3_feature_history_frozen_source_attestation as frozen_history
from app import jiaoch_daily_basic_exact_set_authority as legacy_daily
from tests import test_factor_v2_decision_branch_selector as branch_test_support
from tests import test_factor_v3_daily_basic_733_exact_set_authority as daily_test_support
from tests import test_factor_v3_development_input_authority as candidate_test_support


@pytest.fixture(autouse=True)
def _explicit_native_parent_source_test_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def points_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": "DISPOSABLE_TEST_FIXTURE_ONLY",
            "contract_binding_validated": True,
            "schema": activation.POINTS_CONTRACT_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def parent_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": "DISPOSABLE_TEST_FIXTURE_ONLY",
            "contract_binding_validated": True,
            "schema": activation.PARENT_SOURCE_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def evaluation_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": "DISPOSABLE_TEST_FIXTURE_ONLY",
            "contract_binding_validated": True,
            "schema": activation.FACTOR_V2_EVALUATION_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def history_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": "DISPOSABLE_TEST_FIXTURE_ONLY",
            "contract_binding_validated": True,
            "schema": activation.FEATURE_HISTORY_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    def daily_verifier(**evidence: Any) -> dict[str, Any]:
        return {
            **evidence,
            "authority_scope": "DISPOSABLE_TEST_FIXTURE_ONLY",
            "contract_binding_validated": True,
            "schema": activation.DAILY_BASIC_NATIVE_AUTHORITY_VERDICT_SCHEMA,
            "test_fixture_only": True,
            "verified": False,
        }

    monkeypatch.setattr(
        activation,
        "_verify_points_contract_authority_evidence",
        points_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_native_parent_source_authority_evidence",
        parent_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_factor_v2_evaluation_authority_evidence",
        evaluation_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_feature_history_authority_evidence",
        history_verifier,
    )
    monkeypatch.setattr(
        activation,
        "_verify_daily_basic_authority_evidence",
        daily_verifier,
    )


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    raw = value if isinstance(value, bytes) else _bytes(value)
    return hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: Any) -> str:
    raw = _bytes(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return _sha(raw)


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _false_scope(fields: tuple[str, ...]) -> dict[str, bool]:
    return {field: False for field in fields}


def _assert_no_capability_keys(value: Any) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            assert "capability" not in key.lower().replace("-", "").replace("_", "")
            _assert_no_capability_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            _assert_no_capability_keys(nested)


def _parent_projection() -> tuple[dict[str, Any], dict[str, str]]:
    rows = [
        {
            "candidate_key": "cn-a-share:000001.SZ|2024-07-08",
            "features": [0.1] * 10,
            "signal_date": "2024-07-08",
            "ts_code": "000001.SZ",
        },
        {
            "candidate_key": "cn-a-share:600001.SH|2024-07-09",
            "features": [0.2] * 10,
            "signal_date": "2024-07-09",
            "ts_code": "600001.SH",
        },
    ]
    candidate_keys = sorted(row["candidate_key"] for row in rows)
    source_projection = [
        {
            "candidate_key": row["candidate_key"],
            "signal_date": row["signal_date"],
            "features": row["features"],
        }
        for row in rows
    ]
    roots = {
        "candidate_keys_sha256": _sha(candidate_keys),
        "source_feature_projection_rows_sha256": _sha(source_projection),
        "full_export_rows_sha256": _sha(rows),
    }
    projection = {
        "schema": activation.PARENT_PROJECTION_SCHEMA,
        "contract": deepcopy(activation.PARENT_PROJECTION_CONTRACT),
        **roots,
    }
    return {"rows": rows, "rows_sha256": _sha(rows)}, projection


def _complete_daily_statistic(trade_date: str) -> dict[str, Any]:
    segment_counts = {
        "BSE": 1,
        "SSE_MAIN": 1,
        "SSE_STAR": 1,
        "SZSE_CHINEXT": 1,
        "SZSE_MAIN": 1,
    }
    codes = ["000001.SZ", "300001.SZ", "430001.BJ", "600001.SH", "688001.SH"]
    collection_sha = _sha(["collection", trade_date])
    attempt_sha = _sha(["attempt", trade_date])
    raw_sha = _sha(["raw-body", trade_date])
    return {
        "authoritative_daily_filtered_codes_sha256": _sha(codes),
        "authoritative_daily_generation_id": _sha(["generation", trade_date]),
        "authoritative_daily_generation_lineage_sha256": _sha(
            ["lineage", trade_date]
        ),
        "authoritative_daily_generation_manifest_sha256": _sha(
            ["manifest", trade_date]
        ),
        "authoritative_daily_raw_codes_sha256": _sha(["raw", trade_date]),
        "authoritative_daily_raw_row_count": len(codes),
        "authoritative_daily_raw_segment_counts": dict(segment_counts),
        "authoritative_daily_resolved_identities_sha256": _sha(codes),
        "authoritative_daily_transition_excluded_codes_sha256": _sha([]),
        "authoritative_daily_transition_excluded_row_count": 0,
        "authoritative_daily_vintage": f"{trade_date}T16:00:00+08:00",
        "collection_set_relative_path": (
            f"daily_basic_collection_sets/sha256/{collection_sha[:2]}/"
            f"{collection_sha}.json"
        ),
        "collection_set_sha256": collection_sha,
        "daily_basic_attempt_relative_path": (
            f"daily_basic_attempts/sha256/{attempt_sha[:2]}/{attempt_sha}.json"
        ),
        "daily_basic_attempt_sha256": attempt_sha,
        "daily_basic_canonical_rows_sha256": _sha(["normalized", trade_date]),
        "daily_basic_filtered_codes_sha256": _sha(codes),
        "daily_basic_normalization_receipt_sha256": _sha(
            ["normalization", trade_date]
        ),
        "daily_basic_raw_codes_sha256": _sha(codes),
        "daily_basic_raw_relative_path": (
            f"daily_basic_raw/sha256/{raw_sha[:2]}/{raw_sha}.body"
        ),
        "daily_basic_raw_row_count": len(codes),
        "daily_basic_raw_segment_counts": dict(segment_counts),
        "daily_basic_raw_sha256": raw_sha,
        "daily_basic_resolved_identities_sha256": _sha(codes),
        "daily_basic_source_normalization_rows_sha256": _sha(
            ["source-normalization", trade_date]
        ),
        "daily_basic_transition_excluded_codes_sha256": _sha([]),
        "daily_basic_transition_excluded_row_count": 0,
        "daily_basic_transition_overlap_comparison_fields": [
            "turnover_rate",
            "turnover_rate_f",
            "free_share",
            "float_share",
            "total_mv",
            "circ_mv",
        ],
        "daily_basic_transition_overlap_pair_count": 0,
        "daily_basic_transition_overlap_root_sha256": _sha([]),
        "extra_daily_basic_code_count": 0,
        "missing_authoritative_daily_code_count": 0,
        "source_ts_code_exact_set_verified_after_transition_filter": True,
        "target_scope_codes_sha256": _sha(
            ["000001.SZ", "300001.SZ", "600001.SH"]
        ),
        "target_scope_resolved_identities_sha256": _sha(
            ["000001.SZ", "300001.SZ", "600001.SH"]
        ),
        "target_scope_row_count": 3,
        "trade_date": trade_date,
        "transition_filtered_row_count": len(codes),
        "transition_resolved_identity_exact_set_verified": True,
    }


def _candidate_publication(
    root: Path,
) -> tuple[
    Path,
    str,
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    str,
    dict[str, Path],
]:
    sessions = [
        (date(2023, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(733)
    ]
    prewindow = sessions[:250]
    development = sessions[250:]
    source_dates = [*prewindow, *development[:-1]]
    parent_rows, parent_projection = _parent_projection()
    producer_snapshot = candidate_authority._candidate_producer_snapshot()
    daily_adapter = {
        "candidate_producer_snapshot_root_sha256": producer_snapshot["root_sha256"],
        "schema": candidate_authority.DERIVED_ADAPTER_SCHEMA,
        "source_authority_root_sha256": _sha("daily-source-authority"),
        "source_publication_sha256": _sha("daily-source-publication"),
        "source_receipt_sha256": _sha("daily-source-receipt"),
        **_false_scope(candidate_authority.PROVENANCE_FALSE_FIELDS),
    }
    evaluation_projection = {
        "schema": activation.FACTOR_V2_EVALUATION_PROJECTION_SCHEMA,
        "terminal_decision_descriptor_sha256": _sha("terminal-decision"),
        "evaluator_descriptor_sha256": _sha("evaluator"),
        "cost_slippage_execution_descriptor_sha256": _sha("cost-slippage"),
    }
    branch_unsigned = {
        "arm_decisions": {
            "v2_control": "GREEN",
            "overnight_20": "RED",
            "intraday_20": "RED",
        },
        "arm_order": list(factor_v2_branch_selector.ARM_ORDER),
        "contract_binding_validated": True,
        "embargo_consumed": False,
        "evaluation_artifact_sha256": _sha("candidate-evaluation-artifact"),
        "final_oos_consumed": False,
        "formal_materialization_eligible": False,
        "low_rvol_overlay_status": "VOID",
        "production_recommendation_eligible": False,
        "publisher_terminal_chain_verified": False,
        "schema_version": "factor-v2-decision-branch-structural-adapter/v2",
        "selected_arm": "v2_control",
        "selected_branch": "v2_control",
        "selection_rule": factor_v2_branch_selector.SELECTION_RULE,
        "source_decision_receipt_raw_file_sha256": _sha("candidate-decision-file"),
        "source_decision_receipt_sha256": _sha("candidate-decision-receipt"),
        "source_authority_complete": False,
        "verified": False,
    }
    branch_receipt = {
        **branch_unsigned,
        "receipt_sha256": _sha(branch_unsigned),
    }
    evaluation_source_binding = {
        "branch_receipt_sha256": branch_receipt["receipt_sha256"],
        "decision_receipt_sha256": _sha("candidate-decision-receipt"),
        "decision_receipt_raw_file_sha256": _sha("candidate-decision-file"),
        "evaluation_artifact_sha256": _sha("candidate-evaluation-artifact"),
        "selected_branch": "v2_control",
        "snapshot_schema": "factor-v3-development-factor-v2-evaluation-snapshot/v2",
    }
    calendar = {
        "all_market_session_count": 733,
        "all_market_sessions": sessions,
        "all_market_sessions_sha256": _sha(sessions),
        "development_session_count": 483,
        "development_sessions": development,
        "development_sessions_sha256": _sha(development),
        "prewindow_session_count": 250,
        "prewindow_sessions": prewindow,
        "prewindow_sessions_sha256": _sha(prewindow),
        "schema": "factor-v3-development-calendar-snapshot/v2",
        "source_date_count": 732,
        "source_dates": source_dates,
        "source_dates_sha256": _sha(source_dates),
    }
    per_date = [
        {
            "normalized_rows_sha256": _sha(["normalized", trade_date]),
            "raw_source_rows_sha256": _sha(["raw", trade_date]),
            "segment_counts": {name: 1 for name in activation.UPSTREAM_SOURCE_SEGMENTS},
            "trade_date": trade_date,
        }
        for trade_date in source_dates
    ]
    board = {
        "derived_adapter": daily_adapter,
        "downstream_scope_filter": "mainboard_chinext_candidate_join_only",
        "per_date": per_date,
        "per_date_board_ledger_root_sha256": _sha(per_date),
        "pre_filter_segment_counts": {
            name: len(source_dates) for name in activation.UPSTREAM_SOURCE_SEGMENTS
        },
        "preserved_before_target_scope_filter": True,
        "schema": "factor-v3-development-upstream-board-ledger/v2",
        "source_segments": list(activation.UPSTREAM_SOURCE_SEGMENTS),
    }
    parent_expectation = candidate_authority.points.FACTOR_V3_POINTS_CONTRACT[
        "preregistered_parent_expectation"
    ]
    pinned_parent_adapter_sha = _sha("pinned-parent-adapter")
    parent_source_file_sha = _sha("parent-source-file")
    parent_derived_adapter = {
        "added_identity_field": "ts_code_from_candidate_key",
        "candidate_key_order": ["candidate_key"],
        "candidate_key_projection_fields": ["candidate_key"],
        "materializer_v1_hash_compatible": False,
        "pinned_parent_adapter_source_spec_sha256": pinned_parent_adapter_sha,
        "schema": "factor-v3-factor-v2-parent-row-derived-adapter/v1",
        "source_feature_order": ["signal_date", "candidate_key"],
        "source_feature_projection_fields": [
            "candidate_key",
            "signal_date",
            "features",
        ],
        "full_export_fields": [
            "candidate_key",
            "features",
            "signal_date",
            "ts_code",
        ],
        "full_export_order": ["signal_date", "candidate_key"],
        **{
            field: parent_projection[field]
            for field in activation.PARENT_PROJECTION_FIELDS
        },
    }
    parent_binding_unsigned = {
        "authority_status": "PINNED_HASH_MATCH_ONLY_SOURCE_PRODUCER_UNVERIFIED",
        "candidate_keys_sha256": parent_projection["candidate_keys_sha256"],
        "content_hash_bound": True,
        "development_only": True,
        "factor_v2_common_eligible_overlay_artifact_sha256": parent_expectation[
            "factor_v2_common_eligible_overlay_artifact_sha256"
        ],
        "factor_v2_common_eligible_overlay_manifest_file_sha256": parent_expectation[
            "factor_v2_common_eligible_overlay_manifest_file_sha256"
        ],
        "factor_v2_common_eligible_receipt_sha256": parent_expectation[
            "factor_v2_common_eligible_receipt_sha256"
        ],
        "factor_v2_parent_artifact_sha256": parent_expectation[
            "factor_v2_parent_artifact_sha256"
        ],
        "factor_v2_parent_manifest_file_sha256": parent_expectation[
            "factor_v2_parent_manifest_file_sha256"
        ],
        "file_sha256": parent_source_file_sha,
        "full_export_rows_sha256": parent_projection["full_export_rows_sha256"],
        "formal_materialization_eligible": False,
        "pinned_frozen_source_commit": candidate_authority.FACTOR_V2_FROZEN_SOURCE_COMMIT,
        "pinned_frozen_source_tree_oid": candidate_authority.FACTOR_V2_FROZEN_SOURCE_TREE_OID,
        "pinned_parent_adapter_source_spec_sha256": pinned_parent_adapter_sha,
        "points_contract_sha256": candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256,
        "public_verifier_replay_performed": False,
        "row_count": len(parent_rows["rows"]),
        "rows_sha256": parent_projection["full_export_rows_sha256"],
        "schema": "factor-v2-common-eligible-parent-pinned-candidate/v1",
        "source_feature_projection_rows_sha256": parent_projection[
            "source_feature_projection_rows_sha256"
        ],
        "source_provenance_verified": False,
        **_false_scope(candidate_authority.SAFETY_FALSE_FIELDS),
    }
    parent_binding = {
        **parent_binding_unsigned,
        "receipt_sha256": _sha(parent_binding_unsigned),
    }
    parent_snapshot = {
        "derived_adapter": parent_derived_adapter,
        "parent_hash_binding_receipt": parent_binding,
        "schema": "factor-v3-development-factor-v2-parent-snapshot/v2",
        **parent_expectation,
        "candidate_identity_root_sha256": _sha(
            [
                {
                    "candidate_key": row["candidate_key"],
                    "signal_date": row["signal_date"],
                }
                for row in parent_rows["rows"]
            ]
        ),
        **parent_rows,
        "source_file_sha256": parent_source_file_sha,
        "validator": "validate_factor_v2_common_eligible_parent_hash_binding",
    }
    history_source_projection = {
        "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
        "collection_publication_manifest_sha256": _sha(
            "feature-history-collection-manifest"
        ),
        "embargo_consumed": False,
        "experiment_launch_eligible": False,
        "factor_materialization_eligible": False,
        "feature_history_only": True,
        "final_oos_consumed": False,
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "pit_store_database_sha256": _sha("feature-history-pit-store"),
        "schema_version": "audited-pit-factor-v3-feature-history-authority-receipt/v3",
        "session_count": 250,
        "sessions_sha256": _sha(prewindow),
        "snapshot_index_sha256": _sha("feature-history-snapshot-index"),
        "source_authority_root_sha256": _sha("feature-history-source-authority"),
        "verified": True,
    }
    history_source_receipt_sha = _sha(history_source_projection)
    history_summary = {
        **{
            key: history_source_projection[key]
            for key in (
                "authority_status",
                "feature_history_only",
                "schema_version",
                "session_count",
                "sessions_sha256",
                "source_authority_root_sha256",
                "verified",
            )
        },
        "receipt_sha256": history_source_receipt_sha,
    }
    daily_statistics = [
        _complete_daily_statistic(trade_date) for trade_date in sessions
    ]
    daily_source_projection = {
        "all_supported_segments_compared_before_scope_filter": True,
        "arbitrary_row_drops_permitted": False,
        "audited_daily_authority": {
            "artifact_root_sha256": _sha("audited-daily-artifact"),
            "daily_identity_root_sha256": _sha("daily-identity"),
            "schema": "audited-daily-authority-descriptor/v1",
        },
        "authority_scope": "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY",
        "authority_status": "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET",
        "embargo_consumed": False,
        "exact_set_verified": True,
        "factor_v3_development_materialization_input_eligible": True,
        "factor_v3_target_identity_root_sha256": _sha("target-identity"),
        "factor_v3_target_scope": {
            "excluded_segments": ["SSE_STAR", "BSE"],
            "included_segments": ["SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"],
            "policy_id": "factor-v3-mainboard-chinext-only/v1",
            "target_identity_row_count": 0,
        },
        "final_oos_consumed": False,
        "formal_factor_v3_materialization_performed": False,
        "normalized_daily_basic_row_authority_root_sha256": _sha(
            "normalized-daily-basic-rows"
        ),
        "per_date_statistics": daily_statistics,
        "per_date_statistics_sha256": _sha(daily_statistics),
        "producer_binding": {
            "root_sha256": _sha("daily-producer"),
            "schema": "factor-v3-daily-basic-producer/v1",
        },
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "publication_capability_sha256": _sha("daily-publication-capability"),
        "raw_source_rows_bound": True,
        "row_authority_status": "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY",
        "rows_published": False,
        "schema": "factor-v3-daily-basic-733-exact-set-receipt/v2",
        "security_code_transition_authority": {
            "artifact_root_sha256": _sha("transition-authority"),
            "schema": "security-code-transition-authority/v1",
        },
        "silent_row_drops_permitted": False,
        "source_binding_root_sha256": _sha("source-binding"),
        "source_missingness": {
            "extra_daily_basic_code_count": 0,
            "missing_authoritative_daily_code_count": 0,
            "status": "NONE_AFTER_AUTHORIZED_TRANSITION_FILTER",
            "unproven_source_missingness_count": 0,
        },
        "source_ts_code_exact_set_verified_after_transition_filter": True,
        "trade_date_count": 733,
        "trade_dates": sessions,
        "trade_dates_sha256": _sha(sessions),
        "transition_boundary_authority_root_sha256": _sha("transition-boundary"),
        "transition_boundary_count": 0,
        "transition_overlap_authority_root_sha256": _sha("transition-overlap"),
        "transition_resolved_identity_exact_set_verified": True,
    }
    daily_source_authority_root = _sha(daily_source_projection)
    daily_source_receipt = {
        **daily_source_projection,
        "authority_root_sha256": daily_source_authority_root,
    }
    daily_public_projection = (
        candidate_authority.build_factor_v3_public_source_receipt_projection(
            daily_source_receipt,
            kind="daily_basic_733_v2",
        )
    )
    daily_summary = {
        "authority_root_sha256": daily_source_authority_root,
        **{
            key: daily_source_projection[key]
            for key in (
                "authority_scope",
                "authority_status",
                "normalized_daily_basic_row_authority_root_sha256",
                "row_authority_status",
                "schema",
                "trade_date_count",
                "trade_dates_sha256",
            )
        },
    }
    snapshots: dict[str, dict[str, Any]] = {
        "calendar": calendar,
        "factor_v2_parent": parent_snapshot,
        "daily_basic": {
            "derived_adapter": daily_adapter,
            "rows": [],
            "rows_sha256": _sha([]),
            "schema": "factor-v3-development-daily-basic-snapshot/v2",
            "source_dates": source_dates,
            "source_dates_sha256": _sha(source_dates),
        },
        "daily_traded_cross_section": {
            "rows": [],
            "rows_sha256": _sha([]),
            "schema": "factor-v3-development-daily-cross-section-snapshot/v2",
            "source_dates_sha256": _sha(source_dates),
        },
        "listing_membership": {
            "rows": [],
            "rows_sha256": _sha([]),
            "schema": "factor-v3-development-listing-membership-snapshot/v2",
        },
        "suspensions": {
            "rows": [],
            "rows_sha256": _sha([]),
            "schema": "factor-v3-development-suspension-snapshot/v2",
        },
        "security_code_transitions": {
            "rows": [],
            "rows_sha256": _sha([]),
            "schema": "factor-v3-development-security-transition-snapshot/v2",
        },
        "upstream_board_ledger": board,
        "factor_v2_evaluation": {
            **{
                field: value
                for field, value in evaluation_source_binding.items()
                if field != "snapshot_schema"
            },
            "schema": evaluation_source_binding["snapshot_schema"],
        },
        "feature_history_receipt": {
            "schema": "factor-v3-development-feature-history-receipt-snapshot/v2",
            "projection_schema": "factor-v3-public-receipt-redacted-projection/v1",
            "redacted_field_count": 0,
            "source_receipt_sha256": history_source_receipt_sha,
            "source_receipt_projection": history_source_projection,
            "source_receipt_projection_sha256": _sha(history_source_projection),
        },
        "daily_basic_exact_set_receipt": {
            "derived_adapter": daily_adapter,
            "schema": "factor-v3-development-daily-basic-receipt-snapshot/v2",
            **daily_public_projection,
        },
    }
    snapshot_descriptors: dict[str, dict[str, Any]] = {}
    staging = root / "staging"
    for name, payload in snapshots.items():
        raw = _bytes(payload)
        snapshot_descriptors[name] = {
            "file_sha256": _sha(raw),
            "payload_root_sha256": _sha(payload),
            "relative_path": f"{name}.json",
            "row_count": len(payload["rows"]) if isinstance(payload.get("rows"), list) else None,
            "rows_sha256": payload.get("rows_sha256"),
            "schema": payload["schema"],
        }
        _write(staging / f"{name}.json", payload)
    unsigned_descriptor = {
        "authority_status": candidate_authority.AUTHORITY_STATUS,
        "calendar": {
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
        },
        "development_only": True,
        "factor_v2_branch": branch_receipt,
        "formal_materialization_eligible": False,
        "parent_source_authority_verified": False,
        "points_contract_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
        ),
        "producer_snapshot": producer_snapshot,
        "schema": candidate_authority.DESCRIPTOR_SCHEMA,
        "snapshots": snapshot_descriptors,
        "source_receipts": {
            "daily_basic": daily_summary,
            "feature_history": history_summary,
        },
        "source_spec_sha256": _sha("source-spec"),
        "source_authority_complete": False,
        **_false_scope(candidate_authority.PROVENANCE_FALSE_FIELDS),
        **_false_scope(candidate_authority.SAFETY_FALSE_FIELDS),
    }
    descriptor = {
        **unsigned_descriptor,
        "authority_root_sha256": _sha(unsigned_descriptor),
    }
    descriptor_relative = candidate_authority._descriptor_relative_path(
        descriptor["authority_root_sha256"]
    )
    descriptor_path = root.joinpath(*descriptor_relative.split("/"))
    descriptor_path.parent.mkdir(parents=True, exist_ok=True)
    for name in snapshots:
        (staging / f"{name}.json").replace(descriptor_path.parent / f"{name}.json")
    staging.rmdir()
    descriptor_sha = _write(descriptor_path, descriptor)
    manifest = {
        "authority_root_sha256": descriptor["authority_root_sha256"],
        "descriptor_relative_path": descriptor_relative,
        "descriptor_sha256": descriptor_sha,
        "development_only": True,
        "parent_source_authority_verified": False,
        "producer_snapshot_root_sha256": descriptor["producer_snapshot"]["root_sha256"],
        "schema": candidate_authority.PUBLICATION_MANIFEST_SCHEMA,
        "source_spec_sha256": descriptor["source_spec_sha256"],
        "source_authority_complete": False,
        **_false_scope(candidate_authority.PROVENANCE_FALSE_FIELDS),
        **_false_scope(candidate_authority.SAFETY_FALSE_FIELDS),
    }
    manifest_sha = _sha(manifest)
    manifest_relative = candidate_authority._publication_relative_path(manifest_sha)
    manifest_path = root.joinpath(*manifest_relative.split("/"))
    assert _write(manifest_path, manifest) == manifest_sha
    source_authority_root = root.parent / "source-authorities"
    history_issuance = {
        "authority_manifest_sha256": history_source_projection[
            "collection_publication_manifest_sha256"
        ],
        "publication_capability_sha256": _sha("history-publication-capability"),
        "publication_schema": "audited-pit-factor-v3-feature-history-collection-publication/v1",
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "schema": "audited-pit-factor-v3-feature-history-publication-issuance/v1",
    }
    history_issuance_sha = _sha(history_issuance)
    history_issuance_path = (
        source_authority_root
        / "feature_history_collection_publication_receipts"
        / "sha256"
        / history_issuance_sha[:2]
        / f"{history_issuance_sha}.json"
    )
    assert _write(history_issuance_path, history_issuance) == history_issuance_sha
    feature_run_root = (root.parent / "feature-history-run").resolve()
    feature_run_spec_path = (root.parent / "feature-history-run-spec.json").resolve()
    authority_manifest_sha = history_source_projection[
        "collection_publication_manifest_sha256"
    ]
    history_attestation = {
        "attestor_producer": frozen_history._attestor_producer_binding(),
        "feature_history": {
            "authority_manifest_relative_path": (
                "factor_v3_feature_history_authority_manifests/sha256/"
                f"{authority_manifest_sha[:2]}/{authority_manifest_sha}.json"
            ),
            "authority_manifest_sha256": authority_manifest_sha,
            "feature_run_root": str(feature_run_root),
            "feature_run_spec_file_sha256": _sha("feature-run-spec-file"),
            "feature_run_spec_path": str(feature_run_spec_path),
            "feature_run_spec_sha256": _sha("feature-run-spec-logical"),
            "pit_store_database_sha256": history_source_projection[
                "pit_store_database_sha256"
            ],
            "publication_capability_sha256": history_issuance[
                "publication_capability_sha256"
            ],
            "publication_issuance_relative_path": "/".join(
                history_issuance_path.parts[-4:]
            ),
            "publication_issuance_sha256": history_issuance_sha,
            "receipt_sha256": history_source_receipt_sha,
            "session_count": 250,
            "sessions_sha256": history_source_projection["sessions_sha256"],
            "snapshot_index_sha256": history_source_projection[
                "snapshot_index_sha256"
            ],
            "source_authority_root_sha256": history_source_projection[
                "source_authority_root_sha256"
            ],
        },
        "frozen_source": {
            "commit": "a" * 40,
            "checkout_policy": deepcopy(
                frozen_history.FROZEN_SOURCE_CHECKOUT_POLICY
            ),
            "physical_files": [],
            "physical_files_root_sha256": _sha([]),
            "producer_binding": {},
            "root": str(root.parent.resolve()),
        },
        "schema": frozen_history.ATTESTATION_SCHEMA,
        "verified": True,
    }
    history_attestation_sha = _sha(history_attestation)
    history_attestation_path = (
        source_authority_root
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / history_attestation_sha[:2]
        / f"{history_attestation_sha}.json"
    )
    assert (
        _write(history_attestation_path, history_attestation)
        == history_attestation_sha
    )
    assert (
        frozen_history._validated_attestation(
            attestation_path=history_attestation_path.resolve(),
            expected_attestation_sha256=history_attestation_sha,
        )
        == history_attestation
    )
    daily_file_sha = _sha(daily_source_receipt)
    daily_receipt_path = (
        source_authority_root
        / "factor_v3_daily_basic_733_receipts"
        / "sha256"
        / daily_file_sha[:2]
        / f"{daily_file_sha}.json"
    )
    assert _write(daily_receipt_path, daily_source_receipt) == daily_file_sha
    return (
        manifest_path,
        manifest_sha,
        parent_projection,
        evaluation_projection,
        evaluation_source_binding,
        {
            "all_market_sessions_sha256": _sha(sessions),
            "prewindow_sessions_sha256": _sha(prewindow),
            "development_sessions_sha256": _sha(development),
            "source_dates_sha256": _sha(source_dates),
        },
        _sha(per_date),
        {
            "descriptor": descriptor_path,
            "manifest": manifest_path,
            "snapshot_root": descriptor_path.parent,
            "feature_history_collection_issuance": history_issuance_path,
            "feature_history_collection_issuance_sha256": history_issuance_sha,
            "feature_history_frozen_attestation": history_attestation_path,
            "feature_history_frozen_attestation_sha256": history_attestation_sha,
            "daily_basic_receipt": daily_receipt_path,
            "daily_basic_receipt_sha256": daily_file_sha,
        },
    )


def _self_hashed(payload: dict[str, Any], field: str) -> dict[str, Any]:
    unsigned = deepcopy(payload)
    unsigned.pop(field, None)
    return {**unsigned, field: _sha(unsigned)}


def _real_audited_daily_authority(
    sessions: list[str],
    *,
    label: str,
) -> legacy_daily.AuditedDailyAuthority:
    partitions = tuple(
        replace(
            partition,
            vintage=f"{partition.trade_date}T16:00:00+08:00",
        )
        for partition in candidate_test_support._partitions(sessions)
    )
    partition_refs = [
        {
            "generation_id": item.generation_id,
            "lineage_sha256": item.generation_lineage_sha256,
            "manifest_sha256": item.generation_manifest_sha256,
            "trade_date": item.trade_date,
            "vintage": item.vintage,
        }
        for item in partitions
    ]
    daily_rows = [
        {"trade_date": item.trade_date, "ts_codes": list(item.ts_codes)}
        for item in partitions
    ]
    return legacy_daily.AuditedDailyAuthority(
        manifest_file_sha256=_sha([label, "manifest-file"]),
        manifest_sha256=_sha([label, "manifest"]),
        bundle_sha256=_sha([label, "bundle"]),
        artifact_root_sha256=_sha([label, "artifact"]),
        sqlite_sha256=_sha([label, "sqlite"]),
        coverage_audit_sha256=_sha([label, "coverage"]),
        temporal_contract_sha256=_sha([label, "temporal"]),
        temporal_role="development",
        daily_table_rows=len(partitions) * len(activation.UPSTREAM_SOURCE_SEGMENTS),
        daily_table_sha256=_sha(daily_rows),
        market_generation_count=len(partitions),
        market_generation_root_sha256=_sha(partition_refs),
        partitions=partitions,
    )


def _real_feature_history_receipt(prewindow: list[str]) -> dict[str, Any]:
    unsigned = {
        "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
        "collection_plan_sha256": _sha("real-e2e-history-plan"),
        "collection_publication_manifest_sha256": _sha(
            "real-e2e-history-manifest"
        ),
        "daily_generation_session_count": 250,
        "embargo_consumed": False,
        "exact_nonempty_bak_basic_session_count": 250,
        "experiment_launch_eligible": False,
        "factor_materialization_eligible": False,
        "factor_v3_feature_history_authority_contract_sha256": _sha(
            "real-e2e-history-contract"
        ),
        "factor_v3_points_contract_sha256": (
            candidate_authority.points.FACTOR_V3_POINTS_CONTRACT_SHA256
        ),
        "feature_history_only": True,
        "final_oos_consumed": False,
        "pit_store_database_bytes": 1,
        "pit_store_database_sha256": _sha("real-e2e-history-database"),
        "pit_store_raw_artifact_set_sha256": _sha(
            "real-e2e-history-raw-artifacts"
        ),
        "pit_store_receipt_manifest_sha256": _sha(
            "real-e2e-history-receipt-manifest"
        ),
        "producer_code_root_sha256": _sha("real-e2e-history-producer"),
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "schema_version": (
            "audited-pit-factor-v3-feature-history-authority-receipt/v3"
        ),
        "security_code_transition_contract_sha256": _sha(
            "real-e2e-transition-contract"
        ),
        "session_authority_refs_sha256": _sha(
            ["real-e2e-history-session-ref", *prewindow]
        ),
        "session_count": 250,
        "sessions_sha256": _sha(prewindow),
        "snapshot_index_sha256": _sha("real-e2e-history-snapshot-index"),
        "source_authority_root_sha256": _sha("real-e2e-history-source"),
        "suspend_d_authority_session_count": 250,
        "upstream_beijing_preserved_session_count": 250,
        "upstream_scope_root_sha256": _sha("real-e2e-history-upstream-scope"),
        "upstream_star_preserved_session_count": 250,
        "verified": True,
    }
    return {**unsigned, "receipt_sha256": _sha(unsigned)}


def _write_real_feature_history_fixture(
    root: Path,
    *,
    prewindow: list[str],
) -> dict[str, Any]:
    history_receipt = _real_feature_history_receipt(prewindow)
    authority_root = (root / "source-authorities").resolve()
    feature_run_root = (root / "feature-history-run").resolve()
    feature_run_root.mkdir(parents=True)
    feature_run_spec_path = (root / "feature-history-run-spec.json").resolve()
    feature_run_spec_file_sha256 = _write(
        feature_run_spec_path,
        {"schema": "disposable-real-chain-feature-run-spec/v1"},
    )
    collection_publication_root = (
        root / "feature-history-collection-publication"
    ).resolve()
    collection_publication_root.mkdir()
    frozen_source_root = (root / "feature-history-frozen-source").resolve()
    frozen_source_root.mkdir()
    issuance = {
        "authority_manifest_sha256": history_receipt[
            "collection_publication_manifest_sha256"
        ],
        "publication_capability_sha256": _sha(
            "real-e2e-history-publication-capability"
        ),
        "publication_schema": (
            "audited-pit-factor-v3-feature-history-collection-publication/v1"
        ),
        "publication_status": "DURABLE_POSTVERIFIED_AND_RETURNED",
        "schema": (
            "audited-pit-factor-v3-feature-history-publication-issuance/v1"
        ),
    }
    issuance_sha256 = _sha(issuance)
    issuance_path = (
        authority_root
        / "feature_history_collection_publication_receipts"
        / "sha256"
        / issuance_sha256[:2]
        / f"{issuance_sha256}.json"
    )
    assert _write(issuance_path, issuance) == issuance_sha256
    manifest_sha256 = history_receipt["collection_publication_manifest_sha256"]
    attestation = {
        "attestor_producer": frozen_history._attestor_producer_binding(),
        "feature_history": {
            "authority_manifest_relative_path": (
                "factor_v3_feature_history_authority_manifests/sha256/"
                f"{manifest_sha256[:2]}/{manifest_sha256}.json"
            ),
            "authority_manifest_sha256": manifest_sha256,
            "feature_run_root": str(feature_run_root),
            "feature_run_spec_file_sha256": feature_run_spec_file_sha256,
            "feature_run_spec_path": str(feature_run_spec_path),
            "feature_run_spec_sha256": _sha("real-e2e-history-logical-run-spec"),
            "pit_store_database_sha256": history_receipt[
                "pit_store_database_sha256"
            ],
            "publication_capability_sha256": issuance[
                "publication_capability_sha256"
            ],
            "publication_issuance_relative_path": "/".join(
                issuance_path.parts[-4:]
            ),
            "publication_issuance_sha256": issuance_sha256,
            "receipt_sha256": history_receipt["receipt_sha256"],
            "session_count": 250,
            "sessions_sha256": history_receipt["sessions_sha256"],
            "snapshot_index_sha256": history_receipt["snapshot_index_sha256"],
            "source_authority_root_sha256": history_receipt[
                "source_authority_root_sha256"
            ],
        },
        "frozen_source": {
            "commit": "a" * 40,
            "checkout_policy": deepcopy(
                frozen_history.FROZEN_SOURCE_CHECKOUT_POLICY
            ),
            "physical_files": [],
            "physical_files_root_sha256": _sha([]),
            "producer_binding": {},
            "root": str(frozen_source_root),
        },
        "schema": frozen_history.ATTESTATION_SCHEMA,
        "verified": True,
    }
    attestation_sha256 = _sha(attestation)
    attestation_path = (
        authority_root
        / "factor_v3_feature_history_frozen_source_attestations"
        / "sha256"
        / attestation_sha256[:2]
        / f"{attestation_sha256}.json"
    )
    assert _write(attestation_path, attestation) == attestation_sha256
    assert (
        frozen_history._validated_attestation(
            attestation_path=attestation_path.resolve(),
            expected_attestation_sha256=attestation_sha256,
        )
        == attestation
    )
    return {
        "attestation_path": attestation_path,
        "attestation_sha256": attestation_sha256,
        "collection_publication_root": collection_publication_root,
        "feature_run_root": feature_run_root,
        "feature_run_spec_path": feature_run_spec_path,
        "frozen_source_root": frozen_source_root,
        "history_receipt": history_receipt,
        "issuance_path": issuance_path,
        "issuance_sha256": issuance_sha256,
    }


def _real_daily_candidate_chain_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    prewindow, development = daily_test_support._sessions()
    sessions = [*prewindow, *development]
    parent_path, parent_sha256, _parent_rows = candidate_test_support._parent_snapshot(
        tmp_path,
        monkeypatch,
        development,
    )
    points_contract = candidate_authority.points.FACTOR_V3_POINTS_CONTRACT
    monkeypatch.setattr(
        candidate_authority.points,
        "_FACTOR_V3_POINTS_PARENT_EXPECTATION",
        deepcopy(points_contract["preregistered_parent_expectation"]),
    )
    monkeypatch.setattr(
        candidate_authority.points,
        "FACTOR_V3_POINTS_ARM_STRATEGY_SHA256",
        {
            arm: candidate_authority.points.canonical_sha256(
                candidate_authority.points._arm_strategy_payload(arm)
            )
            for arm in candidate_authority.points.FACTOR_V3_POINTS_ARMS
        },
    )
    candidate_authority.points.assert_frozen_factor_v3_points_contract()
    history = _write_real_feature_history_fixture(
        tmp_path,
        prewindow=prewindow,
    )
    prewindow_authority = _real_audited_daily_authority(
        prewindow,
        label="prewindow",
    )
    development_authority = _real_audited_daily_authority(
        development,
        label="development",
    )
    monkeypatch.setattr(
        daily_authority,
        "_load_feature_history_prewindow_authority",
        lambda **_kwargs: prewindow_authority,
    )
    monkeypatch.setattr(
        daily_authority,
        "_load_development_authority",
        lambda **_kwargs: development_authority,
    )
    monkeypatch.setattr(
        legacy_daily,
        "_load_daily_basic_partition",
        lambda *, collection_ref, **_kwargs: candidate_test_support._daily_partition(
            dict(collection_ref)
        ),
    )
    transition = daily_test_support._transition_authority()
    transition_contract_sha256 = transition.contract_sha256
    monkeypatch.setattr(
        daily_authority,
        "_load_transition_authority",
        lambda **_kwargs: transition,
    )
    points_output_root = (tmp_path / "points").resolve()
    daily_output_root = (tmp_path / "daily-authority").resolve()
    transition_root = (tmp_path / "transition").resolve()
    development_database = (tmp_path / "development.sqlite3").resolve()
    for directory in (points_output_root, daily_output_root, transition_root):
        directory.mkdir()
    development_database.write_bytes(b"disposable-development-database")
    refs = []
    for trade_date in sessions:
        digest = _sha(["real-e2e-collection", trade_date])
        refs.append(
            {
                "collection_set_relative_path": (
                    f"daily_basic_collection_sets/sha256/{digest[:2]}/"
                    f"{digest}.json"
                ),
                "collection_set_sha256": digest,
                "trade_date": trade_date,
            }
        )
    daily_kwargs = {
        "feature_history_run_spec_path": history["feature_run_spec_path"],
        "feature_history_run_root": history["feature_run_root"],
        "feature_history_frozen_source_attestation_path": history[
            "attestation_path"
        ],
        "expected_feature_history_frozen_source_attestation_sha256": history[
            "attestation_sha256"
        ],
        "feature_history_frozen_source_root": history["frozen_source_root"],
        "expected_feature_history_frozen_source_commit": "a" * 40,
        "audited_development_universe_sqlite_path": development_database,
        "expected_development_coverage_audit_sha256": (
            development_authority.coverage_audit_sha256
        ),
        "expected_development_artifact_root_sha256": (
            development_authority.artifact_root_sha256
        ),
        "expected_development_temporal_contract_sha256": (
            development_authority.temporal_contract_sha256
        ),
        "expected_development_temporal_role": "development_4",
        "points_output_root": points_output_root,
        "collection_set_refs": refs,
        "security_code_transition_evidence_root": transition_root,
        "expected_security_code_transition_contract_sha256": (
            transition_contract_sha256
        ),
        "output_root": daily_output_root,
    }
    _source, source_identity = daily_authority._load_733_authority(**daily_kwargs)
    daily_kwargs["expected_source_authority_root_sha256"] = source_identity[
        "root_sha256"
    ]

    history_receipt = history["history_receipt"]
    monkeypatch.setattr(
        candidate_authority.history_runner,
        "load_factor_v3_feature_history_run_spec",
        lambda _path: {
            "collection_plan": {},
            "development_session_refs": [],
            "temporal_partition_contract": {},
            "trade_cal_output_root": str(tmp_path.resolve()),
            "trade_cal_publication": {},
        },
    )
    monkeypatch.setattr(
        candidate_authority.history_authority,
        "verify_factor_v3_feature_history_collection_plan",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        candidate_authority.frozen_attestation,
        "verify_factor_v3_feature_history_frozen_source_attestation",
        lambda **_kwargs: {
            "authority_binding": {
                "collection_publication_output_root": str(
                    history["collection_publication_root"]
                ),
                "manifest_relative_path": "manifest.json",
                "manifest_sha256": history_receipt[
                    "collection_publication_manifest_sha256"
                ],
                "receipt": deepcopy(history_receipt),
            },
            "receipt_sha256": history_receipt["receipt_sha256"],
            "session_count": 250,
            "sessions_sha256": history_receipt["sessions_sha256"],
            "verified": True,
        },
    )

    decision_receipt = branch_test_support._receipt(
        {
            "v2_control": "GREEN",
            "overnight_20": "RED",
            "intraday_20": "RED",
        }
    )
    decision_path, decision_file_sha256 = branch_test_support._write_receipt(
        tmp_path,
        decision_receipt,
    )
    membership = [
        {
            "list_date": "2020-01-01",
            "trade_date": trade_date,
            "ts_code": code,
        }
        for trade_date in sessions
        for code in ("000001.SZ", "300001.SZ", "600001.SH")
    ]
    monkeypatch.setattr(
        candidate_authority,
        "_read_membership_and_suspension_rows",
        lambda **_kwargs: {"membership": membership, "suspensions": []},
    )

    pinned_root = (tmp_path / "factor-v2-frozen-source").resolve()
    pinned_root.mkdir()
    pinned_files = {
        "parent_materialization_manifest_path": (
            tmp_path / "factor-v2-parent-manifest.json"
        ).resolve(),
        "overlay_manifest_path": (
            tmp_path / "factor-v2-overlay-manifest.json"
        ).resolve(),
        "suspension_metadata_path": (
            tmp_path / "factor-v2-suspension.sqlite3"
        ).resolve(),
    }
    for path in pinned_files.values():
        path.write_bytes(b"disposable-fixture")
    return {
        "candidate_output_root": (tmp_path / "candidate-output").resolve(),
        "daily_kwargs": daily_kwargs,
        "decision_file_sha256": decision_file_sha256,
        "decision_path": decision_path,
        "development": development,
        "development_database": development_database,
        "evaluation_projection": {
            "schema": activation.FACTOR_V2_EVALUATION_PROJECTION_SCHEMA,
            "terminal_decision_descriptor_sha256": _sha(
                "real-e2e-terminal-decision"
            ),
            "evaluator_descriptor_sha256": _sha("real-e2e-evaluator"),
            "cost_slippage_execution_descriptor_sha256": _sha(
                "real-e2e-cost-slippage"
            ),
        },
        "history": history,
        "parent_path": parent_path.resolve(),
        "parent_sha256": parent_sha256,
        "pinned_files": pinned_files,
        "pinned_root": pinned_root,
        "prewindow": prewindow,
        "sessions": sessions,
        "source_authority_root_sha256": source_identity["root_sha256"],
    }


def _write_real_candidate_source_spec(
    chain: dict[str, Any],
    daily_publication: dict[str, Any],
) -> tuple[Path, str]:
    daily_kwargs = chain["daily_kwargs"]
    history = chain["history"]
    spec = {
        "schema": "factor-v3-development-input-source-spec/v2",
        "feature_history": {
            "run_spec_path": str(history["feature_run_spec_path"]),
            "run_root": str(history["feature_run_root"]),
            "frozen_source_attestation_path": str(history["attestation_path"]),
            "expected_frozen_source_attestation_sha256": history[
                "attestation_sha256"
            ],
            "frozen_source_root": str(history["frozen_source_root"]),
            "expected_frozen_source_commit": "a" * 40,
        },
        "daily_basic": {
            "audited_development_universe_sqlite_path": str(
                chain["development_database"]
            ),
            "expected_development_coverage_audit_sha256": daily_kwargs[
                "expected_development_coverage_audit_sha256"
            ],
            "expected_development_artifact_root_sha256": daily_kwargs[
                "expected_development_artifact_root_sha256"
            ],
            "expected_development_temporal_contract_sha256": daily_kwargs[
                "expected_development_temporal_contract_sha256"
            ],
            "expected_development_temporal_role": "development_4",
            "expected_source_authority_root_sha256": chain[
                "source_authority_root_sha256"
            ],
            "points_output_root": str(daily_kwargs["points_output_root"]),
            "collection_set_refs": deepcopy(daily_kwargs["collection_set_refs"]),
            "security_code_transition_evidence_root": str(
                daily_kwargs["security_code_transition_evidence_root"]
            ),
            "expected_security_code_transition_contract_sha256": daily_kwargs[
                "expected_security_code_transition_contract_sha256"
            ],
            "authority_output_root": str(daily_kwargs["output_root"]),
            "publication": deepcopy(daily_publication),
        },
        "factor_v2": {
            "decision_receipt_path": str(chain["decision_path"]),
            "expected_decision_receipt_raw_file_sha256": chain[
                "decision_file_sha256"
            ],
            "parent_snapshot_path": str(chain["parent_path"]),
            "expected_parent_snapshot_file_sha256": chain["parent_sha256"],
            "parent_payload_fields": ["features"],
            "pinned_parent_adapter": {
                "schema": "factor-v3-pinned-factor-v2-parent-adapter-source/v1",
                "frozen_source_root": str(chain["pinned_root"]),
                "expected_frozen_source_commit": (
                    candidate_authority.FACTOR_V2_FROZEN_SOURCE_COMMIT
                ),
                "expected_frozen_source_tree_oid": (
                    candidate_authority.FACTOR_V2_FROZEN_SOURCE_TREE_OID
                ),
                "expected_frozen_source_blob_sha256": dict(
                    candidate_authority.FACTOR_V2_FROZEN_SOURCE_BLOB_SHA256
                ),
                **{
                    key: str(value)
                    for key, value in chain["pinned_files"].items()
                },
                "subprocess_contract": deepcopy(
                    candidate_authority.FACTOR_V2_PARENT_SUBPROCESS_CONTRACT
                ),
                "public_verifier_replay_performed": False,
            },
        },
    }
    path = (chain["candidate_output_root"].parent / "source-spec.json").resolve()
    return path, _write(path, spec)


def _real_candidate_activation_bundle(
    chain: dict[str, Any],
    *,
    daily_publication: dict[str, Any],
    candidate_publication: dict[str, Any],
) -> tuple[Any, ...]:
    candidate_root = Path(chain["candidate_output_root"])
    manifest_path = candidate_root.joinpath(
        *candidate_publication["publication_relative_path"].split("/")
    )
    descriptor_path = candidate_root.joinpath(
        *candidate_publication["descriptor_relative_path"].split("/")
    )
    snapshot_root = descriptor_path.parent
    parent = _read(snapshot_root / "factor_v2_parent.json")
    rows = parent["rows"]
    parent_projection = {
        "schema": activation.PARENT_PROJECTION_SCHEMA,
        "contract": deepcopy(activation.PARENT_PROJECTION_CONTRACT),
        "candidate_keys_sha256": _sha(
            sorted(row["candidate_key"] for row in rows)
        ),
        "source_feature_projection_rows_sha256": _sha(
            [
                {
                    "candidate_key": row["candidate_key"],
                    "signal_date": row["signal_date"],
                    "features": row["features"],
                }
                for row in rows
            ]
        ),
        "full_export_rows_sha256": _sha(rows),
    }
    evaluation = _read(snapshot_root / "factor_v2_evaluation.json")
    evaluation_source_binding = {
        "branch_receipt_sha256": evaluation["branch_receipt_sha256"],
        "decision_receipt_sha256": evaluation["decision_receipt_sha256"],
        "decision_receipt_raw_file_sha256": evaluation[
            "decision_receipt_raw_file_sha256"
        ],
        "evaluation_artifact_sha256": evaluation["evaluation_artifact_sha256"],
        "selected_branch": evaluation["selected_branch"],
        "snapshot_schema": evaluation["schema"],
    }
    calendar = _read(snapshot_root / "calendar.json")
    board = _read(snapshot_root / "upstream_board_ledger.json")
    daily_receipt_path = Path(chain["daily_kwargs"]["output_root"]).joinpath(
        *daily_publication["receipt_relative_path"].split("/")
    )
    history = chain["history"]
    candidate_paths = {
        "descriptor": descriptor_path,
        "manifest": manifest_path,
        "output_root": candidate_root,
        "snapshot_root": snapshot_root,
        "feature_history_collection_issuance": history["issuance_path"],
        "feature_history_collection_issuance_sha256": history["issuance_sha256"],
        "feature_history_frozen_attestation": history["attestation_path"],
        "feature_history_frozen_attestation_sha256": history[
            "attestation_sha256"
        ],
        "daily_basic_receipt": daily_receipt_path,
        "daily_basic_receipt_sha256": daily_publication["receipt_sha256"],
    }
    return (
        manifest_path,
        candidate_publication["publication_sha256"],
        parent_projection,
        chain["evaluation_projection"],
        evaluation_source_binding,
        {
            "all_market_sessions_sha256": calendar[
                "all_market_sessions_sha256"
            ],
            "prewindow_sessions_sha256": calendar[
                "prewindow_sessions_sha256"
            ],
            "development_sessions_sha256": calendar[
                "development_sessions_sha256"
            ],
            "source_dates_sha256": calendar["source_dates_sha256"],
        },
        board["per_date_board_ledger_root_sha256"],
        candidate_paths,
    )


def _fixture(
    tmp_path: Path,
    *,
    candidate_bundle: tuple[Any, ...] | None = None,
) -> dict[str, Any]:
    candidate_root = tmp_path / "candidate"
    (
        candidate_publication_path,
        candidate_publication_sha,
        parent_projection,
        evaluation_projection,
        evaluation_source_binding,
        calendar_roots,
        upstream_board_ledger_root,
        candidate_paths,
    ) = (
        _candidate_publication(candidate_root)
        if candidate_bundle is None
        else candidate_bundle
    )
    candidate_root = Path(candidate_paths.get("output_root", candidate_root))
    semantic_root = _sha("parent-semantic-input")
    attempt_key = _sha(
        {
            "schema": activation.PARENT_SOURCE_ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
        }
    )
    global_identity = _sha(
        {
            "attempt_key_sha256": attempt_key,
            "schema": activation.PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA,
        }
    )
    ledger_root = (tmp_path / "machine-global-ledger").resolve()
    claim_root = ledger_root / "attempts" / "sha256" / attempt_key[:2] / attempt_key
    run_claim_path = claim_root / "run.claim.json"
    run_receipt_path = claim_root / "run.receipt.json"
    verify_claim_path = claim_root / "verify.claim.json"
    terminal_epoch_path = claim_root / "terminal.receipt.json"
    run_spec_sha256 = _sha("parent-run-spec")
    run_claim = {
        "action": activation.PARENT_SOURCE_ROOT_ACTION_RUN,
        "attempt_key_sha256": attempt_key,
        "global_attempt_identity_sha256": global_identity,
        "run_spec_sha256": run_spec_sha256,
        "schema": activation.PARENT_SOURCE_RUN_CLAIM_SCHEMA,
        "state": activation.PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED,
    }
    run_claim_sha256 = _write(run_claim_path, run_claim)
    run_receipt = {
        "attempt_key_sha256": attempt_key,
        "global_attempt_identity_sha256": global_identity,
        "run_claim_sha256": run_claim_sha256,
        "run_spec_sha256": run_spec_sha256,
        "schema": activation.PARENT_SOURCE_RUN_RECEIPT_SCHEMA,
        "state": activation.PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED,
    }
    run_receipt_sha256 = _write(run_receipt_path, run_receipt)
    verify_claim = {
        "action": activation.PARENT_SOURCE_ROOT_ACTION_VERIFY,
        "attempt_key_sha256": attempt_key,
        "global_attempt_identity_sha256": global_identity,
        "run_receipt_sha256": run_receipt_sha256,
        "run_spec_sha256": run_spec_sha256,
        "schema": activation.PARENT_SOURCE_VERIFY_CLAIM_SCHEMA,
        "state": activation.PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED,
    }
    verify_claim_sha256 = _write(verify_claim_path, verify_claim)
    terminal_epoch = {
        "attempt_key_sha256": attempt_key,
        "global_attempt_identity_sha256": global_identity,
        "run_receipt_sha256": run_receipt_sha256,
        "run_spec_sha256": run_spec_sha256,
        "schema": activation.PARENT_SOURCE_TERMINAL_RECEIPT_SCHEMA,
        "state": activation.PARENT_SOURCE_ROOT_STATE_TERMINAL,
        "verify_claim_sha256": verify_claim_sha256,
    }
    terminal_epoch_file_sha = _write(terminal_epoch_path, terminal_epoch)
    parent_receipt = _self_hashed(
        {
            "attempt_key_sha256": attempt_key,
            "authority_status": "DISPOSABLE_TEST_PARENT_SOURCE_CONTRACT_ONLY",
            "calendar_projection": {
                **activation.EXACT_CALENDAR_COUNTS,
                **calendar_roots,
            },
            "development_only": True,
            "formal_materialization_eligible": False,
            "global_attempt_identity_sha256": global_identity,
            "global_attempt_ledger_root": str(ledger_root),
            "global_run_claim_path": str(run_claim_path),
            "global_run_receipt_path": str(run_receipt_path),
            "global_terminal_receipt_path": str(terminal_epoch_path),
            "global_verify_claim_path": str(verify_claim_path),
            "independent_public_replay_performed": False,
            "machine_global_root_lease_verified": False,
            "native_lease_identity_sha256": _sha("native-root-lease"),
            "native_lease_policy_version": (
                activation.PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION
            ),
            "observed_root_state": activation.PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED,
            "parent_projection": parent_projection,
            "parent_source_authority_verified": False,
            "points_contract_common_eligible_projection": {
                "common_eligible_candidate_keys_sha256": parent_projection[
                    "candidate_keys_sha256"
                ],
                "common_eligible_source_feature_rows_sha256": parent_projection[
                    "source_feature_projection_rows_sha256"
                ],
            },
            "requested_action": activation.PARENT_SOURCE_ROOT_ACTION_VERIFY,
            "root_epoch_terminal_verified": False,
            "run_claim_sha256": run_claim_sha256,
            "run_receipt_sha256": run_receipt_sha256,
            "run_spec_sha256": run_spec_sha256,
            "schema": activation.DISPOSABLE_PARENT_SOURCE_RECEIPT_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
            "single_attempt_verified": False,
            "source_authority_complete": False,
            "source_authority_verified": False,
            "terminal_receipt_file_sha256": terminal_epoch_file_sha,
            "terminal_root_state": activation.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            "approved_transition": activation.PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY,
            "upstream_board_projection": {
                "downstream_scope_filter": "mainboard_chinext_candidate_join_only",
                "preserved_before_target_scope_filter": True,
                "source_date_count": 732,
                "source_segments": list(activation.UPSTREAM_SOURCE_SEGMENTS),
                "upstream_board_ledger_root_sha256": upstream_board_ledger_root,
            },
            "contract_binding_validated": True,
            "test_fixture_only": True,
            "verified": False,
            "verify_claim_sha256": verify_claim_sha256,
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    parent_receipt_file_sha = _sha(parent_receipt)
    parent_receipt_path = (
        tmp_path
        / "authority"
        / "disposable-parent-source-contract-receipts"
        / "sha256"
        / parent_receipt_file_sha[:2]
        / f"{parent_receipt_file_sha}.json"
    )
    parent_receipt_file_sha = _write(parent_receipt_path, parent_receipt)
    evaluation_receipt = _self_hashed(
        {
            "authority_status": "DISPOSABLE_TEST_FACTOR_V2_EVALUATION_CONTRACT_ONLY",
            "contract_binding_validated": True,
            "development_only": True,
            "evaluation_projection": evaluation_projection,
            "candidate_evaluation_binding": evaluation_source_binding,
            "formal_materialization_eligible": False,
            "independent_public_replay_performed": False,
            "publisher_terminal_chain_verified": False,
            "schema": activation.DISPOSABLE_EVALUATION_RECEIPT_SCHEMA,
            "source_authority_complete": False,
            "test_fixture_only": True,
            "verified": False,
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    evaluation_receipt_file_sha = _sha(evaluation_receipt)
    evaluation_receipt_path = (
        tmp_path
        / "authority"
        / "disposable-evaluation-contract-receipts"
        / "sha256"
        / evaluation_receipt_file_sha[:2]
        / f"{evaluation_receipt_file_sha}.json"
    )
    evaluation_receipt_file_sha = _write(
        evaluation_receipt_path,
        evaluation_receipt,
    )
    kwargs = {
        "candidate_output_root": candidate_root.resolve(),
        "candidate_publication_path": candidate_publication_path.resolve(),
        "expected_candidate_publication_sha256": candidate_publication_sha,
        "parent_source_authority_receipt_path": parent_receipt_path.resolve(),
        "expected_parent_source_authority_receipt_sha256": parent_receipt_file_sha,
        "parent_source_terminal_epoch_receipt_path": terminal_epoch_path.resolve(),
        "expected_parent_source_terminal_epoch_receipt_sha256": terminal_epoch_file_sha,
        "factor_v2_evaluation_authority_receipt_path": evaluation_receipt_path.resolve(),
        "expected_factor_v2_evaluation_authority_receipt_sha256": (
            evaluation_receipt_file_sha
        ),
        "feature_history_collection_issuance_path": candidate_paths[
            "feature_history_collection_issuance"
        ].resolve(),
        "expected_feature_history_collection_issuance_sha256": candidate_paths[
            "feature_history_collection_issuance_sha256"
        ],
        "feature_history_frozen_attestation_path": candidate_paths[
            "feature_history_frozen_attestation"
        ].resolve(),
        "expected_feature_history_frozen_attestation_sha256": candidate_paths[
            "feature_history_frozen_attestation_sha256"
        ],
        "daily_basic_authority_receipt_path": candidate_paths[
            "daily_basic_receipt"
        ].resolve(),
        "expected_daily_basic_authority_receipt_sha256": candidate_paths[
            "daily_basic_receipt_sha256"
        ],
        "output_root": (tmp_path / "activation-output").resolve(),
    }
    return {
        "kwargs": kwargs,
        "parent_projection": parent_projection,
        "evaluation_projection": evaluation_projection,
        "evaluation_source_binding": evaluation_source_binding,
        "calendar_roots": calendar_roots,
        "upstream_board_ledger_root_sha256": upstream_board_ledger_root,
        "paths": {
            "parent": parent_receipt_path,
            "epoch": terminal_epoch_path,
            "epoch_chain": {
                "run_claim": run_claim_path,
                "run_receipt": run_receipt_path,
                "verify_claim": verify_claim_path,
                "terminal_receipt": terminal_epoch_path,
            },
            "evaluation": evaluation_receipt_path,
            "candidate_descriptor": candidate_paths["descriptor"],
            "candidate_manifest": candidate_paths["manifest"],
            "candidate_snapshot_root": candidate_paths["snapshot_root"],
            "feature_history_collection_issuance": candidate_paths[
                "feature_history_collection_issuance"
            ],
            "feature_history_frozen_attestation": candidate_paths[
                "feature_history_frozen_attestation"
            ],
            "daily_basic_receipt": candidate_paths["daily_basic_receipt"],
        },
    }


def _rewrite_receipt(
    fixture: dict[str, Any],
    artifact: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    path = fixture["paths"][artifact]
    payload = _read(path)
    mutate(payload)
    if artifact == "epoch":
        file_sha = _write(path, payload)
        fixture["kwargs"]["expected_parent_source_terminal_epoch_receipt_sha256"] = (
            file_sha
        )
        _rewrite_receipt(
            fixture,
            "parent",
            lambda parent: parent.update(
                {"terminal_receipt_file_sha256": file_sha}
            ),
        )
        return
    payload = _self_hashed(payload, "receipt_root_sha256")
    file_sha = _sha(payload)
    category_root = path.parents[2]
    path = category_root / "sha256" / file_sha[:2] / f"{file_sha}.json"
    assert _write(path, payload) == file_sha
    fixture["paths"][artifact] = path
    kwargs = fixture["kwargs"]
    expected_fields = {
        "parent": "expected_parent_source_authority_receipt_sha256",
        "epoch": "expected_parent_source_terminal_epoch_receipt_sha256",
        "evaluation": "expected_factor_v2_evaluation_authority_receipt_sha256",
    }
    path_fields = {
        "parent": "parent_source_authority_receipt_path",
        "epoch": "parent_source_terminal_epoch_receipt_path",
        "evaluation": "factor_v2_evaluation_authority_receipt_path",
    }
    kwargs[path_fields[artifact]] = path.resolve()
    kwargs[expected_fields[artifact]] = file_sha


def _rewrite_epoch_chain_file(
    fixture: dict[str, Any],
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    mirror_terminal_state: bool = False,
) -> None:
    paths = fixture["paths"]["epoch_chain"]
    payloads = {artifact: _read(path) for artifact, path in paths.items()}
    mutate(payloads[name])
    run_claim_sha256 = _write(paths["run_claim"], payloads["run_claim"])
    if name == "run_claim":
        payloads["run_receipt"]["run_claim_sha256"] = run_claim_sha256
    run_receipt_sha256 = _write(paths["run_receipt"], payloads["run_receipt"])
    if name in {"run_claim", "run_receipt"}:
        payloads["verify_claim"]["run_receipt_sha256"] = run_receipt_sha256
        payloads["terminal_receipt"]["run_receipt_sha256"] = run_receipt_sha256
    verify_claim_sha256 = _write(paths["verify_claim"], payloads["verify_claim"])
    if name in {"run_claim", "run_receipt", "verify_claim"}:
        payloads["terminal_receipt"]["verify_claim_sha256"] = verify_claim_sha256
    terminal_sha256 = _write(
        paths["terminal_receipt"], payloads["terminal_receipt"]
    )
    fixture["kwargs"]["expected_parent_source_terminal_epoch_receipt_sha256"] = (
        terminal_sha256
    )
    parent_updates = {
        "run_claim_sha256": run_claim_sha256,
        "run_receipt_sha256": run_receipt_sha256,
        "terminal_receipt_file_sha256": terminal_sha256,
        "verify_claim_sha256": verify_claim_sha256,
    }
    if mirror_terminal_state:
        parent_updates["terminal_root_state"] = payloads["terminal_receipt"]["state"]
    _rewrite_receipt(
        fixture,
        "parent",
        lambda parent: parent.update(parent_updates),
    )


def _rewrite_epoch_identity(
    fixture: dict[str, Any], field: str, value: str
) -> None:
    for name in fixture["paths"]["epoch_chain"]:
        _rewrite_epoch_chain_file(
            fixture,
            name,
            lambda payload, field=field, value=value: payload.update(
                {field: value}
            ),
        )
    _rewrite_receipt(
        fixture,
        "parent",
        lambda parent: parent.update({field: value}),
    )


def _rewrite_candidate_snapshot(
    fixture: dict[str, Any],
    name: str,
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    old_descriptor = _read(fixture["paths"]["candidate_descriptor"])
    old_snapshot_root = fixture["paths"]["candidate_snapshot_root"]
    payloads = {
        snapshot_name: _read(old_snapshot_root / f"{snapshot_name}.json")
        for snapshot_name in candidate_authority._SNAPSHOT_NAMES
    }
    mutate(payloads[name])
    descriptors: dict[str, dict[str, Any]] = {}
    for snapshot_name, payload in payloads.items():
        raw = _bytes(payload)
        rows = payload.get("rows")
        descriptors[snapshot_name] = {
            "file_sha256": _sha(raw),
            "payload_root_sha256": _sha(payload),
            "relative_path": f"{snapshot_name}.json",
            "row_count": len(rows) if isinstance(rows, list) else None,
            "rows_sha256": payload.get("rows_sha256"),
            "schema": payload["schema"],
        }
    unsigned_descriptor = deepcopy(old_descriptor)
    unsigned_descriptor.pop("authority_root_sha256")
    unsigned_descriptor["snapshots"] = descriptors
    if name == "calendar":
        calendar = payloads["calendar"]
        unsigned_descriptor["calendar"] = {
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
    descriptor = {
        **unsigned_descriptor,
        "authority_root_sha256": _sha(unsigned_descriptor),
    }
    candidate_root = Path(fixture["kwargs"]["candidate_output_root"])
    descriptor_relative = candidate_authority._descriptor_relative_path(
        descriptor["authority_root_sha256"]
    )
    descriptor_path = candidate_root.joinpath(*descriptor_relative.split("/"))
    for snapshot_name, payload in payloads.items():
        _write(descriptor_path.parent / f"{snapshot_name}.json", payload)
    descriptor_sha = _write(descriptor_path, descriptor)
    manifest = _read(fixture["paths"]["candidate_manifest"])
    manifest.update(
        {
            "authority_root_sha256": descriptor["authority_root_sha256"],
            "descriptor_relative_path": descriptor_relative,
            "descriptor_sha256": descriptor_sha,
        }
    )
    manifest_sha = _sha(manifest)
    manifest_relative = candidate_authority._publication_relative_path(manifest_sha)
    manifest_path = candidate_root.joinpath(*manifest_relative.split("/"))
    assert _write(manifest_path, manifest) == manifest_sha
    fixture["paths"].update(
        {
            "candidate_descriptor": descriptor_path,
            "candidate_manifest": manifest_path,
            "candidate_snapshot_root": descriptor_path.parent,
        }
    )
    fixture["kwargs"].update(
        {
            "candidate_publication_path": manifest_path.resolve(),
            "expected_candidate_publication_sha256": manifest_sha,
        }
    )


def _rewrite_candidate_descriptor(
    fixture: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    old_descriptor_path = fixture["paths"]["candidate_descriptor"]
    old_snapshot_root = fixture["paths"]["candidate_snapshot_root"]
    unsigned_descriptor = _read(old_descriptor_path)
    unsigned_descriptor.pop("authority_root_sha256")
    mutate(unsigned_descriptor)
    descriptor = {
        **unsigned_descriptor,
        "authority_root_sha256": _sha(unsigned_descriptor),
    }
    candidate_root = Path(fixture["kwargs"]["candidate_output_root"])
    descriptor_relative = candidate_authority._descriptor_relative_path(
        descriptor["authority_root_sha256"]
    )
    descriptor_path = candidate_root.joinpath(*descriptor_relative.split("/"))
    for snapshot_name in candidate_authority._SNAPSHOT_NAMES:
        source = old_snapshot_root / f"{snapshot_name}.json"
        target = descriptor_path.parent / f"{snapshot_name}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    descriptor_sha = _write(descriptor_path, descriptor)
    manifest = _read(fixture["paths"]["candidate_manifest"])
    manifest.update(
        {
            "authority_root_sha256": descriptor["authority_root_sha256"],
            "descriptor_relative_path": descriptor_relative,
            "descriptor_sha256": descriptor_sha,
        }
    )
    _replace_candidate_manifest(fixture, manifest)
    fixture["paths"].update(
        {
            "candidate_descriptor": descriptor_path,
            "candidate_snapshot_root": descriptor_path.parent,
        }
    )


def _replace_candidate_manifest(
    fixture: dict[str, Any], manifest: dict[str, Any]
) -> None:
    candidate_root = Path(fixture["kwargs"]["candidate_output_root"])
    manifest_sha = _sha(manifest)
    manifest_relative = candidate_authority._publication_relative_path(manifest_sha)
    manifest_path = candidate_root.joinpath(*manifest_relative.split("/"))
    assert _write(manifest_path, manifest) == manifest_sha
    fixture["paths"]["candidate_manifest"] = manifest_path
    fixture["kwargs"].update(
        {
            "candidate_publication_path": manifest_path.resolve(),
            "expected_candidate_publication_sha256": manifest_sha,
        }
    )


def _rewrite_candidate_manifest(
    fixture: dict[str, Any],
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    manifest = _read(fixture["paths"]["candidate_manifest"])
    mutate(manifest)
    _replace_candidate_manifest(fixture, manifest)


def _remove_last_calendar_date(
    payload: dict[str, Any],
    *,
    rows_field: str,
    count_field: str,
    sha_field: str,
) -> None:
    rows = list(payload[rows_field])
    rows.pop()
    payload[rows_field] = rows
    payload[count_field] = len(rows)
    payload[sha_field] = _sha(rows)


def _drop_upstream_segment(payload: dict[str, Any], segment: str) -> None:
    payload["source_segments"] = [
        item for item in payload["source_segments"] if item != segment
    ]
    payload["pre_filter_segment_counts"].pop(segment)
    for entry in payload["per_date"]:
        entry["segment_counts"].pop(segment)
    payload["per_date_board_ledger_root_sha256"] = _sha(payload["per_date"])


def _shorten_upstream_ledger(payload: dict[str, Any]) -> None:
    payload["per_date"] = payload["per_date"][:-1]
    payload["per_date_board_ledger_root_sha256"] = _sha(payload["per_date"])


def _mutate_parent_row(
    payload: dict[str, Any], field: str, value: Any
) -> None:
    payload["rows"][0][field] = value
    payload["rows_sha256"] = _sha(payload["rows"])


def _publication_path(output_root: Path, publication: dict[str, Any]) -> Path:
    return output_root.joinpath(*publication["publication_relative_path"].split("/"))


def _global_attempt_ledger_root(kwargs: dict[str, Any]) -> Path:
    parent = _read(Path(kwargs["parent_source_authority_receipt_path"]))
    return Path(parent["global_attempt_ledger_root"])


def _expected_activation_input_bindings(
    fixture: dict[str, Any]
) -> dict[str, Any]:
    candidate = _read(fixture["paths"]["candidate_manifest"])
    parent = _read(fixture["paths"]["parent"])
    evaluation = _read(fixture["paths"]["evaluation"])
    bindings = {
        "attempt_key_sha256": parent["attempt_key_sha256"],
        "candidate_authority_root_sha256": candidate["authority_root_sha256"],
        "candidate_descriptor_sha256": candidate["descriptor_sha256"],
        "candidate_publication_file_sha256": fixture["kwargs"][
            "expected_candidate_publication_sha256"
        ],
        "daily_basic_authority_receipt_file_sha256": fixture["kwargs"][
            "expected_daily_basic_authority_receipt_sha256"
        ],
        "daily_basic_authority_receipt_path": str(
            fixture["kwargs"]["daily_basic_authority_receipt_path"]
        ),
        "factor_v2_evaluation_authority_receipt_file_sha256": fixture["kwargs"][
            "expected_factor_v2_evaluation_authority_receipt_sha256"
        ],
        "factor_v2_evaluation_authority_receipt_root_sha256": evaluation[
            "receipt_root_sha256"
        ],
        "feature_history_collection_issuance_file_sha256": fixture["kwargs"][
            "expected_feature_history_collection_issuance_sha256"
        ],
        "feature_history_collection_issuance_path": str(
            fixture["kwargs"]["feature_history_collection_issuance_path"]
        ),
        "feature_history_frozen_attestation_file_sha256": fixture["kwargs"][
            "expected_feature_history_frozen_attestation_sha256"
        ],
        "feature_history_frozen_attestation_path": str(
            fixture["kwargs"]["feature_history_frozen_attestation_path"]
        ),
        "global_attempt_identity_sha256": parent[
            "global_attempt_identity_sha256"
        ],
        "global_attempt_ledger_root": parent["global_attempt_ledger_root"],
        "global_run_claim_path": parent["global_run_claim_path"],
        "global_run_receipt_path": parent["global_run_receipt_path"],
        "global_terminal_receipt_path": parent["global_terminal_receipt_path"],
        "global_verify_claim_path": parent["global_verify_claim_path"],
        "native_lease_identity_sha256": parent["native_lease_identity_sha256"],
        "native_lease_policy_version": parent["native_lease_policy_version"],
        "parent_source_authority_receipt_file_sha256": fixture["kwargs"][
            "expected_parent_source_authority_receipt_sha256"
        ],
        "parent_source_authority_receipt_root_sha256": parent[
            "receipt_root_sha256"
        ],
        "parent_source_terminal_receipt_file_sha256": fixture["kwargs"][
            "expected_parent_source_terminal_epoch_receipt_sha256"
        ],
        "run_spec_sha256": parent["run_spec_sha256"],
        "semantic_input_root_sha256": parent["semantic_input_root_sha256"],
    }
    assert set(bindings) == set(activation.ACTIVATION_INPUT_BINDING_FIELDS)
    return bindings


def _directory_alias(alias: Path, target: Path) -> None:
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(alias), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_valid_activation_projects_only_verified_formal_development_input(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    rerooted = _fixture(tmp_path / "rerooted")
    epoch = _read(fixture["paths"]["epoch"])
    rerooted_epoch = _read(rerooted["paths"]["epoch"])
    parent = _read(fixture["paths"]["parent"])
    rerooted_parent = _read(rerooted["paths"]["parent"])
    assert set(path.name for path in fixture["paths"]["epoch_chain"].values()) == set(
        activation.PARENT_SOURCE_EPOCH_FILE_NAMES
    )
    assert epoch["attempt_key_sha256"] == rerooted_epoch["attempt_key_sha256"]
    assert (
        epoch["global_attempt_identity_sha256"]
        == rerooted_epoch["global_attempt_identity_sha256"]
    )
    assert parent["global_run_claim_path"] != rerooted_parent["global_run_claim_path"]
    assert set(fixture["evaluation_source_binding"]) == set(
        activation.FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS
    )
    publication = activation._publish_disposable_test_factor_v3_formal_development_input_activation(
        **fixture["kwargs"]
    )
    assert publication["schema"] == activation.PUBLICATION_SCHEMA
    output_root = Path(fixture["kwargs"]["output_root"])
    publication_path = _publication_path(output_root, publication)
    assert publication_path.is_file()
    assert publication_path.name == f"{publication['publication_sha256']}.json"
    assert "sha256" in publication_path.parts
    assert _sha(publication_path.read_bytes()) == publication["publication_sha256"]
    publication_raw = publication_path.read_bytes()
    assert (
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
        == publication
    )
    assert publication_path.read_bytes() == publication_raw
    manifest = _read(publication_path)
    assert manifest["schema"] == activation.PUBLICATION_SCHEMA
    assert manifest["test_fixture_only"] is True
    assert manifest["contract_binding_validated"] is True
    assert manifest["activation_verified"] is False
    assert manifest["source_authority_complete"] is False
    assert manifest["formal_materialization_eligible"] is False
    assert all(manifest[field] is False for field in activation.SAFETY_FALSE_FIELDS)
    descriptor_path = output_root.joinpath(
        *manifest["descriptor_relative_path"].split("/")
    )
    assert "sha256" in descriptor_path.parts
    assert descriptor_path.name == "input-authority.json"
    assert descriptor_path.parent.name == manifest["activation_root_sha256"]
    assert descriptor_path.parent.parent.name == manifest["activation_root_sha256"][:2]
    descriptor = _read(descriptor_path)
    assert descriptor["authority_scope"] == "DISPOSABLE_TEST_FIXTURE_ONLY"
    assert descriptor["test_fixture_only"] is True
    assert descriptor["schema_version"] == activation.MATERIALIZER_INPUT_AUTHORITY_SCHEMA
    assert descriptor["authority_status"] == "DISPOSABLE_TEST_FIXTURE_CONTRACT_REPLAY_ONLY"
    assert descriptor["contract_binding_validated"] is True
    assert descriptor["activation_verified"] is False
    assert descriptor["verified"] is False
    assert descriptor["source_authority_complete"] is False
    assert descriptor["formal_materialization_eligible"] is False
    assert descriptor["formal_materialization_performed"] is False
    assert descriptor["parent_source_authority_verified"] is False
    assert descriptor["machine_global_root_lease_verified"] is False
    assert descriptor["root_epoch_terminal_verified"] is False
    assert descriptor["input_bindings"] == _expected_activation_input_bindings(
        fixture
    )
    assert set(descriptor["authority_verifier_bindings"]) == {
        "daily_basic",
        "factor_v2_evaluation",
        "feature_history",
        "parent_source",
        "points_contract",
    }
    for binding in descriptor["authority_verifier_bindings"].values():
        assert set(binding) == {
            "file_sha256",
            "relative_path",
            "schema",
            "verdict_root_sha256",
        }
        verdict_path = output_root.joinpath(*binding["relative_path"].split("/"))
        verdict_raw = verdict_path.read_bytes()
        assert verdict_path.name == f"{binding['file_sha256']}.json"
        assert _sha(verdict_raw) == binding["file_sha256"]
        verdict = _read(verdict_path)
        assert _sha(verdict) == binding["verdict_root_sha256"]
        assert verdict["test_fixture_only"] is True
        assert verdict["contract_binding_validated"] is True
        assert verdict["verified"] is False
    assert descriptor["parent_projection"] == fixture["parent_projection"]
    assert descriptor["factor_v2_evaluation_projection"] == fixture[
        "evaluation_projection"
    ]
    assert descriptor["factor_v2_evaluation_source_binding"] == fixture[
        "evaluation_source_binding"
    ]
    assert descriptor["calendar"] == {
        **activation.EXACT_CALENDAR_COUNTS,
        **fixture["calendar_roots"],
    }
    assert descriptor["upstream_board_projection"][
        "preserved_before_target_scope_filter"
    ] is True
    assert descriptor["upstream_board_projection"]["source_segments"] == list(
        activation.UPSTREAM_SOURCE_SEGMENTS
    )
    assert descriptor["upstream_board_projection"][
        "upstream_board_ledger_root_sha256"
    ] == fixture["upstream_board_ledger_root_sha256"]
    assert all(descriptor[field] is False for field in activation.SAFETY_FALSE_FIELDS)
    assert set(descriptor["source_descriptors"]) == set(
        candidate_authority._SNAPSHOT_NAMES
    )
    verifier_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    independent = activation._verify_disposable_test_factor_v3_formal_development_input_activation(
        **verifier_kwargs,
        activation_publication_path=publication_path,
        expected_activation_publication_sha256=publication["publication_sha256"],
        verifier_output_root=(tmp_path / "independent-replay").resolve(),
    )
    assert independent["verified"] is False
    assert independent["independent_public_replay_performed"] is False
    assert independent["differential_contract_replay_performed"] is True
    verifier_receipt = _read(
        (tmp_path / "independent-replay")
        .resolve()
        .joinpath(*independent["receipt_relative_path"].split("/"))
    )
    assert verifier_receipt["authority_scope"] == "DISPOSABLE_TEST_FIXTURE_ONLY"
    assert verifier_receipt["test_fixture_only"] is True
    assert verifier_receipt["contract_binding_validated"] is True
    assert verifier_receipt["activation_verified"] is False
    assert verifier_receipt["source_authority_complete"] is False
    assert verifier_receipt["formal_materialization_eligible"] is False
    assert verifier_receipt["differential_contract_replay_performed"] is True
    assert verifier_receipt["independent_public_replay_performed"] is False
    assert verifier_receipt["verified"] is False
    producer_identity = verifier_receipt[
        "independent_verifier_producer_identity"
    ]
    assert set(producer_identity["dependencies"]) == {
        "factor_v2_branch_contract",
        "independent_core",
        "independent_verifier",
        "points_contract",
    }
    unsigned_identity = deepcopy(producer_identity)
    identity_root = unsigned_identity.pop("root_sha256")
    assert _sha(unsigned_identity) == identity_root
    assert (
        verifier_receipt["independent_verifier_producer_identity_sha256"]
        == identity_root
    )
    for published_root in (
        output_root,
        (tmp_path / "independent-replay").resolve(),
    ):
        for published_path in published_root.rglob("*.json"):
            _assert_no_capability_keys(_read(published_path))
    publication_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    assert publication_path.read_bytes() == b'{"drifted":true}'
    publication_path.write_bytes(publication_raw)
    descriptor_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    assert descriptor_path.read_bytes() == b'{"drifted":true}'


def test_native_parent_source_authority_absence_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(
        activation,
        "_verify_native_parent_source_authority_evidence",
        None,
    )
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="native.*authority|authority.*unavailable",
    ):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


def test_public_formal_entrypoints_reject_disposable_scope_before_any_write(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    output_root = Path(fixture["kwargs"]["output_root"])
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="public formal activation rejects disposable",
    ):
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    assert not output_root.exists()

    publication = (
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    )
    verifier_root = (tmp_path / "formal-verifier-output-must-not-exist").resolve()
    verifier_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    verifier_kwargs["candidate_publication_path"] = (
        tmp_path / "must-not-be-read.json"
    ).resolve()
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="formal independent TCB unavailable",
    ):
        activation.verify_factor_v3_formal_development_input_activation(
            **verifier_kwargs,
            activation_publication_path=_publication_path(
                output_root, publication
            ),
            expected_activation_publication_sha256=publication[
                "publication_sha256"
            ],
            verifier_output_root=verifier_root,
        )
    assert not verifier_root.exists()


def test_public_formal_default_authority_verifiers_fail_before_any_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    for name in (
        "_verify_daily_basic_authority_evidence",
        "_verify_factor_v2_evaluation_authority_evidence",
        "_verify_feature_history_authority_evidence",
        "_verify_native_parent_source_authority_evidence",
        "_verify_points_contract_authority_evidence",
    ):
        monkeypatch.setattr(activation, name, None)
    output_root = Path(fixture["kwargs"]["output_root"])
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="authority.*unavailable|unavailable.*authority",
    ):
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    assert not output_root.exists()


def test_points_contract_runtime_hash_and_exact_session_binding_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    drifted = deepcopy(candidate_authority.points.FACTOR_V3_POINTS_CONTRACT)
    drifted["temporal_role"] = "drifted"
    monkeypatch.setattr(
        candidate_authority.points,
        "FACTOR_V3_POINTS_CONTRACT",
        drifted,
    )
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="points contract|frozen|drift",
    ):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )

    monkeypatch.setattr(
        candidate_authority.points,
        "FACTOR_V3_POINTS_CONTRACT",
        deepcopy(candidate_authority.points.FACTOR_V3_POINTS_CONTRACT),
    )


def test_formal_points_verifier_binds_483_start_end_hash_and_parent_roots() -> None:
    expectation = candidate_authority.points.FACTOR_V3_POINTS_CONTRACT[
        "preregistered_parent_expectation"
    ]
    sessions = expectation["sessions"]
    evidence = {
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
    verdict = activation._verify_formal_points_contract_authority_evidence(
        **evidence
    )
    assert verdict["authority_scope"] == activation.FORMAL_AUTHORITY_SCOPE
    for field in (
        "development_session_count",
        "development_session_start",
        "development_session_end",
        "development_session_sha256",
        "candidate_keys_sha256",
        "source_feature_projection_rows_sha256",
        "full_export_rows_sha256",
        "parent_row_count",
    ):
        mutated = deepcopy(evidence)
        mutated[field] = 0 if field.endswith("count") else "0" * 64
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError
        ):
            activation._verify_formal_points_contract_authority_evidence(
                **mutated
            )


def test_parent_signal_date_must_belong_to_exact_483_session_set(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    calendar = _read(
        fixture["paths"]["candidate_snapshot_root"] / "calendar.json"
    )
    parent = _read(
        fixture["paths"]["candidate_snapshot_root"] / "factor_v2_parent.json"
    )
    parent["rows"][0]["signal_date"] = "2026-01-05"
    parent["rows"][0]["candidate_key"] = "cn-a-share:000001.SZ|2026-01-05"
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="key/date/code|session",
    ):
        activation._validate_candidate_parent(
            parent,
            development_sessions=calendar["development_sessions"],
        )


def test_factor_v2_branch_adapter_contract_is_frozen_exactly(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    descriptor = _read(fixture["paths"]["candidate_descriptor"])
    evaluation_binding = _read(
        fixture["paths"]["candidate_snapshot_root"] / "factor_v2_evaluation.json"
    )
    activation._validate_candidate_branch(
        descriptor["factor_v2_branch"], evaluation_binding
    )

    cases: tuple[Callable[[dict[str, Any]], None], ...] = (
        lambda value: value.update(
            {"arm_order": list(reversed(factor_v2_branch_selector.ARM_ORDER))}
        ),
        lambda value: value.update({"selection_rule": "first-green-arm"}),
        lambda value: value.update({"selected_arm": None}),
        lambda value: value.update({"low_rvol_overlay_status": "ELIGIBLE"}),
    )
    for mutate in cases:
        branch = deepcopy(descriptor["factor_v2_branch"])
        mutate(branch)
        unsigned = dict(branch)
        unsigned.pop("receipt_sha256")
        branch["receipt_sha256"] = _sha(unsigned)
        rebound_evaluation = deepcopy(evaluation_binding)
        rebound_evaluation["branch_receipt_sha256"] = branch["receipt_sha256"]
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError
        ):
            activation._validate_candidate_branch(branch, rebound_evaluation)


def test_daily_basic_733_last_session_statistic_is_replayed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)

    def mutate(payload: dict[str, Any]) -> None:
        source = payload["source_receipt_projection"]
        source["per_date_statistics"][-1]["trade_date"] = "2099-12-31"
        source["per_date_statistics_sha256"] = _sha(
            source["per_date_statistics"]
        )
        payload["source_receipt_projection_sha256"] = _sha(source)

    _rewrite_candidate_snapshot(fixture, "daily_basic_exact_set_receipt", mutate)
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="daily-basic|per-date|session|projection",
    ):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


@pytest.mark.parametrize(
    ("registry", "replacement"),
    [
        (
            "_verify_native_parent_source_authority_evidence",
            lambda **_evidence: {"verified": True},
        ),
        (
            "_verify_factor_v2_evaluation_authority_evidence",
            lambda **_evidence: (_ for _ in ()).throw(
                activation.FactorV3FormalDevelopmentInputActivationError(
                    "native evaluation authority unavailable"
                )
            ),
        ),
    ],
)
def test_registered_authority_verdicts_must_bind_exact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry: str,
    replacement: Any,
) -> None:
    fixture = _fixture(tmp_path)
    monkeypatch.setattr(activation, registry, replacement)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


def test_feature_history_issuance_and_attestation_exact_bindings_fail_closed(
    tmp_path: Path,
) -> None:
    cases: tuple[tuple[str, str, Callable[[dict[str, Any]], None]], ...] = (
        (
            "issuance-status",
            "feature_history_collection_issuance",
            lambda value: value.update({"publication_status": "SEALED"}),
        ),
        (
            "issuance-publication-schema",
            "feature_history_collection_issuance",
            lambda value: value.update({"publication_schema": "wrong"}),
        ),
        (
            "issuance-manifest",
            "feature_history_collection_issuance",
            lambda value: value.update({"authority_manifest_sha256": "0" * 64}),
        ),
        (
            "attestation-receipt",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"receipt_sha256": "0" * 64}
            ),
        ),
        (
            "attestation-issuance-sha",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"publication_issuance_sha256": "0" * 64}
            ),
        ),
        (
            "attestation-issuance-relative-path",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"publication_issuance_relative_path": "wrong/path.json"}
            ),
        ),
        (
            "attestation-manifest",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"authority_manifest_sha256": "0" * 64}
            ),
        ),
        (
            "attestation-manifest-path",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"authority_manifest_relative_path": "wrong/path.json"}
            ),
        ),
        (
            "attestation-feature-run-root",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"feature_run_root": "relative"}
            ),
        ),
        (
            "attestation-feature-fields-missing",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].pop(
                "feature_run_spec_file_sha256"
            ),
        ),
        (
            "attestation-feature-run-spec-path",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"feature_run_spec_path": "relative"}
            ),
        ),
        (
            "attestation-feature-fields-extra",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"unexpected": False}
            ),
        ),
        (
            "attestation-publication-capability",
            "feature_history_frozen_attestation",
            lambda value: value["feature_history"].update(
                {"publication_capability_sha256": "0" * 64}
            ),
        ),
    )
    kw_fields = {
        "feature_history_collection_issuance": (
            "feature_history_collection_issuance_path",
            "expected_feature_history_collection_issuance_sha256",
        ),
        "feature_history_frozen_attestation": (
            "feature_history_frozen_attestation_path",
            "expected_feature_history_frozen_attestation_sha256",
        ),
    }
    for name, artifact, mutate in cases:
        fixture = _fixture(tmp_path / name)
        path = fixture["paths"][artifact]
        payload = _read(path)
        mutate(payload)
        digest = _sha(payload)
        replacement = path.parents[2] / "sha256" / digest[:2] / f"{digest}.json"
        assert _write(replacement, payload) == digest
        path_field, sha_field = kw_fields[artifact]
        fixture["kwargs"][path_field] = replacement.resolve()
        fixture["kwargs"][sha_field] = digest
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError
        ):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_candidate_unverified_or_nonterminal_authority_cannot_promote(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
        (
            "candidate-parent-receipt",
            "parent",
            lambda value: value.update(
                {"authority_status": "CANDIDATE_AUTHORITY_CONTRACT_ONLY"}
            ),
        ),
        *[
            (
                    f"parent-{field}-self-promoted",
                    "parent",
                    lambda value, field=field: value.update({field: True}),
            )
            for field in (
                "formal_materialization_eligible",
                "parent_source_authority_verified",
                "source_authority_complete",
                "source_authority_verified",
                "verified",
            )
        ],
        (
                "caller-self-promotes-independent-replay",
                "parent",
                lambda value: value.update({"independent_public_replay_performed": True}),
        ),
        (
            "parent-authority-schema-drift",
            "parent",
            lambda value: value.update({"schema": "wrong"}),
        ),
        (
                "machine-global-lease-self-promoted",
                "parent",
                lambda value: value.update({"machine_global_root_lease_verified": True}),
        ),
        (
                "root-epoch-terminal-self-promoted",
                "parent",
                lambda value: value.update({"root_epoch_terminal_verified": True}),
        ),
        (
            "terminal-root-state-not-terminal",
            "parent",
            lambda value: value.update(
                {"terminal_root_state": activation.PARENT_SOURCE_ROOT_STATE_VERIFY_CLAIMED}
            ),
        ),
        (
            "parent-development-only-false",
            "parent",
            lambda value: value.update({"development_only": False}),
        ),
        (
                "parent-verified-self-promoted",
                "parent",
                lambda value: value.update({"verified": True}),
        ),
        (
            "parent-global-ledger-root-drift",
            "parent",
            lambda value: value.update({"global_attempt_ledger_root": "C:\\wrong"}),
        ),
        (
            "parent-requested-action-not-verify",
            "parent",
            lambda value: value.update({"requested_action": 1}),
        ),
        (
            "parent-transition-not-start-verify",
            "parent",
            lambda value: value.update({"approved_transition": 2}),
        ),
        (
            "parent-observed-state-not-run-completed",
            "parent",
            lambda value: value.update(
                {"observed_root_state": activation.PARENT_SOURCE_ROOT_STATE_RUN_CLAIMED}
            ),
        ),
        (
                "parent-single-attempt-self-promoted",
                "parent",
                lambda value: value.update({"single_attempt_verified": True}),
        ),
        *[
            (
                f"parent-{field}-epoch-binding-drift",
                "parent",
                lambda value, field=field: value.update({field: "0" * 64}),
            )
            for field in (
                "attempt_key_sha256",
                "global_attempt_identity_sha256",
                "semantic_input_root_sha256",
                "run_claim_sha256",
                "run_receipt_sha256",
                "terminal_receipt_file_sha256",
                "verify_claim_sha256",
                "run_spec_sha256",
                "native_lease_identity_sha256",
            )
        ],
        (
            "parent-run-claim-path-drift",
            "parent",
            lambda value: value.update({"global_run_claim_path": "C:\\wrong\\run.json"}),
        ),
        (
            "parent-run-receipt-path-drift",
            "parent",
            lambda value: value.update(
                {"global_run_receipt_path": "C:\\wrong\\run.receipt.json"}
            ),
        ),
        (
            "parent-verify-claim-path-drift",
            "parent",
            lambda value: value.update(
                {"global_verify_claim_path": "C:\\wrong\\verify.json"}
            ),
        ),
        (
            "parent-terminal-receipt-path-drift",
            "parent",
            lambda value: value.update(
                {"global_terminal_receipt_path": "C:\\wrong\\terminal.receipt.json"}
            ),
        ),
        (
            "parent-native-policy-drift",
            "parent",
            lambda value: value.update({"native_lease_policy_version": "wrong"}),
        ),
        (
            "unverified-evaluator-adapter",
            "evaluation",
            lambda value: value.update(
                {"authority_status": "UNVERIFIED_DEVELOPMENT_ADAPTER"}
            ),
        ),
        *[
            (
                    f"evaluation-{field}-self-promoted",
                    "evaluation",
                    lambda value, field=field: value.update({field: True}),
            )
            for field in (
                "formal_materialization_eligible",
                "independent_public_replay_performed",
                "publisher_terminal_chain_verified",
                "source_authority_complete",
                "verified",
            )
        ],
        (
            "evaluation-schema-drift",
            "evaluation",
            lambda value: value.update({"schema": "wrong"}),
        ),
        (
            "evaluation-projection-schema-drift",
            "evaluation",
            lambda value: value["evaluation_projection"].update({"schema": "wrong"}),
        ),
        (
            "evaluation-projection-extra-field",
            "evaluation",
            lambda value: value["evaluation_projection"].update(
                {"unbound_root_sha256": "0" * 64}
            ),
        ),
        (
            "evaluation-projection-missing-field",
            "evaluation",
            lambda value: value["evaluation_projection"].pop(
                "terminal_decision_descriptor_sha256"
            ),
        ),
        *[
            (
                f"evaluation-{field}-drift",
                "evaluation",
                lambda value, field=field: value["evaluation_projection"].update(
                    {field: "0" * 64}
                ),
            )
            for field in activation.FACTOR_V2_EVALUATION_PROJECTION_FIELDS
        ],
        *[
            (
                f"evaluation-source-{field}-drift",
                "evaluation",
                lambda value, field=field: value[
                    "candidate_evaluation_binding"
                ].update({field: "0" * 64}),
            )
            for field in activation.FACTOR_V2_EVALUATION_SOURCE_BINDING_FIELDS
        ],
        (
            "evaluation-source-binding-extra-field",
            "evaluation",
            lambda value: value["candidate_evaluation_binding"].update(
                {"unbound_field": "wrong"}
            ),
        ),
        (
            "evaluation-source-binding-missing-field",
            "evaluation",
            lambda value: value["candidate_evaluation_binding"].pop(
                "decision_receipt_raw_file_sha256"
            ),
        ),
    ]
    for name, artifact, mutate in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_receipt(fixture, artifact, mutate)
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_machine_global_epoch_is_exact_four_file_terminal_chain(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path / "exact-chain")
    parent = _read(fixture["paths"]["parent"])
    chain = fixture["paths"]["epoch_chain"]
    expected_directory = Path(parent["global_attempt_ledger_root"]).joinpath(
        *activation.PARENT_SOURCE_EPOCH_DIRECTORY_TEMPLATE.format(
            prefix=parent["attempt_key_sha256"][:2],
            attempt_key=parent["attempt_key_sha256"],
        ).split("/")
    )
    assert parent["attempt_key_sha256"] == _sha(
        {
            "schema": activation.PARENT_SOURCE_ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": parent["semantic_input_root_sha256"],
        }
    )
    assert parent["global_attempt_identity_sha256"] == _sha(
        {
            "attempt_key_sha256": parent["attempt_key_sha256"],
            "schema": activation.PARENT_SOURCE_GLOBAL_ATTEMPT_IDENTITY_SCHEMA,
        }
    )
    assert tuple(path.name for path in chain.values()) == (
        activation.PARENT_SOURCE_EPOCH_FILE_NAMES
    )
    assert all(path.parent == expected_directory for path in chain.values())
    for path in chain.values():
        contract = activation.PARENT_SOURCE_EPOCH_FILE_CONTRACT[path.name]
        payload = _read(path)
        assert set(payload) == set(contract["fields"])
        assert payload["schema"] == contract["schema"]
        assert payload["state"] == contract["state"]
        if "action" in contract:
            assert payload["action"] == contract["action"]

    cases: list[
        tuple[str, str, Callable[[dict[str, Any]], None], bool]
    ] = [
        *[
            (
                f"{name}-schema-drift",
                name,
                lambda value: value.update({"schema": "wrong"}),
                False,
            )
            for name in chain
        ],
        *[
            (
                f"{name}-state-drift",
                name,
                lambda value: value.update(
                    {"state": activation.PARENT_SOURCE_ROOT_STATE_EMPTY}
                ),
                name == "terminal_receipt",
            )
            for name in chain
        ],
        *[
            (
                f"{name}-extra-field",
                name,
                lambda value: value.update({"unbound_field": True}),
                False,
            )
            for name in chain
        ],
        *[
            (
                f"{name}-missing-field",
                name,
                lambda value: value.pop("attempt_key_sha256"),
                False,
            )
            for name in chain
        ],
        (
            "run-claim-action-drift",
            "run_claim",
            lambda value: value.update(
                {"action": activation.PARENT_SOURCE_ROOT_ACTION_VERIFY}
            ),
            False,
        ),
        (
            "verify-claim-action-drift",
            "verify_claim",
            lambda value: value.update(
                {"action": activation.PARENT_SOURCE_ROOT_ACTION_RUN}
            ),
            False,
        ),
        *[
            (
                f"{name}-{field}-drift",
                name,
                lambda value, field=field: value.update({field: "0" * 64}),
                False,
            )
            for name in chain
            for field in (
                "attempt_key_sha256",
                "global_attempt_identity_sha256",
                "run_spec_sha256",
            )
        ],
        (
            "run-receipt-run-claim-link-drift",
            "run_receipt",
            lambda value: value.update({"run_claim_sha256": "0" * 64}),
            False,
        ),
        (
            "verify-claim-run-receipt-link-drift",
            "verify_claim",
            lambda value: value.update({"run_receipt_sha256": "0" * 64}),
            False,
        ),
        (
            "terminal-run-receipt-link-drift",
            "terminal_receipt",
            lambda value: value.update({"run_receipt_sha256": "0" * 64}),
            False,
        ),
        (
            "terminal-verify-claim-link-drift",
            "terminal_receipt",
            lambda value: value.update({"verify_claim_sha256": "0" * 64}),
            False,
        ),
    ]
    for name, artifact, mutate, mirror_terminal_state in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_epoch_chain_file(
            fixture,
            artifact,
            mutate,
            mirror_terminal_state=mirror_terminal_state,
        )
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    for name in chain:
        fixture = _fixture(tmp_path / f"missing-{name}")
        fixture["paths"]["epoch_chain"][name].unlink()
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "self-consistent-underived-global-identity")
    _rewrite_epoch_identity(
        fixture,
        "global_attempt_identity_sha256",
        "0" * 64,
    )
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )

    fixture = _fixture(tmp_path / "extra-epoch-file")
    extra = fixture["paths"]["epoch"].parent / "unexpected.json"
    _write(extra, {"schema": "unbound"})
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


def test_candidate_and_unverified_adapters_cannot_self_promote(
    tmp_path: Path,
) -> None:
    for artifact, rewrite in (
        ("manifest", _rewrite_candidate_manifest),
        ("descriptor", _rewrite_candidate_descriptor),
    ):
        for field in (
            "formal_materialization_eligible",
            "parent_source_authority_verified",
            "source_authority_complete",
        ):
            fixture = _fixture(tmp_path / artifact / field)
            rewrite(
                fixture,
                lambda value, field=field: value.update({field: True}),
            )
            with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
                activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                    **fixture["kwargs"]
                )

    for name, mutate in (
        (
            "candidate-factor-v2-branch-formal-eligible",
            lambda value: value["factor_v2_branch"].update(
                {"formal_materialization_eligible": True}
            ),
        ),
        (
            "candidate-factor-v2-branch-verified",
            lambda value: value["factor_v2_branch"].update({"verified": True}),
        ),
    ):
        fixture = _fixture(tmp_path / name)
        _rewrite_candidate_descriptor(fixture, mutate)
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    for field in ("formal_materialization_eligible", "source_provenance_verified"):
        fixture = _fixture(tmp_path / "parent-adapter" / field)
        _rewrite_candidate_snapshot(
            fixture,
            "factor_v2_parent",
            lambda value, field=field: value[
                "parent_hash_binding_receipt"
            ].update({field: True}),
        )
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_three_parent_projections_calendar_and_upstream_board_are_exact(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, Callable[[dict[str, Any]], None]]] = [
        *[
            (
                field,
                lambda value, field=field: value["parent_projection"].update(
                    {field: "0" * 64}
                ),
            )
            for field in activation.PARENT_PROJECTION_FIELDS
        ],
        (
            "projection-schema",
            lambda value: value["parent_projection"].update({"schema": "wrong"}),
        ),
        (
            "projection-extra-key",
            lambda value: value["parent_projection"].update(
                {"unbound_root_sha256": "0" * 64}
            ),
        ),
        (
            "projection-missing-key",
            lambda value: value["parent_projection"].pop(
                "full_export_rows_sha256"
            ),
        ),
        (
            "projection-contract-extra-key",
            lambda value: value["parent_projection"]["contract"].update(
                {"unbound_projection": {"fields": [], "order": []}}
            ),
        ),
        (
            "projection-contract-missing-key",
            lambda value: value["parent_projection"]["contract"].pop(
                "full_export_rows"
            ),
        ),
        (
            "candidate-key-fields",
            lambda value: value["parent_projection"]["contract"][
                "candidate_keys"
            ].update({"fields": ["candidate_key", "signal_date"]}),
        ),
        (
            "candidate-key-order",
            lambda value: value["parent_projection"]["contract"][
                "candidate_keys"
            ].update({"order": ["signal_date", "candidate_key"]}),
        ),
        (
            "source-feature-field-order",
            lambda value: value["parent_projection"]["contract"][
                "source_feature_projection_rows"
            ].update({"fields": ["candidate_key", "features", "signal_date"]}),
        ),
        (
            "source-feature-row-order",
            lambda value: value["parent_projection"]["contract"][
                "source_feature_projection_rows"
            ].update({"order": ["candidate_key", "signal_date"]}),
        ),
        (
            "full-export-fields",
            lambda value: value["parent_projection"]["contract"][
                "full_export_rows"
            ].update(
                {"fields": ["candidate_key", "features", "signal_date"]}
            ),
        ),
        (
            "full-export-order",
            lambda value: value["parent_projection"]["contract"][
                "full_export_rows"
            ].update({"order": ["candidate_key", "signal_date"]}),
        ),
        (
            "points-common-eligible-keys-cross-binding",
            lambda value: value[
                "points_contract_common_eligible_projection"
            ].update({"common_eligible_candidate_keys_sha256": "0" * 64}),
        ),
        (
            "points-common-source-feature-cross-binding",
            lambda value: value[
                "points_contract_common_eligible_projection"
            ].update({"common_eligible_source_feature_rows_sha256": "0" * 64}),
        ),
        *[
            (
                field,
                lambda value, field=field: value["calendar_projection"].update(
                    {field: activation.EXACT_CALENDAR_COUNTS[field] - 1}
                ),
            )
            for field in activation.EXACT_CALENDAR_COUNTS
        ],
        (
            "BSE-missing-before-filter",
            lambda value: value["upstream_board_projection"].update(
                {
                    "source_segments": [
                        item
                        for item in activation.UPSTREAM_SOURCE_SEGMENTS
                        if item != "BSE"
                    ]
                }
            ),
        ),
        (
            "STAR-missing-before-filter",
            lambda value: value["upstream_board_projection"].update(
                {
                    "source_segments": [
                        item
                        for item in activation.UPSTREAM_SOURCE_SEGMENTS
                        if item != "SSE_STAR"
                    ]
                }
            ),
        ),
        (
            "filter-applied-upstream",
            lambda value: value["upstream_board_projection"].update(
                {"preserved_before_target_scope_filter": False}
            ),
        ),
    ]
    for name, mutate in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_receipt(fixture, "parent", mutate)
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_actual_candidate_calendar_board_parent_and_evaluation_snapshots_are_replayed(
    tmp_path: Path,
) -> None:
    cases: list[
        tuple[str, str, Callable[[dict[str, Any]], None]]
    ] = [
        (
            "candidate-prewindow-249",
            "calendar",
            lambda value: _remove_last_calendar_date(
                value,
                rows_field="prewindow_sessions",
                count_field="prewindow_session_count",
                sha_field="prewindow_sessions_sha256",
            ),
        ),
        (
            "candidate-development-482",
            "calendar",
            lambda value: _remove_last_calendar_date(
                value,
                rows_field="development_sessions",
                count_field="development_session_count",
                sha_field="development_sessions_sha256",
            ),
        ),
        (
            "candidate-all-market-732",
            "calendar",
            lambda value: _remove_last_calendar_date(
                value,
                rows_field="all_market_sessions",
                count_field="all_market_session_count",
                sha_field="all_market_sessions_sha256",
            ),
        ),
        (
            "candidate-source-date-731",
            "calendar",
            lambda value: _remove_last_calendar_date(
                value,
                rows_field="source_dates",
                count_field="source_date_count",
                sha_field="source_dates_sha256",
            ),
        ),
        (
            "candidate-board-without-BSE",
            "upstream_board_ledger",
            lambda value: _drop_upstream_segment(value, "BSE"),
        ),
        (
            "candidate-board-without-STAR",
            "upstream_board_ledger",
            lambda value: _drop_upstream_segment(value, "SSE_STAR"),
        ),
        (
            "candidate-board-filtered-upstream",
            "upstream_board_ledger",
            lambda value: value.update(
                {"preserved_before_target_scope_filter": False}
            ),
        ),
        (
            "candidate-board-731-dates",
            "upstream_board_ledger",
            _shorten_upstream_ledger,
        ),
        (
            "candidate-board-prefilter-count-drift",
            "upstream_board_ledger",
            lambda value: value["pre_filter_segment_counts"].update({"BSE": 0}),
        ),
        (
            "candidate-daily-rows-stale-root",
            "daily_basic",
            lambda value: value["rows"].append({"unbound": True}),
        ),
        (
            "candidate-parent-key-row-drift",
            "factor_v2_parent",
            lambda value: _mutate_parent_row(
                value,
                "candidate_key",
                "cn-a-share:999999|2025-01-02",
            ),
        ),
        (
            "candidate-parent-source-feature-row-drift",
            "factor_v2_parent",
            lambda value: _mutate_parent_row(value, "features", [9.9] * 10),
        ),
        (
            "candidate-parent-full-export-ts-code-drift",
            "factor_v2_parent",
            lambda value: _mutate_parent_row(value, "ts_code", "999999.BJ"),
        ),
        *[
            (
                f"candidate-parent-{field}-drift",
                "factor_v2_parent",
                lambda value, field=field: value["derived_adapter"].update(
                    {field: "0" * 64}
                ),
            )
            for field in activation.PARENT_PROJECTION_FIELDS
        ],
        *[
            (
                f"candidate-parent-{field}-contract-drift",
                "factor_v2_parent",
                lambda value, field=field: value["derived_adapter"].update(
                    {field: ["wrong"]}
                ),
            )
            for field in (
                "candidate_key_projection_fields",
                "candidate_key_order",
                "source_feature_projection_fields",
                "source_feature_order",
                "full_export_fields",
                "full_export_order",
            )
        ],
        *[
            (
                f"candidate-parent-receipt-{field}-drift",
                "factor_v2_parent",
                lambda value, field=field: value[
                    "parent_hash_binding_receipt"
                ].update({field: "0" * 64}),
            )
            for field in activation.PARENT_PROJECTION_FIELDS
        ],
        *[
            (
                f"candidate-evaluation-source-{field}-drift",
                "factor_v2_evaluation",
                lambda value, field=field: value.update({field: "0" * 64}),
            )
            for field in (
                "branch_receipt_sha256",
                "decision_receipt_sha256",
                "decision_receipt_raw_file_sha256",
                "evaluation_artifact_sha256",
            )
        ],
        (
            "candidate-evaluation-selected-branch-drift",
            "factor_v2_evaluation",
            lambda value: value.update({"selected_branch": "wrong"}),
        ),
        (
            "candidate-evaluation-schema-drift",
            "factor_v2_evaluation",
            lambda value: value.update({"schema": "wrong"}),
        ),
        (
            "candidate-evaluation-extra-field",
            "factor_v2_evaluation",
            lambda value: value.update({"unbound_field": "wrong"}),
        ),
        (
            "candidate-evaluation-missing-field",
            "factor_v2_evaluation",
            lambda value: value.pop("decision_receipt_raw_file_sha256"),
        ),
    ]
    for name, snapshot, mutate in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_candidate_snapshot(fixture, snapshot, mutate)
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_independent_core_replays_calendar_and_board_daily_cross_binding(
    tmp_path: Path,
) -> None:
    def drift_board_daily_pair(value: dict[str, Any]) -> None:
        value["per_date"][0]["raw_source_rows_sha256"] = _sha("drifted-raw")
        value["per_date_board_ledger_root_sha256"] = _sha(value["per_date"])

    def invalid_calendar_date(value: dict[str, Any]) -> None:
        old = value["all_market_sessions"][0]
        for rows_field, sha_field in (
            ("all_market_sessions", "all_market_sessions_sha256"),
            ("prewindow_sessions", "prewindow_sessions_sha256"),
            ("source_dates", "source_dates_sha256"),
        ):
            rows = value[rows_field]
            rows[rows.index(old)] = "2023-01-00"
            value[sha_field] = _sha(rows)

    cases: tuple[
        tuple[str, str, Callable[[dict[str, Any]], None]], ...
    ] = (
        (
            "board-daily-cross-binding",
            "upstream_board_ledger",
            drift_board_daily_pair,
        ),
        (
            "calendar-schema",
            "calendar",
            lambda value: value.update({"schema": "wrong"}),
        ),
        (
            "calendar-extra-field",
            "calendar",
            lambda value: value.update({"unexpected": False}),
        ),
        ("calendar-invalid-date", "calendar", invalid_calendar_date),
    )
    for name, snapshot, mutate in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_candidate_snapshot(fixture, snapshot, mutate)
        if name == "board-daily-cross-binding":
            board = _read(
                fixture["paths"]["candidate_snapshot_root"]
                / "upstream_board_ledger.json"
            )
            _rewrite_receipt(
                fixture,
                "parent",
                lambda parent: parent["upstream_board_projection"].update(
                    {
                        "upstream_board_ledger_root_sha256": board[
                            "per_date_board_ledger_root_sha256"
                        ]
                    }
                ),
            )
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError
        ):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )
        replay_kwargs = {
            key: value
            for key, value in fixture["kwargs"].items()
            if key != "output_root"
        }
        expected_error = (
            "candidate board/daily receipt per-date binding rejected"
            if name == "board-daily-cross-binding"
            else None
        )
        with pytest.raises(
            independent_core.IndependentCoreError,
            match=expected_error,
        ):
            independent_core.replay_inputs(**replay_kwargs)

    non_unit_fixture = _fixture(tmp_path / "board-non-unit-positive-counts")

    def use_non_unit_board_counts(value: dict[str, Any]) -> None:
        for entry in value["per_date"]:
            entry["segment_counts"]["BSE"] = 2
        value["pre_filter_segment_counts"]["BSE"] = 2 * len(value["per_date"])
        value["per_date_board_ledger_root_sha256"] = _sha(value["per_date"])

    _rewrite_candidate_snapshot(
        non_unit_fixture,
        "upstream_board_ledger",
        use_non_unit_board_counts,
    )
    candidate = independent_core._load_candidate_container(
        candidate_output_root=non_unit_fixture["kwargs"]["candidate_output_root"],
        candidate_publication_path=non_unit_fixture["kwargs"][
            "candidate_publication_path"
        ],
        expected_candidate_publication_sha256=non_unit_fixture["kwargs"][
            "expected_candidate_publication_sha256"
        ],
    )
    independent_core._replay_candidate_semantics(candidate)


def test_differential_core_rejects_daily_basic_scope_and_eligibility_drift(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    source_receipt = _read(fixture["paths"]["daily_basic_receipt"])
    original_snapshot = _read(
        fixture["paths"]["candidate_snapshot_root"]
        / "daily_basic_exact_set_receipt.json"
    )
    false_fields = (
        "arbitrary_row_drops_permitted",
        "embargo_consumed",
        "final_oos_consumed",
        "formal_factor_v3_materialization_performed",
        "production_profile_registered",
        "production_recommendation_eligible",
        "rows_published",
        "silent_row_drops_permitted",
    )
    true_fields = (
        "all_supported_segments_compared_before_scope_filter",
        "exact_set_verified",
        "factor_v3_development_materialization_input_eligible",
        "raw_source_rows_bound",
        "source_ts_code_exact_set_verified_after_transition_filter",
        "transition_resolved_identity_exact_set_verified",
    )
    cases: list[tuple[str, Any]] = [
        *((field, True) for field in false_fields),
        *((field, False) for field in true_fields),
        ("authority_scope", "wrong"),
        ("authority_status", "wrong"),
        ("row_authority_status", "wrong"),
    ]
    for field, value in cases:
        receipt = deepcopy(source_receipt)
        receipt[field] = value
        unsigned = dict(receipt)
        unsigned.pop("authority_root_sha256")
        receipt["authority_root_sha256"] = _sha(unsigned)
        projection = candidate_authority.build_factor_v3_public_source_receipt_projection(
            receipt,
            kind="daily_basic_733_v2",
        )
        snapshot = {
            "derived_adapter": deepcopy(original_snapshot["derived_adapter"]),
            "schema": original_snapshot["schema"],
            **projection,
        }
        with pytest.raises(independent_core.IndependentCoreError):
            independent_core._validate_public_projection(
                source_receipt=receipt,
                snapshot=snapshot,
                kind="daily_basic_733_v2",
                sessions=receipt["trade_dates"],
            )


def test_any_candidate_or_upstream_safety_true_is_rejected_not_silently_zeroed(
    tmp_path: Path,
) -> None:
    for artifact in ("parent", "evaluation"):
        for field in activation.SAFETY_FALSE_FIELDS:
            fixture = _fixture(tmp_path / artifact / field)
            _rewrite_receipt(
                fixture,
                artifact,
                lambda value, field=field: value.update({field: True}),
            )
            with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
                activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                    **fixture["kwargs"]
                )
    for artifact, rewrite in (
        ("candidate-manifest", _rewrite_candidate_manifest),
        ("candidate-descriptor", _rewrite_candidate_descriptor),
    ):
        for field in candidate_authority.SAFETY_FALSE_FIELDS:
            fixture = _fixture(tmp_path / artifact / field)
            rewrite(
                fixture,
                lambda value, field=field: value.update({field: True}),
            )
            with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
                activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                    **fixture["kwargs"]
                )


def test_input_hash_paths_and_root_separation_fail_closed(
    tmp_path: Path,
) -> None:
    expected_sha_fields = (
        "expected_candidate_publication_sha256",
        "expected_parent_source_authority_receipt_sha256",
        "expected_parent_source_terminal_epoch_receipt_sha256",
        "expected_factor_v2_evaluation_authority_receipt_sha256",
    )
    for field in expected_sha_fields:
        fixture = _fixture(tmp_path / field)
        fixture["kwargs"][field] = "0" * 64
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    for name, output_root in (
        ("same-as-candidate", lambda value: value["candidate_output_root"]),
        (
            "ancestor-of-candidate",
            lambda value: Path(value["candidate_output_root"]).parent,
        ),
        (
            "nested-under-candidate",
            lambda value: Path(value["candidate_output_root"]) / "nested-output",
        ),
        (
            "same-as-authority-root",
            lambda value: Path(value["parent_source_authority_receipt_path"]).parents[3],
        ),
        (
            "nested-under-authority-root",
            lambda value: Path(value["parent_source_authority_receipt_path"])
            .parents[3]
            / "nested-output",
        ),
        (
            "same-as-epoch-root",
            _global_attempt_ledger_root,
        ),
        (
            "nested-under-epoch-root",
            lambda value: _global_attempt_ledger_root(value) / "nested-output",
        ),
    ):
        fixture = _fixture(tmp_path / name)
        fixture["kwargs"]["output_root"] = output_root(fixture["kwargs"])
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "loose-candidate-publication")
    loose = tmp_path / "loose-candidate-publication" / "candidate" / "loose.json"
    loose.write_bytes(Path(fixture["kwargs"]["candidate_publication_path"]).read_bytes())
    fixture["kwargs"]["candidate_publication_path"] = loose.resolve()
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )

    fixture = _fixture(tmp_path / "loose-candidate-descriptor")
    manifest = _read(fixture["paths"]["candidate_manifest"])
    descriptor = fixture["paths"]["candidate_descriptor"]
    loose_descriptor = Path(fixture["kwargs"]["candidate_output_root"]) / "loose.json"
    loose_descriptor.write_bytes(descriptor.read_bytes())
    manifest["descriptor_relative_path"] = loose_descriptor.relative_to(
        fixture["kwargs"]["candidate_output_root"]
    ).as_posix()
    _replace_candidate_manifest(fixture, manifest)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )

    for artifact, path_field in (
        ("parent", "parent_source_authority_receipt_path"),
        ("epoch", "parent_source_terminal_epoch_receipt_path"),
        ("evaluation", "factor_v2_evaluation_authority_receipt_path"),
    ):
        fixture = _fixture(tmp_path / f"loose-{artifact}-receipt")
        source = fixture["paths"][artifact]
        loose = source.parents[3] / f"loose-{artifact}.json"
        loose.write_bytes(source.read_bytes())
        fixture["kwargs"][path_field] = loose.resolve()
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )



def test_candidate_receipt_and_output_reparse_aliases_are_rejected(
    tmp_path: Path,
) -> None:
    cases = (
        (
            "candidate",
            "candidate_publication_path",
            lambda fixture: Path(fixture["kwargs"]["candidate_output_root"]),
        ),
        (
            "parent-receipt",
            "parent_source_authority_receipt_path",
            lambda fixture: fixture["paths"]["parent"].parents[3],
        ),
        (
            "evaluation-receipt",
            "factor_v2_evaluation_authority_receipt_path",
            lambda fixture: fixture["paths"]["evaluation"].parents[3],
        ),
        (
            "terminal-receipt",
            "parent_source_terminal_epoch_receipt_path",
            lambda fixture: _global_attempt_ledger_root(fixture["kwargs"]),
        ),
    )
    for name, path_field, root_from_fixture in cases:
        fixture = _fixture(tmp_path / name)
        root = root_from_fixture(fixture)
        source = Path(fixture["kwargs"][path_field])
        alias = root.parent / f"{root.name}-{name}-alias"
        _directory_alias(alias, root)
        fixture["kwargs"][path_field] = alias.joinpath(
            *source.relative_to(root).parts
        )
        if name == "candidate":
            fixture["kwargs"]["candidate_output_root"] = alias
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation._publish_disposable_test_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "output-root")
    real_output = Path(fixture["kwargs"]["output_root"])
    real_output.mkdir(parents=True)
    output_alias = real_output.parent / "activation-output-alias"
    _directory_alias(output_alias, real_output)
    fixture["kwargs"]["output_root"] = output_alias
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


def test_activation_publication_and_verifier_reparse_aliases_are_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    publication = activation._publish_disposable_test_factor_v3_formal_development_input_activation(
        **fixture["kwargs"]
    )
    output_root = Path(fixture["kwargs"]["output_root"])
    publication_path = _publication_path(output_root, publication)
    verify_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    output_alias = tmp_path / "activation-publication-alias"
    _directory_alias(output_alias, output_root)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs,
            activation_publication_path=output_alias.joinpath(
                *publication_path.relative_to(output_root).parts
            ),
            expected_activation_publication_sha256=publication["publication_sha256"],
            verifier_output_root=(tmp_path / "verifier").resolve(),
        )

    real_verifier = tmp_path / "real-verifier"
    real_verifier.mkdir()
    verifier_alias = tmp_path / "verifier-alias"
    _directory_alias(verifier_alias, real_verifier)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs,
            activation_publication_path=publication_path,
            expected_activation_publication_sha256=publication["publication_sha256"],
            verifier_output_root=verifier_alias,
        )


def test_independent_verifier_receipt_is_path_based_cas_and_create_once(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    publication = activation._publish_disposable_test_factor_v3_formal_development_input_activation(
        **fixture["kwargs"]
    )
    output_root = Path(fixture["kwargs"]["output_root"])
    publication_path = _publication_path(output_root, publication)
    publication_sha = publication["publication_sha256"]
    verify_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    verifier_root = (tmp_path / "independent-verifier").resolve()
    verify_kwargs.update(
        {
            "activation_publication_path": publication_path.resolve(),
            "expected_activation_publication_sha256": publication_sha,
            "verifier_output_root": verifier_root,
        }
    )
    result = activation._verify_disposable_test_factor_v3_formal_development_input_activation(
        **verify_kwargs
    )
    assert result["schema"] == activation.INDEPENDENT_VERIFIER_RECEIPT_SCHEMA
    receipt_path = verifier_root.joinpath(*result["receipt_relative_path"].split("/"))
    original_raw = receipt_path.read_bytes()
    assert receipt_path.name == f"{result['receipt_file_sha256']}.json"
    assert "sha256" in receipt_path.parts
    assert _sha(original_raw) == result["receipt_file_sha256"]
    receipt = _read(receipt_path)
    publication_manifest = _read(publication_path)
    assert receipt["verified"] is False
    assert receipt["independent_public_replay_performed"] is False
    assert receipt["differential_contract_replay_performed"] is True
    assert receipt["formal_materialization_eligible"] is False
    assert receipt["formal_materialization_performed"] is False
    assert receipt["activation_publication_file_sha256"] == publication_sha
    assert receipt["activation_root_sha256"] == publication_manifest[
        "activation_root_sha256"
    ]
    assert receipt["activation_descriptor_file_sha256"] == publication_manifest[
        "descriptor_sha256"
    ]
    assert receipt["replayed_input_bindings"] == _expected_activation_input_bindings(
        fixture
    )
    assert all(receipt[field] is False for field in activation.SAFETY_FALSE_FIELDS)
    assert (
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
        == result
    )
    assert receipt_path.read_bytes() == original_raw
    receipt_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
    assert receipt_path.read_bytes() == b'{"drifted":true}'
    receipt_path.write_bytes(original_raw)
    descriptor_path = output_root.joinpath(
        *publication_manifest["descriptor_relative_path"].split("/")
    )
    descriptor_raw = descriptor_path.read_bytes()
    descriptor_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
    assert descriptor_path.read_bytes() == b'{"drifted":true}'
    descriptor_path.write_bytes(descriptor_raw)
    publication_raw = publication_path.read_bytes()
    publication_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
    assert publication_path.read_bytes() == b'{"drifted":true}'
    publication_path.write_bytes(publication_raw)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **{
                **verify_kwargs,
                "expected_activation_publication_sha256": "0" * 64,
            }
        )
    loose_publication = output_root / "loose-activation-publication.json"
    loose_publication.write_bytes(publication_raw)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **{
                **verify_kwargs,
                "activation_publication_path": loose_publication,
            }
        )
    loose_descriptor = output_root / "loose-input-authority.json"
    loose_descriptor.write_bytes(descriptor_raw)
    forged_manifest = deepcopy(publication_manifest)
    forged_manifest["descriptor_relative_path"] = loose_descriptor.relative_to(
        output_root
    ).as_posix()
    forged_publication_sha = _sha(forged_manifest)
    forged_publication_path = (
        output_root
        / "publications"
        / "sha256"
        / forged_publication_sha[:2]
        / f"{forged_publication_sha}.json"
    )
    assert _write(forged_publication_path, forged_manifest) == forged_publication_sha
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation._verify_disposable_test_factor_v3_formal_development_input_activation(
            **{
                **verify_kwargs,
                "activation_publication_path": forged_publication_path,
                "expected_activation_publication_sha256": forged_publication_sha,
            }
        )
    parent = _read(fixture["paths"]["parent"])
    candidate_root = Path(fixture["kwargs"]["candidate_output_root"])
    authority_root = fixture["paths"]["parent"].parents[3]
    epoch_root = Path(parent["global_attempt_ledger_root"])
    overlap_roots = (
        output_root,
        output_root / "nested-verifier",
        output_root.parent,
        candidate_root,
        candidate_root / "nested-verifier",
        authority_root,
        authority_root / "nested-verifier",
        epoch_root,
        epoch_root / "nested-verifier",
    )
    for overlap_root in overlap_roots:
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError,
            match="independent|overlap|root",
        ):
            activation._verify_disposable_test_factor_v3_formal_development_input_activation(
                **{**verify_kwargs, "verifier_output_root": overlap_root.resolve()}
            )


def test_atomic_create_only_never_exposes_partial_final_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = (tmp_path / "cas" / "sha256" / "aa" / "artifact.json").resolve()

    def fail_link(_source: Any, _destination: Any, **_kwargs: Any) -> None:
        raise OSError("injected no-replace publication failure")

    monkeypatch.setattr(activation.os, "link", fail_link)
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="injected|publication|create-only",
    ):
        activation._write_create_only(target, b'{"complete":true}', label="fixture CAS")
    assert not target.exists()
    assert not list(target.parent.glob(".factor-v3-cas-*.tmp"))


def test_publication_is_terminal_and_not_visible_before_postverification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)

    def fail_postverify(_held: Any) -> None:
        raise activation.FactorV3FormalDevelopmentInputActivationError(
            "injected prepublication postverification failure"
        )

    monkeypatch.setattr(activation, "_postverify_held_inputs", fail_postverify)
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="prepublication",
    ):
        activation._publish_disposable_test_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    publication_root = Path(fixture["kwargs"]["output_root"]) / "publications"
    assert not publication_root.exists() or not list(publication_root.rglob("*.json"))


def test_close_all_attempts_every_handle_before_raising() -> None:
    calls: list[str] = []

    class Handle:
        def __init__(self, name: str, *, fails: bool = False) -> None:
            self.name = name
            self.fails = fails

        def close(self) -> None:
            calls.append(self.name)
            if self.fails:
                raise OSError(f"{self.name} close failed")

    with pytest.raises(OSError, match="second close failed"):
        activation._close_all_held_files(
            [Handle("first"), Handle("second", fails=True), Handle("third")]
        )
    assert calls == ["third", "second", "first"]


def test_independent_verifier_does_not_reuse_publisher_replay_or_builders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = _fixture(tmp_path)
    publication = activation._publish_disposable_test_factor_v3_formal_development_input_activation(
        **fixture["kwargs"]
    )
    publication_path = _publication_path(
        Path(fixture["kwargs"]["output_root"]), publication
    )

    def shared_path_used(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("publisher replay/builder reached by independent verifier")

    for name in (
        "_activation_output_root",
        "_assert_isolated",
        "_descriptor_for",
        "_forbidden_roots",
        "_hold_files",
        "_hold_inputs",
        "_manifest_for",
        "_postverify_activation_output",
        "_postverify_held_inputs",
        "_postverify_inputs",
        "_read_json",
        "_relative_path",
        "_replay_inputs",
        "_safe_output_root",
        "_validate_candidate",
        "_validate_evaluation_receipt",
        "_validate_parent_receipt",
        "_write_create_only",
    ):
        monkeypatch.setattr(activation, name, shared_path_used)
    verifier_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    result = activation._verify_disposable_test_factor_v3_formal_development_input_activation(
        **verifier_kwargs,
        activation_publication_path=publication_path,
        expected_activation_publication_sha256=publication["publication_sha256"],
        verifier_output_root=(tmp_path / "independent-differential").resolve(),
    )
    assert result["verified"] is False
    assert result["independent_public_replay_performed"] is False
    assert result["differential_contract_replay_performed"] is True


def test_independent_verifier_identity_binds_core_source_bytes() -> None:
    from app import (
        factor_v3_formal_development_input_activation_independent_core as core,
    )
    from app import (
        factor_v3_formal_development_input_activation_independent_verifier as independent,
    )

    source = Path(core.__file__).resolve(strict=True)
    original = source.read_bytes()
    before = independent._producer_identity()
    try:
        source.write_bytes(original + b"\n")
        after = independent._producer_identity()
        assert after["root_sha256"] != before["root_sha256"]
        assert (
            after["dependencies"]["independent_core"]["sha256"]
            != before["dependencies"]["independent_core"]["sha256"]
        )
    finally:
        source.write_bytes(original)
    assert independent._producer_identity() == before


def test_real_daily_candidate_to_disposable_differential_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _real_daily_candidate_chain_inputs(tmp_path, monkeypatch)
    daily_publication = daily_authority.publish_factor_v3_daily_basic_733_exact_set_coverage(
        **chain["daily_kwargs"]
    )
    daily_verified = daily_authority.verify_factor_v3_daily_basic_733_exact_set_coverage(
        **chain["daily_kwargs"],
        publication=daily_publication,
    )
    candidate_spec_path, candidate_spec_sha256 = _write_real_candidate_source_spec(
        chain,
        daily_publication,
    )
    candidate_publication = candidate_authority.publish_factor_v3_development_input_authority(
        source_spec_path=candidate_spec_path,
        expected_source_spec_sha256=candidate_spec_sha256,
        output_root=chain["candidate_output_root"],
    )
    candidate_verified = candidate_authority.verify_factor_v3_development_input_authority(
        source_spec_path=candidate_spec_path,
        expected_source_spec_sha256=candidate_spec_sha256,
        output_root=chain["candidate_output_root"],
        publication=candidate_publication,
    )
    candidate_bundle = _real_candidate_activation_bundle(
        chain,
        daily_publication=daily_publication,
        candidate_publication=candidate_publication,
    )
    fixture = _fixture(
        tmp_path / "activation",
        candidate_bundle=candidate_bundle,
    )
    activation_publication = activation._publish_disposable_test_factor_v3_formal_development_input_activation(
        **fixture["kwargs"]
    )
    activation_publication_path = _publication_path(
        Path(fixture["kwargs"]["output_root"]),
        activation_publication,
    )
    verifier_kwargs = {
        key: value
        for key, value in fixture["kwargs"].items()
        if key != "output_root"
    }
    independent = activation._verify_disposable_test_factor_v3_formal_development_input_activation(
        **verifier_kwargs,
        activation_publication_path=activation_publication_path,
        expected_activation_publication_sha256=activation_publication[
            "publication_sha256"
        ],
        verifier_output_root=(tmp_path / "differential-replay").resolve(),
    )

    sessions = chain["sessions"]
    assert daily_verified["verified"] is True
    assert daily_verified["trade_dates"] == sessions
    assert len(sessions[:250]) == 250
    assert len(sessions[250:]) == 483
    daily_receipt_path = Path(chain["daily_kwargs"]["output_root"]).joinpath(
        *daily_publication["receipt_relative_path"].split("/")
    )
    daily_receipt = _read(daily_receipt_path)
    assert _sha(daily_receipt) == daily_publication["receipt_sha256"]
    daily_unsigned = dict(daily_receipt)
    assert daily_unsigned.pop("authority_root_sha256") == _sha(daily_unsigned)
    assert daily_receipt["trade_date_count"] == 733
    assert daily_receipt["trade_dates"] == sessions
    assert daily_receipt["per_date_statistics"][-1]["trade_date"] == sessions[-1]
    for statistic in daily_receipt["per_date_statistics"]:
        assert statistic["daily_basic_raw_segment_counts"] == {
            "BSE": 1,
            "SSE_MAIN": 1,
            "SSE_STAR": 1,
            "SZSE_CHINEXT": 1,
            "SZSE_MAIN": 1,
        }

    candidate_descriptor = _read(candidate_bundle[7]["descriptor"])
    candidate_calendar = _read(
        candidate_bundle[7]["snapshot_root"] / "calendar.json"
    )
    candidate_board = _read(
        candidate_bundle[7]["snapshot_root"] / "upstream_board_ledger.json"
    )
    assert candidate_verified["verified"] is False
    assert candidate_calendar["all_market_session_count"] == 733
    assert candidate_calendar["prewindow_session_count"] == 250
    assert candidate_calendar["development_session_count"] == 483
    assert candidate_calendar["source_date_count"] == 732
    assert candidate_calendar["source_dates"] == sessions[:-1]
    assert candidate_board["source_segments"] == list(
        activation.UPSTREAM_SOURCE_SEGMENTS
    )
    assert len(candidate_board["per_date"]) == 732
    assert all(
        entry["segment_counts"]
        == {
            "BSE": 1,
            "SSE_MAIN": 1,
            "SSE_STAR": 1,
            "SZSE_CHINEXT": 1,
            "SZSE_MAIN": 1,
        }
        for entry in candidate_board["per_date"]
    )
    assert candidate_descriptor["source_authority_complete"] is False
    assert candidate_descriptor["formal_materialization_eligible"] is False
    assert all(
        candidate_descriptor[field] is False
        for field in candidate_authority.SAFETY_FALSE_FIELDS
    )

    activation_manifest = _read(activation_publication_path)
    activation_descriptor = _read(
        Path(fixture["kwargs"]["output_root"]).joinpath(
            *activation_manifest["descriptor_relative_path"].split("/")
        )
    )
    assert activation_descriptor["authority_scope"] == "DISPOSABLE_TEST_FIXTURE_ONLY"
    assert activation_descriptor["test_fixture_only"] is True
    assert activation_descriptor["activation_verified"] is False
    assert activation_descriptor["source_authority_complete"] is False
    assert activation_descriptor["formal_materialization_eligible"] is False
    assert activation_descriptor["verified"] is False
    assert independent["differential_contract_replay_performed"] is True
    assert independent["independent_public_replay_performed"] is False
    assert independent["verified"] is False
    independent_receipt = _read(
        (tmp_path / "differential-replay")
        .resolve()
        .joinpath(*independent["receipt_relative_path"].split("/"))
    )
    assert independent_receipt["test_fixture_only"] is True
    assert independent_receipt["replayed_input_bindings"][
        "daily_basic_authority_receipt_file_sha256"
    ] == daily_publication["receipt_sha256"]
    assert independent_receipt["replayed_input_bindings"][
        "candidate_publication_file_sha256"
    ] == candidate_publication["publication_sha256"]


def test_disposable_public_projection_rejects_real_daily_receipt_schema_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chain = _real_daily_candidate_chain_inputs(tmp_path, monkeypatch)
    publication = daily_authority.publish_factor_v3_daily_basic_733_exact_set_coverage(
        **chain["daily_kwargs"]
    )
    receipt_path = Path(chain["daily_kwargs"]["output_root"]).joinpath(
        *publication["receipt_relative_path"].split("/")
    )
    receipt = _read(receipt_path)

    def missing_field(value: dict[str, Any]) -> None:
        value["per_date_statistics"][0].pop("daily_basic_attempt_sha256")

    def extra_field(value: dict[str, Any]) -> None:
        value["per_date_statistics"][0]["unbound"] = "drift"

    def semantic_drift(value: dict[str, Any]) -> None:
        value["per_date_statistics"][0][
            "source_ts_code_exact_set_verified_after_transition_filter"
        ] = False

    for mutate in (missing_field, extra_field, semantic_drift):
        drifted = deepcopy(receipt)
        mutate(drifted)
        drifted["per_date_statistics_sha256"] = _sha(
            drifted["per_date_statistics"]
        )
        unsigned = dict(drifted)
        unsigned.pop("authority_root_sha256")
        drifted["authority_root_sha256"] = _sha(unsigned)
        projection = candidate_authority.build_factor_v3_public_source_receipt_projection(
            drifted,
            kind="daily_basic_733_v2",
        )
        snapshot = {
            "derived_adapter": {},
            "schema": "factor-v3-development-daily-basic-receipt-snapshot/v2",
            **projection,
        }
        with pytest.raises(ValueError):
            candidate_authority.validate_factor_v3_public_source_receipt_projection(
                source_receipt=drifted,
                projected_snapshot=snapshot,
                kind="daily_basic_733_v2",
                sessions=chain["sessions"],
                formal_schema_required=False,
            )
