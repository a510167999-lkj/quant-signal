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


FAILED_BOOK_IDS = frozenset(row["candidate_id"] for row in BOOK_ROUND_VARIANTS)

# Combine the two closest books: single-slot (best MDD) and volume_confirmed
# (best return). Distinct from overlay-7 and book-6.
COMBO_ROUND_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "combo_vol_t2_m1",
        "role": "combo",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "volume_confirmed + single slot (best-return × best-MDD books).",
    },
    {
        "candidate_id": "combo_vol_t1_m1",
        "role": "combo",
        "kernel": {
            "top_n": 1,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "volume_confirmed + single name.",
    },
    {
        "candidate_id": "combo_vol_fav",
        "role": "combo",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable",),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "volume_confirmed + favorable-only.",
    },
    {
        "candidate_id": "combo_t2_m1_fav",
        "role": "combo",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable",),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Single slot + favorable-only.",
    },
    {
        "candidate_id": "combo_vol_t2_m1_fav",
        "role": "combo",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable",),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "volume + single slot + favorable-only.",
    },
    {
        "candidate_id": "combo_vol_cd10",
        "role": "combo",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 10,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "volume_confirmed",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "volume_confirmed + cooldown 10.",
    },
)

FAILED_COMBO_IDS = frozenset(row["candidate_id"] for row in COMBO_ROUND_VARIANTS)


def iter_book_round_variants() -> tuple[dict[str, Any], ...]:
    return BOOK_ROUND_VARIANTS


def iter_combo_round_variants() -> tuple[dict[str, Any], ...]:
    return COMBO_ROUND_VARIANTS


COMBO_OVERLAY_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "combo_vol_t2_m1_dd16",
        "base_id": "combo_vol_t2_m1",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 16.0,
            "resume_dd_pct": 10.0,
        },
        "rationale": "Best combo book + block new entries at 16% DD.",
    },
    {
        "candidate_id": "combo_vol_t2_m1_dd18",
        "base_id": "combo_vol_t2_m1",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 18.0,
            "resume_dd_pct": 12.0,
        },
        "rationale": "Best combo book + block at 18% DD.",
    },
    {
        "candidate_id": "combo_vol_t2_m1_dd15",
        "base_id": "combo_vol_t2_m1",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 15.0,
            "resume_dd_pct": 10.0,
        },
        "rationale": "Best combo book + block at 15% DD.",
    },
    {
        "candidate_id": "combo_vol_t2_m1_streak5",
        "base_id": "combo_vol_t2_m1",
        "overlay": {
            "kind": "loss_streak",
            "streak": 5,
            "cooldown_signal_days": 3,
        },
        "rationale": "Best combo book + mild streak gate.",
    },
    {
        "candidate_id": "combo_vol_t2_m1_tier14_18",
        "base_id": "combo_vol_t2_m1",
        "overlay": {
            "kind": "drawdown_tier",
            "half_dd_pct": 14.0,
            "block_dd_pct": 18.0,
            "hard_block_dd_pct": 20.0,
        },
        "rationale": "Best combo book + half at 14%, block at 18%.",
    },
)


def iter_combo_overlay_variants() -> tuple[dict[str, Any], ...]:
    return COMBO_OVERLAY_VARIANTS


FAILED_COMBO_OVERLAY_IDS = frozenset(
    row["candidate_id"] for row in COMBO_OVERLAY_VARIANTS
)

_COMBO_VOL_T2_M1_KERNEL: dict[str, Any] = {
    "top_n": 2,
    "max_active_positions": 1,
    "symbol_cooldown_days": 5,
    "market_levels": ("favorable", "neutral"),
    "required_signal_tags": (
        "breadth_ma20_gte_60",
        "breakout_20d",
        "volume_confirmed",
    ),
    "excluded_signal_tags": ("price_gap_down",),
}

# Diagnosis of combo_vol_t2_m1 (55 trades): worst DD is 2026-01-23..2026-05-14.
# Hard DD blocks made MDD worse. Drop-approx said excluding rsi_repair
# (signal-day RSI mention) clips that episode. Finite clip set only.
CLIP_ROUND_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "clip_no_rsi_repair",
        "role": "clip_reselect",
        "kernel": {
            **_COMBO_VOL_T2_M1_KERNEL,
            "excluded_signal_tags": ("price_gap_down", "rsi_repair"),
        },
        "rationale": "Re-select combo book excluding rsi_repair.",
    },
    {
        "candidate_id": "clip_skip_rsi_repair",
        "role": "clip_skip",
        "base_id": "combo_vol_t2_m1",
        "kernel": dict(_COMBO_VOL_T2_M1_KERNEL),
        "skip_tags": ("rsi_repair",),
        "rationale": "Keep combo book; skip rsi_repair entries, no refill.",
    },
    {
        "candidate_id": "clip_no_med10",
        "role": "clip_reselect",
        "kernel": {
            **_COMBO_VOL_T2_M1_KERNEL,
            "excluded_signal_tags": ("price_gap_down", "breadth_median_ret20_gte_10"),
        },
        "rationale": "Re-select combo book excluding overheated median-ret20>=10.",
    },
    {
        "candidate_id": "clip_skip_med10",
        "role": "clip_skip",
        "base_id": "combo_vol_t2_m1",
        "kernel": dict(_COMBO_VOL_T2_M1_KERNEL),
        "skip_tags": ("breadth_median_ret20_gte_10",),
        "rationale": "Keep combo book; skip median-ret20>=10 entries, no refill.",
    },
    {
        "candidate_id": "clip_skip_rsi_advlt50",
        "role": "clip_skip",
        "base_id": "combo_vol_t2_m1",
        "kernel": dict(_COMBO_VOL_T2_M1_KERNEL),
        "skip_tags": ("rsi_repair", "breadth_advancing_lt_50"),
        "rationale": "Skip rsi_repair and advancing<50, no refill.",
    },
    {
        "candidate_id": "clip_skip_hvol",
        "role": "clip_skip",
        "base_id": "combo_vol_t2_m1",
        "kernel": dict(_COMBO_VOL_T2_M1_KERNEL),
        "skip_tags": ("high_volatility",),
        "rationale": "Skip high_volatility entries, no refill.",
    },
)


def iter_clip_round_variants() -> tuple[dict[str, Any], ...]:
    return CLIP_ROUND_VARIANTS


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
    "CLIP_ROUND_VARIANTS",
    "COMBO_OVERLAY_VARIANTS",
    "COMBO_ROUND_VARIANTS",
    "FAILED_BOOK_IDS",
    "FAILED_COMBO_IDS",
    "FAILED_COMBO_OVERLAY_IDS",
    "FAILED_OVERLAY_IDS",
    "SIGNAL_KERNEL",
    "STAGE_GOAL_ID",
    "iter_book_round_variants",
    "iter_clip_round_variants",
    "iter_combo_overlay_variants",
    "iter_combo_round_variants",
    "merged_kernel",
]
