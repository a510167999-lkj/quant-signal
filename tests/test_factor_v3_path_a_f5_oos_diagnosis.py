from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_f5_oos_diagnosis as f5


def test_favorable_conditions_check() -> None:
    # all 4 conditions met => favorable
    breadth_full = {
        "above_ma20_pct": 70, "above_ma60_pct": 65,
        "return_20d_positive_pct": 65, "median_return_20d_pct": 5,
    }
    res = f5._favorable_conditions_met(breadth_full)
    assert res["met_count"] == 4
    assert res["conditions"]["above_ma20_pct"]["passed"] is True

    # missing 1 => near-miss
    breadth_miss1 = {**breadth_full, "above_ma20_pct": 60}  # < 65
    res2 = f5._favorable_conditions_met(breadth_miss1)
    assert res2["met_count"] == 3
    assert res2["conditions"]["above_ma20_pct"]["passed"] is False


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(f5.PathAF5Error):
        f5.build_path_a_f5_oos_diagnosis(repo_root=tmp_path)


def test_f5_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / f5.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    if not (repo / f5.DEFAULT_OOS_QUALIFIED).is_file():
        pytest.skip("OOS qualified trades missing")
    report = f5.build_path_a_f5_oos_diagnosis(repo_root=repo)
    assert report["ok"] is True
    assert report["refit"] is False
    assert report["parameter_search"] is False
    assert report["meets_user_requirement_as_guarantee"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    # diagnosis must carry the key sections
    assert "market_level_distribution_by_day" in report
    assert "frozen_rule_prefilter" in report
    assert "counterfactual_include_cautious" in report
    assert "favorable_threshold_sensitivity" in report
    assert "diagnosis_conclusion" in report
    # frozen rule allowed levels must be {favorable, neutral}
    pre = report["frozen_rule_prefilter"]
    assert set(pre["allowed_levels"]) == {"favorable", "neutral"}
    # counterfactual must include cautious
    cf = report["counterfactual_include_cautious"]
    assert "cautious" in cf["allowed_levels"]
    # favorable conditions must be 4 (the frozen AND-set)
    sens = report["favorable_threshold_sensitivity"]
    assert len(sens["conditions"]) == 4
