from __future__ import annotations

import pandas as pd

from app.factor_v3_path_a_protocol_signal import (
    build_signal_trades,
    entry_masks,
    signal_masks,
)
from app.factor_v3_path_a_protocol_signal_specs import (
    BREAKOUT_60D_TAG,
    HOLD10_TAG,
    HOLD5_TAG,
    LIMIT_FOLLOW_TAG,
    MA60_RECLAIM_TAG,
    PULLBACK_TAG,
    RECLAIM_TAG,
    assert_signal_variants_obey_protocol,
    iter_protocol_signal_entry_variants,
    iter_protocol_signal_hold_variants,
    iter_protocol_signal_variants,
)
from app.factor_v3_path_a_research_protocol import CONTAMINATED_CANDIDATE_IDS


def _reclaim_frame() -> pd.DataFrame:
    closes = [80.0] * 41 + [100.0] * 18 + [95.0, 100.0] + [100.0] * 8
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )


def test_signal_masks_distinguish_pullback_from_breakout() -> None:
    frame = _reclaim_frame()
    masks = signal_masks(frame)
    assert bool(masks.at[60, "pullback"]) is True
    assert bool(masks.at[60, "reclaim"]) is True
    assert bool(masks.at[60, "breakout"]) is False

    broken = frame.copy()
    broken.loc[60, ["open", "high", "close"]] = 101.0
    broken_masks = signal_masks(broken)
    assert bool(broken_masks.at[60, "pullback"]) is False
    assert bool(broken_masks.at[60, "reclaim"]) is True
    assert bool(broken_masks.at[60, "breakout"]) is True


def test_signal_variants_are_new_identity() -> None:
    assert_signal_variants_obey_protocol()
    ids = [row["candidate_id"] for row in iter_protocol_signal_variants()]
    assert ids == [
        "sig_pull",
        "sig_pull_2s",
        "sig_pull_vol",
        "sig_pull_liq100",
        "sig_reclaim",
        "sig_pull_negext",
    ]
    assert set(ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert required & {PULLBACK_TAG, RECLAIM_TAG}
    hold_ids = [row["candidate_id"] for row in iter_protocol_signal_hold_variants()]
    assert hold_ids == [
        "sig_pull_h10",
        "sig_pull_negext_h10",
        "sig_pull_2s_h10",
        "sig_reclaim_h10",
        "sig_pull_h10_liq",
        "sig_pull_negext_h5",
    ]
    assert set(hold_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    entry_ids = [row["candidate_id"] for row in iter_protocol_signal_entry_variants()]
    assert entry_ids == [
        "ent_lim_follow",
        "ent_lim_follow_2s",
        "ent_lim_follow_liq",
        "ent_ma60",
        "ent_bo60",
        "ent_lim_negext",
    ]
    assert set(entry_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_entry_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert required & {LIMIT_FOLLOW_TAG, MA60_RECLAIM_TAG, BREAKOUT_60D_TAG}


def test_build_signal_trades_emits_reclaim_tags() -> None:
    frame = _reclaim_frame()
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[60, "date"]),
        end_date=str(frame.at[60, "date"]),
    )
    assert trades
    first = trades[0]
    assert first["symbol"] == "000001"
    assert PULLBACK_TAG in first["signal_tags"]
    assert RECLAIM_TAG in first["signal_tags"]
    assert "breakout_20d" not in first["signal_tags"]
    assert first["candidate_amount"] == 200_000_000.0
    assert first["mark_to_market_path"]
    assert HOLD5_TAG in first["signal_tags"]
    assert HOLD10_TAG not in first["signal_tags"]


def test_build_signal_trades_can_emit_hold10() -> None:
    closes = [80.0] * 41 + [100.0] * 18 + [95.0, 100.0] + [100.0] * 16
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[60, "date"]),
        end_date=str(frame.at[60, "date"]),
        hold_horizons=(5, 10),
    )
    tags = {tuple(trade["signal_tags"]) for trade in trades}
    flat = {tag for row in tags for tag in row}
    assert HOLD5_TAG in flat
    assert HOLD10_TAG in flat
    assert any(HOLD10_TAG in trade["signal_tags"] for trade in trades)


def test_entry_masks_limit_follow_and_60d_breakout() -> None:
    closes = [10.0] * 60
    closes[-1] = 11.0
    closes.append(11.1)
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )
    masks = entry_masks(frame, "000001")
    assert bool(masks.at[59, "limit_follow"]) is False
    assert bool(masks.at[60, "limit_follow"]) is True
    highs = [10.0] * 60 + [13.0]
    closes60 = [10.0] * 60 + [13.0]
    boom = pd.DataFrame(
        {
            "date": pd.bdate_range("2023-08-01", periods=61).strftime("%Y-%m-%d"),
            "open": closes60,
            "high": highs,
            "low": [value * 0.99 for value in closes60],
            "close": closes60,
            "volume": [1_000_000.0] * 61,
            "amount": [200_000_000.0] * 61,
        }
    )
    boom_masks = entry_masks(boom, "000001")
    assert bool(boom_masks.at[60, "breakout_60d"]) is True


def test_entry_trades_tag_limit_follow() -> None:
    closes = [10.0] * 60 + [11.0, 11.1] + [11.1] * 6
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": closes,
            "low": [value * 0.99 for value in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[61, "date"]),
        end_date=str(frame.at[61, "date"]),
        book="entry",
    )
    assert trades
    assert LIMIT_FOLLOW_TAG in trades[0]["signal_tags"]
    assert RECLAIM_TAG not in trades[0]["signal_tags"]
