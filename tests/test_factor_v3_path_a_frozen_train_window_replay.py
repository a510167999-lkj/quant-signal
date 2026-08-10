from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_frozen_train_window_replay as r


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(r.PathAFrozenTrainReplayError):
        r.build_path_a_frozen_train_window_replay(repo_root=tmp_path)


def test_replay_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / r.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    if not (repo / r.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = r.build_path_a_frozen_train_window_replay(repo_root=repo)
    assert report["ok"] is True
    assert report["refit"] is False
    assert report["fixed_spec"] is True
    assert report["automatic_trading_allowed"] is False
    assert report["effective_strategy_found"] is False
    assert report["verdict"]["meets_user_requirement_as_guarantee"] is False
    assert int(report["rolling_12m_summary"]["window_count"]) > 0
    assert len(report["rolling_12m_windows"]) == int(
        report["rolling_12m_summary"]["window_count"]
    )
    assert report["bias_and_boundary"]["guarantees"]["effective_strategy_certified"] is False
