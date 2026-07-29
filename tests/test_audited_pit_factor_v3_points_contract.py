from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import math

import pytest

from app.audited_pit_factor_v3_points_contract import (
    FACTOR_V3_POINTS_ARMS,
    FACTOR_V3_POINTS_ARM_STRATEGY_SHA256,
    FACTOR_V3_POINTS_CONTRACT,
    FACTOR_V3_POINTS_CONTRACT_SHA256,
    FACTOR_V3_POINTS_FEATURE_NAMES,
    FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256,
    canonical_sha256,
    compute_abnormal_turnover_rate_f_20_to_250,
    factor_v3_parent_identity_root,
    factor_v3_points_arm_contract,
    materialize_factor_v3_points_rows,
    parse_strict_factor_v3_json,
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


def _materialize(
    sessions: list[dict[str, object]],
    parents: list[dict[str, object]],
    daily_basic: list[dict[str, object]],
) -> dict[str, object]:
    return materialize_factor_v3_points_rows(
        sessions=sessions,
        parent_rows=parents,
        daily_basic_rows=daily_basic,
        parent_binding_root_sha256=(FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256),
        expected_parent_identity_root_sha256=(factor_v3_parent_identity_root(parents)),
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
    assert FACTOR_V3_POINTS_CONTRACT["embargo_consumed"] is False
    assert FACTOR_V3_POINTS_CONTRACT["final_oos_consumed"] is False
    assert FACTOR_V3_POINTS_CONTRACT["production_profile_registered"] is False
    assert FACTOR_V3_POINTS_CONTRACT["production_recommendation_eligible"] is False
    assert canonical_sha256(FACTOR_V3_POINTS_CONTRACT) == (FACTOR_V3_POINTS_CONTRACT_SHA256)
    assert FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256 == (
        "de7c9a3715631d186e730d775673df1ed753717be44029b6c12dba34e56a5ef5"
    )
    assert FACTOR_V3_POINTS_CONTRACT_SHA256 == (
        "791ca95296969424f0255ef7aeb011e98b4051e0e2b62dbdac12213716b3448b"
    )
    assert dict(FACTOR_V3_POINTS_ARM_STRATEGY_SHA256) == {
        "control": "cd6e0e40954c5ad7f956b10b9426d3e40ce07e7e3cd6292baa7a4a32620259f9",
        "turnover_level": "0d9bd4d0801e0f6a71e8c9d509cb3cb417efa8ad895c152d9363dff43a93459a",
        "abnormal_turnover": "a0754536544368a9b3743f5b6f7a0ced62529a2bbbae85f0f6aa0d4f828028b9",
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


def test_materialization_uses_t_minus_1_and_deterministic_midranks() -> None:
    sessions, parents, daily_basic = _fixture()
    original = deepcopy((sessions, parents, daily_basic))

    result = _materialize(sessions, parents, daily_basic)

    assert (sessions, parents, daily_basic) == original
    rows = result["rows"]
    assert [row["candidate_key"] for row in rows] == [
        parents[0]["candidate_key"],
        parents[1]["candidate_key"],
        parents[2]["candidate_key"],
    ]
    assert {row["source_session"] for row in rows} == {sessions[-2]["trade_date"]}
    assert [row["turnover_rate_f_rank"] for row in rows] == pytest.approx([-0.5, 0.0, 0.5])
    assert [row["abnormal_turnover_rate_f_20_to_250_rank"] for row in rows] == pytest.approx(
        [-0.25, -0.25, 0.5]
    )
    receipt = result["receipt"]
    assert receipt["parent_row_count"] == len(parents)
    assert receipt["output_row_count"] == len(parents)
    assert receipt["dropped_parent_row_count"] == 0
    assert receipt["parent_identity_root_sha256"] == (factor_v3_parent_identity_root(parents))
    assert receipt["output_identity_root_sha256"] == (receipt["parent_identity_root_sha256"])
    assert receipt["receipt_sha256"] == canonical_sha256(
        {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    )


def test_materialization_requires_full_exact_daily_basic_source_union() -> None:
    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="missing"):
        _materialize(sessions, parents, daily_basic[:-1])

    extra = {
        "ts_code": "000001.SZ",
        "trade_date": sessions[-1]["trade_date"],
        "turnover_rate_f": 999.0,
    }
    with pytest.raises(ValueError, match="unused"):
        _materialize(sessions, parents, [*daily_basic, extra])


def test_materialization_rejects_parent_root_drift() -> None:
    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="parent binding"):
        materialize_factor_v3_points_rows(
            sessions=sessions,
            parent_rows=parents,
            daily_basic_rows=daily_basic,
            parent_binding_root_sha256="0" * 64,
            expected_parent_identity_root_sha256=(factor_v3_parent_identity_root(parents)),
        )
    with pytest.raises(ValueError, match="parent identity"):
        materialize_factor_v3_points_rows(
            sessions=sessions,
            parent_rows=parents,
            daily_basic_rows=daily_basic,
            parent_binding_root_sha256=(FACTOR_V3_POINTS_PARENT_BINDING_ROOT_SHA256),
            expected_parent_identity_root_sha256="0" * 64,
        )


@pytest.mark.parametrize("ts_code", ["688001.SH", "920001.BJ", "430001.BJ"])
def test_materialization_rejects_excluded_boards(ts_code: str) -> None:
    sessions, parents, daily_basic = _fixture()
    parents[0]["ts_code"] = ts_code
    with pytest.raises(ValueError, match="scope"):
        factor_v3_parent_identity_root(parents)


@pytest.mark.parametrize(
    ("target", "field"),
    [
        ("session", "unexpected"),
        ("parent", "unexpected"),
        ("daily", "unexpected"),
    ],
)
def test_materialization_rejects_unknown_fields(
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
        _materialize(sessions, parents, daily_basic)


def test_materialization_rejects_bool_nan_and_duplicates() -> None:
    sessions, parents, daily_basic = _fixture()
    daily_basic[0]["turnover_rate_f"] = True
    with pytest.raises(ValueError, match="bool"):
        _materialize(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    daily_basic[0]["turnover_rate_f"] = float("nan")
    with pytest.raises(ValueError, match="finite"):
        _materialize(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="duplicate parent"):
        _materialize(sessions, [*parents, dict(parents[0])], daily_basic)

    sessions, parents, daily_basic = _fixture()
    with pytest.raises(ValueError, match="duplicate daily_basic"):
        _materialize(
            sessions,
            parents,
            [*daily_basic, dict(daily_basic[0])],
        )


def test_sessions_must_have_consecutive_positions_unique_ordered_dates() -> None:
    sessions, parents, daily_basic = _fixture()
    sessions[10]["session_position"] = 11
    with pytest.raises(ValueError, match="consecutive"):
        _materialize(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    sessions[10]["trade_date"] = sessions[9]["trade_date"]
    with pytest.raises(ValueError, match="ordered and unique"):
        _materialize(sessions, parents, daily_basic)

    sessions, parents, daily_basic = _fixture()
    sessions[10]["session_position"] = True
    with pytest.raises(ValueError, match="bool"):
        _materialize(sessions, parents, daily_basic)


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
