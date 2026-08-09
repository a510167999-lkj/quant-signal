from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

import pytest

from app import factor_v3_development_input_authority as candidate_authority
from app import factor_v3_formal_development_input_activation as activation
from app import factor_v3_parent_source_development_authority as parent_authority


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


def _parent_projection() -> tuple[dict[str, Any], dict[str, str]]:
    rows = [
        {
            "candidate_key": "cn-a-share:000001|2025-01-02",
            "features": [0.1] * 10,
            "signal_date": "2025-01-02",
            "ts_code": "000001.SZ",
        },
        {
            "candidate_key": "cn-a-share:600001|2025-01-03",
            "features": [0.2] * 10,
            "signal_date": "2025-01-03",
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


def _candidate_publication(
    root: Path,
) -> tuple[
    Path,
    str,
    dict[str, Any],
    dict[str, str],
    dict[str, str],
    str,
]:
    sessions = [
        (date(2023, 1, 1) + timedelta(days=index)).isoformat()
        for index in range(733)
    ]
    prewindow = sessions[:250]
    development = sessions[250:]
    source_dates = [*prewindow, *development[:-1]]
    parent_rows, parent_projection = _parent_projection()
    evaluation_projection = {
        "schema": activation.FACTOR_V2_EVALUATION_PROJECTION_SCHEMA,
        "terminal_decision_descriptor_sha256": _sha("terminal-decision"),
        "evaluator_descriptor_sha256": _sha("evaluator"),
        "cost_slippage_execution_descriptor_sha256": _sha("cost-slippage"),
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
    parent_snapshot = {
        "derived_adapter": {
            "schema": "factor-v3-factor-v2-parent-row-derived-adapter/v1",
            "candidate_key_projection_fields": ["candidate_key"],
            "candidate_key_order": ["candidate_key"],
            "source_feature_projection_fields": [
                "candidate_key",
                "signal_date",
                "features",
            ],
            "source_feature_order": ["signal_date", "candidate_key"],
            "full_export_fields": [
                "candidate_key",
                "features",
                "signal_date",
                "ts_code",
            ],
            "full_export_order": ["signal_date", "candidate_key"],
            **{field: parent_projection[field] for field in activation.PARENT_PROJECTION_FIELDS},
        },
        "parent_hash_binding_receipt": {
            "authority_status": "PINNED_HASH_MATCH_ONLY_SOURCE_PRODUCER_UNVERIFIED",
            "formal_materialization_eligible": False,
            "source_provenance_verified": False,
            **{field: parent_projection[field] for field in activation.PARENT_PROJECTION_FIELDS},
        },
        "schema": "factor-v3-development-factor-v2-parent-snapshot/v2",
        **parent_rows,
    }
    snapshots: dict[str, dict[str, Any]] = {
        "calendar": calendar,
        "factor_v2_parent": parent_snapshot,
        "daily_basic": {
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
        "factor_v2_evaluation": evaluation_projection,
        "feature_history_receipt": {
            "schema": "factor-v3-development-feature-history-receipt-snapshot/v2",
            "verified": True,
            "prewindow_session_count": 250,
            "prewindow_sessions_sha256": _sha(prewindow),
        },
        "daily_basic_exact_set_receipt": {
            "schema": "factor-v3-development-daily-basic-receipt-snapshot/v2",
            "verified": True,
            "trade_date_count": 733,
            "source_date_count": 732,
            "source_dates_sha256": _sha(source_dates),
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
        "factor_v2_branch": {
            "authority_status": "STRUCTURAL_ADAPTER_ONLY",
            "formal_materialization_eligible": False,
            "verified": False,
        },
        "formal_materialization_eligible": False,
        "parent_source_authority_verified": False,
        "points_contract_sha256": _sha("points-contract"),
        "producer_snapshot": {
            "candidate_snapshot_only": True,
            "root_sha256": _sha("candidate-producer"),
        },
        "schema": candidate_authority.DESCRIPTOR_SCHEMA,
        "snapshots": snapshot_descriptors,
        "source_receipts": {
            "daily_basic": {"verified": True},
            "feature_history": {"verified": True},
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
    return (
        manifest_path,
        manifest_sha,
        parent_projection,
        evaluation_projection,
        {
            "all_market_sessions_sha256": _sha(sessions),
            "prewindow_sessions_sha256": _sha(prewindow),
            "development_sessions_sha256": _sha(development),
            "source_dates_sha256": _sha(source_dates),
        },
        _sha(per_date),
    )


def _self_hashed(payload: dict[str, Any], field: str) -> dict[str, Any]:
    unsigned = deepcopy(payload)
    unsigned.pop(field, None)
    return {**unsigned, field: _sha(unsigned)}


def _fixture(tmp_path: Path) -> dict[str, Any]:
    candidate_root = tmp_path / "candidate"
    (
        candidate_publication_path,
        candidate_publication_sha,
        parent_projection,
        evaluation_projection,
        calendar_roots,
        upstream_board_ledger_root,
    ) = _candidate_publication(candidate_root)
    semantic_root = _sha("parent-semantic-input")
    attempt_key = _sha(
        {
            "schema": parent_authority.ATTEMPT_KEY_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
        }
    )
    global_identity = _sha(
        {
            "attempt_key_sha256": attempt_key,
            "schema": "factor-v3-parent-source-global-attempt-identity/v1",
        }
    )
    ledger_root = (tmp_path / "machine-global-ledger").resolve()
    claim_root = ledger_root / "attempts" / "sha256" / attempt_key[:2] / attempt_key
    run_claim_path = claim_root / "run.claim.json"
    verify_claim_path = claim_root / "verify.claim.json"
    terminal_epoch = _self_hashed(
        {
            "action": activation.PARENT_SOURCE_ROOT_ACTION_VERIFY,
            "approved_transition": activation.PARENT_SOURCE_ROOT_TRANSITION_START_VERIFY,
            "attempt_key_sha256": attempt_key,
            "development_only": True,
            "global_attempt_identity_sha256": global_identity,
            "global_attempt_ledger_root": str(ledger_root),
            "global_run_claim_path": str(run_claim_path),
            "global_verify_claim_path": str(verify_claim_path),
            "machine_global_root_lease_verified": True,
            "native_lease_identity_sha256": _sha("native-root-lease"),
            "native_lease_policy_version": "factor-v3-parent-source-machine-global-root-lease/v1",
            "observed_root_state": activation.PARENT_SOURCE_ROOT_STATE_COMPLETED,
            "root_epoch_terminal_verified": True,
            "run_claim_sha256": _sha("run-claim"),
            "run_spec_sha256": _sha("parent-run-spec"),
            "schema": activation.PARENT_SOURCE_TERMINAL_EPOCH_RECEIPT_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
            "single_attempt_verified": True,
            "terminal_root_state": activation.PARENT_SOURCE_ROOT_STATE_TERMINAL,
            "verified": True,
            "verify_claim_sha256": _sha("verify-claim"),
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    terminal_epoch_path = tmp_path / "authority" / "terminal-epoch.json"
    terminal_epoch_file_sha = _write(terminal_epoch_path, terminal_epoch)
    parent_receipt = _self_hashed(
        {
            "attempt_key_sha256": attempt_key,
            "authority_status": "VERIFIED_DEVELOPMENT_PARENT_SOURCE_AUTHORITY",
            "calendar_projection": {
                **activation.EXACT_CALENDAR_COUNTS,
                **calendar_roots,
            },
            "development_only": True,
            "formal_materialization_eligible": True,
            "global_attempt_identity_sha256": global_identity,
            "independent_public_replay_performed": True,
            "machine_global_root_lease_verified": True,
            "parent_projection": parent_projection,
            "parent_source_authority_verified": True,
            "points_contract_common_eligible_projection": {
                "common_eligible_candidate_keys_sha256": parent_projection[
                    "candidate_keys_sha256"
                ],
                "common_eligible_source_feature_rows_sha256": parent_projection[
                    "source_feature_projection_rows_sha256"
                ],
            },
            "root_epoch_terminal_verified": True,
            "schema": parent_authority.AUTHORITY_RECEIPT_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
            "source_authority_complete": True,
            "source_authority_verified": True,
            "terminal_epoch_receipt_file_sha256": terminal_epoch_file_sha,
            "terminal_epoch_receipt_root_sha256": terminal_epoch[
                "receipt_root_sha256"
            ],
            "upstream_board_projection": {
                "downstream_scope_filter": "mainboard_chinext_candidate_join_only",
                "preserved_before_target_scope_filter": True,
                "source_date_count": 732,
                "source_segments": list(activation.UPSTREAM_SOURCE_SEGMENTS),
                "upstream_board_ledger_root_sha256": upstream_board_ledger_root,
            },
            "verified": True,
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    parent_receipt_path = tmp_path / "authority" / "parent-source.json"
    parent_receipt_file_sha = _write(parent_receipt_path, parent_receipt)
    evaluation_receipt = _self_hashed(
        {
            "authority_status": "VERIFIED_FORMAL_DEVELOPMENT_EVALUATOR_AUTHORITY",
            "development_only": True,
            "evaluation_projection": evaluation_projection,
            "formal_materialization_eligible": True,
            "independent_public_replay_performed": True,
            "publisher_terminal_chain_verified": True,
            "schema": activation.FACTOR_V2_EVALUATION_AUTHORITY_RECEIPT_SCHEMA,
            "source_authority_complete": True,
            "verified": True,
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    evaluation_receipt_path = tmp_path / "authority" / "evaluation.json"
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
        "output_root": (tmp_path / "activation-output").resolve(),
    }
    return {
        "kwargs": kwargs,
        "parent_projection": parent_projection,
        "evaluation_projection": evaluation_projection,
        "calendar_roots": calendar_roots,
        "upstream_board_ledger_root_sha256": upstream_board_ledger_root,
        "paths": {
            "parent": parent_receipt_path,
            "epoch": terminal_epoch_path,
            "evaluation": evaluation_receipt_path,
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
    payload = _self_hashed(payload, "receipt_root_sha256")
    file_sha = _write(path, payload)
    kwargs = fixture["kwargs"]
    expected_fields = {
        "parent": "expected_parent_source_authority_receipt_sha256",
        "epoch": "expected_parent_source_terminal_epoch_receipt_sha256",
        "evaluation": "expected_factor_v2_evaluation_authority_receipt_sha256",
    }
    kwargs[expected_fields[artifact]] = file_sha
    if artifact == "epoch":
        parent = _read(fixture["paths"]["parent"])
        parent["terminal_epoch_receipt_file_sha256"] = file_sha
        parent["terminal_epoch_receipt_root_sha256"] = payload["receipt_root_sha256"]
        parent = _self_hashed(parent, "receipt_root_sha256")
        kwargs["expected_parent_source_authority_receipt_sha256"] = _write(
            fixture["paths"]["parent"], parent
        )


def _publication_path(output_root: Path, publication: dict[str, Any]) -> Path:
    return output_root.joinpath(*publication["publication_relative_path"].split("/"))


def test_valid_activation_projects_only_verified_formal_development_input(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    rerooted = _fixture(tmp_path / "rerooted")
    epoch = _read(fixture["paths"]["epoch"])
    rerooted_epoch = _read(rerooted["paths"]["epoch"])
    assert epoch["attempt_key_sha256"] == rerooted_epoch["attempt_key_sha256"]
    assert (
        epoch["global_attempt_identity_sha256"]
        == rerooted_epoch["global_attempt_identity_sha256"]
    )
    assert epoch["global_run_claim_path"] != rerooted_epoch["global_run_claim_path"]
    publication = activation.publish_factor_v3_formal_development_input_activation(
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
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
        == publication
    )
    assert publication_path.read_bytes() == publication_raw
    manifest = _read(publication_path)
    descriptor_path = output_root.joinpath(
        *manifest["descriptor_relative_path"].split("/")
    )
    descriptor = _read(descriptor_path)
    assert descriptor["schema_version"] == activation.MATERIALIZER_INPUT_AUTHORITY_SCHEMA
    assert descriptor["authority_status"] == "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT"
    assert descriptor["verified"] is True
    assert descriptor["formal_materialization_eligible"] is True
    assert descriptor["formal_materialization_performed"] is False
    assert descriptor["parent_source_authority_verified"] is True
    assert descriptor["machine_global_root_lease_verified"] is True
    assert descriptor["root_epoch_terminal_verified"] is True
    assert descriptor["parent_projection"] == fixture["parent_projection"]
    assert descriptor["factor_v2_evaluation_projection"] == fixture[
        "evaluation_projection"
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


def test_candidate_unverified_or_nonterminal_authority_cannot_promote(
    tmp_path: Path,
) -> None:
    cases: list[tuple[str, str, Callable[[dict[str, Any]], None]]] = [
        (
            "candidate-parent-receipt",
            "parent",
            lambda value: value.update(
                {
                    "authority_status": "CANDIDATE_AUTHORITY_CONTRACT_ONLY",
                    "formal_materialization_eligible": False,
                    "parent_source_authority_verified": False,
                    "source_authority_complete": False,
                    "source_authority_verified": False,
                    "verified": False,
                }
            ),
        ),
        (
            "caller-all-true-without-independent-replay",
            "parent",
            lambda value: value.update({"independent_public_replay_performed": False}),
        ),
        (
            "machine-global-lease-not-verified",
            "epoch",
            lambda value: value.update({"machine_global_root_lease_verified": False}),
        ),
        (
            "root-epoch-not-terminal",
            "epoch",
            lambda value: value.update(
                {
                    "root_epoch_terminal_verified": False,
                    "terminal_root_state": activation.PARENT_SOURCE_ROOT_STATE_COMPLETED,
                }
            ),
        ),
        (
            "unverified-evaluator-adapter",
            "evaluation",
            lambda value: value.update(
                {
                    "authority_status": "UNVERIFIED_DEVELOPMENT_ADAPTER",
                    "formal_materialization_eligible": False,
                    "publisher_terminal_chain_verified": False,
                    "source_authority_complete": False,
                    "verified": False,
                }
            ),
        ),
    ]
    for name, artifact, mutate in cases:
        fixture = _fixture(tmp_path / name)
        _rewrite_receipt(fixture, artifact, mutate)
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError,
            match="authority|lease|epoch|terminal|verified|replay",
        ):
            activation.publish_factor_v3_formal_development_input_activation(
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
            "source-feature-field-order",
            lambda value: value["parent_projection"]["contract"][
                "source_feature_projection_rows"
            ].update({"fields": ["candidate_key", "features", "signal_date"]}),
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
        with pytest.raises(
            activation.FactorV3FormalDevelopmentInputActivationError,
            match="projection|calendar|250|483|733|732|BSE|STAR|filter",
        ):
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )


def test_independent_verifier_receipt_is_path_based_cas_and_create_once(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    output_root = tmp_path / "already-activated"
    descriptor = {
        "schema_version": activation.MATERIALIZER_INPUT_AUTHORITY_SCHEMA,
        "authority_status": "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT",
        "development_only": True,
        "formal_materialization_eligible": True,
        "verified": True,
        "parent_projection": fixture["parent_projection"],
        "factor_v2_evaluation_projection": fixture["evaluation_projection"],
        "calendar": {
            **activation.EXACT_CALENDAR_COUNTS,
            **fixture["calendar_roots"],
        },
        "machine_global_root_lease_verified": True,
        "root_epoch_terminal_verified": True,
        **_false_scope(activation.SAFETY_FALSE_FIELDS),
    }
    descriptor_path = output_root / "inputs" / "input-authority.json"
    descriptor_sha = _write(descriptor_path, descriptor)
    manifest = {
        "activation_root_sha256": _sha(descriptor),
        "descriptor_relative_path": "inputs/input-authority.json",
        "descriptor_sha256": descriptor_sha,
        "formal_materialization_eligible": True,
        "schema": activation.ACTIVATION_SCHEMA,
        "verified": True,
        **_false_scope(activation.SAFETY_FALSE_FIELDS),
    }
    publication_path = output_root / "publications" / "activation.json"
    publication_sha = _write(publication_path, manifest)
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
    result = activation.verify_factor_v3_formal_development_input_activation(
        **verify_kwargs
    )
    assert result["schema"] == activation.INDEPENDENT_VERIFIER_RECEIPT_SCHEMA
    receipt_path = verifier_root.joinpath(*result["receipt_relative_path"].split("/"))
    original_raw = receipt_path.read_bytes()
    assert _sha(original_raw) == result["receipt_file_sha256"]
    receipt = _read(receipt_path)
    assert receipt["verified"] is True
    assert receipt["independent_public_replay_performed"] is True
    assert receipt["formal_materialization_eligible"] is True
    assert receipt["formal_materialization_performed"] is False
    assert all(receipt[field] is False for field in activation.SAFETY_FALSE_FIELDS)
    assert (
        activation.verify_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
        == result
    )
    assert receipt_path.read_bytes() == original_raw
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="independent|overlap|root",
    ):
        activation.verify_factor_v3_formal_development_input_activation(
            **{**verify_kwargs, "verifier_output_root": output_root.resolve()}
        )
