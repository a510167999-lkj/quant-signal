from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_p1_acceptance_protocol as p1a
from app import factor_v3_path_a_p1_walk_forward as wf


# ---------------------------------------------------------------------------
# P1-A acceptance protocol
# ---------------------------------------------------------------------------


def test_w_fields_locked() -> None:
    # Catalog §3.2 P1-A mandates these fields; changing the set is a version bump.
    assert "W1_worst_rolling_return_pct" in p1a.W_FIELDS
    assert "W2_worst_rolling_mdd_pct" in p1a.W_FIELDS
    assert "W3_both_pass_rate" in p1a.W_FIELDS
    assert "W5_latest_rolling_pass_50_15" in p1a.W_FIELDS
    assert any(f.startswith("W6_") for f in p1a.W_FIELDS)


def test_build_w_scoreboard_projects_metrics() -> None:
    metrics = {
        "portfolio_compounded_return_pct": 55.0,
        "portfolio_max_drawdown_pct": -12.0,
        "rolling_1y_latest_return_pct": 55.0,
        "rolling_1y_latest_max_drawdown_pct": -12.0,
        "rolling_12m_summary": {
            "min_return_pct": 10.0,
            "worst_mdd_pct": -18.0,
            "both_pass_rate": 0.4,
            "return_pass_rate": 0.6,
            "drawdown_pass_rate": 0.5,
        },
    }
    board = p1a.build_w_scoreboard(metrics)
    assert board["W1_worst_rolling_return_pct"] == 10.0
    assert board["W2_worst_rolling_mdd_pct"] == -18.0
    assert board["W3_both_pass_rate"] == 0.4
    assert board["W5_latest_rolling_pass_50_15"] is True
    assert board["W6_full_path_mdd_pass_15"] is True


def test_validate_variants_flags_missing() -> None:
    complete = {"scoreboard": {f: 1.0 for f in p1a.W_FIELDS}}
    complete["scoreboard"]["W5_latest_rolling_pass_50_15"] = False
    incomplete = {"candidate_id": "x", "scoreboard": {"W3_both_pass_rate": 0.1}}
    res = p1a.validate_variants([complete, incomplete])
    assert res["all_complete"] is False
    assert len(res["violations"]) == 1
    assert res["violations"][0]["candidate_id"] == "x"
    assert "W1_worst_rolling_return_pct" in res["violations"][0]["missing"]


# ---------------------------------------------------------------------------
# P1-B walk-forward fold slicing
# ---------------------------------------------------------------------------


def test_protocol_constants_locked() -> None:
    assert wf.TRAIN_MONTHS == 12
    assert wf.TEST_MONTHS == 3
    assert wf.STEP_MONTHS == 3


def test_folds_non_overlapping_and_offset() -> None:
    # 24 months of trades (2025-01 .. 2026-12); first test fold starts at
    # month 13 (2026-01), then steps by 3 => folds at 2026-01, 2026-04,
    # 2026-07, 2026-10.
    trades = []
    for m in range(1, 13):
        trades.append({"signal_date": f"2025-{m:02d}-15", "symbol": f"a{m}"})
    for m in range(1, 13):
        trades.append({"signal_date": f"2026-{m:02d}-15", "symbol": f"b{m}"})
    folds = wf.build_walk_forward_folds(trades)
    assert len(folds) == 4
    test_starts = [f["test_window"]["start"] for f in folds]
    assert test_starts == ["2026-01", "2026-04", "2026-07", "2026-10"]
    # test windows do not overlap
    ends = [f["test_window"]["end"] for f in folds]
    for i in range(len(folds) - 1):
        assert ends[i] < test_starts[i + 1]
    # each fold's train window ends the month before its test window
    for f in folds:
        assert f["train_window"]["end"] < f["test_window"]["start"]


def test_folds_empty_on_short_data() -> None:
    # Only 6 months of data -> no test fold can start at month 13.
    trades = [{"signal_date": f"2025-{m:02d}-01", "symbol": str(m)} for m in range(1, 7)]
    assert wf.build_walk_forward_folds(trades) == []


def test_wif_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / wf.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    if not (repo / wf.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = wf.build_path_a_p1_walk_forward(repo_root=repo)
    assert report["ok"] is True
    assert report["refit"] is False
    assert report["parameter_search"] is False
    assert report["meets_user_requirement_as_guarantee"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    # protocol invariants
    proto = report["protocol"]
    assert proto["test_fold_zero_refit"] is True
    assert proto["train_fold_search_allowed"] is False
    assert proto["non_overlapping_test_segments"] is True
    assert report["fold_count"] >= 1
    # each fold carries a W scoreboard
    for f in report["folds"]:
        assert "W3_both_pass_rate" in f["scoreboard"]
