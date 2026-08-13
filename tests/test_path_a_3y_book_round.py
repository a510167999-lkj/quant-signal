from __future__ import annotations

from app import factor_v3_path_a_3y_book_round as rnd
from app import research_goal_contract as goal
from app.factor_v3_path_a_3y_book_round_specs import (
    FAILED_OVERLAY_IDS,
    iter_book_round_variants,
    merged_kernel,
)


def _trade(
    symbol: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    return_pct: float,
    *,
    tags: tuple[str, ...] = ("breadth_ma20_gte_60", "breakout_20d"),
    market_level: str = "favorable",
) -> dict:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "return_pct": return_pct,
        "rank_score": float(return_pct),
        "max_adverse_pct": min(return_pct, -1.0),
        "market_level": market_level,
        "signal_tags": list(tags),
        "mark_to_market_path": [
            {"date": entry_date, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {
                "date": exit_date,
                "close_return_pct": return_pct,
                "low_return_pct": min(return_pct, 0.0),
            },
        ],
    }


def test_book_ids_are_disjoint_from_failed_overlay_seven() -> None:
    ids = [row["candidate_id"] for row in iter_book_round_variants()]
    assert len(ids) == 6
    assert len(ids) == len(set(ids))
    assert set(ids).isdisjoint(FAILED_OVERLAY_IDS)
    kernel = merged_kernel(iter_book_round_variants()[0])
    assert kernel["roundtrip_cost_bps"] == 25.0
    assert kernel["exposure_multiplier"] == 1.0


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
    one = (
        {
            "candidate_id": "book_t1_m1",
            "role": "book",
            "kernel": {
                "top_n": 1,
                "max_active_positions": 1,
                "symbol_cooldown_days": 5,
                "market_levels": ("favorable", "neutral"),
                "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
                "excluded_signal_tags": ("price_gap_down",),
            },
            "rationale": "test",
        },
    )
    win = rnd.score_book_round(winners, variants=one)["variants"][0]
    lose = rnd.score_book_round(losers, variants=one)["variants"][0]
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
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 50.0
    assert goal.TARGET_MAX_DRAWDOWN_PCT == 15.0
    assert goal.AUTOMATIC_TRADING_ALLOWED is False
    assert win["promotable"] is False
    assert win["automatic_trading_allowed"] is False
