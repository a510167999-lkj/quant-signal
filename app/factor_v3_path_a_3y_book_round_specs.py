"""Finite 3y selection-book variants. Not the failed overlay-only seven."""

from __future__ import annotations

from typing import Any

from app.factor_v3_path_a_p0_drawdown_overlay_specs import SIGNAL_KERNEL

STAGE_GOAL_ID = "path-a-3y-book-round/v1"

# Failed overlay-only ids from path-a-3y-drawdown-round/v1. This set must stay
# disjoint so the iteration is not a re-run of that catalog.
FAILED_OVERLAY_IDS = frozenset(
    {
        "e4_primary",
        "p0_loss_streak_3",
        "p0_dd_block_15",
        "dd_block_18",
        "dd_block_20",
        "dd_tier_15_20",
        "loss_streak_5",
    }
)

# Frozen book / filter variants on the same e4 tag family. Costs unchanged.
# favorable-only tightens market_level; it does not loosen it.
BOOK_ROUND_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "book_t2_m1",
        "role": "book",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Tighter book: top 2, one slot.",
    },
    {
        "candidate_id": "book_t1_m1",
        "role": "book",
        "kernel": {
            "top_n": 1,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Single-name book.",
    },
    {
        "candidate_id": "book_cd10",
        "role": "book",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 10,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Same slots as e4, longer symbol cooldown.",
    },
    {
        "candidate_id": "book_fav_only",
        "role": "book",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable",),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Tighten market_level to favorable only.",
    },
    {
        "candidate_id": "book_t2_m2_cd8",
        "role": "book",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 2,
            "symbol_cooldown_days": 8,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Top 2, two slots, cooldown 8.",
    },
    {
        "candidate_id": "book_vol_confirm",
        "role": "book",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "e4 tags plus volume_confirmed.",
    },
)


def iter_book_round_variants() -> tuple[dict[str, Any], ...]:
    return BOOK_ROUND_VARIANTS


def merged_kernel(variant: dict[str, Any]) -> dict[str, Any]:
    kernel = dict(SIGNAL_KERNEL)
    kernel["market_levels"] = list(SIGNAL_KERNEL["market_levels"])
    kernel["required_signal_tags"] = list(SIGNAL_KERNEL["required_signal_tags"])
    kernel["excluded_signal_tags"] = list(SIGNAL_KERNEL["excluded_signal_tags"])
    patch = variant.get("kernel") or {}
    for key, value in patch.items():
        kernel[key] = list(value) if isinstance(value, (tuple, list)) else value
    return kernel


__all__ = [
    "BOOK_ROUND_VARIANTS",
    "FAILED_OVERLAY_IDS",
    "SIGNAL_KERNEL",
    "STAGE_GOAL_ID",
    "iter_book_round_variants",
    "merged_kernel",
]
