from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_f3_oos_daily_roll as f3


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(f3.PathAF3Error):
        f3.build_path_a_f3_oos_daily_roll(repo_root=tmp_path)


def test_resolve_oos_end_date_as_of() -> None:
    assert f3.resolve_oos_end_date(as_of="2026-08-12") == "2026-08-12"


def test_f3_skip_path_on_real_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    store = repo / "data/research_pit_store/path_a_oos_market_full/metadata.sqlite3"
    if not store.is_file():
        pytest.skip("OOS store missing")
    # Skip work: store already covers a far past as-of.
    report = f3.build_path_a_f3_oos_daily_roll(
        repo_root=repo,
        as_of="2026-07-06",
        extend=False,
        generate_qualified=False,
        skip_if_no_new_session=True,
    )
    assert report["ok"] is True
    assert report["skipped"] is True
    assert report["automatic_trading_allowed"] is False
    assert report["effective_strategy_found"] is False
    assert report["formal_final_oos_executable"] is False


def test_market_level_snapshot_missing_file(tmp_path: Path) -> None:
    snap = f3._oos_market_level_snapshot(tmp_path / "nonexistent.json")
    assert snap["present"] is False
    assert snap["frozen_rule_pass_trades"] == 0


def test_market_level_snapshot_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    qt_path = repo / "data/research_cache/path_a_oos/qualified_hold5_stop5_oos.json"
    if not qt_path.is_file():
        pytest.skip("OOS qualified trades missing")
    snap = f3._oos_market_level_snapshot(qt_path)
    assert snap["present"] is True
    assert isinstance(snap["by_trade"], dict)
    assert snap["frozen_rule_pass_trades"] >= 0
    # all keys in by_trade are known levels
    for lvl in snap["by_trade"]:
        assert lvl in ("favorable", "neutral", "cautious", "defensive", "unknown")

