"""Frozen train-window policy for Factor V3 strategy research.

User intent (2026-08-10):
  - Do **not** require daily sync of the previous trading day.
  - Treat data **before 2026-08-01** as the training set cutoff.
  - Goal is a verifiable effective strategy, never automatic trading.

This contract is intentionally aligned with the existing Factor V3 exact
calendar (development through 2026-07-03) and frozen-v1 contamination
boundary. It does not open embargo/final-OOS.
"""

from __future__ import annotations

from types import MappingProxyType

from app import audited_pit_factor_v3_formal_materializer_v2_contract as materializer
from app import research_goal_contract as goal

TRAIN_WINDOW_FREEZE_SCHEMA = "factor-v3-train-window-freeze-contract/v1"
TRAIN_WINDOW_FREEZE_POLICY_ID = "train-before-2026-08-01/v1"

# Exclusive calendar cutoff: no training session on or after this date.
TRAIN_EXCLUSIVE_END_DATE = "2026-08-01"

# Inclusive last development/session end already sealed by Factor V3 calendar.
# Must be strictly before TRAIN_EXCLUSIVE_END_DATE.
TRAIN_INCLUSIVE_SESSION_END = materializer.FROZEN_DEVELOPMENT_SESSION_END  # 2026-07-03
TRAIN_INCLUSIVE_SESSION_START = materializer.FROZEN_DEVELOPMENT_SESSION_START  # 2024-07-05

# Prewindow sits before development; all_market = prewindow + development.
TRAIN_PREWINDOW_SESSION_COUNT = int(
    materializer.EXACT_CALENDAR_COUNTS["prewindow_session_count"]
)
TRAIN_DEVELOPMENT_SESSION_COUNT = int(
    materializer.EXACT_CALENDAR_COUNTS["development_session_count"]
)
TRAIN_ALL_MARKET_SESSION_COUNT = int(
    materializer.EXACT_CALENDAR_COUNTS["all_market_session_count"]
)

# Explicit: research stage does not pull next-day market for this train freeze.
DAILY_INCREMENTAL_SYNC_REQUIRED = False
AUTOMATIC_TRADING_ALLOWED = goal.AUTOMATIC_TRADING_ALLOWED  # must stay False

TRAIN_WINDOW_FREEZE_DOCUMENT = MappingProxyType(
    {
        "schema": TRAIN_WINDOW_FREEZE_SCHEMA,
        "policy_id": TRAIN_WINDOW_FREEZE_POLICY_ID,
        "train_exclusive_end_date": TRAIN_EXCLUSIVE_END_DATE,
        "train_inclusive_session_start": TRAIN_INCLUSIVE_SESSION_START,
        "train_inclusive_session_end": TRAIN_INCLUSIVE_SESSION_END,
        "prewindow_session_count": TRAIN_PREWINDOW_SESSION_COUNT,
        "development_session_count": TRAIN_DEVELOPMENT_SESSION_COUNT,
        "all_market_session_count": TRAIN_ALL_MARKET_SESSION_COUNT,
        "frozen_development_sessions_sha256": (
            materializer.FROZEN_DEVELOPMENT_SESSIONS_SHA256
        ),
        "daily_incremental_sync_required": DAILY_INCREMENTAL_SYNC_REQUIRED,
        "automatic_trading_allowed": AUTOMATIC_TRADING_ALLOWED,
        "embargo_consumed": False,
        "final_oos_consumed": False,
        "notes": (
            "Training uses sealed history ending 2026-07-03 (before 2026-08-01). "
            "No requirement to append each prior trading day after the freeze. "
            "Strategy research only; never auto-trade."
        ),
    }
)


class TrainWindowFreezeError(ValueError):
    """Raised when a date or calendar violates the train-window freeze."""


def assert_train_window_freeze_consistent() -> None:
    if TRAIN_INCLUSIVE_SESSION_END >= TRAIN_EXCLUSIVE_END_DATE:
        raise TrainWindowFreezeError(
            "train inclusive session end must be before exclusive end "
            f"{TRAIN_EXCLUSIVE_END_DATE}"
        )
    if materializer.FROZEN_DEVELOPMENT_SESSION_END != TRAIN_INCLUSIVE_SESSION_END:
        raise TrainWindowFreezeError(
            "materializer frozen development end drifted from train freeze"
        )
    if materializer.FROZEN_DEVELOPMENT_SESSION_START != TRAIN_INCLUSIVE_SESSION_START:
        raise TrainWindowFreezeError(
            "materializer frozen development start drifted from train freeze"
        )
    expected_counts = {
        "prewindow_session_count": TRAIN_PREWINDOW_SESSION_COUNT,
        "development_session_count": TRAIN_DEVELOPMENT_SESSION_COUNT,
        "all_market_session_count": TRAIN_ALL_MARKET_SESSION_COUNT,
        "source_date_count": TRAIN_ALL_MARKET_SESSION_COUNT - 1,
    }
    if dict(materializer.EXACT_CALENDAR_COUNTS) != expected_counts:
        raise TrainWindowFreezeError(
            f"exact calendar counts drifted: {dict(materializer.EXACT_CALENDAR_COUNTS)}"
        )
    if DAILY_INCREMENTAL_SYNC_REQUIRED is not False:
        raise TrainWindowFreezeError("daily incremental sync must remain disabled")
    if AUTOMATIC_TRADING_ALLOWED is not False:
        raise TrainWindowFreezeError("automatic trading must remain disabled")


def assert_session_date_in_train_window(session_date: str, *, label: str) -> str:
    if type(session_date) is not str or len(session_date) != 10:
        raise TrainWindowFreezeError(f"{label} must be YYYY-MM-DD")
    if session_date >= TRAIN_EXCLUSIVE_END_DATE:
        raise TrainWindowFreezeError(
            f"{label}={session_date} is on/after train exclusive end "
            f"{TRAIN_EXCLUSIVE_END_DATE}"
        )
    return session_date


def assert_session_dates_in_train_window(
    dates: list[str] | tuple[str, ...],
    *,
    label: str,
) -> list[str]:
    if not dates:
        raise TrainWindowFreezeError(f"{label} must be non-empty")
    out = [assert_session_date_in_train_window(d, label=label) for d in dates]
    if out != sorted(out):
        raise TrainWindowFreezeError(f"{label} must be strictly sorted")
    if len(set(out)) != len(out):
        raise TrainWindowFreezeError(f"{label} must not contain duplicates")
    if out[-1] > TRAIN_INCLUSIVE_SESSION_END:
        raise TrainWindowFreezeError(
            f"{label} last session {out[-1]} exceeds sealed train end "
            f"{TRAIN_INCLUSIVE_SESSION_END}"
        )
    return out


def train_window_freeze_descriptor() -> dict[str, object]:
    assert_train_window_freeze_consistent()
    return dict(TRAIN_WINDOW_FREEZE_DOCUMENT)


__all__ = [
    "AUTOMATIC_TRADING_ALLOWED",
    "DAILY_INCREMENTAL_SYNC_REQUIRED",
    "TRAIN_ALL_MARKET_SESSION_COUNT",
    "TRAIN_DEVELOPMENT_SESSION_COUNT",
    "TRAIN_EXCLUSIVE_END_DATE",
    "TRAIN_INCLUSIVE_SESSION_END",
    "TRAIN_INCLUSIVE_SESSION_START",
    "TRAIN_PREWINDOW_SESSION_COUNT",
    "TRAIN_WINDOW_FREEZE_DOCUMENT",
    "TRAIN_WINDOW_FREEZE_POLICY_ID",
    "TRAIN_WINDOW_FREEZE_SCHEMA",
    "TrainWindowFreezeError",
    "assert_session_date_in_train_window",
    "assert_session_dates_in_train_window",
    "assert_train_window_freeze_consistent",
    "train_window_freeze_descriptor",
]
