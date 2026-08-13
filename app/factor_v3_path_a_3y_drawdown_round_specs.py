"""Finite 3y drawdown-reduction overlays on the frozen e4 kernel.

No grid. No market_level loosening. No 50/15 or universe edits.
"""

from __future__ import annotations

from typing import Any

from app.factor_v3_path_a_p0_drawdown_overlay_specs import SIGNAL_KERNEL

STAGE_GOAL_ID = "path-a-3y-drawdown-round/v1"

# Short frozen list: keep the known P0 anchors, add a few later-trigger
# drawdown gates aimed at clipping e4's ~24% MDD without a parameter sweep.
DRAWDOWN_ROUND_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "e4_primary",
        "role": "baseline",
        "overlay": {"kind": "none"},
        "rationale": "Frozen e4 kernel, no overlay.",
    },
    {
        "candidate_id": "p0_loss_streak_3",
        "role": "p0_anchor",
        "overlay": {
            "kind": "loss_streak",
            "streak": 3,
            "cooldown_signal_days": 5,
        },
        "rationale": "Previous 2y winner; already failed 3y P0 replay.",
    },
    {
        "candidate_id": "p0_dd_block_15",
        "role": "p0_anchor",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 15.0,
            "resume_dd_pct": 10.0,
        },
        "rationale": "Best-MDD P0 overlay on the same 3y slice.",
    },
    {
        "candidate_id": "dd_block_18",
        "role": "dd_round",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 18.0,
            "resume_dd_pct": 12.0,
        },
        "rationale": "Block new entries only after 18% peak DD; resume under 12%.",
    },
    {
        "candidate_id": "dd_block_20",
        "role": "dd_round",
        "overlay": {
            "kind": "drawdown_block",
            "block_dd_pct": 20.0,
            "resume_dd_pct": 14.0,
        },
        "rationale": "Later block at 20% DD to keep more of e4's return.",
    },
    {
        "candidate_id": "dd_tier_15_20",
        "role": "dd_round",
        "overlay": {
            "kind": "drawdown_tier",
            "half_dd_pct": 15.0,
            "block_dd_pct": 20.0,
            "hard_block_dd_pct": 22.0,
        },
        "rationale": "Half-size from 15% DD; block new entries at 20%.",
    },
    {
        "candidate_id": "loss_streak_5",
        "role": "dd_round",
        "overlay": {
            "kind": "loss_streak",
            "streak": 5,
            "cooldown_signal_days": 3,
        },
        "rationale": "Milder streak gate than p0_loss_streak_3.",
    },
)


def iter_drawdown_round_variants() -> tuple[dict[str, Any], ...]:
    return DRAWDOWN_ROUND_VARIANTS


__all__ = [
    "DRAWDOWN_ROUND_VARIANTS",
    "SIGNAL_KERNEL",
    "STAGE_GOAL_ID",
    "iter_drawdown_round_variants",
]
