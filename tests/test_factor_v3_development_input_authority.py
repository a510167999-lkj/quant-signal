from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any

import pytest

from app import audited_pit_factor_v3_points_contract as points
from app import factor_v3_development_input_authority as authority
from app import factor_v3_formal_development_input_activation as activation
from app import jiaoch_daily_basic_exact_set_authority as legacy_daily
from app.jiaoch_points_response_normalization import NormalizedDailyBasicRow


def test_candidate_producer_snapshot_never_claims_git_or_loaded_source_authority() -> None:
    snapshot = authority._candidate_producer_snapshot()

    assert snapshot["schema"] == authority.PRODUCER_SNAPSHOT_SCHEMA
    assert snapshot["candidate_snapshot_only"] is True
    assert "source_head" not in snapshot
    assert "git_executable" not in snapshot
    assert all(snapshot[field] is False for field in authority.PROVENANCE_FALSE_FIELDS)
    assert all(entry["bytes"] > 0 for entry in snapshot["entries"])
    assert all(len(entry["sha256"]) == 64 for entry in snapshot["entries"])


def _bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_bytes(value)).hexdigest()


def _dates(count: int) -> list[str]:
    start = date(2022, 1, 3)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


def _row_database(path: Path, trade_date: str, ts_code: str) -> str:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            CREATE TABLE daily_universe (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                exchange TEXT NOT NULL,
                name TEXT NOT NULL,
                industry TEXT,
                list_date TEXT,
                receipt_dataset TEXT NOT NULL,
                receipt_partition TEXT NOT NULL,
                PRIMARY KEY (trade_date, ts_code)
            );
            CREATE TABLE suspension_events (
                trade_date TEXT NOT NULL,
                ts_code TEXT NOT NULL,
                suspend_timing TEXT NOT NULL,
                suspend_type TEXT NOT NULL,
                receipt_dataset TEXT NOT NULL,
                receipt_partition TEXT NOT NULL,
                PRIMARY KEY (trade_date, ts_code, suspend_type, suspend_timing)
            );
            """
        )
        connection.execute(
            "INSERT INTO daily_universe VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (trade_date, ts_code, "SZ", "fixture", None, "20200101", "bak_basic", trade_date),
        )
        connection.commit()
    finally:
        connection.close()
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _patch_points_contract(
    monkeypatch: pytest.MonkeyPatch,
    *,
    development: list[str],
    parent_rows: list[dict[str, Any]],
) -> None:
    contract = deepcopy(points.FACTOR_V3_POINTS_CONTRACT)
    expectation = contract["preregistered_parent_expectation"]
    source_projection = [
        {
            "candidate_key": row["candidate_key"],
            "features": row["features"],
            "signal_date": row["signal_date"],
        }
        for row in sorted(
            parent_rows,
            key=lambda item: (item["signal_date"], item["candidate_key"]),
        )
    ]
    expectation["common_eligible_candidate_count"] = len(parent_rows)
    expectation["common_eligible_candidate_keys_sha256"] = _sha(
        sorted(row["candidate_key"] for row in parent_rows)
    )
    expectation["common_eligible_source_feature_rows_sha256"] = _sha(source_projection)
    expectation["sessions"] = {
        "count": 483,
        "start": development[0],
        "end": development[-1],
        "sha256": _sha(development),
    }
    contract["preregistered_parent_expectation_sha256"] = _sha(expectation)
    policy = contract["formal_parent_sample_policy"]
    policy["source_candidate_count"] = len(parent_rows)
    policy["source_candidate_keys_sha256"] = expectation[
        "common_eligible_candidate_keys_sha256"
    ]
    policy["source_feature_rows_sha256"] = expectation[
        "common_eligible_source_feature_rows_sha256"
    ]
    monkeypatch.setattr(points, "FACTOR_V3_POINTS_CONTRACT", contract)
    monkeypatch.setattr(points, "FACTOR_V3_POINTS_CONTRACT_SHA256", _sha(contract))
    monkeypatch.setattr(
        points,
        "FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256",
        contract["preregistered_parent_expectation_sha256"],
    )


def _parent_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    development: list[str],
) -> tuple[Path, str, list[dict[str, Any]]]:
    rows = [
        {
            "candidate_key": f"cn-a-share:{code}|{signal_date}",
            "signal_date": signal_date,
            "ts_code": code,
            "features": [float(index + offset) for offset in range(10)],
        }
        for index, signal_date in enumerate(development)
        for code in ("000001.SZ", "300001.SZ")
    ]
    _patch_points_contract(monkeypatch, development=development, parent_rows=rows)
    payload = {
        **points.FACTOR_V3_POINTS_CONTRACT["preregistered_parent_expectation"],
        "rows": rows,
        "rows_sha256": _sha(rows),
        "candidate_identity_root_sha256": _sha(
            [
                {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"]}
                for row in rows
            ]
        ),
    }
    path = tmp_path / "parent.json"
    raw = _bytes(payload)
    path.write_bytes(raw)
    return path, hashlib.sha256(raw).hexdigest(), rows


def _daily_receipt(
    tmp_path: Path,
    sessions: list[str],
) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    per_date = [
        {
            "trade_date": trade_date,
            "authoritative_daily_raw_codes_sha256": _sha(["daily", trade_date]),
            "daily_basic_canonical_rows_sha256": _sha(["basic", trade_date]),
            "daily_basic_raw_segment_counts": {
                "BSE": 1,
                "SSE_MAIN": 1,
                "SSE_STAR": 1,
                "SZSE_CHINEXT": 1,
                "SZSE_MAIN": 1,
            },
        }
        for trade_date in sessions
    ]
    unsigned = {
        "all_supported_segments_compared_before_scope_filter": True,
        "arbitrary_row_drops_permitted": False,
        "audited_daily_authority": {
            "artifact_root_sha256": _sha("audited-daily-artifact"),
            "daily_identity_root_sha256": _sha("daily-identity"),
            "schema": "audited-daily-authority-descriptor/v1",
        },
        "schema": "factor-v3-daily-basic-733-exact-set-receipt/v2",
        "authority_status": "VERIFIED_FACTOR_V3_733_DAILY_BASIC_EXACT_SET",
        "authority_scope": "FACTOR_V3_250_PREWINDOW_PLUS_483_DEVELOPMENT_INPUT_ONLY",
        "row_authority_status": "GRANTED_FOR_BOUND_FACTOR_V3_733_COVERAGE_ONLY",
        "exact_set_verified": True,
        "trade_date_count": 733,
        "trade_dates": sessions,
        "trade_dates_sha256": _sha(sessions),
        "normalized_daily_basic_row_authority_root_sha256": _sha("normalized"),
        "per_date_statistics": per_date,
        "per_date_statistics_sha256": _sha(per_date),
        "publication_capability_sha256": _sha("publication-proof"),
        "factor_v3_development_materialization_input_eligible": True,
        "factor_v3_target_identity_root_sha256": _sha("target-identity"),
        "factor_v3_target_scope": {
            "excluded_segments": ["SSE_STAR", "BSE"],
            "included_segments": ["SSE_MAIN", "SZSE_MAIN", "SZSE_CHINEXT"],
            "policy_id": "factor-v3-mainboard-chinext-only/v1",
            "target_identity_row_count": 0,
        },
        "formal_factor_v3_materialization_performed": False,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "producer_binding": {
            "root_sha256": _sha("daily-producer"),
            "schema": "factor-v3-daily-basic-producer/v1",
        },
        "production_profile_registered": False,
        "production_recommendation_eligible": False,
        "raw_source_rows_bound": True,
        "rows_published": False,
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
        "transition_boundary_authority_root_sha256": _sha("transition-boundary"),
        "transition_boundary_count": 0,
        "transition_overlap_authority_root_sha256": _sha("transition-overlap"),
        "transition_resolved_identity_exact_set_verified": True,
    }
    receipt = {**unsigned, "authority_root_sha256": _sha(unsigned)}
    raw = _bytes(receipt)
    receipt_sha = hashlib.sha256(raw).hexdigest()
    relative = (
        f"factor_v3_daily_basic_733_receipts/sha256/{receipt_sha[:2]}/"
        f"{receipt_sha}.json"
    )
    path = tmp_path / "daily-authority" / Path(*relative.split("/"))
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    publication = {
        "schema": "factor-v3-daily-basic-733-exact-set-publication/v2",
        "receipt_relative_path": relative,
        "receipt_sha256": receipt_sha,
        "attestation_relative_path": (
            f"factor_v3_daily_basic_733_attestations/sha256/{'a' * 2}/{'a' * 64}.json"
        ),
        "attestation_sha256": "a" * 64,
        "publication_relative_path": (
            f"factor_v3_daily_basic_733_publications/sha256/{'b' * 2}/{'b' * 64}.json"
        ),
        "publication_sha256": "b" * 64,
    }
    return path, receipt, publication


def _partitions(sessions: list[str]) -> tuple[legacy_daily.AuthoritativeDailyPartition, ...]:
    codes = (
        "430001.BJ",
        "600001.SH",
        "688001.SH",
        "300001.SZ",
        "000001.SZ",
    )
    return tuple(
        legacy_daily.AuthoritativeDailyPartition(
            trade_date=trade_date,
            generation_id=_sha(["generation", trade_date]),
            generation_manifest_sha256=_sha(["manifest", trade_date]),
            generation_lineage_sha256=_sha(["lineage", trade_date]),
            vintage="historical_backfill",
            ts_codes=tuple(sorted(codes)),
        )
        for trade_date in sessions
    )


def _source_authority(
    sessions: list[str],
) -> tuple[legacy_daily.AuditedDailyAuthority, dict[str, Any]]:
    partitions = _partitions(sessions)
    value = legacy_daily.AuditedDailyAuthority(
        manifest_file_sha256=_sha("manifest-file"),
        manifest_sha256=_sha("manifest"),
        bundle_sha256=_sha("bundle"),
        artifact_root_sha256=_sha("artifact"),
        sqlite_sha256=_sha("sqlite"),
        coverage_audit_sha256=_sha("coverage"),
        temporal_contract_sha256=_sha("temporal"),
        temporal_role="development",
        daily_table_rows=len(sessions) * 5,
        daily_table_sha256=_sha("daily-table"),
        market_generation_count=733,
        market_generation_root_sha256=_sha("generations"),
        partitions=partitions,
    )
    identity = {
        "development_authority": {"sqlite_sha256": _sha("development-sqlite")},
        "root_sha256": _sha("source-authority"),
        "session_count": 733,
    }
    return value, identity


def _daily_partition(ref: dict[str, str]) -> legacy_daily.DailyBasicPartition:
    segment_by_code = {
        "430001.BJ": ("BSE", False),
        "600001.SH": ("SSE_MAIN", True),
        "688001.SH": ("SSE_STAR", False),
        "300001.SZ": ("SZSE_CHINEXT", True),
        "000001.SZ": ("SZSE_MAIN", True),
    }
    rows = tuple(
        NormalizedDailyBasicRow(
            ts_code=code,
            trade_date=ref["trade_date"],
            turnover_rate=1.0,
            turnover_rate_f=2.0,
            free_share=3.0,
            float_share=4.0,
            total_mv=5.0,
            circ_mv=6.0,
            market_segment=segment,
            research_scope_mainboard_chinext=in_scope,
        )
        for code, (segment, in_scope) in sorted(segment_by_code.items())
    )
    return legacy_daily.DailyBasicPartition(
        trade_date=ref["trade_date"],
        collection_set_relative_path=ref["collection_set_relative_path"],
        collection_set_sha256=ref["collection_set_sha256"],
        attempt_relative_path="attempt.json",
        attempt_sha256=_sha(["attempt", ref["trade_date"]]),
        raw_relative_path="raw.json",
        raw_sha256=_sha(["raw", ref["trade_date"]]),
        source_normalization_rows_sha256=_sha(["source", ref["trade_date"]]),
        canonical_rows_sha256=_sha(
            [
                {
                    "ts_code": row.ts_code,
                    "trade_date": row.trade_date,
                    "turnover_rate": row.turnover_rate,
                    "turnover_rate_f": row.turnover_rate_f,
                    "free_share": row.free_share,
                    "float_share": row.float_share,
                    "total_mv": row.total_mv,
                    "circ_mv": row.circ_mv,
                    "market_segment": row.market_segment,
                    "research_scope_mainboard_chinext": row.research_scope_mainboard_chinext,
                }
                for row in rows
            ]
        ),
        normalization_receipt_sha256=_sha(["normalization", ref["trade_date"]]),
        rows=rows,
    )


def _write_source_spec(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Path, str, dict[str, Any], dict[str, Any]]:
    sessions = _dates(733)
    prewindow = sessions[:250]
    development = sessions[250:]
    parent_path, parent_sha, parent_rows = _parent_snapshot(
        tmp_path,
        monkeypatch,
        development,
    )
    _, daily_receipt, daily_publication = _daily_receipt(tmp_path, sessions)
    refs = []
    for trade_date in sessions:
        digest = _sha(["collection", trade_date])
        refs.append(
            {
                "trade_date": trade_date,
                "collection_set_sha256": digest,
                "collection_set_relative_path": (
                    f"daily_basic_collection_sets/sha256/{digest[:2]}/{digest}.json"
                ),
            }
        )
    decision_path = tmp_path / f"{'d' * 64}.json"
    decision_path.write_text("{}", encoding="utf-8")
    for directory in (
        tmp_path / "feature-run",
        tmp_path / "frozen-source",
        tmp_path / "points",
        tmp_path / "transition",
    ):
        directory.mkdir()
    for file_path in (
        tmp_path / "feature-spec.json",
        tmp_path / "attestation.json",
        tmp_path / "development.sqlite3",
    ):
        file_path.write_text("{}", encoding="utf-8")
    spec = {
        "schema": "factor-v3-development-input-source-spec/v2",
        "feature_history": {
            "run_spec_path": str((tmp_path / "feature-spec.json").resolve()),
            "run_root": str((tmp_path / "feature-run").resolve()),
            "frozen_source_attestation_path": str((tmp_path / "attestation.json").resolve()),
            "expected_frozen_source_attestation_sha256": _sha("attestation"),
            "frozen_source_root": str((tmp_path / "frozen-source").resolve()),
            "expected_frozen_source_commit": "1" * 40,
        },
        "daily_basic": {
            "audited_development_universe_sqlite_path": str(
                (tmp_path / "development.sqlite3").resolve()
            ),
            "expected_development_coverage_audit_sha256": _sha("dev-coverage"),
            "expected_development_artifact_root_sha256": _sha("dev-artifact"),
            "expected_development_temporal_contract_sha256": _sha("dev-temporal"),
            "expected_development_temporal_role": "development_4",
            "expected_source_authority_root_sha256": _sha("source-authority"),
            "points_output_root": str((tmp_path / "points").resolve()),
            "collection_set_refs": refs,
            "security_code_transition_evidence_root": str((tmp_path / "transition").resolve()),
            "expected_security_code_transition_contract_sha256": _sha("transition"),
            "authority_output_root": str((tmp_path / "daily-authority").resolve()),
            "publication": daily_publication,
        },
        "factor_v2": {
            "decision_receipt_path": str(decision_path.resolve()),
            "expected_decision_receipt_raw_file_sha256": "d" * 64,
            "parent_snapshot_path": str(parent_path.resolve()),
            "expected_parent_snapshot_file_sha256": parent_sha,
            "parent_payload_fields": ["features"],
            "pinned_parent_adapter": {
                "schema": "factor-v3-pinned-factor-v2-parent-adapter-source/v1",
                "frozen_source_root": str((tmp_path / "factor-v2-frozen-source").resolve()),
                "expected_frozen_source_commit": authority.FACTOR_V2_FROZEN_SOURCE_COMMIT,
                "expected_frozen_source_tree_oid": authority.FACTOR_V2_FROZEN_SOURCE_TREE_OID,
                "expected_frozen_source_blob_sha256": dict(
                    authority.FACTOR_V2_FROZEN_SOURCE_BLOB_SHA256
                ),
                "parent_materialization_manifest_path": str(
                    (tmp_path / "factor-v2-parent-manifest.json").resolve()
                ),
                "overlay_manifest_path": str(
                    (tmp_path / "factor-v2-overlay-manifest.json").resolve()
                ),
                "suspension_metadata_path": str(
                    (tmp_path / "factor-v2-suspension.sqlite3").resolve()
                ),
                "subprocess_contract": deepcopy(authority.FACTOR_V2_PARENT_SUBPROCESS_CONTRACT),
                "public_verifier_replay_performed": False,
            },
        },
    }
    (tmp_path / "factor-v2-frozen-source").mkdir()
    for path in (
        tmp_path / "factor-v2-parent-manifest.json",
        tmp_path / "factor-v2-overlay-manifest.json",
        tmp_path / "factor-v2-suspension.sqlite3",
    ):
        path.write_bytes(b"fixture")
    spec_path = tmp_path / "source-spec.json"
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    context = {
        "sessions": sessions,
        "prewindow": prewindow,
        "development": development,
        "parent_rows": parent_rows,
        "daily_receipt": daily_receipt,
    }
    return spec_path, hashlib.sha256(raw).hexdigest(), spec, context


def _install_source_stubs(
    monkeypatch: pytest.MonkeyPatch,
    context: dict[str, Any],
) -> dict[str, int]:
    calls = {"history_plan": 0, "history_attestation": 0, "daily": 0, "branch": 0}
    sessions = context["sessions"]
    source, identity = _source_authority(sessions)

    def history_receipt() -> dict[str, Any]:
        unsigned = {
            "schema_version": "audited-pit-factor-v3-feature-history-authority-receipt/v3",
            "verified": True,
            "authority_status": "VERIFIED_FEATURE_HISTORY_ONLY",
            "feature_history_only": True,
            "factor_v3_points_contract_sha256": points.FACTOR_V3_POINTS_CONTRACT_SHA256,
            "factor_v3_feature_history_authority_contract_sha256": _sha(
                "feature-history-contract"
            ),
            "collection_publication_manifest_sha256": _sha("history-manifest"),
            "collection_plan_sha256": _sha("history-plan"),
            "session_count": 250,
            "sessions_sha256": _sha(context["prewindow"]),
            "session_authority_refs_sha256": _sha("history-session-refs"),
            "snapshot_index_sha256": _sha("history-snapshot-index"),
            "pit_store_database_sha256": _sha("history-database"),
            "pit_store_database_bytes": 1,
            "pit_store_receipt_manifest_sha256": _sha("history-receipt-manifest"),
            "pit_store_raw_artifact_set_sha256": _sha("history-raw-artifact-set"),
            "source_authority_root_sha256": _sha("history-source"),
            "upstream_scope_root_sha256": _sha("history-upstream-scope"),
            "producer_code_root_sha256": _sha("history-producer"),
            "exact_nonempty_bak_basic_session_count": 250,
            "daily_generation_session_count": 250,
            "suspend_d_authority_session_count": 250,
            "upstream_star_preserved_session_count": 250,
            "upstream_beijing_preserved_session_count": 250,
            "security_code_transition_contract_sha256": _sha(
                "security-code-transition-contract"
            ),
            "factor_materialization_eligible": False,
            "experiment_launch_eligible": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_profile_registered": False,
            "production_recommendation_eligible": False,
        }
        return {**unsigned, "receipt_sha256": _sha(unsigned)}

    def load_history_spec(_path: Any) -> dict[str, Any]:
        return {
            "collection_plan": {},
            "development_session_refs": [],
            "temporal_partition_contract": {},
            "trade_cal_output_root": str(Path.cwd()),
            "trade_cal_publication": {},
        }

    def history_plan(**_kwargs: Any) -> None:
        calls["history_plan"] += 1

    def history_attestation(**_kwargs: Any) -> dict[str, Any]:
        calls["history_attestation"] += 1
        receipt = history_receipt()
        return {
            "verified": True,
            "session_count": 250,
            "sessions_sha256": _sha(context["prewindow"]),
            "receipt_sha256": receipt["receipt_sha256"],
            "authority_binding": {
                "collection_publication_output_root": str(Path.cwd()),
                "manifest_relative_path": "manifest.json",
                "manifest_sha256": _sha("history-manifest"),
                "receipt": receipt,
            },
        }

    def daily_verify(**_kwargs: Any) -> dict[str, Any]:
        calls["daily"] += 1
        receipt_raw = _bytes(context["daily_receipt"])
        return {
            "verified": True,
            "authority_root_sha256": context["daily_receipt"]["authority_root_sha256"],
            "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
            "trade_date_count": 733,
            "trade_dates": sessions,
        }

    def branch_receipt(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        calls["branch"] += 1
        unsigned = {
            "schema_version": "factor-v2-decision-branch-structural-adapter/v2",
            "source_decision_receipt_raw_file_sha256": "d" * 64,
            "source_decision_receipt_sha256": _sha("decision"),
            "evaluation_artifact_sha256": _sha("evaluation"),
            "arm_order": ["v2_control", "overnight_20", "intraday_20"],
            "arm_decisions": {
                "v2_control": "GREEN",
                "overnight_20": "RED",
                "intraday_20": "RED",
            },
            "selection_rule": "first_green_in_arm_order_else_low_rvol20_rank_overlay_20",
            "selected_branch": "v2_control",
            "selected_arm": "v2_control",
            "low_rvol_overlay_status": "VOID",
            "contract_binding_validated": True,
            "publisher_terminal_chain_verified": False,
            "source_authority_complete": False,
            "formal_materialization_eligible": False,
            "verified": False,
            "embargo_consumed": False,
            "final_oos_consumed": False,
            "production_recommendation_eligible": False,
        }
        return {**unsigned, "receipt_sha256": _sha(unsigned)}

    monkeypatch.setattr(
        authority.history_runner,
        "verify_factor_v3_feature_history_run",
        lambda **_kwargs: pytest.fail("side-effectful runner verifier must not be called"),
    )
    monkeypatch.setattr(
        authority.history_runner,
        "load_factor_v3_feature_history_run_spec",
        load_history_spec,
    )
    monkeypatch.setattr(
        authority.history_authority,
        "verify_factor_v3_feature_history_collection_plan",
        history_plan,
    )
    monkeypatch.setattr(
        authority.frozen_attestation,
        "verify_factor_v3_feature_history_frozen_source_attestation",
        history_attestation,
    )
    monkeypatch.setattr(
        authority.daily_authority,
        "verify_factor_v3_daily_basic_733_exact_set_coverage",
        daily_verify,
    )
    monkeypatch.setattr(
        authority.branch_selector,
        "build_factor_v2_decision_branch_receipt",
        branch_receipt,
    )
    monkeypatch.setattr(authority.daily_authority, "_load_733_authority", lambda **_kwargs: (source, identity))
    monkeypatch.setattr(
        authority.legacy_daily,
        "_load_daily_basic_partition",
        lambda *, collection_ref, **_kwargs: _daily_partition(dict(collection_ref)),
    )
    monkeypatch.setattr(
        authority.daily_authority,
        "_load_transition_authority",
        lambda **_kwargs: legacy_daily.TransitionAuthority(
            contract_sha256=_sha("transition"),
            evidence_receipt_sha256=_sha("transition-receipt"),
            evidence_receipts_sha256=_sha([]),
            transition_count=0,
            transitions_by_code={},
        ),
    )
    membership = [
        {"trade_date": trade_date, "ts_code": code, "list_date": "2020-01-01"}
        for trade_date in sessions
        for code in ("000001.SZ", "300001.SZ", "600001.SH")
    ]
    monkeypatch.setattr(
        authority,
        "_read_membership_and_suspension_rows",
        lambda **_kwargs: {"membership": membership, "suspensions": []},
    )
    return calls


def _publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, int], Path, str]:
    source_spec, source_spec_sha, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    calls = _install_source_stubs(monkeypatch, context)
    output_root = tmp_path / "output"
    publication = authority.publish_factor_v3_development_input_authority(
        source_spec_path=source_spec,
        expected_source_spec_sha256=source_spec_sha,
        output_root=output_root,
    )
    return publication, context, calls, source_spec, source_spec_sha


def test_publish_and_public_postverify_real_authority_schemas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, context, calls, source_spec, source_spec_sha = _publish(tmp_path, monkeypatch)

    assert publication["schema"] == "factor-v3-development-input-authority-publication/v3"
    verified = authority.verify_factor_v3_development_input_authority(
        source_spec_path=source_spec,
        expected_source_spec_sha256=source_spec_sha,
        output_root=tmp_path / "output",
        publication=publication,
    )
    assert verified["verified"] is False
    assert verified["candidate_verified"] is True
    assert verified["source_authority_complete"] is False
    assert all(verified[field] is False for field in authority.PROVENANCE_FALSE_FIELDS)
    assert verified["development_only"] is True
    assert verified["source_date_count"] == 732
    assert calls == {
        "history_plan": 3,
        "history_attestation": 3,
        "daily": 3,
        "branch": 3,
    }

    descriptor_path = tmp_path / "output" / Path(
        *publication["descriptor_relative_path"].split("/")
    )
    descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
    assert descriptor["schema"] == "factor-v3-development-input-authority/v3"
    assert descriptor["calendar"] == {
        "all_market_session_count": 733,
        "all_market_sessions_sha256": _sha(context["sessions"]),
        "development_session_count": 483,
        "development_sessions_sha256": _sha(context["development"]),
        "prewindow_session_count": 250,
        "prewindow_sessions_sha256": _sha(context["prewindow"]),
        "source_date_count": 732,
        "source_dates_sha256": _sha(context["sessions"][:-1]),
    }
    assert descriptor["factor_v2_branch"]["selected_branch"] == "v2_control"
    assert descriptor["formal_materialization_eligible"] is False
    assert "producer_binding" not in descriptor
    assert descriptor["producer_snapshot"]["candidate_snapshot_only"] is True
    assert all(
        descriptor[field] is False for field in authority.PROVENANCE_FALSE_FIELDS
    )
    assert all(
        descriptor["producer_snapshot"][field] is False
        for field in authority.PROVENANCE_FALSE_FIELDS
    )
    daily_snapshot_path = descriptor_path.parent / descriptor["snapshots"]["daily_basic"][
        "relative_path"
    ]
    daily_snapshot = json.loads(daily_snapshot_path.read_text(encoding="utf-8"))
    daily_adapter = daily_snapshot["derived_adapter"]
    assert daily_adapter["schema"] == authority.DERIVED_ADAPTER_SCHEMA
    assert "producer_root_sha256" not in daily_adapter
    assert all(
        daily_adapter[field] is False for field in authority.PROVENANCE_FALSE_FIELDS
    )
    manifest_path = tmp_path / "output" / Path(
        *publication["publication_relative_path"].split("/")
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "producer_root_sha256" not in manifest
    assert manifest["producer_snapshot_root_sha256"] == descriptor["producer_snapshot"][
        "root_sha256"
    ]
    assert all(manifest[field] is False for field in authority.PROVENANCE_FALSE_FIELDS)
    assert descriptor["source_receipts"]["feature_history"]["schema_version"] == (
        "audited-pit-factor-v3-feature-history-authority-receipt/v3"
    )
    assert descriptor["source_receipts"]["daily_basic"]["schema"] == (
        "factor-v3-daily-basic-733-exact-set-receipt/v2"
    )
    assert all(descriptor[field] is False for field in authority.SAFETY_FALSE_FIELDS)

    snapshots = descriptor["snapshots"]
    parent_path = descriptor_path.parent / snapshots["factor_v2_parent"]["relative_path"]
    parent = json.loads(parent_path.read_text(encoding="utf-8"))
    parent_rows = sorted(
        context["parent_rows"],
        key=lambda row: (row["signal_date"], row["candidate_key"]),
    )
    candidate_keys_root = _sha(sorted(row["candidate_key"] for row in parent_rows))
    source_feature_rows = [
        {
            "candidate_key": row["candidate_key"],
            "features": row["features"],
            "signal_date": row["signal_date"],
        }
        for row in parent_rows
    ]
    source_feature_root = _sha(source_feature_rows)
    full_export_root = _sha(parent_rows)
    binding = parent["parent_hash_binding_receipt"]
    assert binding["content_hash_bound"] is True
    assert binding["source_provenance_verified"] is False
    assert binding["formal_materialization_eligible"] is False
    assert binding["candidate_keys_sha256"] == candidate_keys_root
    assert binding["source_feature_projection_rows_sha256"] == source_feature_root
    assert binding["full_export_rows_sha256"] == full_export_root
    assert len({candidate_keys_root, source_feature_root, full_export_root}) == 3
    assert binding["public_verifier_replay_performed"] is False
    parent_unsigned = dict(binding)
    parent_receipt_sha = parent_unsigned.pop("receipt_sha256")
    assert parent_receipt_sha == _sha(parent_unsigned)
    adapter = parent["derived_adapter"]
    assert adapter == {
        "added_identity_field": "ts_code_from_candidate_key",
        "candidate_key_order": ["candidate_key"],
        "candidate_key_projection_fields": ["candidate_key"],
        "candidate_keys_sha256": candidate_keys_root,
        "full_export_fields": ["candidate_key", "features", "signal_date", "ts_code"],
        "full_export_order": ["signal_date", "candidate_key"],
        "full_export_rows_sha256": full_export_root,
        "materializer_v1_hash_compatible": False,
        "pinned_parent_adapter_source_spec_sha256": binding[
            "pinned_parent_adapter_source_spec_sha256"
        ],
        "schema": "factor-v3-factor-v2-parent-row-derived-adapter/v1",
        "source_feature_order": ["signal_date", "candidate_key"],
        "source_feature_projection_fields": ["candidate_key", "signal_date", "features"],
        "source_feature_projection_rows_sha256": source_feature_root,
    }
    daily_path = descriptor_path.parent / snapshots["daily_basic"]["relative_path"]
    daily = json.loads(daily_path.read_text(encoding="utf-8"))
    assert daily["source_dates"] == context["sessions"][:-1]
    assert {row["ts_code"] for row in daily["rows"]} == {
        "000001.SZ",
        "300001.SZ",
        "600001.SH",
    }
    listing_path = descriptor_path.parent / snapshots["listing_membership"]["relative_path"]
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    assert listing["interval_semantics"] == (
        "contiguous_verified_market_session_membership_only"
    )
    assert all(row["membership_start"] == context["sessions"][0] for row in listing["rows"])
    assert all(row["membership_end"] == context["sessions"][-1] for row in listing["rows"])
    board_path = descriptor_path.parent / snapshots["upstream_board_ledger"]["relative_path"]
    board = json.loads(board_path.read_text(encoding="utf-8"))
    assert board["preserved_before_target_scope_filter"] is True
    assert board["source_segments"] == [
        "BSE",
        "SSE_MAIN",
        "SSE_STAR",
        "SZSE_CHINEXT",
        "SZSE_MAIN",
    ]
    assert len(board["per_date"]) == 732
    receipt_path = descriptor_path.parent / snapshots["daily_basic_exact_set_receipt"][
        "relative_path"
    ]
    receipt_projection = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt_projection["redacted_field_count"] == 1
    assert receipt_projection["source_receipt_projection_sha256"] == _sha(
        receipt_projection["source_receipt_projection"]
    )
    history_projection_path = (
        descriptor_path.parent
        / snapshots["feature_history_receipt"]["relative_path"]
    )
    history_projection = json.loads(
        history_projection_path.read_text(encoding="utf-8")
    )
    history_receipt = {
        **history_projection["source_receipt_projection"],
        "receipt_sha256": history_projection["source_receipt_sha256"],
    }
    authority.validate_factor_v3_public_source_receipt_projection(
        source_receipt=history_receipt,
        projected_snapshot=history_projection,
        kind="feature_history_v3",
        sessions=context["prewindow"],
        formal_schema_required=False,
    )
    authority.validate_factor_v3_public_source_receipt_projection(
        source_receipt=context["daily_receipt"],
        projected_snapshot=receipt_projection,
        kind="daily_basic_733_v2",
        sessions=context["sessions"],
        formal_schema_required=False,
    )
    snapshot_payloads = {
        name: json.loads(
            (descriptor_path.parent / snapshots[name]["relative_path"]).read_text(
                encoding="utf-8"
            )
        )
        for name in authority._SNAPSHOT_NAMES
    }
    activation._validate_candidate_receipt_snapshots(
        snapshot_payloads,
        snapshot_payloads["calendar"],
    )

    for source_receipt, projected_snapshot, kind, sessions in (
        (
            history_receipt,
            history_projection,
            "feature_history_v3",
            context["prewindow"],
        ),
        (
            context["daily_receipt"],
            receipt_projection,
            "daily_basic_733_v2",
            context["sessions"],
        ),
    ):
        for mutation in ("extra", "missing"):
            drifted = deepcopy(projected_snapshot)
            projection = drifted["source_receipt_projection"]
            if mutation == "extra":
                projection["unbound_public_field"] = True
            else:
                projection.pop(next(iter(projection)))
            drifted["source_receipt_projection_sha256"] = _sha(projection)
            with pytest.raises(ValueError, match="projection"):
                authority.validate_factor_v3_public_source_receipt_projection(
                    source_receipt=source_receipt,
                    projected_snapshot=drifted,
                    kind=kind,
                    sessions=sessions,
                    formal_schema_required=False,
                )

    combined_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "output").rglob("*.json")
    ).lower()
    assert not any(term in combined_text for term in ("token", "secret", "capability", "password"))


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda value: value.update({"daily_basic_normalized_row_authority_root_sha256": "0" * 64}), "fields"),
        (lambda value: value.pop("factor_v2"), "fields"),
        (
            lambda value: value["daily_basic"].update(
                {"coverage_receipt_sha256": "0" * 64}
            ),
            "fields",
        ),
    ],
)
def test_source_spec_rejects_extra_missing_and_legacy_alias_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
    match: str,
) -> None:
    spec_path, _digest, spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    mutation(spec)
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match=match):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=hashlib.sha256(raw).hexdigest(),
            output_root=tmp_path / "output",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("expected_frozen_source_tree_oid"),
        lambda value: value.update({"expected_frozen_source_commit": "0" * 40}),
        lambda value: value["expected_frozen_source_blob_sha256"].update(
            {"app/audited_pit_factor_v2.py": "0" * 64}
        ),
        lambda value: value["subprocess_contract"].update({"python_flags": []}),
        lambda value: value.update({"public_verifier_replay_performed": True}),
    ],
)
def test_pinned_parent_adapter_source_and_subprocess_contract_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Any,
) -> None:
    spec_path, _digest, spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    mutation(spec["factor_v2"]["pinned_parent_adapter"])
    raw = _bytes(spec)
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="pinned|subprocess|replay|frozen"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=hashlib.sha256(raw).hexdigest(),
            output_root=tmp_path / "output",
        )


def test_source_spec_rejects_duplicate_keys_before_any_public_verifier(
    tmp_path: Path,
) -> None:
    spec_path = tmp_path / "source-spec.json"
    raw = b'{"schema":"a","schema":"b"}'
    spec_path.write_bytes(raw)
    with pytest.raises(ValueError, match="duplicate"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=hashlib.sha256(raw).hexdigest(),
            output_root=tmp_path / "output",
        )


def test_real_733_status_and_history_fields_are_required_not_legacy_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    receipt_path = next((tmp_path / "daily-authority").rglob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["authority_status"] = "VERIFIED_DEVELOPMENT_DAILY_BASIC_EXACT_SET"
    receipt_path.write_bytes(_bytes(receipt))
    with pytest.raises(ValueError, match="733.*status|authority status"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=digest,
            output_root=tmp_path / "output",
        )


@pytest.mark.parametrize("count", [249, 251, 482, 484, 732, 734])
def test_exact_250_483_733_and_732_calendar_contracts_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    sessions = context["sessions"][:count]
    source, identity = _source_authority(sessions)
    monkeypatch.setattr(
        authority.daily_authority,
        "_load_733_authority",
        lambda **_kwargs: (source, {**identity, "session_count": count}),
    )
    with pytest.raises(ValueError, match="733|250|483|732|session"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=digest,
            output_root=tmp_path / "output",
        )


def test_bse_and_star_must_be_present_on_every_upstream_date(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    receipt_path = next((tmp_path / "daily-authority").rglob("*.json"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["per_date_statistics"][7]["daily_basic_raw_segment_counts"]["BSE"] = 0
    receipt_path.write_bytes(_bytes(receipt))
    with pytest.raises(ValueError, match="BSE|STAR|segment"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=digest,
            output_root=tmp_path / "output",
        )


def test_factor_v2_branch_adapter_must_be_exact_and_content_addressed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)

    def forged(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "factor-v2-decision-branch-structural-adapter/v2",
            "selected_branch": "forged",
            "verified": True,
            "receipt_sha256": "0" * 64,
        }

    monkeypatch.setattr(
        authority.branch_selector,
        "build_factor_v2_decision_branch_receipt",
        forged,
    )
    with pytest.raises(ValueError, match="branch"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=digest,
            output_root=tmp_path / "output",
        )


def test_parent_signal_date_requires_exact_daily_membership(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    missing_date = context["development"][-1]
    membership = [
        {"trade_date": trade_date, "ts_code": code, "list_date": "2020-01-01"}
        for trade_date in context["sessions"]
        for code in ("000001.SZ", "300001.SZ", "600001.SH")
        if not (trade_date == missing_date and code == "000001.SZ")
    ]
    monkeypatch.setattr(
        authority,
        "_read_membership_and_suspension_rows",
        lambda **_kwargs: {"membership": membership, "suspensions": []},
    )
    with pytest.raises(ValueError, match="parent.*membership|membership.*signal"):
        authority.publish_factor_v3_development_input_authority(
            source_spec_path=spec_path,
            expected_source_spec_sha256=digest,
            output_root=tmp_path / "output",
        )


def test_membership_transition_overlap_is_resolved_as_one_daily_cross_section(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec_path, digest, _spec, context = _write_source_spec(tmp_path, monkeypatch)
    _install_source_stubs(monkeypatch, context)
    effective_date = context["development"][5]
    predecessor_date = context["development"][4]
    predecessor = "300114.SZ"
    successor = "302132.SZ"
    security_id = f"cn-a-share:{predecessor}"
    descriptor = {
        "canonical_ts_code": predecessor,
        "effective_date": effective_date,
        "predecessor_ts_code": predecessor,
        "security_id": security_id,
        "successor_ts_code": successor,
        "transition_id": _sha("300114-to-302132"),
    }
    transition = legacy_daily.TransitionAuthority(
        contract_sha256=_sha("transition"),
        evidence_receipt_sha256=_sha("transition-receipt"),
        evidence_receipts_sha256=_sha(["transition-receipt"]),
        transition_count=1,
        transitions_by_code={predecessor: descriptor, successor: descriptor},
    )
    monkeypatch.setattr(
        authority.daily_authority,
        "_load_transition_authority",
        lambda **_kwargs: transition,
    )
    membership = [
        {"trade_date": trade_date, "ts_code": code, "list_date": "2020-01-01"}
        for trade_date in context["sessions"]
        for code in ("000001.SZ", "300001.SZ", "600001.SH")
    ]
    membership.extend(
        [
            {
                "trade_date": predecessor_date,
                "ts_code": predecessor,
                "list_date": "2020-01-01",
            },
            {
                "trade_date": effective_date,
                "ts_code": predecessor,
                "list_date": "2020-01-01",
            },
            {
                "trade_date": effective_date,
                "ts_code": successor,
                "list_date": "2020-01-01",
            },
        ]
    )
    monkeypatch.setattr(
        authority,
        "_read_membership_and_suspension_rows",
        lambda **_kwargs: {"membership": membership, "suspensions": []},
    )

    publication = authority.publish_factor_v3_development_input_authority(
        source_spec_path=spec_path,
        expected_source_spec_sha256=digest,
        output_root=tmp_path / "output",
    )
    descriptor_path = tmp_path / "output" / Path(
        *publication["descriptor_relative_path"].split("/")
    )
    published = json.loads(descriptor_path.read_text(encoding="utf-8"))
    transition_path = descriptor_path.parent / published["snapshots"][
        "security_code_transitions"
    ]["relative_path"]
    transition_snapshot = json.loads(transition_path.read_text(encoding="utf-8"))
    rows = transition_snapshot["rows"]
    assert any(row["ts_code"] == predecessor for row in rows)
    assert any(row["ts_code"] == successor for row in rows)


def test_public_immutable_exporter_is_wired_from_verified_cas_binding(
    tmp_path: Path,
) -> None:
    publication_root = tmp_path / "collection-publication"
    publication_root.mkdir()
    cas_source = tmp_path / "cas-source.sqlite3"
    cas_sha = _row_database(cas_source, "20220103", "000001.SZ")
    development_database = tmp_path / "development.sqlite3"
    development_sha = _row_database(
        development_database,
        "20220104",
        "300001.SZ",
    )
    snapshot = {
        "database": {"bytes": cas_source.stat().st_size, "path": "metadata.sqlite3", "sha256": cas_sha},
        "raw_artifact_count": 1,
        "raw_artifact_set_sha256": _sha("raw"),
        "raw_artifacts": [{"fixture": True}],
        "receipt_manifest_sha256": _sha("receipts"),
        "schema": "audited-pit-factor-v3-feature-history-snapshot-index/v1",
        "session_count": 250,
        "sessions_sha256": _sha(["2022-01-03"]),
    }
    snapshot_raw = _bytes(snapshot)
    snapshot_sha = hashlib.sha256(snapshot_raw).hexdigest()
    snapshot_relative = (
        f"feature_history_collection_snapshots/sha256/{snapshot_sha[:2]}/"
        f"{snapshot_sha}/snapshot-index.json"
    )
    snapshot_path = publication_root / Path(*snapshot_relative.split("/"))
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_bytes(snapshot_raw)
    (snapshot_path.parent / "metadata.sqlite3").write_bytes(cas_source.read_bytes())
    manifest = {
        "schema": "audited-pit-factor-v3-feature-history-collection-manifest/v2",
        "snapshot_index_relative_path": snapshot_relative,
        "snapshot_index_sha256": snapshot_sha,
    }
    manifest_raw = _bytes(manifest)
    manifest_sha = hashlib.sha256(manifest_raw).hexdigest()
    manifest_relative = f"manifests/{manifest_sha}.json"
    manifest_path = publication_root / Path(*manifest_relative.split("/"))
    manifest_path.parent.mkdir()
    manifest_path.write_bytes(manifest_raw)
    exported = authority._read_membership_and_suspension_rows(
        feature_history_source_binding={
            "collection_publication_output_root": str(publication_root.resolve()),
            "manifest_relative_path": manifest_relative,
            "manifest_sha256": manifest_sha,
            "receipt": {
                "pit_store_database_sha256": cas_sha,
                "snapshot_index_sha256": snapshot_sha,
            },
        },
        audited_development_universe_sqlite_path=development_database.resolve(),
        expected_development_database_sha256=development_sha,
        prewindow_dates=["2022-01-03"],
        development_dates=["2022-01-04"],
    )
    assert exported == {
        "membership": [
            {"list_date": "2020-01-01", "trade_date": "2022-01-03", "ts_code": "000001.SZ"},
            {"list_date": "2020-01-01", "trade_date": "2022-01-04", "ts_code": "300001.SZ"},
        ],
        "suspensions": [],
    }


def test_public_verifier_rejects_extra_missing_reparse_and_source_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, _context, _calls, source_spec, source_spec_sha = _publish(
        tmp_path,
        monkeypatch,
    )
    descriptor_path = tmp_path / "output" / Path(
        *publication["descriptor_relative_path"].split("/")
    )
    extra = descriptor_path.parent / "unbound.json"
    extra.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unbound|extra"):
        authority.verify_factor_v3_development_input_authority(
            source_spec_path=source_spec,
            expected_source_spec_sha256=source_spec_sha,
            output_root=tmp_path / "output",
            publication=publication,
        )
    extra.unlink()

    snapshot_path = next(
        path
        for path in descriptor_path.parent.glob("*.json")
        if path.name != descriptor_path.name
    )
    original = snapshot_path.read_bytes()
    snapshot_path.unlink()
    with pytest.raises(ValueError, match="missing|unavailable"):
        authority.verify_factor_v3_development_input_authority(
            source_spec_path=source_spec,
            expected_source_spec_sha256=source_spec_sha,
            output_root=tmp_path / "output",
            publication=publication,
        )
    snapshot_path.write_bytes(original)

    monkeypatch.setattr(
        authority,
        "_is_reparse_point",
        lambda path: Path(path) == snapshot_path,
    )
    with pytest.raises(ValueError, match="reparse"):
        authority.verify_factor_v3_development_input_authority(
            source_spec_path=source_spec,
            expected_source_spec_sha256=source_spec_sha,
            output_root=tmp_path / "output",
            publication=publication,
        )


def test_publisher_is_idempotent_and_postverify_detects_parent_toctou(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    publication, context, calls, source_spec, source_spec_sha = _publish(
        tmp_path,
        monkeypatch,
    )
    repeated = authority.publish_factor_v3_development_input_authority(
        source_spec_path=source_spec,
        expected_source_spec_sha256=source_spec_sha,
        output_root=tmp_path / "output",
    )
    assert repeated == publication
    assert calls["history_plan"] == 4

    factor_v2 = json.loads(source_spec.read_text(encoding="utf-8"))["factor_v2"]
    parent = Path(factor_v2["parent_snapshot_path"])
    parent.write_bytes(_bytes({"replaced": True}))
    with pytest.raises(ValueError, match="parent.*content address|parent.*drift"):
        authority.verify_factor_v3_development_input_authority(
            source_spec_path=source_spec,
            expected_source_spec_sha256=source_spec_sha,
            output_root=tmp_path / "output",
            publication=publication,
        )


def test_create_only_publication_never_overwrites_a_racing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target.json"
    rival = b'{"rival":true}'

    def racing_link(_source: Any, destination: Any) -> None:
        Path(destination).write_bytes(rival)
        raise FileExistsError

    monkeypatch.setattr(authority.os, "link", racing_link)
    with pytest.raises(ValueError, match="concurrent.*drift|existing.*drift"):
        authority._create_or_verify(target, b'{"expected":true}', label="fixture")
    assert target.read_bytes() == rival
