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


# Train-only diagnosis on the 1667-name QT (holdout sealed):
# breakout_20d raw expectancy is <= 0; moderate / RS-negative / anti-chase
# rank have the only positive books. These six change identity or rank
# factor. They are not more quality filters on e4.
ALLOWED_RANK_KEYS = frozenset({"rank_score", "neg_ext20", "rs20"})

PROTOCOL_ALT_VARIANTS: tuple[dict[str, Any], ...] = (
    {
        "candidate_id": "alt_rs_leader",
        "role": "protocol_alt",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("stock_rs20_market_leader",),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Drop breakout. Cross-section 20d market leader.",
    },
    {
        "candidate_id": "alt_rs_leader_xvol",
        "role": "protocol_alt",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("stock_rs20_market_leader",),
            "excluded_signal_tags": ("price_gap_down", "high_volatility"),
        },
        "rationale": "20d leader, skip chaotic vol>=45 names.",
    },
    {
        "candidate_id": "alt_rs60_strong_2s",
        "role": "protocol_alt",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("stock_rs60_strong", "ma_structure"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "Slower 60d relative strength, two slots for occupancy.",
    },
    {
        "candidate_id": "alt_rs_neg",
        "role": "protocol_alt",
        "rank_key": "rank_score",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("stock_rs20_lt_0", "ma_structure"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "A-share short-horizon reversal: 20d relative weakness.",
    },
    {
        "candidate_id": "alt_negext",
        "role": "protocol_alt",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 2,
            "max_active_positions": 1,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("breadth_ma20_gte_60", "breakout_20d"),
            "excluded_signal_tags": ("price_gap_down",),
        },
        "rationale": "e4 tags remain; rank by least-extended 20d return.",
    },
    {
        "candidate_id": "alt_pull_negext_2s",
        "role": "protocol_alt",
        "rank_key": "neg_ext20",
        "kernel": {
            "top_n": 3,
            "max_active_positions": 2,
            "symbol_cooldown_days": 5,
            "market_levels": ("favorable", "neutral"),
            "required_signal_tags": ("ma_structure", "moderate_20d_momentum"),
            "excluded_signal_tags": ("price_gap_down", "extended_20d_momentum"),
        },
        "rationale": "Drop breakout. Moderate-momentum pullback, anti-chase rank, two slots.",
    },
)


def iter_protocol_alt_variants() -> tuple[dict[str, Any], ...]:
    return PROTOCOL_ALT_VARIANTS


def apply_rank_key(
    trades: list[dict[str, Any]],
    rank_key: str | None,
) -> list[dict[str, Any]]:
    """Copy trades and overwrite rank_score. Does not mutate the input list."""

    key = str(rank_key or "rank_score").strip() or "rank_score"
    if key not in ALLOWED_RANK_KEYS:
        raise ValueError(f"unknown rank_key: {rank_key!r}")
    if key == "rank_score":
        return list(trades)

    out: list[dict[str, Any]] = []
    for trade in trades:
        copy = dict(trade)
        relative = trade.get("relative_strength") or {}
        if key == "neg_ext20":
            raw = relative.get("stock_return_20d_pct")
            try:
                copy["rank_score"] = -float(raw)
            except (TypeError, ValueError):
                copy["rank_score"] = 0.0
        elif key == "rs20":
            raw = relative.get("relative_strength_20d_pct")
            try:
                copy["rank_score"] = float(raw)
            except (TypeError, ValueError):
                copy["rank_score"] = -1e9
        out.append(copy)
    return out


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
        rank_key = row.get("rank_key")
        if rank_key not in (None, *ALLOWED_RANK_KEYS):
            raise ValueError(f"{row['candidate_id']} uses unknown rank_key {rank_key!r}")
