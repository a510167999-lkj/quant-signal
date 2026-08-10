"""P2 small variants table (frozen, no grid search).

Catalog §4.2 — at most 4 variants. The signal kernel is the frozen e4_primary;
only the market-level set (P2 fav_only) or the entry-size formula (P2
vol_target) changes. p2_fav_only_plus_p0_best layers the P0 winning overlay on
fav_only once a unique P0 winner exists.
"""

from __future__ import annotations

from typing import Any

# Volatility target for p2_vol_target_10 (catalog §4.3):
# exposure = clip(sigma_target / sigma_realized, 0.25, 1.0); sigma_target is the
# daily-equivalent of ~10% annualized; lookback fixed at 20 days.
VOL_TARGET_LOOKBACK_DAYS = 20
VOL_TARGET_ANNUAL_PCT = 10.0
VOL_TARGET_MIN_SCALE = 0.25
VOL_TARGET_MAX_SCALE = 1.0

# P0 winner to layer into p2_fav_only_plus_p0_best (frozen after P0 replay).
P0_WINNER_OVERLAY: dict[str, Any] = {
    "kind": "loss_streak",
    "streak": 3,
    "cooldown_signal_days": 5,
}

P2_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "e4_primary",
        "role": "baseline",
        "change": "none",
        "rationale": "Frozen primary; anchor for P2 comparison.",
    },
    {
        "candidate_id": "p2_fav_only",
        "role": "p2_variant",
        "change": {
            "kind": "market_levels",
            "market_levels": ["favorable"],
        },
        "rationale": "Drop neutral from the market-level set; favorable only.",
    },
    {
        "candidate_id": "p2_vol_target_10",
        "role": "p2_variant",
        "change": {
            "kind": "vol_target",
            "lookback_days": VOL_TARGET_LOOKBACK_DAYS,
            "annual_target_pct": VOL_TARGET_ANNUAL_PCT,
            "min_scale": VOL_TARGET_MIN_SCALE,
            "max_scale": VOL_TARGET_MAX_SCALE,
        },
        "rationale": (
            "Entry size = clip(sigma_target/sigma_realized, 0.25, 1.0); "
            "sigma_realized from past 20 realized kernel equity daily returns."
        ),
    },
    {
        "candidate_id": "p2_fav_only_plus_p0_best",
        "role": "p2_variant",
        "change": {
            "kind": "market_levels_plus_overlay",
            "market_levels": ["favorable"],
            "overlay": P0_WINNER_OVERLAY,
        },
        "rationale": "fav_only + P0 winning overlay (loss_streak_3) layered on.",
    },
)


def iter_p2_variants() -> tuple[dict[str, Any], ...]:
    return P2_VARIANTS


__all__ = [
    "P0_WINNER_OVERLAY",
    "P2_VARIANTS",
    "VOL_TARGET_ANNUAL_PCT",
    "VOL_TARGET_LOOKBACK_DAYS",
    "VOL_TARGET_MAX_SCALE",
    "VOL_TARGET_MIN_SCALE",
    "iter_p2_variants",
]
