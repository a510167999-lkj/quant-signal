from app import audited_pit_loss_cluster_cooldown as cooldown


def _trade(symbol, signal_date, exit_date, exit_reason, rank_score=100.0):
    return {
        "symbol": symbol,
        "signal_date": signal_date,
        "entry_date": signal_date,
        "exit_date": exit_date,
        "exit_reason": exit_reason,
        "rank_score": rank_score,
        "holding_days": 1,
        "return_pct": -5.0 if exit_reason == "stop_loss" else 2.0,
        "max_adverse_pct": -5.0,
        "max_favorable_pct": 1.0,
        "mark_to_market_path": [],
    }


def test_realized_three_stop_cluster_blocks_exactly_five_market_sessions():
    sessions = [f"2025-01-{index + 1:02d}" for index in range(10)]
    initial = [
        _trade(f"00000{index}", "2025-01-01", "2025-01-02", "stop_loss")
        for index in range(1, 4)
    ]
    by_date = {"2025-01-01": initial}
    for index, session in enumerate(sessions[2:8], start=4):
        by_date[session] = [
            _trade(
                f"0000{index:02d}",
                session,
                "2025-01-09",
                "time_exit",
            )
        ]

    selected, receipt = cooldown._select_with_loss_cluster_cooldown(
        by_date,
        sessions,
    )

    assert [trade["signal_date"] for trade in selected] == [
        "2025-01-01",
        "2025-01-01",
        "2025-01-01",
        "2025-01-08",
    ]
    assert receipt["trigger_dates"] == ["2025-01-03"]
    assert receipt["blocked_candidate_count"] == 5
    assert receipt["selected_count"] == 4
    jan2 = next(day for day in receipt["days"] if day["signal_date"] == "2025-01-02")
    jan3 = next(day for day in receipt["days"] if day["signal_date"] == "2025-01-03")
    jan7 = next(day for day in receipt["days"] if day["signal_date"] == "2025-01-07")
    jan8 = next(day for day in receipt["days"] if day["signal_date"] == "2025-01-08")
    assert jan2["triggered"] is False
    assert jan3["triggered"] is True
    assert jan3["trigger_event_count"] == 3
    assert jan3["cooldown_before"] == 5
    assert jan7["cooldown_after"] == 0
    assert jan8["cooldown_before"] == 0


def test_cooldown_keeps_selected_trade_exit_paths_unchanged_and_is_deterministic():
    sessions = ["2025-01-01", "2025-01-02", "2025-01-03"]
    trade = _trade(
        "000001",
        "2025-01-01",
        "2025-01-02",
        "time_exit",
    )
    before = dict(trade)
    first, first_receipt = cooldown._select_with_loss_cluster_cooldown(
        {"2025-01-01": [trade]},
        sessions,
    )
    second, second_receipt = cooldown._select_with_loss_cluster_cooldown(
        {"2025-01-01": [trade]},
        sessions,
    )

    assert trade == before
    assert first == second == [before]
    assert first_receipt == second_receipt
    assert len(first_receipt["receipt_sha256"]) == 64
