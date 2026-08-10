from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_f2_oos_extend_replay as f2


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(f2.PathAF2Error):
        f2.build_path_a_f2_oos_extend_replay(repo_root=tmp_path)


def test_f2_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "data/research_partitions/path-a-shadow-post-train-oos-v1.json").is_file():
        pytest.skip("shadow contract missing")
    if not (repo / "data/research_runs/path_a_f0_oos_readiness/FROZEN_CANDIDATE.json").is_file():
        pytest.skip("frozen candidate missing")
    # Do not hit network or regenerate expensive QT in unit test.
    report = f2.build_path_a_f2_oos_extend_replay(
        repo_root=repo,
        extend=False,
        generate_qualified=False,
    )
    assert report["ok"] is True
    assert report["effective_strategy_found"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    assert report["formal_final_oos_still_sealed"] is True
    assert report["evaluate_policy"]["refit_allowed"] is False
    assert "comparison_to_f1" in report
