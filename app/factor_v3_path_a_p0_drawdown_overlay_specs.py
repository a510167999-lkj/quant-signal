"""P0 drawdown overlay variant table (frozen, no grid search).

Signal kernel remains e4_primary; only new-entry gate / size changes.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Any

# Shared signal kernel (must match F0 frozen primary economic identity).
SIGNAL_KERNEL = MappingProxyType(
    {
        "top_n": 3,
        "max_active_positions": 2,
        "symbol_cooldown_days": 5,
        "market_levels": ("favorable", "neutral"),
        "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
        "excluded_signal_tags": ("price_gap_down",),
        "hold_days": 5,
        "capital_model": "slot-daily",
        "exposure_multiplier": 1.0,
        "roundtrip_cost_bps": 25.0,
        "slippage_bps": 10.0,
        "annual_financing_rate_pct": 0.0,
        "pre_exit_calendar_gap_days": 0,
        "prior_high_trailing_stop_pct": None,
        "partial_profit_activation_pct": None,
        "partial_profit_fraction": 0.0,
    }
)

# Catalog §2.3 — finite set only.
P0_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "e4_primary",
        "role": "baseline",
        "overlay": {"kind": "none"},
        "rationale": "Frozen primary; no overlay.",
    },
    {
        "candidate_id": "p0_dd_block_12",
        "role": "p0_overlay",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 12.0,
            "resume_dd_pct": 8.0,
        },
        "rationale": "Block new entries while peak drawdown >=12%; resume when <8%.",
    },
    {
        "candidate_id": "p0_dd_block_15",
        "role": "p0_overlay",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 15.0,
            "resume_dd_pct": 10.0,
        },
        "rationale": "Block new entries while peak drawdown >=15%; resume when <10%.",
    },
    {
        "candidate_id": "p0_dd_tier_8_12_15",
        "role": "p0_overlay",
        "overlay": {
            "kind": "drawdown_tier",
            "half_dd_pct": 8.0,
            "block_dd_pct": 12.0,
            # >=15% same as block_new (no forced flatten of open books).
            "hard_block_dd_pct": 15.0,
        },
        "rationale": (
            "dd in [8,12)-> half new-entry size; >=12% block new entries; "
            "open books exit on original hold."
        ),
    },
    {
        "candidate_id": "p0_loss_streak_3",
        "role": "p0_overlay",
        "overlay": {
            "kind": "loss_streak",
            "streak": 3,
            "cooldown_signal_days": 5,
        },
        "rationale": "After 3 consecutive closed losers, block new entries for 5 signal days.",
    },
    {
        "candidate_id": "p0_dd12_plus_streak3",
        "role": "p0_overlay",
        "overlay": {
            "kind": "combo_or",
            "drawdown_block": {
                "block_dd_pct": 12.0,
                "resume_dd_pct": 8.0,
            },
            "loss_streak": {
                "streak": 3,
                "cooldown_signal_days": 5,
            },
        },
        "rationale": "Block new entries if dd_block_12 OR loss_streak_3 fires.",
    },
)


def iter_p0_variants() -> tuple[dict[str, Any], ...]:
    return P0_VARIANTS


__all__ = ["P0_VARIANTS", "SIGNAL_KERNEL", "iter_p0_variants"]
