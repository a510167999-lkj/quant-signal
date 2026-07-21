import json
from datetime import date, timedelta
from pathlib import Path

import app.research_sweep as research_sweep
from app.jobs import (
    _compact_hold_sweep_result,
    _compact_research_sweep_payload,
    _load_qualified_trades_payload,
    _parse_hold_days_list,
    _write_qualified_trades_payload,
)
from app.research_backtest import _window_portfolio_stats
from app.research_sweep import (
    _apply_partial_profit_lock,
    _apply_prior_high_trailing_stop,
    _holding_calendar_gap_tags,
    _latest_window_portfolio_stats,
    _trade_metrics,
    _trade_sweep_tags,
    _truncate_trade_before_calendar_gap,
    sweep_qualified_trades,
)


def test_trade_metrics_exposes_full_rolling_12m_and_window_calmar():
    trades = []
    start = date(2024, 1, 5)
    for index in range(20):
        signal_date = start + timedelta(days=index * 30)
        trades.append(
            {
                "symbol": "%06d" % (600000 + index),
                "signal_date": signal_date.isoformat(),
                "entry_date": signal_date.isoformat(),
                "exit_date": (signal_date + timedelta(days=5)).isoformat(),
                "return_pct": 5.0 if index % 4 else -3.0,
                "max_adverse_pct": -1.0,
            }
        )

    metrics = _trade_metrics(trades, hold_days=5)

    windows = metrics["rolling_1y_windows"]
    assert len(windows) >= 2
    latest = windows[-1]
    assert metrics["rolling_1y_latest_return_pct"] == latest["return_pct"]
    assert metrics["rolling_1y_latest_max_drawdown_pct"] == latest["max_drawdown_pct"]
    assert "payoff_ratio" in latest
    assert "profit_factor" in latest
    assert "calmar" in latest
    if latest["max_drawdown_pct"]:
        expected_calmar = latest["return_pct"] / abs(latest["max_drawdown_pct"])
        assert metrics["calmar_latest_12m"] == round(expected_calmar, 2)


def test_sweep_qualified_trades_ranks_target_passing_filters():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 8,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "candidate_rank": 12,
            "candidate_amount": 500000000,
            "candidate_change_pct": 2.5,
            "candidate_prefilter_score": 12.5,
            "prior_win_rate_pct": 62,
            "prior_avg_return_pct": 1.5,
            "prior_avg_adverse_pct": 3.8,
            "market_level": "favorable",
            "signal_tags": [
                "score_gte_5",
                "balanced_rsi",
                "volume_confirmed",
                "breadth_ma20_gte_60",
            ],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-03",
            "exit_date": "2025-01-13",
            "return_pct": 6,
            "max_adverse_pct": -1.5,
            "rank_score": 9,
            "candidate_rank": 55,
            "candidate_amount": 50000000,
            "candidate_change_pct": 4.5,
            "candidate_prefilter_score": 11.5,
            "prior_win_rate_pct": 45,
            "prior_avg_return_pct": 0.5,
            "prior_avg_adverse_pct": 5.0,
            "market_level": "favorable",
            "signal_tags": [
                "score_gte_5",
                "balanced_rsi",
                "volume_confirmed",
                "breadth_ma20_gte_60",
            ],
        },
        {
            "symbol": "600003",
            "signal_date": "2025-01-04",
            "exit_date": "2025-01-14",
            "return_pct": -4,
            "max_adverse_pct": -5,
            "rank_score": 8,
            "candidate_rank": 70,
            "candidate_amount": 50000000,
            "candidate_change_pct": -1.5,
            "candidate_prefilter_score": 10,
            "prior_win_rate_pct": 40,
            "prior_avg_return_pct": -1,
            "prior_avg_adverse_pct": 6,
            "market_level": "defensive",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=2,
        min_trades=2,
        max_filter_size=1,
        symbol_cooldown_days=0,
        max_active_positions=0,
    )

    assert result["qualified_trade_count"] == 3
    assert "candidate_rank_lte_20" in result["available_tags"]
    assert "amount_gte_300m" in result["available_tags"]
    assert "candidate_change_0_to_3" in result["available_tags"]
    assert "candidate_change_3_to_6" in result["available_tags"]
    assert "candidate_change_negative" in result["available_tags"]
    assert "prefilter_score_gte_12" in result["available_tags"]
    assert "prior_win_gte_60" in result["available_tags"]
    assert "prior_return_gte_1" in result["available_tags"]
    assert "prior_adverse_lte_4" in result["available_tags"]
    assert "breadth_ma20_gte_60" in result["available_tags"]
    assert result["target_win_drawdown_pass_count"] > 0
    assert result["target_all_pass_count"] == 0
    assert result["diagnostics"]["best_by_win_rate"]["trade_win_rate_pct"] == 100.0
    assert result["top"]
    best = result["top"][0]
    assert best["target_win_drawdown_pass"] is True
    assert best["target_one_year_return_pass"] is False
    assert best["target_all_pass"] is False
    assert best["target_rolling_12m_stability_pass"] is False
    assert best["trade_win_rate_pct"] == 100.0
    assert best["selected_trade_count"] == 2
    assert best["required_signal_tags"] or best["market_levels"]


def test_sweep_qualified_trades_uses_configurable_one_year_target():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-02-03",
            "exit_date": "2025-02-13",
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=1,
        target_one_year_return_pct=4,
    )

    best = result["top"][0]
    assert result["target_one_year_return_pct"] == 4
    assert best["target_one_year_return_pass"] is True
    assert best["target_all_pass"] is True
    assert best["target_gap_1y_return_pct"] <= 0


def test_sweep_qualified_trades_defaults_to_current_50pct_return_target():
    result = sweep_qualified_trades(
        [
            {
                "symbol": "600001",
                "signal_date": "2025-01-02",
                "exit_date": "2025-01-12",
                "return_pct": 20,
                "max_adverse_pct": -1,
                "rank_score": 10,
                "market_level": "favorable",
                "signal_tags": ["score_gte_5", "balanced_rsi"],
            },
            {
                "symbol": "600002",
                "signal_date": "2025-02-03",
                "exit_date": "2025-02-13",
                "return_pct": 20,
                "max_adverse_pct": -1,
                "rank_score": 9,
                "market_level": "favorable",
                "signal_tags": ["score_gte_5", "balanced_rsi"],
            },
        ],
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=1,
    )

    assert result["target_one_year_return_pct"] == 50.0


def test_sweep_qualified_trades_fixed_spec_never_selects_a_better_market_subset():
    trades = []
    for index in range(20):
        trades.append(
            {
                "symbol": f"{600100 + index:06d}",
                "signal_date": f"2025-01-{index + 1:02d}",
                "exit_date": f"2025-02-{index + 1:02d}",
                "return_pct": 4 if index % 2 == 0 else -4,
                "max_adverse_pct": -1 if index % 2 == 0 else -5,
                "rank_score": 20 - index,
                "market_level": "favorable" if index % 2 == 0 else "defensive",
                "signal_tags": ["score_gte_5"],
            }
        )

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=10,
        min_trades=1,
        max_filter_size=0,
        required_signal_tags=[],
        excluded_signal_tags=[],
        market_levels=[],
        fixed_spec=True,
    )

    assert result["spec_count"] == 1
    assert result["top"][0]["required_signal_tags"] == []
    assert result["top"][0]["market_levels"] == []
    assert result["top"][0]["selected_trade_count"] == 20
    assert result["top"][0]["trade_win_count"] == 10
    assert result["top"][0]["trade_nonwin_count"] == 10
    assert result["top"][0]["trade_win_rate_pct"] == 50.0


def test_slot_daily_rolling_trade_count_counts_trades_not_position_days():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-01",
            "entry_date": "2025-01-02",
            "exit_date": "2025-01-04",
            "return_pct": 3,
            "max_adverse_pct": -1,
            "mark_to_market_path": [
                {"date": "2025-01-02", "close_return_pct": 1, "low_return_pct": -1},
                {"date": "2025-01-03", "close_return_pct": 2, "low_return_pct": 0},
                {"date": "2025-01-04", "close_return_pct": 3, "low_return_pct": 1},
            ],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-05",
            "return_pct": 4,
            "max_adverse_pct": -1,
            "mark_to_market_path": [
                {"date": "2025-01-03", "close_return_pct": 1, "low_return_pct": -1},
                {"date": "2025-01-04", "close_return_pct": 2, "low_return_pct": 0},
                {"date": "2025-01-05", "close_return_pct": 4, "low_return_pct": 1},
            ],
        },
    ]

    metrics = _trade_metrics(
        trades,
        hold_days=3,
        max_active_positions=2,
        capital_model="slot-daily",
    )

    assert metrics["rolling_1y_latest_trade_count"] == 2
    assert metrics["rolling_1y_latest_active_position_days"] == 6


def test_trade_metrics_reports_payoff_profit_factor_and_calmar():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2024-01-02",
            "entry_date": "2024-01-03",
            "exit_date": "2024-01-04",
            "return_pct": 10.0,
            "max_adverse_pct": -1.0,
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-03",
            "entry_date": "2025-01-06",
            "exit_date": "2025-01-07",
            "return_pct": -5.0,
            "max_adverse_pct": -5.0,
        },
    ]

    metrics = _trade_metrics(trades, hold_days=1, capital_model="signal-day")

    assert metrics["trade_payoff_ratio"] == 2.0
    assert metrics["trade_profit_factor"] == 2.0
    assert metrics["portfolio_calmar_latest_1y"] == round(
        metrics["rolling_1y_latest_return_pct"]
        / abs(metrics["portfolio_max_drawdown_pct"]),
        2,
    )


def test_sweep_target_requires_profit_factor_and_calmar_quality():
    trades = []
    for index in range(10):
        trades.append(
            {
                "symbol": f"{601000 + index:06d}",
                "signal_date": f"2024-{index + 1:02d}-02",
                "entry_date": f"2024-{index + 1:02d}-03",
                "exit_date": f"2024-{index + 1:02d}-04",
                "return_pct": 1.0 if index < 9 else -8.0,
                "max_adverse_pct": -1.0 if index < 9 else -8.0,
                "rank_score": 10 - index,
                "market_level": "favorable",
                "signal_tags": ["score_gte_5"],
            }
        )

    result = sweep_qualified_trades(
        trades,
        hold_days=1,
        top_n=1,
        min_trades=10,
        max_filter_size=0,
        target_one_year_return_pct=-100.0,
        fixed_spec=True,
    )

    best = result["top"][0]
    assert best["target_win_drawdown_pass"] is True
    assert best["trade_profit_factor"] < 1.3
    assert best["target_quality_pass"] is False
    assert best["target_all_pass"] is False


def test_sweep_qualified_trades_can_scan_exposure_multiplier():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-02-03",
            "exit_date": "2025-02-13",
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=1,
        target_one_year_return_pct=10,
        exposure_multipliers=[1, 3],
    )

    best = result["top"][0]
    assert result["exposure_multipliers"] == [1.0, 3.0]
    assert best["exposure_multiplier"] == 3.0
    assert best["target_all_pass"] is True


def test_sweep_qualified_trades_can_force_exact_exposure_multiplier():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 20,
            "max_adverse_pct": -8,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-02-03",
            "exit_date": "2025-02-13",
            "return_pct": 20,
            "max_adverse_pct": -8,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=0,
        target_drawdown_pct=5,
        exposure_multipliers=[2],
        force_exposure_multipliers=True,
        required_signal_tags=["score_gte_5"],
        market_levels=["favorable"],
    )

    assert result["exposure_multipliers"] == [2.0]
    assert result["spec_count"] == 1
    assert result["returned_count"] == 1
    assert result["top"][0]["exposure_multiplier"] == 2.0


def test_sweep_qualified_trades_can_run_targeted_filter_only():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "breakout_20d"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-02-03",
            "exit_date": "2025-02-13",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "neutral",
            "signal_tags": ["score_gte_5", "breakout_20d"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=3,
        required_signal_tags=["score_gte_5", "breakout_20d"],
        market_levels=["favorable", "neutral"],
    )

    assert result["spec_count"] == 1
    assert result["returned_count"] == 1
    assert result["top"][0]["required_signal_tags"] == ["breakout_20d", "score_gte_5"]
    assert result["top"][0]["market_levels"] == ["favorable", "neutral"]


def test_sweep_qualified_trades_can_exclude_risk_tags():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-10",
            "return_pct": -5,
            "max_adverse_pct": -6,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "breakout_20d"],
            "entry_executability": {"executable": True, "gap_pct": -1.5, "intraday_range_pct": 4},
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-03",
            "exit_date": "2025-01-11",
            "return_pct": 8,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "breakout_20d"],
            "entry_executability": {"executable": True, "gap_pct": 0.2, "intraday_range_pct": 3},
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=1,
        min_trades=1,
        max_filter_size=0,
        required_signal_tags=["score_gte_5", "breakout_20d"],
        excluded_signal_tags=["entry_gap_lt_neg1"],
        market_levels=["favorable"],
    )

    best = result["top"][0]
    assert result["excluded_signal_tags"] == ["entry_gap_lt_neg1"]
    assert best["excluded_signal_tags"] == ["entry_gap_lt_neg1"]
    assert best["selected_trade_count"] == 1
    assert best["trade_win_rate_pct"] == 100.0


def test_trade_sweep_tags_derives_proxy_market_tags_from_cached_context():
    tags = _trade_sweep_tags(
        {
            "signal_tags": ["score_gte_5"],
            "relative_strength": {
                "proxy_return_20d_avg_pct": -1.2,
                "proxy_return_20d_max_pct": 2.5,
                "proxy_return_60d_avg_pct": 6.4,
                "proxy_return_60d_max_pct": 8.1,
            },
        }
    )

    assert "score_gte_5" in tags
    assert "proxy20_avg_lt_0" in tags
    assert "proxy60_avg_gte_5" in tags
    assert "proxy60_max_gte_5" in tags
    assert "proxy_market_fragile" in tags


def test_trade_sweep_tags_derives_announcement_tags_from_cached_context():
    tags = _trade_sweep_tags(
        {
            "signal_tags": ["score_gte_5"],
            "announcement_context": {
                "level": "high_risk",
                "score": -18,
                "allow_recommendation": False,
                "announcement_count": 3,
                "negative_count": 2,
                "positive_count": 0,
                "event_counts": {"regulatory_penalty": 1},
            },
        }
    )

    assert "score_gte_5" in tags
    assert "announcement_level_high_risk" in tags
    assert "announcement_blocked" in tags
    assert "announcement_count_gte_1" in tags
    assert "announcement_negative_gte_2" in tags
    assert "announcement_score_lte_neg14" in tags
    assert "announcement_event_regulatory_penalty" in tags


def test_sweep_qualified_trades_applies_financing_and_execution_costs():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "exit_date": "2025-01-12",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-02-03",
            "exit_date": "2025-02-13",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5", "balanced_rsi"],
        },
    ]

    gross = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=0,
        exposure_multipliers=[2],
    )
    net = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=1,
        min_trades=2,
        max_filter_size=0,
        exposure_multipliers=[2],
        annual_financing_rate_pct=10,
        roundtrip_cost_bps=100,
        slippage_bps=50,
    )

    assert net["top"][0]["annual_financing_rate_pct"] == 10.0
    assert (
        net["top"][0]["portfolio_compounded_return_pct"]
        < gross["top"][0]["portfolio_compounded_return_pct"]
    )


def test_sweep_qualified_trades_supports_slot_exit_capital_model():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-10",
            "holding_days": 5,
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-10",
            "holding_days": 5,
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
    ]

    signal_day = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=2,
        max_active_positions=2,
        min_trades=2,
        max_filter_size=0,
    )
    slot_exit = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=2,
        max_active_positions=2,
        min_trades=2,
        max_filter_size=0,
        target_one_year_return_pct=15,
        capital_model="slot-exit",
    )

    assert slot_exit["capital_model"] == "slot-exit"
    assert slot_exit["top"][0]["capital_model"] == "slot-exit"
    assert (
        slot_exit["top"][0]["portfolio_compounded_return_pct"]
        > signal_day["top"][0]["portfolio_compounded_return_pct"]
    )
    assert slot_exit["top"][0]["target_all_pass"] is True


def test_sweep_qualified_trades_supports_slot_daily_drawdown_model():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-10",
            "holding_days": 5,
            "return_pct": 20,
            "max_adverse_pct": -10,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {"date": "2025-01-03", "close_return_pct": 0, "low_return_pct": -10},
                {"date": "2025-01-10", "close_return_pct": 20, "low_return_pct": 20},
            ],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-10",
            "holding_days": 5,
            "return_pct": 20,
            "max_adverse_pct": -10,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {"date": "2025-01-03", "close_return_pct": 0, "low_return_pct": -10},
                {"date": "2025-01-10", "close_return_pct": 20, "low_return_pct": 20},
            ],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=10,
        top_n=2,
        max_active_positions=2,
        min_trades=2,
        max_filter_size=0,
        target_one_year_return_pct=15,
        capital_model="slot-daily",
    )

    best = result["top"][0]
    assert result["capital_model"] == "slot-daily"
    assert best["capital_model"] == "slot-daily"
    assert best["portfolio_compounded_return_pct"] == 20.0
    assert best["portfolio_max_drawdown_pct"] == -10.0
    assert best["target_win_drawdown_pass"] is True


def test_holding_calendar_gap_tags_classify_long_holiday_paths():
    long_gap_tags = _holding_calendar_gap_tags(
        {
            "mark_to_market_path": [
                {"date": "2025-10-09", "close_return_pct": 1},
                {"date": "2025-09-25", "close_return_pct": 0},
            ]
        }
    )
    short_gap_tags = _holding_calendar_gap_tags(
        {
            "mark_to_market_path": [
                {"date": "2025-01-03", "close_return_pct": 0},
                {"date": "2025-01-06", "close_return_pct": 1},
            ]
        }
    )

    assert {
        "holding_calendar_gap_gte_5",
        "holding_calendar_gap_gte_7",
        "holding_calendar_gap_gte_10",
    } <= long_gap_tags
    assert "holding_calendar_gap_lte_6" not in long_gap_tags
    assert {"holding_calendar_gap_lte_4", "holding_calendar_gap_lte_6"} <= short_gap_tags
    assert "holding_calendar_gap_gte_5" not in short_gap_tags


def test_truncate_trade_before_calendar_gap_exits_before_long_closure():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-09-25",
        "entry_date": "2025-09-26",
        "exit_date": "2025-10-09",
        "return_pct": 12,
        "max_adverse_pct": -4,
        "mark_to_market_path": [
            {"date": "2025-09-26", "close_return_pct": 2, "low_return_pct": -1},
            {"date": "2025-09-30", "close_return_pct": 5, "low_return_pct": 1},
            {"date": "2025-10-09", "close_return_pct": 12, "low_return_pct": -4},
        ],
    }

    adjusted = _truncate_trade_before_calendar_gap(trade, min_gap_days=7)

    assert adjusted["exit_date"] == "2025-09-30"
    assert adjusted["return_pct"] == 5.0
    assert adjusted["max_adverse_pct"] == -1.0
    assert adjusted["holding_days"] == 2
    assert adjusted["exit_reason"] == "pre_calendar_gap_exit"
    assert len(adjusted["mark_to_market_path"]) == 2


def test_sweep_qualified_trades_applies_pre_calendar_gap_exit():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-09-25",
            "entry_date": "2025-09-26",
            "exit_date": "2025-10-09",
            "return_pct": 20,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {"date": "2025-09-26", "close_return_pct": 1, "low_return_pct": -1},
                {"date": "2025-09-30", "close_return_pct": 4, "low_return_pct": 0},
                {"date": "2025-10-09", "close_return_pct": 20, "low_return_pct": 2},
            ],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-10-10",
            "entry_date": "2025-10-13",
            "exit_date": "2025-10-17",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {"date": "2025-10-13", "close_return_pct": 0, "low_return_pct": -1},
                {"date": "2025-10-17", "close_return_pct": 10, "low_return_pct": 1},
            ],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=1,
        min_trades=2,
        max_filter_size=0,
        max_active_positions=1,
        capital_model="slot-daily",
        pre_exit_calendar_gap_days=7,
    )

    best = result["top"][0]
    assert result["pre_exit_calendar_gap_days"] == 7
    assert best["pre_exit_calendar_gap_days"] == 7
    assert best["trade_avg_return_pct"] == 7.0


def test_prior_high_trailing_stop_uses_only_completed_prior_highs():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-10",
        "return_pct": -5,
        "max_adverse_pct": -6,
        "mark_to_market_path": [
            {
                "date": "2025-01-03",
                "open_return_pct": 0,
                "high_return_pct": 6,
                "close_return_pct": 4,
                "low_return_pct": 0,
            },
            {
                "date": "2025-01-06",
                "open_return_pct": 5,
                "high_return_pct": 8,
                "close_return_pct": -5,
                "low_return_pct": -6,
            },
        ],
    }

    adjusted = _apply_prior_high_trailing_stop(trade, trailing_stop_pct=5, activation_pct=0)

    assert adjusted["exit_date"] == "2025-01-06"
    assert adjusted["exit_reason"] == "prior_high_trailing_stop"
    assert adjusted["return_pct"] == 0.7
    assert adjusted["max_adverse_pct"] == 0.0
    assert adjusted["mark_to_market_path"][-1]["high_return_pct"] == 5.0


def test_prior_high_trailing_stop_exits_at_open_when_gap_crosses_stop():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-10",
        "return_pct": -5,
        "max_adverse_pct": -6,
        "mark_to_market_path": [
            {
                "date": "2025-01-03",
                "open_return_pct": 0,
                "high_return_pct": 10,
                "close_return_pct": 8,
                "low_return_pct": 0,
            },
            {
                "date": "2025-01-06",
                "open_return_pct": 2,
                "high_return_pct": 6,
                "close_return_pct": -5,
                "low_return_pct": -6,
            },
        ],
    }

    adjusted = _apply_prior_high_trailing_stop(trade, trailing_stop_pct=5, activation_pct=0)

    assert adjusted["exit_date"] == "2025-01-06"
    assert adjusted["return_pct"] == 2.0
    assert adjusted["max_adverse_pct"] == 0.0


def test_partial_profit_lock_uses_prior_completed_high_and_next_open():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-07",
        "return_pct": 0,
        "max_adverse_pct": -1,
        "mark_to_market_path": [
            {
                "date": "2025-01-03",
                "open_return_pct": 0,
                "high_return_pct": 10,
                "low_return_pct": -1,
                "close_return_pct": 8,
            },
            {
                "date": "2025-01-06",
                "open_return_pct": 6,
                "high_return_pct": 12,
                "low_return_pct": 4,
                "close_return_pct": 5,
            },
            {
                "date": "2025-01-07",
                "open_return_pct": 3,
                "high_return_pct": 4,
                "low_return_pct": -2,
                "close_return_pct": 0,
            },
        ],
    }

    adjusted = _apply_partial_profit_lock(trade, activation_pct=8, fraction=0.5)

    assert adjusted["exit_reason"] == "partial_profit_lock"
    assert adjusted["exit_date"] == "2025-01-07"
    assert adjusted["return_pct"] == 3.0
    assert adjusted["mark_to_market_path"][1]["open_return_pct"] == 6.0
    assert adjusted["mark_to_market_path"][1]["close_return_pct"] == 5.5
    assert adjusted["mark_to_market_path"][2]["low_return_pct"] == 2.0
    assert adjusted["max_adverse_pct"] == -1.0


def test_partial_profit_lock_does_not_use_same_day_high_to_trigger():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-06",
        "return_pct": 5,
        "max_adverse_pct": -1,
        "mark_to_market_path": [
            {
                "date": "2025-01-03",
                "open_return_pct": 0,
                "high_return_pct": 7,
                "low_return_pct": -1,
                "close_return_pct": 4,
            },
            {
                "date": "2025-01-06",
                "open_return_pct": 3,
                "high_return_pct": 12,
                "low_return_pct": 2,
                "close_return_pct": 5,
            },
        ],
    }

    adjusted = _apply_partial_profit_lock(trade, activation_pct=8, fraction=0.5)

    assert adjusted is trade


def test_partial_profit_lock_full_fraction_exits_at_next_open():
    trade = {
        "symbol": "600001",
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-07",
        "return_pct": 0,
        "max_adverse_pct": -1,
        "mark_to_market_path": [
            {
                "date": "2025-01-03",
                "open_return_pct": 0,
                "high_return_pct": 10,
                "low_return_pct": -1,
                "close_return_pct": 8,
            },
            {
                "date": "2025-01-06",
                "open_return_pct": 6,
                "high_return_pct": 12,
                "low_return_pct": 4,
                "close_return_pct": 5,
            },
            {
                "date": "2025-01-07",
                "open_return_pct": 3,
                "high_return_pct": 4,
                "low_return_pct": -2,
                "close_return_pct": 0,
            },
        ],
    }

    adjusted = _apply_partial_profit_lock(trade, activation_pct=8, fraction=1.0)

    assert adjusted["exit_reason"] == "profit_lock_exit"
    assert adjusted["exit_date"] == "2025-01-06"
    assert adjusted["return_pct"] == 6.0
    assert len(adjusted["mark_to_market_path"]) == 2


def test_sweep_qualified_trades_reports_partial_profit_parameters():
    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-02",
            "entry_date": "2025-01-03",
            "exit_date": "2025-01-07",
            "return_pct": 0,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {
                    "date": "2025-01-03",
                    "open_return_pct": 0,
                    "high_return_pct": 10,
                    "low_return_pct": -1,
                    "close_return_pct": 8,
                },
                {
                    "date": "2025-01-06",
                    "open_return_pct": 6,
                    "high_return_pct": 8,
                    "low_return_pct": 4,
                    "close_return_pct": 5,
                },
                {
                    "date": "2025-01-07",
                    "open_return_pct": 3,
                    "high_return_pct": 4,
                    "low_return_pct": -2,
                    "close_return_pct": 0,
                },
            ],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-08",
            "entry_date": "2025-01-09",
            "exit_date": "2025-01-13",
            "return_pct": 2,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
            "mark_to_market_path": [
                {
                    "date": "2025-01-09",
                    "open_return_pct": 0,
                    "high_return_pct": 9,
                    "low_return_pct": -1,
                    "close_return_pct": 6,
                },
                {
                    "date": "2025-01-10",
                    "open_return_pct": 4,
                    "high_return_pct": 5,
                    "low_return_pct": 1,
                    "close_return_pct": 2,
                },
            ],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=1,
        min_trades=2,
        max_filter_size=0,
        required_signal_tags=["score_gte_5"],
        market_levels=["favorable"],
        partial_profit_activation_pct=8,
        partial_profit_fraction=0.5,
    )

    assert result["partial_profit_activation_pct"] == 8.0
    assert result["partial_profit_fraction"] == 0.5
    assert result["top"][0]["partial_profit_activation_pct"] == 8.0
    assert result["top"][0]["partial_profit_fraction"] == 0.5


def test_sweep_qualified_trades_can_apply_correlation_budget(tmp_path):
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    dates = [
        "2025-01-02",
        "2025-01-03",
        "2025-01-06",
        "2025-01-07",
        "2025-01-08",
        "2025-01-09",
    ]

    def write_history(symbol, closes):
        records = [
            {"date": date, "open": close, "high": close, "low": close, "close": close}
            for date, close in zip(dates, closes)
        ]
        path = cache_dir / ("a_%s_620_qfq.json" % symbol)
        path.write_text(
            json.dumps({"source": "test", "records": records}),
            encoding="utf-8",
        )

    write_history("600001", [10, 11, 10.5, 12, 11.7, 13])
    write_history("600002", [20, 22, 21, 24, 23.4, 26])
    write_history("600003", [10, 9.8, 10.4, 10.1, 10.8, 10.2])

    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-08",
            "exit_date": "2025-01-10",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-08",
            "exit_date": "2025-01-10",
            "return_pct": 8,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600003",
            "signal_date": "2025-01-09",
            "exit_date": "2025-01-13",
            "return_pct": 7,
            "max_adverse_pct": -1,
            "rank_score": 8,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=2,
        min_trades=2,
        max_filter_size=0,
        max_active_positions=2,
        required_signal_tags=["score_gte_5"],
        market_levels=["favorable"],
        correlation_threshold=0.95,
        correlation_lookback_days=5,
        correlation_min_periods=3,
        correlation_cache_dir=str(cache_dir),
    )

    best = result["top"][0]
    assert result["correlation_threshold"] == 0.95
    assert best["selected_trade_count"] == 2
    assert best["correlation_skip_count"] == 1
    assert best["trade_avg_return_pct"] == 8.5


def test_sweep_qualified_trades_reuses_correlation_history_cache(monkeypatch):
    dates = [
        "2025-01-02",
        "2025-01-03",
        "2025-01-06",
        "2025-01-07",
        "2025-01-08",
        "2025-01-09",
    ]
    histories = {
        "a_600001_620_qfq.json": [10, 11, 10.5, 12, 11.7, 13],
        "a_600002_620_qfq.json": [20, 22, 21, 24, 23.4, 26],
        "a_600003_620_qfq.json": [10, 9.8, 10.4, 10.1, 10.8, 10.2],
    }
    read_counts = {}

    def fake_read_json(path, default):
        name = Path(path).name
        read_counts[name] = read_counts.get(name, 0) + 1
        closes = histories.get(name)
        if not closes:
            return default
        return {
            "source": "test",
            "records": [
                {"date": date, "open": close, "high": close, "low": close, "close": close}
                for date, close in zip(dates, closes)
            ],
        }

    monkeypatch.setattr(research_sweep, "read_json", fake_read_json)

    trades = [
        {
            "symbol": "600001",
            "signal_date": "2025-01-08",
            "exit_date": "2025-01-10",
            "return_pct": 10,
            "max_adverse_pct": -1,
            "rank_score": 10,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600002",
            "signal_date": "2025-01-08",
            "exit_date": "2025-01-10",
            "return_pct": 8,
            "max_adverse_pct": -1,
            "rank_score": 9,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
        {
            "symbol": "600003",
            "signal_date": "2025-01-09",
            "exit_date": "2025-01-13",
            "return_pct": 7,
            "max_adverse_pct": -1,
            "rank_score": 8,
            "market_level": "favorable",
            "signal_tags": ["score_gte_5"],
        },
    ]

    result = sweep_qualified_trades(
        trades,
        hold_days=5,
        top_n=2,
        min_trades=2,
        max_filter_size=0,
        max_active_positions=2,
        correlation_threshold=0.95,
        correlation_lookback_days=5,
        correlation_min_periods=3,
        correlation_cache_dir="cache",
    )

    assert result["returned_count"] == 4
    assert sum(read_counts.values()) == 3
    assert read_counts == {
        "a_600001_620_qfq.json": 1,
        "a_600002_620_qfq.json": 1,
        "a_600003_620_qfq.json": 1,
    }


def test_correlation_as_of_reuses_pair_value_cache(monkeypatch):
    calls = []
    history = {
        "600001": [
            ("2025-01-03", 0.10),
            ("2025-01-06", -0.05),
            ("2025-01-07", 0.14),
            ("2025-01-08", -0.03),
        ],
        "600002": [
            ("2025-01-03", 0.11),
            ("2025-01-06", -0.04),
            ("2025-01-07", 0.13),
            ("2025-01-08", -0.02),
        ],
    }

    def fake_history_returns(symbol, cache_dir, history_lookback_days, returns_cache):
        calls.append(symbol)
        return history[symbol]

    monkeypatch.setattr(research_sweep, "_history_returns_for_symbol", fake_history_returns)

    returns_cache = {}
    correlation_cache = {}
    first = research_sweep._correlation_as_of(
        "600001",
        "600002",
        "2025-01-08",
        "cache",
        5,
        3,
        620,
        returns_cache,
        correlation_cache,
    )
    second = research_sweep._correlation_as_of(
        "600002",
        "600001",
        "2025-01-08",
        "cache",
        5,
        3,
        620,
        returns_cache,
        correlation_cache,
    )

    assert first == second
    assert first > 0.95
    assert calls == ["600001", "600002"]


def test_latest_window_portfolio_stats_matches_full_rolling_latest():
    equity_points = [
        {"signal_date": "2024-01-02", "equity": 1.02, "count": 1},
        {"signal_date": "2024-08-01", "equity": 1.10, "count": 2},
        {"signal_date": "2025-01-15", "equity": 1.21, "count": 3},
        {"signal_date": "2025-04-01", "equity": 1.15, "count": 1},
    ]

    latest = _latest_window_portfolio_stats(equity_points, days=365)
    full_latest = _window_portfolio_stats(equity_points, days=365)["latest"]

    assert latest == full_latest


def test_latest_window_portfolio_stats_uses_close_equity_peak_for_drawdown():
    equity_points = [
        {"signal_date": "2025-01-02", "equity": 1.20, "drawdown_equity": 1.00, "count": 1},
        {"signal_date": "2025-01-03", "equity": 1.10, "drawdown_equity": 1.05, "count": 1},
    ]

    latest = _latest_window_portfolio_stats(equity_points, days=365)
    full_latest = _window_portfolio_stats(equity_points, days=365)["latest"]

    assert latest["max_drawdown_pct"] == -12.5
    assert latest == full_latest


def test_compact_research_sweep_payload_includes_historical_context():
    compact = _compact_research_sweep_payload(
        {
            "summary": {
                "start_date": "2024-07-05",
                "candidate_mode": "historical_daily_prefilter",
                "max_universe_symbols": 300,
                "daily_prefilter_max_deep": 80,
                "historical_candidate_days": 480,
                "raw_qualified_trade_count": 10,
                "stop_loss_pct": 5,
                "take_profit_pct": 12,
                "trailing_stop_pct": 6,
            }
        },
        {
            "qualified_trade_count": 10,
            "available_tag_count": 3,
            "spec_count": 4,
            "returned_count": 1,
            "target_win_drawdown_pass_count": 0,
            "target_all_pass_count": 0,
            "target_win_rate_pct": 70,
            "target_drawdown_pct": 5,
            "target_one_year_return_pct": 200,
            "min_trades": 2,
            "top": [
                {
                    "label": "balanced_rsi|market_favorable",
                    "selected_trade_count": 2,
                    "trade_win_rate_pct": 100,
                    "target_all_pass": False,
                }
            ],
        },
        output_limit=1,
    )

    assert compact["source_summary"]["candidate_mode"] == "historical_daily_prefilter"
    assert compact["source_summary"]["max_universe_symbols"] == 300
    assert compact["source_summary"]["historical_candidate_days"] == 480
    assert compact["source_summary"]["stop_loss_pct"] == 5
    assert compact["source_summary"]["take_profit_pct"] == 12
    assert compact["source_summary"]["trailing_stop_pct"] == 6
    assert compact["sweep_summary"]["target_one_year_return_pct"] == 200
    assert compact["sweep_summary"]["target_all_pass_count"] == 0
    assert compact["top"][0]["label"] == "balanced_rsi|market_favorable"


def test_parse_hold_days_list_deduplicates_positive_days():
    assert _parse_hold_days_list("3, 5, bad, 5, 10, 0") == [3, 5, 10]
    assert _parse_hold_days_list("") == [3, 5, 7, 10]


def test_qualified_trades_payload_round_trip(tmp_path):
    output_path = tmp_path / "qualified.json"
    payload = {
        "summary": {"start_date": "2024-07-06"},
        "qualified_trades": [{"symbol": "600001", "signal_date": "2025-01-02"}],
    }

    export = _write_qualified_trades_payload(str(output_path), payload)
    loaded = _load_qualified_trades_payload(str(output_path))

    assert export["qualified_trade_count"] == 1
    assert loaded == payload


def test_compact_hold_sweep_result_keeps_hold_day_and_best_rows():
    result = _compact_hold_sweep_result(
        5,
        {
            "summary": {
                "hold_days": 5,
                "selected_trade_count": 54,
                "portfolio_max_drawdown_pct": -2.21,
            }
        },
        {
            "target_one_year_return_pct": 200,
            "diagnostics": {
                "best_target_win_drawdown": {
                    "rolling_1y_latest_return_pct": 96.82,
                }
            },
            "top": [
                {
                    "label": "best",
                    "rolling_1y_latest_return_pct": 96.82,
                }
            ],
        },
        output_limit=1,
    )

    assert result["hold_days"] == 5
    assert result["source_summary"]["selected_trade_count"] == 54
    assert (
        result["diagnostics"]["best_target_win_drawdown"]["rolling_1y_latest_return_pct"] == 96.82
    )
    assert result["top"][0]["label"] == "best"
