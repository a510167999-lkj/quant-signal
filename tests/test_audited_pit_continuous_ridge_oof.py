from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app import audited_pit_continuous_ridge_oof as ridge


def _sessions(count: int) -> list[str]:
    start = date(2025, 1, 2)
    return [(start + timedelta(days=index)).isoformat() for index in range(count)]


def _bars(
    sessions: list[str],
    *,
    ts_codes: tuple[str, ...] = (
        "000001.SZ",
        "300001.SZ",
        "688001.SH",
        "430001.BJ",
    ),
) -> pd.DataFrame:
    rows = []
    for ts_code in ts_codes:
        for index, trade_date in enumerate(sessions):
            if ts_code == "000001.SZ":
                close = 100.0 + index
            elif ts_code == "300001.SZ":
                close = 200.0
            else:
                close = 300.0 + index
            rows.append(
                {
                    "date": trade_date,
                    "ts_code": ts_code,
                    "source_ts_code": ts_code,
                    "security_id": f"cn-a-share:{ts_code}",
                    "security_code_transition_id": None,
                    "security_code_transition_contract_sha256": None,
                    "open": close - 0.5,
                    "high": close + 1.0,
                    "low": close - 1.0,
                    "close": close,
                    "pre_close": close - 1.0,
                    "amount": 1_000.0 + index,
                    "adj_factor": 1.0,
                    "suspended": False,
                    "membership_name": "正常公司",
                    "membership_industry": (
                        "制造"
                        if ts_code != "300001.SZ"
                        else "软件"
                    ),
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": trade_date,
                    "membership_list_date": "2020-01-01",
                }
            )
    return pd.DataFrame(rows)


def test_frozen_spec_is_ranked_liquidity_oof_and_mainboard_chinext_only():
    spec = ridge.CONTINUOUS_RIDGE_OOF_SPEC

    assert (
        spec["schema_version"]
        == "development-pit-cross-sectional-ranked-liquidity-ridge-oof/v2"
    )
    assert spec["features"] == list(ridge.FEATURE_NAMES)
    assert spec["raw_stock_features"] == list(
        ridge.RAW_STOCK_FEATURE_NAMES
    )
    assert len(ridge.RAW_STOCK_FEATURE_NAMES) == 8
    assert len(ridge.FEATURE_NAMES) == 10
    assert spec["cross_section_transform"] == {
        "method": "deterministic_midrank",
        "mapping": "2*midrank/(N+1)-1",
        "tie_policy": "equal_raw_values_share_average_rank",
        "missing_policy": "reject",
    }
    assert spec["model"]["ridge_lambda"] == 1.0
    assert spec["model"]["loss"] == "squared_error"
    assert spec["model"]["hyperparameter_search"] is False
    assert spec["walk_forward"] == {
        "minimum_training_sessions": 126,
        "validation_sessions": 63,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "folds": "continuous_non_overlapping_validation_windows",
    }
    assert spec["selection"]["minimum_predicted_net_return_pct"] == 0.0
    assert spec["market_scope"]["allowed_boards"] == [
        "shanghai_main",
        "shenzhen_main",
        "chinext",
    ]
    assert spec["market_scope"]["excluded_boards"] == [
        "science_technology",
        "beijing",
    ]


def test_rolling_v3_spec_changes_only_training_memory_and_identity():
    v2 = deepcopy(ridge.CONTINUOUS_RIDGE_OOF_SPEC)
    v3 = deepcopy(ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC)

    assert v3["schema_version"] == (
        "development-pit-cross-sectional-ranked-liquidity-"
        "ridge-rolling-oof/v3"
    )
    assert v3["signal_tag"] == (
        "cross_sectional_ranked_liquidity_ridge_rolling_126_oof"
    )
    assert v3["walk_forward"] == {
        "minimum_training_sessions": 126,
        "training_window_type": "trailing_frozen_signal_sessions",
        "training_window_sessions": 126,
        "validation_sessions": 63,
        "purge": "complete_exit_date_strictly_before_validation_start",
        "folds": "continuous_non_overlapping_validation_windows",
    }
    assert ridge._sha256(v3) == (
        "c5730311c3660cf4afa8fae5c442b80622c16cb4633bd26c5d5a889525fb3be4"
    )

    for spec in (v2, v3):
        spec.pop("schema_version")
        spec.pop("signal_tag")
        spec.pop("walk_forward")
    assert v3 == v2


def test_rolling_public_runner_rejects_any_strategy_drift(
    monkeypatch,
    tmp_path,
):
    from app.config import Settings

    monkeypatch.setitem(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC["selection"],
        "top_n",
        4,
    )
    with pytest.raises(
        ridge.AuditedPITDevelopmentReplayError,
        match="canonical hash",
    ):
        ridge.run_audited_pit_ranked_liquidity_ridge_rolling_oof(
            settings=Settings(),
            audited_pit_universe_path=tmp_path / "universe.sqlite3",
            expected_coverage_audit_sha256="a" * 64,
            expected_artifact_root_sha256="b" * 64,
            temporal_contract_path=tmp_path / "temporal.json",
            expected_temporal_contract_sha256="c" * 64,
            security_code_transition_evidence_root=tmp_path / "transition",
            expected_security_code_transition_contract_sha256="d" * 64,
            start_date="2024-07-05",
            end_date="2026-07-03",
            output_dir=tmp_path / "output",
        )


def test_exact_features_follow_frozen_math_and_exclude_star_and_beijing():
    sessions = _sessions(61)
    features, receipt = ridge._build_exact_cross_section_features(
        _bars(sessions),
        sessions,
        minimum_cross_section_members=2,
    )

    final = features[features["signal_date"].eq(sessions[-1])]
    assert final["ts_code"].tolist() == ["000001.SZ", "300001.SZ"]
    assert not final["ts_code"].str.startswith(("688", "689", "4", "8", "9")).any()
    row = final[final["ts_code"].eq("000001.SZ")].iloc[0]

    closes = np.asarray([100.0 + index for index in range(61)])
    amounts = np.asarray([1_000.0 + index for index in range(61)])
    one_step_returns = closes[-21:][1:] / closes[-21:][:-1] - 1.0
    expected_return_5 = (160.0 / 155.0 - 1.0) * 100.0
    expected_raw = {
        "amount_level_20": math.log1p(float(np.mean(amounts[-20:]))),
        "amount_volatility_20": float(np.std(amounts[-20:], ddof=0)),
        "amount_surge_5_to_60": math.log(
            float(np.mean(amounts[-5:])) / float(np.mean(amounts[-60:]))
        ),
        "amihud_20": float(
            np.mean(np.abs(one_step_returns) / amounts[-20:])
        ),
        "realized_volatility_20_pct": (
            float(np.std(one_step_returns, ddof=0)) * 100.0
        ),
        "max_return_20_pct": float(np.max(one_step_returns)) * 100.0,
        "signal_return_1d_pct": (
            (160.0 / 159.0 - 1.0) * 100.0
        ),
        "reversal_20_skip5_pct": (
            -(155.0 / 135.0 - 1.0) * 100.0
        ),
    }
    for name, expected in expected_raw.items():
        assert row[name] == pytest.approx(expected)
    assert row["amount_level_20_rank"] == pytest.approx(0.0)
    assert row["amount_volatility_20_rank"] == pytest.approx(0.0)
    assert row["amount_surge_5_to_60_rank"] == pytest.approx(0.0)
    assert row["amihud_20_rank"] == pytest.approx(1.0 / 3.0)
    assert row["realized_volatility_20_pct_rank"] == pytest.approx(
        1.0 / 3.0
    )
    assert row["max_return_20_pct_rank"] == pytest.approx(1.0 / 3.0)
    assert row["signal_return_1d_pct_rank"] == pytest.approx(1.0 / 3.0)
    assert row["reversal_20_skip5_pct_rank"] == pytest.approx(
        -1.0 / 3.0
    )
    assert row["cross_section_above_ma20_fraction"] == pytest.approx(0.5)
    assert row["cross_section_median_return_5d_pct"] == pytest.approx(
        expected_return_5 / 2.0
    )
    assert receipt["minimum_cross_section_members"] == 2
    assert receipt["scope_policy_id"] == "research-mainboard-chinext/v1"
    assert receipt["cross_section_transform"] == (
        ridge.CONTINUOUS_RIDGE_OOF_SPEC["cross_section_transform"]
    )
    assert receipt["feature_row_count"] == len(features)


def test_exact_features_preserve_nullable_security_transition_metadata():
    sessions = _sessions(61)
    bars = _bars(
        sessions,
        ts_codes=("000001.SZ", "300001.SZ"),
    )
    bars["security_code_transition_contract_sha256"] = "d" * 64
    bars.loc[
        bars["ts_code"].eq("300001.SZ"),
        "security_code_transition_id",
    ] = "transition-1"

    features, _ = ridge._build_exact_cross_section_features(
        bars,
        sessions,
        minimum_cross_section_members=2,
    )
    records = features[
        features["signal_date"].eq(sessions[-1])
    ].to_dict("records")
    by_symbol = {str(row["symbol"]): row for row in records}

    assert by_symbol["000001"]["security_code_transition_id"] is None
    assert (
        by_symbol["300001"]["security_code_transition_id"]
        == "transition-1"
    )
    assert {
        row["security_code_transition_contract_sha256"]
        for row in records
    } == {"d" * 64}
    ridge._sha256(records)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_audited_payload_hash_rejects_nonfinite_with_json_path(value):
    payload = [{"nested": [{"return_pct": value}]}]

    with pytest.raises(
        ridge.AuditedPITDevelopmentReplayError,
        match=r"\$\.completed_candidates\[0\]\.nested\[0\]\.return_pct",
    ):
        ridge._audited_payload_sha256(
            payload,
            root_path="$.completed_candidates",
        )


def test_audited_payload_hash_matches_canonical_hash_for_finite_values():
    payload = [{"nested": [{"return_pct": 1.25}]}]

    assert ridge._audited_payload_sha256(
        payload,
        root_path="$.completed_candidates",
    ) == ridge._sha256(payload)


def test_midrank_ties_share_rank_and_list_date_is_fail_closed():
    sessions = _sessions(61)
    bars = _bars(
        sessions,
        ts_codes=("000001.SZ", "000002.SZ", "300001.SZ"),
    )
    final_date = sessions[-1]
    bars.loc[
        bars["ts_code"].eq("000002.SZ"),
        ["open", "high", "low", "close"],
    ] = bars.loc[
        bars["ts_code"].eq("000001.SZ"),
        ["open", "high", "low", "close"],
    ].to_numpy()
    bars.loc[
        bars["ts_code"].eq("000002.SZ"),
        "amount",
    ] = bars.loc[bars["ts_code"].eq("000001.SZ"), "amount"].to_numpy()
    bars.loc[
        bars["ts_code"].eq("300001.SZ")
        & bars["date"].eq(final_date),
        "membership_list_date",
    ] = None

    features, receipt = ridge._build_exact_cross_section_features(
        bars,
        sessions,
        minimum_cross_section_members=2,
    )
    final = features[features["signal_date"].eq(final_date)]

    assert final["ts_code"].tolist() == ["000001.SZ", "000002.SZ"]
    for name in ridge.RANKED_STOCK_FEATURE_NAMES:
        assert final[name].tolist() == pytest.approx([0.0, 0.0])
    assert receipt["status_counts"]["invalid_membership_list_date"] >= 1

    future_listed = bars.copy()
    future_listed.loc[
        future_listed["ts_code"].eq("300001.SZ"),
        "membership_list_date",
    ] = "2030-01-01"
    future_features, _ = ridge._build_exact_cross_section_features(
        future_listed,
        sessions,
        minimum_cross_section_members=2,
    )
    assert not future_features["ts_code"].eq("300001.SZ").any()


@pytest.mark.parametrize(
    "invalid_list_date",
    (
        " 2020-01-01",
        "2020-01-01 ",
        "20200101",
        "2020/01/01",
    ),
)
def test_list_date_noncanonical_text_is_fail_closed(invalid_list_date):
    sessions = _sessions(61)
    bars = _bars(
        sessions,
        ts_codes=("000001.SZ", "000002.SZ", "300001.SZ"),
    )
    bars.loc[
        bars["ts_code"].eq("300001.SZ"),
        "membership_list_date",
    ] = invalid_list_date

    features, receipt = ridge._build_exact_cross_section_features(
        bars,
        sessions,
        minimum_cross_section_members=2,
    )

    assert not features["ts_code"].eq("300001.SZ").any()
    assert receipt["status_counts"]["invalid_membership_list_date"] >= 1


@pytest.mark.parametrize("invalid_amount", (0.0, -1.0, float("nan")))
def test_unused_t_minus_60_amount_does_not_change_feature_pool(
    invalid_amount,
):
    sessions = _sessions(61)
    bars = _bars(sessions, ts_codes=("000001.SZ", "300001.SZ"))
    bars.loc[
        bars["ts_code"].eq("000001.SZ")
        & bars["date"].eq(sessions[0]),
        "amount",
    ] = invalid_amount

    features, receipt = ridge._build_exact_cross_section_features(
        bars,
        sessions,
        minimum_cross_section_members=1,
    )

    final = features[features["signal_date"].eq(sessions[-1])]
    assert final["ts_code"].tolist() == ["000001.SZ", "300001.SZ"]


@pytest.mark.parametrize("invalid_amount", (0.0, -1.0, float("nan")))
def test_used_60_session_amount_window_is_fail_closed(invalid_amount):
    sessions = _sessions(61)
    bars = _bars(sessions, ts_codes=("000001.SZ", "300001.SZ"))
    bars.loc[
        bars["ts_code"].eq("000001.SZ")
        & bars["date"].eq(sessions[1]),
        "amount",
    ] = invalid_amount

    features, receipt = ridge._build_exact_cross_section_features(
        bars,
        sessions,
        minimum_cross_section_members=1,
    )

    final = features[features["signal_date"].eq(sessions[-1])]
    assert final["ts_code"].tolist() == ["300001.SZ"]
    assert receipt["status_counts"]["signal_ineligible"] >= 1


def test_partial_midrank_ties_are_invariant_to_input_permutation():
    raw = np.asarray([3.0, 1.0, 1.0, 4.0])
    expected = np.asarray([0.2, -0.4, -0.4, 0.6])
    actual = ridge._deterministic_midrank(raw)

    assert actual.tolist() == pytest.approx(expected.tolist())

    permutation = np.asarray([2, 0, 3, 1])
    permuted = ridge._deterministic_midrank(raw[permutation])
    restored = np.empty_like(permuted)
    restored[permutation] = permuted
    assert restored.tolist() == pytest.approx(expected.tolist())


def test_features_require_every_global_session_and_ignore_future_bars():
    sessions = _sessions(62)
    bars = _bars(sessions, ts_codes=("000001.SZ", "300001.SZ"))
    missing = bars[
        ~(
            bars["ts_code"].eq("000001.SZ")
            & bars["date"].eq(sessions[30])
        )
    ]
    incomplete, receipt = ridge._build_exact_cross_section_features(
        missing,
        sessions,
        minimum_cross_section_members=1,
    )

    assert not (
        incomplete["ts_code"].eq("000001.SZ")
        & incomplete["signal_date"].eq(sessions[-1])
    ).any()
    assert receipt["status_counts"]["missing_exact_61_session_history"] >= 1

    before, _ = ridge._build_exact_cross_section_features(
        bars[bars["date"].le(sessions[60])],
        sessions[:61],
        minimum_cross_section_members=2,
    )
    future = bars.copy()
    future.loc[
        future["date"].eq(sessions[61]), ["open", "high", "low", "close"]
    ] = [9_000.0, 10_000.0, 8_000.0, 9_500.0]
    after, _ = ridge._build_exact_cross_section_features(
        future,
        sessions,
        minimum_cross_section_members=2,
    )
    key = ["ts_code", "signal_date"]
    columns = [*key, *ridge.FEATURE_NAMES]
    expected = before[before["signal_date"].eq(sessions[60])][columns]
    actual = after[after["signal_date"].eq(sessions[60])][columns]
    pd.testing.assert_frame_equal(
        expected.reset_index(drop=True),
        actual.reset_index(drop=True),
    )


def test_deterministic_even_median_uses_arithmetic_middle_pair():
    assert ridge._deterministic_median([4.0, 1.0, 3.0, 2.0]) == 2.5
    assert ridge._deterministic_median([9.0, 1.0, 4.0]) == 4.0


def test_weighted_continuous_ridge_equalizes_signal_dates_and_keeps_intercept():
    x = np.zeros((3, 1), dtype=float)
    y = np.asarray([0.0, 2.0, 5.0])
    model = ridge._fit_weighted_continuous_ridge(
        x,
        y,
        ["2025-01-02", "2025-01-02", "2025-01-03"],
        ridge_lambda=1.0,
    )

    assert model["coefficients"][0] == pytest.approx(3.0)
    assert model["coefficients"][1] == pytest.approx(0.0)
    assert model["scale"][0] == pytest.approx(1.0)

    directional = ridge._fit_weighted_continuous_ridge(
        np.asarray([[-2.0], [-1.0], [1.0], [2.0]]),
        np.asarray([-4.0, -2.0, 2.0, 4.0]),
        ["2025-01-02", "2025-01-02", "2025-01-03", "2025-01-03"],
        ridge_lambda=1.0,
    )
    scores = ridge._predict_continuous_ridge(
        directional,
        np.asarray([[-1.0], [1.0]]),
    )
    assert scores[1] > scores[0]


def _feature_row(
    candidate_key: str,
    signal_date: str,
    value: float,
) -> dict:
    row = {
        "candidate_key": candidate_key,
        "signal_date": signal_date,
        "symbol": candidate_key[:6],
        "ts_code": f"{candidate_key[:6]}.SZ",
        "source_ts_code": f"{candidate_key[:6]}.SZ",
        "security_id": f"entity:{candidate_key[:6]}",
        "signal_industry": "行业",
        "candidate_amount": 1_000.0 + value,
        "signal_frame_index": 60,
    }
    row.update({name: 0.0 for name in ridge.FEATURE_NAMES})
    row[ridge.FEATURE_NAMES[0]] = value
    return row


def test_purged_oof_uses_only_mature_complete_training_outcomes():
    sessions = _sessions(7)
    features = pd.DataFrame(
        [
            _feature_row("000001|a", sessions[0], -2.0),
            _feature_row("000002|b", sessions[1], 2.0),
            _feature_row("000003|purged", sessions[2], 100.0),
            _feature_row("000004|censored", sessions[3], -100.0),
            _feature_row("000005|v1", sessions[4], -1.0),
            _feature_row("000006|v2", sessions[5], 1.0),
        ]
    )
    outcomes = [
        {
            "candidate_key": "000001|a",
            "exit_date": sessions[2],
            "return_pct": -2.0,
            "right_censored": False,
        },
        {
            "candidate_key": "000002|b",
            "exit_date": sessions[3],
            "return_pct": 3.0,
            "right_censored": False,
        },
        {
            "candidate_key": "000003|purged",
            "exit_date": sessions[4],
            "return_pct": 1_000.0,
            "right_censored": False,
        },
        {
            "candidate_key": "000004|censored",
            "exit_date": sessions[5],
            "right_censored": True,
        },
        {
            "candidate_key": "000005|v1",
            "exit_date": sessions[6],
            "return_pct": -99.0,
            "right_censored": False,
        },
        {
            "candidate_key": "000006|v2",
            "exit_date": sessions[6],
            "return_pct": 99.0,
            "right_censored": False,
        },
    ]

    scored, receipt = ridge._build_purged_oof_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=4,
        validation_sessions=2,
    )
    mutated = [
        {
            **outcome,
            "return_pct": (
                -float(outcome.get("return_pct") or 0.0) * 10_000.0
                if outcome["candidate_key"] in {"000005|v1", "000006|v2"}
                else outcome.get("return_pct")
            ),
        }
        for outcome in outcomes
    ]
    rescored, _ = ridge._build_purged_oof_scores(
        features,
        mutated,
        sessions,
        minimum_training_sessions=4,
        validation_sessions=2,
    )

    assert scored["candidate_key"].tolist() == ["000005|v1", "000006|v2"]
    assert scored["predicted_net_return_pct"].tolist() == pytest.approx(
        rescored["predicted_net_return_pct"].tolist()
    )
    fold = receipt["folds"][0]
    assert fold["training_candidate_count"] == 2
    assert fold["training_last_exit_date"] == sessions[3]
    assert fold["validation_candidate_count"] == 2
    assert fold["training_label"] == "gross_return_pct_minus_0.45"


def test_rolling_oof_uses_exact_trailing_sessions_and_purges_immature():
    sessions = _sessions(10)
    features = pd.DataFrame(
        [
            _feature_row(
                f"{index:06d}|row",
                signal_date,
                float(index),
            )
            for index, signal_date in enumerate(sessions)
        ]
    )
    outcomes = [
        {
            "candidate_key": f"{index:06d}|row",
            "exit_date": sessions[index + 1],
            "return_pct": float(index - 2),
            "right_censored": False,
        }
        for index in range(len(sessions) - 1)
    ]

    scored, receipt = ridge._build_purged_oof_scores(
        features,
        outcomes,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
        receipt_schema_version=(
            "ranked-liquidity-ridge-purged-oof-receipt/v3"
        ),
    )

    second_fold = receipt["folds"][1]
    assert second_fold["validation_start"] == sessions[6]
    assert second_fold["training_window_type"] == (
        "trailing_frozen_signal_sessions"
    )
    assert second_fold["training_window_session_count"] == 4
    assert second_fold["training_window_start"] == sessions[2]
    assert second_fold["training_window_end"] == sessions[5]
    assert second_fold["training_window_sessions_sha256"] == ridge._sha256(
        sessions[2:6]
    )
    assert second_fold["eligible_window_training_candidate_count"] == 4
    assert second_fold["purged_immature_candidate_count"] == 1
    assert second_fold["training_candidate_count"] == 3
    assert second_fold["training_signal_date_count"] == 3

    outside_mutated = [
        {
            **outcome,
            "return_pct": (
                float(outcome["return_pct"]) + 10_000.0
                if outcome["candidate_key"] in {
                    "000000|row",
                    "000001|row",
                }
                else outcome["return_pct"]
            ),
        }
        for outcome in outcomes
    ]
    rescored_outside, _ = ridge._build_purged_oof_scores(
        features,
        outside_mutated,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
        receipt_schema_version=(
            "ranked-liquidity-ridge-purged-oof-receipt/v3"
        ),
    )
    fold_two_keys = {"000006|row", "000007|row"}
    original_fold_two = scored[
        scored["candidate_key"].isin(fold_two_keys)
    ]["predicted_net_return_pct"].tolist()
    outside_fold_two = rescored_outside[
        rescored_outside["candidate_key"].isin(fold_two_keys)
    ]["predicted_net_return_pct"].tolist()
    assert outside_fold_two == pytest.approx(original_fold_two)

    inside_mutated = [
        {
            **outcome,
            "return_pct": (
                float(outcome["return_pct"]) + 10_000.0
                if outcome["candidate_key"] == "000002|row"
                else outcome["return_pct"]
            ),
        }
        for outcome in outcomes
    ]
    rescored_inside, _ = ridge._build_purged_oof_scores(
        features,
        inside_mutated,
        sessions,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
        receipt_schema_version=(
            "ranked-liquidity-ridge-purged-oof-receipt/v3"
        ),
    )
    inside_fold_two = rescored_inside[
        rescored_inside["candidate_key"].isin(fold_two_keys)
    ]["predicted_net_return_pct"].tolist()
    assert inside_fold_two != pytest.approx(original_fold_two)

    assert ridge.verify_rolling_oof_receipt(
        features,
        outcomes,
        sessions,
        scored,
        receipt,
        minimum_training_sessions=4,
        training_window_sessions=4,
        validation_sessions=2,
    ) == {
        "verified": True,
        "receipt_sha256": receipt["receipt_sha256"],
        "fold_count": 3,
        "oof_candidate_count": len(scored),
    }
    tampered = deepcopy(receipt)
    tampered["folds"][1]["training_window_start"] = sessions[1]
    tampered["folds_sha256"] = ridge._sha256(tampered["folds"])
    tampered_without_self = {
        key: value
        for key, value in tampered.items()
        if key != "receipt_sha256"
    }
    tampered["receipt_sha256"] = ridge._sha256(tampered_without_self)
    with pytest.raises(ValueError, match="rolling OOF receipt"):
        ridge.verify_rolling_oof_receipt(
            features,
            outcomes,
            sessions,
            scored,
            tampered,
            minimum_training_sessions=4,
            training_window_sessions=4,
            validation_sessions=2,
        )


def _outcome_candidate(
    symbol: str,
    signal_date: str,
    *,
    security_id: str,
    industry: str,
    amount: float,
    predicted: float,
    exit_date: str,
    right_censored: bool = False,
) -> dict:
    return {
        "candidate_key": f"{security_id}|{signal_date}",
        "symbol": symbol,
        "ts_code": f"{symbol}.SZ",
        "security_id": security_id,
        "signal_date": signal_date,
        "entry_date": signal_date,
        "exit_date": exit_date,
        "signal_industry": industry,
        "candidate_amount": amount,
        "predicted_net_return_pct": predicted,
        "right_censored": right_censored,
    }


def test_positive_pool_and_baseline_share_candidates_with_stable_entity_cap():
    signal_date = "2025-01-02"
    exit_date = "2025-01-10"
    candidates = [
        _outcome_candidate(
            "000001",
            signal_date,
            security_id="entity-a",
            industry="科技",
            amount=100.0,
            predicted=5.0,
            exit_date=exit_date,
        ),
        _outcome_candidate(
            "000002",
            signal_date,
            security_id="entity-b",
            industry="科技",
            amount=500.0,
            predicted=4.0,
            exit_date=exit_date,
        ),
        _outcome_candidate(
            "000003",
            signal_date,
            security_id="entity-c",
            industry="金融",
            amount=300.0,
            predicted=3.0,
            exit_date=exit_date,
        ),
        _outcome_candidate(
            "300001",
            signal_date,
            security_id="entity-d",
            industry="制造",
            amount=200.0,
            predicted=2.0,
            exit_date=exit_date,
            right_censored=True,
        ),
        _outcome_candidate(
            "000004",
            signal_date,
            security_id="entity-e",
            industry="消费",
            amount=1_000.0,
            predicted=0.0,
            exit_date=exit_date,
        ),
    ]
    pool, pool_receipt = ridge._positive_score_pool(candidates)
    main, main_receipt = ridge._select_with_industry_cap_receipt(
        pool,
        rank_mode="predicted_net_return",
        top_n=3,
        max_active_positions=3,
    )
    baseline, baseline_receipt = ridge._select_with_industry_cap_receipt(
        pool,
        rank_mode="signal_date_amount",
        top_n=3,
        max_active_positions=3,
    )

    assert pool_receipt["positive_candidate_count"] == 4
    assert [item["symbol"] for item in main] == [
        "000001",
        "000003",
        "300001",
    ]
    assert [item["symbol"] for item in baseline] == [
        "000002",
        "000003",
        "300001",
    ]
    assert (
        main_receipt["candidate_table_sha256"]
        == baseline_receipt["candidate_table_sha256"]
    )
    assert main_receipt["days"][0]["decisions"][1]["decision"] == (
        "active_industry"
    )
    assert any(item["right_censored"] is True for item in main)

    next_day = _outcome_candidate(
        "302132",
        "2025-01-03",
        security_id="entity-a",
        industry="航空",
        amount=9_999.0,
        predicted=99.0,
        exit_date="2025-01-11",
    )
    selected, receipt = ridge._select_with_industry_cap_receipt(
        [*pool, next_day],
        rank_mode="predicted_net_return",
        top_n=3,
        max_active_positions=3,
    )
    assert [item["security_id"] for item in selected].count("entity-a") == 1
    day = next(
        item for item in receipt["days"] if item["signal_date"] == "2025-01-03"
    )
    assert day["decisions"][0]["decision"] == "active_security"


def _coherently_rehash_feature_receipt(features, receipt):
    replayed = deepcopy(receipt)
    ordered_features = features.sort_values(
        ["signal_date", "security_id", "source_ts_code"],
        kind="mergesort",
    ).reset_index(drop=True)
    groups = []
    for original_group in replayed["cross_section_groups"]:
        signal_date = original_group["signal_date"]
        group = ordered_features[
            ordered_features["signal_date"].eq(signal_date)
        ].sort_values(
            ["security_id", "source_ts_code"],
            kind="mergesort",
        )
        member_keys = group["security_id"].astype(str).tolist()
        breadth_inputs = [
            {
                "security_id": str(row.security_id),
                "above_ma20": bool(row.above_ma20),
            }
            for row in group.itertuples(index=False)
        ]
        return_inputs = [
            {
                "security_id": str(row.security_id),
                "value": float(row.return_5d_pct),
            }
            for row in group.itertuples(index=False)
        ]
        rebuilt_group = {
            **original_group,
            "member_count": len(group),
            "member_keys_sha256": ridge._sha256(member_keys),
            "breadth_inputs_sha256": ridge._sha256(breadth_inputs),
            "return_5d_inputs_sha256": ridge._sha256(return_inputs),
            "raw_feature_inputs_sha256": {
                name: ridge._sha256(
                    [
                        {
                            "security_id": str(row.security_id),
                            "value": float(getattr(row, name)),
                        }
                        for row in group.itertuples(index=False)
                    ]
                )
                for name in ridge.RAW_STOCK_FEATURE_NAMES
            },
            "ranked_feature_outputs_sha256": {
                name: ridge._sha256(
                    [
                        {
                            "security_id": str(row.security_id),
                            "value": float(getattr(row, name)),
                        }
                        for row in group.itertuples(index=False)
                    ]
                )
                for name in ridge.RANKED_STOCK_FEATURE_NAMES
            },
            "cross_section_above_ma20_fraction": (
                sum(item["above_ma20"] for item in breadth_inputs)
                / len(breadth_inputs)
            ),
            "cross_section_median_return_5d_pct": (
                ridge._deterministic_median(
                    [item["value"] for item in return_inputs]
                )
            ),
        }
        rebuilt_group.pop("statistics_sha256", None)
        rebuilt_group["statistics_sha256"] = ridge._sha256(rebuilt_group)
        groups.append(rebuilt_group)

    replayed["cross_section_group_count"] = len(groups)
    replayed["cross_section_groups"] = groups
    replayed["cross_section_groups_sha256"] = ridge._sha256(groups)
    replayed["feature_row_count"] = len(ordered_features)
    replayed["feature_rows_sha256"] = ridge._sha256(
        [
            ridge._feature_row_payload(row)
            for row in ordered_features.to_dict("records")
        ]
    )
    replayed.pop("receipt_sha256", None)
    replayed["receipt_sha256"] = ridge._sha256(replayed)
    return replayed


def test_feature_receipt_replays_and_rejects_member_or_statistic_tampering():
    sessions = _sessions(61)
    features, receipt = ridge._build_exact_cross_section_features(
        _bars(sessions, ts_codes=("000001.SZ", "300001.SZ")),
        sessions,
        minimum_cross_section_members=2,
    )

    verified = ridge.verify_feature_receipt(features, receipt)
    assert verified["verified"] is True
    assert verified["receipt_sha256"] == receipt["receipt_sha256"]
    assert _coherently_rehash_feature_receipt(features, receipt) == receipt

    tampered_features = features.copy()
    tampered_features.loc[
        tampered_features.index[-1], "signal_return_1d_pct_rank"
    ] += 1.0
    with pytest.raises(ValueError, match="feature receipt"):
        ridge.verify_feature_receipt(tampered_features, receipt)

    tampered_member_features = features.copy()
    tampered_member_features.loc[
        tampered_member_features.index[0],
        "security_id",
    ] = "cn-a-share:000000.SZ"
    coherently_rehashed_member_receipt = _coherently_rehash_feature_receipt(
        tampered_member_features,
        receipt,
    )
    with pytest.raises(ValueError, match="feature receipt"):
        ridge.verify_feature_receipt(
            tampered_member_features,
            coherently_rehashed_member_receipt,
        )

    coherently_rehashed_statistic_receipt = deepcopy(receipt)
    statistic_group = coherently_rehashed_statistic_receipt[
        "cross_section_groups"
    ][0]
    statistic_group["cross_section_above_ma20_fraction"] = 0.75
    statistic_group.pop("statistics_sha256", None)
    statistic_group["statistics_sha256"] = ridge._sha256(statistic_group)
    coherently_rehashed_statistic_receipt["cross_section_groups_sha256"] = (
        ridge._sha256(
            coherently_rehashed_statistic_receipt["cross_section_groups"]
        )
    )
    coherently_rehashed_statistic_receipt.pop("receipt_sha256", None)
    coherently_rehashed_statistic_receipt["receipt_sha256"] = ridge._sha256(
        coherently_rehashed_statistic_receipt
    )
    with pytest.raises(ValueError, match="feature receipt"):
        ridge.verify_feature_receipt(
            features,
            coherently_rehashed_statistic_receipt,
        )


def test_continuous_label_subtracts_exact_friction_without_clipping():
    assert [
        ridge._continuous_net_label(value)
        for value in (-100.0, 0.45, 200.0)
    ] == pytest.approx([-100.45, 0.0, 199.55])
    assert ridge._signal_date_weights(
        ["d1", "d2", "d2", "d2"]
    ).tolist() == pytest.approx([1.0, 1 / 3, 1 / 3, 1 / 3])


def test_label_friction_is_bound_to_execution_costs(monkeypatch):
    monkeypatch.setitem(
        ridge.CONTINUOUS_RIDGE_OOF_SPEC["label"],
        "friction_percentage_points",
        0.44,
    )

    with pytest.raises(ValueError, match="label friction"):
        ridge._assert_shared_strict_execution_contract()


def test_483_sessions_form_six_contiguous_nonoverlapping_oof_folds():
    sessions = _sessions(483)
    folds = ridge._fold_ranges(sessions)

    assert len(folds) == 6
    assert folds[0] == (sessions[126], sessions[188])
    assert folds[-1] == (sessions[441], sessions[482])
    positions = {trade_date: index for index, trade_date in enumerate(sessions)}
    for left, right in zip(folds, folds[1:]):
        assert positions[right[0]] == positions[left[1]] + 1


def test_positive_pool_uses_strict_unrounded_zero_and_rejects_nonfinite():
    candidates = [
        _outcome_candidate(
            "000001",
            "2025-01-02",
            security_id="entity-a",
            industry="科技",
            amount=1.0,
            predicted=-1.0,
            exit_date="2025-01-10",
        ),
        _outcome_candidate(
            "000002",
            "2025-01-02",
            security_id="entity-b",
            industry="金融",
            amount=1.0,
            predicted=0.0,
            exit_date="2025-01-10",
        ),
        _outcome_candidate(
            "000003",
            "2025-01-02",
            security_id="entity-c",
            industry="制造",
            amount=1.0,
            predicted=float(np.nextafter(0.0, 1.0)),
            exit_date="2025-01-10",
        ),
    ]

    pool, _ = ridge._positive_score_pool(candidates)
    assert [candidate["security_id"] for candidate in pool] == ["entity-c"]

    with pytest.raises(ValueError, match="nonfinite"):
        ridge._positive_score_pool(
            [
                {
                    **candidates[-1],
                    "candidate_key": "nonfinite",
                    "predicted_net_return_pct": float("nan"),
                }
            ]
        )


def test_uniform_tail_cutoff_precedes_entry_and_outcome_queries(monkeypatch):
    calls = []
    keep = {
        "candidate_key": "keep",
        "signal_date": "2025-01-02",
        "symbol": "000001",
    }
    cut = {
        "candidate_key": "cut",
        "signal_date": "2025-01-03",
        "symbol": "000002",
    }

    def cutoff(candidates, *, sessions, family):
        calls.append(("tail", [item["candidate_key"] for item in candidates]))
        return [keep], {"receipt_sha256": "tail"}

    def preflight(candidates, **kwargs):
        calls.append(
            ("entry", [item["candidate_key"] for item in candidates])
        )
        return [keep], {"receipt_sha256": "entry"}, {}

    def outcomes(candidates, **kwargs):
        calls.append(
            ("outcome", [item["candidate_key"] for item in candidates])
        )
        return [keep], [], {"receipt_sha256": "outcome"}

    monkeypatch.setattr(ridge, "_apply_uniform_tail_cutoff", cutoff)
    monkeypatch.setattr(ridge, "_preflight_strict_entries", preflight)
    monkeypatch.setattr(ridge, "_build_strict_outcomes", outcomes)
    result = ridge._strict_execution_dataset(
        [keep, cut],
        frames_by_symbol={},
        sessions=_sessions(10),
        adapter=object(),
        suspension_evidence={},
        terminal_listing_evidence={},
    )

    assert calls == [
        ("tail", ["keep", "cut"]),
        ("entry", ["keep"]),
        ("outcome", ["keep"]),
    ]
    assert result["completed_candidates"] == [keep]


def test_bulk_next_open_adapter_matches_strict_fill_gate_without_requeries():
    sessions = _sessions(4)
    refs = [
        {
            "trade_date": trade_date,
            "generation_id": f"generation-{index}",
            "manifest_sha256": f"{index + 1:064x}",
            "lineage_sha256": f"{index + 11:064x}",
            "vintage": "historical_backfill",
        }
        for index, trade_date in enumerate(sessions)
    ]
    rows = [
        {
            "generation_id": refs[0]["generation_id"],
            "trade_date": sessions[0],
            "ts_code": "000001.SZ",
            "open": 10.0,
            "limit_generation_id": refs[0]["generation_id"],
            "pre_close": 9.8,
            "up_limit": 10.8,
            "down_limit": 8.8,
            "suspended": 0,
        },
        {
            "generation_id": refs[1]["generation_id"],
            "trade_date": sessions[1],
            "ts_code": "000001.SZ",
            "open": 10.1,
            "limit_generation_id": None,
            "pre_close": None,
            "up_limit": None,
            "down_limit": None,
            "suspended": 0,
        },
        {
            "generation_id": refs[2]["generation_id"],
            "trade_date": sessions[2],
            "ts_code": "000001.SZ",
            "open": 10.2,
            "limit_generation_id": refs[2]["generation_id"],
            "pre_close": 10.0,
            "up_limit": 11.0,
            "down_limit": 9.0,
            "suspended": 1,
        },
        {
            "generation_id": refs[0]["generation_id"],
            "trade_date": sessions[0],
            "ts_code": "300001.SZ",
            "open": 22.0,
            "limit_generation_id": refs[0]["generation_id"],
            "pre_close": 20.0,
            "up_limit": 22.0,
            "down_limit": 18.0,
            "suspended": 0,
        },
    ]

    class FakeConnection:
        def __init__(self):
            self.calls = 0

        def execute(self, query, params):
            self.calls += 1
            assert "market_session_generation_rows_stk_limit" in query
            assert "market_session_generation_rows_suspend_d" in query
            assert "upper(trim(suspension.suspend_type)) = 'S'" in query
            assert params == (sessions[0], sessions[-1])
            return list(rows)

    class BaseAdapter:
        contract_sha256 = "a" * 64
        artifact_root_sha256 = "b" * 64

        def baseline_scenarios(self):
            return ()

    connection = FakeConnection()
    adapter, receipt = ridge._load_bulk_next_open_replay_adapter(
        connection,
        base_adapter=BaseAdapter(),
        market_generation_refs=refs,
        sessions=sessions,
    )

    assert connection.calls == 1
    assert adapter.next_open("000001", sessions[0], "buy") == {
        "fillable": True,
        "reason": "raw_open",
        "raw_price": 10.0,
        "generation_proof": refs[0],
    }
    assert (
        adapter.next_open("000001", sessions[1], "buy")["reason"]
        == "missing_price_limit"
    )
    assert (
        adapter.next_open("000001", sessions[2], "sell")["reason"]
        == "suspended"
    )
    assert (
        adapter.next_open("000001", sessions[3], "sell")["reason"]
        == "missing_raw_bar"
    )
    assert (
        adapter.next_open("300001", sessions[0], "buy")["reason"]
        == "buy_open_locked_limit"
    )
    assert adapter.next_open("300001", sessions[0], "sell")["fillable"] is True
    with pytest.raises(ridge.PITReceiptError, match="unknown symbol"):
        adapter.next_open("600001", sessions[0], "buy")
    assert len(adapter.contract_sha256) == 64
    assert receipt["evidence_row_count"] == 4
    assert receipt["receipt_sha256"] == ridge._sha256(
        {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
    )


def test_selected_censor_blocks_each_evidence_gate(monkeypatch):
    passing_metrics = {
        "trade_win_rate_pct": 60.0,
        "portfolio_max_drawdown_pct": -10.0,
        "trade_profit_factor": 2.0,
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct": 60.0,
        "calmar_latest_12m": 2.0,
        "rolling_1y_windows": [
            {
                "return_pct": 60.0,
                "max_drawdown_pct": -10.0,
                "payoff_ratio": 2.0,
                "profit_factor": 2.0,
                "calmar": 2.0,
            }
        ],
    }
    monkeypatch.setattr(
        ridge,
        "_trade_metrics",
        lambda *args, **kwargs: passing_metrics,
    )
    censor = _outcome_candidate(
        "000001",
        "2025-01-02",
        security_id="entity-a",
        industry="industry-a",
        amount=1.0,
        predicted=10.0,
        exit_date="2026-07-03",
        right_censored=True,
    )
    censor["censor_reason"] = "no_strict_sell_fill_through_coverage_end"
    complete = [
        _outcome_candidate(
            f"00000{index + 2}",
            "2025-01-02",
            security_id=f"entity-{chr(ord('b') + index)}",
            industry=f"industry-{chr(ord('b') + index)}",
            amount=400.0 - index * 100.0,
            predicted=3.0 - index,
            exit_date="2025-01-10",
        )
        for index in range(3)
    ]

    main, _ = ridge._evaluate_fixed_oof(
        [censor, *complete],
        rank_mode="predicted_net_return",
        evaluation_session_dates=_sessions(366),
    )
    baseline, _ = ridge._evaluate_fixed_oof(
        [censor, *complete],
        rank_mode="signal_date_amount",
        evaluation_session_dates=_sessions(366),
    )
    main_row = main["top"][0]
    baseline_row = baseline["top"][0]

    assert main_row["evidence_complete"] is False
    assert baseline_row["evidence_complete"] is True
    assert ridge._advancement_gate_passes(main_row, baseline_row) is False


def test_fixed_oof_gates_use_unrounded_float64_metrics(monkeypatch):
    monkeypatch.setitem(
        ridge.CONTINUOUS_RIDGE_OOF_SPEC["advancement_thresholds"],
        "minimum_complete_trades",
        1,
    )
    rounded_pass_raw_fail = {
        "gate_metric_basis": "unrounded_float64",
        "trade_win_rate_pct": 52.0,
        "trade_win_rate_pct_raw": 51.9999,
        "portfolio_max_drawdown_pct": -15.0,
        "portfolio_max_drawdown_pct_raw": -15.0001,
        "trade_profit_factor": 1.3,
        "trade_profit_factor_raw": 1.2999,
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct": 50.0,
        "rolling_1y_latest_return_pct_raw": 49.9999,
        "calmar_latest_12m": 1.5,
        "calmar_latest_12m_raw": 1.4999,
        "rolling_1y_windows": [
            {
                "return_pct": 50.0,
                "return_pct_raw": 49.9999,
                "max_drawdown_pct": -15.0,
                "max_drawdown_pct_raw": -15.0001,
                "payoff_ratio": 1.3,
                "payoff_ratio_raw": 1.2999,
                "profit_factor": 1.3,
                "profit_factor_raw": 1.2999,
                "calmar": 1.5,
                "calmar_raw": 1.4999,
            }
        ],
    }
    monkeypatch.setattr(
        ridge,
        "_trade_metrics",
        lambda *args, **kwargs: rounded_pass_raw_fail,
    )
    candidate = _outcome_candidate(
        "000001",
        "2025-01-02",
        security_id="entity-a",
        industry="industry-a",
        amount=1.0,
        predicted=1.0,
        exit_date="2025-01-10",
    )

    sweep, _ = ridge._evaluate_fixed_oof(
        [candidate],
        rank_mode="predicted_net_return",
        evaluation_session_dates=_sessions(366),
    )
    row = sweep["top"][0]

    assert row["gate_metric_basis"] == "unrounded_float64"
    assert row["target_full_development_quality_pass"] is False
    assert row["target_latest_12m_pass"] is False
    assert row["target_rolling_12m_stability_pass"] is False
    assert row["target_all_pass"] is False


def _sidecar_payload(
    schema_version: str,
    *,
    strategy_sha256: str,
    source: dict,
    producer_code: dict,
) -> dict:
    return {
        "schema_version": schema_version,
        "strategy_sha256": strategy_sha256,
        "source": source,
        "producer_code": producer_code,
        "data": [1, 2, 3],
    }


def test_compact_receipt_summary_replaces_full_self_hash_and_is_verifiable():
    full_receipt = {
        "schema_version": "full-receipt/v1",
        "event_count": 2,
        "events": [{"key": "a"}, {"key": "b"}],
        "events_sha256": ridge._sha256([{"key": "a"}, {"key": "b"}]),
    }
    full_receipt["receipt_sha256"] = ridge._sha256(full_receipt)

    summary = ridge._compact_receipt_summary(
        full_receipt,
        omitted_fields={
            "events": {
                "count_field": "event_count",
                "sha256_field": "events_sha256",
            }
        },
    )

    assert "events" not in summary
    assert "receipt_sha256" not in summary
    assert summary["full_receipt_sha256"] == full_receipt["receipt_sha256"]
    assert summary["omitted_fields"]["events"] == {
        "count": 2,
        "sha256": full_receipt["events_sha256"],
    }
    ridge._verify_compact_receipt_summary(summary)
    tampered = {**summary, "event_count": 3}
    with pytest.raises(ValueError, match="summary"):
        ridge._verify_compact_receipt_summary(tampered)
    forged_full_hash = {
        **full_receipt,
        "receipt_sha256": "f" * 64,
    }
    with pytest.raises(ValueError, match="full receipt"):
        ridge._compact_receipt_summary(
            forged_full_hash,
            omitted_fields={
                "events": {
                    "count_field": "event_count",
                    "sha256_field": "events_sha256",
                }
            },
        )
    forged_content_hash = {
        **full_receipt,
        "events_sha256": "0" * 64,
    }
    forged_content_hash["receipt_sha256"] = ridge._sha256(
        {
            key: value
            for key, value in forged_content_hash.items()
            if key != "receipt_sha256"
        }
    )
    with pytest.raises(ValueError, match="omitted evidence"):
        ridge._compact_receipt_summary(
            forged_content_hash,
            omitted_fields={
                "events": {
                    "count_field": "event_count",
                    "sha256_field": "events_sha256",
                }
            },
        )


def test_producer_binding_includes_pdf_parser_version():
    producer = ridge._producer_binding()
    rolling_producer = ridge._producer_binding(artifact_version=3)

    assert producer["pypdf_version"]
    assert producer["schema_version"].endswith("/v2")
    assert rolling_producer["schema_version"].endswith("/v3")
    assert producer["root_sha256"] != rolling_producer["root_sha256"]


def test_result_bundle_is_content_addressed_path_stable_and_drift_closed(
    monkeypatch,
    tmp_path,
):
    producer = {"schema_version": "producer/v1", "root_sha256": "a" * 64}
    strategy_sha256 = "b" * 64
    source = {"artifact_root_sha256": "c" * 64}
    sidecars = {
        name: _sidecar_payload(
            f"{name}-sidecar/v1",
            strategy_sha256=strategy_sha256,
            source=source,
            producer_code=producer,
        )
        for name in ("features", "models", "execution", "selection")
    }
    main = {
        "schema_version": "ranked-liquidity-ridge-result/v2",
        "strategy_sha256": strategy_sha256,
        "source": source,
        "scope": {"development_only": True},
    }
    monkeypatch.setattr(ridge, "_producer_binding", lambda: producer)

    first = ridge._write_result_bundle(
        tmp_path / "first",
        main_payload=main,
        sidecar_payloads=sidecars,
        expected_producer_code=producer,
    )
    second = ridge._write_result_bundle(
        tmp_path / "second",
        main_payload=main,
        sidecar_payloads=sidecars,
        expected_producer_code=producer,
    )

    assert first["artifact"]["artifact_sha256"] == second["artifact"][
        "artifact_sha256"
    ]
    assert {
        name: value["artifact_sha256"]
        for name, value in first["runtime_sidecars"].items()
    } == {
        name: value["artifact_sha256"]
        for name, value in second["runtime_sidecars"].items()
    }
    main_path = first["artifact"]["path"]
    body = pd.read_json(main_path, typ="series").to_dict()
    digest = body.pop("artifact_sha256")
    assert ridge._sha256(body) == digest
    assert str(tmp_path) not in str(body)
    assert sorted(body["sidecars"]) == [
        "execution",
        "features",
        "models",
        "selection",
    ]

    calls = 0

    def drifting_producer():
        nonlocal calls
        calls += 1
        if calls < 2:
            return producer
        return {"schema_version": "producer/v1", "root_sha256": "d" * 64}

    monkeypatch.setattr(ridge, "_producer_binding", drifting_producer)
    with pytest.raises(ValueError, match="producer"):
        ridge._write_result_bundle(
            tmp_path / "drift",
            main_payload=main,
            sidecar_payloads=sidecars,
            expected_producer_code=producer,
        )


def test_end_to_end_runner_stays_development_only(monkeypatch, tmp_path):
    from app.config import Settings

    sessions = _sessions(196)
    ts_codes = ("000001.SZ", "000002.SZ", "000003.SZ", "300001.SZ")
    rows = []
    for offset, ts_code in enumerate(ts_codes):
        growth = 0.0015 + offset * 0.0005
        for index, trade_date in enumerate(sessions):
            close = 100.0 * (1.0 + growth) ** index
            rows.append(
                {
                    "date": trade_date,
                    "ts_code": ts_code,
                    "source_ts_code": ts_code,
                    "security_id": f"cn-a-share:{ts_code}",
                    "security_code_transition_id": (
                        "transition-1"
                        if ts_code == "000002.SZ"
                        else None
                    ),
                    "security_code_transition_contract_sha256": "d" * 64,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "pre_close": close / (1.0 + growth),
                    "amount": 1_000_000.0 + offset * 10_000.0 + index,
                    "adj_factor": 1.0,
                    "membership_name": f"normal-{offset}",
                    "membership_industry": f"industry-{offset}",
                    "membership_list_date": "2020-01-01",
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": trade_date,
                    "suspended": 0,
                }
            )
    bars = pd.DataFrame(rows)
    bar_receipt = {
        "schema_version": "test-ranked-loader/v2",
        "security_code_transition_application_receipt_sha256": "e" * 64,
    }
    bar_receipt["receipt_sha256"] = ridge._sha256(bar_receipt)
    coverage_sha = "a" * 64
    artifact_root_sha = "b" * 64
    temporal_sha = "c" * 64
    transition_receipt = {
        "schema_version": "test-transition/v1",
        "contract_sha256": "d" * 64,
        "contract": {"schema_version": "test-transition-contract/v1"},
        "receipt_sha256": "f" * 64,
    }
    market_generation_refs = [
        {
            "trade_date": trade_date,
            "generation_id": f"generation-{trade_date}",
            "manifest_sha256": "2" * 64,
            "lineage_sha256": "3" * 64,
            "vintage": "historical_backfill",
        }
        for trade_date in sessions
    ]
    outcome_query_order = []
    oof_feature_signal_dates = []
    original_tail_cutoff = ridge._apply_uniform_tail_cutoff
    original_oof_builder = ridge._build_purged_oof_scores

    def tracked_tail_cutoff(*args, **kwargs):
        outcome_query_order.append("tail_cutoff")
        return original_tail_cutoff(*args, **kwargs)

    def tracked_suspension_loader(*args, **kwargs):
        outcome_query_order.append("suspension_evidence")
        return {}

    def tracked_terminal_loader(*args, **kwargs):
        outcome_query_order.append("terminal_listing_evidence")
        return {}

    def tracked_bulk_loader(*args, **kwargs):
        outcome_query_order.append("bulk_next_open_evidence")
        receipt = {
            "schema_version": "test-bulk-next-open/v2",
            "evidence_row_count": len(bars),
            "receipt_sha256": "4" * 64,
        }
        return kwargs["base_adapter"], receipt

    def tracked_oof_builder(features, *args, **kwargs):
        oof_feature_signal_dates.extend(
            features["signal_date"].astype(str).tolist()
        )
        return original_oof_builder(features, *args, **kwargs)

    class FakeUniverse:
        start_date = sessions[0]
        end_date = sessions[-1]
        coverage_audit_sha256 = coverage_sha
        artifact_root_sha256 = artifact_root_sha
        temporal_contract_sha256 = temporal_sha
        temporal_role = "development"
        manifest = {
            "market_generations": {"refs": market_generation_refs}
        }

        def _require_open(self):
            return object()

        def close(self):
            return None

    by_key = {
        (str(row.ts_code)[:6], str(row.date)): row
        for row in bars.itertuples(index=False)
    }

    class FakeAdapter:
        contract_sha256 = "1" * 64

        def __init__(self, *_args, **_kwargs):
            pass

        def next_open(self, symbol, trade_date, side="buy"):
            row = by_key[(symbol, trade_date)]
            return {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": float(row.open),
                "generation_proof": {
                    "trade_date": trade_date,
                    "generation_id": f"generation-{trade_date}",
                    "manifest_sha256": "2" * 64,
                    "lineage_sha256": "3" * 64,
                    "vintage": "historical_backfill",
                    "resolved_ts_code": str(row.source_ts_code),
                },
            }

    monkeypatch.setitem(
        ridge.CONTINUOUS_RIDGE_OOF_SPEC,
        "minimum_cross_section_members",
        4,
    )
    monkeypatch.setitem(
        ridge.CONTINUOUS_RIDGE_OOF_SPEC,
        "required_market_session_count",
        len(sessions),
    )
    monkeypatch.setitem(
        ridge.CONTINUOUS_RIDGE_OOF_SPEC,
        "required_oof_fold_count",
        2,
    )
    monkeypatch.setitem(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
        "minimum_cross_section_members",
        4,
    )
    monkeypatch.setitem(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
        "required_market_session_count",
        len(sessions),
    )
    monkeypatch.setitem(
        ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
        "required_oof_fold_count",
        2,
    )
    monkeypatch.setattr(
        ridge,
        "load_temporal_partition_contract",
        lambda _path: {"contract_sha256": temporal_sha},
    )
    monkeypatch.setattr(
        ridge,
        "assert_range_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        ridge,
        "load_security_code_transition_evidence",
        lambda *_args, **_kwargs: dict(transition_receipt),
    )
    monkeypatch.setattr(
        ridge.AuditedPointInTimeUniverse,
        "from_file",
        lambda *_args, **_kwargs: FakeUniverse(),
    )
    monkeypatch.setattr(
        ridge,
        "_exact_membership_sessions",
        lambda *_args, **_kwargs: sessions,
    )
    monkeypatch.setattr(
        ridge,
        "_load_ranked_liquidity_bars",
        lambda *_args, **_kwargs: (bars.copy(), dict(bar_receipt)),
    )
    monkeypatch.setattr(
        ridge,
        "_load_suspension_evidence",
        tracked_suspension_loader,
    )
    monkeypatch.setattr(
        ridge,
        "_load_terminal_listing_evidence",
        tracked_terminal_loader,
    )
    monkeypatch.setattr(
        ridge,
        "_load_bulk_next_open_replay_adapter",
        tracked_bulk_loader,
    )
    monkeypatch.setattr(
        ridge,
        "_apply_uniform_tail_cutoff",
        tracked_tail_cutoff,
    )
    monkeypatch.setattr(
        ridge,
        "_build_purged_oof_scores",
        tracked_oof_builder,
    )
    monkeypatch.setattr(
        ridge,
        "remap_security_code_transition_suspension_evidence",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        ridge,
        "remap_security_code_transition_terminal_evidence",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(ridge, "ArtifactNativeReplayAdapter", FakeAdapter)
    monkeypatch.setattr(
        ridge,
        "SecurityCodeTransitionReplayAdapter",
        lambda adapter, **_kwargs: adapter,
    )

    result = ridge.run_audited_pit_ranked_liquidity_ridge_oof(
        settings=Settings(),
        audited_pit_universe_path=tmp_path / "universe.sqlite3",
        expected_coverage_audit_sha256=coverage_sha,
        expected_artifact_root_sha256=artifact_root_sha,
        temporal_contract_path=tmp_path / "temporal.json",
        expected_temporal_contract_sha256=temporal_sha,
        security_code_transition_evidence_root=tmp_path / "transition",
        expected_security_code_transition_contract_sha256="d" * 64,
        start_date=sessions[0],
        end_date=sessions[-1],
        output_dir=tmp_path / "output",
    )

    assert result["scope"]["development_only"] is True
    assert result["scope"]["embargo_consumed"] is False
    assert result["scope"]["final_oos_consumed"] is False
    assert result["scope"]["eligible_for_profile_registration"] is False
    assert outcome_query_order == [
        "tail_cutoff",
        "suspension_evidence",
        "terminal_listing_evidence",
        "bulk_next_open_evidence",
    ]
    assert max(oof_feature_signal_dates) == sessions[-7]
    assert result["feature_candidate_count"] > 0
    assert result["oof_candidate_count"] > 0
    assert sorted(result["runtime_sidecars"]) == [
        "execution",
        "features",
        "models",
        "selection",
    ]
    v2_model_sidecar = json.loads(
        Path(result["runtime_sidecars"]["models"]["path"]).read_text(
            encoding="utf-8"
        )
    )
    assert "oof_replay_verification" not in v2_model_sidecar
    progress = json.loads(
        (
            tmp_path
            / "output"
            / ".ranked_liquidity_v2_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert progress["stage"] == "completed"
    assert (
        progress["artifact_sha256"]
        == result["artifact"]["artifact_sha256"]
    )
    v2_progress_path = (
        tmp_path / "output" / ".ranked_liquidity_v2_progress.json"
    )
    v2_progress_bytes = v2_progress_path.read_bytes()

    outcome_query_order.clear()
    oof_feature_signal_dates.clear()
    rolling_result = (
        ridge._run_audited_pit_ranked_liquidity_ridge_oof(
            settings=Settings(),
            audited_pit_universe_path=tmp_path / "universe.sqlite3",
            expected_coverage_audit_sha256=coverage_sha,
            expected_artifact_root_sha256=artifact_root_sha,
            temporal_contract_path=tmp_path / "temporal.json",
            expected_temporal_contract_sha256=temporal_sha,
            security_code_transition_evidence_root=(
                tmp_path / "transition"
            ),
            expected_security_code_transition_contract_sha256="d" * 64,
            start_date=sessions[0],
            end_date=sessions[-1],
            output_dir=tmp_path / "rolling-output",
            strategy_spec=ridge.ROLLING_CONTINUOUS_RIDGE_OOF_SPEC,
            artifact_version=3,
            training_window_sessions=126,
        )
    )

    assert rolling_result["schema_version"] == (
        "ranked-liquidity-ridge-result/v3"
    )
    assert rolling_result["strategy"]["schema_version"] == (
        "development-pit-cross-sectional-ranked-liquidity-"
        "ridge-rolling-oof/v3"
    )
    assert rolling_result["strategy"]["signal_tag"] == (
        "cross_sectional_ranked_liquidity_ridge_rolling_126_oof"
    )
    assert rolling_result["producer_code"]["schema_version"] == (
        "audited-pit-ranked-liquidity-producer/v3"
    )
    assert rolling_result["scope"]["development_only"] is True
    assert rolling_result["scope"]["embargo_consumed"] is False
    assert rolling_result["scope"]["final_oos_consumed"] is False
    assert (
        rolling_result["scope"]["eligible_for_profile_registration"]
        is False
    )
    assert outcome_query_order == [
        "tail_cutoff",
        "suspension_evidence",
        "terminal_listing_evidence",
        "bulk_next_open_evidence",
    ]
    assert max(oof_feature_signal_dates) == sessions[-7]
    assert v2_progress_path.read_bytes() == v2_progress_bytes
    rolling_progress = json.loads(
        (
            tmp_path
            / "rolling-output"
            / ".ranked_liquidity_v3_progress.json"
        ).read_text(encoding="utf-8")
    )
    assert rolling_progress["stage"] == "completed"
    assert rolling_progress["schema_version"].endswith("/v3")

    sidecars = {
        name: json.loads(
            Path(payload["path"]).read_text(encoding="utf-8")
        )
        for name, payload in rolling_result["runtime_sidecars"].items()
    }
    assert {
        name: sidecar["schema_version"]
        for name, sidecar in sidecars.items()
    } == {
        "execution": "ranked-liquidity-execution-sidecar/v3",
        "features": "ranked-liquidity-feature-sidecar/v3",
        "models": "ranked-liquidity-model-sidecar/v3",
        "selection": "ranked-liquidity-selection-sidecar/v3",
    }
    rolling_oof = sidecars["models"]["oof_receipt"]
    assert rolling_oof["schema_version"].endswith("/v3")
    assert rolling_oof["training_window_sessions"] == 126
    assert rolling_oof["ridge_lambda"] == 1.0
    assert len(rolling_oof["folds"]) == 2
    assert all(
        fold["training_window_type"]
        == "trailing_frozen_signal_sessions"
        and fold["training_window_session_count"] == 126
        for fold in rolling_oof["folds"]
    )
    assert (
        sidecars["models"]["oof_replay_verification"]["verified"]
        is True
    )
    assert sidecars["execution"]["outcome_receipt"][
        "schema_version"
    ].endswith("/v3")
    main_sweep = sidecars["selection"]["main_sweep"]
    baseline_sweep = sidecars["selection"]["amount_baseline_sweep"]
    assert main_sweep["schema_version"].endswith("/v3")
    assert baseline_sweep["schema_version"].endswith("/v3")
    score_evidence = sidecars["selection"][
        "scored_execution_candidate_evidence"
    ]
    assert score_evidence["schema_version"] == (
        "ranked-liquidity-score-evidence/v3"
    )
    assert score_evidence["columns"] == list(
        ridge.SCORED_EXECUTION_EVIDENCE_COLUMNS
    )
    assert score_evidence["row_count"] == len(score_evidence["rows"])
    assert all(
        isinstance(row, list)
        and len(row) == len(ridge.SCORED_EXECUTION_EVIDENCE_COLUMNS)
        for row in score_evidence["rows"]
    )
    assert main_sweep["top"][0]["required_signal_tags"] == [
        "cross_sectional_ranked_liquidity_ridge_rolling_126_oof"
    ]
    assert sidecars["selection"]["main_selection_receipt"][
        "candidate_table_sha256"
    ] == sidecars["selection"]["amount_baseline_selection_receipt"][
        "candidate_table_sha256"
    ]
    assert len(rolling_result["artifact"]["artifact_sha256"]) == 64
    assert rolling_result["verification"]["verified"] is True
    assert rolling_result["verification"]["checks"] == {
        "independent_rolling_oof_replay": True,
        "exact_trailing_126_session_windows": True,
        "window_external_labels_excluded": True,
        "window_internal_mature_labels_bound": True,
        "immature_labels_purged": True,
        "six_fold_geometry_matches_v2": True,
        "content_addressing_verified": True,
        "shared_positive_candidate_pool_verified": True,
        "selected_board_scope_verified": True,
    }
    assert len(
        rolling_result["runtime_verification"]["artifact_sha256"]
    ) == 64
    assert all(
        len(payload["artifact_sha256"]) == 64
        for payload in rolling_result["runtime_sidecars"].values()
    )
    assert sidecars["selection"]["selected_evidence"]

    tamper_index = 0

    def tampered_bundle(
        *,
        mutate_main=None,
        mutate_models=None,
        mutate_selection=None,
    ):
        nonlocal tamper_index
        tamper_index += 1
        bundle = deepcopy(rolling_result)
        main_document = json.loads(
            Path(bundle["artifact"]["path"]).read_text(encoding="utf-8")
        )
        main_document.pop("artifact_sha256")
        tamper_root = tmp_path / f"tamper-{tamper_index}"
        for sidecar_name, mutate_sidecar in (
            ("models", mutate_models),
            ("selection", mutate_selection),
        ):
            if mutate_sidecar is None:
                continue
            sidecar_document = json.loads(
                Path(
                    bundle["runtime_sidecars"][sidecar_name]["path"]
                ).read_text(encoding="utf-8")
            )
            sidecar_document.pop("artifact_sha256")
            mutate_sidecar(sidecar_document)
            sidecar_runtime = ridge._write_content_addressed(
                tamper_root / "sidecars",
                sidecar_document,
            )
            bundle["runtime_sidecars"][sidecar_name] = sidecar_runtime
            main_document["sidecars"][sidecar_name] = (
                ridge._stable_sidecar_reference(sidecar_runtime)
            )
        if mutate_main is not None:
            mutate_main(main_document)
        bundle["artifact"] = ridge._write_content_addressed(
            tamper_root,
            main_document,
        )
        return bundle

    def tamper_scope(main_document):
        main_document["scope"]["production_recommendation_eligible"] = True

    def tamper_relative_path(main_document):
        main_document["sidecars"]["selection"]["relative_path"] = (
            "sidecars/not-the-selection-sidecar.json"
        )

    def tamper_strict_outcome_count(main_document):
        main_document["strict_outcome_candidate_count"] += 1

    def tamper_sidecar_schema(selection_document):
        selection_document["schema_version"] = (
            "ranked-liquidity-selection-sidecar/v2"
        )

    def tamper_oof_window(models_document):
        receipt = models_document["oof_receipt"]
        fold = receipt["folds"][0]
        fold["training_window_session_count"] -= 1
        fold_unsigned = {
            key: value
            for key, value in fold.items()
            if key != "receipt_sha256"
        }
        fold["receipt_sha256"] = ridge._sha256(fold_unsigned)
        receipt["folds_sha256"] = ridge._sha256(receipt["folds"])
        receipt_unsigned = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = ridge._sha256(receipt_unsigned)
        models_document["oof_replay_verification"][
            "receipt_sha256"
        ] = receipt["receipt_sha256"]

    def tamper_selection_parameters(selection_document):
        receipt = selection_document["main_selection_receipt"]
        receipt["parameters"]["top_n"] += 1
        summary_unsigned = {
            key: value
            for key, value in receipt.items()
            if key != "summary_receipt_sha256"
        }
        receipt["summary_receipt_sha256"] = ridge._sha256(
            summary_unsigned
        )

    def tamper_positive_pool(selection_document):
        receipt = selection_document["positive_pool_receipt"]
        receipt["positive_candidate_count"] += 1
        unsigned = {
            key: value
            for key, value in receipt.items()
            if key != "receipt_sha256"
        }
        receipt["receipt_sha256"] = ridge._sha256(unsigned)
        tampered_positive_receipt_sha["value"] = receipt["receipt_sha256"]

    def bind_tampered_positive_pool(main_document):
        main_document["positive_pool_receipt_sha256"] = (
            tampered_positive_receipt_sha["value"]
        )

    def tamper_selected_evidence(selection_document):
        selection_document["selected_evidence"].pop()
        selection_document["selected_evidence_sha256"] = ridge._sha256(
            selection_document["selected_evidence"]
        )

    def tamper_selected_content(selection_document):
        evidence = selection_document["selected_evidence"]
        evidence[0]["predicted_net_return_pct"] += 1.0
        selection_document["selected_evidence_sha256"] = ridge._sha256(
            evidence
        )

    tampered_gate = not sidecars["selection"]["advancement_gate_passed"]

    def tamper_advancement_selection(selection_document):
        selection_document["advancement_gate_passed"] = tampered_gate

    def tamper_advancement_main(main_document):
        main_document["scope"]["advancement_gate_passed"] = tampered_gate
        main_document["advancement_gate"][
            "all_required_gates_passed"
        ] = tampered_gate

    def tamper_positive_hash_selection(selection_document):
        selection_document[
            "positive_candidate_payload_hashes_sha256"
        ] = "0" * 64

    def tamper_positive_hash_main(main_document):
        main_document[
            "positive_candidate_payload_hashes_sha256"
        ] = "0" * 64

    assert (
        sidecars["selection"]["main_sweep"]["top"][0]["target_all_pass"]
        is False
    )
    tampered_sweep_holder = {}

    def tamper_sweep_selection(selection_document):
        sweep = selection_document["main_sweep"]
        row = sweep["top"][0]
        row["target_full_development_quality_pass"] = True
        row["target_latest_12m_pass"] = True
        row["target_rolling_12m_stability_pass"] = True
        row["target_all_pass"] = True
        sweep["target_all_pass_count"] = 1
        sweep["target_rolling_12m_stability_pass_count"] = 1
        selection_document["advancement_gate_passed"] = True
        tampered_sweep_holder["value"] = deepcopy(sweep)

    def bind_tampered_sweep(main_document):
        main_document["main_sweep"] = tampered_sweep_holder["value"]
        main_document["scope"]["advancement_gate_passed"] = True
        main_document["advancement_gate"][
            "all_required_gates_passed"
        ] = True

    tampered_raw_metric_holder = {}

    def tamper_raw_metric_selection(selection_document):
        sweep = selection_document["main_sweep"]
        row = sweep["top"][0]
        row["trade_win_rate_pct_raw"] = (
            float(row["trade_win_rate_pct_raw"]) + 0.01
        )
        tampered_raw_metric_holder["value"] = deepcopy(sweep)

    def bind_tampered_raw_metric(main_document):
        main_document["main_sweep"] = (
            tampered_raw_metric_holder["value"]
        )

    tampered_projection_holder = {}

    def tamper_selected_projection(selection_document):
        selected_item = selection_document["selected_evidence"][0]
        selected_item["right_censored"] = (
            selected_item.get("right_censored") is not True
        )
        selection_document["selected_evidence_sha256"] = ridge._sha256(
            selection_document["selected_evidence"]
        )
        trade_key = ridge._selection_trade_key(selected_item)
        evidence = selection_document[
            "scored_execution_candidate_evidence"
        ]
        evidence_row = next(
            row for row in evidence["rows"] if row[2] == trade_key
        )
        evidence_row[9] = ridge._sha256(selected_item)
        evidence["rows_sha256"] = ridge._sha256(evidence["rows"])
        evidence["candidate_payload_hashes_sha256"] = ridge._sha256(
            [row[9] for row in evidence["rows"]]
        )
        evidence["positive_candidate_payload_hashes_sha256"] = (
            ridge._sha256(
                [row[9] for row in evidence["rows"] if float(row[5]) > 0.0]
            )
        )
        evidence_unsigned = {
            key: value
            for key, value in evidence.items()
            if key != "receipt_sha256"
        }
        evidence["receipt_sha256"] = ridge._sha256(evidence_unsigned)
        selection_document[
            "positive_candidate_payload_hashes_sha256"
        ] = evidence["positive_candidate_payload_hashes_sha256"]
        tampered_projection_holder.update(
            {
                "rows": evidence["rows_sha256"],
                "payloads": evidence[
                    "candidate_payload_hashes_sha256"
                ],
                "positive_payloads": evidence[
                    "positive_candidate_payload_hashes_sha256"
                ],
            }
        )

    def bind_tampered_projection(main_document):
        main_document[
            "scored_execution_candidate_evidence_rows_sha256"
        ] = tampered_projection_holder["rows"]
        main_document[
            "scored_execution_candidate_payload_hashes_sha256"
        ] = tampered_projection_holder["payloads"]
        main_document[
            "positive_candidate_payload_hashes_sha256"
        ] = tampered_projection_holder["positive_payloads"]

    tampered_outcome_holder = {}

    def tamper_selected_outcome(selection_document):
        selected_item = next(
            item
            for item in selection_document["selected_evidence"]
            if item.get("right_censored") is not True
            and item.get("return_pct") is not None
        )
        selected_item["return_pct"] = (
            float(selected_item["return_pct"]) + 0.01
        )
        selection_document["selected_evidence_sha256"] = ridge._sha256(
            selection_document["selected_evidence"]
        )
        trade_key = ridge._selection_trade_key(selected_item)
        evidence = selection_document[
            "scored_execution_candidate_evidence"
        ]
        evidence_row = next(
            row for row in evidence["rows"] if row[2] == trade_key
        )
        evidence_row[8] = ridge._sha256(
            ridge._outcome_payload_from_scored_candidate(selected_item)
        )
        evidence_row[9] = ridge._sha256(selected_item)
        evidence["rows_sha256"] = ridge._sha256(evidence["rows"])
        evidence["candidate_payload_hashes_sha256"] = ridge._sha256(
            [row[9] for row in evidence["rows"]]
        )
        evidence["positive_candidate_payload_hashes_sha256"] = (
            ridge._sha256(
                [row[9] for row in evidence["rows"] if float(row[5]) > 0.0]
            )
        )
        evidence_unsigned = {
            key: value
            for key, value in evidence.items()
            if key != "receipt_sha256"
        }
        evidence["receipt_sha256"] = ridge._sha256(evidence_unsigned)
        selection_document[
            "positive_candidate_payload_hashes_sha256"
        ] = evidence["positive_candidate_payload_hashes_sha256"]
        tampered_outcome_holder.update(
            {
                "rows": evidence["rows_sha256"],
                "payloads": evidence[
                    "candidate_payload_hashes_sha256"
                ],
                "positive_payloads": evidence[
                    "positive_candidate_payload_hashes_sha256"
                ],
            }
        )

    def bind_tampered_outcome(main_document):
        main_document[
            "scored_execution_candidate_evidence_rows_sha256"
        ] = tampered_outcome_holder["rows"]
        main_document[
            "scored_execution_candidate_payload_hashes_sha256"
        ] = tampered_outcome_holder["payloads"]
        main_document[
            "positive_candidate_payload_hashes_sha256"
        ] = tampered_outcome_holder["positive_payloads"]

    tampered_positive_receipt_sha = {}
    for bundle in (
        tampered_bundle(mutate_main=tamper_scope),
        tampered_bundle(mutate_main=tamper_relative_path),
        tampered_bundle(mutate_main=tamper_strict_outcome_count),
        tampered_bundle(mutate_selection=tamper_sidecar_schema),
        tampered_bundle(mutate_models=tamper_oof_window),
        tampered_bundle(
            mutate_selection=tamper_selection_parameters
        ),
        tampered_bundle(
            mutate_main=bind_tampered_positive_pool,
            mutate_selection=tamper_positive_pool,
        ),
        tampered_bundle(
            mutate_main=tamper_positive_hash_main,
            mutate_selection=tamper_positive_hash_selection,
        ),
        tampered_bundle(mutate_selection=tamper_selected_evidence),
        tampered_bundle(mutate_selection=tamper_selected_content),
        tampered_bundle(
            mutate_main=tamper_advancement_main,
            mutate_selection=tamper_advancement_selection,
        ),
        tampered_bundle(
            mutate_main=bind_tampered_sweep,
            mutate_selection=tamper_sweep_selection,
        ),
        tampered_bundle(
            mutate_main=bind_tampered_raw_metric,
            mutate_selection=tamper_raw_metric_selection,
        ),
        tampered_bundle(
            mutate_main=bind_tampered_projection,
            mutate_selection=tamper_selected_projection,
        ),
        tampered_bundle(
            mutate_main=bind_tampered_outcome,
            mutate_selection=tamper_selected_outcome,
        ),
    ):
        with pytest.raises(
            ValueError,
            match="rolling result bundle verification failed",
        ):
            ridge.verify_rolling_result_bundle(bundle)


def test_jobs_cli_dispatches_ranked_liquidity_ridge_oof(
    monkeypatch,
    tmp_path,
):
    from app import jobs

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"schema_version": "test-ranked-liquidity-ridge-result/v2"}

    monkeypatch.setattr(
        jobs,
        "run_audited_pit_ranked_liquidity_ridge_oof",
        fake_runner,
        raising=False,
    )
    monkeypatch.setattr(jobs, "get_settings", lambda: "settings")
    result = jobs.main(
        [
            "research-audited-pit-ranked-liquidity-ridge-oof",
            "--audited-pit-universe-path",
            str(tmp_path / "universe.sqlite3"),
            "--expected-coverage-audit-sha256",
            "a" * 64,
            "--expected-artifact-root-sha256",
            "b" * 64,
            "--temporal-contract-path",
            str(tmp_path / "temporal.json"),
            "--expected-temporal-contract-sha256",
            "c" * 64,
            "--security-code-transition-evidence-root",
            str(tmp_path / "transition"),
            "--expected-security-code-transition-contract-sha256",
            "d" * 64,
            "--start-date",
            "2024-07-05",
            "--end-date",
            "2026-07-03",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert result == 0
    assert captured["settings"] == "settings"
    assert captured["start_date"] == "2024-07-05"
    assert captured["end_date"] == "2026-07-03"
    assert captured["security_code_transition_evidence_root"] == str(
        tmp_path / "transition"
    )


def test_jobs_cli_dispatches_ranked_liquidity_rolling_ridge_oof(
    monkeypatch,
    tmp_path,
):
    from app import jobs

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {
            "schema_version": "test-ranked-liquidity-ridge-result/v3"
        }

    monkeypatch.setattr(
        jobs,
        "run_audited_pit_ranked_liquidity_ridge_rolling_oof",
        fake_runner,
        raising=False,
    )
    monkeypatch.setattr(jobs, "get_settings", lambda: "settings")
    result = jobs.main(
        [
            "research-audited-pit-ranked-liquidity-ridge-rolling-oof",
            "--audited-pit-universe-path",
            str(tmp_path / "universe.sqlite3"),
            "--expected-coverage-audit-sha256",
            "a" * 64,
            "--expected-artifact-root-sha256",
            "b" * 64,
            "--temporal-contract-path",
            str(tmp_path / "temporal.json"),
            "--expected-temporal-contract-sha256",
            "c" * 64,
            "--security-code-transition-evidence-root",
            str(tmp_path / "transition"),
            "--expected-security-code-transition-contract-sha256",
            "d" * 64,
            "--start-date",
            "2024-07-05",
            "--end-date",
            "2026-07-03",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert result == 0
    assert captured["settings"] == "settings"
    assert captured["start_date"] == "2024-07-05"
    assert captured["end_date"] == "2026-07-03"
    assert captured["security_code_transition_evidence_root"] == str(
        tmp_path / "transition"
    )
