from __future__ import annotations

import pandas as pd

from app.factor_v3_path_a_protocol_signal import (
    bounce_masks,
    build_signal_trades,
    classic_masks,
    entry_masks,
    gap_masks,
    shape_masks,
    signal_masks,
)
from app.factor_v3_path_a_protocol_signal_specs import (
    BOLL_RECLAIM_TAG,
    BREAKOUT_60D_TAG,
    DN2_BOUNCE_TAG,
    DN3_BOUNCE_TAG,
    ENGULF_TAG,
    GAP_DELAY_TAG,
    GAP_OPEN_TAG,
    GAP_TRUE_TAG,
    HAMMER_TAG,
    HOLD10_TAG,
    HOLD5_TAG,
    INSIDE_UP_TAG,
    KDJ_OVERSOLD_TAG,
    LIMIT_FOLLOW_TAG,
    LO20_BOUNCE_TAG,
    MA10_RECLAIM_TAG,
    MA60_RECLAIM_TAG,
    MACD_GOLD_TAG,
    NR7_UP_TAG,
    PULLBACK_TAG,
    RECLAIM_TAG,
    TIGHT5_UP_TAG,
    assert_signal_variants_obey_protocol,
    iter_protocol_signal_bounce_variants,
    iter_protocol_signal_classic_variants,
    iter_protocol_signal_entry_variants,
    iter_protocol_signal_gap_variants,
    iter_protocol_signal_flow_variants,
    iter_protocol_signal_industry_variants,
    iter_protocol_signal_turn_variants,
    iter_protocol_signal_value_variants,
    iter_protocol_signal_hold_variants,
    iter_protocol_signal_shape_variants,
    iter_protocol_signal_variants,
)
from app.indicators import add_indicators
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
    bounce_ids = [row["candidate_id"] for row in iter_protocol_signal_bounce_variants()]
    assert bounce_ids == [
        "bounce_dn2",
        "bounce_dn3",
        "bounce_dn2_negext",
        "bounce_inside",
        "bounce_tight5",
        "bounce_dn2_liq",
    ]
    assert set(bounce_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_bounce_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert PULLBACK_TAG not in required
        assert required & {DN2_BOUNCE_TAG, DN3_BOUNCE_TAG, INSIDE_UP_TAG, TIGHT5_UP_TAG}
    shape_ids = [row["candidate_id"] for row in iter_protocol_signal_shape_variants()]
    assert shape_ids == [
        "shape_engulf",
        "shape_engulf_negext",
        "shape_hammer",
        "shape_ma10",
        "shape_lo20",
        "shape_nr7",
    ]
    assert set(shape_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_shape_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert PULLBACK_TAG not in required
        assert DN2_BOUNCE_TAG not in required
        assert required & {
            ENGULF_TAG,
            HAMMER_TAG,
            MA10_RECLAIM_TAG,
            LO20_BOUNCE_TAG,
            NR7_UP_TAG,
        }
    classic_ids = [row["candidate_id"] for row in iter_protocol_signal_classic_variants()]
    assert classic_ids == [
        "classic_macd",
        "classic_macd_negext",
        "classic_kdj",
        "classic_kdj_negext",
        "classic_boll",
        "classic_boll_negext",
    ]
    assert set(classic_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_classic_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert PULLBACK_TAG not in required
        assert DN2_BOUNCE_TAG not in required
        assert required & {MACD_GOLD_TAG, KDJ_OVERSOLD_TAG, BOLL_RECLAIM_TAG}
    gap_ids = [row["candidate_id"] for row in iter_protocol_signal_gap_variants()]
    assert gap_ids == [
        "gap_true",
        "gap_true_negext",
        "gap_open",
        "gap_open_negext",
        "gap_delay",
        "gap_delay_negext",
    ]
    assert set(gap_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    for row in iter_protocol_signal_gap_variants():
        required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
        assert "breakout_20d" not in required
        assert PULLBACK_TAG not in required
        assert DN2_BOUNCE_TAG not in required
        assert required & {GAP_TRUE_TAG, GAP_OPEN_TAG, GAP_DELAY_TAG}
    value_ids = [row["candidate_id"] for row in iter_protocol_signal_value_variants()]
    assert value_ids == [
        "val_bounce_pb",
        "val_bounce_pe",
        "val_bounce_unprof_pb",
        "val_bounce_small",
        "val_reclaim_pb",
        "val_gap_pb",
    ]
    assert set(value_ids).isdisjoint(CONTAMINATED_CANDIDATE_IDS)
    assert [row["candidate_id"] for row in iter_protocol_signal_turn_variants()] == [
        "turn_x2",
        "turn_x2_negext",
        "turn_cs90",
        "turn_cs90_negext",
    ]
    assert [row["candidate_id"] for row in iter_protocol_signal_flow_variants()] == [
        "flow_in",
        "flow_in_negext",
    ]
    assert [row["candidate_id"] for row in iter_protocol_signal_industry_variants()] == [
        "ind_lead",
        "ind_lead_negext",
        "ind_enter",
        "ind_enter_negext",
    ]


def test_bounce_masks_fire_two_day_down_then_up() -> None:
    closes = [80.0] * 40 + [100.0] * 20 + [98.0, 96.0, 97.5] + [97.5] * 6
    highs = [value + 0.3 for value in closes]
    lows = [value - 0.3 for value in closes]
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": closes,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )
    masks = bounce_masks(frame)
    assert bool(masks.at[62, "dn2"]) is True
    assert bool(masks.at[62, "dn3"]) is False
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[62, "date"]),
        end_date=str(frame.at[62, "date"]),
        book="bounce",
    )
    assert trades
    assert DN2_BOUNCE_TAG in trades[0]["signal_tags"]
    assert RECLAIM_TAG not in trades[0]["signal_tags"]
    assert PULLBACK_TAG not in trades[0]["signal_tags"]


def test_shape_masks_fire_bullish_engulfing() -> None:
    closes = [80.0] * 40 + [100.0] * 19 + [99.0, 101.4] + [101.4] * 6
    opens = [80.0] * 40 + [100.0] * 19 + [100.5, 98.6] + [101.4] * 6
    highs = [max(o, c) + 0.1 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.1 for o, c in zip(opens, closes)]
    for index in range(40, 59):
        highs[index] = 104.0
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )
    masks = shape_masks(frame)
    assert bool(masks.at[60, "engulf"]) is True
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[60, "date"]),
        end_date=str(frame.at[60, "date"]),
        book="shape",
    )
    assert trades
    assert ENGULF_TAG in trades[0]["signal_tags"]
    assert RECLAIM_TAG not in trades[0]["signal_tags"]
    assert DN2_BOUNCE_TAG not in trades[0]["signal_tags"]


def _classic_recovery_frame() -> pd.DataFrame:
    closes = (
        [80.0 + index * 0.4 for index in range(50)]
        + [100.0] * 15
        + [99.0, 98.0, 97.0, 96.5, 97.5, 99.0, 101.0, 102.0]
        + [102.0] * 10
        + [100.0, 96.0, 90.0, 84.0, 80.0]
        + [81.5, 83.0, 85.0, 87.0, 89.0, 91.0, 93.0]
        + [93.0] * 8
    )
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    opens = [closes[0]] + closes[:-1]
    highs = [max(o, c) + 0.4 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.4 for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )


def test_classic_masks_match_textbook_events() -> None:
    frame = _classic_recovery_frame()
    work = add_indicators(frame)
    masks = classic_masks(frame)
    close = work["close"]
    prior_high20 = work["high"].shift(1).rolling(20, min_periods=20).max()
    uptrend = work["ma60"].notna() & (work["ma20"] > work["ma60"])
    not_breakout = close <= prior_high20
    macd_expected = (
        uptrend
        & (work["macd"].shift(1) <= work["macd_signal"].shift(1))
        & (work["macd"] > work["macd_signal"])
    ).fillna(False)
    kdj_expected = (
        work["ma60"].notna()
        & (work["kdj_k"].shift(1) <= work["kdj_d"].shift(1))
        & (work["kdj_k"] > work["kdj_d"])
        & ((work["kdj_j"].shift(1) <= 20.0) | (work["kdj_k"].shift(1) <= 20.0))
    ).fillna(False)
    boll_expected = (
        work["ma60"].notna()
        & (close.shift(1) < work["boll_lower"].shift(1))
        & (close >= work["boll_lower"])
        & not_breakout
    ).fillna(False)
    assert masks["macd_cross"].astype(bool).tolist() == macd_expected.astype(bool).tolist()
    assert masks["kdj_cross"].astype(bool).tolist() == kdj_expected.astype(bool).tolist()
    assert masks["boll_reclaim"].astype(bool).tolist() == boll_expected.astype(bool).tolist()
    assert bool(masks["macd_cross"].any()) is True
    assert bool(masks["kdj_cross"].any()) is True
    assert bool(masks["boll_reclaim"].any()) is True
    fire_idx = int(masks.index[masks["fire"]][0])
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[fire_idx, "date"]),
        end_date=str(frame.at[fire_idx, "date"]),
        book="classic",
    )
    assert trades
    tags = set(trades[0]["signal_tags"])
    assert tags & {MACD_GOLD_TAG, KDJ_OVERSOLD_TAG, BOLL_RECLAIM_TAG}
    assert RECLAIM_TAG not in tags
    assert DN2_BOUNCE_TAG not in tags
    assert ENGULF_TAG not in tags


def _gap_event_frame() -> pd.DataFrame:
    n = 70
    closes = [80.0 + index * 0.3 for index in range(n)]
    opens = [closes[0]] + closes[:-1]
    highs = [104.0] * n
    lows = [value - 0.3 for value in closes]
    # 70: true gap fill. prior low ~ 100.4, prior close ~ 100.7
    opens.append(99.5)
    closes.append(100.6)
    highs.append(100.8)
    lows.append(99.3)
    # 71-73 quiet
    for _ in range(3):
        opens.append(100.6)
        closes.append(100.6)
        highs.append(100.8)
        lows.append(100.3)
    # 74 origin, 75 unfilled down gap, 76 next-day fill
    opens.extend([100.6, 99.0, 98.9])
    closes.extend([100.6, 98.8, 100.5])
    highs.extend([100.8, 99.2, 100.6])
    lows.extend([100.3, 98.5, 98.7])
    # 77-79 quiet with a deep prior low so 80 is not a true gap
    for _ in range(2):
        opens.append(100.5)
        closes.append(100.5)
        highs.append(100.7)
        lows.append(100.2)
    opens.append(100.5)
    closes.append(100.5)
    highs.append(100.7)
    lows.append(96.0)
    # 80: 3% low-open reclaim, open still above prior low
    opens.append(96.8)
    closes.append(100.6)
    highs.append(100.7)
    lows.append(96.5)
    for _ in range(8):
        opens.append(100.6)
        closes.append(100.6)
        highs.append(100.8)
        lows.append(100.3)
    dates = pd.bdate_range("2023-08-01", periods=len(closes)).strftime("%Y-%m-%d")
    return pd.DataFrame(
        {
            "date": dates,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1_000_000.0] * len(closes),
            "amount": [200_000_000.0] * len(closes),
        }
    )


def test_gap_masks_fire_three_fill_identities() -> None:
    frame = _gap_event_frame()
    masks = gap_masks(frame)
    assert bool(masks.at[70, "gap_true"]) is True
    assert bool(masks.at[76, "gap_delay"]) is True
    assert bool(masks.at[80, "gap_open"]) is True
    assert bool(masks.at[80, "gap_true"]) is False
    trades = build_signal_trades(
        [({"symbol": "000001", "name": "test"}, frame)],
        start_date=str(frame.at[70, "date"]),
        end_date=str(frame.at[80, "date"]),
        book="gap",
    )
    tags = {tag for trade in trades for tag in trade["signal_tags"]}
    assert GAP_TRUE_TAG in tags
    assert GAP_OPEN_TAG in tags
    assert GAP_DELAY_TAG in tags
    assert RECLAIM_TAG not in tags
    assert DN2_BOUNCE_TAG not in tags
    assert MACD_GOLD_TAG not in tags


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
