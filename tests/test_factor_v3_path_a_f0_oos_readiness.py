from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_f0_oos_readiness as f0


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(f0.PathAF0Error):
        f0.build_path_a_f0_oos_readiness(repo_root=tmp_path)


def test_f0_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / "data/research_partitions/frozen-v1.json").is_file():
        pytest.skip("frozen-v1 missing")
    if not (repo / f0.DEFAULT_E4_POINTER).is_file():
        pytest.skip("E4 pointer missing")
    report = f0.build_path_a_f0_oos_readiness(repo_root=repo)
    assert report["ok"] is True
    assert report["effective_strategy_found"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    codes = {b["code"] for b in report["blockers"]}
    assert "final_oos_sealed_no_ops" in codes
    assert "no_final_oos_trade_rows" in codes
    assert report["frozen_primary_candidate"]["candidate_id"].startswith("e4_primary_")
