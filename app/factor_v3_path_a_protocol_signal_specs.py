"""Pre-registered Path-A signal identities. Not tag filters on the old breakout book."""

from __future__ import annotations

from typing import Any

from app.factor_v3_path_a_protocol_baseline_specs import assert_variants_obey_protocol

STAGE_GOAL_ID = "path-a-protocol-signal/v1"
SIGNAL_FAMILY = "path-a-protocol-signal/v1"
PULLBACK_TAG = "trend_pullback_ma20_reclaim"
RECLAIM_TAG = "ma20_trend_reclaim"

# Written down before the new QT is scored. Economic identity:
# buy a 20/60 uptrend on the session that reclaims MA20 after a dip.
# Strict pullback also refuses a same-day 20d high breakout.
PROTOCOL_SIGNAL_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "sig_pull",
        "role": "protocol_signal",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict MA20 reclaim, not a 20d breakout. One slot.",
    },
    {
        "candidate_id": "sig_pull_2s",
        "role": "protocol_signal",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim, two slots for occupancy.",
    },
    {
        "candidate_id": "sig_pull_vol",
        "role": "protocol_signal",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, "volume_confirmed"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim with volume confirmation.",
    },
    {
        "candidate_id": "sig_pull_liq100",
        "role": "protocol_signal",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, "amount_gte_100m"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim on names with 100m yuan bar amount.",
    },
    {
        "candidate_id": "sig_reclaim",
        "role": "protocol_signal",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (RECLAIM_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Wider MA20 reclaim; same-day 20d breakout allowed.",
    },
    {
        "candidate_id": "sig_pull_negext",
        "role": "protocol_signal",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim, rank by least-extended 20d return.",
    },
)


def iter_protocol_signal_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_VARIANTS


HOLD5_TAG = "hold5"
HOLD10_TAG = "hold10"
SIGNAL_HOLD_FAMILY = "path-a-protocol-signal-hold/v1"
SIGNAL_HOLD_STAGE_GOAL_ID = "path-a-protocol-signal-hold/v1"

# Written before the larger-universe / longer-hold QT is scored.
# 5-day reclaim already transferred MDD; 10-day hold is the return-density lever.
PROTOCOL_SIGNAL_HOLD_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "sig_pull_h10",
        "role": "protocol_signal_hold",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD10_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim, 10-day hold, one slot.",
    },
    {
        "candidate_id": "sig_pull_negext_h10",
        "role": "protocol_signal_hold",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD10_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Best 5d identity, 10-day hold, anti-chase rank.",
    },
    {
        "candidate_id": "sig_pull_2s_h10",
        "role": "protocol_signal_hold",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD10_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Strict reclaim, 10-day hold, two slots.",
    },
    {
        "candidate_id": "sig_reclaim_h10",
        "role": "protocol_signal_hold",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (RECLAIM_TAG, HOLD10_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Wider reclaim, 10-day hold.",
    },
    {
        "candidate_id": "sig_pull_h10_liq",
        "role": "protocol_signal_hold",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD10_TAG, "amount_gte_100m"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "10-day reclaim on 100m yuan bars.",
    },
    {
        "candidate_id": "sig_pull_negext_h5",
        "role": "protocol_signal_hold",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD5_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Zero-refit of best 5d rule on the larger Jiaoch universe.",
    },
)


def iter_protocol_signal_hold_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_HOLD_VARIANTS


FROZEN_RECLAIM_CANDIDATE_ID = "sig_pull_negext_h5"
FROZEN_RECLAIM_VARIANT = next(
    row
    for row in PROTOCOL_SIGNAL_HOLD_VARIANTS
    if row["candidate_id"] == FROZEN_RECLAIM_CANDIDATE_ID
)


LIMIT_FOLLOW_TAG = "limit_up_next_day_confirm"
MA60_RECLAIM_TAG = "trend_ma60_reclaim"
BREAKOUT_60D_TAG = "breakout_60d"
SIGNAL_ENTRY_FAMILY = "path-a-protocol-signal-entry/v1"
SIGNAL_ENTRY_STAGE_GOAL_ID = "path-a-protocol-signal-entry/v1"

# Third entry book. Not MA20 reclaim and not 20d breakout.
# limit_follow: yesterday limit-up, today holds the close and is not another board.
# ma60_reclaim: slower trend turn. breakout_60d: slower breakout.
PROTOCOL_SIGNAL_ENTRY_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "ent_lim_follow",
        "role": "protocol_signal_entry",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (LIMIT_FOLLOW_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Limit-up yesterday, today confirms without another board.",
    },
    {
        "candidate_id": "ent_lim_follow_2s",
        "role": "protocol_signal_entry",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (LIMIT_FOLLOW_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Limit-follow, two slots.",
    },
    {
        "candidate_id": "ent_lim_follow_liq",
        "role": "protocol_signal_entry",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (LIMIT_FOLLOW_TAG, "amount_gte_100m"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Limit-follow on 100m yuan bars.",
    },
    {
        "candidate_id": "ent_ma60",
        "role": "protocol_signal_entry",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (MA60_RECLAIM_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Reclaim MA60 in a 20/60 uptrend. Slower than MA20 reclaim.",
    },
    {
        "candidate_id": "ent_bo60",
        "role": "protocol_signal_entry",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (BREAKOUT_60D_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "60-day high breakout. Slower than the failed 20d breakout.",
    },
    {
        "candidate_id": "ent_lim_negext",
        "role": "protocol_signal_entry",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (LIMIT_FOLLOW_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Limit-follow, rank by least-extended 20d return.",
    },
)


def iter_protocol_signal_entry_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_ENTRY_VARIANTS


DN2_BOUNCE_TAG = "down2_bounce_uptrend"
DN3_BOUNCE_TAG = "down3_bounce_uptrend"
INSIDE_UP_TAG = "inside_bar_break_up"
TIGHT5_UP_TAG = "tight5_range_break_up"
SIGNAL_BOUNCE_FAMILY = "path-a-protocol-signal-bounce/v1"
SIGNAL_BOUNCE_STAGE_GOAL_ID = "path-a-protocol-signal-bounce/v1"

# Fourth identity book. Not MA20 reclaim, not 20d/60d breakout, not limit-follow.
# Written before the bounce QT is scored. Dual-partition gate: train AND
# holdout must both pass 26/15. Do not retune sig_pull_negext_h5.
PROTOCOL_SIGNAL_BOUNCE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "bounce_dn2",
        "role": "protocol_signal_bounce",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Two down closes then an up close in a 20/60 uptrend.",
    },
    {
        "candidate_id": "bounce_dn3",
        "role": "protocol_signal_bounce",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN3_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Three down closes then an up close. Stricter bounce.",
    },
    {
        "candidate_id": "bounce_dn2_negext",
        "role": "protocol_signal_bounce",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Two-day bounce, anti-chase rank.",
    },
    {
        "candidate_id": "bounce_inside",
        "role": "protocol_signal_bounce",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (INSIDE_UP_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Yesterday inside bar, today closes above that high.",
    },
    {
        "candidate_id": "bounce_tight5",
        "role": "protocol_signal_bounce",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (TIGHT5_UP_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "5-day range compressed vs 20-day, then close breaks 5-day high.",
    },
    {
        "candidate_id": "bounce_dn2_liq",
        "role": "protocol_signal_bounce",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG, "amount_gte_100m"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Two-day bounce on 100m yuan bars.",
    },
)


def iter_protocol_signal_bounce_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_BOUNCE_VARIANTS


ENGULF_TAG = "bullish_engulf_uptrend"
HAMMER_TAG = "hammer_bounce_uptrend"
MA10_RECLAIM_TAG = "trend_ma10_reclaim"
LO20_BOUNCE_TAG = "bounce_off_20d_low"
NR7_UP_TAG = "nr7_break_uptrend"
SIGNAL_SHAPE_FAMILY = "path-a-protocol-signal-shape/v1"
SIGNAL_SHAPE_STAGE_GOAL_ID = "path-a-protocol-signal-shape/v1"

# Fifth identity book. Not MA20 reclaim, not down2 bounce, not limit-follow.
# Written before the shape QT is scored. Dual-partition 26/15 still required.
PROTOCOL_SIGNAL_SHAPE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "shape_engulf",
        "role": "protocol_signal_shape",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (ENGULF_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Bullish engulfing in a 20/60 uptrend. Not a 20d breakout.",
    },
    {
        "candidate_id": "shape_engulf_negext",
        "role": "protocol_signal_shape",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (ENGULF_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Bullish engulfing, anti-chase rank.",
    },
    {
        "candidate_id": "shape_hammer",
        "role": "protocol_signal_shape",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (HAMMER_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Long lower wick in an uptrend after a dip.",
    },
    {
        "candidate_id": "shape_ma10",
        "role": "protocol_signal_shape",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (MA10_RECLAIM_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Faster MA10 reclaim with MA10>MA20>MA60. Not MA20 reclaim.",
    },
    {
        "candidate_id": "shape_lo20",
        "role": "protocol_signal_shape",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (LO20_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Bounce after a close near the 20-day low. Mean reversion.",
    },
    {
        "candidate_id": "shape_nr7",
        "role": "protocol_signal_shape",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (NR7_UP_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Narrowest range in 7 days, then close above prior high.",
    },
)


def iter_protocol_signal_shape_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_SHAPE_VARIANTS


MACD_GOLD_TAG = "macd_gold_uptrend"
KDJ_OVERSOLD_TAG = "kdj_oversold_cross"
BOLL_RECLAIM_TAG = "boll_lower_reclaim"
SIGNAL_CLASSIC_FAMILY = "path-a-protocol-signal-classic/v1"
SIGNAL_CLASSIC_STAGE_GOAL_ID = "path-a-protocol-signal-classic/v1"

# Sixth identity book. Textbook MACD / KDJ / BOLL events, not tag filters
# on reclaim or bounce. Written before the classic QT is scored.
PROTOCOL_SIGNAL_CLASSIC_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "classic_macd",
        "role": "protocol_signal_classic",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (MACD_GOLD_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "12/26/9 DIF crosses above DEA while MA20>MA60.",
    },
    {
        "candidate_id": "classic_macd_negext",
        "role": "protocol_signal_classic",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (MACD_GOLD_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "MACD golden cross, anti-chase rank.",
    },
    {
        "candidate_id": "classic_kdj",
        "role": "protocol_signal_classic",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (KDJ_OVERSOLD_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "9/3/3 K crosses D after J or K was <= 20. Reversal.",
    },
    {
        "candidate_id": "classic_kdj_negext",
        "role": "protocol_signal_classic",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (KDJ_OVERSOLD_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "KDJ oversold golden cross, anti-chase rank.",
    },
    {
        "candidate_id": "classic_boll",
        "role": "protocol_signal_classic",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (BOLL_RECLAIM_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Close was below the 20,2 lower band and reclaims it.",
    },
    {
        "candidate_id": "classic_boll_negext",
        "role": "protocol_signal_classic",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (BOLL_RECLAIM_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Bollinger lower-band reclaim, anti-chase rank.",
    },
)


def iter_protocol_signal_classic_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_CLASSIC_VARIANTS


GAP_TRUE_TAG = "gap_down_fill_same_day"
GAP_OPEN_TAG = "low_open_reclaim_3pct"
GAP_DELAY_TAG = "gap_down_fill_next_day"
GAP_OPEN_RECLAIM_FRAC = 0.03
SIGNAL_GAP_FAMILY = "path-a-protocol-signal-gap/v1"
SIGNAL_GAP_STAGE_GOAL_ID = "path-a-protocol-signal-gap/v1"

# Seventh identity book. Gap-fill events, not reclaim / bounce / shape / MACD.
# Written before the gap QT is scored. Dual-partition 26/15 still required.
PROTOCOL_SIGNAL_GAP_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "gap_true",
        "role": "protocol_signal_gap",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_TRUE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Open below prior low, close back through it. MA20>MA60.",
    },
    {
        "candidate_id": "gap_true_negext",
        "role": "protocol_signal_gap",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_TRUE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Same-day true gap fill, anti-chase rank.",
    },
    {
        "candidate_id": "gap_open",
        "role": "protocol_signal_gap",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_OPEN_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Open at least 3% below prior close, close reclaims it.",
    },
    {
        "candidate_id": "gap_open_negext",
        "role": "protocol_signal_gap",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_OPEN_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "3% low-open reclaim, anti-chase rank.",
    },
    {
        "candidate_id": "gap_delay",
        "role": "protocol_signal_gap",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_DELAY_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Yesterday left an unfilled down gap; today closes it.",
    },
    {
        "candidate_id": "gap_delay_negext",
        "role": "protocol_signal_gap",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_DELAY_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Next-day gap fill, anti-chase rank.",
    },
)


def iter_protocol_signal_gap_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_GAP_VARIANTS


VALUE_PE_NULL_TAG = "value_pe_null"
VALUE_PE_TTM_OK_TAG = "value_pe_ttm_ok"
VALUE_PB_OK_TAG = "value_pb_ok"
SIGNAL_VALUE_FAMILY = "path-a-protocol-signal-value/v1"
SIGNAL_VALUE_STAGE_GOAL_ID = "path-a-protocol-signal-value/v1"

# Value ranks on already-scored entry books. Bounce / reclaim / gap fires
# stay frozen. Null PE is a valid state, not an exclusion.
PROTOCOL_SIGNAL_VALUE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "val_bounce_pb",
        "role": "protocol_signal_value",
        "rank_key": "pb_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Down2 bounce, buy cheapest PB. Null PE stays eligible.",
    },
    {
        "candidate_id": "val_bounce_pe",
        "role": "protocol_signal_value",
        "rank_key": "pe_ttm_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG, VALUE_PE_TTM_OK_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Down2 bounce among names with a PE_TTM, cheapest first.",
    },
    {
        "candidate_id": "val_bounce_unprof_pb",
        "role": "protocol_signal_value",
        "rank_key": "pb_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG, VALUE_PE_NULL_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Down2 bounce on loss-making names, cheapest PB. Not an exclusion.",
    },
    {
        "candidate_id": "val_bounce_small",
        "role": "protocol_signal_value",
        "rank_key": "circ_mv_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (DN2_BOUNCE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Down2 bounce, smallest circulating cap.",
    },
    {
        "candidate_id": "val_reclaim_pb",
        "role": "protocol_signal_value",
        "rank_key": "pb_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (PULLBACK_TAG, HOLD5_TAG),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "MA20 reclaim, cheapest PB. Reclaim fire not retuned.",
    },
    {
        "candidate_id": "val_gap_pb",
        "role": "protocol_signal_value",
        "rank_key": "pb_asc",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (GAP_TRUE_TAG,),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Same-day true gap fill, cheapest PB.",
    },
)


def iter_protocol_signal_value_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_SIGNAL_VALUE_VARIANTS


def assert_signal_variants_obey_protocol() -> None:
    for variants in (
        PROTOCOL_SIGNAL_VARIANTS,
        PROTOCOL_SIGNAL_HOLD_VARIANTS,
        PROTOCOL_SIGNAL_ENTRY_VARIANTS,
        PROTOCOL_SIGNAL_BOUNCE_VARIANTS,
        PROTOCOL_SIGNAL_SHAPE_VARIANTS,
        PROTOCOL_SIGNAL_CLASSIC_VARIANTS,
        PROTOCOL_SIGNAL_GAP_VARIANTS,
        PROTOCOL_SIGNAL_VALUE_VARIANTS,
    ):
        assert_variants_obey_protocol(variants)
        for row in variants:
            required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
            if "breakout_20d" in required:
                raise ValueError(f"{row['candidate_id']} still requires breakout_20d")
