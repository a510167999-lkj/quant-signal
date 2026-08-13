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


# Train-only diagnosis on the 1667-name holdout QT (holdout sealed):
# quality filters compress MDD toward 15% but latest-1y stays ~2-12%.
# These six are written down before holdout is scored.
PROTOCOL_FACTOR_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "factor_buy",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "action_buy",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "One slot; BUY only, skip watch-grade signals.",
    },
    {
        "candidate_id": "factor_cvol",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "controlled_volatility",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Avoid chaotic breakouts; require controlled 20d vol.",
    },
    {
        "candidate_id": "factor_rsi_bal",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "balanced_rsi",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Require balanced RSI. Not the banned rsi_repair skip.",
    },
    {
        "candidate_id": "factor_ma70",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "breadth_ma20_gte_70",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Stronger breadth: 70% of names above MA20.",
    },
    {
        "candidate_id": "factor_score5",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "score_gte_5",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Higher composite score; fewer, cleaner entries.",
    },
    {
        "candidate_id": "factor_cvol_2slot",
        "role": "protocol_factor",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": (
                "breadth_ma20_gte_60",
                "breakout_20d",
                "controlled_volatility",
            ),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "controlled_vol with two slots for more return density.",
    },
)


def iter_protocol_factor_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_FACTOR_VARIANTS


def assert_variants_obey_protocol(
    variants: tuple[dict[str, Any], ...] | None = None,
) -> None:
    banned = set(BANNED_POSTHOC_SKIP_TAGS)
    chosen = variants or PROTOCOL_BASELINE_VARIANTS
    for row in chosen:
        skip = set(row.get("skip_tags") or ())
        if skip & banned:
            raise ValueError(f"{row['candidate_id']} uses banned post-hoc skip")
        if row.get("entry_scale") not in (None, 1, 1.0):
            raise ValueError(f"{row['candidate_id']} uses a post-hoc size cut")
        excluded = set((row.get("kernel") or {}).get("excluded_signal_tags") or ())
        if excluded & banned:
            raise ValueError(f"{row['candidate_id']} excludes a banned post-hoc tag")
