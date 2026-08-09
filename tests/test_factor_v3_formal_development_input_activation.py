from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

import pytest

from app import factor_v3_development_input_authority as candidate_authority
from app import factor_v3_formal_development_input_activation as activation
from app import factor_v3_parent_source_development_authority as parent_authority


@pytest.fixture(autouse=True)
def _explicit_native_parent_source_test_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authority = object()
    monkeypatch.setattr(
        activation,
        "_DISPOSABLE_TESTING_NATIVE_PARENT_SOURCE_AUTHORITY",
        authority,
        raising=False,
    )
    monkeypatch.setattr(
        activation,
        "_REGISTERED_NATIVE_PARENT_SOURCE_AUTHORITY",
        authority,
        raising=False,
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
    evaluation_projection = {
        "schema": activation.FACTOR_V2_EVALUATION_PROJECTION_SCHEMA,
        "terminal_decision_descriptor_sha256": _sha("terminal-decision"),
        "evaluator_descriptor_sha256": _sha("evaluator"),
        "cost_slippage_execution_descriptor_sha256": _sha("cost-slippage"),
    }
    evaluation_source_binding = {
        "branch_receipt_sha256": _sha("candidate-branch-receipt"),
        "decision_receipt_sha256": _sha("candidate-decision-receipt"),
        "decision_receipt_raw_file_sha256": _sha("candidate-decision-file"),
        "evaluation_artifact_sha256": _sha("candidate-evaluation-artifact"),
        "selected_branch": "shallow_gbdt",
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
        },
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
        evaluation_source_binding,
        calendar_roots,
        upstream_board_ledger_root,
        candidate_paths,
    ) = _candidate_publication(candidate_root)
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
            "authority_status": "VERIFIED_DEVELOPMENT_PARENT_SOURCE_AUTHORITY",
            "calendar_projection": {
                **activation.EXACT_CALENDAR_COUNTS,
                **calendar_roots,
            },
            "development_only": True,
            "formal_materialization_eligible": True,
            "global_attempt_identity_sha256": global_identity,
            "global_attempt_ledger_root": str(ledger_root),
            "global_run_claim_path": str(run_claim_path),
            "global_run_receipt_path": str(run_receipt_path),
            "global_terminal_receipt_path": str(terminal_epoch_path),
            "global_verify_claim_path": str(verify_claim_path),
            "independent_public_replay_performed": True,
            "machine_global_root_lease_verified": True,
            "native_lease_identity_sha256": _sha("native-root-lease"),
            "native_lease_policy_version": (
                activation.PARENT_SOURCE_NATIVE_LEASE_POLICY_VERSION
            ),
            "observed_root_state": activation.PARENT_SOURCE_ROOT_STATE_RUN_COMPLETED,
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
            "requested_action": activation.PARENT_SOURCE_ROOT_ACTION_VERIFY,
            "root_epoch_terminal_verified": True,
            "run_claim_sha256": run_claim_sha256,
            "run_receipt_sha256": run_receipt_sha256,
            "run_spec_sha256": run_spec_sha256,
            "schema": parent_authority.AUTHORITY_RECEIPT_SCHEMA,
            "semantic_input_root_sha256": semantic_root,
            "single_attempt_verified": True,
            "source_authority_complete": True,
            "source_authority_verified": True,
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
            "verified": True,
            "verify_claim_sha256": verify_claim_sha256,
            **_false_scope(activation.SAFETY_FALSE_FIELDS),
        },
        "receipt_root_sha256",
    )
    parent_receipt_file_sha = _sha(parent_receipt)
    parent_receipt_path = (
        tmp_path
        / "authority"
        / "parent-source-receipts"
        / "sha256"
        / parent_receipt_file_sha[:2]
        / f"{parent_receipt_file_sha}.json"
    )
    parent_receipt_file_sha = _write(parent_receipt_path, parent_receipt)
    evaluation_receipt = _self_hashed(
        {
            "authority_status": "VERIFIED_FORMAL_DEVELOPMENT_EVALUATOR_AUTHORITY",
            "development_only": True,
            "evaluation_projection": evaluation_projection,
            "candidate_evaluation_binding": evaluation_source_binding,
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
    evaluation_receipt_file_sha = _sha(evaluation_receipt)
    evaluation_receipt_path = (
        tmp_path
        / "authority"
        / "evaluation-receipts"
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
        "factor_v2_evaluation_authority_receipt_file_sha256": fixture["kwargs"][
            "expected_factor_v2_evaluation_authority_receipt_sha256"
        ],
        "factor_v2_evaluation_authority_receipt_root_sha256": evaluation[
            "receipt_root_sha256"
        ],
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
    assert manifest["formal_materialization_eligible"] is True
    assert all(manifest[field] is False for field in activation.SAFETY_FALSE_FIELDS)
    descriptor_path = output_root.joinpath(
        *manifest["descriptor_relative_path"].split("/")
    )
    assert "sha256" in descriptor_path.parts
    assert descriptor_path.name == "input-authority.json"
    assert descriptor_path.parent.name == manifest["activation_root_sha256"]
    assert descriptor_path.parent.parent.name == manifest["activation_root_sha256"][:2]
    descriptor = _read(descriptor_path)
    assert descriptor["schema_version"] == activation.MATERIALIZER_INPUT_AUTHORITY_SCHEMA
    assert descriptor["authority_status"] == "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT"
    assert descriptor["verified"] is True
    assert descriptor["formal_materialization_eligible"] is True
    assert descriptor["formal_materialization_performed"] is False
    assert descriptor["parent_source_authority_verified"] is True
    assert descriptor["machine_global_root_lease_verified"] is True
    assert descriptor["root_epoch_terminal_verified"] is True
    assert descriptor["input_bindings"] == _expected_activation_input_bindings(
        fixture
    )
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
    independent = activation.verify_factor_v3_formal_development_input_activation(
        **verifier_kwargs,
        activation_publication_path=publication_path,
        expected_activation_publication_sha256=publication["publication_sha256"],
        verifier_output_root=(tmp_path / "independent-replay").resolve(),
    )
    assert independent["verified"] is True
    assert independent["independent_public_replay_performed"] is True
    publication_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )
    assert publication_path.read_bytes() == b'{"drifted":true}'
    publication_path.write_bytes(publication_raw)
    descriptor_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
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
        "_REGISTERED_NATIVE_PARENT_SOURCE_AUTHORITY",
        None,
    )
    with pytest.raises(
        activation.FactorV3FormalDevelopmentInputActivationError,
        match="native.*authority|authority.*unavailable",
    ):
        activation.publish_factor_v3_formal_development_input_activation(
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
                f"parent-{field}-false",
                "parent",
                lambda value, field=field: value.update({field: False}),
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
            "caller-all-true-without-independent-replay",
            "parent",
            lambda value: value.update({"independent_public_replay_performed": False}),
        ),
        (
            "parent-authority-schema-drift",
            "parent",
            lambda value: value.update({"schema": "wrong"}),
        ),
        (
            "machine-global-lease-not-verified",
            "parent",
            lambda value: value.update({"machine_global_root_lease_verified": False}),
        ),
        (
            "root-epoch-terminal-boolean-false",
            "parent",
            lambda value: value.update({"root_epoch_terminal_verified": False}),
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
            "parent-verified-false",
            "parent",
            lambda value: value.update({"verified": False}),
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
            "parent-single-attempt-false",
            "parent",
            lambda value: value.update({"single_attempt_verified": False}),
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
                f"evaluation-{field}-false",
                "evaluation",
                lambda value, field=field: value.update({field: False}),
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
            activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    for name in chain:
        fixture = _fixture(tmp_path / f"missing-{name}")
        fixture["paths"]["epoch_chain"][name].unlink()
        with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "self-consistent-underived-global-identity")
    _rewrite_epoch_identity(
        fixture,
        "global_attempt_identity_sha256",
        "0" * 64,
    )
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )

    fixture = _fixture(tmp_path / "extra-epoch-file")
    extra = fixture["paths"]["epoch"].parent / "unexpected.json"
    _write(extra, {"schema": "unbound"})
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
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
                activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
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
                activation.publish_factor_v3_formal_development_input_activation(
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
                activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "loose-candidate-publication")
    loose = tmp_path / "loose-candidate-publication" / "candidate" / "loose.json"
    loose.write_bytes(Path(fixture["kwargs"]["candidate_publication_path"]).read_bytes())
    fixture["kwargs"]["candidate_publication_path"] = loose.resolve()
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
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
        activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
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
            activation.publish_factor_v3_formal_development_input_activation(
                **fixture["kwargs"]
            )

    fixture = _fixture(tmp_path / "output-root")
    real_output = Path(fixture["kwargs"]["output_root"])
    real_output.mkdir(parents=True)
    output_alias = real_output.parent / "activation-output-alias"
    _directory_alias(output_alias, real_output)
    fixture["kwargs"]["output_root"] = output_alias
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.publish_factor_v3_formal_development_input_activation(
            **fixture["kwargs"]
        )


def test_activation_publication_and_verifier_reparse_aliases_are_rejected(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    publication = activation.publish_factor_v3_formal_development_input_activation(
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
        activation.verify_factor_v3_formal_development_input_activation(
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
        activation.verify_factor_v3_formal_development_input_activation(
            **verify_kwargs,
            activation_publication_path=publication_path,
            expected_activation_publication_sha256=publication["publication_sha256"],
            verifier_output_root=verifier_alias,
        )


def test_independent_verifier_receipt_is_path_based_cas_and_create_once(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    publication = activation.publish_factor_v3_formal_development_input_activation(
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
    result = activation.verify_factor_v3_formal_development_input_activation(
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
    assert receipt["verified"] is True
    assert receipt["independent_public_replay_performed"] is True
    assert receipt["formal_materialization_eligible"] is True
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
        activation.verify_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
        == result
    )
    assert receipt_path.read_bytes() == original_raw
    receipt_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.verify_factor_v3_formal_development_input_activation(
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
        activation.verify_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
    assert descriptor_path.read_bytes() == b'{"drifted":true}'
    descriptor_path.write_bytes(descriptor_raw)
    publication_raw = publication_path.read_bytes()
    publication_path.write_bytes(b'{"drifted":true}')
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.verify_factor_v3_formal_development_input_activation(
            **verify_kwargs
        )
    assert publication_path.read_bytes() == b'{"drifted":true}'
    publication_path.write_bytes(publication_raw)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.verify_factor_v3_formal_development_input_activation(
            **{
                **verify_kwargs,
                "expected_activation_publication_sha256": "0" * 64,
            }
        )
    loose_publication = output_root / "loose-activation-publication.json"
    loose_publication.write_bytes(publication_raw)
    with pytest.raises(activation.FactorV3FormalDevelopmentInputActivationError):
        activation.verify_factor_v3_formal_development_input_activation(
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
        activation.verify_factor_v3_formal_development_input_activation(
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
            activation.verify_factor_v3_formal_development_input_activation(
                **{**verify_kwargs, "verifier_output_root": overlap_root.resolve()}
            )
