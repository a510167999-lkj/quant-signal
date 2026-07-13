import pytest

from app.recommendation_profile import (
    DEFAULT_PROFILE,
    profile_to_dict,
    validate_profile,
)


def test_default_profile_is_the_50pct_15dd_stock_advice_contract():
    profile = DEFAULT_PROFILE

    assert profile.profile_id == "primary_50_return_15_drawdown"
    assert profile.target_annualized_return_pct == 50.0
    assert profile.max_drawdown_pct == 15.0
    assert profile.min_payoff_ratio == 1.3
    assert profile.min_profit_factor == 1.3
    assert profile.min_calmar == 1.5
    assert profile.min_signal_days == 120
    assert profile.max_recommendations == 3
    assert profile.market_scope == ("a",)
    assert profile.auto_order is False


def test_profile_has_stable_hash_and_rejects_weaker_thresholds():
    payload = profile_to_dict(DEFAULT_PROFILE)

    assert len(payload["profile_hash"]) == 64
    weakened = dict(payload)
    weakened["max_drawdown_pct"] = 15.1

    with pytest.raises(ValueError, match="max_drawdown_pct"):
        validate_profile(weakened)


def test_profile_rejects_non_stock_scope_or_execution_capability():
    payload = profile_to_dict(DEFAULT_PROFILE)

    for key, value, message in (
        ("market_scope", ["a", "etf"], "market_scope"),
        ("auto_order", True, "auto_order"),
    ):
        invalid = dict(payload)
        invalid[key] = value
        with pytest.raises(ValueError, match=message):
            validate_profile(invalid)
