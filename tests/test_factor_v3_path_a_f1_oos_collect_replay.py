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
        generate_qualified=False,
    )
    assert report["ok"] is True
    assert report["effective_strategy_found"] is False
    assert report["automatic_trading_allowed"] is False
    assert report["formal_final_oos_executable"] is False
    assert report["formal_final_oos_still_sealed"] is True
    assert report["evaluate_policy"]["refit_allowed"] is False
    assert report["frozen_candidate"]["candidate_id"].startswith("e4_primary_")
    oos_n = int((report.get("oos_qualified_audit") or {}).get("oos_trade_count") or 0)
    codes = {b["code"] for b in report["blockers"]}
    if oos_n == 0:
        assert "oos_qualified_trades_missing" in codes
    else:
        assert "oos_qualified_trades_missing" not in codes
        assert report["zero_refit_replay"]["executed"] is True


def test_oos_trade_inventory_counts_frozen_prefilter() -> None:
    trades = [
        {
            "signal_date": "2026-07-10",
            "market_level": "favorable",
            "signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        },
        {
            "signal_date": "2026-07-11",
            "market_level": "cautious",
            "signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        },
        {
            "signal_date": "2026-07-12",
            "market_level": "favorable",
            "signal_tags": ["breadth_ma20_gte_60", "breakout_20d", "price_gap_down"],
        },
    ]
    spec = {
        "market_levels": ["favorable", "neutral"],
        "required_signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        "excluded_signal_tags": ["price_gap_down"],
    }
    inv = f1._oos_trade_inventory(trades, spec)
    assert inv["oos_trade_count"] == 3
    assert inv["count_matching_market_levels"] == 2  # two favorable
    assert inv["count_matching_frozen_spec_prefilter"] == 1  # exclude gap_down
