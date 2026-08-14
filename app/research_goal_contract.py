"""Frozen research goal and market-scope contract for quant-signal-lkj.

Single source of truth for the personal research target. Code that gates
advancement, materialization eligibility, or production-profile registration
must not silently widen these bounds.
"""

from __future__ import annotations

from types import MappingProxyType

# --- Mission ---
RESEARCH_GOAL_SCHEMA = "quant-signal-lkj-research-goal/v1"
RESEARCH_GOAL_SUMMARY = (
    "基于 Jiaoch（Tushare 镜像）迭代一条可复现的沪深 A 股策略："
    "真实成本后滚动 12 个月净年化 ≥30%，最大回撤 ≤15%；"
    "仅沪主板、深主板、创业板；不含 ST、科创板、北证；不自动交易。"
)

# --- Data authority ---
DATA_SOURCE_POLICY = "jiaoch_only"
DATA_SOURCE_DESCRIPTION = (
    "仅允许 Jiaoch（Tushare 镜像站）作为正式研究/训练/回测数据源；"
    "禁止未授权混入其它行情源作为正式证据。"
)

# --- Market scope (downstream tradeable) ---
# Upstream may retain full-board ledgers for PIT proof; strategy universe is
# Shanghai/Shenzhen only — main boards + ChiNext. Explicitly exclude BSE, STAR,
# ST / *ST names, and delisting names.
UPSTREAM_SOURCE_SEGMENTS = (
    "BSE",
    "SSE_MAIN",
    "SSE_STAR",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
DOWNSTREAM_ELIGIBLE_SEGMENTS = (
    "SSE_MAIN",
    "SZSE_CHINEXT",
    "SZSE_MAIN",
)
DOWNSTREAM_EXCLUDED_SEGMENTS = (
    "BSE",
    "SSE_STAR",
)
SEGMENT_CLASSIFICATION_CONTRACT = MappingProxyType(
    {
        "BSE": ("suffix", ".BJ"),
        "SSE_MAIN": ("prefix_and_suffix", ("60", ".SH")),
        "SSE_STAR": ("prefix_and_suffix", ("688", ".SH")),
        "SZSE_CHINEXT": ("prefix_and_suffix", ("30", ".SZ")),
        "SZSE_MAIN": ("prefix_and_suffix", ("00", ".SZ")),
    }
)
# Name tokens are matched as substrings against the uppercased display name.
DOWNSTREAM_EXCLUDED_NAME_TOKENS = ("*ST", "ST", "退市", "退")
DOWNSTREAM_EXCLUDE_ST = True
DOWNSTREAM_EXCLUDE_DELISTED = True

# --- Performance acceptance (formal advancement) ---
# Net of declared cost/slippage. Rolling windows are exact-session calendar
# windows on the frozen development/embargo/final-OOS partitions — never mixed.
TARGET_ROLLING_12M_NET_RETURN_PCT = 30.0
TARGET_MAX_DRAWDOWN_PCT = 15.0
# Supporting diagnostics (do not replace the two primary targets above).
TARGET_PROFIT_FACTOR_MIN = 1.3
TARGET_CALMAR_MIN = 1.5
OBSERVED_WIN_RATE_BAND_PCT = (52.0, 60.0)

# --- Lifecycle / safety ---
MAX_DAILY_RECOMMENDATIONS = 3
AUTOMATIC_TRADING_ALLOWED = False
PRODUCTION_PROFILE_REQUIRES = MappingProxyType(
    {
        "data_source_policy": DATA_SOURCE_POLICY,
        "downstream_eligible_segments": DOWNSTREAM_ELIGIBLE_SEGMENTS,
        "downstream_excluded_segments": DOWNSTREAM_EXCLUDED_SEGMENTS,
        "downstream_excluded_name_tokens": DOWNSTREAM_EXCLUDED_NAME_TOKENS,
        "downstream_exclude_st": DOWNSTREAM_EXCLUDE_ST,
        "downstream_exclude_delisted": DOWNSTREAM_EXCLUDE_DELISTED,
        "target_rolling_12m_net_return_pct": TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": TARGET_MAX_DRAWDOWN_PCT,
        "target_profit_factor_min": TARGET_PROFIT_FACTOR_MIN,
        "target_calmar_min": TARGET_CALMAR_MIN,
        "embargo_and_final_oos_isolated": True,
        "automatic_trading_allowed": AUTOMATIC_TRADING_ALLOWED,
    }
)


def research_goal_descriptor() -> dict[str, object]:
    """Canonical, JSON-serializable goal snapshot for receipts and docs."""

    return {
        "schema": RESEARCH_GOAL_SCHEMA,
        "summary": RESEARCH_GOAL_SUMMARY,
        "data_source_policy": DATA_SOURCE_POLICY,
        "data_source_description": DATA_SOURCE_DESCRIPTION,
        "upstream_source_segments": list(UPSTREAM_SOURCE_SEGMENTS),
        "downstream_eligible_segments": list(DOWNSTREAM_ELIGIBLE_SEGMENTS),
        "downstream_excluded_segments": list(DOWNSTREAM_EXCLUDED_SEGMENTS),
        "downstream_excluded_name_tokens": list(DOWNSTREAM_EXCLUDED_NAME_TOKENS),
        "downstream_exclude_st": DOWNSTREAM_EXCLUDE_ST,
        "downstream_exclude_delisted": DOWNSTREAM_EXCLUDE_DELISTED,
        "target_rolling_12m_net_return_pct": TARGET_ROLLING_12M_NET_RETURN_PCT,
        "target_max_drawdown_pct": TARGET_MAX_DRAWDOWN_PCT,
        "target_profit_factor_min": TARGET_PROFIT_FACTOR_MIN,
        "target_calmar_min": TARGET_CALMAR_MIN,
        "observed_win_rate_band_pct": list(OBSERVED_WIN_RATE_BAND_PCT),
        "max_daily_recommendations": MAX_DAILY_RECOMMENDATIONS,
        "automatic_trading_allowed": AUTOMATIC_TRADING_ALLOWED,
    }


def assert_segment_is_downstream_eligible(segment: str) -> None:
    if segment not in DOWNSTREAM_ELIGIBLE_SEGMENTS:
        raise ValueError(
            f"segment {segment!r} is outside research goal market scope "
            f"(eligible={DOWNSTREAM_ELIGIBLE_SEGMENTS}, "
            f"excluded={DOWNSTREAM_EXCLUDED_SEGMENTS})"
        )


def name_is_downstream_excluded(name: str) -> bool:
    """True when the display name is ST / *ST or delisting-related."""

    upper = str(name or "").upper()
    return any(token in upper for token in DOWNSTREAM_EXCLUDED_NAME_TOKENS)


def meets_primary_performance_targets(
    *,
    rolling_12m_net_return_pct: float,
    max_drawdown_pct: float,
) -> bool:
    """Primary gate: net rolling-12m return and max drawdown only."""

    return (
        float(rolling_12m_net_return_pct) >= TARGET_ROLLING_12M_NET_RETURN_PCT
        and abs(float(max_drawdown_pct)) <= TARGET_MAX_DRAWDOWN_PCT
    )
