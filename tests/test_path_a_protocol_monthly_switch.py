from __future__ import annotations

from app import factor_v3_path_a_protocol_monthly_switch as switch
from app import factor_v3_path_a_protocol_monthly_switch_specs as specs
from app import research_goal_contract as goal


def test_three_arms_include_cash_bounce_reclaim() -> None:
    ids = [row["arm_id"] for row in specs.iter_arm_pool()]
    assert ids == ["cash", "bounce_dn2_negext", "sig_pull_negext_h5"]
    assert specs.INITIAL_ARM_ID == "cash"
    assert specs.MAX_ABS_MDD_PCT == goal.TARGET_MAX_DRAWDOWN_PCT == 15.0
    assert specs.SELECTION_CRITERION == "max_window_return_among_mdd_le_15_else_cash"
    assert specs.ESTIMATION_WINDOW_TRADING_DAYS == 126


def test_select_live_arm_requires_mdd_gate_then_return() -> None:
    scores = {
        "bounce_dn2_negext": {
            "abs_mdd": 20.0,
            "compounded_return_pct": 40.0,
            "trade_count": 8,
        },
        "sig_pull_negext_h5": {
            "abs_mdd": 10.0,
            "compounded_return_pct": 8.0,
            "trade_count": 6,
        },
    }
    assert switch.select_live_arm(scores) == "sig_pull_negext_h5"
    scores["bounce_dn2_negext"]["abs_mdd"] = 9.0
    assert switch.select_live_arm(scores) == "bounce_dn2_negext"
    empty = {
        "bounce_dn2_negext": {
            "abs_mdd": 20.0,
            "compounded_return_pct": 40.0,
            "trade_count": 8,
        },
        "sig_pull_negext_h5": {
            "abs_mdd": 18.0,
            "compounded_return_pct": 12.0,
            "trade_count": 4,
        },
    }
    assert switch.select_live_arm(empty) == "cash"
    assert (
        switch.select_live_arm(
            {
                "bounce_dn2_negext": {
                    "abs_mdd": 5.0,
                    "compounded_return_pct": 3.0,
                    "trade_count": 0,
                }
            }
        )
        == "cash"
    )


def test_simulation_starts_in_cash_and_can_switch() -> None:
    dates = [f"2024-{month:02d}-05" for month in range(1, 8)]
    bounce = [
        {
            "signal_date": day,
            "symbol": f"b{index}",
            "return_pct": 4.0,
            "max_adverse_pct": -2.0,
            "entry_date": day,
            "exit_date": day,
            "mark_to_market_path": [
                {"date": day, "close_return_pct": 0.0, "low_return_pct": 0.0},
                {"date": day, "close_return_pct": 4.0, "low_return_pct": -1.0},
            ],
        }
        for index, day in enumerate(dates)
    ]
    reclaim = [
        {
            "signal_date": day,
            "symbol": f"r{index}",
            "return_pct": -3.0,
            "max_adverse_pct": -20.0,
            "entry_date": day,
            "exit_date": day,
            "mark_to_market_path": [
                {"date": day, "close_return_pct": 0.0, "low_return_pct": 0.0},
                {"date": day, "close_return_pct": -3.0, "low_return_pct": -20.0},
            ],
        }
        for index, day in enumerate(dates)
    ]
    ledger, periods = switch.simulate_monthly_switch(
        arm_trades_by_id={
            "bounce_dn2_negext": bounce,
            "sig_pull_negext_h5": reclaim,
            "cash": [],
        },
        calendar_dates=dates,
        window_days=3,
        cooldown_days=20,
        initial_arm="cash",
    )
    assert ledger[0]["selected_arm"] == "cash"
    assert ledger[0]["reason"] == "initial_seed_cash"
    assert any(row["selected_arm"] == "bounce_dn2_negext" for row in ledger[1:])
    assert periods
    assert periods[0]["arm_id"] == "cash"


def test_week_decision_points_one_per_iso_week() -> None:
    dates = [
        "2025-01-06",
        "2025-01-08",
        "2025-01-10",
        "2025-01-13",
        "2025-01-17",
    ]
    assert switch.week_decision_points(dates) == ["2025-01-06", "2025-01-13"]


def test_weekly_cadence_uses_week_points() -> None:
    dates = [
        "2024-01-02",
        "2024-01-03",
        "2024-01-08",
        "2024-01-09",
        "2024-01-15",
        "2024-01-16",
        "2024-01-22",
    ]
    bounce = [
        {
            "signal_date": day,
            "symbol": f"b{index}",
            "return_pct": 4.0,
            "max_adverse_pct": -2.0,
            "entry_date": day,
            "exit_date": day,
            "mark_to_market_path": [
                {"date": day, "close_return_pct": 0.0, "low_return_pct": 0.0},
                {"date": day, "close_return_pct": 4.0, "low_return_pct": -1.0},
            ],
        }
        for index, day in enumerate(dates)
    ]
    ledger, _periods = switch.simulate_monthly_switch(
        arm_trades_by_id={
            "bounce_dn2_negext": bounce,
            "sig_pull_negext_h5": [],
            "cash": [],
        },
        calendar_dates=dates,
        window_days=2,
        cooldown_days=5,
        initial_arm="cash",
        cadence="week",
    )
    assert len(ledger) == 4
    assert ledger[0]["selected_arm"] == "cash"
