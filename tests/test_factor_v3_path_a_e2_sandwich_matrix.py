from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_e2_sandwich_matrix as e2


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(e2.PathAE2Error):
        e2.build_path_a_e2_sandwich_matrix(repo_root=tmp_path)


def test_e2_smoke_limited_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / e2.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = e2.build_path_a_e2_sandwich_matrix(repo_root=repo, max_variants=2)
    assert report["ok"] is True
    assert report["tcb_used"] is False
    assert report["development_only"] is True
    assert report["variant_count"] == 2
    assert report["scored_count"] >= 1
