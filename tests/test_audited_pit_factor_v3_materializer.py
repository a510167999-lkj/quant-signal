from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
import inspect
import json
from pathlib import Path
from typing import Any

import pytest

from app import audited_pit_factor_v3_points_contract as points
from app import audited_pit_factor_v3_materializer as materializer


def _sha(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _sessions(count: int) -> list[str]:
    start = date(2023, 1, 2)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(count)]


def _patch_points_contract(
    monkeypatch: pytest.MonkeyPatch,
    *,
    development_sessions: list[str],
    parent_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    contract = deepcopy(points.FACTOR_V3_POINTS_CONTRACT)
    expectation = contract["preregistered_parent_expectation"]
    expectation["common_eligible_candidate_count"] = len(parent_rows)
    expectation["common_eligible_candidate_keys_sha256"] = _sha(
        [row["candidate_key"] for row in parent_rows]
    )
    expectation["common_eligible_source_feature_rows_sha256"] = _sha(parent_rows)
    expectation["original_parent_feature_row_count"] = len(parent_rows) + 1
    expectation["original_parent_feature_rows_sha256"] = _sha(["original", parent_rows])
    expectation["all_candidate_keys_sha256"] = _sha(
        ["suspended", *[row["candidate_key"] for row in parent_rows]]
    )
    expectation["preregistered_suspension_excluded_candidate_count"] = 1
    expectation["preregistered_suspension_excluded_candidate_keys_sha256"] = _sha(
        ["suspended"]
    )
    expectation["sessions"] = {
        "count": len(development_sessions),
        "start": development_sessions[0],
        "end": development_sessions[-1],
        "sha256": _sha(development_sessions),
    }
    policy = contract["formal_parent_sample_policy"]
    policy["source_candidate_count"] = len(parent_rows)
    policy["source_candidate_keys_sha256"] = expectation[
        "common_eligible_candidate_keys_sha256"
    ]
    policy["source_feature_rows_sha256"] = expectation[
        "common_eligible_source_feature_rows_sha256"
    ]
    contract["preregistered_parent_expectation_sha256"] = _sha(expectation)
    contract_sha256 = _sha(contract)
    monkeypatch.setattr(points, "FACTOR_V3_POINTS_CONTRACT", contract)
    monkeypatch.setattr(points, "FACTOR_V3_POINTS_CONTRACT_SHA256", contract_sha256)
    monkeypatch.setattr(
        points,
        "FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256",
        contract["preregistered_parent_expectation_sha256"],
    )
    return contract


def _identity_roots() -> dict[str, str]:
    return {
        "factor_v2_parent_producer_root_sha256": _sha("factor-v2-parent-producer"),
        "extended_trading_calendar_descriptor_root_sha256": _sha("calendar"),
        "extended_trading_calendar_receipt_sha256": _sha("calendar-receipt"),
        "feature_history_source_authority_root_sha256": _sha("feature-history"),
        "factor_v3_feature_history_verification_receipt_sha256": _sha("feature-history-receipt"),
        "daily_basic_normalized_row_authority_root_sha256": _sha("daily-basic"),
        "daily_basic_coverage_receipt_sha256": _sha("daily-basic-receipt"),
        "daily_traded_cross_section_root_sha256": _sha("daily-cross-section"),
        "pit_listing_membership_root_sha256": _sha("listing"),
        "pit_suspension_root_sha256": _sha("suspension"),
        "security_code_transition_contract_sha256": _sha("transition-contract"),
        "security_code_transition_evidence_root_sha256": _sha("transition-evidence"),
        "factor_v2_terminal_decision_descriptor_sha256": _sha("terminal-decision-descriptor"),
        "factor_v2_evaluator_descriptor_sha256": _sha("evaluator-descriptor"),
        "factor_v2_cost_slippage_execution_descriptor_sha256": _sha("cost-slippage-execution"),
    }


def _bundle(monkeypatch: pytest.MonkeyPatch) -> tuple[dict[str, Any], dict[str, Any]]:
    calendar = _sessions(733)
    prewindow = calendar[:250]
    development = calendar[250:]
    source_dates = [*prewindow, *development[:-1]]
    security_one = "cn-a-share:000001"
    security_two = "cn-a-share:600001"
    parent_rows = [
        {
            "candidate_key": f"cn-a-share:000002.SZ|{signal_date}",
            "signal_date": signal_date,
            "ts_code": "000002.SZ",
            "frozen_target": 0.01,
        }
        for signal_date in development
    ]
    parent_rows.extend(
        {
            "candidate_key": f"cn-a-share:600001.SH|{signal_date}",
            "signal_date": signal_date,
            "ts_code": "600001.SH",
            "frozen_target": -0.01,
        }
        for signal_date in development
    )
    contract = _patch_points_contract(
        monkeypatch,
        development_sessions=development,
        parent_rows=parent_rows,
    )
    roots = _identity_roots()
    transitions = [
        {
            "security_id": security_one,
            "ts_code": "000001.SZ",
            "effective_start": "2020-01-01",
            "effective_end": prewindow[-1],
        },
        {
            "security_id": security_one,
            "ts_code": "000002.SZ",
            "effective_start": development[0],
            "effective_end": "9999-12-31",
        },
        {
            "security_id": security_two,
            "ts_code": "600001.SH",
            "effective_start": "2020-01-01",
            "effective_end": "9999-12-31",
        },
    ]
    daily_rows: list[dict[str, Any]] = []
    cross_section_rows: list[dict[str, str]] = []
    for position, trade_date in enumerate(source_dates):
        first_code = "000001.SZ" if trade_date <= prewindow[-1] else "000002.SZ"
        first_turnover = 1.0
        second_turnover = 4.0 if position >= len(source_dates) - 20 else 2.0
        for security_id, ts_code, turnover in (
            (security_one, first_code, first_turnover),
            (security_two, "600001.SH", second_turnover),
        ):
            cross_section_rows.append(
                {
                    "security_id": security_id,
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                }
            )
            daily_rows.append(
                {
                    "security_id": security_id,
                    "ts_code": ts_code,
                    "trade_date": trade_date,
                    "turnover_rate_f": turnover,
                }
            )
    parent = {
        **contract["preregistered_parent_expectation"],
        "rows": parent_rows,
        "rows_sha256": _sha(parent_rows),
        "candidate_identity_root_sha256": _sha(
            [
                {"candidate_key": row["candidate_key"], "signal_date": row["signal_date"]}
                for row in parent_rows
            ]
        ),
    }
    per_date_board_ledger = [
        {
            "trade_date": trade_date,
            "raw_source_rows_sha256": _sha(["raw-source", trade_date]),
            "normalized_rows_sha256": _sha(["normalized-source", trade_date]),
            "segment_counts": {
                "BSE": 1,
                "SSE_MAIN": 1,
                "SSE_STAR": 1,
                "SZSE_CHINEXT": 1,
                "SZSE_MAIN": 1,
            },
        }
        for trade_date in source_dates
    ]
    board_ledger_root = _sha(per_date_board_ledger)
    bundle = {
        "schema_version": "audited-pit-factor-v3-development-input-bundle/v1",
        "points_contract_sha256": points.FACTOR_V3_POINTS_CONTRACT_SHA256,
        "authority_identities": roots,
        "factor_v2_parent": parent,
        "calendar": {
            "all_market_sessions": calendar,
            "prewindow_sessions": prewindow,
            "development_sessions": development,
            "all_market_sessions_sha256": _sha(calendar),
            "prewindow_sessions_sha256": _sha(prewindow),
            "development_sessions_sha256": _sha(development),
            "authority_receipt_sha256": roots[
                "extended_trading_calendar_receipt_sha256"
            ],
        },
        "daily_basic": {
            "rows": daily_rows,
            "rows_sha256": _sha(daily_rows),
            "source_dates": source_dates,
            "source_dates_sha256": _sha(source_dates),
        },
        "daily_traded_cross_section": {
            "rows": cross_section_rows,
            "rows_sha256": _sha(cross_section_rows),
        },
        "listing_membership": {
            "rows": [
                {
                    "security_id": security_one,
                    "listing_date": "2020-01-01",
                    "membership_start": "2020-01-01",
                    "membership_end": "9999-12-31",
                },
                {
                    "security_id": security_two,
                    "listing_date": "2020-01-01",
                    "membership_start": "2020-01-01",
                    "membership_end": "9999-12-31",
                },
            ],
        },
        "suspensions": {"rows": []},
        "security_code_transitions": {
            "rows": transitions,
            "rows_sha256": _sha(transitions),
        },
        "upstream_board_ledger": {
            "preserved_before_target_scope_filter": True,
            "source_segments": [
                "BSE",
                "SSE_MAIN",
                "SSE_STAR",
                "SZSE_CHINEXT",
                "SZSE_MAIN",
            ],
            "pre_filter_segment_counts": {
                "BSE": 1,
                "SSE_MAIN": 1,
                "SSE_STAR": 1,
                "SZSE_CHINEXT": 1,
                "SZSE_MAIN": 1,
            },
            "per_date": per_date_board_ledger,
            "per_date_board_ledger_root_sha256": board_ledger_root,
        },
        "factor_v2_evaluation": {
            "terminal_decision_descriptor_sha256": roots[
                "factor_v2_terminal_decision_descriptor_sha256"
            ],
            "evaluator_descriptor_sha256": roots[
                "factor_v2_evaluator_descriptor_sha256"
            ],
            "cost_slippage_execution_descriptor_sha256": roots[
                "factor_v2_cost_slippage_execution_descriptor_sha256"
            ],
        },
    }
    verified = {
        "feature_history": {
            "verified": True,
            "receipt_sha256": roots[
                "factor_v3_feature_history_verification_receipt_sha256"
            ],
            "source_authority_root_sha256": roots[
                "feature_history_source_authority_root_sha256"
            ],
            "prewindow_session_count": 250,
            "prewindow_sessions_sha256": _sha(prewindow),
            "factor_materialization_eligible": False,
            "formal_factor_materialization_eligible": False,
        },
        "daily_basic": {
            "verified": True,
            "authority_status": "VERIFIED_DEVELOPMENT_DAILY_BASIC_EXACT_SET",
            "coverage_receipt_sha256": roots["daily_basic_coverage_receipt_sha256"],
            "normalized_row_authority_root_sha256": roots[
                "daily_basic_normalized_row_authority_root_sha256"
            ],
            "source_dates_sha256": _sha(source_dates),
            "upstream_board_ledger_root_sha256": board_ledger_root,
            "factor_v3_development_materialization_input_eligible": True,
            "formal_factor_v3_materialization_performed": False,
        },
    }
    return bundle, verified


def _write_bundle(tmp_path: Path, bundle: dict[str, Any]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "input-bundle.json"
    path.write_text(
        json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def _write_input_authority_descriptor(
    tmp_path: Path,
    bundle: dict[str, Any],
    verified: dict[str, Any],
) -> tuple[Path, str]:
    root = tmp_path / "immutable-input-snapshot"
    root.mkdir(parents=True, exist_ok=True)
    source_payloads = {
        "factor_v2_parent": bundle["factor_v2_parent"],
        "calendar": bundle["calendar"],
        "daily_basic": bundle["daily_basic"],
        "daily_traded_cross_section": bundle["daily_traded_cross_section"],
        "listing_membership": bundle["listing_membership"],
        "suspensions": bundle["suspensions"],
        "security_code_transitions": bundle["security_code_transitions"],
        "upstream_board_ledger": bundle["upstream_board_ledger"],
        "factor_v2_evaluation": bundle["factor_v2_evaluation"],
        "feature_history_receipt": verified["feature_history"],
        "daily_basic_exact_set_receipt": verified["daily_basic"],
    }
    roots = bundle["authority_identities"]
    receipt_sha256 = {
        "factor_v2_parent": bundle["factor_v2_parent"]["factor_v2_common_eligible_receipt_sha256"],
        "calendar": roots["extended_trading_calendar_receipt_sha256"],
        "daily_basic": roots["daily_basic_coverage_receipt_sha256"],
        "daily_traded_cross_section": roots["daily_basic_coverage_receipt_sha256"],
        "listing_membership": roots["pit_listing_membership_root_sha256"],
        "suspensions": roots["pit_suspension_root_sha256"],
        "security_code_transitions": roots["security_code_transition_evidence_root_sha256"],
        "upstream_board_ledger": roots["daily_basic_coverage_receipt_sha256"],
        "factor_v2_evaluation": roots["factor_v2_evaluator_descriptor_sha256"],
        "feature_history_receipt": roots[
            "factor_v3_feature_history_verification_receipt_sha256"
        ],
        "daily_basic_exact_set_receipt": roots["daily_basic_coverage_receipt_sha256"],
    }
    producer_root_sha256 = {
        "factor_v2_parent": roots["factor_v2_parent_producer_root_sha256"],
        "calendar": roots["extended_trading_calendar_descriptor_root_sha256"],
        "daily_basic": roots["daily_basic_normalized_row_authority_root_sha256"],
        "daily_traded_cross_section": roots["daily_traded_cross_section_root_sha256"],
        "listing_membership": roots["pit_listing_membership_root_sha256"],
        "suspensions": roots["pit_suspension_root_sha256"],
        "security_code_transitions": roots["security_code_transition_contract_sha256"],
        "upstream_board_ledger": roots["daily_basic_normalized_row_authority_root_sha256"],
        "factor_v2_evaluation": roots["factor_v2_evaluator_descriptor_sha256"],
        "feature_history_receipt": roots["feature_history_source_authority_root_sha256"],
        "daily_basic_exact_set_receipt": roots["daily_basic_normalized_row_authority_root_sha256"],
    }
    descriptors: dict[str, dict[str, str]] = {}
    for name, payload in source_payloads.items():
        path = root / f"{name}.json"
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        path.write_bytes(raw)
        descriptors[name] = {
            "relative_path": path.name,
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "receipt_sha256": receipt_sha256[name],
            "producer_root_sha256": producer_root_sha256[name],
        }
    descriptor = {
        "schema_version": "audited-pit-factor-v3-development-input-authority/v1",
        "authority_status": "VERIFIED_CONCRETE_IMMUTABLE_INPUT_SNAPSHOT",
        "development_only": True,
        "points_contract_sha256": bundle["points_contract_sha256"],
        "authority_identities": roots,
        "parent_payload_fields": ["frozen_target"],
        "source_descriptors": descriptors,
    }
    descriptor_path = root / "input-authority.json"
    raw = json.dumps(
        descriptor,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    descriptor_path.write_bytes(raw)
    return descriptor_path, hashlib.sha256(raw).hexdigest()


def _materialize(
    tmp_path: Path,
    bundle: dict[str, Any],
    verified: dict[str, Any],
) -> dict[str, Any]:
    descriptor_path, descriptor_sha256 = _write_input_authority_descriptor(
        tmp_path,
        bundle,
        verified,
    )
    return materializer.materialize_factor_v3_development_candidate(
        input_authority_descriptor_path=descriptor_path,
        expected_input_authority_descriptor_sha256=descriptor_sha256,
        output_root=tmp_path / "candidates",
    )


def test_materializes_only_development_candidate_with_exact_pit_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)

    result = _materialize(tmp_path, bundle, verified)

    candidate_path = Path(result["candidate_path"])
    receipt_path = Path(result["receipt_path"])
    candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert candidate_path.parts[-4:-1] == ("factor-v3-development-candidates", "sha256", result["artifact_sha256"][:2])
    assert candidate["development_only"] is True
    assert candidate["formal_materialization_performed"] is False
    assert candidate["frozen_points_formal_materializer_implemented"] is False
    assert candidate["formal_receipt_eligible"] is False
    assert candidate["embargo_consumed"] is False
    assert candidate["final_oos_consumed"] is False
    assert candidate["production_recommendation_eligible"] is False
    assert candidate["calendar"]["development_session_count"] == 483
    assert candidate["calendar"]["prewindow_session_count"] == 250
    assert candidate["calendar"]["source_date_union_count"] == 732
    assert candidate["calendar"]["source_dates"] == bundle["daily_basic"]["source_dates"]
    assert candidate["history_eligible_row_count"] == len(bundle["factor_v2_parent"]["rows"])
    assert candidate["excluded_row_count"] == 0
    assert candidate["eligible_candidate_keys_sha256"] == _sha(
        [row["candidate_key"] for row in candidate["rows"]]
    )
    assert candidate["eligible_identity_rows_sha256"] == candidate["output_identity_root_sha256"]
    assert candidate["excluded_candidate_keys_sha256"] == _sha([])
    assert candidate["exclusion_reason_rows_sha256"] == _sha([])
    assert candidate["history_eligible_identity_root_sha256"] == candidate["output_identity_root_sha256"]
    assert {
        arm["identity_root_sha256"] for arm in candidate["arms"].values()
    } == {candidate["output_identity_root_sha256"]}
    assert candidate["arms"]["control"]["feature_names"] == []
    assert candidate["arms"]["turnover_level"]["feature_names"] == ["turnover_rate_f_rank"]
    assert candidate["arms"]["abnormal_turnover"]["feature_names"] == [
        "abnormal_turnover_rate_f_20_to_250_rank"
    ]
    first_date = bundle["calendar"]["development_sessions"][0]
    rows = [row for row in candidate["rows"] if row["signal_date"] == first_date]
    assert [row["candidate_key"] for row in rows] == [
        f"cn-a-share:000002.SZ|{first_date}",
        f"cn-a-share:600001.SH|{first_date}",
    ]
    assert {row["source_input_date_label"] for row in rows} == {
        bundle["calendar"]["prewindow_sessions"][-1]
    }
    assert [row["turnover_rate_f_rank"] for row in rows] == pytest.approx(
        [-1 / 3, 1 / 3]
    )
    assert [row["abnormal_turnover_rate_f_20_to_250_rank"] for row in rows] == pytest.approx([0.0, 0.0])
    assert [row["frozen_target"] for row in rows] == [0.01, -0.01]
    last_date = bundle["calendar"]["development_sessions"][-1]
    last_rows = [row for row in candidate["rows"] if row["signal_date"] == last_date]
    assert [row["abnormal_turnover_rate_f_20_to_250_rank"] for row in last_rows] == pytest.approx(
        [-1 / 3, 1 / 3]
    )
    assert candidate["upstream_board_ledger"]["preserved_before_target_scope_filter"] is True
    assert candidate["upstream_board_ledger"]["source_segments"][:3] == [
        "BSE",
        "SSE_MAIN",
        "SSE_STAR",
    ]
    assert candidate["upstream_board_ledger"]["per_date_board_ledger_root_sha256"] == _sha(
        candidate["upstream_board_ledger"]["per_date"]
    )
    assert set(candidate["source_descriptors"]) == {
        "factor_v2_parent",
        "calendar",
        "daily_basic",
        "daily_traded_cross_section",
        "listing_membership",
        "suspensions",
        "security_code_transitions",
        "upstream_board_ledger",
        "factor_v2_evaluation",
        "feature_history_receipt",
        "daily_basic_exact_set_receipt",
    }
    assert receipt["factor_v2_evaluation"] == bundle["factor_v2_evaluation"]
    assert candidate["producer_binding"]["root_sha256"] == receipt["producer_binding"]["root_sha256"]
    assert "publication_capability" not in candidate_path.read_text(encoding="utf-8")
    assert "publication_capability" not in receipt_path.read_text(encoding="utf-8")
    assert result["verification"]["verified"] is True


def test_fail_closed_when_verified_authority_or_exact_source_date_union_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    verified["daily_basic"]["verified"] = False
    with pytest.raises(ValueError, match="daily-basic.*verified"):
        _materialize(tmp_path, bundle, verified)
    assert not list((tmp_path / "candidates").rglob("*.json"))

    bundle, verified = _bundle(monkeypatch)
    verified["daily_basic"]["source_dates_sha256"] = _sha(["wrong-date"])
    with pytest.raises(ValueError, match="source-date union"):
        _materialize(tmp_path, bundle, verified)
    assert not list((tmp_path / "candidates").rglob("*.json"))


def test_daily_basic_missing_for_authoritatively_traded_security_blocks_whole_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    bundle["daily_basic"]["rows"].pop()
    bundle["daily_basic"]["rows_sha256"] = _sha(bundle["daily_basic"]["rows"])

    with pytest.raises(ValueError, match="daily_basic missing for authoritatively traded"):
        _materialize(tmp_path, bundle, verified)


def test_authorized_history_exclusions_keep_one_shared_subset_for_all_arms(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    source_dates = bundle["daily_basic"]["source_dates"]
    suspended_dates = set(source_dates[:131])
    bundle["daily_basic"]["rows"] = [
        row
        for row in bundle["daily_basic"]["rows"]
        if not (row["security_id"] == "cn-a-share:000001" and row["trade_date"] in suspended_dates)
    ]
    bundle["daily_traded_cross_section"]["rows"] = [
        row
        for row in bundle["daily_traded_cross_section"]["rows"]
        if not (row["security_id"] == "cn-a-share:000001" and row["trade_date"] in suspended_dates)
    ]
    bundle["suspensions"]["rows"] = [
        {"security_id": "cn-a-share:000001", "trade_date": trade_date}
        for trade_date in sorted(suspended_dates)
    ]
    bundle["daily_basic"]["rows_sha256"] = _sha(bundle["daily_basic"]["rows"])
    bundle["daily_traded_cross_section"]["rows_sha256"] = _sha(
        bundle["daily_traded_cross_section"]["rows"]
    )

    result = _materialize(tmp_path, bundle, verified)
    candidate = json.loads(Path(result["candidate_path"]).read_text(encoding="utf-8"))

    assert any(
        row["reason"]
        == "observed_trading_records_less_than_120_in_250_market_session_window"
        for row in candidate["exclusion_ledger"]
    )
    assert {
        arm["identity_root_sha256"] for arm in candidate["arms"].values()
    } == {candidate["output_identity_root_sha256"]}
    assert candidate["excluded_row_count"] + candidate["history_eligible_row_count"] == len(
        bundle["factor_v2_parent"]["rows"]
    )


def test_ipo_and_unresolved_transition_are_preregistered_exclusions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    bundle["listing_membership"]["rows"][0]["listing_date"] = bundle["calendar"][
        "development_sessions"
    ][0]
    result = _materialize(tmp_path, bundle, verified)
    candidate = json.loads(Path(result["candidate_path"]).read_text(encoding="utf-8"))
    assert any(
        row["reason"] == "ipo_age_less_than_6_calendar_months"
        for row in candidate["exclusion_ledger"]
    )

    bundle, verified = _bundle(monkeypatch)
    bundle["security_code_transitions"]["rows"] = bundle["security_code_transitions"]["rows"][1:]
    bundle["security_code_transitions"]["rows_sha256"] = _sha(
        bundle["security_code_transitions"]["rows"]
    )
    result = _materialize(tmp_path, bundle, verified)
    candidate = json.loads(Path(result["candidate_path"]).read_text(encoding="utf-8"))
    assert any(
        row["reason"] == "unresolved_authoritative_security_code_transition"
        for row in candidate["exclusion_ledger"]
    )


def test_short_observation_threshold_uses_the_last_20_market_sessions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    short_window_dates = set(bundle["calendar"]["prewindow_sessions"][-6:])
    bundle["daily_basic"]["rows"] = [
        row
        for row in bundle["daily_basic"]["rows"]
        if not (row["security_id"] == "cn-a-share:000001" and row["trade_date"] in short_window_dates)
    ]
    bundle["daily_traded_cross_section"]["rows"] = [
        row
        for row in bundle["daily_traded_cross_section"]["rows"]
        if not (row["security_id"] == "cn-a-share:000001" and row["trade_date"] in short_window_dates)
    ]
    bundle["suspensions"]["rows"] = [
        {"security_id": "cn-a-share:000001", "trade_date": trade_date}
        for trade_date in sorted(short_window_dates)
    ]
    bundle["daily_basic"]["rows_sha256"] = _sha(bundle["daily_basic"]["rows"])
    bundle["daily_traded_cross_section"]["rows_sha256"] = _sha(
        bundle["daily_traded_cross_section"]["rows"]
    )

    result = _materialize(tmp_path, bundle, verified)
    candidate = json.loads(Path(result["candidate_path"]).read_text(encoding="utf-8"))
    first_date = bundle["calendar"]["development_sessions"][0]
    assert {
        row["reason"]
        for row in candidate["exclusion_ledger"]
        if row["signal_date"] == first_date and row["candidate_key"].startswith("cn-a-share:000002")
    } == {"observed_trading_records_less_than_15_in_20_market_session_window"}


def test_parent_signal_must_be_within_pit_listing_membership_interval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    bundle["listing_membership"]["rows"][0]["membership_end"] = bundle["calendar"][
        "prewindow_sessions"
    ][-1]

    with pytest.raises(ValueError, match="PIT listing membership"):
        _materialize(tmp_path, bundle, verified)


def test_parent_payload_cannot_persist_sensitive_or_unregistered_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    bundle["factor_v2_parent"]["rows"][0]["token"] = "must-never-persist"
    bundle["factor_v2_parent"]["rows_sha256"] = _sha(bundle["factor_v2_parent"]["rows"])

    with pytest.raises(ValueError, match="sensitive|parent payload"):
        _materialize(tmp_path, bundle, verified)


def test_pinned_descriptor_and_every_source_snapshot_are_replayed_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    descriptor_path, descriptor_sha256 = _write_input_authority_descriptor(
        tmp_path,
        bundle,
        verified,
    )
    materialize = materializer.materialize_factor_v3_development_candidate
    assert "verify_feature_history" not in inspect.signature(materialize).parameters
    assert "verify_daily_basic" not in inspect.signature(materialize).parameters
    with pytest.raises(ValueError, match="descriptor content address"):
        materialize(
            input_authority_descriptor_path=descriptor_path,
            expected_input_authority_descriptor_sha256=_sha("foreign-descriptor"),
            output_root=tmp_path / "wrong-descriptor",
        )

    ledger_path = descriptor_path.parent / "upstream_board_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["per_date"][0]["segment_counts"]["BSE"] = 999
    ledger_path.write_text(
        json.dumps(ledger, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="source snapshot content address"):
        materialize(
            input_authority_descriptor_path=descriptor_path,
            expected_input_authority_descriptor_sha256=descriptor_sha256,
            output_root=tmp_path / "tampered-source",
        )

    clean_descriptor_path, clean_descriptor_sha256 = _write_input_authority_descriptor(
        tmp_path / "fresh",
        bundle,
        verified,
    )
    extra_path = clean_descriptor_path.parent / "unbound.json"
    extra_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="unbound file"):
        materialize(
            input_authority_descriptor_path=clean_descriptor_path,
            expected_input_authority_descriptor_sha256=clean_descriptor_sha256,
            output_root=tmp_path / "unbound-source",
        )


def test_create_only_post_verifier_and_unsafe_inputs_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, verified = _bundle(monkeypatch)
    result = _materialize(tmp_path, bundle, verified)
    candidate_path = Path(result["candidate_path"])
    receipt_path = Path(result["receipt_path"])
    before_mtime = candidate_path.stat().st_mtime_ns

    repeated = _materialize(tmp_path, bundle, verified)
    assert repeated["candidate_path"] == result["candidate_path"]
    assert candidate_path.stat().st_mtime_ns == before_mtime

    candidate_path.write_text("foreign replacement", encoding="utf-8")
    with pytest.raises(ValueError, match="content-addressed candidate"):
        _materialize(tmp_path, bundle, verified)

    bundle, verified = _bundle(monkeypatch)
    bundle["factor_v2_parent"]["rows"][0]["publication_capability"] = "must-never-persist"
    bundle["factor_v2_parent"]["rows_sha256"] = _sha(bundle["factor_v2_parent"]["rows"])
    with pytest.raises(ValueError, match="capability"):
        _materialize(tmp_path / "capability", bundle, verified)

    bundle, verified = _bundle(monkeypatch)
    descriptor_path, descriptor_sha256 = _write_input_authority_descriptor(
        tmp_path / "wal",
        bundle,
        verified,
    )
    Path(f"{descriptor_path}-wal").write_text("live-store-sidecar", encoding="utf-8")
    with pytest.raises(ValueError, match="WAL|SHM|live store"):
        materializer.materialize_factor_v3_development_candidate(
            input_authority_descriptor_path=descriptor_path,
            expected_input_authority_descriptor_sha256=descriptor_sha256,
            output_root=tmp_path / "wal-candidates",
        )

    receipt_path.write_text("tampered", encoding="utf-8")
    descriptor_path, descriptor_sha256 = _write_input_authority_descriptor(
        tmp_path / "verify",
        bundle,
        verified,
    )
    with pytest.raises(ValueError, match="receipt"):
        materializer.verify_factor_v3_development_candidate(
            candidate_path=candidate_path,
            receipt_path=receipt_path,
            input_authority_descriptor_path=descriptor_path,
            expected_input_authority_descriptor_sha256=descriptor_sha256,
            expected_artifact_sha256=result["artifact_sha256"],
            expected_receipt_sha256=result["receipt_sha256"],
        )
