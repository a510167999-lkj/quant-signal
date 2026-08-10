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
