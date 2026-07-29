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
    assert abnormal["minimum_history_market_sessions"] == 250
    assert abnormal["latest_source_session"] == "T-1"
    assert abnormal["literature_replication_claimed"] is False
    assert abnormal["variant"] == "free_float_turnover_rate_f"

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
        "5267707efcbee9088fd44ddc44f68bb686982d75b97514a091db79620e7b289f"
    )
    assert FACTOR_V3_POINTS_CONTRACT_SHA256 == (
        "ab34593e75abad87e41ab82b2961a4cb7177d4d3568cf605b7afa2d227699245"
    )
    assert dict(FACTOR_V3_POINTS_ARM_STRATEGY_SHA256) == {
        "control": "f72026779fb336825ead11b10a0b8ea91e102ea667b9a5082d2c9c8b2c534483",
        "turnover_level": "363a7e8a4a99aa8c3645bcba62dcd4c69da05c48b7fe365de88887dab813f7c8",
        "abnormal_turnover": "87d75a57de221b2d66e52ab378da60ba270d348419a640eb6594a240837f5cc0",
    }


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
