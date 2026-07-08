from app.execution import assess_entry_executability


def test_entry_executability_rejects_large_gap_up():
    result = assess_entry_executability(
        {"close": 10.0},
        {"open": 10.8, "high": 10.9, "low": 10.7, "close": 10.75},
        max_gap_up_pct=6,
    )

    assert result["executable"] is False
    assert result["gap_pct"] == 8.0
    assert result["reasons"]


def test_entry_executability_allows_normal_open():
    result = assess_entry_executability(
        {"close": 10.0},
        {"open": 10.2, "high": 10.5, "low": 10.1, "close": 10.4},
        max_gap_up_pct=6,
    )

    assert result["executable"] is True
    assert result["gap_pct"] == 2.0


def test_entry_executability_rejects_large_intraday_range():
    result = assess_entry_executability(
        {"close": 10.0},
        {"open": 10.1, "high": 11.0, "low": 10.0, "close": 10.2},
        max_intraday_range_pct=8,
    )

    assert result["executable"] is False
    assert result["intraday_range_pct"] == 10.0
