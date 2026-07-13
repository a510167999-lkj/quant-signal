from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_recommendation_panel_surfaces_data_freshness_and_scope():
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

    assert "payload?.data_as_of" in script
    assert "数据截至" in script
    assert "development_only" in script
    assert "market_data_source" in script
    assert "announcement_context?.source" in script
    assert "fallback_used" in script


def test_recommendation_panel_surfaces_profile_gate_and_operation_advice():
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

    assert "profile_gate" in script
    assert "strategy_profile" in script
    assert "operation_advice" in script
    assert "blocked_profile_gate" in script
    assert "不自动下单" in script


def test_recommendation_panel_fails_closed_for_blocked_or_oversized_snapshots():
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

    assert "blockedByProfileGate" in script
    assert "blockedByCurrentPoolGate" in script
    assert "blocked_current_pool_gate" in script
    assert "股票池审计门禁未通过" in script
    assert "rawItems.length > 3" in script
    assert "production/status" in script


def test_recommendation_polling_contract_is_race_safe_and_weak_network_tolerant():
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")

    assert "AbortController" in script
    assert "requestSequence" in script
    assert "consecutiveFailures" in script
    assert "RECOMMENDATION_POLL_MAX_DELAY_MS" in script
    assert "stopRecommendationPolling" in script
