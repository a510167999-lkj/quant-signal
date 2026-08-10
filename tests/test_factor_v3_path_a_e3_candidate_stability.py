from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_e3_candidate_stability as e3


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(e3.PathAE3Error):
        e3.build_path_a_e3_candidate_stability(repo_root=tmp_path)


def test_e3_on_real_trades_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / e3.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = e3.build_path_a_e3_candidate_stability(repo_root=repo)
    assert report["ok"] is True
    assert report["tcb_used"] is False
    assert report["development_only"] is True
    assert report["effective_strategy_found"] is False
    assert report["oos_authorized"] is False
    assert len(report["candidates"]) == len(e3.FROZEN_CANDIDATES)
    # Current sealed data: latest dual-pass may exist, but rolling stability should fail.
    assert report["any_stability_ready_for_oos_planning"] is False
