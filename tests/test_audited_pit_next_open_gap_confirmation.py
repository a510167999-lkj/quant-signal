from app import audited_pit_next_open_gap_confirmation as confirmation
from app.audited_pit_development_replay import AuditedPITDevelopmentReplayError


def _trade(symbol, gap_pct, *, cutoff="next_open"):
    return {
        "symbol": symbol,
        "signal_date": "2025-01-02",
        "entry_date": "2025-01-03",
        "exit_date": "2025-01-08",
        "exit_reason": "time_exit",
        "holding_days": 5,
        "return_pct": 8.0,
        "max_adverse_pct": -2.0,
        "max_favorable_pct": 10.0,
        "mark_to_market_path": [{"date": "2025-01-03", "close_return_pct": 1.0}],
        "rank_score": 100.0,
        "signal_tags": ["breakout_20d"],
        "entry_executability": {
            "gap_pct": gap_pct,
            "decision_cutoff": cutoff,
        },
    }


def test_next_open_gap_filter_uses_registered_half_open_bounds():
    trades = [
        _trade("000001", 1.99),
        _trade("000002", 2.0),
        _trade("000003", 4.99),
        _trade("000004", 5.0),
        _trade("000005", None),
        _trade("000006", 2.5, cutoff=None),
    ]

    filtered, receipt = confirmation._filter_next_open_gap_candidates(trades)

    assert [trade["symbol"] for trade in filtered] == ["000002", "000003"]
    assert all(
        trade["signal_tags"] == ["breakout_20d", "next_open_gap_2_to_5"]
        for trade in filtered
    )
    assert receipt["candidate_count"] == 6
    assert receipt["pass_candidate_count"] == 2
    assert receipt["below_minimum_count"] == 1
    assert receipt["at_or_above_maximum_count"] == 1
    assert receipt["missing_count"] == 2
    assert len(receipt["receipt_sha256"]) == 64


def test_next_open_gap_filter_is_deterministic_and_preserves_exit_paths():
    trades = [_trade("000001", 2.0), _trade("000002", 4.99)]
    before = confirmation._exit_paths(trades)

    first, first_receipt = confirmation._filter_next_open_gap_candidates(trades)
    second, second_receipt = confirmation._filter_next_open_gap_candidates(trades)

    assert first == second
    assert first_receipt == second_receipt
    assert confirmation._exit_paths(first) == before
    assert [trade["signal_tags"] for trade in trades] == [
        ["breakout_20d"],
        ["breakout_20d"],
    ]


def test_next_open_gap_filter_rejects_non_open_decision_cutoff():
    try:
        confirmation._filter_next_open_gap_candidates(
            [_trade("000001", 2.5, cutoff="session_close")]
        )
    except AuditedPITDevelopmentReplayError:
        pass
    else:
        raise AssertionError("a non-open cutoff must fail closed")


def test_fixed_gap_sweep_requires_exactly_one_top_row():
    row = {"selected_trade_count": 4}

    assert confirmation._single_fixed_spec_row({"top": [row]}) == row
    for invalid in ({"top": row}, {"top": []}, {"top": [row, row]}):
        try:
            confirmation._single_fixed_spec_row(invalid)
        except AuditedPITDevelopmentReplayError:
            pass
        else:
            raise AssertionError("invalid fixed sweep shape must fail closed")
