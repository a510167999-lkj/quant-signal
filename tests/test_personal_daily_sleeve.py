from __future__ import annotations

import json
from pathlib import Path

from app import personal_capital_contract as contract
from app import personal_daily_sleeve as sleeve
from app import research_goal_contract as goal
from app.factor_v3_path_a_protocol_signal_specs import iter_protocol_signal_bounce_variants


def _series(symbol: str, closes: list[float], start: str = "2026-07-01") -> dict:
    from datetime import date, timedelta

    day = date.fromisoformat(start)
    records = []
    i = 0
    while len(records) < len(closes):
        if day.weekday() < 5:
            records.append({"date": day.isoformat(), "close": closes[i]})
            i += 1
        day += timedelta(days=1)
    return {"symbol": symbol, "records": records}


def test_identity_is_not_path_a_and_not_effective() -> None:
    assert sleeve.CANDIDATE_ID == "personal_daily_negext_m2"
    assert sleeve.CANDIDATE_ID != "bounce_dn2_negext"
    assert sleeve.EFFECTIVE_STRATEGY is False
    assert sleeve.AUTOMATIC_TRADING_ALLOWED is False
    assert goal.TARGET_ROLLING_12M_NET_RETURN_PCT == 26.0
    assert sleeve.CANDIDATE_ID not in {
        row["candidate_id"] for row in iter_protocol_signal_bounce_variants()
    }


def test_stale_last_bar_is_not_ranked_against_live_session() -> None:
    live = _series("000001", [10.0] * 20 + [9.0], start="2026-07-01")
    stale = _series("600000", [10.0] * 20 + [1.0], start="2026-06-01")
    ranked = sleeve.rank_negext20([live, stale], top_n=2)
    assert [row["symbol"] for row in ranked] == ["000001"]


def test_rank_picks_most_negative_20d_and_skips_star() -> None:
    rows = [
        _series("000001", [10.0] * 20 + [7.0]),
        _series("600000", [10.0] * 20 + [8.0]),
        _series("300001", [10.0] * 20 + [12.0]),
        _series("688001", [10.0] * 20 + [1.0]),
    ]
    ranked = sleeve.rank_negext20(rows, top_n=2)
    assert [row["symbol"] for row in ranked] == ["000001", "600000"]
    assert ranked[0]["ret20"] < ranked[1]["ret20"] < 0


def test_daily_report_has_two_slots_and_rebalance_actions() -> None:
    ranked = [
        {"symbol": "000001", "close": 10.0, "ret20": -0.3, "rank_score": 0.3},
        {"symbol": "600000", "close": 12.0, "ret20": -0.2, "rank_score": 0.2},
    ]
    report = sleeve.build_daily_report(
        ranked,
        previous_symbols=["600000", "300001"],
        capital_cny=200_000,
        as_of="2026-08-21",
        data_through="2026-08-21",
    )
    assert report["candidate_id"] == "personal_daily_negext_m2"
    assert report["effective_strategy"] is False
    assert report["auto_order"] is False
    symbols = [row["symbol"] for row in report["picks"]]
    assert symbols == ["000001", "600000"]
    by_sym = {row["symbol"]: row for row in report["picks"]}
    assert by_sym["000001"]["action"] == "enter"
    assert by_sym["600000"]["action"] == "hold"
    assert report["exits"] == ["300001"]
    for row in report["picks"]:
        assert row["notional"] <= 200_000 * 0.30 + 1e-6
        assert row["lots"] >= 1
    view = sleeve.public_view(report)
    assert view["available"] is True
    assert len(view["picks"]) == 2
    assert "不是连跌反弹" in view["detail"]


def test_page_labels_sleeve_as_not_bounce() -> None:
    html = Path("app/static/index.html").read_text(encoding="utf-8")
    assert "不是连跌反弹" in html
    assert "dailySleeveList" in html


def test_scan_cache_dir(tmp_path: Path) -> None:
    def dump(symbol: str, last: float) -> None:
        recs = [{"date": f"2026-07-{i+1:02d}", "close": 10.0} for i in range(20)]
        recs.append({"date": "2026-08-21", "close": last})
        path = tmp_path / f"a_{symbol}_1400_qfq.json"
        path.write_text(json.dumps({"records": recs}), encoding="utf-8")

    dump("000001", 6.0)
    dump("600000", 9.0)
    dump("688001", 1.0)
    ranked = sleeve.scan_cache(tmp_path, top_n=2)
    assert [row["symbol"] for row in ranked] == ["000001", "600000"]
