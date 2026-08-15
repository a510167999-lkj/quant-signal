"""Pre-registered monthly 3-arm switch for Path-A identities.

Arms are cash, the frozen MA20 reclaim, and the down2 bounce.
Selection is written down before scoring: among live arms whose
estimation-window MDD is within 15%, pick the highest compounded
return. If none qualify, sit in cash. Not P3's min-MDD rule.
"""

from __future__ import annotations

from typing import Any

from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal_specs import (
    FROZEN_RECLAIM_CANDIDATE_ID,
    FROZEN_RECLAIM_VARIANT,
    iter_protocol_signal_bounce_variants,
)

STAGE_GOAL_ID = "path-a-protocol-monthly-switch/v1"
CASH_ARM_ID = "cash"
BOUNCE_ARM_ID = "bounce_dn2_negext"
RECLAIM_ARM_ID = FROZEN_RECLAIM_CANDIDATE_ID
INITIAL_ARM_ID = CASH_ARM_ID

UPDATE_CADENCE = "first_signal_date_of_month"
ESTIMATION_WINDOW_TRADING_DAYS = 126
SWITCH_COOLDOWN_TRADING_DAYS = 20
MAX_ABS_MDD_PCT = goal.TARGET_MAX_DRAWDOWN_PCT
SELECTION_CRITERION = "max_window_return_among_mdd_le_15_else_cash"

BOUNCE_VARIANT = next(
    row
    for row in iter_protocol_signal_bounce_variants()
    if row["candidate_id"] == BOUNCE_ARM_ID
)
RECLAIM_VARIANT = FROZEN_RECLAIM_VARIANT

ARM_POOL: tuple[dict[str, Any], ...] = (
    {
        "arm_id": CASH_ARM_ID,
        "kind": "cash",
        "zh": "空仓",
        "rationale": "Fallback when no live arm clears the 15% window MDD gate.",
    },
    {
        "arm_id": BOUNCE_ARM_ID,
        "kind": "identity",
        "zh": "连跌反弹抗追高",
        "variant": BOUNCE_VARIANT,
        "rationale": "Locked-split dual-pass identity. Not retuned here.",
    },
    {
        "arm_id": RECLAIM_ARM_ID,
        "kind": "identity",
        "zh": "回踩抗追高",
        "variant": RECLAIM_VARIANT,
        "rationale": "User-accepted reclaim hypothesis. Not retuned here.",
    },
)


def iter_arm_pool() -> tuple[dict[str, Any], ...]:
    return ARM_POOL


def live_arm_ids() -> tuple[str, ...]:
    return (BOUNCE_ARM_ID, RECLAIM_ARM_ID)
