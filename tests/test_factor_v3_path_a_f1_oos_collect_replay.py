from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_f1_oos_collect_replay as f1


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(f1.PathAF1Error):
        f1.build_path_a_f1_oos_collect_replay(repo_root=tmp_path)


def test_f1_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / f1.DEFAULT_SHADOW_CONTRACT).is_file():
        pytest.skip("shadow contract missing")
    if not (repo / f1.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    report = f1.build_path_a_f1_oos_collect_replay(
        repo_root=repo,
        collect=False,
    )
    assert report["ok"] is True
    assert report["effective_strategy_found"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    assert report["formal_final_oos_still_sealed"] is True
    assert report["evaluate_policy"]["refit_allowed"] is False
    assert report["frozen_candidate"]["candidate_id"].startswith("e4_primary_")
    codes = {b["code"] for b in report["blockers"]}
    # Without OOS qualified trades this is expected.
    assert "oos_qualified_trades_missing" in codes
