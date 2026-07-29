from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from app import audited_pit_factor_v3_points_contract as points_contract_module
from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_ARMS,
    FACTOR_V3_POINTS_ARM_STRATEGY_SHA256,
    FACTOR_V3_POINTS_CONTRACT,
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    FACTOR_V3_POINTS_FEATURE_NAMES,
    FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256,
    canonical_sha256,
    compute_abnormal_turnover_rate_f_20_to_250,
    factor_v3_points_arm_contract,
    parse_strict_factor_v3_json,
    preview_factor_v3_points_rows_unbound,
)


def _sessions(count: int = 251) -> list[dict[str, object]]:
    start = date(2025, 1, 1)
    return [
        {
            "session_position": position,
            "trade_date": (start + timedelta(days=position)).isoformat(),
        }
        for position in range(count)
    ]


def _fixture() -> tuple[
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    sessions = _sessions()
    signal_date = sessions[-1]["trade_date"]
    parents = [
        {
            "candidate_key": f"cn-a-share:{ts_code}|{signal_date}",
            "signal_date": signal_date,
            "ts_code": ts_code,
        }
        for ts_code in ("000001.SZ", "300001.SZ", "600001.SH")
    ]
    source_sessions = sessions[:-1]
    daily_basic: list[dict[str, object]] = []
    for ts_code in ("000001.SZ", "300001.SZ", "600001.SH"):
        for position, session in enumerate(source_sessions):
            if ts_code == "000001.SZ":
                turnover = 1.0
            elif ts_code == "300001.SZ":
                turnover = 2.0
            else:
                turnover = 6.0 if position >= 230 else 3.0
            daily_basic.append(
                {
                    "ts_code": ts_code,
                    "trade_date": session["trade_date"],
                    "turnover_rate_f": turnover,
                }
            )
    return sessions, parents, daily_basic


def _preview(
    sessions: list[dict[str, object]],
    parents: list[dict[str, object]],
    daily_basic: list[dict[str, object]],
) -> dict[str, object]:
    return preview_factor_v3_points_rows_unbound(
        sessions=sessions,
        parent_rows=parents,
        daily_basic_rows=daily_basic,
    )


def test_contract_freezes_scope_lag_features_arms_and_safety_gate() -> None:
    assert FACTOR_V3_POINTS_FEATURE_NAMES == (
        "turnover_rate_f_rank",
        "abnormal_turnover_rate_f_20_to_250_rank",
    )
    assert dict(FACTOR_V3_POINTS_ARMS) == {
        "control": (),
        "turnover_level": ("turnover_rate_f_rank",),
        "abnormal_turnover": ("abnormal_turnover_rate_f_20_to_250_rank",),
    }
    assert FACTOR_V3_POINTS_CONTRACT["arm_order"] == [
        "control",
        "turnover_level",
        "abnormal_turnover",
    ]
    assert "combined" not in FACTOR_V3_POINTS_CONTRACT["arms"]
    assert FACTOR_V3_POINTS_CONTRACT["combination_search_permitted"] is False

    scope = FACTOR_V3_POINTS_CONTRACT["market_scope"]
    assert scope["policy_id"] == "research-mainboard-chinext/v1"
    assert scope["excluded_boards"] == ["science_technology", "beijing"]

    sources = FACTOR_V3_POINTS_CONTRACT["sources"]
    assert sources["daily_basic"]["lag_market_sessions"] == 1
    assert sources["moneyflow"]["lag_market_sessions"] == 1
    assert sources["daily_basic"]["recommendation_1502_uses"] == "T-1"
    assert sources["moneyflow"]["recommendation_1502_uses"] == "T-1"
    assert sources["moneyflow"]["diagnostic_only"] is True
    assert sources["moneyflow"]["hard_filter_permitted"] is False
    assert sources["moneyflow"]["first_round_arm_permitted"] is False

    abnormal = FACTOR_V3_POINTS_CONTRACT["features"]["abnormal_turnover_rate_f_20_to_250_rank"]
    assert abnormal["raw_formula"] == "log(mean_20/mean_250)"
    assert abnormal["window_market_sessions"] == {
        "short": 20,
        "long": 250,
    }
    assert abnormal["minimum_observed_trading_records"] == {
        "short_window": 15,
        "long_window": 120,
    }
    assert abnormal["minimum_ipo_age_calendar_months"] == 6
    assert abnormal["missing_observation_fill"] == "none"
    assert abnormal["latest_source_session"] == "T-1"
    assert abnormal["literature_replication_claimed"] is False
    assert abnormal["variant"] == "free_float_turnover_rate_f"
    assert abnormal["paper_original_turnover_denominator"] == "total_shares"
    assert abnormal["project_turnover_denominator"] == "free_float_shares"
    assert {feature["scope"] for feature in FACTOR_V3_POINTS_CONTRACT["features"].values()} == {
        "same_signal_date_exact_history_eligible_common_subset"
    }

    warnings = FACTOR_V3_POINTS_CONTRACT["research_warnings"]
    assert warnings["china_anomaly_replication_insignificant_pct"] == 83.37
    assert warnings["paper_return_is_expected_to_transfer"] is False
    assert warnings["expand_search_from_paper_result_permitted"] is False
    assert warnings["project_frozen_oof_is_only_return_authority"] is True
    assert FACTOR_V3_POINTS_CONTRACT["source_anchors"] == {
        "abnormal_turnover_literature": {
            "title": "Size and Value in China",
            "doi": "10.1016/j.jfineco.2019.03.008",
        },
        "china_anomaly_replication": {
            "title": ("Replicating and Digesting Anomalies in the Chinese A-share Market"),
            "doi": "10.1287/mnsc.2023.4904",
        },
        "daily_basic_documentation": ("https://tushare.pro/document/2?doc_id=32"),
    }

    gate = FACTOR_V3_POINTS_CONTRACT["experiment_gate"]
    assert gate["factor_v2_verified_terminal_decision_required"] is True
    assert gate["factor_v2_verified_terminal_decision_present"] is False
    assert gate["experiment_launch_eligible"] is False
    prerequisites = FACTOR_V3_POINTS_CONTRACT["formal_materialization_prerequisites"]
    assert prerequisites == {
        "verified_factor_v2_parent_descriptor": {
            "required": True,
            "present": False,
            "descriptor_sha256": None,
        },
        "authoritative_extended_trading_calendar_descriptor": {
            "required": True,
            "present": False,
            "descriptor_root_sha256": None,
        },
        "verified_jiaoch_points_collection_and_normalized_row_authority": {
            "required": True,
            "present": False,
            "collection_set_root_sha256": None,
            "normalized_row_authority_root_sha256": None,
            "source_bound_context_required": True,
            "source_bound_context_root_sha256": None,
        },
        "all_present": False,
        "formal_materializer_implemented": False,
    }
    preview_policy = FACTOR_V3_POINTS_CONTRACT["preview_policy"]
    assert preview_policy["authority_status"] == "UNBOUND_PREVIEW_ONLY"
    assert preview_policy["math_semantics"] == (
        "strict_complete_250_row_synthetic_diagnostic_not_formal_eligibility"
    )
    assert preview_policy["exact_250_observed_rows_required_by_preview_only"] is True
    assert preview_policy["formal_history_eligibility_implemented"] is False
    assert preview_policy["parent_authority_verified"] is False
    assert preview_policy["session_calendar_authority_verified"] is False
    assert preview_policy["daily_basic_row_authority_verified"] is False
    assert preview_policy["formal_materialization_performed"] is False
    assert preview_policy["ordered_date_labels_are_market_sessions"] is False
    assert preview_policy["pit_claimed"] is False
    assert FACTOR_V3_POINTS_CONTRACT["embargo_consumed"] is False
    assert FACTOR_V3_POINTS_CONTRACT["final_oos_consumed"] is False
    assert FACTOR_V3_POINTS_CONTRACT["production_profile_registered"] is False
    assert FACTOR_V3_POINTS_CONTRACT["production_recommendation_eligible"] is False
    assert canonical_sha256(FACTOR_V3_POINTS_CONTRACT) == (FACTOR_V3_POINTS_CONTRACT_SHA256)
    assert FACTOR_V3_POINTS_PARENT_EXPECTATION_SHA256 == (
        "25f2802cfa11fa61a3e08f44886141bd82c01c89e7d9ee01a3e882b8a3561c99"
    )
    assert FACTOR_V3_POINTS_CONTRACT_SHA256 == (
        "699bfb91aeed1523168ac1604f4fc920e21723fcbefbcdb868b390394f2e17b2"
    )
    assert dict(FACTOR_V3_POINTS_ARM_STRATEGY_SHA256) == {
        "control": "36b47deda4ca4b300b090586a3f69147939c34148ac764fea04463bae3e7adfc",
        "turnover_level": "c4291b2d0e52cad77e7338032d6fde2a7b9b5ce7f813e6594516752f23df2b98",
        "abnormal_turnover": "1e64701d826492af3043b0846b445597d2678c91070de2a1559f1c3003b3fb8a",
    }


def test_formal_parent_expectation_binds_common_eligible_overlay() -> None:
    parent = FACTOR_V3_POINTS_CONTRACT["preregistered_parent_expectation"]

    assert parent["schema_version"] == ("audited-pit-factor-v3-points-parent-expectation/v2")
    assert parent["sample_reference"] == ("factor_v2_development_4_common_eligible_sample")
    assert parent["factor_v2_common_eligible_overlay_artifact_sha256"] == (
        "abd4b2166da4520952d7bfc5c8a988bc0a8027dd576a90ae0fdda2560b3a02f8"
    )
    assert parent["factor_v2_common_eligible_overlay_manifest_file_sha256"] == (
        "9131f15e13f247a4663fae658af544b94bf2af01a3494b8eb0a3d0c095ee1312"
    )
    assert parent["factor_v2_common_eligible_receipt_sha256"] == (
        "86199759116362c6317db7ca73b78dc56c9adaada57c2661a4b33028be44db4d"
    )
    assert parent["original_parent_feature_row_count"] == 1_796_835
    assert parent["original_parent_feature_rows_sha256"] == (
        "7cbd9bfe61052f87d736f6a7d14fdc1ad350ce66add98347ea36d9c2ed48b337"
    )
    assert parent["common_eligible_candidate_count"] == 1_796_834
    assert parent["common_eligible_candidate_keys_sha256"] == (
        "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
    )
    assert parent["common_eligible_source_feature_rows_sha256"] == (
        "62f02c3d3b068f50b95d29a06a218e58dd72693113081570ded95ae73d7ec59f"
    )
    assert parent["preregistered_suspension_excluded_candidate_count"] == 1
    assert parent["preregistered_suspension_excluded_candidate_keys_sha256"] == (
        "a2149f2a5de78780459652aaf64bcb940ab2ad7631dd3260007f1ad1ec1ab18a"
    )
    assert "parent_feature_row_count" not in parent
    assert "parent_feature_rows_sha256" not in parent


def test_formal_policy_preregisters_one_fail_closed_history_subset_for_all_arms() -> None:
    policy = FACTOR_V3_POINTS_CONTRACT["formal_parent_sample_policy"]

    assert policy["source_sample_reference"] == ("factor_v2_development_4_common_eligible_sample")
    assert policy["source_candidate_count"] == 1_796_834
    assert policy["source_candidate_keys_sha256"] == (
        "ded45539b436764ee9c8bf45329105444a735e46f56fa90d7521a40ce9538544"
    )
    assert policy["coverage"] == ("deterministic_history_eligible_subset_of_common_eligible_parent")
    assert policy["same_history_eligible_subset_for_all_arms"] is True
    assert policy["arbitrary_row_drops_permitted"] is False
    assert policy["silent_row_drops_permitted"] is False
    assert policy["zero_fill_permitted"] is False
    assert policy["output_identity_must_equal_history_eligible_subset_identity"] is True

    subset = policy["history_eligible_subset_policy"]
    assert subset["schema_version"] == (
        "audited-pit-factor-v3-points-history-eligible-subset-policy/v1"
    )
    assert subset["window_market_sessions"] == {
        "short": 20,
        "long": 250,
    }
    assert subset["minimum_observed_trading_records"] == {
        "short_window": 15,
        "long_window": 120,
    }
    assert subset["minimum_ipo_age_calendar_months"] == 6
    assert subset["latest_usable_source_session"] == "T-1"
    assert subset["allowed_exclusion_reasons"] == [
        "ipo_age_less_than_6_calendar_months",
        "observed_trading_records_less_than_15_in_20_market_session_window",
        "observed_trading_records_less_than_120_in_250_market_session_window",
        "unresolved_authoritative_security_code_transition",
    ]
    assert subset["formal_materialization_blocking_conditions"] == [
        "authoritative_daily_cross_section_exact_set_coverage_failed",
        "daily_basic_missing_for_authoritatively_traded_security",
        "unaccounted_or_unproven_source_missingness",
    ]
    assert subset["exact_250_observed_rows_required"] is False
    assert subset["missing_observation_fill"] == "none"
    assert subset["authoritative_suspension_rows_count_as_observed"] is False
    assert subset["exclusion_record_identity_fields"] == [
        "candidate_key",
        "reason",
        "authority_evidence_root_sha256",
    ]
    assert subset["excluded_candidate_count_required"] is True
    assert subset["excluded_candidate_keys_sha256_required"] is True
    assert subset["exclusion_reason_rows_sha256_required"] is True
    assert subset["eligible_candidate_count_required"] is True
    assert subset["eligible_candidate_keys_sha256_required"] is True
    assert subset["eligible_identity_rows_sha256_required"] is True
    assert subset["unrecognized_or_unproven_missingness_policy"] == "fail_closed"

    sample_policy_sha256 = canonical_sha256(policy)
    assert {
        factor_v3_points_arm_contract(arm)["sample_policy_sha256"] for arm in FACTOR_V3_POINTS_ARMS
    } == {sample_policy_sha256}


def test_arm_contracts_are_individually_content_addressed() -> None:
    contracts = {arm: factor_v3_points_arm_contract(arm) for arm in FACTOR_V3_POINTS_ARMS}
    for arm, contract in contracts.items():
        assert contract["arm"] == arm
        assert contract["points_feature_names"] == list(FACTOR_V3_POINTS_ARMS[arm])
        assert contract["factor_v3_points_contract_sha256"] == (FACTOR_V3_POINTS_CONTRACT_SHA256)
        assert contract["strategy_sha256"] == canonical_sha256(
            {key: value for key, value in contract.items() if key != "strategy_sha256"}
        )
    assert len({value["strategy_sha256"] for value in contracts.values()}) == 3
    with pytest.raises(ValueError, match="frozen"):
        factor_v3_points_arm_contract("combined")


def test_abnormal_turnover_uses_exact_20_over_250_log_ratio() -> None:
    history = [3.0] * 230 + [6.0] * 20
    observed = compute_abnormal_turnover_rate_f_20_to_250(history)
    assert observed == pytest.approx(math.log(6.0 / 3.24))


@pytest.mark.parametrize(
    ("history", "match"),
    [
        ([1.0] * 249, "250"),
        ([1.0] * 249 + [float("nan")], "finite"),
        ([1.0] * 249 + [True], "bool"),
        ([0.0] * 250, "positive"),
        ([-1.0] * 250, "non-negative"),
    ],
)
def test_abnormal_turnover_rejects_invalid_history(
    history: list[object],
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        compute_abnormal_turnover_rate_f_20_to_250(history)


def test_unbound_preview_uses_last_input_label_and_deterministic_midranks() -> None:
    sessions, parents, daily_basic = _fixture()
    original = deepcopy((sessions, parents, daily_basic))

    result = _preview(sessions, parents, daily_basic)

    assert (sessions, parents, daily_basic) == original
    assert set(result) == {"preview_rows", "unbound_preview_receipt"}
    rows = result["preview_rows"]
    assert [row["candidate_key"] for row in rows] == [
        parents[0]["candidate_key"],
        parents[1]["candidate_key"],
        parents[2]["candidate_key"],
    ]
    assert {row["source_input_date_label"] for row in rows} == {sessions[-2]["trade_date"]}
    assert [row["turnover_rate_f_rank"] for row in rows] == pytest.approx([-0.5, 0.0, 0.5])
    assert [row["abnormal_turnover_rate_f_20_to_250_rank"] for row in rows] == pytest.approx(
        [-0.25, -0.25, 0.5]
    )
    receipt = result["unbound_preview_receipt"]
    assert receipt["schema_version"] == ("audited-pit-factor-v3-points-unbound-preview/v1")
    assert receipt["authority_status"] == "UNBOUND_PREVIEW_ONLY"
    assert receipt["row_authority_status"] == "NOT_GRANTED"
    assert receipt["receipt_sha256_semantics"] == ("preview_self_integrity_only_not_authority")
    assert receipt["parent_input_row_count"] == len(parents)
    assert receipt["preview_output_row_count"] == len(parents)
    assert receipt["preview_dropped_input_row_count"] == 0
    assert receipt["parent_authority_verified"] is False
    assert receipt["session_calendar_authority_verified"] is False
    assert receipt["daily_basic_row_authority_verified"] is False
    assert receipt["formal_materialization_performed"] is False
    assert receipt["formal_receipt_eligible"] is False
    assert receipt["ordered_date_labels_are_market_sessions"] is False
    assert receipt["pit_claimed"] is False
    assert receipt["experiment_launch_eligible"] is False
    assert receipt["embargo_consumed"] is False
    assert receipt["final_oos_consumed"] is False
    assert receipt["production_profile_registered"] is False
    assert receipt["production_recommendation_eligible"] is False
    assert "parent_binding_root_sha256" not in receipt
    assert "parent_identity_root_sha256" not in receipt
    assert "output_identity_root_sha256" not in receipt
    assert receipt["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )


def test_unbound_preview_requires_full_exact_local_source_union() -> None:
    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="missing"):
        _preview(sessions, parents, daily_basic[:-1])

    extra = {
        "ts_code": "000001.SZ",
        "trade_date": sessions[-1]["trade_date"],
        "turnover_rate_f": 999.0,
    }
    with pytest.raises(ValueError, match="unused"):
        _preview(sessions, parents, [*daily_basic, extra])


def test_old_formal_materializer_name_is_not_public() -> None:
    assert not hasattr(
        points_contract_module,
        "materialize_factor_v3_points_rows",
    )
    assert not hasattr(
        points_contract_module,
        "factor_v3_parent_identity_root",
    )
    assert not hasattr(
        points_contract_module,
        "FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256",
    )


def test_natural_dates_and_arbitrary_subsample_remain_unbound_preview() -> None:
    sessions, parents, daily_basic = _fixture()
    one_parent = parents[:1]
    one_symbol_rows = [row for row in daily_basic if row["ts_code"] == one_parent[0]["ts_code"]]

    result = _preview(sessions, one_parent, one_symbol_rows)

    assert len(result["preview_rows"]) == 1
    receipt = result["unbound_preview_receipt"]
    assert receipt["authority_status"] == "UNBOUND_PREVIEW_ONLY"
    assert receipt["parent_authority_verified"] is False
    assert receipt["session_calendar_authority_verified"] is False
    assert receipt["daily_basic_row_authority_verified"] is False
    assert receipt["formal_materialization_performed"] is False
    assert receipt["formal_receipt_eligible"] is False


@pytest.mark.parametrize("ts_code", ["688001.SH", "920001.BJ", "430001.BJ"])
def test_preview_rejects_excluded_boards(ts_code: str) -> None:
    sessions, parents, daily_basic = _fixture()
    parents[0]["ts_code"] = ts_code
    with pytest.raises(ValueError, match="scope"):
        _preview(sessions, parents, daily_basic)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("session", "unexpected"),
        ("parent", "unexpected"),
        ("daily", "unexpected"),
    ],
)
def test_preview_rejects_unknown_fields(
    target: str,
    field: str,
) -> None:
    sessions, parents, daily_basic = _fixture()
    values = {
        "session": sessions,
        "parent": parents,
        "daily": daily_basic,
    }[target]
    values[0][field] = "not permitted"
    with pytest.raises(ValueError, match="fields"):
        _preview(sessions, parents, daily_basic)


def test_preview_rejects_bool_nan_and_duplicates() -> None:
    sessions, parents, daily_basic = _fixture()
    daily_basic[0]["turnover_rate_f"] = True
    with pytest.raises(ValueError, match="bool"):
        _preview(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    daily_basic[0]["turnover_rate_f"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        _preview(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="duplicate parent"):
        _preview(sessions, [*parents, dict(parents[0])], daily_basic)

    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="duplicate daily_basic"):
        _preview(
            sessions,
            parents,
            [*daily_basic, dict(daily_basic[0])],
        )


def test_input_labels_must_have_consecutive_positions_unique_ordered_dates() -> None:
    sessions, parents, daily_basic = _fixture()
    sessions[10]["session_position"] = 11
    with pytest.raises(ValueError, match="consecutive"):
        _preview(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    sessions[10]["trade_date"] = sessions[9]["trade_date"]
    with pytest.raises(ValueError, match="ordered and unique"):
        _preview(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    sessions[10]["session_position"] = True
    with pytest.raises(ValueError, match="bool"):
        _preview(sessions, parents, daily_basic)


def test_strict_json_rejects_duplicate_keys_nan_and_non_object() -> None:
    assert parse_strict_factor_v3_json('{"a":1,"b":[2]}') == {
        "a": 1,
        "b": [2],
    }
    with pytest.raises(ValueError, match="duplicate"):
        parse_strict_factor_v3_json('{"a":1,"a":2}')
    with pytest.raises(ValueError, match="non-finite"):
        parse_strict_factor_v3_json('{"a":NaN}')
    with pytest.raises(ValueError, match="object"):
        parse_strict_factor_v3_json("[1,2]")
