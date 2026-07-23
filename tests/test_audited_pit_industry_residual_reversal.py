from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3

import pandas as pd
import pytest

from app import audited_pit_industry_residual_reversal as residual
from app import research_security_code_transition as transition


def test_frozen_spec_binds_entry_offsets_gates_baseline_and_overlap():
    spec = residual.INDUSTRY_RESIDUAL_SPEC

    assert spec["entry_signal_offset_sessions"] == 1
    assert spec["planned_exit_signal_offset_sessions"] == 6
    assert spec["entry_numeric_abs_tolerance"] == 1e-9
    assert spec["advancement_thresholds"] == {
        "minimum_complete_trades": 20,
        "minimum_full_win_rate_pct": 52.0,
        "maximum_full_drawdown_pct": 15.0,
        "minimum_full_profit_factor": 1.3,
        "minimum_latest_365d_return_pct": 50.0,
        "minimum_latest_365d_calmar": 1.5,
        "all_complete_365d_minimum_return_pct": 50.0,
        "all_complete_365d_maximum_drawdown_pct": 15.0,
        "all_complete_365d_minimum_payoff_ratio": 1.3,
        "all_complete_365d_minimum_profit_factor": 1.3,
        "all_complete_365d_minimum_calmar": 1.5,
    }
    assert spec["amount_baseline"]["candidate_table"] == "shared_exact"
    assert spec["amount_baseline"]["portfolio_replay"] == "independent_empty"
    assert spec["overlap"]["comparison_stage"].endswith("before_outcome")
    assert spec["overlap"]["candidate_key_fields"] == [
        "symbol",
        "signal_date",
        "entry_date",
    ]
    assert spec["security_identity"] == {
        "stable_key": "security_id",
        "canonical_symbol_policy": "predecessor_code",
        "market_bar_code_field": "source_ts_code",
        "execution_code_policy": "actual_code_on_trade_date",
        "pre_effective_successor_policy": "exclude_provider_backfill",
        "share_quantity_transition_ratio": 1.0,
        "transition_contract_sha256": (
            transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        ),
    }


def _sessions(count: int = 65) -> list[str]:
    return [
        value.strftime("%Y-%m-%d")
        for value in pd.bdate_range("2025-01-02", periods=count)
    ]


def _bars(
    sessions: list[str],
    *,
    symbols: int = 10,
    industry: str = "测试行业",
) -> pd.DataFrame:
    rows = []
    for offset in range(symbols):
        symbol = f"{offset + 1:06d}"
        ts_code = f"{symbol}.SZ"
        for index, trade_date in enumerate(sessions):
            close = 100.0 + offset + index * 0.1
            rows.append(
                {
                    "date": trade_date,
                    "ts_code": ts_code,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "pre_close": close - 0.1,
                    "amount": 1_000_000.0 + offset,
                    "adj_factor": 1.0,
                    "membership_name": f"测试{offset}",
                    "membership_industry": industry,
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": trade_date,
                    "suspended": 0,
                }
            )
    return pd.DataFrame(rows)


def test_symbol_features_continue_across_official_code_transition():
    sessions = [
        value.strftime("%Y-%m-%d")
        for value in pd.bdate_range("2024-11-25", periods=70)
    ]
    effective = "2025-02-17"
    rows = []
    for index, trade_date in enumerate(sessions):
        close = 60.0 + index * 0.1
        pre_close = 60.0 + max(0, index - 1) * 0.1
        codes = (
            ["300114.SZ", "302132.SZ"]
            if trade_date < effective
            else ["302132.SZ"]
        )
        for ts_code in codes:
            rows.append(
                {
                    "date": trade_date,
                    "ts_code": ts_code,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "pre_close": pre_close,
                    "amount": 1_000_000.0,
                    "adj_factor": 1.0,
                    "membership_name": (
                        "中航电测"
                        if ts_code == "300114.SZ"
                        else "中航成飞"
                    ),
                    "membership_industry": "电器仪表",
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": trade_date,
                    "suspended": 0,
                }
            )
    canonical, _ = transition.apply_security_code_transition_contract(
        pd.DataFrame(rows),
        sessions=sessions,
        contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
    )

    features, _ = residual._build_symbol_return_features(
        canonical,
        sessions,
    )
    latest = features[
        (features["ts_code"] == "300114.SZ")
        & (features["date"] == sessions[-1])
    ].iloc[0]

    assert latest["source_ts_code"] == "302132.SZ"
    assert latest["security_id"] == "cn-a-share:300114.SZ"
    assert latest["observed_history_count"] == 70


@pytest.mark.parametrize("missing_offset", [1, 5])
def test_features_use_global_session_offsets_not_symbol_row_shift(
    missing_offset: int,
):
    sessions = _sessions()
    bars = _bars(sessions)
    target_date = sessions[-1]
    missing_date = sessions[-1 - missing_offset]
    bars = bars[
        ~(
            (bars["ts_code"] == "000001.SZ")
            & (bars["date"] == missing_date)
        )
    ].copy()

    features, receipt = residual._build_symbol_return_features(
        bars,
        sessions,
    )

    assert not bool(
        (
            (features["ts_code"] == "000001.SZ")
            & (features["date"] == target_date)
        ).any()
    )
    assert receipt["status_counts"]["missing_exact_feature_session"] >= 1


@pytest.mark.parametrize(
    ("history_count", "expected"),
    [(59, False), (60, True)],
)
def test_features_require_sixty_complete_observed_bars(
    history_count: int,
    expected: bool,
):
    sessions = _sessions()
    bars = _bars(sessions)
    target_rows = bars["ts_code"] == "000001.SZ"
    keep_dates = set(sessions[-history_count:])
    bars = bars[
        ~target_rows
        | bars["date"].isin(keep_dates)
    ].copy()

    features, receipt = residual._build_symbol_return_features(
        bars,
        sessions,
    )
    present = bool(
        (
            (features["ts_code"] == "000001.SZ")
            & (features["date"] == sessions[-1])
        ).any()
    )

    assert present is expected
    if not expected:
        assert receipt["status_counts"]["insufficient_history"] >= 1


def test_adjusted_returns_are_causal_and_future_invariant():
    sessions = _sessions()
    bars = _bars(sessions)
    signal_date = sessions[-2]
    previous_date = sessions[-3]
    fifth_previous_date = sessions[-7]
    target = bars["ts_code"] == "000001.SZ"
    bars.loc[target & (bars["date"] == signal_date), ["close", "adj_factor"]] = [
        50.0,
        2.0,
    ]
    bars.loc[target & (bars["date"] == previous_date), ["close", "adj_factor"]] = [
        100.0,
        1.0,
    ]
    bars.loc[
        target & (bars["date"] == fifth_previous_date),
        ["close", "adj_factor"],
    ] = [80.0, 1.0]

    first, _ = residual._build_symbol_return_features(
        bars[bars["date"] <= signal_date].copy(),
        sessions[:-1],
    )
    future_changed = bars.copy()
    future_changed.loc[
        target & (future_changed["date"] == sessions[-1]),
        ["close", "adj_factor", "membership_industry"],
    ] = [9999.0, 9.0, "未来行业"]
    second, _ = residual._build_symbol_return_features(
        future_changed,
        sessions,
    )
    columns = ["date", "ts_code", "adjusted_close", "r1", "r5"]
    left = first[
        (first["ts_code"] == "000001.SZ")
        & (first["date"] == signal_date)
    ][columns].reset_index(drop=True)
    right = second[
        (second["ts_code"] == "000001.SZ")
        & (second["date"] == signal_date)
    ][columns].reset_index(drop=True)

    pd.testing.assert_frame_equal(left, right)
    assert left.loc[0, "adjusted_close"] == 100.0
    assert left.loc[0, "r1"] == 0.0
    assert left.loc[0, "r5"] == 0.25


def test_symbol_features_reject_double_negative_price_and_factor():
    sessions = _sessions()
    bars = _bars(sessions)
    target = (
        (bars["ts_code"] == "000001.SZ")
        & (bars["date"] == sessions[-1])
    )
    bars.loc[target, ["close", "adj_factor"]] = [-50.0, -2.0]

    features, receipt = residual._build_symbol_return_features(
        bars,
        sessions,
    )

    assert not bool(
        (
            (features["ts_code"] == "000001.SZ")
            & (features["date"] == sessions[-1])
        ).any()
    )
    assert receipt["status_counts"]["invalid_price_or_adjustment"] == 1


@pytest.mark.parametrize(
    ("r5_values", "expected"),
    [([1.0, 2.0, 3.0], 2.0), ([1.0, 2.0, 3.0, 4.0], 2.5)],
)
def test_industry_median_is_order_invariant_for_odd_and_even_counts(
    r5_values: list[float],
    expected: float,
):
    members = [
        {
            "symbol": f"{index + 1:06d}",
            "ts_code": f"{index + 1:06d}.SZ",
            "r1": value / 10.0,
            "r5": value,
            "membership_receipt_dataset": "bak_basic",
            "membership_receipt_partition": "2025-01-01",
        }
        for index, value in enumerate(r5_values)
    ]

    ordered = residual._deterministic_industry_record(
        "2025-01-01",
        "行业A",
        members,
    )
    shuffled = residual._deterministic_industry_record(
        "2025-01-01",
        "行业A",
        list(reversed(deepcopy(members))),
    )

    assert ordered == shuffled
    assert ordered["median_r5"] == expected
    assert ordered["member_count"] == len(members)


def test_industry_median_includes_target_and_requires_ten_complete_members():
    members = pd.DataFrame(
        [
            {
                "date": "2025-01-01",
                "ts_code": f"{index + 1:06d}.SZ",
                "symbol": f"{index + 1:06d}",
                "name": f"测试{index}",
                "industry_key": "行业A",
                "r1": 0.01 + index / 1000.0,
                "r5": float(index),
                "amount": 1_000_000.0 + index,
                "membership_receipt_dataset": "bak_basic",
                "membership_receipt_partition": "2025-01-01",
            }
            for index in range(10)
        ]
    )

    complete, receipt = residual._build_industry_features(members)

    assert len(complete) == 10
    assert complete["industry_median_r5"].unique().tolist() == [4.5]
    assert receipt["group_count"] == 1
    assert receipt["groups"][0]["member_keys"] == [
        f"cn-a-share:{index + 1:06d}.SZ" for index in range(10)
    ]
    assert receipt["groups"][0]["membership_receipt_refs"] == [
        {"dataset": "bak_basic", "partition": "2025-01-01"}
    ]
    assert receipt["groups"][0]["raw_candidate_keys"] == []
    incomplete, incomplete_receipt = residual._build_industry_features(
        members.iloc[:-1].copy()
    )
    assert incomplete.empty
    assert incomplete_receipt["status_counts"]["industry_member_count_lt_10"] == 1


def test_residual_boundaries_are_strict_and_never_rounded():
    below = math.nextafter(1.0, -math.inf)
    above = math.nextafter(1.0, math.inf)

    assert residual._is_residual_candidate(
        stock_r5=below,
        industry_median_r5=1.0,
        stock_r1=above,
        industry_median_r1=1.0,
    )
    assert not residual._is_residual_candidate(
        stock_r5=1.0,
        industry_median_r5=1.0,
        stock_r1=above,
        industry_median_r1=1.0,
    )
    assert not residual._is_residual_candidate(
        stock_r5=below,
        industry_median_r5=1.0,
        stock_r1=1.0,
        industry_median_r1=1.0,
    )


def test_raw_candidate_receipt_binds_features_and_signal_industry():
    signal_date = "2025-01-02"
    members = pd.DataFrame(
        [
            {
                "date": signal_date,
                "ts_code": f"{index + 1:06d}.SZ",
                "symbol": f"{index + 1:06d}",
                "name": f"测试{index}",
                "industry_key": "行业A",
                "r1": float(9 - index),
                "r5": float(index),
                "amount": 1_000_000.0 + index,
                "membership_receipt_dataset": "bak_basic",
                "membership_receipt_partition": signal_date,
            }
            for index in range(10)
        ]
    )
    enriched, _ = residual._build_industry_features(members)
    frames = {
        f"{index + 1:06d}": pd.DataFrame([{"date": signal_date}])
        for index in range(10)
    }

    candidates, receipt = residual._build_residual_raw_candidates(
        enriched,
        frames_by_symbol=frames,
    )

    assert [candidate["symbol"] for candidate in candidates] == [
        "000001",
        "000002",
        "000003",
        "000004",
        "000005",
    ]
    assert all(candidate["signal_industry"] == "行业A" for candidate in candidates)
    assert all(
        candidate["signal_ts_code"] == candidate["ts_code"]
        for candidate in candidates
    )
    assert all(
        candidate["security_id"] == f"cn-a-share:{candidate['ts_code']}"
        for candidate in candidates
    )
    assert receipt["raw_candidate_count"] == 5
    assert receipt["raw_candidate_keys"] == [
        f"{signal_date}|{symbol}"
        for symbol in ["000001", "000002", "000003", "000004", "000005"]
    ]
    assert len(receipt["raw_candidate_feature_values_sha256"]) == 64


def test_candidate_preserves_stable_and_signal_date_actual_codes():
    signal_date = "2025-02-17"
    members = pd.DataFrame(
        [
            {
                "date": signal_date,
                "ts_code": f"{index + 1:06d}.SZ",
                "symbol": f"{index + 1:06d}",
                "name": f"测试{index}",
                "industry_key": "行业A",
                "r1": float(9 - index),
                "r5": float(index),
                "amount": 1_000_000.0 + index,
                "membership_receipt_dataset": "bak_basic",
                "membership_receipt_partition": signal_date,
            }
            for index in range(10)
        ]
    )
    enriched, _ = residual._build_industry_features(members)
    target = enriched["ts_code"] == "000001.SZ"
    enriched.loc[target, "ts_code"] = "300114.SZ"
    enriched.loc[target, "symbol"] = "300114"
    enriched.loc[target, "source_ts_code"] = "302132.SZ"
    enriched.loc[target, "security_id"] = "cn-a-share:300114.SZ"
    enriched.loc[target, "security_code_transition_id"] = "a" * 64
    enriched.loc[
        target,
        "security_code_transition_contract_sha256",
    ] = transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    frames = {
        str(row.symbol): pd.DataFrame([{"date": signal_date}])
        for row in enriched.itertuples(index=False)
    }

    candidates, _ = residual._build_residual_raw_candidates(
        enriched,
        frames_by_symbol=frames,
    )
    candidate = next(
        item for item in candidates if item["symbol"] == "300114"
    )

    assert candidate["ts_code"] == "300114.SZ"
    assert candidate["signal_ts_code"] == "302132.SZ"
    assert candidate["security_id"] == "cn-a-share:300114.SZ"


def test_uniform_tail_cutoff_is_shared_and_precedes_entry():
    sessions = _sessions(10)
    kept_candidate = {
        **_candidate(
            "000001",
            sessions[3],
            industry="行业A",
            exit_date=sessions[9],
            residual_score=1.0,
            amount=1.0,
        ),
        "signal_frame_index": 3,
    }
    cut_candidate = {
        **_candidate(
            "000002",
            sessions[4],
            industry="行业B",
            exit_date=sessions[9],
            residual_score=1.0,
            amount=1.0,
        ),
        "signal_frame_index": 4,
    }

    kept, receipt = residual._apply_uniform_tail_cutoff(
        [kept_candidate, cut_candidate],
        sessions=sessions,
        family="industry_residual_reversal_5d_1d",
    )

    assert [candidate["symbol"] for candidate in kept] == ["000001"]
    assert receipt["required_signal_to_exit_offset_sessions"] == 6
    assert receipt["kept_count"] == 1
    assert receipt["cut_count"] == 1


def test_legacy_overlap_candidates_use_fixed_masks_and_exact_name_gate(
    monkeypatch,
):
    sessions = _sessions(3)
    frame = pd.DataFrame(
        [
            {
                "date": trade_date,
                "open": 100.0,
                "high": 101.0,
                "low": 99.0,
                "close": 100.0,
                "amount": 1_000_000.0,
                "adj_factor": 1.0,
                "membership_name": "测试股份",
            }
            for trade_date in sessions
        ]
    )

    def fake_masks(source):
        assert source is frame
        return pd.DataFrame(
            {
                "pullback": [False, True, False],
                "breakout": [False, False, True],
            },
            index=source.index,
        )

    monkeypatch.setattr(residual, "_fixed_signal_masks", fake_masks)

    families, receipt = residual._build_legacy_raw_candidates(
        {"000001": frame}
    )

    assert [
        candidate["signal_date"]
        for candidate in families["trend_pullback_ma20_reclaim"]
    ] == [sessions[1]]
    assert [
        candidate["signal_date"]
        for candidate in families["breakout_20d"]
    ] == [sessions[2]]
    assert receipt["families"]["breakout_20d"]["raw_candidate_count"] == 1
    assert receipt["families"]["trend_pullback_ma20_reclaim"][
        "raw_candidate_count"
    ] == 1


def _candidate(
    symbol: str,
    signal_date: str,
    *,
    industry: str,
    exit_date: str,
    residual_score: float,
    amount: float,
    right_censored: bool = False,
) -> dict:
    entry_date = (
        pd.Timestamp(signal_date) + pd.offsets.BDay(1)
    ).strftime("%Y-%m-%d")
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "signal_industry": industry,
        "industry_r5_gap": residual_score,
        "candidate_amount": amount,
        "right_censored": right_censored,
    }


def test_selected_position_freezes_signal_date_industry_until_exit():
    candidates = [
        {
            **_candidate(
                "000001",
                "2025-01-02",
                industry="行业X",
                exit_date="2025-01-06",
                residual_score=3.0,
                amount=100.0,
            ),
            "later_industry": "行业Y",
        },
        _candidate(
            "000002",
            "2025-01-03",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=3.0,
            amount=100.0,
        ),
        _candidate(
            "000003",
            "2025-01-03",
            industry="行业Y",
            exit_date="2025-01-10",
            residual_score=2.0,
            amount=90.0,
        ),
        _candidate(
            "000004",
            "2025-01-06",
            industry="行业X",
            exit_date="2025-01-13",
            residual_score=4.0,
            amount=120.0,
        ),
    ]

    selected, receipt = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="industry_residual",
        top_n=1,
        max_active_positions=3,
    )

    assert [item["symbol"] for item in selected] == [
        "000001",
        "000003",
        "000004",
    ]
    january_third = next(
        day for day in receipt["days"] if day["signal_date"] == "2025-01-03"
    )
    assert january_third["decisions"][0]["decision"] == "active_industry"
    assert receipt["parameters"]["industry_source"] == "signal_date_frozen"


def test_industry_capacity_skips_same_industry_and_scans_downward():
    candidates = [
        _candidate(
            "000001",
            "2025-01-02",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=4.0,
            amount=100.0,
        ),
        _candidate(
            "000002",
            "2025-01-02",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=3.0,
            amount=90.0,
        ),
        _candidate(
            "000003",
            "2025-01-02",
            industry="行业Y",
            exit_date="2025-01-10",
            residual_score=2.0,
            amount=80.0,
        ),
        _candidate(
            "000004",
            "2025-01-02",
            industry="行业Z",
            exit_date="2025-01-10",
            residual_score=1.0,
            amount=70.0,
        ),
    ]

    selected, receipt = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="industry_residual",
        top_n=3,
        max_active_positions=3,
    )

    assert [item["symbol"] for item in selected] == [
        "000001",
        "000003",
        "000004",
    ]
    decisions = receipt["days"][0]["decisions"]
    assert decisions[1]["decision"] == "active_industry"
    assert decisions[2]["decision"] == "selected"
    assert decisions[3]["decision"] == "selected"


def test_main_and_amount_baseline_replay_independently():
    candidates = [
        _candidate(
            "000001",
            "2025-01-02",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=10.0,
            amount=1.0,
        ),
        _candidate(
            "000002",
            "2025-01-02",
            industry="行业Y",
            exit_date="2025-01-10",
            residual_score=1.0,
            amount=10.0,
        ),
    ]

    main, main_receipt = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="industry_residual",
        top_n=1,
        max_active_positions=1,
    )
    baseline, baseline_receipt = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="signal_date_amount",
        top_n=1,
        max_active_positions=1,
    )

    assert [item["symbol"] for item in main] == ["000001"]
    assert [item["symbol"] for item in baseline] == ["000002"]
    assert (
        main_receipt["candidate_table_sha256"]
        == baseline_receipt["candidate_table_sha256"]
    )
    assert main_receipt["receipt_sha256"] != baseline_receipt["receipt_sha256"]


def test_selected_censor_blocks_and_unselected_censor_does_not():
    censor = _candidate(
        "000001",
        "2025-01-02",
        industry="行业X",
        exit_date="2026-07-03",
        residual_score=10.0,
        amount=1.0,
        right_censored=True,
    )
    complete = _candidate(
        "000002",
        "2025-01-02",
        industry="行业Y",
        exit_date="2025-01-10",
        residual_score=1.0,
        amount=10.0,
    )

    selected, _ = residual._select_with_industry_cap_receipt(
        [censor, complete],
        rank_mode="industry_residual",
        top_n=1,
        max_active_positions=1,
    )
    unselected, _ = residual._select_with_industry_cap_receipt(
        [censor, complete],
        rank_mode="signal_date_amount",
        top_n=1,
        max_active_positions=1,
    )

    assert not residual._selected_evidence_complete(selected)
    assert residual._selected_evidence_complete(unselected)


def test_amount_baseline_censor_or_any_main_gate_blocks_advancement():
    main = {
        "target_all_pass": True,
        "target_rolling_12m_stability_pass": True,
        "evidence_complete": True,
    }

    assert residual._advancement_gate_passes(
        main,
        {"evidence_complete": True},
    )
    assert not residual._advancement_gate_passes(
        main,
        {"evidence_complete": False},
    )
    assert not residual._advancement_gate_passes(
        {**main, "target_rolling_12m_stability_pass": False},
        {"evidence_complete": True},
    )


def test_overlap_uses_post_entry_filter_exact_keys_and_fixed_denominators():
    new_keys = {
        ("000001", "2025-01-02", "2025-01-03"),
        ("000002", "2025-01-02", "2025-01-03"),
    }
    old_keys = {
        ("000001", "2025-01-02", "2025-01-03"),
        ("000002", "2025-01-02", "2025-01-06"),
        ("000003", "2025-01-02", "2025-01-03"),
    }

    receipt = residual._overlap_receipt(
        new_keys,
        {
            "breakout_20d": old_keys,
            "trend_pullback_ma20_reclaim": set(),
        },
    )
    breakout = receipt["families"]["breakout_20d"]

    assert breakout["intersection_count"] == 1
    assert breakout["new_candidate_count"] == 2
    assert breakout["old_candidate_count"] == 3
    assert breakout["intersection_over_new"] == 0.5
    assert breakout["intersection_over_old"] == pytest.approx(1 / 3)
    assert breakout["jaccard"] == 0.25


def test_industry_and_selection_receipt_verifiers_reject_tampering():
    members = pd.DataFrame(
        [
            {
                "date": "2025-01-01",
                "ts_code": f"{index + 1:06d}.SZ",
                "symbol": f"{index + 1:06d}",
                "name": f"测试{index}",
                "industry_key": "行业A",
                "r1": 0.01 + index / 1000.0,
                "r5": float(index),
                "amount": 1_000_000.0 + index,
                "membership_receipt_dataset": "bak_basic",
                "membership_receipt_partition": "2025-01-01",
            }
            for index in range(10)
        ]
    )
    _, industry_receipt = residual._build_industry_features(members)
    residual.verify_industry_feature_receipt(members, industry_receipt)
    tampered_industry = deepcopy(industry_receipt)
    tampered_industry["groups"][0]["median_r5"] += 1.0
    with pytest.raises(ValueError, match="industry feature receipt"):
        residual.verify_industry_feature_receipt(members, tampered_industry)

    candidates = [
        _candidate(
            "000001",
            "2025-01-02",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=2.0,
            amount=1.0,
        ),
        _candidate(
            "000002",
            "2025-01-02",
            industry="行业Y",
            exit_date="2025-01-10",
            residual_score=1.0,
            amount=2.0,
        ),
    ]
    _, selection_receipt = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="industry_residual",
        top_n=3,
        max_active_positions=3,
    )
    residual.verify_industry_selection_receipt(
        candidates,
        selection_receipt,
        expected_rank_mode="industry_residual",
    )
    tampered_selection = deepcopy(selection_receipt)
    tampered_selection["days"][0]["decisions"][0]["decision"] = "rejected"
    with pytest.raises(ValueError, match="industry selection receipt"):
        residual.verify_industry_selection_receipt(
            candidates,
            tampered_selection,
            expected_rank_mode="industry_residual",
        )


def test_selection_verifier_rejects_nonfrozen_capacity_receipt():
    candidates = [
        _candidate(
            "000001",
            "2025-01-02",
            industry="行业X",
            exit_date="2025-01-10",
            residual_score=2.0,
            amount=1.0,
        ),
        _candidate(
            "000002",
            "2025-01-02",
            industry="行业Y",
            exit_date="2025-01-10",
            residual_score=1.0,
            amount=2.0,
        ),
    ]
    _, nonfrozen = residual._select_with_industry_cap_receipt(
        candidates,
        rank_mode="industry_residual",
        top_n=1,
        max_active_positions=1,
    )

    with pytest.raises(ValueError, match="frozen strategy"):
        residual.verify_industry_selection_receipt(
            candidates,
            nonfrozen,
            expected_rank_mode="industry_residual",
        )


class _FakeAdapter:
    def __init__(self, verdicts):
        self.verdicts = verdicts
        self.calls = []

    def next_open(self, symbol, trade_date, side="buy"):
        self.calls.append((symbol, trade_date, side))
        verdict = dict(self.verdicts[(symbol, trade_date, side)])
        verdict["generation_proof"] = {
            "trade_date": trade_date,
            "generation_id": f"generation-{trade_date}",
            "manifest_sha256": "a" * 64,
            "lineage_sha256": "b" * 64,
            "vintage": "historical_backfill",
        }
        return verdict


def _preflight_frame(
    sessions: list[str],
    *,
    entry_open: float,
    entry_high: float = 120.0,
    entry_low: float = 80.0,
) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "date": trade_date,
                "open": entry_open if index == 1 else 100.0,
                "high": entry_high if index == 1 else 101.0,
                "low": entry_low if index == 1 else 99.0,
                "close": 100.0,
                "amount": 1_000_000.0,
                "adj_factor": 1.0,
                "membership_name": "测试股份",
            }
            for index, trade_date in enumerate(sessions)
        ]
    )


def test_next_open_filter_runs_before_both_rankings_and_backfills():
    sessions = _sessions(8)
    frames = {
        "000001": _preflight_frame(sessions, entry_open=106.01),
        "000002": _preflight_frame(sessions, entry_open=106.0),
        "000003": _preflight_frame(sessions, entry_open=100.0),
    }
    raw_candidates = [
        {
            **_candidate(
                symbol,
                sessions[0],
                industry=industry,
                exit_date=sessions[6],
                residual_score=score,
                amount=amount,
            ),
            "signal_frame_index": 0,
        }
        for symbol, industry, score, amount in (
            ("000001", "行业X", 10.0, 100.0),
            ("000002", "行业Y", 9.0, 90.0),
            ("000003", "行业Z", 8.0, 80.0),
        )
    ]
    adapter = _FakeAdapter(
        {
            (symbol, sessions[1], "buy"): {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": float(frame.iloc[1]["open"]),
            }
            for symbol, frame in frames.items()
        }
    )

    executable, receipt, _ = residual._preflight_strict_entries(
        raw_candidates,
        frames_by_symbol=frames,
        sessions=sessions,
        adapter=adapter,
    )
    main, _ = residual._select_with_industry_cap_receipt(
        executable,
        rank_mode="industry_residual",
        top_n=2,
        max_active_positions=2,
    )
    baseline, _ = residual._select_with_industry_cap_receipt(
        executable,
        rank_mode="signal_date_amount",
        top_n=2,
        max_active_positions=2,
    )

    assert [item["symbol"] for item in executable] == ["000002", "000003"]
    assert [item["symbol"] for item in main] == ["000002", "000003"]
    assert [item["symbol"] for item in baseline] == ["000002", "000003"]
    assert receipt["status_counts"]["entry_strategy_filter_rejected"] == 1
    changed = {
        symbol: frame.assign(
            high=[101.0, 10000.0, *([101.0] * 6)],
            low=[99.0, 0.01, *([99.0] * 6)],
        )
        for symbol, frame in frames.items()
    }
    second_adapter = _FakeAdapter(adapter.verdicts)
    second, second_receipt, _ = residual._preflight_strict_entries(
        raw_candidates,
        frames_by_symbol=changed,
        sessions=sessions,
        adapter=second_adapter,
    )
    assert [
        (item["symbol"], item["entry_date"])
        for item in second
    ] == [
        (item["symbol"], item["entry_date"])
        for item in executable
    ]
    assert second_receipt == receipt


def test_tail_cutoff_precedes_every_entry_and_outcome_query():
    sessions = _sessions(8)
    signal_date = sessions[2]
    frame = _preflight_frame(sessions, entry_open=100.0)
    candidate = {
        **_candidate(
            "000001",
            signal_date,
            industry="行业X",
            exit_date=sessions[-1],
            residual_score=1.0,
            amount=1.0,
        ),
        "signal_frame_index": 2,
    }
    adapter = _FakeAdapter({})

    executable, receipt, _ = residual._preflight_strict_entries(
        [candidate],
        frames_by_symbol={"000001": frame},
        sessions=sessions,
        adapter=adapter,
    )

    assert executable == []
    assert receipt["status_counts"]["administrative_signal_cutoff"] == 1
    assert adapter.calls == []


def test_exact_membership_loader_binds_signal_date_industry_receipt():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE market_session_generation_head (
            trade_date TEXT,
            generation_id TEXT
        );
        CREATE TABLE market_session_generation_rows_daily (
            trade_date TEXT,
            generation_id TEXT,
            ts_code TEXT,
            open REAL,
            high REAL,
            low REAL,
            close REAL,
            pre_close REAL,
            amount REAL
        );
        CREATE TABLE market_session_generation_rows_adj_factor (
            trade_date TEXT,
            generation_id TEXT,
            ts_code TEXT,
            adj_factor REAL
        );
        CREATE TABLE market_session_generation_rows_suspend_d (
            trade_date TEXT,
            generation_id TEXT,
            ts_code TEXT,
            suspend_type TEXT
        );
        CREATE TABLE daily_universe (
            trade_date TEXT,
            ts_code TEXT,
            name TEXT,
            industry TEXT,
            receipt_dataset TEXT,
            receipt_partition TEXT
        );
        INSERT INTO market_session_generation_head VALUES
            ('2025-01-02', 'generation-1');
        INSERT INTO market_session_generation_rows_daily VALUES
            ('2025-01-02', 'generation-1', '000001.SZ',
             10, 11, 9, 10.5, 10, 1000000);
        INSERT INTO market_session_generation_rows_adj_factor VALUES
            ('2025-01-02', 'generation-1', '000001.SZ', 2);
        INSERT INTO daily_universe VALUES
            ('2025-01-02', '000001.SZ', '测试股份', '行业A',
             'bak_basic', '2025-01-02');
        """
    )

    bars, receipt = residual._load_industry_feature_bars(
        connection,
        start_date="2025-01-02",
        end_date="2025-01-02",
    )

    assert bars.loc[0, "membership_industry"] == "行业A"
    assert bars.loc[0, "membership_receipt_dataset"] == "bak_basic"
    assert bars.loc[0, "membership_receipt_partition"] == "2025-01-02"
    assert receipt["matched_exact_membership_row_count"] == 1
    assert receipt["missing_exact_membership_row_count"] == 0
    assert len(receipt["receipt_sha256"]) == 64
    connection.execute(
        "UPDATE daily_universe SET receipt_partition = '2025-01-01'"
    )
    with pytest.raises(
        residual.AuditedPITDevelopmentReplayError,
        match="non-exact membership receipt",
    ):
        residual._load_industry_feature_bars(
            connection,
            start_date="2025-01-02",
            end_date="2025-01-02",
        )


def test_strict_outcome_builder_reuses_preflight_and_preserves_frozen_industry():
    sessions = _sessions(8)
    frame = _preflight_frame(sessions, entry_open=100.0)
    candidate = {
        **_candidate(
            "000001",
            sessions[0],
            industry="行业X",
            exit_date=sessions[6],
            residual_score=0.03,
            amount=100.0,
        ),
        "name": "测试股份",
        "signal_frame_index": 0,
        "industry_r1_residual": 0.01,
        "industry_r5_residual": -0.03,
        "industry_group_sha256": "c" * 64,
    }
    adapter = _FakeAdapter(
        {
            ("000001", sessions[1], "buy"): {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": 100.0,
            },
            ("000001", sessions[6], "sell"): {
                "fillable": True,
                "reason": "raw_open",
                "raw_price": 100.0,
            },
        }
    )
    executable, _, cache = residual._preflight_strict_entries(
        [candidate],
        frames_by_symbol={"000001": frame},
        sessions=sessions,
        adapter=adapter,
    )

    completed, censored, receipt = residual._build_strict_outcomes(
        executable,
        frames_by_symbol={"000001": frame},
        sessions=sessions,
        adapter=adapter,
        verdict_cache=cache,
        suspension_evidence={},
        terminal_listing_evidence={},
    )

    assert censored == []
    assert len(completed) == 1
    assert completed[0]["signal_industry"] == "行业X"
    assert completed[0]["industry_r5_gap"] == 0.03
    assert completed[0]["exit_date"] == sessions[6]
    assert receipt["status_counts"] == {"candidate_built": 1}


def test_fixed_evaluation_enforces_every_preregistered_gate(monkeypatch):
    candidates = []
    signal_dates = _sessions(140)[::7][:20]
    for index, signal_date in enumerate(signal_dates):
        exit_date = (
            pd.Timestamp(signal_date) + pd.offsets.BDay(6)
        ).strftime("%Y-%m-%d")
        candidates.append(
            _candidate(
                f"{index + 1:06d}",
                signal_date,
                industry=f"行业{index}",
                exit_date=exit_date,
                residual_score=1.0,
                amount=100.0,
            )
        )
    passing_metrics = {
        "selected_trade_count": 20,
        "trade_win_rate_pct": 52.0,
        "trade_profit_factor": 1.3,
        "portfolio_max_drawdown_pct": -15.0,
        "rolling_1y_latest_full_window": True,
        "rolling_1y_latest_return_pct": 50.0,
        "calmar_latest_12m": 1.5,
        "rolling_1y_windows": [
            {
                "return_pct": 50.0,
                "max_drawdown_pct": -15.0,
                "payoff_ratio": 1.3,
                "profit_factor": 1.3,
                "calmar": 1.5,
            }
        ],
    }
    monkeypatch.setattr(
        residual,
        "_trade_metrics",
        lambda *_args, **_kwargs: deepcopy(passing_metrics),
    )

    sweep, _ = residual._evaluate_fixed_family(
        candidates,
        rank_mode="industry_residual",
        required_tags=["industry_residual_reversal_5d_1d"],
        evaluation_session_dates=_sessions(400),
    )
    row = residual._single_fixed_spec_row(sweep)

    assert row["target_all_pass"] is True
    assert row["target_rolling_12m_stability_pass"] is True
    failing_metrics = deepcopy(passing_metrics)
    failing_metrics["rolling_1y_windows"][0]["profit_factor"] = 1.29
    monkeypatch.setattr(
        residual,
        "_trade_metrics",
        lambda *_args, **_kwargs: deepcopy(failing_metrics),
    )
    failed, _ = residual._evaluate_fixed_family(
        candidates,
        rank_mode="industry_residual",
        required_tags=["industry_residual_reversal_5d_1d"],
        evaluation_session_dates=_sessions(400),
    )
    failed_row = residual._single_fixed_spec_row(failed)
    assert failed_row["target_all_pass"] is True
    assert failed_row["target_rolling_12m_stability_pass"] is False
    assert not residual._advancement_gate_passes(
        failed_row,
        {"evidence_complete": True},
    )


def test_runner_requires_frozen_settings_before_opening_artifacts(tmp_path):
    with pytest.raises(
        residual.AuditedPITDevelopmentReplayError,
        match="requires frozen settings",
    ):
        residual.run_audited_pit_industry_residual_reversal(
            settings=object(),
            audited_pit_universe_path=tmp_path / "universe.sqlite3",
            expected_coverage_audit_sha256="a" * 64,
            expected_artifact_root_sha256="b" * 64,
            temporal_contract_path=tmp_path / "temporal.json",
            expected_temporal_contract_sha256="c" * 64,
            security_code_transition_evidence_root=(
                tmp_path / "transition-evidence"
            ),
            expected_security_code_transition_contract_sha256=(
                transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
            ),
            start_date="2025-01-02",
            end_date="2026-07-03",
            output_dir=tmp_path / "output",
        )


def test_producer_binding_drift_fails_closed(monkeypatch):
    frozen = {"root_sha256": "a" * 64}
    monkeypatch.setattr(
        residual,
        "_producer_binding",
        lambda: {"root_sha256": "b" * 64},
    )

    with pytest.raises(
        residual.AuditedPITDevelopmentReplayError,
        match="changed during replay",
    ):
        residual._assert_producer_binding_unchanged(frozen)


def test_jobs_cli_dispatches_industry_residual_replay(monkeypatch, tmp_path):
    from app import jobs

    captured = {}

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"schema_version": "test-result/v1"}

    monkeypatch.setattr(
        jobs,
        "run_audited_pit_industry_residual_reversal",
        fake_runner,
        raising=False,
    )
    monkeypatch.setattr(jobs, "get_settings", lambda: "settings")
    result = jobs.main(
        [
            "research-audited-pit-industry-residual-reversal",
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
            str(tmp_path / "transition-evidence"),
            "--expected-security-code-transition-contract-sha256",
            transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256,
            "--start-date",
            "2025-01-02",
            "--end-date",
            "2026-07-03",
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )

    assert result == 0
    assert captured["settings"] == "settings"
    assert captured["start_date"] == "2025-01-02"
    assert captured["end_date"] == "2026-07-03"
    assert captured["security_code_transition_evidence_root"] == str(
        tmp_path / "transition-evidence"
    )
    assert (
        captured["expected_security_code_transition_contract_sha256"]
        == transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    )


def test_end_to_end_replay_is_development_only_and_path_stable(
    monkeypatch,
    tmp_path,
):
    from app.config import Settings

    sessions = _sessions(66)
    rows = []
    for offset in range(10):
        symbol = f"{offset + 1:06d}"
        ts_code = f"{symbol}.SZ"
        base = 100.0 + offset / 10.0
        for index, trade_date in enumerate(sessions):
            close = base
            if offset == 0 and index == 58:
                close = 90.0
            elif offset == 0 and index >= 59:
                close = 95.0
            rows.append(
                {
                    "date": trade_date,
                    "ts_code": ts_code,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "pre_close": close,
                    "amount": 1_000_000.0 + offset,
                    "adj_factor": 1.0,
                    "membership_name": f"测试{offset}",
                    "membership_industry": "行业A",
                    "membership_receipt_dataset": "bak_basic",
                    "membership_receipt_partition": trade_date,
                    "suspended": 0,
                }
            )
    bars, transition_application_receipt = (
        transition.apply_security_code_transition_contract(
            pd.DataFrame(rows),
            sessions=sessions,
            contract=transition.SECURITY_CODE_TRANSITION_CONTRACT,
        )
    )
    bar_receipt = {
        "schema_version": "test-loader/v2",
        "matched_exact_membership_row_count": len(bars),
        "missing_exact_membership_row_count": 0,
        "security_code_transition_application": (
            transition_application_receipt
        ),
        "security_code_transition_application_receipt_sha256": (
            transition_application_receipt["receipt_sha256"]
        ),
    }
    bar_receipt["receipt_sha256"] = residual._sha256(bar_receipt)
    coverage_sha = "a" * 64
    artifact_root_sha = "b" * 64
    temporal_sha = "c" * 64
    transition_evidence_receipt = {
        "schema_version": "test-transition-evidence/v1",
        "contract_sha256": (
            transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        ),
        "contract": deepcopy(
            transition.SECURITY_CODE_TRANSITION_CONTRACT
        ),
    }
    transition_evidence_receipt["receipt_sha256"] = residual._sha256(
        transition_evidence_receipt
    )

    class FakeUniverse:
        start_date = sessions[0]
        end_date = sessions[-1]
        coverage_audit_sha256 = coverage_sha
        artifact_root_sha256 = artifact_root_sha
        temporal_contract_sha256 = temporal_sha
        temporal_role = "development"

        def _require_open(self):
            return object()

        def close(self):
            return None

    by_key = {
        (str(row.ts_code)[:6], str(row.date)): row
        for row in bars.itertuples(index=False)
    }

    class FakeAdapter:
        artifact_root_sha256 = artifact_root_sha
        contract_sha256 = "d" * 64

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
                    "manifest_sha256": "e" * 64,
                    "lineage_sha256": "f" * 64,
                    "vintage": "historical_backfill",
                },
            }

    monkeypatch.setattr(
        residual,
        "load_temporal_partition_contract",
        lambda _path: {"contract_sha256": temporal_sha},
    )
    monkeypatch.setattr(
        residual,
        "assert_range_allowed",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        residual,
        "load_security_code_transition_evidence",
        lambda *_args, **_kwargs: deepcopy(
            transition_evidence_receipt
        ),
    )
    monkeypatch.setattr(
        residual.AuditedPointInTimeUniverse,
        "from_file",
        lambda *_args, **_kwargs: FakeUniverse(),
    )
    monkeypatch.setattr(
        residual,
        "_exact_membership_sessions",
        lambda *_args, **_kwargs: sessions,
    )
    monkeypatch.setattr(
        residual,
        "_load_industry_feature_bars",
        lambda *_args, **_kwargs: (bars.copy(), deepcopy(bar_receipt)),
    )
    monkeypatch.setattr(
        residual,
        "_load_suspension_evidence",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        residual,
        "_load_terminal_listing_evidence",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(residual, "ArtifactNativeReplayAdapter", FakeAdapter)

    common = {
        "settings": Settings(),
        "audited_pit_universe_path": tmp_path / "universe.sqlite3",
        "expected_coverage_audit_sha256": coverage_sha,
        "expected_artifact_root_sha256": artifact_root_sha,
        "temporal_contract_path": tmp_path / "temporal.json",
        "expected_temporal_contract_sha256": temporal_sha,
        "security_code_transition_evidence_root": (
            tmp_path / "transition-evidence"
        ),
        "expected_security_code_transition_contract_sha256": (
            transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
        ),
        "start_date": sessions[0],
        "end_date": sessions[-1],
    }
    first = residual.run_audited_pit_industry_residual_reversal(
        **common,
        output_dir=tmp_path / "first",
    )
    second = residual.run_audited_pit_industry_residual_reversal(
        **common,
        output_dir=tmp_path / "second",
    )

    assert first["scope"]["development_only"] is True
    assert first["scope"]["final_oos_consumed"] is False
    assert first["completed_candidate_count"] == 1
    assert first["right_censored_position_count"] == 0
    assert first["source"][
        "security_code_transition_contract_sha256"
    ] == transition.SECURITY_CODE_TRANSITION_CONTRACT_SHA256
    assert first["source"][
        "security_code_transition_evidence_receipt_sha256"
    ] == transition_evidence_receipt["receipt_sha256"]
    assert first["source"][
        "security_code_transition_application_receipt_sha256"
    ] == transition_application_receipt["receipt_sha256"]
    assert first["source"][
        "base_artifact_native_replay_contract_sha256"
    ] == FakeAdapter.contract_sha256
    assert first["source"][
        "artifact_native_replay_contract_sha256"
    ] != FakeAdapter.contract_sha256
    assert first["artifact"]["artifact_sha256"] == second["artifact"][
        "artifact_sha256"
    ]
    assert first["industry_receipt_sidecar"] == second[
        "industry_receipt_sidecar"
    ]
    assert first["execution_candidate_sidecar"] == second[
        "execution_candidate_sidecar"
    ]
    artifact_body = json.loads(
        (
            tmp_path
            / "first"
            / f"{first['artifact']['artifact_sha256']}.json"
        ).read_text(encoding="utf-8")
    )
    stored_hash = artifact_body.pop("artifact_sha256")
    assert stored_hash == residual._sha256(artifact_body)
    assert str(tmp_path / "first") not in json.dumps(
        artifact_body,
        ensure_ascii=False,
    )
    for sidecar in first["runtime_sidecars"].values():
        sidecar_body = json.loads(
            Path(sidecar["path"]).read_text(encoding="utf-8")
        )
        for key in (
            "security_code_transition_contract_sha256",
            "security_code_transition_evidence_receipt_sha256",
            "security_code_transition_application_receipt_sha256",
            "base_artifact_native_replay_contract_sha256",
            "artifact_native_replay_contract_sha256",
        ):
            assert sidecar_body["source"][key] == first["source"][key]
        if sidecar_body["schema_version"] == (
            "audited-pit-industry-feature-sidecar/v2"
        ):
            assert sidecar_body[
                "security_code_transition_evidence"
            ] == transition_evidence_receipt
        sidecar_hash = sidecar_body.pop("artifact_sha256")
        assert sidecar_hash == residual._sha256(sidecar_body)
