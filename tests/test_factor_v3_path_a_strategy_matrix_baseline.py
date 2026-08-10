from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_strategy_matrix_baseline as path_a


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(path_a.PathAStrategyMatrixError):
        path_a.build_path_a_strategy_matrix_baseline(repo_root=tmp_path)


def test_baseline_on_real_evaluation_if_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    artifact = repo / path_a.DEFAULT_EVALUATION_ARTIFACT
    if not artifact.is_file():
        pytest.skip("factor v2 evaluation artifact not present")
    report = path_a.build_path_a_strategy_matrix_baseline(repo_root=repo)
    assert report["ok"] is True
    assert report["path"] == "A_no_tcb_development_matrix"
    assert report["tcb_used"] is False
    assert report["development_only"] is True
    assert report["automatic_trading_allowed"] is False
    assert report["effective_strategy_found"] is False
    assert len(report["arm_scores"]) >= 1
    # Current sealed baseline: no arm passes both targets.
    assert report["any_arm_passes_50_15"] is False
