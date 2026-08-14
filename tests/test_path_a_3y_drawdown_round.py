from __future__ import annotations

from app import factor_v3_path_a_3y_drawdown_round as rnd
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_drawdown_round_specs import (
    DRAWDOWN_ROUND_VARIANTS,
    SIGNAL_KERNEL,
    iter_drawdown_round_variants,
)


def _trade(
    symbol: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    return_pct: float,
) -> dict:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "return_pct": return_pct,
        "max_adverse_pct": min(return_pct, -1.0),
        "market_level": "favorable",
        "signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        "mark_to_market_path": [
            {"date": entry_date, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {
                "date": exit_date,
                "close_return_pct": return_pct,
                "low_return_pct": min(return_pct, 0.0),
            },
        ],
    }


def test_variant_list_is_finite_and_keeps_e4_kernel() -> None:
    ids = [row["candidate_id"] for row in iter_drawdown_round_variants()]
    assert ids[0] == "e4_primary"
    assert "p0_loss_streak_3" in ids
    assert len(ids) == 7
    assert len(ids) == len(set(ids))
    assert SIGNAL_KERNEL["market_levels"] == ("favorable", "neutral")
    assert SIGNAL_KERNEL["required_signal_tags"] == (
        "breadth_ma20_gte_60",
        "breakout_20d",
    )


def test_dual_pass_flag_is_computed_by_goal_contract() -> None:
    winners = [
        _trade("000001", "2024-01-02", "2024-01-03", "2024-01-10", 12.0),
        _trade("000002", "2024-02-02", "2024-02-05", "2024-02-12", 12.0),
        _trade("000003", "2024-03-04", "2024-03-05", "2024-03-12", 12.0),
        _trade("000004", "2024-04-02", "2024-04-03", "2024-04-10", 12.0),
        _trade("000005", "2024-05-06", "2024-05-07", "2024-05-14", 12.0),
        _trade("000006", "2024-06-03", "2024-06-04", "2024-06-11", 12.0),
        _trade("000007", "2024-07-02", "2024-07-03", "2024-07-10", 12.0),
        _trade("000008", "2024-08-02", "2024-08-05", "2024-08-12", 12.0),
    ]
    losers = [
        _trade("000001", "2024-01-02", "2024-01-03", "2024-01-10", -20.0),
        _trade("000002", "2024-02-02", "2024-02-05", "2024-02-12", -20.0),
        _trade("000003", "2024-03-04", "2024-03-05", "2024-03-12", -20.0),
    ]
    none_only = (
        {
            "candidate_id": "e4_primary",
            "role": "baseline",
            "overlay": {"kind": "none"},
            "rationale": "test",
        },
    )
    win_report = rnd.score_drawdown_round(winners, variants=none_only)
    lose_report = rnd.score_drawdown_round(losers, variants=none_only)
    win = win_report["variants"][0]
    lose = lose_report["variants"][0]
    assert win["dual_pass_via"] == (
        "research_goal_contract.meets_primary_performance_targets"
    )
    assert win["dual_pass_50_15"] is goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=float(win["full_path_return_pct"]),
        max_drawdown_pct=float(win["full_path_mdd_pct"]),
    )
    assert lose["dual_pass_50_15"] is goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=float(lose["full_path_return_pct"]),
        max_drawdown_pct=float(lose["full_path_mdd_pct"]),
    )
    assert win["dual_pass_50_15"] is True
    assert lose["dual_pass_50_15"] is False
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 30.0
    assert goal.TARGET_MAX_DRAWDOWN_PCT == 15.0
    assert goal.AUTOMATIC_TRADING_ALLOWED is False
    assert DRAWDOWN_ROUND_VARIANTS[0]["candidate_id"] == "e4_primary"
    assert win_report["automatic_trading_allowed"] is False
    assert win["promotable"] is False
