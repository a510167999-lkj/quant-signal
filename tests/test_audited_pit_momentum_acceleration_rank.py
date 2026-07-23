import math

import pandas as pd

from app import audited_pit_momentum_acceleration_rank as acceleration
from app.audited_pit_development_replay import AuditedPITDevelopmentReplayError


def _trade(symbol):
    return {
        "symbol": symbol,
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-08",
        "exit_reason": "time_exit",
        "holding_days": 5,
        "return_pct": 8.0,
        "max_adverse_pct": -2.0,
        "max_favorable_pct": 10.0,
        "mark_to_market_path": [{"date": "2025-01-03", "close_return_pct": 1.0}],
        "rank_score": 100.0,
        "signal_tags": ["breakout_20d"],
    }


def test_momentum_acceleration_rank_uses_fixed_window_formula():
    trades = [_trade("000001"), _trade("000002"), _trade("000003")]
    features = pd.DataFrame(
        [
            {
                "trade_index": 0,
                "signal_return_20d_pct": 30.0,
                "signal_return_60d_pct": 60.0,
            },
            {
                "trade_index": 1,
                "signal_return_20d_pct": 20.0,
                "signal_return_60d_pct": 15.0,
            },
            {
                "trade_index": 2,
                "signal_return_20d_pct": math.nan,
                "signal_return_60d_pct": 30.0,
            },
        ]
    )

    ranked, baseline, receipt = acceleration._rank_momentum_acceleration(
        trades, features
    )

    assert [trade["symbol"] for trade in ranked] == ["000001", "000002"]
    assert [trade["rank_score"] for trade in ranked] == [10.0, 15.0]
    assert [trade["symbol"] for trade in baseline] == ["000001", "000002"]
    assert [trade["rank_score"] for trade in baseline] == [100.0, 100.0]
    assert all(
        trade["signal_tags"] == ["breakout_20d", "momentum_acceleration_rank"]
        for trade in ranked
    )
    assert receipt["candidate_count"] == 3
    assert receipt["complete_candidate_count"] == 2
    assert receipt["missing_candidate_count"] == 1
    assert len(receipt["receipt_sha256"]) == 64


def test_momentum_acceleration_rank_is_deterministic_and_preserves_exit_paths():
    trades = [_trade("000001"), _trade("000002")]
    features = pd.DataFrame(
        [
            {
                "trade_index": 0,
                "signal_return_20d_pct": 20.0,
                "signal_return_60d_pct": 30.0,
            },
            {
                "trade_index": 1,
                "signal_return_20d_pct": 20.0,
                "signal_return_60d_pct": 30.0,
            },
        ]
    )
    before = acceleration._exit_paths(trades)

    first, baseline, first_receipt = acceleration._rank_momentum_acceleration(
        trades, features
    )
    second, _, second_receipt = acceleration._rank_momentum_acceleration(
        trades, features
    )

    assert first == second
    assert first_receipt == second_receipt
    assert acceleration._exit_paths(first) == acceleration._exit_paths(baseline)
    assert acceleration._exit_paths(first) == before
    assert [trade["rank_score"] for trade in trades] == [100.0, 100.0]


def test_momentum_acceleration_rank_rejects_cardinality_mismatch():
    try:
        acceleration._rank_momentum_acceleration(
            [_trade("000001")],
            pd.DataFrame(
                columns=[
                    "trade_index",
                    "signal_return_20d_pct",
                    "signal_return_60d_pct",
                ]
            ),
        )
    except AuditedPITDevelopmentReplayError:
        pass
    else:
        raise AssertionError("feature cardinality mismatch must fail closed")


def test_fixed_acceleration_sweep_requires_exactly_one_top_row():
    row = {"selected_trade_count": 4}

    assert acceleration._single_fixed_spec_row({"top": [row]}) == row
    for invalid in ({"top": row}, {"top": []}, {"top": [row, row]}):
        try:
            acceleration._single_fixed_spec_row(invalid)
        except AuditedPITDevelopmentReplayError:
            pass
        else:
            raise AssertionError("invalid fixed sweep shape must fail closed")
