"""Fail-closed evidence gate for automatic stock operation advice."""

from __future__ import annotations

import math
from typing import Any, Dict, List

from app.recommendation_profile import DEFAULT_PROFILE, RecommendationProfile


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _require_number(
    metrics: Dict[str, Any], key: str, reasons: List[str], *, minimum: float | None = None, maximum: float | None = None
) -> float | None:
    value = _number(metrics.get(key))
    if value is None:
        reasons.append("%s_missing" % key)
        return None
    if minimum is not None and value < minimum:
        reasons.append("%s_below_min" % key)
    if maximum is not None and value > maximum:
        reasons.append("%s_above_max" % key)
    return value


def _rolling_reasons(profile: RecommendationProfile, windows: Any) -> List[str]:
    if not isinstance(windows, list) or not windows:
        return ["rolling_12m_missing"]
    reasons: List[str] = []
    for index, window in enumerate(windows):
        if not isinstance(window, dict):
            reasons.append("rolling_12m_window_%d_invalid" % index)
            continue
        prefix = "rolling_12m_window_%d" % index
        checks = (
            ("annualized_return_pct", profile.target_annualized_return_pct, None),
            ("max_drawdown_pct", None, profile.max_drawdown_pct),
            ("payoff_ratio", profile.min_payoff_ratio, None),
            ("profit_factor", profile.min_profit_factor, None),
            ("calmar", profile.min_calmar, None),
        )
        for key, minimum, maximum in checks:
            value = _number(window.get(key))
            if value is None:
                reasons.append("%s_%s_missing" % (prefix, key))
            elif minimum is not None and value < minimum:
                reasons.append("%s_%s_below_min" % (prefix, key))
            elif maximum is not None and value > maximum:
                reasons.append("%s_%s_above_max" % (prefix, key))
    return reasons


def evaluate_recommendation_gate(
    profile: RecommendationProfile = DEFAULT_PROFILE,
    metrics: Dict[str, Any] | None = None,
    evidence: Dict[str, Any] | None = None,
    health: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return structured promotion evidence without ever enabling execution."""
    metrics = metrics or {}
    evidence = evidence or {}
    health = health or {}
    reasons: List[str] = []

    _require_number(metrics, "annualized_return_pct", reasons, minimum=profile.target_annualized_return_pct)
    _require_number(metrics, "max_drawdown_pct", reasons, maximum=profile.max_drawdown_pct)
    _require_number(
        metrics,
        "win_rate_pct",
        reasons,
        minimum=profile.min_win_rate_pct,
        maximum=profile.max_win_rate_pct,
    )
    _require_number(
        metrics,
        "win_rate_wilson_lower_pct",
        reasons,
        minimum=profile.min_win_rate_wilson_lower_pct,
    )
    _require_number(metrics, "payoff_ratio", reasons, minimum=profile.min_payoff_ratio)
    _require_number(metrics, "profit_factor", reasons, minimum=profile.min_profit_factor)
    _require_number(metrics, "calmar", reasons, minimum=profile.min_calmar)
    _require_number(metrics, "signal_days", reasons, minimum=profile.min_signal_days)
    reasons.extend(_rolling_reasons(profile, metrics.get("rolling_12m")))

    for key in (
        "pit_contract",
        "temporal_contract",
        "cost_slippage",
        "artifact_execution",
        "strategy_signal_replay",
        "outcome_replay",
    ):
        if evidence.get(key) is not True:
            reasons.append("%s_missing" % key)
    if health.get("ok") is not True:
        reasons.append("production_health_not_ok")
        reasons.extend(str(item) for item in (health.get("reasons") or []))

    unique_reasons = list(dict.fromkeys(reasons))
    development_ready = not unique_reasons
    live_proof = development_ready and all(
        evidence.get(key) is True for key in ("final_oos", "shadow", "live_monitoring")
    )
    return {
        "profile_id": profile.profile_id,
        "profile_version": profile.version,
        "development_ready": development_ready,
        "live_proof": live_proof,
        "evidence_scope": "live_proof" if live_proof else "development_only",
        "reasons": unique_reasons,
        "auto_order": False,
    }
