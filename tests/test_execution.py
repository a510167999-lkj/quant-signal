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
        decision_cutoff="session_close",
    )

    assert result["executable"] is False
    assert result["intraday_range_pct"] == 10.0


def test_next_open_executability_ignores_later_session_high_and_low():
    calm = assess_entry_executability(
        {"close": 10.0},
        {"open": 10.1, "high": 10.2, "low": 10.0, "close": 10.2},
        decision_cutoff="next_open",
    )
    volatile = assess_entry_executability(
        {"close": 10.0},
        {"open": 10.1, "high": 12.0, "low": 9.0, "close": 11.0},
        decision_cutoff="next_open",
    )

    assert calm == volatile
    assert calm["executable"] is True
    assert calm["intraday_range_pct"] is None
    assert calm["decision_cutoff"] == "next_open"


def test_next_open_executability_allows_exact_gap_boundaries():
    exact_gap_up = assess_entry_executability(
        {"close": 100.0},
        {"open": 106.0},
        max_gap_up_pct=6.0,
        decision_cutoff="next_open",
    )
    exact_gap_down = assess_entry_executability(
        {"close": 100.0},
        {"open": 93.0},
        max_gap_down_pct=7.0,
        decision_cutoff="next_open",
    )

    assert exact_gap_up["executable"] is True
    assert exact_gap_down["executable"] is True


def test_next_open_executability_rejects_gap_beyond_absolute_tolerance():
    above_gap_up = assess_entry_executability(
        {"close": 100.0},
        {"open": 106.000000002},
        max_gap_up_pct=6.0,
        decision_cutoff="next_open",
    )
    below_gap_down = assess_entry_executability(
        {"close": 100.0},
        {"open": 92.999999998},
        max_gap_down_pct=7.0,
        decision_cutoff="next_open",
    )

    assert above_gap_up["executable"] is False
    assert below_gap_down["executable"] is False


def test_next_open_executability_treats_exact_locked_limit_as_locked():
    exact_locked_limit = assess_entry_executability(
        {"close": 100.0},
        {"open": 109.3},
        locked_limit_gap_pct=9.3,
        max_gap_up_pct=10.0,
        decision_cutoff="next_open",
    )
    below_locked_limit = assess_entry_executability(
        {"close": 100.0},
        {"open": 109.299999998},
        locked_limit_gap_pct=9.3,
        max_gap_up_pct=10.0,
        decision_cutoff="next_open",
    )

    assert exact_locked_limit["executable"] is False
    assert below_locked_limit["executable"] is True


def test_next_open_executability_never_falls_back_to_close():
    result = assess_entry_executability(
        {"close": 10.0},
        {"open": None, "high": 10.8, "low": 9.8, "close": 10.5},
        decision_cutoff="next_open",
    )

    assert result["executable"] is False
    assert result["gap_pct"] is None
    assert result["reasons"] == ["入场价或前收价缺失。"]
