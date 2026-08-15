from __future__ import annotations

from app import factor_v3_path_a_protocol_reclaim_freeze as freeze
from app import factor_v3_path_a_protocol_reclaim_oos as oos
from app import factor_v3_path_a_research_protocol as proto
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    HOLD5_TAG,
    PULLBACK_TAG,
)


def _trade(
    symbol: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    return_pct: float,
    *,
    market_level: str = "favorable",
) -> dict:
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "return_pct": return_pct,
        "rank_score": -4.0,
        "relative_strength": {"stock_return_20d_pct": 4.0},
        "max_adverse_pct": min(return_pct, -1.0),
        "market_level": market_level,
        "signal_tags": [PULLBACK_TAG, HOLD5_TAG],
        "mark_to_market_path": [
            {"date": entry_date, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {
                "date": exit_date,
                "close_return_pct": return_pct,
                "low_return_pct": min(return_pct, 0.0),
            },
        ],
    }


def test_independent_oos_starts_after_locked_holdout() -> None:
    assert oos.INDEPENDENT_OOS_START > proto.HOLDOUT_END
    assert oos.INDEPENDENT_OOS_START == "2026-07-04"
    assert proto.partition_for_signal_date(oos.INDEPENDENT_OOS_START) is None
    assert proto.partition_for_signal_date("2026-07-03") == "holdout"
    assert oos.twelve_month_window_evaluable(
        oos.INDEPENDENT_OOS_START, oos.DEFAULT_OOS_END
    ) is False
    assert oos.twelve_month_window_evaluable("2025-07-04", "2026-07-04") is True
    assert oos.TWELVE_MONTH_DUE_DATE == "2027-07-04"


def test_filter_drops_train_and_holdout_dates() -> None:
    trades = [
        _trade("000001", "2025-01-02", "2025-01-03", "2025-01-10", 5.0),
        _trade("000002", "2026-07-03", "2026-07-06", "2026-07-13", 5.0),
        _trade("000003", "2026-07-06", "2026-07-07", "2026-07-14", 5.0),
        _trade("000004", "2026-08-14", "2026-08-17", "2026-08-24", 5.0),
    ]
    kept = oos.filter_independent_oos_trades(
        trades, start=oos.INDEPENDENT_OOS_START, end="2026-08-13"
    )
    assert [row["symbol"] for row in kept] == ["000003"]


def test_short_oos_cannot_be_effective_or_formal() -> None:
    trades = [
        _trade("000001", "2026-07-06", "2026-07-07", "2026-07-14", 4.0),
        _trade("000002", "2026-07-20", "2026-07-21", "2026-07-28", -3.0),
        _trade("000003", "2026-08-03", "2026-08-04", "2026-08-11", 2.0),
    ]
    report = oos.build_reclaim_oos_report(
        trades=trades,
        oos_start=oos.INDEPENDENT_OOS_START,
        oos_end=oos.DEFAULT_OOS_END,
    )
    card = freeze.build_frozen_reclaim_card()
    assert report["candidate_id"] == FROZEN_RECLAIM_CANDIDATE_ID
    assert report["candidate_spec_sha256"] == card["candidate_spec_sha256"]
    assert report["independent_oos"] is True
    assert report["formal_final_oos"] is False
    assert report["formal_final_oos_still_sealed"] is True
    assert report["twelve_month_evaluable"] is False
    assert report["independent_oos_dual_pass_26_15"] is False
    assert report["effective_strategy"] is False
    assert report["promotable"] is False
    assert report["zero_refit"] is True
    assert report["automatic_trading_allowed"] is goal.AUTOMATIC_TRADING_ALLOWED
    assert report["target_rolling_12m_net_return_pct"] == 26.0
    table = oos.format_reclaim_oos_table(report)
    assert "twelve_month_evaluable=False" in table
    assert "twelve_month_due_date=2027-07-04" in table
    assert "effective_strategy=False" in table
    assert "independent_oos_dual_pass_26_15=False" in table
