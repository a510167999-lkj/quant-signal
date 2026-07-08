import pandas as pd

import app.backtest as backtest_module
from app.backtest import run_backtest
from app.signal_tags import (
    build_announcement_tags,
    build_candidate_context_tags,
    build_entry_executability_tags,
    build_industry_rotation_tags,
    build_market_breadth_tags,
    build_price_action_tags,
    build_proxy_market_tags,
)
from app.signals import evaluate_signal


def sample_frame(direction="up", periods=180):
    dates = pd.date_range("2025-01-01", periods=periods, freq="B")
    base = []
    price = 100.0
    for index in range(periods):
        if direction == "up":
            price *= 1 + (0.0008 if index < periods - 25 else 0.0025)
        else:
            price *= 1 - (0.0008 if index < periods - 25 else 0.0025)
        base.append(price)
    frame = pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [value * 0.995 for value in base],
            "high": [value * 1.01 for value in base],
            "low": [value * 0.99 for value in base],
            "close": base,
            "volume": [1_000_000 + index * 1_000 for index in range(periods)],
        }
    )
    return frame


def test_evaluate_signal_returns_action_and_levels():
    result = evaluate_signal(sample_frame("up"))

    assert result["action"] in {"BUY", "WATCH", "HOLD", "REDUCE", "SELL"}
    assert "stop_loss" in result["levels"]
    assert result["trade_plans"]["short_term"]["horizon"] == "3-10 个交易日"
    assert result["trade_plans"]["long_term"]["horizon"] == "1-6 个月滚动复核"
    assert result["reasons"]


def test_downtrend_signal_is_not_buy():
    result = evaluate_signal(sample_frame("down"))

    assert result["action"] in {"HOLD", "REDUCE", "SELL"}
    assert result["score"] < 2


def test_backtest_returns_metrics():
    result = run_backtest(sample_frame("up"))

    assert "strategy_return_pct" in result
    assert "max_drawdown_pct" in result
    assert result["end_date"] >= result["start_date"]


def test_backtest_enters_on_next_bar_open(monkeypatch):
    frame = sample_frame("up", periods=120)
    frame["open"] = [200 + index for index in range(len(frame))]
    frame["close"] = [200 + index * 1.002 for index in range(len(frame))]
    frame["high"] = frame[["open", "close"]].max(axis=1) * 1.01
    frame["low"] = frame[["open", "close"]].min(axis=1) * 0.99
    calls = {"count": 0}

    def fake_signal(history):
        calls["count"] += 1
        return {
            "action": "BUY" if calls["count"] == 1 else "HOLD",
            "levels": {"stop_loss": 1},
            "score": 5,
        }

    monkeypatch.setattr(backtest_module, "evaluate_signal", fake_signal)

    result = run_backtest(frame)

    assert result["recent_trades"][0]["side"] == "BUY"
    assert result["recent_trades"][0]["price"] == round(float(frame.iloc[81]["open"]), 4)


def test_candidate_context_tags_are_signal_day_only():
    tags = build_candidate_context_tags(
        {
            "candidate_rank": 18,
            "candidate_amount": 1_200_000_000,
            "candidate_amount_rank_pct": 8,
            "candidate_change_pct": 2.2,
            "candidate_prefilter_score": 13.2,
            "candidate_rank_pct": 22,
            "prior_win_rate_pct": 72,
            "prior_avg_return_pct": 3.5,
            "prior_avg_adverse_pct": 2.8,
            "margin_eligibility": {
                "exchange": "SZSE",
                "financing_underlying": True,
                "financing_eligible": True,
                "short_underlying": True,
                "short_eligible": True,
                "collateral_eligible": True,
                "price_limit": "20%",
            },
        }
    )

    assert "candidate_rank_lte_20" in tags
    assert "candidate_rank_pct_top_25" in tags
    assert "amount_gte_1b" in tags
    assert "amount_rank_pct_top_10" in tags
    assert "candidate_change_0_to_3" in tags
    assert "prefilter_score_gte_13" in tags
    assert "prior_win_gte_70" in tags
    assert "prior_return_gte_3" in tags
    assert "prior_adverse_lte_3" in tags
    assert "margin_financing_eligible" in tags
    assert "margin_financing_underlying" in tags
    assert "margin_short_eligible" in tags
    assert "margin_short_underlying" in tags
    assert "margin_collateral_eligible" in tags
    assert "margin_exchange_szse" in tags
    assert "margin_price_limit_20" in tags


def test_entry_executability_tags_bucket_open_gap_and_range():
    tags = build_entry_executability_tags(
        {
            "executable": True,
            "gap_pct": -0.8,
            "intraday_range_pct": 5.5,
        }
    )

    assert "entry_executable" in tags
    assert "entry_gap_gte_neg1" in tags
    assert "entry_gap_neg1_to_2" in tags
    assert "entry_range_lt_6" in tags
    assert "entry_gap_lt_neg1" not in tags


def test_market_breadth_tags_use_signal_day_cross_section():
    tags = build_market_breadth_tags(
        {
            "sample_count": 150,
            "above_ma20_pct": 64,
            "above_ma60_pct": 52,
            "return_20d_positive_pct": 61,
            "advancing_pct": 48,
            "median_return_20d_pct": 6.5,
            "liquid_300m_pct": 55,
        }
    )

    assert "breadth_sample_gte_100" in tags
    assert "breadth_ma20_gte_60" in tags
    assert "breadth_ma20_gte_70" not in tags
    assert "breadth_ma60_gte_50" in tags
    assert "breadth_ret20_pos_gte_60" in tags
    assert "breadth_advancing_lt_50" in tags
    assert "breadth_median_ret20_gte_5" in tags
    assert "breadth_liquid_300m_gte_50" in tags


def test_proxy_market_tags_use_asof_proxy_returns():
    tags = build_proxy_market_tags(
        {
            "proxy_return_20d_avg_pct": 12.5,
            "proxy_return_20d_max_pct": 16.2,
            "proxy_return_60d_avg_pct": 6.1,
            "proxy_return_60d_max_pct": 12.4,
        }
    )

    assert "proxy20_avg_gte_10" in tags
    assert "proxy20_avg_gte_15" not in tags
    assert "proxy20_max_gte_15" in tags
    assert "proxy60_avg_gte_5" in tags
    assert "proxy60_max_gte_10" in tags
    assert "proxy_market_bullish" in tags
    assert "proxy_market_hot" in tags
    assert "proxy_market_fragile" not in tags


def test_announcement_tags_describe_asof_disclosure_context():
    tags = build_announcement_tags(
        {
            "level": "watch_risk",
            "score": -7,
            "allow_recommendation": True,
            "announcement_count": 6,
            "negative_count": 2,
            "positive_count": 1,
            "event_counts": {
                "pledge_or_reduction": 2,
                "routine_governance": 4,
            },
        }
    )

    assert "announcement_level_watch_risk" in tags
    assert "announcement_allowed" in tags
    assert "announcement_count_gte_5" in tags
    assert "announcement_negative_gte_2" in tags
    assert "announcement_positive" in tags
    assert "announcement_score_neg" in tags
    assert "announcement_event_pledge_or_reduction" in tags
    assert "announcement_event_routine_governance" in tags


def test_price_action_tags_use_signal_day_only_context():
    tags = build_price_action_tags(
        {
            "gap_pct": 1.2,
            "intraday_return_pct": 3.4,
            "range_pct": 6.5,
            "close_position_pct": 92,
            "upper_shadow_pct": 1.1,
            "lower_shadow_pct": 4.2,
            "signal_change_pct": 9.7,
            "limit_threshold_pct": 10,
            "recent_limit_up_count_20d": 1,
            "recent_near_limit_up_count_20d": 2,
            "recent_large_up_count_20d": 3,
        }
    )

    assert "price_gap_up_0_to_2" in tags
    assert "price_intraday_gain_gte_3" in tags
    assert "price_range_4_to_8" in tags
    assert "price_close_near_high" in tags
    assert "price_lower_shadow_gte_3" in tags
    assert "price_signal_limit_up" in tags
    assert "recent_limit_up_20d" in tags
    assert "recent_near_limit_up_20d_gte_2" in tags
    assert "recent_large_up_20d_gte_3" in tags


def test_industry_rotation_tags_describe_board_context():
    tags = build_industry_rotation_tags(
        {
            "sample_count": 60,
            "above_ma20_pct": 66,
            "return_20d_positive_pct": 72,
            "advancing_pct": 58,
            "median_return_20d_pct": 4.5,
            "top_return_20d_pct": 23,
            "return_20d_dispersion_pct": 18,
        }
    )

    assert "industry_sample_gte_50" in tags
    assert "industry_ma20_gte_60" in tags
    assert "industry_ret20_pos_gte_70" in tags
    assert "industry_advancing_gte_50" in tags
    assert "industry_median_ret20_gte_0" in tags
    assert "industry_top_ret20_gte_20" in tags
    assert "industry_dispersion_gte_15" in tags
    assert "industry_rotation_broad" in tags
