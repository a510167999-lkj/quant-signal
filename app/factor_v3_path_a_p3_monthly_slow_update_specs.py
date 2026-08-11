"""P3 monthly slow-update discipline (catalog §5).

A low-frequency, small-arm-set adaptive layer. Each month, on the first trading
day, the engine evaluates frozen candidate arms over a past estimation window
and selects one per a pre-registered rule. The decision is written to a ledger.
Candidate arms MUST come from P0/P2 frozen variant_ids (no new specs).

Discipline (catalog §5.2 — locked; change => bump version):
- update cadence: first trading day of each calendar month
- estimation window: past 126 trading days (~6 months), data-as-of only
- candidate arms: at most 3, from P0/P2 frozen variant_ids
- selection rule: best estimation-window MDD; tiebreak higher compounded return
- switch cooldown: hold an arm >= 20 trading days unless a P0-level circuit
  breaker (loss_streak_3) fires on it
- no in-window grid search; no daily/weekly re-fit
"""

from __future__ import annotations

from typing import Any

# Cadence
UPDATE_CADENCE = "first_trading_day_of_month"
ESTIMATION_WINDOW_TRADING_DAYS = 126
SWITCH_COOLDOWN_TRADING_DAYS = 20

# Selection rule (pre-registered, exhaustive tiebreak order)
SELECTION_CRITERION = "best_estimation_window_mdd_then_return"
SELECTION_TIEBREAK = [
    "min_abs_mdd",  # smaller |MDD| first
    "max_compounded_return_pct",
    "max_both_pass_rate",
    "candidate_id_lexicographic",  # deterministic last resort
]

# Candidate arms (catalog §5.3). All MUST be frozen variant_ids from P0/P2.
# e4_primary is always present (baseline anchor); the P0 winner and a defensive
# vol-target arm round out the pool of 3.
ARM_POOL: tuple[dict[str, Any], ...] = (
    {
        "arm_id": "e4_primary",
        "source": "P0/P2 baseline",
        "frozen_variant_id": "e4_primary",
        "rationale": "Baseline kernel; always available as the no-change arm.",
    },
    {
        "arm_id": "p0_loss_streak_3",
        "source": "P0 winner",
        "frozen_variant_id": "p0_loss_streak_3",
        "rationale": "P0 replay winner: MDD -14.7% / ret 85.6% / both 49.4%.",
    },
    {
        "arm_id": "p2_vol_target_10",
        "source": "P2 defensive",
        "frozen_variant_id": "p2_vol_target_10",
        "rationale": "P2 vol-target: MDD -5.98% (very defensive), ret 20.3%.",
    },
)

# Circuit breaker that may override the cooldown (catalog §5.2).
CIRCUIT_BREAKER_ARM = "p0_loss_streak_3"
CIRCUIT_BREAKER_OVERLAY = {
    "kind": "loss_streak",
    "streak": 3,
    "cooldown_signal_days": 5,
}


def iter_arm_pool() -> tuple[dict[str, Any], ...]:
    return ARM_POOL


__all__ = [
    "ARM_POOL",
    "CIRCUIT_BREAKER_ARM",
    "CIRCUIT_BREAKER_OVERLAY",
    "ESTIMATION_WINDOW_TRADING_DAYS",
    "SELECTION_CRITERION",
    "SELECTION_TIEBREAK",
    "SWITCH_COOLDOWN_TRADING_DAYS",
    "UPDATE_CADENCE",
    "iter_arm_pool",
]
