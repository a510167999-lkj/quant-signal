"""Locked Path-A search protocol after the 191-slice false positive.

Performance thresholds come from research_goal_contract (currently 26/15).
This module only locks *how* we search so we cannot retune on the mined
191-name book or treat full-path return as 12-month net.
"""

from __future__ import annotations

from types import MappingProxyType

from app import research_goal_contract as goal

PROTOCOL_ID = "path-a-locked-split/v1"
PROTOCOL_SCHEMA = "path-a-locked-split-protocol/v1"

# Time split is frozen before the next search. signal_date < HOLDOUT_START
# is train; holdout is [HOLDOUT_START, HOLDOUT_END].
TRAIN_START = "2023-07-03"
HOLDOUT_START = "2025-07-01"
HOLDOUT_END = "2026-07-03"

# The 191 traded-slice hunt and every skip/scale dug from it.
REJECTED_SLICE_CANDIDATE_ID = "vol_skip_rsi_adv_s85"
REJECTED_SLICE_STATUS = "rejected_slice_hypothesis"
CONTAMINATED_CANDIDATE_IDS = frozenset(
    {
        "vol_skip_rsi",
        "vol_skip_rsi_adv",
        "vol_skip_rsi_adv_s87",
        "vol_skip_rsi_adv_s85",
        "vol_skip_med10_or_adv",
        "vol_skip_med10_nors10",
        "clip_no_rsi_repair",
        "clip_skip_rsi_repair",
        "clip_no_med10",
        "clip_skip_med10",
        "clip_skip_rsi_advlt50",
        "clip_skip_hvol",
        "combo_vol_t2_m1",
        "combo_vol_t1_m1",
        "combo_vol_fav",
        "combo_t2_m1_fav",
        "combo_vol_t2_m1_fav",
        "combo_vol_cd10",
        "combo_vol_t2_m1_dd16",
        "combo_vol_t2_m1_dd18",
        "combo_vol_t2_m1_dd15",
        "combo_vol_t2_m1_streak5",
        "combo_vol_t2_m1_tier14_18",
    }
)
BANNED_POSTHOC_SKIP_TAGS = frozenset(
    {
        "rsi_repair",
        "breadth_advancing_lt_50",
        "breadth_median_ret20_gte_10",
    }
)

SEARCH_UNIVERSE_POLICY = "jiaoch_v2_holdout_not_191_traded_slice"
DATA_SOURCE_POLICY = goal.DATA_SOURCE_POLICY

# Primary dual-pass for this protocol: latest rolling 12m return + that
# partition's max drawdown. Full-path compounded return is diagnostic only.
SCORE_RULE = MappingProxyType(
    {
        "return_leg": "latest_rolling_12m_net",
        "drawdown_leg": "partition_max_drawdown",
        "full_path_return_is_not_annualized": True,
        "window_dual_pass_rate_is_diagnostic": True,
    }
)


def partition_for_signal_date(signal_date: str) -> str | None:
    day = str(signal_date or "")[:10]
    if not day:
        return None
    if TRAIN_START <= day < HOLDOUT_START:
        return "train"
    if HOLDOUT_START <= day <= HOLDOUT_END:
        return "holdout"
    return None


def filter_trades_for_partition(
    trades: list[dict],
    partition: str,
) -> list[dict]:
    wanted = str(partition)
    out: list[dict] = []
    for trade in trades:
        if partition_for_signal_date(str(trade.get("signal_date") or "")) == wanted:
            out.append(trade)
    return out


def meets_locked_split_targets(
    *,
    latest_12m_net_return_pct: float | None,
    partition_max_drawdown_pct: float | None,
) -> bool:
    """Latest 12m net and partition MDD. Do not pass full-path return here."""

    if latest_12m_net_return_pct is None or partition_max_drawdown_pct is None:
        return False
    return goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=float(latest_12m_net_return_pct),
        max_drawdown_pct=float(partition_max_drawdown_pct),
    )


def protocol_descriptor() -> dict[str, object]:
    return {
        "schema": PROTOCOL_SCHEMA,
        "protocol_id": PROTOCOL_ID,
        "train_start": TRAIN_START,
        "holdout_start": HOLDOUT_START,
        "holdout_end": HOLDOUT_END,
        "rejected_slice_candidate_id": REJECTED_SLICE_CANDIDATE_ID,
        "rejected_slice_status": REJECTED_SLICE_STATUS,
        "contaminated_candidate_ids": sorted(CONTAMINATED_CANDIDATE_IDS),
        "banned_posthoc_skip_tags": sorted(BANNED_POSTHOC_SKIP_TAGS),
        "search_universe_policy": SEARCH_UNIVERSE_POLICY,
        "data_source_policy": DATA_SOURCE_POLICY,
        "score_rule": dict(SCORE_RULE),
        "target_rolling_12m_net_return_pct": goal.TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": goal.TARGET_MAX_DRAWDOWN_PCT,
        "automatic_trading_allowed": goal.AUTOMATIC_TRADING_ALLOWED,
        "effective_strategy": False,
    }
