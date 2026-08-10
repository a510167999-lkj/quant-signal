from __future__ import annotations

from pathlib import Path

import pytest

from app import factor_v3_path_a_p2_small_variants as p2
from app import factor_v3_path_a_p2_small_variants_specs as specs


def test_variant_catalog_capped_at_four() -> None:
    """Catalog §4.2: at most 4 variants; adding more requires a new version."""

    ids = [v["candidate_id"] for v in specs.iter_p2_variants()]
    assert len(ids) <= 4
    assert ids[0] == "e4_primary"
    assert len(ids) == len(set(ids))
    # the three declared changes must be present
    assert "p2_fav_only" in ids
    assert "p2_vol_target_10" in ids
    assert "p2_fav_only_plus_p0_best" in ids


def test_vol_target_constants_locked() -> None:
    # Catalog §4.3: lookback fixed, not searchable.
    assert specs.VOL_TARGET_LOOKBACK_DAYS == 20
    assert specs.VOL_TARGET_MIN_SCALE == 0.25
    assert specs.VOL_TARGET_MAX_SCALE == 1.0


def test_vol_target_scale_uses_only_past_returns() -> None:
    """The scale at date D must use only returns strictly before D."""

    # build a synthetic curve with known daily returns
    curve = [
        {"event_date": f"2025-01-{d:02d}", "return_pct": r}
        for d, r in enumerate([1.0, -1.0, 2.0, -2.0, 1.0, 0.5, -0.5, 3.0, -3.0, 1.0,
                               0.0, 2.0, -1.0, 1.5, -0.5, 2.0, -2.0, 1.0, 0.5, -1.0,
                               2.0], start=1)
    ]
    scales = p2._vol_target_scale_series(
        curve,
        lookback_days=20,
        annual_target_pct=10.0,
        min_scale=0.25,
        max_scale=1.0,
    )
    # first entry has no past => most conservative (min_scale)
    assert scales["2025-01-01"] == 0.25
    # all scales within bounds
    assert all(0.25 <= s <= 1.0 for s in scales.values())
    # 21st date has exactly 20 past returns => scale computed (not the floor)
    assert scales["2025-01-21"] > 0.25


def test_requires_local_research(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VPS_RUNTIME_ROLE", raising=False)
    with pytest.raises(p2.PathAP2Error):
        p2.build_path_a_p2_small_variants(repo_root=tmp_path)


def test_p2_on_real_workspace_if_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VPS_RUNTIME_ROLE", "local_research")
    repo = Path(__file__).resolve().parents[1]
    if not (repo / p2.DEFAULT_FROZEN_CANDIDATE).is_file():
        pytest.skip("frozen candidate missing")
    if not (repo / p2.DEFAULT_QUALIFIED_TRADES).is_file():
        pytest.skip("qualified trades missing")
    report = p2.build_path_a_p2_small_variants(repo_root=repo)
    assert report["ok"] is True
    assert report["refit"] is False
    assert report["parameter_search"] is False
    assert report["meets_user_requirement_as_guarantee"] is False
    assert report["automatic_trading_allowed"] is False
    # signal kernel tags unchanged across all variants
    base_kernel = report["signal_kernel"]
    for v in report["variants"]:
        assert v["applied_kernel"]["required_signal_tags"] == base_kernel["required_signal_tags"]
        assert v["applied_kernel"]["excluded_signal_tags"] == base_kernel["excluded_signal_tags"]
        assert v["applied_kernel"]["top_n"] == base_kernel["top_n"]
        assert v["applied_kernel"]["hold_days"] == base_kernel["hold_days"]
    # P1-A scoreboard present and complete on all variants
    assert report["p1a_acceptance"]["all_complete"] is True
    # vol_target variant must carry per-trade scale annotations
    vt = next(v for v in report["variants"] if v["candidate_id"] == "p2_vol_target_10")
    assert "vol_target applied" in vt.get("note", "")
