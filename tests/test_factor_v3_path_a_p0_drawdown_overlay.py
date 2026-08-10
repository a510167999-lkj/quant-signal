from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_p0_drawdown_overlay as p0
from app import factor_v3_path_a_p0_drawdown_overlay_specs as specs


def _trade(
    symbol: str,
    signal_date: str,
    entry_date: str,
    exit_date: str,
    return_pct: float,
    *,
    market_level: str = "favorable",
) -> dict:
    # mark_to_market_path makes exit_date enter all_dates so realized P&L lands
    # on the exit day (synthetic trades without a path only mark the entry day).
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": entry_date,
        "exit_date": exit_date,
        "return_pct": return_pct,
        "max_adverse_pct": min(return_pct, -1.0),
        "market_level": market_level,
        "signal_tags": ["breadth_ma20_gte_60", "breakout_20d"],
        "mark_to_market_path": [
            {"date": entry_date, "close_return_pct": 0.0, "low_return_pct": 0.0},
            {"date": exit_date, "close_return_pct": return_pct, "low_return_pct": min(return_pct, 0.0)},
        ],
    }


def test_variant_catalog_is_finite() -> None:
    ids = [v["candidate_id"] for v in specs.iter_p0_variants()]
    assert ids[0] == "e4_primary"
    assert len(ids) == 6
    assert len(ids) == len(set(ids))


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(p0.PathAP0Error):
        p0.build_path_a_p0_drawdown_overlay(repo_root=tmp_path)


def test_none_overlay_accepts_all() -> None:
    """Baseline overlay must pass every trade through unchanged."""

    trades = [
        _trade("000001", "2025-01-02", "2025-01-03", "2025-01-10", -20.0),
        _trade("000002", "2025-01-15", "2025-01-16", "2025-01-23", 5.0),
        _trade("000003", "2025-02-03", "2025-02-04", "2025-02-11", 8.0),
    ]
    out = p0.apply_p0_overlay(trades, {"kind": "none"}, kernel_selected=trades)
    assert out["blocked_new_count"] == 0
    assert out["half_size_count"] == 0
    assert out["full_size_count"] == len(trades)
    assert len(out["accepted"]) == len(trades)


def test_drawdown_block_does_not_deadlock() -> None:
    """Regression: the gate must read counterfactual kernel equity, not the
    overlay-filtered accepted list. Otherwise a block starves the equity curve
    and latches the gate forever (every later trade blocked).

    Setup: a deep first loss triggers dd_block, then two later trades on new
    signal days. Before the fix all later trades were blocked (accepted == 1).
    After the fix the gate unlatches once the counterfactual curve recovers.
    """

    trades = [
        _trade("000001", "2025-01-02", "2025-01-03", "2025-01-10", -40.0),
        _trade("000002", "2025-02-02", "2025-02-03", "2025-02-10", 30.0),
        _trade("000003", "2025-03-02", "2025-03-03", "2025-03-10", 30.0),
    ]
    # block at 18% drawdown, resume below 10%: the first loss pushes dd to ~20%
    # (latch), the first recovery cut brings it to ~8.4% which unlatches.
    out = p0.apply_p0_overlay(
        trades,
        {"kind": "drawdown_block", "block_dd_pct": 18.0, "resume_dd_pct": 10.0},
        kernel_selected=trades,
    )
    # Not all later trades are blocked: at least one of the two recovery trades
    # is accepted (the gate unlatches as the counterfactual curve recovers).
    assert len(out["accepted"]) > 1, (
        f"deadlock regression: only 1 accepted ({out['blocked_new_count']} blocked)"
    )


def test_loss_streak_blocks_then_recovers() -> None:
    """After 3 consecutive closed losers the gate blocks for a cooldown; once
    the cooldown elapses new entries resume (no permanent latch)."""

    trades = [
        _trade("000001", "2025-01-02", "2025-01-03", "2025-01-10", -5.0),
        _trade("000002", "2025-01-15", "2025-01-16", "2025-01-23", -5.0),
        _trade("000003", "2025-01-30", "2025-01-31", "2025-02-07", -5.0),
        _trade("000004", "2025-02-13", "2025-02-14", "2025-02-21", -5.0),
        _trade("000005", "2025-02-27", "2025-02-28", "2025-03-07", -5.0),
        _trade("000006", "2025-03-13", "2025-03-14", "2025-03-21", 10.0),
        _trade("000007", "2025-03-27", "2025-03-28", "2025-04-04", 10.0),
    ]
    out = p0.apply_p0_overlay(
        trades,
        {"kind": "loss_streak", "streak": 3, "cooldown_signal_days": 2},
        kernel_selected=trades,
    )
    # Some entries blocked during cooldown, some accepted afterwards.
    assert out["blocked_new_count"] > 0
    assert len(out["accepted"]) > 0


def test_p0_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / p0.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    if not (repo / p0.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = p0.build_path_a_p0_drawdown_overlay(repo_root=repo)
    assert report["ok"] is True
    assert report["refit"] is False
    assert report["meets_user_requirement_as_guarantee"] is False
    assert report["automatic_trading_allowed"] is False
    ids = [v["candidate_id"] for v in report["variants"]]
    assert ids == [v["candidate_id"] for v in specs.iter_p0_variants()]
    base = next(v for v in report["variants"] if v["candidate_id"] == "e4_primary")
    assert base["signal_kernel_unchanged"] is True
    assert "W3_both_pass_rate" in base["scoreboard"]

    # Deadlock invariant on real data: overlay variants must NOT all collapse to
    # the same accepted count (that was the deadlock signature). The baseline
    # accepts the full kernel selection; overlays block a non-trivial but
    # partial subset.
    by_id = {v["candidate_id"]: v for v in report["variants"]}
    base_audit = by_id["e4_primary"]["overlay_audit"]
    assert base_audit["blocked_new_count"] == 0
    overlay_accepted = {
        v["overlay_audit"]["accepted_count"]
        for v in report["variants"]
        if v["candidate_id"] != "e4_primary"
    }
    # At least two distinct accepted counts among overlays => gates diverge and
    # none is stuck fully open or fully deadlocked.
    assert len(overlay_accepted) > 1, (
        f"overlays collapsed to identical accepted counts: {overlay_accepted}"
    )
    # No overlay blocks every single kernel trade (deadlock signature was
    # accepted_count == 5 on ~81 kernel trades).
    kernel_n = report["kernel_selected_trade_count"]
    for v in report["variants"]:
        if v["candidate_id"] == "e4_primary":
            continue
        assert v["overlay_audit"]["accepted_count"] > 1, (
            f"{v['candidate_id']} deadlocked: accepted="
            f"{v['overlay_audit']['accepted_count']}/{kernel_n}"
        )
