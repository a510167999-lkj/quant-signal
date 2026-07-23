import pandas as pd

from app.config import Settings
from app.current_pool_development_replay import (
    SIMPLE_BREAKOUT_SPEC,
    _candidate_trades_from_bars,
)


def test_simple_current_pool_replay_uses_only_breakout_and_basic_stop():
    rows = []
    for index in range(27):
        close = 10.0 if index < 20 else 11.0
        rows.append(
            {
                "date": f"2025-01-{index + 1:02d}",
                "ts_code": "000001.SZ",
                "open": 10.4 if index == 21 else close,
                "high": 11.0 if index >= 20 else 10.0,
                "low": 9.0 if index == 21 else 10.0,
                "close": close,
                "pre_close": 10.0,
                "amount": 100_000_000.0,
                "adj_factor": 1.0,
                "suspended": False,
            }
        )

    trades = _candidate_trades_from_bars(
        pd.DataFrame(rows), {"000001": "测试股"}, Settings()
    )

    assert trades
    first = trades[0]
    assert first["signal_date"] == "2025-01-21"
    assert first["signal_tags"] == ["breakout_20d"]
    assert first["exit_reason"] == "stop_loss"
    assert first["current_universe_bias"] is True
    assert SIMPLE_BREAKOUT_SPEC["exposure_multiplier"] == 1.0
    assert SIMPLE_BREAKOUT_SPEC["capital_model"] == "slot-daily"
