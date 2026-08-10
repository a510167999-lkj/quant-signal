from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_e4_stability_improve as e4


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(e4.PathAE4Error):
        e4.build_path_a_e4_stability_improve(repo_root=tmp_path)


def test_e4_smoke_limited(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / e4.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = e4.build_path_a_e4_stability_improve(repo_root=repo, max_variants=2)
    assert report["ok"] is True
    assert report["tcb_used"] is False
    assert report["development_only"] is True
    assert report["effective_strategy_found"] is False
    assert report["variant_count"] == 2
    assert report["scored_count"] >= 1
