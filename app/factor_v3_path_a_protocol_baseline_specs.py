"""Pre-registered kernels for the locked split. No post-hoc skips or size cuts."""

from __future__ import annotations

from typing import Any

from app.factor_v3_path_a_research_protocol import BANNED_POSTHOC_SKIP_TAGS

STAGE_GOAL_ID = "path-a-protocol-baseline/v1"

# Finite set written down before scoring the 1667-name time split.
# No rsi_repair / advancing / med10 skips. No entry_scale haircut.
PROTOCOL_BASELINE_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "proto_e4",
        "role": "protocol_baseline",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Frozen e4 economic identity. No extra tags.",
    },
    {
        "candidate_id": "proto_vol",
        "role": "protocol_baseline",
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
        "rationale": "e4 plus volume_confirmed. No skip overlay.",
    },
    {
        "candidate_id": "proto_t2_m1",
        "role": "protocol_baseline",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "e4 tags, one slot. Portfolio control only.",
    },
)


def iter_protocol_baseline_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_BASELINE_VARIANTS


def assert_variants_obey_protocol() -> None:
    banned = set(BANNED_POSTHOC_SKIP_TAGS)
    for row in PROTOCOL_BASELINE_VARIANTS:
        skip = set(row.get("skip_tags") or ())
        if skip & banned:
            raise ValueError(f"{row['candidate_id']} uses banned post-hoc skip")
        if row.get("entry_scale") not in (None, 1, 1.0):
            raise ValueError(f"{row['candidate_id']} uses a post-hoc size cut")
        excluded = set((row.get("kernel") or {}).get("excluded_signal_tags") or ())
        if excluded & banned:
            raise ValueError(f"{row['candidate_id']} excludes a banned post-hoc tag")
