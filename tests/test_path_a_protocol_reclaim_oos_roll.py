from __future__ import annotations

import json
from pathlib import Path

from app import factor_v3_path_a_protocol_reclaim_oos as oos
from app import factor_v3_path_a_protocol_reclaim_oos_roll as roll
from app import factor_v3_path_a_research_protocol as proto


def test_cache_frontier_snapshot_uses_last_bar(tmp_path: Path) -> None:
    one = tmp_path / "a_000001_1400_qfq.json"
    two = tmp_path / "a_000002_1400_qfq.json"
    one.write_text(
        json.dumps(
            {
                "source": "Jiaoch stk_mins daily qfq; jiaoch-daily-bars/shares-cny/v2",
                "records": [{"date": "2026-08-12", "close": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    two.write_text(
        json.dumps(
            {
                "source": "Jiaoch stk_mins daily qfq; jiaoch-daily-bars/shares-cny/v2",
                "records": [{"date": "2026-08-14", "close": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    snap = roll.cache_frontier_snapshot(tmp_path)
    assert snap["latest"] == "2026-08-14"
    assert snap["files"] == 2
    assert snap["by_last"]["2026-08-12"] == 1
    assert snap["by_last"]["2026-08-14"] == 1


def test_twelve_month_due_date_is_one_year_after_oos_start() -> None:
    assert oos.INDEPENDENT_OOS_START == "2026-07-04"
    assert oos.TWELVE_MONTH_DUE_DATE == "2027-07-04"
    assert oos.twelve_month_window_evaluable("2026-07-04", "2026-08-13") is False
    assert oos.twelve_month_window_evaluable("2026-07-04", "2027-07-04") is True
    assert oos.days_until_twelve_month_evaluable("2026-08-15") == 323
    assert oos.days_until_twelve_month_evaluable("2027-07-04") == 0
    assert oos.days_until_twelve_month_evaluable("2027-07-05") == 0
    assert proto.partition_for_signal_date("2027-07-04") is None


def test_merge_appends_only_newer_scaled_bars() -> None:
    old = [
        {
            "date": "2026-08-12",
            "open": 10.0,
            "high": 10.2,
            "low": 9.9,
            "close": 10.0,
            "volume": 1.0,
            "amount": 1.0,
        },
        {
            "date": "2026-08-13",
            "open": 10.0,
            "high": 10.3,
            "low": 9.8,
            "close": 10.1,
            "volume": 1.0,
            "amount": 1.0,
        },
    ]
    new = [
        {
            "date": "2026-08-13",
            "open": 20.0,
            "high": 20.6,
            "low": 19.6,
            "close": 20.2,
            "volume": 2.0,
            "amount": 2.0,
        },
        {
            "date": "2026-08-14",
            "open": 20.4,
            "high": 20.8,
            "low": 20.0,
            "close": 20.6,
            "volume": 3.0,
            "amount": 3.0,
        },
    ]
    merged, added = roll.merge_extended_records(old, new)
    assert [row["date"] for row in added] == ["2026-08-14"]
    assert merged[-1]["date"] == "2026-08-14"
    assert merged[0]["close"] == 10.0
    assert merged[1]["close"] == 10.1
    assert abs(merged[-1]["close"] - 10.3) < 1e-9


def test_merge_no_new_when_tail_already_covered() -> None:
    old = [
        {
            "date": "2026-08-13",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        }
    ]
    new = [
        {
            "date": "2026-08-12",
            "open": 1.0,
            "high": 1.0,
            "low": 1.0,
            "close": 1.0,
            "volume": 1.0,
            "amount": 1.0,
        },
        {
            "date": "2026-08-13",
            "open": 1.1,
            "high": 1.1,
            "low": 1.1,
            "close": 1.1,
            "volume": 1.0,
            "amount": 1.0,
        },
    ]
    merged, added = roll.merge_extended_records(old, new)
    assert added == []
    assert merged == old


def test_roll_table_never_claims_effective() -> None:
    table = roll.format_reclaim_oos_roll_table(
        {
            "stage_goal_id": roll.STAGE_GOAL_ID,
            "as_of": "2026-08-15",
            "oos_end": "2026-08-13",
            "skipped": True,
            "new_jiaoch_days": [],
            "twelve_month_due_date": "2027-07-04",
            "days_until_twelve_month_evaluable": 323,
            "twelve_month_evaluable": False,
            "effective_strategy": False,
            "oos": {"raw_trade_count": 314, "score": {"selected_trade_count": 0}},
        }
    )
    assert "effective_strategy=False" in table
    assert "due=2027-07-04" in table
    assert "days_left=323" in table
