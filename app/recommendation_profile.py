"""Canonical strategy contract for the stock-operation-advice surface."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, Tuple


@dataclass(frozen=True)
class RecommendationProfile:
    profile_id: str = "primary_50_return_15_drawdown"
    version: str = "v1"
    target_annualized_return_pct: float = 50.0
    max_drawdown_pct: float = 15.0
    min_win_rate_pct: float = 52.0
    max_win_rate_pct: float = 60.0
    min_win_rate_wilson_lower_pct: float = 52.0
    min_payoff_ratio: float = 1.3
    min_profit_factor: float = 1.3
    min_calmar: float = 1.5
    min_signal_days: int = 120
    max_recommendations: int = 3
    market_scope: Tuple[str, ...] = ("a",)
    required_signal_tags: Tuple[str, ...] = (
        "breadth_advancing_gte_50",
        "breakout_20d",
    )
    allowed_market_levels: Tuple[str, ...] = ("favorable", "neutral")
    roundtrip_cost_bps: float = 25.0
    slippage_bps: float = 10.0
    evidence_scope: str = "development_only"
    auto_order: bool = False


DEFAULT_PROFILE = RecommendationProfile()


def _canonical_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "profile_hash"}


def _profile_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(
        _canonical_payload(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _normalise(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, list):
        return list(value)
    return value


def profile_to_dict(profile: RecommendationProfile = DEFAULT_PROFILE) -> Dict[str, Any]:
    payload = {key: _normalise(value) for key, value in asdict(profile).items()}
    payload["profile_hash"] = _profile_hash(payload)
    return payload


def _require_equal(payload: Dict[str, Any], key: str, expected: Any) -> None:
    actual = _normalise(payload.get(key))
    expected_value = _normalise(expected)
    if actual != expected_value:
        raise ValueError("%s must equal the registered strategy profile" % key)


def validate_profile(payload: Dict[str, Any]) -> RecommendationProfile:
    """Validate a serialized profile and reject any weakened safety boundary."""
    if not isinstance(payload, dict):
        raise ValueError("profile must be an object")

    baseline = profile_to_dict(DEFAULT_PROFILE)
    missing_fields = [key for key in baseline if key not in payload]
    if missing_fields:
        raise ValueError("profile fields missing: %s" % ",".join(sorted(missing_fields)))
    for key in ("profile_id", "version", "market_scope", "required_signal_tags", "allowed_market_levels"):
        _require_equal(payload, key, baseline[key])

    numeric_minimums = {
        "target_annualized_return_pct": baseline["target_annualized_return_pct"],
        "min_win_rate_pct": baseline["min_win_rate_pct"],
        "min_win_rate_wilson_lower_pct": baseline["min_win_rate_wilson_lower_pct"],
        "min_payoff_ratio": baseline["min_payoff_ratio"],
        "min_profit_factor": baseline["min_profit_factor"],
        "min_calmar": baseline["min_calmar"],
        "min_signal_days": baseline["min_signal_days"],
    }
    for key, minimum in numeric_minimums.items():
        try:
            value = float(payload[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("%s must be numeric" % key) from exc
        if value < minimum:
            raise ValueError("%s cannot weaken the registered strategy profile" % key)

    if float(payload.get("max_drawdown_pct", 0)) > baseline["max_drawdown_pct"]:
        raise ValueError("max_drawdown_pct cannot weaken the registered strategy profile")
    if float(payload.get("max_win_rate_pct", 0)) > baseline["max_win_rate_pct"]:
        raise ValueError("max_win_rate_pct cannot weaken the registered strategy profile")
    if int(payload.get("max_recommendations", 0)) > baseline["max_recommendations"]:
        raise ValueError("max_recommendations cannot exceed the daily advice cap")
    _require_equal(payload, "roundtrip_cost_bps", baseline["roundtrip_cost_bps"])
    _require_equal(payload, "slippage_bps", baseline["slippage_bps"])
    _require_equal(payload, "evidence_scope", baseline["evidence_scope"])
    _require_equal(payload, "auto_order", False)

    supplied_hash = str(payload.get("profile_hash") or "")
    if supplied_hash != _profile_hash(payload):
        raise ValueError("profile_hash does not match the profile payload")

    values = {key: _normalise(payload.get(key)) for key in asdict(DEFAULT_PROFILE)}
    values["market_scope"] = tuple(values["market_scope"])
    values["required_signal_tags"] = tuple(values["required_signal_tags"])
    values["allowed_market_levels"] = tuple(values["allowed_market_levels"])
    return RecommendationProfile(**values)


def profile_required_tags(profile: RecommendationProfile = DEFAULT_PROFILE) -> Iterable[str]:
    return profile.required_signal_tags
