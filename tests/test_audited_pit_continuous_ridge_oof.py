from __future__ import annotations

from datetime import date, timedelta
import math

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
                }
            )
    return pd.DataFrame(rows)


def test_frozen_spec_is_continuous_oof_and_mainboard_chinext_only():
    spec = ridge.CONTINUOUS_RIDGE_OOF_SPEC

    assert (
        spec["schema_version"]
        == "development-pit-cross-sectional-continuous-ridge-oof/v1"
    )
    assert spec["features"] == list(ridge.FEATURE_NAMES)
    assert len(ridge.FEATURE_NAMES) == 12
    assert spec["model"]["ridge_lambda"] == 1.0
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
    one_step_returns = closes[-21:][1:] / closes[-21:][:-1] - 1.0
    expected_return_20 = (160.0 / 140.0 - 1.0) * 100.0
    assert row["log_amount"] == pytest.approx(math.log1p(1_060.0))
    assert row["amount_to_prior20_median"] == pytest.approx(
        1_060.0 / 1_049.5
    )
    assert row["distance_prior20_high_pct"] == pytest.approx(0.0)
    assert row["signal_return_1d_pct"] == pytest.approx(
        (160.0 / 159.0 - 1.0) * 100.0
    )
    assert row["signal_close_location_pct"] == pytest.approx(50.0)
    assert row["signal_range_pct"] == pytest.approx(
        (161.0 / 159.0 - 1.0) * 100.0
    )
    assert row["signal_return_20d_pct"] == pytest.approx(expected_return_20)
    assert row["signal_return_60d_pct"] == pytest.approx(60.0)
    assert row["realized_volatility_20d_pct"] == pytest.approx(
        np.std(one_step_returns, ddof=0) * 100.0
    )
    assert row["distance_ma20_pct"] == pytest.approx(
        (160.0 / np.mean(closes[-20:]) - 1.0) * 100.0
    )
    assert row["cross_section_above_ma20_fraction"] == pytest.approx(0.5)
    assert row["cross_section_median_return_20d_pct"] == pytest.approx(
        expected_return_20 / 2.0
    )
    assert receipt["minimum_cross_section_members"] == 2
    assert receipt["scope_policy_id"] == "research-mainboard-chinext/v1"
    assert receipt["feature_row_count"] == len(features)


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
    sessions = _sessions(6)
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
            "exit_date": sessions[5],
            "return_pct": -99.0,
            "right_censored": False,
        },
        {
            "candidate_key": "000006|v2",
            "exit_date": sessions[5],
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

