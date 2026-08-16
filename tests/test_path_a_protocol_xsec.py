from __future__ import annotations

import pandas as pd

from app.factor_v3_path_a_protocol_regime import last_level, simulate_regime_switch
from app.factor_v3_path_a_protocol_signal import flow_masks, industry_masks, turn_masks


def _uptrend_frame(**extra) -> pd.DataFrame:
    n = 70
    close = [80 + i * 0.4 for i in range(n)]
    data = {
        "close": close,
        "high": [200.0] * (n - 1) + [close[-1] + 0.2],
        "low": [value - 0.2 for value in close],
        "open": close,
    }
    data.update(extra)
    return pd.DataFrame(data)


def test_turn_masks_fire_on_two_times_median() -> None:
    frame = _uptrend_frame(
        turnover_rate=[1.0] * 69 + [2.2],
        turnover_cs90=[False] * 69 + [True],
    )
    masks = turn_masks(frame)
    assert bool(masks.iloc[-1]["turn_x2"]) is True
    assert bool(masks.iloc[-1]["turn_cs90"]) is True


def test_flow_masks_fire_on_inflow_cross() -> None:
    frame = _uptrend_frame(net_mf_amount=[-1.0] * 69 + [3.0])
    assert bool(flow_masks(frame).iloc[-1]["flow_in"]) is True


def test_industry_masks_fire_on_top_quintile() -> None:
    frame = _uptrend_frame(industry_rank=[0.4] * 69 + [0.9])
    masks = industry_masks(frame)
    assert bool(masks.iloc[-1]["ind_lead"]) is True
    assert bool(masks.iloc[-1]["ind_enter"]) is True


def test_regime_uses_favorable_only() -> None:
    dates = ["2024-01-02", "2024-01-08", "2024-01-15", "2024-01-22"]
    series = {
        "2024-01-02": "cautious",
        "2024-01-08": "favorable",
        "2024-01-15": "neutral",
    }
    ledger, _periods = simulate_regime_switch(
        bounce_trades=[],
        calendar_dates=dates,
        market_series=series,
        cadence="week",
    )
    assert ledger[0]["selected_arm"] == "cash"
    assert last_level(series, "2024-01-08") == "favorable"
    assert any(row["selected_arm"] == "bounce_dn2_negext" for row in ledger)
    assert any(row["selected_arm"] == "cash" for row in ledger[1:])
