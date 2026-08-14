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


def assert_signal_variants_obey_protocol() -> None:
    for variants in (
        PROTOCOL_SIGNAL_VARIANTS,
        PROTOCOL_SIGNAL_HOLD_VARIANTS,
        PROTOCOL_SIGNAL_ENTRY_VARIANTS,
    ):
        assert_variants_obey_protocol(variants)
        for row in variants:
            required = set((row.get("kernel") or {}).get("required_signal_tags") or ())
            if "breakout_20d" in required:
                raise ValueError(f"{row['candidate_id']} still requires breakout_20d")
