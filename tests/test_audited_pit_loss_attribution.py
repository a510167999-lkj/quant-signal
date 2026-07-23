import pandas as pd

from app import audited_pit_loss_attribution as attribution


def _bars():
    rows = []
    for index in range(22):
        signal_day = index == 20
        close = 11.0 if signal_day else 10.0
        rows.append(
            {
                "date": f"2025-01-{index + 1:02d}",
                "ts_code": "000001.SZ",
                "open": 10.0,
                "high": 11.0 if signal_day else 10.0,
                "low": 10.0,
                "close": close,
                "pre_close": 10.0,
                "amount": 200.0 if signal_day else 100.0,
                "adj_factor": 1.0,
                "suspended": False,
                "membership_name": "历史名称",
            }
        )
    return pd.DataFrame(rows)


def _trade(**overrides):
    trade = {
        "signal_date": "2025-01-21",
        "symbol": "000001",
        "exit_date": "2025-01-27",
        "exit_reason": "time_exit",
        "holding_days": 5,
        "return_pct": 2.0,
        "max_adverse_pct": -1.0,
        "max_favorable_pct": 4.0,
        "entry_executability": {"gap_pct": 1.5},
    }
    trade.update(overrides)
    return trade


def test_annotate_selected_trade_uses_causal_signal_features_and_real_costs():
    annotated = attribution._annotate_selected_trades([_trade()], _bars())

    assert annotated == [
        {
            "signal_date": "2025-01-21",
            "symbol": "000001",
            "exit_date": "2025-01-27",
            "exit_reason": "time_exit",
            "holding_days": 5,
            "gross_return_pct": 2.0,
            "net_return_pct": 1.55,
            "max_adverse_pct": -1.0,
            "max_favorable_pct": 4.0,
            "entry_gap_pct": 1.5,
            "breakout_extension_pct": 10.0,
            "signal_day_return_pct": 10.0,
            "signal_close_location_pct": 100.0,
            "signal_range_pct": 10.0,
            "amount_to_prior20_median": 2.0,
            "signal_return_20d_pct": 10.0,
        }
    ]


def test_build_attribution_keeps_diagnostic_bins_descriptive():
    result = attribution._build_attribution(
        [
            _trade(),
            _trade(
                return_pct=-5.0,
                max_adverse_pct=-6.0,
                max_favorable_pct=3.0,
                exit_reason="stop_loss",
            ),
        ],
        _bars(),
    )

    assert result["selected_trade_count"] == 2
    assert result["all_selected"]["win_rate_pct"] == 50.0
    assert result["all_selected"]["profit_factor"] == 0.28
    assert result["entry_gap_pct"][0]["bucket"] == "0_to_2"
    assert result["breakout_extension_pct"][0]["bucket"] == "gte_5"
    assert result["loser_max_favorable_pct"][0]["bucket"] == "2_to_5"
    assert result["interpretation_guardrails"] == {
        "descriptive_only": True,
        "not_a_strategy_sweep": True,
        "equal_slot_trade_sums_are_not_portfolio_returns": True,
        "final_oos_consumed": False,
    }
