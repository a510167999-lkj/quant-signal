from __future__ import annotations

from app import research_goal_contract as goal


def test_research_goal_freezes_jiaoch_hs_targets_and_exclusions() -> None:
    descriptor = goal.research_goal_descriptor()
    assert descriptor["schema"] == goal.RESEARCH_GOAL_SCHEMA
    assert descriptor["data_source_policy"] == "jiaoch_only"
    assert descriptor["downstream_eligible_segments"] == [
        "SSE_MAIN",
        "SZSE_CHINEXT",
        "SZSE_MAIN",
    ]
    assert descriptor["downstream_excluded_segments"] == ["BSE", "SSE_STAR"]
    assert descriptor["downstream_exclude_st"] is True
    assert descriptor["downstream_exclude_delisted"] is True
    assert "ST" in descriptor["downstream_excluded_name_tokens"]
    assert descriptor["target_rolling_12m_net_return_pct"] == 26.0
    assert descriptor["target_max_drawdown_pct"] == 15.0
    assert descriptor["automatic_trading_allowed"] is False
    assert descriptor["max_daily_recommendations"] == 3
    assert "主板" in goal.RESEARCH_GOAL_SUMMARY
    assert "创业板" in goal.RESEARCH_GOAL_SUMMARY
    assert "ST" in goal.RESEARCH_GOAL_SUMMARY
    assert "科创" in goal.RESEARCH_GOAL_SUMMARY
    assert "北证" in goal.RESEARCH_GOAL_SUMMARY
    assert "Jiaoch" in goal.RESEARCH_GOAL_SUMMARY or "jiaoch" in goal.DATA_SOURCE_POLICY


def test_downstream_eligibility_and_primary_performance_gate() -> None:
    goal.assert_segment_is_downstream_eligible("SSE_MAIN")
    goal.assert_segment_is_downstream_eligible("SZSE_CHINEXT")
    try:
        goal.assert_segment_is_downstream_eligible("SSE_STAR")
        raise AssertionError("SSE_STAR must be rejected")
    except ValueError as exc:
        assert "SSE_STAR" in str(exc)
    try:
        goal.assert_segment_is_downstream_eligible("BSE")
        raise AssertionError("BSE must be rejected")
    except ValueError as exc:
        assert "BSE" in str(exc)

    assert goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=26.0,
        max_drawdown_pct=15.0,
    )
    assert goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=60.0,
        max_drawdown_pct=-10.0,
    )
    assert not goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=25.9,
        max_drawdown_pct=10.0,
    )
    assert not goal.meets_primary_performance_targets(
        rolling_12m_net_return_pct=55.0,
        max_drawdown_pct=15.1,
    )

    assert goal.name_is_downstream_excluded("ST长油")
    assert goal.name_is_downstream_excluded("*ST海航")
    assert goal.name_is_downstream_excluded("某某退市")
    assert not goal.name_is_downstream_excluded("贵州茅台")
