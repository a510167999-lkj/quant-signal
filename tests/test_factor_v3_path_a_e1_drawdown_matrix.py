from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_e1_drawdown_matrix as e1


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(e1.PathAE1Error):
        e1.build_path_a_e1_drawdown_matrix(repo_root=tmp_path)


def test_e1_smoke_limited_variants(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    trades = repo / e1.DEFAULT_QUALIFIED_TRADES
    if not trades.is_file():
        pytest.skip("qualified trades missing")
    report = e1.build_path_a_e1_drawdown_matrix(
        repo_root=repo,
        max_variants=2,
    )
    assert report["ok"] is True
    assert report["tcb_used"] is False
    assert report["development_only"] is True
    assert report["automatic_trading_allowed"] is False
    assert report["variant_count"] == 2
    assert report["scored_count"] >= 1
