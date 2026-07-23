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

    tampered_features = features.copy()
    tampered_features.loc[
        tampered_features.index[-1], "signal_return_1d_pct_rank"
    ] += 1.0
    with pytest.raises(ValueError, match="feature receipt"):
        ridge.verify_feature_receipt(tampered_features, receipt)

    tampered_receipt = {
        **receipt,
        "cross_section_groups": [
            {
                **receipt["cross_section_groups"][0],
                "cross_section_above_ma20_fraction": 0.75,
            }
        ],
    }
    with pytest.raises(ValueError, match="feature receipt"):
        ridge.verify_feature_receipt(features, tampered_receipt)


def test_continuous_label_subtracts_exact_friction_without_clipping():
    assert [
        ridge._continuous_net_label(value)
        for value in (-100.0, 0.45, 200.0)
    ] == pytest.approx([-100.45, 0.0, 199.55])
    assert ridge._signal_date_weights(
        ["d1", "d2", "d2", "d2"]
    ).tolist() == pytest.approx([1.0, 1 / 3, 1 / 3, 1 / 3])


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
        industry="科技",
        amount=1.0,
        predicted=10.0,
        exit_date="2026-07-03",
        right_censored=True,
    )
    complete = _outcome_candidate(
        "000002",
        "2025-01-02",
        security_id="entity-b",
        industry="金融",
        amount=100.0,
        predicted=1.0,
        exit_date="2025-01-10",
    )

    main, _ = ridge._evaluate_fixed_oof(
        [censor, complete],
        rank_mode="predicted_net_return",
        evaluation_session_dates=_sessions(366),
    )
    baseline, _ = ridge._evaluate_fixed_oof(
        [censor, complete],
        rank_mode="signal_date_amount",
        evaluation_session_dates=_sessions(366),
    )
    main_row = main["top"][0]
    baseline_row = baseline["top"][0]

    assert main_row["evidence_complete"] is False
    assert baseline_row["evidence_complete"] is True
    assert ridge._advancement_gate_passes(main_row, baseline_row) is False


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
