"""Personal small-capital book. Separate from the 26/15 research line.

Not a production profile. Not formally effective. No automatic orders.
"""

from __future__ import annotations

from app import research_goal_contract as goal

PERSONAL_BOOK_SCHEMA = "personal-capital-book/v1"
STAGE_GOAL_ID = "personal-capital-book/v1"
CANDIDATE_ID = "bounce_dn2_negext"

MIN_CAPITAL_CNY = 100_000.0
MAX_CAPITAL_CNY = 500_000.0
DEFAULT_CAPITAL_CNY = 200_000.0
POSITION_FRACTION = 0.5
LOT_SIZE = 100
MIN_NOTIONAL_CNY = 10_000.0
COMMISSION_RATE = 0.00025
MIN_COMMISSION_CNY = 5.0
STAMP_TAX_SELL = 0.0005
SHANGHAI_TRANSFER_RATE = 0.00001
PAUSE_DRAWDOWN_PCT = 10.0
FLATTEN_DRAWDOWN_PCT = 15.0
MAX_NEW_OPENS_PER_DAY = 1
LIVE_HAIRCUT = 0.5

AUTOMATIC_TRADING_ALLOWED = goal.AUTOMATIC_TRADING_ALLOWED
EFFECTIVE_STRATEGY = False
PRODUCTION_PROFILE = False


def clamp_capital(capital_cny: float) -> float:
    value = float(capital_cny)
    if value < MIN_CAPITAL_CNY or value > MAX_CAPITAL_CNY:
        raise ValueError(
            f"personal capital must be in [{MIN_CAPITAL_CNY:.0f}, {MAX_CAPITAL_CNY:.0f}]"
        )
    return value
